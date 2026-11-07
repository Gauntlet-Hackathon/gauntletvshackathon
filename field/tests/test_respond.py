import json

import pytest
from fastapi.testclient import TestClient
from field.tests.test_board import RULES, add, extracted, output

from rescue_field.api import create_app
from rescue_field.respond import details, find_job, incident_label
from rescue_field.store import EventStore
from rescue_shared.ulid import new_ulid

A = "01JC8Y00000000000000000147"
B = "01JC8Y00000000000000000222"
SHELTER = "Shelter 9 (Test Hall)"


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    ext = extracted(people=3, location="2210 Telephone Road")
    ext["vulnerable"] = [{"kind": "elderly", "confidence": 0.9}]
    ext["hazards"] = [{"kind": "rising_water", "confidence": 0.8}]
    add(s, A, output("P1", "R-P1-01"), ext=ext)
    add(s, B, output("P2", "R-P2-01"), ext=extracted(location="Elm St"))
    yield s
    s.close()


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("VOLUNTEER_NAME", "Jordan K.")
    monkeypatch.setenv("SHELTER_NAMES", SHELTER)
    with TestClient(create_app(store=store)) as c:
        yield c


def status(client, rid, **body):
    return client.post(f"/requests/{rid}/status", json=body)


def job(client, crew):
    r = client.get("/respond/job.json", params={"crew": crew})
    assert r.status_code == 200, r.text
    return r.json()["job"]


def test_no_job_state(client):
    assert job(client, "Boat 1") is None
    assert job(client, "") is None
    page = client.get("/respond", params={"crew": "Boat 1"})
    assert page.status_code == 200 and "No job right now" in page.text
    assert 'aria-current="page">Respond' in page.text


def test_job_matches_crew_and_card(client):
    assert status(client, A, status="assigned", crew="Boat 1").status_code == 200
    assert job(client, "Boat 2") is None
    j = job(client, "  boat 1 ")  # case and spaces do not matter
    assert j["request_id"] == A and j["incident"] == "0147" and j["pill"] == "Needs help now"
    assert j["place"] == "2210 Telephone Road"
    assert j["details"][:3] == ["3 people", "Older person", "Water rising"]
    assert j["radio"]["enroute"] == (
        "Jordan K. here. On my way to 2210 Telephone Road, incident 0147."
    )
    page = client.get("/respond", params={"crew": "Boat 1"}).text
    for text in ("Needs help now", "#0147", "2210 Telephone Road", "Call when close:",
                 "Sent to you", "On the way", "There", "Done", SHELTER,
                 "I can't take this one"):
        assert text in page, text
    for leak in ("R-P1-01", RULES, "rules@", "hf:", "confidence", "0.9"):
        assert leak not in page, leak


def test_latest_assign_wins(store):
    def assign(rid, crew):
        store.append_events([{
            "event_id": new_ulid(), "request_id": rid, "type": "assigned",
            "device_id": "field-01", "occurred_at": "2026-11-07T10:05:00Z",
            "rules_version": RULES, "model_version": "m", "payload": {"crew": crew, "note": None},
        }])

    assign(A, "Boat 1")
    assign(A, "Boat 2")  # reassigned (e.g. from another device): Boat 2 has it now
    events = store.all_events()
    assert find_job(events, "Boat 1") is None
    assert find_job(events, "Boat 2")["request_id"] == A
    assign(B, "Boat 2")  # most recent assignment for the crew is the job
    assert find_job(store.all_events(), "Boat 2")["request_id"] == B


def test_closed_jobs_are_not_shown(client):
    status(client, A, status="assigned", crew="Boat 1")
    status(client, A, status="escalated", reason="x")
    assert job(client, "Boat 1") is None


def _last(store):
    return store.all_events()[-1]


@pytest.mark.parametrize(
    "body, kind, payload",
    [
        ({"status": "rescued", "rescued_count": 3, "note": f"to: {SHELTER}"},
         "rescued", {"rescued_count": 3, "note": f"to: {SHELTER}"}),
        ({"status": "escalated", "reason": "Got 2 out, others still there"},
         "escalated", {"reason": "Got 2 out, others still there", "note": None}),
        ({"status": "rescued", "rescued_count": 0, "note": "nobody there"},
         "rescued", {"rescued_count": 0, "note": "nobody there"}),
        ({"status": "escalated", "reason": "Couldn't get there: water too fast"},
         "escalated", {"reason": "Couldn't get there: water too fast", "note": None}),
        ({"status": "escalated", "reason": "Boat 1 can't take this one"},
         "escalated", {"reason": "Boat 1 can't take this one", "note": None}),
    ],
)
def test_each_outcome_writes_one_event(client, store, body, kind, payload):
    status(client, A, status="assigned", crew="Boat 1")
    n = len(store.all_events())
    assert status(client, A, **body).status_code == 200
    assert len(store.all_events()) == n + 1
    assert _last(store)["type"] == kind and _last(store)["payload"] == payload
    assert job(client, "Boat 1") is None
    again = status(client, A, **body)
    assert again.status_code == 409 and "cannot move" in again.json()["detail"]


def test_page_script_builds_the_same_bodies():
    js = (
        __import__("pathlib").Path(__file__).parents[1] / "rescue_field" / "static" / "respond.js"
    ).read_text()
    for snippet in ('"to: "', '" out, others still there"', '"nobody there"',
                    "\" can't take this one\"", "\"Couldn't get there\"", "j.detail"):
        assert snippet in js, snippet


def test_helpers():
    assert incident_label(A) == "0147"
    e = extracted(people=1)
    e["needs"] = [{"kind": "meds", "hours_without": 6}]
    e["time_ref"]["text"] = "since 3am"
    assert details(e)[0] == "1 person"
    assert "Needs medicine (none for 6 hours)" in details(e)
    assert "Said: since 3am" in details(e)
    assert "confidence" not in json.dumps(details(e))
