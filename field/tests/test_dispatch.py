"""Send someone and close the loop after a rescue (UI-3): /requests/{id}/send and /update."""

import copy
import html as htmllib
import json
import re

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app
from rescue_field.board import build_board
from rescue_field.dispatch import draft_message, find_row
from rescue_field.store import EventStore
from rescue_field.volunteer import build_queue
from rescue_shared.contracts_io import CONTRACTS
from rescue_shared.ulid import new_ulid

BASE = json.loads((CONTRACTS / "examples" / "payload-extracted.valid.json").read_text())
RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
MODEL = "hf:rescue-org/rescue-tagger@4d5e6f7a8b"

STATUS_TYPES = {"assigned", "escalated", "rescued"}


# Same fixtures as test_volunteer.py (tests are not importable from each other).
def ext(location="12 Elm St", people=3, hazards=(("rising_water", 0.9),), vulnerable=()):
    e = copy.deepcopy(BASE)
    e["location"] = {"text": location, "type": "address", "confidence": 0.9}
    e["people_count"]["value"] = people
    e["hazards"] = [{"kind": k, "confidence": c} for k, c in hazards]
    e["vulnerable"] = [{"kind": k, "confidence": 0.9} for k in vulnerable]
    return e


def out(tier=None, rule="R-P2-04", outcome="prioritized", dup=None, q=None, reason=None, prov=None):
    return {
        "outcome": outcome,
        "priority_tier": tier,
        "rule_id": rule,
        "duplicate_of": dup,
        "abstain_reason": reason,
        "provisional_tier": prov,
        "follow_up_question": q,
        "rules_version": RULES,
    }


def add(store, decision, e=None, at="2026-11-07T10:00:00Z", raw="help", lang=None, contact=None):
    rid = new_ulid()
    e = e or ext()
    payloads = [
        ("received", {"channel": "sms", "raw_text": raw, "lang_hint": lang, "contact": contact}),
        ("extracted", e),
        ("decided", {"engine_input": {"extracted": e, "context": {}}, "engine_output": decision}),
    ]
    store.append_events(
        {
            "event_id": new_ulid(),
            "request_id": rid,
            "type": kind,
            "device_id": "field-01",
            "occurred_at": at,
            "rules_version": RULES,
            "model_version": MODEL,
            "payload": p,
        }
        for kind, p in payloads
    )
    return rid


def section(html, sid):
    """The HTML of <section id=sid> (sections are not nested)."""
    m = re.search(rf'<section id="{sid}".*?</section>', html, re.S)
    assert m, sid
    return m.group(0)


def next_id(html):
    stack = section(html, "next")
    m = re.search(r'<div class="next-item" data-request-id="([^"]+)">', stack)
    return m.group(1) if m else None


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("SHELTER_NAMES", "Shelter 1 (Lincoln High), Shelter 2 (Oak Hall)")
    with TestClient(create_app(store=store)) as c:
        yield c


def status_events(store, rid):
    return [e for e in store.request_events(rid) if e["type"] in STATUS_TYPES]


def assign(client, rid, crew="Mike's boat"):
    r = client.post(f"/requests/{rid}/send", data={"crew": crew})
    assert r.status_code == 200, r.text
    return r


def update(client, rid, **form):
    return client.post(f"/requests/{rid}/update", data=form)


def confirmation(html):
    m = re.search(r'<section id="confirmation".*?</section>', html, re.S)
    return htmllib.unescape(m.group(0))


# --- Send someone -------------------------------------------------------------------------------


def test_send_page_lists_boats_with_seat_warning(store, client):
    e = ext("2210 Telephone Rd", people=3)
    rid = add(store, out("P1", "R-P1-03"), e, contact="713-555-0142")
    client.post("/boats", json={"name": "Mike's boat", "seats": 6})
    client.post("/boats", json={"name": "Sara's boat", "seats": 2})
    html = client.get("/").text
    assert f'href="/requests/{rid}/send"' in section(html, "next")
    page = client.get(f"/requests/{rid}/send").text
    mike = re.search(r"Mike&#39;s boat.*?</label>", page, re.S).group(0)
    sara = re.search(r"Sara&#39;s boat.*?</label>", page, re.S).group(0)
    assert "Not enough room" not in mike
    assert "Not enough room for 3" in sara
    assert 'name="crew_other"' in page and "Copy message" in page and "On the way" in page


def test_drafted_message_only_extracted_fields_and_contact(store, client):
    raw = "SECRETWORD water coming in fast 2210 telephone rd grandma on oxygen zzqx"
    e = ext(
        "2210 Telephone Rd",
        people=3,
        hazards=(("medical_device_no_power", 0.91),),
        vulnerable=("elderly",),
    )
    rid = add(store, out("P1", "R-P1-03"), e, raw=raw, contact="713-555-0142")
    row = find_row(build_board(store), rid)
    msg = draft_message(row)
    assert "2210 Telephone Rd" in msg and "713-555-0142" in msg
    assert "3 people" in msg and "Medical device without power" in msg and "older person" in msg
    for word in ("SECRETWORD", "zzqx", "grandma", "coming in fast", "oxygen"):
        assert word not in msg
    page = htmllib.unescape(client.get(f"/requests/{rid}/send").text)
    draft = re.search(r'<pre id="draft" class="draft">(.*?)</pre>', page, re.S).group(1)
    assert draft == msg
    assert "SECRETWORD" not in page


def test_on_the_way_assigns_boat_and_moves_to_out_now(store, client):
    rid = add(store, out("P1", "R-P1-01"), ext("1 Top St"))
    other = add(store, out("P2", "R-P2-01"), ext("2 Mid St"))
    client.post("/boats", json={"name": "Boat 2", "seats": 4})
    r = assign(client, rid, "Boat 2")
    assert "Boat 2 is on the way" in confirmation(r.text)
    [ev] = status_events(store, rid)
    assert ev["type"] == "assigned" and ev["payload"]["crew"] == "Boat 2"
    html = client.get("/").text
    assert next_id(html) == other
    assert rid not in section(html, "next") and rid not in section(html, "waiting")
    out_now = section(html, "out-now")
    assert rid in out_now and f'href="/requests/{rid}/update"' in out_now


def test_free_text_crew_wins_over_picked_boat(store, client):
    rid = add(store, out("P2", "R-P2-01"))
    client.post(f"/requests/{rid}/send", data={"crew": "Boat 2", "crew_other": "Fire dept truck 9"})
    [ev] = status_events(store, rid)
    assert ev["payload"]["crew"] == "Fire dept truck 9"


def test_send_without_crew_keeps_page(store, client):
    rid = add(store, out("P2", "R-P2-01"))
    r = client.post(f"/requests/{rid}/send", data={"crew_other": "  "})
    assert r.status_code == 422 and "Pick a boat or type who is going." in r.text
    assert status_events(store, rid) == []


def test_send_409_shows_message_and_keeps_typed(store, client):
    rid = add(store, out("P2", "R-P2-01"))
    client.post(f"/requests/{rid}/status", json={"status": "assigned", "crew": "Boat 1"})
    r = client.post(f"/requests/{rid}/send", data={"crew_other": "Dev's kayak"})
    assert r.status_code == 409
    assert 'id="form-error"' in r.text and "cannot move assigned -&gt; assigned" in r.text
    assert 'value="Dev&#39;s kayak"' in r.text
    assert len(status_events(store, rid)) == 1


def test_unknown_request_404(client):
    assert client.get("/requests/NOPE/send").status_code == 404
    assert client.post("/requests/NOPE/update", data={"outcome": "nobody"}).status_code == 404


# --- Out now -> Update: one test per outcome ----------------------------------------------------


def test_got_everyone_out(store, client):
    rid = add(store, out("P1", "R-P1-01"), ext(people=3))
    assign(client, rid)
    page = client.get(f"/requests/{rid}/update").text
    assert "Shelter 2 (Oak Hall)" in page and 'name="rescued_count"' in page and 'value="3"' in page
    r = update(client, rid, outcome="all", rescued_count="3", shelter="Shelter 2 (Oak Hall)")
    assert r.status_code == 200 and "off every list" in confirmation(r.text)
    evs = status_events(store, rid)
    assert [e["type"] for e in evs] == ["assigned", "rescued"]
    assert evs[1]["payload"] == {"rescued_count": 3, "note": "to: Shelter 2 (Oak Hall)"}
    html = client.get("/").text
    assert rid not in html


def test_got_some_out(store, client):
    rid = add(store, out("P1", "R-P1-01"), ext(people=5))
    assign(client, rid)
    r = update(client, rid, outcome="some", got_count="2")
    assert r.status_code == 200
    evs = status_events(store, rid)
    assert [e["type"] for e in evs] == ["assigned", "escalated"]
    assert evs[1]["payload"] == {"reason": "Got 2 out, others still there", "note": None}
    assert next_id(client.get("/").text) == rid


def test_nobody_there(store, client):
    rid = add(store, out("P2", "R-P2-01"))
    assign(client, rid)
    r = update(client, rid, outcome="nobody", rescued_count="7", reason="ignored")
    assert r.status_code == 200
    evs = status_events(store, rid)
    assert [e["type"] for e in evs] == ["assigned", "rescued"]
    assert evs[1]["payload"] == {"rescued_count": 0, "note": "nobody there"}


def test_couldnt_get_there_keeps_priority_and_goes_to_top(store, client):
    add(store, out("P1", "R-P1-01"), ext("1 Top St"))
    rid = add(store, out("P2", "R-P2-01"), ext("2 Mid St"))
    before = next(c for c in build_queue(build_board(store))["queue"] if c["request_id"] == rid)
    assign(client, rid)
    r = update(client, rid, outcome="noreach", reason="Bridge under water")
    assert r.status_code == 200
    assert f"still {before['pill']['text']}" in confirmation(r.text)
    evs = status_events(store, rid)
    assert [e["type"] for e in evs] == ["assigned", "escalated"]
    assert evs[1]["payload"] == {"reason": "Bridge under water", "note": None}
    q = build_queue(build_board(store))
    assert q["next"]["request_id"] == rid
    assert q["next"]["pill"] == before["pill"]  # priority never drops
    html = client.get("/").text
    assert next_id(html) == rid
    card = section(html, "next").split("next-item")[1]
    assert "Couldn&#39;t get there" in card and "second crew" in card


def test_outcome_needs_its_fields_and_keeps_typed(store, client):
    rid = add(store, out("P2", "R-P2-01"))
    assign(client, rid)
    r = update(client, rid, outcome="noreach", reason=" ")
    assert r.status_code == 422 and "Say why" in r.text
    r = update(client, rid, outcome="some", got_count="0", reason="kept text")
    assert r.status_code == 422 and 'value="kept text"' in r.text
    r = update(client, rid, outcome="all", rescued_count="", shelter="Shelter 1 (Lincoln High)")
    assert r.status_code == 422
    r = update(client, rid)
    assert r.status_code == 422 and "Pick what happened." in r.text
    assert len(status_events(store, rid)) == 1


def test_update_409_shows_message_and_keeps_typed(store, client):
    rid = add(store, out("P2", "R-P2-01"))
    assign(client, rid)
    assert update(client, rid, outcome="nobody").status_code == 200
    r = update(client, rid, outcome="noreach", reason="Road closed at Main")
    assert r.status_code == 409
    assert "cannot move rescued -&gt; escalated" in r.text
    assert 'value="Road closed at Main"' in r.text
    assert re.search(r'value="noreach" required checked', r.text)
    assert len(status_events(store, rid)) == 2


def test_other_posts_closed_too(store, client):
    root = add(store, out("P1", "R-P1-01"), ext("5 Oak St"))
    dup = add(
        store,
        out("P1", "R-P1-01", "duplicate", dup=root),
        ext("5 Oak St"),
        at="2026-11-07T10:05:00Z",
    )
    r = assign(client, root)
    assert "Other posts about this house went with it" in confirmation(r.text)
    r = update(client, root, outcome="all", rescued_count="2", shelter="Shelter 1 (Lincoln High)")
    assert (
        "Other posts about this house were closed too, so nobody else gets sent."
        in confirmation(r.text)
    )
    html = client.get("/").text
    assert root not in html and dup not in html
    # Nothing is deleted: every event stays, one status event per action on the card.
    assert [e["type"] for e in status_events(store, root)] == ["assigned", "rescued"]
    assert status_events(store, dup) == []
    assert len(store.all_events()) == 8
