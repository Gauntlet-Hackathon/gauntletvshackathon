import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from field.tests.test_board import add, extracted, output

from rescue_field.api import create_app
from rescue_field.respond import outcome_body, radio_lines
from rescue_field.shell import shell_config
from rescue_field.store import EventStore
from rescue_shared.ulid import new_ulid

RESPOND_JS = Path(__file__).parents[1] / "rescue_field" / "static" / "respond.js"
R1 = "01JC8Y00000000000000000147"
R2 = "01JC8Y00000000000000000222"
R3 = "01JC8Y00000000000000000333"


def status_event(rid, kind, payload, at="2026-11-07T10:05:00Z"):
    return {
        "event_id": new_ulid(),
        "request_id": rid,
        "type": kind,
        "device_id": "field-01",
        "occurred_at": at,
        "rules_version": "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21",
        "model_version": "hf:rescue-org/rescue-tagger@4d5e6f7a8b",
        "payload": payload,
    }


def assign(store, rid, crew):
    store.append_events([status_event(rid, "assigned", {"crew": crew, "note": None})])


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    ext = extracted(people=3, location="2210 Telephone Road")
    ext["hazards"] = [{"kind": "rising_water", "confidence": 0.9}]
    ext["vulnerable"] = [{"kind": "elderly", "confidence": 0.9}]
    ext["needs"] = []
    add(s, R1, output("P1", "R-P1-01"), ext=ext)
    s.append_events(
        [
            status_event(
                R1,
                "received",
                {"channel": "sms", "raw_text": "x", "lang_hint": None, "contact": "713-555-0142"},
                at="2026-11-07T10:00:00Z",
            )
        ]
    )
    add(s, R2, output("P2", "R-P2-01"), ext=extracted(location="Elm St"))
    yield s
    s.close()


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def job(client, crew):
    r = client.get("/respond/job.json", params={"crew": crew})
    assert r.status_code == 200, r.text
    return r.json()["job"]


def test_no_job_state(client, store):
    assert job(client, "Boat 1") is None
    page = client.get("/respond", params={"crew": "Boat 1"})
    assert page.status_code == 200 and "No job right now" in page.text
    assert "Pick your boat" in client.get("/respond").text


def test_job_matched_by_latest_assign(client, store):
    assign(store, R1, "Boat 2")
    assert job(client, "Boat 2")["request_id"] == R1
    assign(store, R1, "Boat 1")  # e.g. a re-dispatch synced down: the latest assign wins
    assert job(client, "Boat 2") is None
    j = job(client, " boat 1 ")  # trimmed, any case
    assert j["request_id"] == R1 and j["incident"] == "0147"
    assert j["pill"] == "Needs help now" and j["place"] == "2210 Telephone Road"
    assert j["details"] == ["3 people", "An older person", "Water rising"]
    assert j["contact"] == "713-555-0142"
    assert j["radio"]["enroute"].endswith("here. On my way to 2210 Telephone Road, incident 0147.")


def test_escalated_or_rescued_is_not_a_job(client, store):
    assign(store, R1, "Boat 1")
    store.append_events([status_event(R1, "escalated", {"reason": "x", "note": None})])
    assert job(client, "Boat 1") is None


def test_board_order_and_more(client, store):
    assign(store, R2, "Boat 1")
    assign(store, R1, "Boat 1")
    j = job(client, "Boat 1")
    assert j["request_id"] == R1 and j["more"] == 1


def test_page_shows_card_without_rule_ids(client, store):
    client.post("/boats", json={"name": "Boat 1", "seats": 4})
    assign(store, R1, "Boat 1")
    html = client.get("/respond", params={"crew": "Boat 1"}).text
    for text in (
        "Needs help now",
        "#0147",
        "2210 Telephone Road",
        "Call when close: 713-555-0142",
        "Sent to you",
        "On the way",
        "There",
        "Done",
        "Got everyone out",
        "Got some out",
        "Nobody there",
        "Couldn't get there",
        "I can't take this one",
        "/respond?crew=Boat%201",
    ):
        assert text in html, text
    assert not re.search(r"R-(P[123]|ABS|NAR|DUP)-", html)
    assert "rules@" not in html and "hf:" not in html and "confidence" not in html
    assert 'href="/respond" class="selected"' in html
    for shelter in shell_config()["shelter_names"]:
        assert shelter in html


def test_radio_lines_use_only_place_incident_and_name():
    lines = radio_lines({"place": "2210 Telephone Road", "incident": "0147"}, "Jordan K.")
    assert lines["enroute"] == "Jordan K. here. On my way to 2210 Telephone Road, incident 0147."
    assert lines["assigned"] == "Jordan K. here. Got incident 0147, 2210 Telephone Road."


OUTCOMES = [
    (
        "all",
        {"count": 3, "shelter": "Shelter 1 (Lincoln High)"},
        "rescued",
        {"rescued_count": 3, "note": "to: Shelter 1 (Lincoln High)"},
    ),
    ("some", {"count": 2}, "escalated", {"reason": "Got 2 out, others still there", "note": None}),
    ("nobody", {}, "rescued", {"rescued_count": 0, "note": "nobody there"}),
    (
        "noreach",
        {"reason": "water too fast"},
        "escalated",
        {"reason": "Couldn't get there: water too fast", "note": None},
    ),
    ("decline", {}, "escalated", {"reason": "Boat 1 can't take this one", "note": None}),
]


@pytest.mark.parametrize(("kind", "inputs", "etype", "payload"), OUTCOMES)
def test_each_outcome_writes_one_expected_event(client, store, kind, inputs, etype, payload):
    assign(store, R1, "Boat 1")
    before = len(store.all_events())
    body = outcome_body(kind, boat="Boat 1", **inputs)
    r = client.post(f"/requests/{R1}/status", json=body)
    assert r.status_code == 200, r.text
    events = store.all_events()
    assert len(events) == before + 1
    assert events[-1]["type"] == etype
    assert {k: events[-1]["payload"].get(k) for k in payload} == payload
    assert job(client, "Boat 1") is None
    # A second tap is refused by the status API with a message the page shows.
    again = client.post(f"/requests/{R1}/status", json=body)
    assert again.status_code == 409 and again.json()["detail"].startswith("cannot move")
    assert len(store.all_events()) == before + 1


def test_js_bodies_match_python(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    cases = [[kind, {"boat": "Boat 1", **inputs}] for kind, inputs, _, _ in OUTCOMES]
    cases.append(["noreach", {"boat": "Boat 1", "reason": "  "}])
    script = tmp_path / "h.js"
    script.write_text(
        "const m = require(process.argv[2]);"
        "const cases = JSON.parse(process.argv[3]);"
        "console.log(JSON.stringify(cases.map(([k, v]) => m.bodyFor(k, v))));"
    )
    out = subprocess.run(
        [node, str(script), str(RESPOND_JS), json.dumps(cases)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    expected = [
        outcome_body(k, boat=v["boat"], **{x: y for x, y in v.items() if x != "boat"})
        for k, v in cases
    ]
    assert json.loads(out.stdout) == expected
