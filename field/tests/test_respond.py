"""Respond mode for the boat crew (UI-5): GET /respond and GET /respond/job.json."""

import copy
import html as htmllib
import json
import re

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app
from rescue_field.screens.respond import find_job, radio_lines
from rescue_field.store import EventStore
from rescue_shared.contracts_io import CONTRACTS
from rescue_shared.ulid import new_ulid

BASE = json.loads((CONTRACTS / "examples" / "payload-extracted.valid.json").read_text())
RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
MODEL = "hf:rescue-org/rescue-tagger@4d5e6f7a8b"
STATUS_TYPES = {"assigned", "escalated", "rescued"}


def ext(location="2210 Telephone Road", people=3, hazards=(("rising_water", 0.9),)):
    e = copy.deepcopy(BASE)
    e["location"] = {"text": location, "type": "address", "confidence": 0.9}
    e["people_count"]["value"] = people
    e["hazards"] = [{"kind": k, "confidence": c} for k, c in hazards]
    e["vulnerable"] = []
    return e


def out(tier="P1", rule="R-P1-01"):
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


def _event(rid, kind, payload, at="2026-11-07T10:00:00Z"):
    return {
        "event_id": new_ulid(),
        "request_id": rid,
        "type": kind,
        "device_id": "field-01",
        "occurred_at": at,
        "rules_version": RULES,
        "model_version": MODEL,
        "payload": payload,
    }


def add(store, decision=None, e=None, contact="713-555-0142", raw="SECRETWORD help zzqx"):
    rid = new_ulid()
    e = e or ext()
    decision = decision or out()
    store.append_events(
        [
            _event(rid, "received", {"channel": "sms", "raw_text": raw, "lang_hint": None,
                                     "contact": contact}),
            _event(rid, "extracted", e),
            _event(rid, "decided", {"engine_input": {"extracted": e, "context": {}},
                                    "engine_output": decision}),
        ]
    )
    return rid


def assign_raw(store, rid, crew):
    store.append_events([_event(rid, "assigned", {"crew": crew, "note": None})])


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("SHELTER_NAMES", "Shelter 1 (Lincoln High), Shelter 2 (Oak Hall)")
    monkeypatch.setenv("VOLUNTEER_NAME", "Jordan K.")
    with TestClient(create_app(store=store)) as c:
        yield c


def status_events(store, rid):
    return [e for e in store.request_events(rid) if e["type"] in STATUS_TYPES]


def forms(html):
    """outcome -> (action, {hidden name: value}) for each status form on the page."""
    found = {}
    for m in re.finditer(r'<form[^>]*data-outcome="([^"]+)"[^>]*action="([^"]+)">(.*?)</form>',
                         html, re.S):
        hidden = dict(
            (htmllib.unescape(n), htmllib.unescape(v))
            for n, v in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">',
                                   m.group(3))
        )
        found[m.group(1)] = (m.group(2), hidden, m.group(3))
    return found


def page(client, boat, step=None):
    params = {"boat": boat} | ({"step": step} if step else {})
    r = client.get("/respond", params=params)
    assert r.status_code == 200, r.text
    return r.text


# --- Matching ------------------------------------------------------------------------------------


def test_job_matches_crew_and_latest_assign_wins(store):
    a = add(store)
    b = add(store, out("P2", "R-P2-04"))
    assign_raw(store, a, "Mike's boat")
    assign_raw(store, b, "Sara's boat")
    assign_raw(store, a, "Sara's boat")  # reassigned: the latest assign wins
    events = store.all_events()
    assert find_job(events, "Mike's boat") is None
    job = find_job(events, "  sara's   BOAT ")
    assert job["request_id"] == a  # board order: the P1 card first
    assert find_job(events, "") is None


def test_escalated_or_rescued_is_not_a_job(store, client):
    rid = add(store)
    client.post(f"/requests/{rid}/status", json={"status": "assigned", "crew": "Boat 1"})
    assert find_job(store.all_events(), "Boat 1")["request_id"] == rid
    client.post(f"/requests/{rid}/status", json={"status": "escalated", "reason": "x"})
    assert find_job(store.all_events(), "Boat 1") is None


def test_no_job_state(store, client):
    add(store)
    html = page(client, "Boat 9")
    assert "No job right now" in html and 'id="job"' not in html
    assert client.get("/respond/job.json", params={"boat": "Boat 9"}).json() == {
        "boat": "Boat 9",
        "job": None,
    }


def test_pick_boat_lists_roster(client):
    client.post("/boats", json={"name": "Mike's boat", "seats": 6})
    html = client.get("/respond").text
    assert "Pick your boat" in html
    assert 'href="/respond?boat=Mike%27s%20boat"' in html
    assert 'name="boat"' in html  # or type a crew name


def test_shell_toggle_points_at_respond(client):
    html = client.get("/respond").text
    assert re.search(r'<a href="/respond" class="selected" aria-current="page">Respond</a>', html)
    assert 'href="/respond"' in client.get("/app").text


# --- Job card and radio lines --------------------------------------------------------------------


def test_job_card_words_and_no_internals(store, client):
    rid = add(store)
    client.post(f"/requests/{rid}/status", json={"status": "assigned", "crew": "Boat 1"})
    html = page(client, "Boat 1", "enroute")
    text = htmllib.unescape(html)
    assert "Needs help now" in text
    assert f"#{rid[-4:]}" in text
    assert "2210 Telephone Road" in text
    assert "Call when close: 713-555-0142" in text
    assert f"Jordan K. here. On my way to 2210 Telephone Road, incident {rid[-4:]}." in text
    for chip in ("Sent to you", "On the way", "There", "Done"):
        assert chip in text
    assert "R-P1" not in html and "rules@" not in html and "hf:" not in html
    assert "SECRETWORD" not in html and "zzqx" not in html
    assert not re.search(r"0\.\d+", re.sub(r"<script.*?</script>", "", html, flags=re.S))
    # Outcomes only after There / Done; "I can't take this one" always.
    assert 'id="outcomes"' not in html and "I can't take this one" in html
    assert 'id="outcomes"' in page(client, "Boat 1", "onscene")
    assert 'id="outcomes"' in page(client, "Boat 1", "done")
    job = client.get("/respond/job.json", params={"boat": "boat 1"}).json()["job"]
    assert job["request_id"] == rid and job["crew"] == "Boat 1"
    assert "R-P1" not in json.dumps(job)


def test_radio_lines_without_address():
    row = {"request_id": "01ABCDEFGHJKMNPQRSTVWX0147", "extracted": {"location": None}}
    lines = radio_lines(row, "Sam B.")
    assert lines["enroute"] == "Sam B. here. On my way to incident 0147."
    assert lines["done"] == "Sam B. here. Incident 0147 done."


# --- Outcomes: each writes exactly one event ------------------------------------------------------


def _assigned(store, client, crew="Mike's boat"):
    rid = add(store)
    client.post(f"/requests/{rid}/status", json={"status": "assigned", "crew": crew})
    return rid


def _post(client, action, hidden, **typed):
    return client.post(action, data=hidden | typed)


@pytest.mark.parametrize(
    ("outcome", "typed", "status", "payload"),
    [
        ("all", {"rescued_count": "3", "note": "to: Shelter 2 (Oak Hall)"}, "rescued",
         {"rescued_count": 3, "note": "to: Shelter 2 (Oak Hall)"}),
        ("nobody", {}, "rescued", {"rescued_count": 0, "note": "nobody there"}),
        ("noreach", {"reason": "Road under water"}, "escalated",
         {"reason": "Road under water", "note": None}),
        ("cant", {}, "escalated", {"reason": "Mike's boat can't take this one", "note": None}),
    ],
)
def test_outcome_writes_expected_event(store, client, outcome, typed, status, payload):
    rid = _assigned(store, client)
    found = forms(page(client, "Mike's boat", "onscene"))
    action, hidden, _ = found[outcome]
    assert action == f"/requests/{rid}/status"
    r = _post(client, action, hidden, **typed)
    assert r.status_code == 200, r.text
    evs = status_events(store, rid)
    assert [e["type"] for e in evs] == ["assigned", status]
    assert evs[-1]["payload"] == payload


def test_all_outcome_offers_the_shelters(store, client):
    _assigned(store, client)
    _, _, body = forms(page(client, "Mike's boat", "done"))["all"]
    assert '<option value="to: Shelter 1 (Lincoln High)">' in body
    assert '<option value="to: Shelter 2 (Oak Hall)">' in body
    assert 'name="rescued_count"' in body and 'value="3"' in body


def test_some_outcome_reason(store, client):
    """respond.js turns got_count into the reason; this mirrors bodyFor()."""
    rid = _assigned(store, client)
    action, hidden, body = forms(page(client, "Mike's boat", "onscene"))["some"]
    assert hidden == {"status": "escalated"} and 'name="got_count"' in body
    r = _post(client, action, hidden, reason="Got 2 out, others still there")
    assert r.status_code == 200
    assert status_events(store, rid)[-1]["payload"]["reason"] == "Got 2 out, others still there"


def test_job_gone_after_outcome_and_409_message(store, client):
    rid = _assigned(store, client)
    action, hidden, _ = forms(page(client, "Mike's boat", "onscene"))["nobody"]
    assert _post(client, action, hidden).status_code == 200
    assert "No job right now" in page(client, "Mike's boat")
    again = _post(client, action, hidden)  # a stale page posts again
    assert again.status_code == 409
    assert again.json()["detail"] == "cannot move rescued -> rescued"
    assert len(status_events(store, rid)) == 2


def test_respond_js_composes_some_reason_and_shows_409():
    from rescue_field.shell import STATIC_DIR

    js = (STATIC_DIR / "respond.js").read_text()
    assert '"Got " + ' in js and '" out, others still there"' in js
    assert "res.j.detail" in js and "if (busy) return;" in js
    assert "localStorage" in js
