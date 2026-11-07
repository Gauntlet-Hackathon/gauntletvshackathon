"""Volunteer app, Queue mode (UI-2): GET / serves Next, Waiting and Out now from the board."""

import copy
import json
import re

import pytest
from fastapi.testclient import TestClient

from rescue_field import words
from rescue_field.api import create_app
from rescue_field.board import Q_HOAX, build_board
from rescue_field.store import EventStore
from rescue_field.volunteer import build_queue
from rescue_shared.contracts_io import CONTRACTS
from rescue_shared.ulid import new_ulid

BASE = json.loads((CONTRACTS / "examples" / "payload-extracted.valid.json").read_text())
RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
MODEL = "hf:rescue-org/rescue-tagger@4d5e6f7a8b"
Q_CNT = "We have different counts for this location. How many people need help right now?"
Q_CONFIRM = (
    "Please confirm: is anyone in immediate danger (rising water, injury, fire, trapped), "
    "and is a child, elderly or disabled person with you?"
)
Q_LOC = "What's the street address, or the nearest cross streets or landmark?"
INTERNAL = re.compile(r"R-(P[123]|ABS|NAR|DUP)-|rules@|hf:|stub:|0\.\d\d|confidence", re.I)


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


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def section(html, sid):
    """The HTML of <section id=sid> (sections are not nested)."""
    m = re.search(rf'<section id="{sid}".*?</section>', html, re.S)
    assert m, sid
    return m.group(0)


def next_id(html):
    stack = section(html, "next")
    m = re.search(r'<div class="next-item" data-request-id="([^"]+)">', stack)
    return m.group(1) if m else None


def first_open_board_row(board_html):
    row = re.compile(r'data-request-id="([^"]+)" data-tier="P\d" data-status="(\w+)"')
    for m in row.finditer(board_html):
        if m.group(2) == "new":
            return m.group(1)
    return None


def contract_rule_ids():
    text = (CONTRACTS / "engine_rules.md").read_text()
    line = text.split("## Rule ids", 1)[1].strip().splitlines()[0]
    ids = []
    for tok in re.findall(r"`([^`]+)`", line):
        m = re.fullmatch(r"(R-P\d-)(\d+)…(\d+)", tok)
        if m:
            width = len(m.group(2))
            lo, hi = int(m.group(2)), int(m.group(3))
            ids += [f"{m.group(1)}{i:0{width}d}" for i in range(lo, hi + 1)]
        else:
            ids.append(tok)
    return ids


def test_every_rule_id_has_a_sentence():
    ids = contract_rule_ids()
    assert len(ids) == 21 and "R-P1-05" in ids and "R-ABS-CNT" in ids
    for rid in ids:
        assert words.sentence_for(rid), rid
        assert not INTERNAL.search(words.sentence_for(rid))
    assert set(words.RULE_SENTENCES) == set(ids)


def test_pills():
    assert [words.tier_pill(t)["text"] for t in ("P1", "P2", "P3")] == [
        "Needs help now", "Needs help soon", "Can wait"]
    assert words.maybe_pill("P1") == {"text": "maybe Needs help now", "cls": "pill-maybe"}
    assert words.maybe_pill(None) is None
    assert words.other_posts(0) is None
    assert words.other_posts(1) == "+1 other post" and words.other_posts(2) == "+2 other posts"


def test_headline_from_fields():
    e = ext(people=3, hazards=(("rising_water", 0.9), ("on_roof", 0.9)), vulnerable=("child",))
    e["needs"] = [{"kind": "water", "hours_without": 30}]
    assert words.headline(e) == "3 people on a roof, Water rising, With a child"
    assert "No drinking water for 30 hours" in words.details(e)
    shaky = ext(people=1, hazards=(("injury", 0.6),))
    shaky["needs"] = []
    assert words.headline(shaky) == "1 person, maybe someone hurt"
    bare = ext(people=None, hazards=())
    bare["needs"] = []
    assert words.headline(bare) == "People asking for help"


def test_next_equals_first_board_tier_row(store, client):
    add(store, out("P3", "R-P3-01"), ext("3 Low St"))
    p2 = add(store, out("P2", "R-P2-01"), ext("2 Mid St"))
    p1 = add(store, out("P1", "R-P1-01"), ext("1 Top St"))
    p1b = add(store, out("P1", "R-P1-05"), ext("9 Fire St", people=1), at="2026-11-07T10:05:00Z")
    for _ in range(3):
        html, board_html = client.get("/").text, client.get("/board").text
        assert next_id(html) == first_open_board_row(board_html)
        if next_id(html) == p1:
            client.post(f"/requests/{p1}/status", json={"status": "assigned", "crew": "Boat 2"})
        elif next_id(html) == p1b:
            client.post(f"/requests/{p1b}/status", json={"status": "assigned", "crew": "Boat 3"})
    assert next_id(client.get("/").text) == p2
    q = build_queue(build_board(store))
    assert [c["request_id"] for c in q["out_now"]] == [p1, p1b]
    assert p1 in section(client.get("/").text, "out-now")


def test_escalated_goes_first_labelled(store, client):
    add(store, out("P1", "R-P1-01"), ext("1 Top St"))
    p3 = add(store, out("P3", "R-P3-01"), ext("3 Low St"))
    client.post(f"/requests/{p3}/status", json={"status": "assigned", "crew": "Boat 2"})
    client.post(f"/requests/{p3}/status", json={"status": "escalated", "reason": "road blocked"})
    html = client.get("/").text
    assert next_id(html) == p3
    assert "Couldn&#39;t get there" in section(html, "next").split("next-item")[1]


def test_set_aside_and_repeats_never_in_next(store, client):
    aside = add(store, out(None, "R-NAR-01", "not_a_request"), raw="praying for everyone")
    root = add(store, out("P2", "R-P2-01"), ext("5 Oak St"))
    dup1 = add(store, out("P1", "R-DUP-01", "duplicate", dup=root), ext("5 Oak St"),
               at="2026-11-07T10:01:00Z")
    dup2 = add(store, out("P2", "R-DUP-01", "duplicate", dup=root), ext("5 Oak St"),
               at="2026-11-07T10:02:00Z")
    html = client.get("/").text
    nxt = section(html, "next")
    assert next_id(html) == root and "+2 other posts" in nxt and "Needs help now" in nxt
    for rid in (aside, dup1, dup2):
        assert rid not in nxt
    for rid in (dup1, dup2):
        assert f'data-request-id="{rid}"' not in html  # repeats never get their own card
    q = build_queue(build_board(store))
    assert [c["request_id"] for c in q["set_aside"]] == [aside] and q["waiting"] == []
    m = re.search(r'<details id="set-aside".*?</details>', html, re.S)
    assert m and "Set aside (1)" in m.group(0) and aside in m.group(0)
    assert " open" not in m.group(0).split(">")[0]  # collapsed
    assert html.index('id="check-first-heading"') < html.index('id="set-aside"')


def test_check_first_question_exact_and_no_send_form(store, client):
    q = "Is <this> a request & for help? If so, what do you need and where are you?"
    rid = add(store, out(None, "R-ABS-REQ", "abstain", q=q, reason="low_confidence_request",
                         prov="P1"))
    html = client.get("/").text
    waiting = section(html, "waiting")
    card = re.search(rf'<article[^>]*data-request-id="{rid}".*?</article>', waiting, re.S).group(0)
    escaped = "Is &lt;this&gt; a request &amp; for help? If so, what do you need and where are you?"
    assert escaped in card
    assert "Check first" in card and "maybe Needs help now" in card and "pill-maybe" in card
    assert "Copy question" in card
    assert "<form" not in card and "Send" not in card
    assert next_id(html) is None  # an abstain never becomes Next


def test_check_first_spanish_line(store, client):
    add(store, out(None, "R-ABS-LOC", "abstain", q=Q_LOC, reason="no_usable_location", prov="P2"),
        raw="ayuda por favor", lang="es")
    html = client.get("/").text
    assert "En español: ¿Cuál es la dirección" in html
    assert "What&#39;s the street address, or the nearest cross streets or landmark?" in html


def test_abstained_root_lifted_by_duplicate_keeps_question_and_hoax_label(store, client):
    root = add(store, out(None, "R-ABS-HOAX", "abstain", q=Q_HOAX, reason="possible_hoax",
                          prov="P2"), ext("7 Pine St"))
    dup = add(store, out("P1", "R-DUP-01", "duplicate", dup=root), ext("7 Pine St"),
              at="2026-11-07T10:03:00Z")
    html = client.get("/").text
    nxt = section(html, "next")
    assert next_id(html) == root
    assert Q_HOAX in nxt and "Possible hoax" in nxt and "Needs help now" in nxt
    # Shown once, on the tier card; never a second (repeat) Check first card.
    assert html.count(Q_HOAX) == 2  # question text + copy button data
    assert f'data-request-id="{dup}"' not in html


def test_prioritized_row_with_question_shows_it(store, client):
    add(store, out("P1", "R-P1-02", q=Q_CONFIRM), ext("4 Ash St", hazards=(("injury", 0.6),)))
    nxt = section(client.get("/").text, "next")
    assert Q_CONFIRM in nxt
    assert "Needs confirmation" in nxt and "Needs help now" in nxt


def test_count_conflict_label(store, client):
    add(store, out(None, "R-ABS-CNT", "abstain", q=Q_CNT, reason="conflicting_counts", prov="P1"))
    html = client.get("/").text
    assert Q_CNT in section(html, "waiting")
    assert "Messages from the same person give different numbers of people." in html


def test_no_internal_details_in_html(store, client):
    add(store, out("P1", "R-P1-01", q=Q_CONFIRM), ext("1 Top St"), contact="555-0101")
    add(store, out(None, "R-ABS-REQ", "abstain", q=Q_LOC, reason="no_usable_location",
                   prov="P3"))
    add(store, out(None, "R-NAR-02", "not_a_request"))
    html = client.get("/").text
    frag = client.get("/", headers={"HX-Request": "true"}).text
    for page in (html, frag):
        assert not INTERNAL.search(page), INTERNAL.search(page)
    assert "Why is this one first?" in html
    assert "Water is rising and people are trapped or on a roof." in html
    assert "Text message · 555-0101 ·" in html
    assert "1 Top St" in html and 'class="address"' in html


def test_card_has_incident_badge(store, client):
    rid = add(store, out("P2", "R-P2-01"))
    assert f">#{rid[-4:]}<" in client.get("/").text


def test_fragment_and_board_route(store, client):
    add(store, out("P1", "R-P1-01"))
    page = client.get("/")
    frag = client.get("/", headers={"HX-Request": "true"})
    assert page.status_code == frag.status_code == 200
    assert "<html" in page.text and "CommunityRescue" in page.text and 'id="queue"' in page.text
    assert "<html" not in frag.text and 'id="next"' in frag.text
    assert "Skip for now" in page.text and "setInterval(refresh" in page.text
    board = client.get("/board")
    assert board.status_code == 200 and "Rescue triage board" in board.text
    assert "R-P1-01" in board.text  # the technical view is unchanged


def test_recheck_link_only_when_route_exists(store):
    add(store, out("P1", "R-P1-01"))
    app = create_app(store=store)
    with TestClient(app) as c:
        assert "Check again" not in c.get("/").text

    @app.post("/requests/{request_id}/recheck")
    def _recheck(request_id: str):  # pragma: no cover - only registered
        return {}

    with TestClient(app) as c:
        assert "Check again" in c.get("/").text
