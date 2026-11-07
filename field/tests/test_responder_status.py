"""OSM chips write responder_status events; Out now shows each boat's latest status (#310)."""

import copy
import html as htmllib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from rescue_field import downsync
from rescue_field.api import create_app
from rescue_field.status import latest_responder_status
from rescue_field.store import EventStore
from rescue_shared.contracts_io import CONTRACTS, validator_for
from rescue_shared.ulid import new_ulid

BASE = json.loads((CONTRACTS / "examples" / "payload-extracted.valid.json").read_text())
RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
MODEL = "hf:rescue-org/rescue-tagger@4d5e6f7a8b"
DECISION = {
    "outcome": "prioritized",
    "priority_tier": "P1",
    "rule_id": "R-P1-01",
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
        "device_id": "laptop-dev-01",
        "occurred_at": at,
        "rules_version": RULES,
        "model_version": MODEL,
        "payload": payload,
    }


def add(store):
    rid = new_ulid()
    e = copy.deepcopy(BASE)
    e["location"] = {"text": "2210 Telephone Road", "type": "address", "confidence": 0.9}
    e["people_count"]["value"] = 3
    e["hazards"] = [{"kind": "rising_water", "confidence": 0.9}]
    e["vulnerable"] = []
    store.append_events([
        _event(rid, "received", {"channel": "sms", "raw_text": "help", "lang_hint": None,
                                 "contact": "713-555-0142"}),
        _event(rid, "extracted", e),
        _event(rid, "decided", {"engine_input": {"extracted": e, "context": {}},
                                "engine_output": DECISION}),
    ])
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
    monkeypatch.setenv("VOLUNTEER_NAME", "Jordan K.")
    with TestClient(create_app(store=store)) as c:
        yield c


def page(client, boat, step=None):
    params = {"boat": boat} | ({"step": step} if step else {})
    r = client.get("/respond", params=params)
    assert r.status_code == 200, r.text
    return r.text


RESPOND_JS = Path(__file__).parents[1] / "rescue_field" / "static" / "respond.js"


def stamp(minutes_ago):
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def responder_events(store, rid):
    return [e for e in store.request_events(rid) if e["type"] == "responder_status"]


def tap(client, rid, status, crew="Boat 1", note="Jordan K. here. On my way."):
    return client.post(
        f"/requests/{rid}/responder-status", data={"crew": crew, "status": status, "note": note}
    )


def remote_status(rid, status, crew, at, device="field-07"):
    ev = _event(rid, "responder_status", {"crew": crew, "status": status, "note": None}, at)
    ev["device_id"] = device
    return ev


def out_now(client):
    html = client.get("/").text
    section = html[html.index('id="out-now"'):]
    return htmllib.unescape(section)


# --- The route -----------------------------------------------------------------------------------


def test_one_event_per_tap_and_repeats_add_nothing(store, client):
    rid = add(store)
    client.post(f"/requests/{rid}/status", json={"status": "assigned", "crew": "Boat 1"})
    outbox_before = store.outbox_count()

    r = tap(client, rid, "on_the_way")
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True
    again = tap(client, rid, "on_the_way")
    assert again.status_code == 200 and again.json() == r.json() | {"created": False}
    assert tap(client, rid, "on_the_way", crew="  boat   1 ").json()["created"] is False

    evs = responder_events(store, rid)
    assert len(evs) == 1
    ev = evs[0]
    assert ev["payload"] == {
        "crew": "Boat 1", "status": "on_the_way", "note": "Jordan K. here. On my way."
    }
    assert ev["rules_version"] == RULES and ev["model_version"] == MODEL
    assert not list(validator_for("event.schema.json").iter_errors(ev))
    assert store.outbox_count() == outbox_before + 1  # queued for upload: works offline

    assert tap(client, rid, "there").json()["created"] is True
    assert tap(client, rid, "on_the_way").json()["created"] is True  # a step back is a new tap
    assert [e["payload"]["status"] for e in responder_events(store, rid)] == [
        "on_the_way", "there", "on_the_way"
    ]


def test_never_changes_the_request_status(store, client):
    rid = add(store)
    client.post(f"/requests/{rid}/status", json={"status": "assigned", "crew": "Boat 1"})
    tap(client, rid, "done")
    assert [e["type"] for e in store.request_events(rid)][-2:] == ["assigned", "responder_status"]
    q = client.get("/").text
    assert f'data-request-id="{rid}"' in q[q.index('id="out-now"'):]  # still assigned, Out now
    # The operator can still record the outcome: rescued is a forward move from assigned.
    r = client.post(f"/requests/{rid}/status", json={"status": "rescued"})
    assert r.status_code == 200, r.text


def test_rejects_bad_input(store, client):
    rid = add(store)
    assert tap(client, rid, "arrived").status_code == 422
    assert tap(client, rid, "there", crew="  ").status_code == 422
    assert tap(client, new_ulid(), "there").status_code == 404
    r = client.post(f"/requests/{rid}/responder-status", content=b"[]",
                    headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert responder_events(store, rid) == []


# --- Out now -------------------------------------------------------------------------------------


def test_out_now_shows_latest_status_with_age(store, client):
    rid = add(store)
    assign_raw(store, rid, "Boat 1")
    store.append_events([
        _event(rid, "responder_status", {"crew": "Boat 1", "status": "sent", "note": None},
               stamp(9)),
        _event(rid, "responder_status", {"crew": "Boat 1", "status": "on_the_way", "note": None},
               stamp(4)),
    ])
    assert "Boat 1: On the way · 4 min ago" in out_now(client)


def test_status_from_a_crew_no_longer_assigned_is_ignored():
    rid = new_ulid()
    events = [
        _event(rid, "assigned", {"crew": "Boat 1", "note": None}),
        _event(rid, "responder_status", {"crew": "Boat 1", "status": "there", "note": None}),
        _event(rid, "assigned", {"crew": "Boat 2", "note": None}),
    ]
    assert latest_responder_status(events) == {}
    events.append(
        _event(rid, "responder_status", {"crew": "boat 2", "status": "sent", "note": None})
    )
    assert latest_responder_status(events)[rid]["status"] == "sent"


def test_latest_is_by_occurred_at_not_arrival_order():
    rid = new_ulid()
    events = [
        _event(rid, "assigned", {"crew": "Boat 1", "note": None}),
        remote_status(rid, "there", "Boat 1", "2026-11-07T10:05:00Z"),
        remote_status(rid, "sent", "Boat 1", "2026-11-07T10:01:00Z"),  # arrived late
    ]
    assert latest_responder_status(events)[rid]["status"] == "there"


def test_down_synced_status_from_another_laptop_appears_in_out_now(store, client):
    rid = add(store)
    assign_raw(store, rid, "Boat 7")
    ev = remote_status(rid, "there", "Boat 7", stamp(2))
    body = {"next_since": 3, "events": [ev], "approved": {}}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    with httpx.Client(base_url="http://c", transport=transport) as c:
        assert downsync.pull(c, store, device_id="laptop-dev-01") == [ev["event_id"]]
    assert ev["event_id"] not in {e["event_id"] for e in store.pending_outbox()}  # not ours
    assert "Boat 7: There · 2 min ago" in out_now(client)


def test_down_sync_keeps_responder_status_beside_forward_only_statuses():
    s = EventStore(check_same_thread=False)
    try:
        rid = new_ulid()
        s.append_events([_event(rid, "assigned", {"crew": "Boat 7", "note": None})])
        incoming = [
            remote_status(rid, "on_the_way", "Boat 7", "2026-11-07T10:01:00Z"),
            remote_status(rid, "on_the_way", "Boat 7", "2026-11-07T10:01:00Z"),
        ]
        incoming[1]["event_id"] = new_ulid()
        repeat = _event(rid, "assigned", {"crew": "Boat 7", "note": None})
        kept = downsync.forward_only(s, [incoming[0], repeat, incoming[1]])
        assert kept == incoming  # the repeated assigned drops; responder statuses never do
    finally:
        s.close()


# --- Respond mode --------------------------------------------------------------------------------


def chips(html):
    """step key -> (data-status, data-note) for each OSM chip."""
    return {
        m.group(3): (m.group(1), htmllib.unescape(m.group(2)))
        for m in re.finditer(
            r'<a class="chip osm-chip[^"]*"[^>]*data-status="([^"]+)" data-note="([^"]*)"'
            r'[^>]*href="[^"]*step=([a-z]+)"', html
        )
    }


def test_respond_chips_carry_status_and_radio_line(store, client):
    rid = add(store)
    client.post(f"/requests/{rid}/status", json={"status": "assigned", "crew": "Boat 1"})
    html = page(client, "Boat 1")
    found = chips(html)
    assert {k: v[0] for k, v in found.items()} == {
        "assigned": "sent", "enroute": "on_the_way", "onscene": "there", "done": "done"
    }
    assert found["enroute"][1] == (
        f"Jordan K. here. On my way to 2210 Telephone Road, incident {rid[-4:]}."
    )
    cfg = json.loads(re.search(r'id="respond-config"[^>]*>(.*?)</script>', html).group(1))
    assert cfg == {"boat": "Boat 1", "crew": "Boat 1", "request_id": rid}
    assert 'class="chip osm-chip selected"' not in html  # nothing saved yet


def test_respond_page_opens_on_the_saved_step(store, client):
    rid = add(store)
    client.post(f"/requests/{rid}/status", json={"status": "assigned", "crew": "Boat 1"})
    tap(client, rid, "there")
    html = page(client, "Boat 1")
    assert re.search(r'class="chip osm-chip selected"[^>]*data-status="there"', html)
    assert 'id="outcomes"' in html and 'id="radio-line"' in html
    assert "Saved here" in html
    assert "Saved here" not in page(client, "Boat 1", "enroute")


def test_respond_js_posts_each_chip_tap():
    js = RESPOND_JS.read_text()
    assert "/responder-status" in js
    for field in ('"crew"', '"status"', '"note"'):
        assert f"body.set({field}" in js
