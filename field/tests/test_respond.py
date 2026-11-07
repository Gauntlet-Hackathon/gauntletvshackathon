"""Respond mode for the boat crew (UI-5): job matching, no-job state, outcome events."""

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app
from rescue_field.screens.respond import radio_lines
from rescue_field.store import EventStore
from rescue_shared.contracts_io import CONTRACTS
from rescue_shared.ulid import new_ulid

BASE = json.loads((CONTRACTS / "examples" / "payload-extracted.valid.json").read_text())
RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
MODEL = "hf:rescue-org/rescue-tagger@4d5e6f7a8b"
INTERNAL = re.compile(r"R-(P[123]|ABS|NAR|DUP)-|rules@|hf:|stub:|0\.\d\d|confidence", re.I)

RESPOND_JS = Path(__file__).resolve().parents[1] / "rescue_field" / "static" / "respond.js"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("VOLUNTEER_NAME", "Jordan K.")
    monkeypatch.setenv("SHELTER_NAMES", "Shelter 1 (Lincoln High),Shelter 2")


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def ext(location="12 Elm St", people=3, vulnerable=()):
    e = copy.deepcopy(BASE)
    e["location"] = {"text": location, "type": "address", "confidence": 0.9}
    e["people_count"]["value"] = people
    e["hazards"] = [{"kind": "rising_water", "confidence": 0.9}]
    e["vulnerable"] = [{"kind": k, "confidence": 0.9} for k in vulnerable]
    return e


def out(tier, rule="R-P2-04"):
    return {
        "outcome": "prioritized",
        "priority_tier": tier,
        "rule_id": rule,
        "duplicate_of": None,
        "abstain_reason": None,
        "provisional_tier": None,
        "follow_up_question": None,
        "rules_version": RULES,
    }


def add(store, decision, e=None, contact=None):
    rid = new_ulid()
    e = e or ext()
    payloads = [
        ("received", {"channel": "sms", "raw_text": "help", "lang_hint": None, "contact": contact}),
        ("extracted", e),
        ("decided", {"engine_input": {"extracted": e, "context": {}}, "engine_output": decision}),
    ]
    store.append_events(
        {
            "event_id": new_ulid(),
            "request_id": rid,
            "type": kind,
            "device_id": "field-01",
            "occurred_at": "2026-11-07T10:00:00Z",
            "rules_version": RULES,
            "model_version": MODEL,
            "payload": p,
        }
        for kind, p in payloads
    )
    return rid


def raw_status(store, rid, kind, payload):
    store.append_events(
        [
            {
                "event_id": new_ulid(),
                "request_id": rid,
                "type": kind,
                "device_id": "field-01",
                "occurred_at": "2026-11-07T10:05:00Z",
                "rules_version": RULES,
                "model_version": MODEL,
                "payload": payload,
            }
        ]
    )


def job(client, crew):
    return client.get("/respond/job.json", params={"crew": crew}).json()["job"]


def status_events(store, rid):
    return [
        e for e in store.request_events(rid) if e["type"] in ("assigned", "escalated", "rescued")
    ]


def test_job_matches_crew_case_and_space_insensitive(client, store):
    rid = add(store, out("P1", "R-P1-01"), ext("2210 Telephone Road"), contact="713-555-0142")
    add(store, out("P2"), ext("9 Oak St"))
    assert (
        client.post(
            f"/requests/{rid}/status", json={"status": "assigned", "crew": "Boat 1"}
        ).status_code
        == 200
    )
    j = job(client, "  boat   1 ")
    assert j["request_id"] == rid
    assert j["incident"] == "#" + rid[-4:]
    assert j["pill"]["text"] == "Needs help now"
    assert job(client, "Boat 2") is None


def test_latest_assign_wins(client, store):
    rid = add(store, out("P2"), ext("9 Oak St"))
    raw_status(store, rid, "assigned", {"crew": "Boat 1", "note": None})
    raw_status(store, rid, "assigned", {"crew": "Boat 2", "note": None})
    assert job(client, "Boat 1") is None
    assert job(client, "Boat 2")["request_id"] == rid


def test_escalated_or_rescued_is_not_a_job(client, store):
    a = add(store, out("P1", "R-P1-01"))
    b = add(store, out("P2"))
    for rid in (a, b):
        client.post(f"/requests/{rid}/status", json={"status": "assigned", "crew": "Boat 1"})
    client.post(f"/requests/{a}/status", json={"status": "escalated", "reason": "x"})
    assert job(client, "Boat 1")["request_id"] == b
    client.post(f"/requests/{b}/status", json={"status": "rescued", "rescued_count": 1})
    assert job(client, "Boat 1") is None


def test_no_job_page_and_picker(client, store):
    client.post("/boats", json={"name": "Skiff", "seats": 4})
    page = client.get("/respond", params={"crew": "Skiff"})
    assert page.status_code == 200
    assert "No job right now" in page.text
    assert 'class="selected" aria-current="page">Respond' in page.text
    assert 'value="Skiff"' in page.text
    # No crew yet: the picker, no job heading.
    first = client.get("/respond").text
    assert "Pick your boat" in first and "No job right now" not in first


def test_shell_points_respond_at_respond(client):
    assert 'href="/respond"' in client.get("/").text


def test_job_page_card_and_radio_lines(client, store):
    e = ext("2210 Telephone Road", people=3, vulnerable=("elderly",))
    e["time_ref"] = {"text": "since 3am", "is_stale_post": False}
    rid = add(store, out("P1", "R-P1-04"), e, contact="Maria 713-555-0142")
    client.post(f"/requests/{rid}/status", json={"status": "assigned", "crew": "Boat 1"})
    html = client.get("/respond", params={"crew": "Boat 1"}).text
    num = rid[-4:]
    assert "Needs help now" in html and f"#{num}" in html
    assert "2210 Telephone Road" in html
    assert "Call when close: Maria 713-555-0142" in html
    assert "They said: since 3am" in html
    assert f"Jordan K. here. On my way to 2210 Telephone Road, incident {num}." in html
    for label in ("Sent to you", "On the way", "There", "Done"):
        assert f">{label}</button>" in html
    for label in ("Got everyone out", "Got some out", "Nobody there", "Couldn't get there"):
        assert label in html.replace("&#39;", "'")
    assert "Boat 1 can't take this one" in html.replace("&#39;", "'")
    assert "<option>Shelter 2</option>" in html
    assert not INTERNAL.search(html), INTERNAL.search(html)


def test_radio_lines_use_only_name_address_incident():
    lines = radio_lines("Jordan K.", "2210 Telephone Road", "#0147")
    assert lines["enroute"] == "Jordan K. here. On my way to 2210 Telephone Road, incident 0147."
    assert lines["done"] == "Jordan K. here. Incident 0147 done."
    assert "the address on the job" in radio_lines("A", None, "#0001")["onscene"]


def js_body(action, values):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    script = (
        "const {respondBody} = require(process.argv[1]);"
        "console.log(JSON.stringify(respondBody(process.argv[2], JSON.parse(process.argv[3]))));"
    )
    args = [node, "-e", script, str(RESPOND_JS), action, json.dumps(values)]
    done = subprocess.run(args, capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout)


VALUES = {
    "allCount": "3",
    "shelter": "Shelter 2",
    "someCount": "2",
    "noreachReason": "Water too fast",
    "declineReason": "Boat 1 can't take this one",
}
EXPECTED = {
    "all": ("rescued", {"rescued_count": 3, "note": "to: Shelter 2"}),
    "some": ("escalated", {"reason": "Got 2 out, others still there", "note": None}),
    "nobody": ("rescued", {"rescued_count": 0, "note": "nobody there"}),
    "noreach": ("escalated", {"reason": "Water too fast", "note": None}),
    "decline": ("escalated", {"reason": "Boat 1 can't take this one", "note": None}),
}


@pytest.mark.parametrize("action", sorted(EXPECTED))
def test_each_button_writes_one_expected_event(client, store, action):
    rid = add(store, out("P1", "R-P1-01"))
    client.post(f"/requests/{rid}/status", json={"status": "assigned", "crew": "Boat 1"})
    body = js_body(action, VALUES)
    r = client.post(f"/requests/{rid}/status", json=body)
    assert r.status_code == 200, r.text
    events = status_events(store, rid)
    assert [e["type"] for e in events] == ["assigned", EXPECTED[action][0]]
    assert events[-1]["payload"] == EXPECTED[action][1]
    # A second tap is refused (409) with a message, and writes nothing.
    again = client.post(f"/requests/{rid}/status", json=body)
    assert again.status_code == 409 and again.json()["detail"]
    assert len(status_events(store, rid)) == 2


def test_some_without_count_is_a_message_not_a_post():
    assert isinstance(js_body("some", {**VALUES, "someCount": ""}), str)
