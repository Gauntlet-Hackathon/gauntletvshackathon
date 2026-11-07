"""E3: duplicates, contradictions, hoaxes (contracts/engine_rules.md steps 4-5)."""

import copy
import json
from pathlib import Path

import pytest

from rescue_engine.fallback import decide_one
from rescue_engine.fallback.rules import Q_COUNTS, Q_HOAX, RULES_VERSION

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    json.loads(line)
    for line in (ROOT / "contracts/fixtures/cases.jsonl").read_text().splitlines()
]
E3_CASES = [
    c for c in CASES if c["expected"]["rule_id"] in {"R-DUP-01", "R-ABS-HOAX", "R-ABS-CNT"}
]


def inp(case):
    return {"extracted": case["extracted"], "context": case["context"]}


def by_id(case_id):
    return next(c for c in CASES if c["case_id"] == case_id)


def test_fixture_coverage():
    assert {c["case_id"] for c in E3_CASES} == {f"C{n}" for n in range(18, 25)}


@pytest.mark.parametrize("case", E3_CASES, ids=lambda c: c["case_id"])
def test_fixture_cases(case):
    assert decide_one(inp(case)) == {**case["expected"], "rules_version": RULES_VERSION}


def variant(case_id, candidates=None, **ctx):
    c = copy.deepcopy(by_id(case_id))
    if candidates is not None:
        c["context"]["duplicate_candidates"] = candidates
    c["context"].update(ctx)
    return c


def cand(rid, at, count=2, contact=False, tier="P3"):
    return {
        "request_id": rid, "received_at": at, "people_count": count,
        "contact_match": contact, "priority_tier": tier,
    }


def test_duplicate_of_is_earliest_and_group_keeps_highest_tier():
    # C24 is P3 on its own; the oldest candidate is P3, a later one P1.
    c = variant("C24", [
        cand("A", "2026-11-07T06:00:00Z", 4, tier="P3"),
        cand("B", "2026-11-07T06:30:00Z", 4, tier="P1"),
    ])
    out = decide_one(inp(c))
    assert (out["outcome"], out["duplicate_of"], out["priority_tier"]) == ("duplicate", "A", "P1")


def test_duplicate_own_tier_higher_than_group():
    out = decide_one(inp(by_id("C23")))  # own P1 beats candidate P2
    assert out["priority_tier"] == "P1"


def test_candidate_with_null_tier_is_ignored():
    c = variant("C24", [cand("A", "2026-11-07T06:00:00Z", 4, tier=None)])
    assert decide_one(inp(c))["priority_tier"] == "P3"


def test_unknown_count_is_not_a_conflict():
    c = variant("C18", [cand("A", "2026-11-07T06:00:00Z", None, contact=True, tier="P2")])
    assert decide_one(inp(c))["outcome"] == "duplicate"


def test_different_count_without_contact_match_is_duplicate():
    c = variant("C18", [cand("A", "2026-11-07T06:00:00Z", 2, contact=False, tier="P2")])
    assert decide_one(inp(c))["outcome"] == "duplicate"


def test_conflict_in_any_candidate_abstains_with_question():
    c = variant("C18", [
        cand("A", "2026-11-07T06:00:00Z", 5, contact=True),
        cand("B", "2026-11-07T06:30:00Z", 2, contact=True),
    ])
    out = decide_one(inp(c))
    assert out["abstain_reason"] == "conflicting_counts"
    assert out["follow_up_question"] == Q_COUNTS
    assert out["priority_tier"] is None and out["duplicate_of"] is None


def test_hoax_needs_low_request_confidence():
    c = variant("C20")
    c["extracted"]["is_request"]["confidence"] = 0.9
    assert decide_one(inp(c))["outcome"] == "prioritized"
    c["extracted"]["is_request"]["confidence"] = 0.85  # boundary: not below the max
    assert decide_one(inp(c))["outcome"] == "prioritized"


def test_hoax_needs_marker_hit():
    c = variant("C20", hoax_marker_hits=0)
    assert decide_one(inp(c))["outcome"] == "prioritized"


def test_hoax_is_never_discarded_and_asks_a_human():
    out = decide_one(inp(by_id("C20")))
    assert out["outcome"] == "abstain" and out["follow_up_question"] == Q_HOAX


def test_hoax_beats_duplicate():
    c = variant("C21", [cand("A", "2026-11-07T06:00:00Z", 4)])
    assert decide_one(inp(c))["abstain_reason"] == "possible_hoax"
