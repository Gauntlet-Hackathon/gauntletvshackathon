"""Send someone and close the loop after a rescue (UI-3): /requests/{id}/send and /update."""

import copy
import json
import re

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app
from rescue_field.board import build_board
from rescue_field.screens.dispatch import draft_message, find_row
from rescue_field.shell import shell_config
from rescue_field.store import EventStore
from rescue_field.volunteer import build_queue
from rescue_shared.contracts_io import CONTRACTS
from rescue_shared.ulid import new_ulid

SHELTER = shell_config({})["shelter_names"][0]
STATUSES = ("assigned", "escalated", "rescued")

BASE = json.loads((CONTRACTS / "examples" / "payload-extracted.valid.json").read_text())
RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
MODEL = "hf:rescue-org/rescue-tagger@4d5e6f7a8b"


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


def add(store, decision, e=None, at="2026-11-07T10:00:00Z", raw="help", contact=None):
    rid = new_ulid()
    e = e or ext()
    payloads = [
        ("received", {"channel": "sms", "raw_text": raw, "lang_hint": None, "contact": contact}),
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
    start = html.index(f'<section id="{sid}"')
    return html[start : html.index("</section>", start)]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("SHELTER_NAMES", raising=False)
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def status_events(s, rid):
    return [e for e in s.request_events(rid) if e["type"] in STATUSES]


def queue_ids(s):
    q = build_queue(build_board(s))
    return (
        [c["request_id"] for c in q["queue"]],
        [c["request_id"] for c in q["out_now"]],
        q,
    )


def assign(c, rid, crew="Mike's boat"):
    r = c.post(f"/requests/{rid}/send", data={"crew": crew})
    assert r.status_code == 200, r.text
    return r


def test_next_card_has_send_and_out_now_has_update(store, client):
    rid = add(store, out("P1", "R-P1-01"))
    nxt = section(client.get("/").text, "next")
    assert f'href="/requests/{rid}/send"' in nxt and "Send someone" in nxt
    assign(client, rid)
    html = client.get("/").text
    assert f'href="/requests/{rid}/send"' not in html
    out_now = section(html, "out-now")
    assert f'href="/requests/{rid}/update"' in out_now and "Mike&#39;s boat went" in out_now


def test_check_first_card_has_no_send(store, client):
    d = out(None, "R-ABS-LOC", "abstain", q="Where?", reason="missing_location", prov="P1")
    rid = add(store, d)
    assert f"/requests/{rid}/send" not in client.get("/").text


def test_send_page_lists_boats_with_seat_warning(store, client):
    client.post("/boats", json={"name": "Mike's boat", "seats": 6})
    client.post("/boats", json={"name": "Kayak", "seats": 1})
    rid = add(store, out("P1", "R-P1-01"), ext(people=3), contact="713-555-0142")
    html = client.get(f"/requests/{rid}/send").text
    kayak = html[html.index("Kayak") :]
    assert "Not enough room for 3" in kayak[:300]
    mike = html[html.index("Mike&#39;s boat") :]
    assert "Room for everyone" in mike[:300]
    assert "Call 713-555-0142 when close." in html


def test_assign_with_boat_writes_one_assigned_and_moves_to_out_now(store, client):
    client.post("/boats", json={"name": "Kayak", "seats": 1})
    rid = add(store, out("P1", "R-P1-01"), ext(people=3))
    r = assign(client, rid, "Kayak")  # too small is warned, still allowed
    assert "Kayak is on the way to 12 Elm St" in r.text
    [ev] = status_events(store, rid)
    assert ev["type"] == "assigned" and ev["payload"]["crew"] == "Kayak"
    queue, out_now, q = queue_ids(store)
    assert rid not in queue and rid not in [c["request_id"] for c in q["waiting"]]
    assert out_now == [rid]


def test_free_text_crew_wins_over_radio(store, client):
    rid = add(store, out("P2", "R-P2-04"))
    client.post(f"/requests/{rid}/send", data={"crew": "Kayak", "crew_other": "High-water truck"})
    assert status_events(store, rid)[0]["payload"]["crew"] == "High-water truck"


def test_send_without_crew_keeps_page(store, client):
    rid = add(store, out("P2", "R-P2-04"))
    r = client.post(f"/requests/{rid}/send", data={"crew_other": "  "})
    assert r.status_code == 422 and "Pick a boat or type who is going." in r.text
    assert status_events(store, rid) == []


def test_drafted_message_only_extracted_fields_and_contact(store):
    raw = "SECRET raw words zebra please hurry 99 Oak Ave"
    e = ext(location="2210 Telephone Road", people=3, hazards=(("rising_water", 0.9),),
            vulnerable=("elderly",))
    rid = add(store, out("P1", "R-P1-04"), e, raw=raw, contact="713-555-0142")
    msg = draft_message(find_row(build_board(store), rid))
    assert "2210 Telephone Road" in msg and "3 people" in msg and "713-555-0142" in msg
    assert "an older person" in msg and "Water rising" in msg
    for word in ("SECRET", "zebra", "hurry", "Oak"):
        assert word not in msg
    assert not re.search(r"R-P|rules@|hf:|0\.\d", msg)


def test_send_409_shows_message_and_keeps_typed(store, client):
    rid = add(store, out("P1", "R-P1-01"))
    client.post(f"/requests/{rid}/status", json={"status": "escalated", "reason": "blocked"})
    r = client.post(f"/requests/{rid}/send", data={"crew_other": "Truck team"})
    assert r.status_code == 409
    assert "cannot move escalated -&gt; assigned" in r.text
    assert 'value="Truck team"' in r.text
    assert [e["type"] for e in status_events(store, rid)] == ["escalated"]


def _out(s, c, people=2, dups=0):
    rid = add(s, out("P1", "R-P1-01"), ext(people=people))
    for _ in range(dups):
        add(s, out("P1", "R-DUP-01", "duplicate", dup=rid), ext(people=people),
            at="2026-11-07T10:05:00Z")
    assign(c, rid)
    return rid


def test_got_everyone_out(store, client):
    rid = _out(store, client, dups=1)
    form = client.get(f"/requests/{rid}/update").text
    assert 'value="2"' in form and SHELTER in form  # count prefilled, shelters from SHELTER_NAMES
    r = client.post(f"/requests/{rid}/update",
                    data={"outcome": "all", "count": "2", "shelter": SHELTER})
    assert r.status_code == 200
    assert "Marked safe: 2 people from 12 Elm St" in r.text
    assert "Other posts about this house were closed too, so nobody else gets sent." in r.text
    evs = status_events(store, rid)
    assert [e["type"] for e in evs] == ["assigned", "rescued"]  # one new event, nothing deleted
    assert evs[1]["payload"] == {"rescued_count": 2, "note": f"to: {SHELTER}"}
    queue, out_now, q = queue_ids(store)
    assert rid not in queue and rid not in out_now
    assert all(rid not in [c["request_id"] for c in q[k]] for k in ("check_first", "set_aside"))


def test_got_some_out(store, client):
    rid = _out(store, client, people=5)
    r = client.post(f"/requests/{rid}/update", data={"outcome": "some", "some_count": "3"})
    assert r.status_code == 200 and "back at the top of Next" in r.text
    evs = status_events(store, rid)
    assert [e["type"] for e in evs] == ["assigned", "escalated"]
    assert evs[1]["payload"] == {"reason": "Got 3 out, others still there", "note": None}
    assert queue_ids(store)[0][0] == rid


def test_nobody_there(store, client):
    rid = _out(store, client)
    r = client.post(f"/requests/{rid}/update", data={"outcome": "nobody"})
    assert r.status_code == 200 and "nobody was at 12 Elm St" in r.text
    assert "Other posts about this house" not in r.text  # no duplicates here
    evs = status_events(store, rid)
    assert [e["type"] for e in evs] == ["assigned", "rescued"]
    assert evs[1]["payload"] == {"rescued_count": 0, "note": "nobody there"}


def test_couldnt_get_there_back_on_top_same_priority(store, client):
    first = add(store, out("P1", "R-P1-01"), ext(location="1 First St"))
    rid = add(store, out("P2", "R-P2-04"), ext(location="2 Second St"))
    assign(client, rid)
    r = client.post(f"/requests/{rid}/update",
                    data={"outcome": "couldnt", "reason": "Bridge under water"})
    assert r.status_code == 200 and "at the same priority" in r.text
    evs = status_events(store, rid)
    assert [e["type"] for e in evs] == ["assigned", "escalated"]
    assert evs[1]["payload"] == {"reason": "Bridge under water", "note": None}
    queue, _, q = queue_ids(store)
    assert queue == [rid, first]  # back at the top of Next
    assert q["queue"][0]["pill"]["text"] == "Needs help soon"  # priority never drops
    assert find_row(build_board(store), rid)["tier"] == "P2"


def test_update_form_errors_keep_values(store, client):
    rid = _out(store, client)
    r = client.post(f"/requests/{rid}/update", data={"outcome": "couldnt", "reason": " "})
    assert r.status_code == 422 and "Type a reason" in r.text
    r = client.post(f"/requests/{rid}/update", data={"outcome": "some", "some_count": "0"})
    assert r.status_code == 422 and 'value="0"' in r.text
    r = client.post(f"/requests/{rid}/update",
                    data={"outcome": "all", "count": "2", "shelter": "Nowhere"})
    assert r.status_code == 422 and "Pick a shelter" in r.text
    assert [e["type"] for e in status_events(store, rid)] == ["assigned"]


def test_update_409_shows_message_and_keeps_typed(store, client):
    rid = _out(store, client)
    client.post(f"/requests/{rid}/update", data={"outcome": "nobody"})
    r = client.post(f"/requests/{rid}/update",
                    data={"outcome": "couldnt", "reason": "Road closed at Main"})
    assert r.status_code == 409
    assert "cannot move rescued -&gt; escalated" in r.text
    assert 'value="Road closed at Main"' in r.text
    assert [e["type"] for e in status_events(store, rid)] == ["assigned", "rescued"]


def test_unknown_request_404(client):
    assert client.get("/requests/nope/send").status_code == 404
    assert client.post("/requests/nope/update", data={"outcome": "nobody"}).status_code == 404
