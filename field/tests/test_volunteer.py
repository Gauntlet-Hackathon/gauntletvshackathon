"""UI-2 queue mode: GET / (Next, Waiting, Out now) over the same board snapshot as /board."""

import copy
import html as htmllib
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

BASE_EXTRACTED = json.loads((CONTRACTS / "examples" / "payload-extracted.valid.json").read_text())
RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
MODEL = "hf:rescue-org/rescue-tagger@4d5e6f7a8b"
Q_LOC = "What's the street address, or the nearest cross streets or landmark?"
Q_CONFIRM = (
    "Please confirm: is anyone in immediate danger (rising water, injury, fire, trapped), "
    "and is a child, elderly or disabled person with you?"
)


def extracted(people=2, vulnerable=0, location="Elm St", hazards=()):
    e = copy.deepcopy(BASE_EXTRACTED)
    e["location"]["text"] = location
    e["location"]["type"] = "address"
    e["people_count"]["value"] = people
    e["vulnerable"] = [{"kind": "child", "confidence": 0.9}] * vulnerable
    e["hazards"] = [{"kind": k, "confidence": 0.93} for k in hazards]
    return e


def output(tier, rule="R-P2-01", outcome="prioritized", duplicate_of=None, question=None,
           reason=None, provisional=None):
    return {
        "outcome": outcome,
        "priority_tier": tier,
        "rule_id": rule,
        "duplicate_of": duplicate_of,
        "abstain_reason": reason,
        "provisional_tier": provisional,
        "follow_up_question": question,
        "rules_version": RULES,
    }


def add(store, rid, out, ext=None, at="2026-11-07T10:00:00Z", raw="help", contact=None,
        lang=None):
    ext = ext or extracted()
    payloads = [
        ("received", {"channel": "sms", "raw_text": raw, "lang_hint": lang, "contact": contact}),
        ("extracted", ext),
        ("decided", {"engine_input": {"extracted": ext, "context": {}}, "engine_output": out}),
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
            "payload": payload,
        }
        for kind, payload in payloads
    )


def set_status(store, rid, kind, payload):
    store.append_events([{
        "event_id": new_ulid(), "request_id": rid, "type": kind, "device_id": "field-01",
        "occurred_at": "2026-11-07T12:00:00Z", "rules_version": RULES, "model_version": MODEL,
        "payload": payload,
    }])


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


def page(store, **kw):
    with TestClient(create_app(store=store)) as client:
        r = client.get("/", **kw)
    assert r.status_code == 200
    return r.text


def section(html, name):
    start = html.index(f'id="screen-{name}"')
    end = html.index("</section>", start)
    return html[start:end]


def card(html, rid):
    m = re.search(rf'<article class="card[^"]*" data-request-id="{rid}".*?</article>', html, re.S)
    assert m, rid
    return m.group(0)


def visible_next_id(html):
    m = re.search(r'<div class="next-card" data-request-id="([^"]+)">', section(html, "next"))
    return m and m.group(1)


def seed_mixed(store):
    add(store, "A0001", output("P2", "R-P2-01"), extracted(location="loc-a"))
    add(store, "B0002", output("P1", "R-P1-02"), extracted(location="loc-b", hazards=["injury"]),
        at="2026-11-07T10:05:00Z")
    add(store, "C0003", output("P1", "R-P1-01"), extracted(people=5, location="loc-c",
        hazards=["rising_water", "on_roof"]), at="2026-11-07T10:10:00Z")
    add(store, "D0004", output("P3", "R-P3-01"), extracted(location="loc-d"))
    add(store, "E0005", output(None, "R-ABS-LOC", "abstain", question=Q_LOC,
        reason="no_usable_location", provisional="P1"), extracted(location=None))
    add(store, "F0006", output(None, "R-NAR-01", "not_a_request"), extracted(location="loc-f"),
        raw="stay safe everyone")


# ---- words ------------------------------------------------------------------------------


def test_every_rule_id_has_a_sentence():
    rules = (CONTRACTS / "engine_rules.md").read_text()
    line = re.search(r"^## Rule ids\n(.+)$", rules, re.M).group(1)
    ids = set()
    for token in re.findall(r"`([^`]+)`", line):
        m = re.fullmatch(r"(R-P\d)-(\d+)…(\d+)", token)
        if m:
            ids |= {f"{m[1]}-{i:02d}" for i in range(int(m[2]), int(m[3]) + 1)}
        else:
            ids.add(token)
    # Every rule ID named anywhere in the contract is in the summary line.
    assert set(re.findall(r"`(R-[A-Z0-9]+-[A-Z0-9]+)`", rules)) <= ids
    assert len(ids) == 21
    for rid in ids:
        s = words.RULE_SENTENCES[rid]
        assert s.endswith(".") and "R-" not in s and len(s.split(".")) == 2, rid


def test_pills_and_folding_words():
    assert [words.tier_pill(t) for t in ("P1", "P2", "P3")] == [
        "Needs help now", "Needs help soon", "Can wait"]
    assert words.provisional_pill("P1") == "maybe Needs help now"
    assert words.provisional_pill(None) is None
    assert (words.CHECK_FIRST, words.SET_ASIDE) == ("Check first", "Set aside")
    assert words.other_posts(0) is None
    assert words.other_posts(1) == "+1 other post" and words.other_posts(3) == "+3 other posts"


def test_headline_is_composed_from_fields():
    e = extracted(people=3, vulnerable=1, hazards=["rising_water", "on_roof"])
    assert words.headline(e) == "3 people, water rising and on a roof, with a child"
    e = extracted(people=None)
    e["needs"] = [{"kind": "water", "hours_without": 30}]
    assert words.headline(e) == "Needs drinking water"
    assert "Without drinking water for 30 h" in words.details(e)
    assert words.contact_line("sms", "555-0100", "5 min ago") == (
        "Text message from 555-0100 · 5 min ago")


# ---- ordering parity with /board --------------------------------------------------------


def test_next_equals_first_board_tier_row(store):
    seed_mixed(store)
    board = build_board(store)
    first = next(r for t in board["tiers"] for r in t["rows"] if r["status"] is None)
    q = build_queue(board)
    assert q["next"][0]["id"] == first["request_id"] == "C0003"
    # Same order as the board, tier by tier.
    assert [c["id"] for c in q["next"]] == [
        r["request_id"] for t in board["tiers"] for r in t["rows"]]
    html = page(store)
    assert visible_next_id(html) == "C0003"
    assert "Needs help now" in card(html, "C0003")


def test_next_moves_on_when_assigned_and_escalated_comes_first(store):
    seed_mixed(store)
    set_status(store, "C0003", "assigned", {"crew": "Boat 2"})
    html = page(store)
    assert visible_next_id(html) == "B0002"
    assert 'data-request-id="C0003"' in section(html, "out")
    set_status(store, "D0004", "escalated", {"reason": "road closed"})
    html = page(store)
    assert visible_next_id(html) == "D0004"
    assert "Couldn't get there" in card(section(html, "next"), "D0004")
    set_status(store, "A0001", "rescued", {"rescued_count": 2})
    assert 'data-request-id="A0001"' not in page(store)


def test_waiting_lists_others_then_check_first_then_set_aside(store):
    seed_mixed(store)
    w = section(page(store), "waiting")
    hidden_first = re.search(r'<div class="waiting-item" data-request-id="C0003" hidden>', w)
    assert hidden_first
    assert w.index('"B0002"') < w.index('"A0001"') < w.index('"D0004"') < w.index("Check first")
    assert w.index("Check first") < w.index('"E0005"') < w.index('<details class="set-aside">')
    assert w.index('<details class="set-aside">') < w.index('"F0006"')
    assert "Set aside (1)" in w


def test_set_aside_and_repeats_never_in_next(store):
    add(store, "R0001", output("P2", "R-P2-01"), extracted(location="loc-r"))
    add(store, "R0002", output("P1", "R-DUP-01", "duplicate", duplicate_of="R0001"),
        extracted(location="loc-r"), at="2026-11-07T10:30:00Z")
    add(store, "R0003", output("P2", "R-DUP-01", "duplicate", duplicate_of="R0001"),
        extracted(location="loc-r"), at="2026-11-07T10:40:00Z")
    add(store, "N0001", output(None, "R-NAR-01", "not_a_request"), raw="thanks all")
    html = page(store)
    nxt = section(html, "next")
    assert visible_next_id(html) == "R0001"
    assert "N0001" not in nxt
    assert 'data-request-id="R0002"' not in html and 'data-request-id="R0003"' not in html
    c = card(nxt, "R0001")
    assert "+2 other posts" in c and "Needs help now" in c


# ---- Check first ------------------------------------------------------------------------


def test_check_first_question_exact_and_no_send_form(store):
    q = 'Is anyone hurt & where are you <exactly>?'
    add(store, "Q0001", output(None, "R-ABS-LOC", "abstain", question=q,
        reason="no_usable_location", provisional="P1"), extracted(location=None))
    html = page(store)
    c = card(html, "Q0001")
    assert f'<p class="question" lang="en">{htmllib.escape(q, quote=False)}</p>' in c
    assert htmllib.unescape(re.search(r'data-copy="([^"]*)"', c).group(1)) == q
    assert '<span class="pill pill-provisional">maybe Needs help now</span>' in c
    assert "Check first" in c and "No address yet" in c
    assert "<form" not in html and "Send" not in c
    assert visible_next_id(html) is None and "Nobody is waiting right now." in html


def test_ranked_card_shows_question_from_abstained_root_lifted_by_duplicate(store):
    """Review O-08: an abstained hoax root joined by a later P1 duplicate keeps its flag."""
    add(store, "H0001", output(None, "R-ABS-HOAX", "abstain", question=Q_HOAX,
        reason="possible_hoax", provisional="P2"), extracted(location="loc-h"))
    add(store, "H0002", output("P1", "R-DUP-01", "duplicate", duplicate_of="H0001"),
        extracted(location="loc-h", hazards=["injury"]), at="2026-11-07T10:20:00Z")
    board = build_board(store)
    assert board["tiers"][0]["rows"][0]["request_id"] == "H0001"
    html = page(store)
    c = card(section(html, "next"), "H0001")
    assert "Needs help now" in c
    assert f'<p class="question" lang="en">{Q_HOAX}</p>' in c
    assert '<span class="pill pill-check">Might not be real</span>' in c
    # The grouped follow-up is a repeat: no Check first card of its own.
    assert html.count('<article class="card card-check"') == 0
    assert section(html, "next").count(Q_HOAX) == 2  # the question text and its copy button


def test_ranked_card_shows_prioritized_decision_question(store):
    add(store, "P0001", output("P1", "R-P1-05", question=Q_CONFIRM), extracted(location="loc-p"))
    c = card(section(page(store), "next"), "P0001")
    assert f'<p class="question" lang="en">{Q_CONFIRM}</p>' in c
    assert '<span class="pill pill-check">Needs confirmation</span>' in c


def test_spanish_line_when_words_es_exists(store, monkeypatch):
    import sys
    import types

    es = types.ModuleType("rescue_field.words_es")
    es.QUESTIONS = {Q_LOC: "¿Cuál es la dirección?"}
    monkeypatch.setitem(sys.modules, "rescue_field.words_es", es)
    add(store, "S0001", output(None, "R-ABS-LOC", "abstain", question=Q_LOC,
        reason="no_usable_location", provisional="P2"), extracted(location=None), lang="es")
    add(store, "S0002", output(None, "R-ABS-LOC", "abstain", question=Q_LOC,
        reason="no_usable_location", provisional="P2"), extracted(location=None))
    html = page(store)
    assert '<p class="question-es" lang="es">¿Cuál es la dirección?</p>' in card(html, "S0001")
    assert "question-es" not in card(html, "S0002")


# ---- what the volunteer never sees ------------------------------------------------------


def test_no_internal_details_in_html(store):
    seed_mixed(store)
    html = page(store)
    assert not re.search(r"R-(P[123]|ABS|NAR|DUP)-", html)
    for leak in (RULES, MODEL, "rules@", "hf:", "0.93", "confidence", "Model:"):
        assert leak not in html, leak
    for rid, sentence in (("C0003", words.RULE_SENTENCES["R-P1-01"]),
                          ("E0005", words.RULE_SENTENCES["R-ABS-LOC"])):
        assert sentence in htmllib.unescape(card(html, rid))


def test_card_parts(store):
    add(store, "ZZABCD", output("P1", "R-P1-02"), extracted(location="12 Oak St",
        hazards=["injury"]), contact="555-0100")
    c = card(page(store), "ZZABCD")
    assert '<p class="address">12 Oak St</p>' in c
    assert "#ABCD" in c and "Text message from 555-0100" in c
    assert "<summary>Why is this one first?</summary>" in c
    assert "Check again" not in c  # no recheck endpoint (#302) yet


def test_fragment_and_board_route(store):
    seed_mixed(store)
    frag = page(store, headers={"HX-Request": "true"})
    assert "<html" not in frag and 'id="screen-next"' in frag
    full = page(store)
    assert "<html" in full and "Skip for now" in full and "setInterval(refresh" in full
    assert "Out now" in full and "Waiting" in full
    with TestClient(create_app(store=store)) as client:
        board = client.get("/board")
    assert board.status_code == 200 and "Rescue triage board" in board.text


def test_empty_queue(store):
    html = page(store)
    assert "Nobody is waiting right now." in html and "Nobody is out right now." in html


def test_check_again_link_when_recheck_route_exists(store):
    add(store, "K0001", output("P2", "R-P2-01"))
    app = create_app(store=store)

    @app.post("/requests/{request_id}/recheck")
    def recheck(request_id: str):  # stand-in for #302
        return {}

    with TestClient(app) as client:
        html = client.get("/").text
    assert 'class="recheck" data-request-id="K0001">Check again</a>' in html
