import copy
import json

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app
from rescue_field.board import build_board
from rescue_field.store import EventStore
from rescue_shared.contracts_io import CONTRACTS
from rescue_shared.ulid import new_ulid

EXAMPLES = CONTRACTS / "examples"
BASE_EXTRACTED = json.loads((EXAMPLES / "payload-extracted.valid.json").read_text())
RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
MODEL = "hf:rescue-org/rescue-tagger@4d5e6f7a8b"


def extracted(people=2, vulnerable=0, location="Elm St"):
    e = copy.deepcopy(BASE_EXTRACTED)
    e["location"]["text"] = location
    e["people_count"]["value"] = people
    e["vulnerable"] = [{"kind": "child", "confidence": 0.9}] * vulnerable
    return e


def output(tier, rule="R-P2-01", outcome="prioritized", duplicate_of=None, question=None):
    return {
        "outcome": outcome,
        "priority_tier": tier,
        "rule_id": rule,
        "duplicate_of": duplicate_of,
        "abstain_reason": "low_confidence_request" if outcome == "abstain" else None,
        "provisional_tier": None,
        "follow_up_question": question,
        "rules_version": RULES,
    }


def add(store, rid, out, ext=None, at="2026-11-07T10:00:00Z", rules=RULES, model=MODEL, raw="help"):
    ext = ext or extracted()
    payloads = [
        ("received", {"channel": "sms", "raw_text": raw, "lang_hint": None, "contact": None}),
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
            "rules_version": rules,
            "model_version": model,
            "payload": payload,
        }
        for kind, payload in payloads
    )


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


def get(store, **kw):
    with TestClient(create_app(store=store)) as client:
        r = client.get("/", **kw)
    assert r.status_code == 200, r.text
    return r


def test_empty_board(store):
    r = get(store)
    assert "Follow-up queue" in r.text and "None." in r.text


def test_tiers_listed_p1_then_p2_then_p3(store):
    add(store, "R3", output("P3", "R-P3-01"), extracted(location="loc-three"))
    add(store, "R1", output("P1", "R-P1-01"), extracted(location="loc-one"))
    add(store, "R2", output("P2"), extracted(location="loc-two"))
    text = get(store).text
    assert text.index("loc-one") < text.index("loc-two") < text.index("loc-three")
    assert text.index('id="tier-P1"') < text.index('id="tier-P2"') < text.index('id="tier-P3"')


def test_within_tier_vulnerable_then_people_then_older(store):
    add(store, "A", output("P2"), extracted(people=9), at="2026-11-07T09:00:00Z")
    add(store, "B", output("P2"), extracted(people=2, vulnerable=1), at="2026-11-07T11:00:00Z")
    add(store, "C", output("P2"), extracted(people=5), at="2026-11-07T10:00:00Z")
    add(store, "D", output("P2"), extracted(people=5), at="2026-11-07T08:00:00Z")
    rows = build_board(store)["tiers"][1]["rows"]
    assert [r["request_id"] for r in rows] == ["B", "A", "D", "C"]


def test_abstains_in_follow_up_queue_with_exact_question(store):
    q = "Is anyone hurt, and what is the nearest cross street?"
    add(store, "AB", output(None, "R-ABS-01", "abstain", question=q), extracted(location="loc-ab"))
    board = build_board(store)
    assert [r["request_id"] for r in board["follow_ups"]] == ["AB"]
    assert all(not t["rows"] for t in board["tiers"])
    text = get(store).text
    assert f'<strong class="question">{q}</strong>' in text
    assert text.index("Follow-up queue") < text.index(q)


def test_not_a_request_is_left_off(store):
    add(store, "N", output(None, "R-NAR-01", "not_a_request"), extracted(location="loc-nar"))
    assert "loc-nar" not in get(store).text


def test_duplicates_collapse_under_earliest_with_highest_tier(store):
    add(store, "ORIG", output("P3", "R-P3-01"), extracted(location="loc-orig"),
        at="2026-11-07T09:00:00Z")
    add(store, "DUP1", output("P1", "R-P1-01", "duplicate", duplicate_of="ORIG"),
        extracted(location="loc-dup1"), at="2026-11-07T09:30:00Z")
    add(store, "DUP2", output("P2", "R-P2-01", "duplicate", duplicate_of="DUP1"),
        extracted(location="loc-dup2"), at="2026-11-07T09:45:00Z")
    board = build_board(store)
    assert [len(t["rows"]) for t in board["tiers"]] == [1, 0, 0]
    row = board["tiers"][0]["rows"][0]
    assert row["request_id"] == "ORIG"
    assert [d["request_id"] for d in row["duplicates"]] == ["DUP1", "DUP2"]
    text = get(store).text
    assert "loc-orig" in text and "loc-dup1" not in text and "loc-dup2" not in text
    assert "2 duplicate(s)" in text
    assert "R-P1-01" in text  # the rule of the member that set the group's tier


def test_row_shows_versions_and_expands_to_fields_with_confidences(store):
    add(store, "X", output("P1", "R-P1-01"), extracted(location="loc-x"),
        rules="rules@zzz+bin:0000", model="hf:test@abc")
    text = get(store).text
    assert '<span class="rule">R-P1-01</span>' in text
    assert 'class="rules-version">rules@zzz+bin:0000<' in text
    assert 'class="model-version">hf:test@abc<' in text
    assert "<details>" in text and "Extracted fields" in text
    assert "0.81" in text and "rising_water" in text and "0.9" in text


def test_htmx_request_gets_fragment_only(store):
    add(store, "X", output("P2"), extracted(location="loc-x"))
    r = get(store, headers={"HX-Request": "true"})
    assert "<html" not in r.text and "loc-x" in r.text


def abstain(provisional=None, reason="no_usable_location"):
    out = output(None, "R-ABS-LOC", "abstain", question="Where are you?")
    out["abstain_reason"] = reason
    if provisional is not None:
        out["provisional_tier"] = provisional
    return out


def test_follow_ups_sorted_by_provisional_tier_then_within_tier_key(store):
    # oldest first by arrival, but P1 must jump the queue (DECISIONS R1)
    add(store, "OLD3", abstain("P3"), extracted(people=9), at="2026-11-07T08:00:00Z")
    add(store, "NONE", abstain(None), extracted(people=9), at="2026-11-07T07:00:00Z")
    add(store, "P2", abstain("P2"), extracted(), at="2026-11-07T09:00:00Z")
    add(store, "P1B", abstain("P1"), extracted(people=2), at="2026-11-07T10:00:00Z")
    add(store, "P1A", abstain("P1"), extracted(people=2, vulnerable=1), at="2026-11-07T11:00:00Z")
    order = [r["request_id"] for r in build_board(store)["follow_ups"]]
    # within P1: vulnerable first; a missing provisional_tier sorts after P3
    assert order == ["P1A", "P1B", "P2", "OLD3", "NONE"]


def test_follow_ups_without_provisional_tier_keep_within_tier_order(store):
    add(store, "A", abstain(), extracted(people=1), at="2026-11-07T08:00:00Z")
    add(store, "B", abstain(), extracted(people=5), at="2026-11-07T09:00:00Z")
    add(store, "C", abstain(), extracted(people=1), at="2026-11-07T07:00:00Z")
    order = [r["request_id"] for r in build_board(store)["follow_ups"]]
    assert order == ["B", "C", "A"]


def test_follow_up_card_shows_provisional_tier(store):
    add(store, "AB", abstain("P1"), extracted(location="loc-ab"))
    add(store, "NO", abstain(None), extracted(location="loc-no"))
    text = get(store).text
    assert 'data-provisional-tier="P1"' in text
    assert '<span class="provisional-tier tier-P1"' in text and "P1?</span>" in text
    assert 'data-provisional-tier=""' in text  # no badge without one
    assert text.count('class="provisional-tier') == 1


def test_follow_up_group_takes_highest_provisional_tier(store):
    # A duplicate never carries a provisional_tier (contract), so a member without one must
    # neither lower nor drop the group's tier: the group keeps the highest tier it has (the
    # root's P1), and still jumps the older P2 follow-up.
    add(store, "ORIG", abstain("P1"), extracted(location="loc-orig"), at="2026-11-07T09:00:00Z")
    dup = abstain(None)
    dup["outcome"], dup["duplicate_of"] = "duplicate", "ORIG"
    dup["abstain_reason"] = None
    assert dup["provisional_tier"] is None
    add(store, "DUP", dup, extracted(location="loc-dup"), at="2026-11-07T09:30:00Z")
    add(store, "OTHER", abstain("P2"), extracted(location="loc-other"), at="2026-11-07T08:00:00Z")
    follow = build_board(store)["follow_ups"]
    assert [(r["request_id"], r["provisional_tier"]) for r in follow] == [
        ("ORIG", "P1"),
        ("OTHER", "P2"),
    ]
    assert [d["request_id"] for d in follow[0]["duplicates"]] == ["DUP"]
