"""E2: step 3 priority tiers + uncertainty escalation (contracts/engine_rules.md)."""

import copy
import json
from pathlib import Path

import pytest

from rescue_engine.fallback import decide_one
from rescue_engine.fallback.rules import Q_CONFIRM, Q_HAZARD, RULES_VERSION, TIER_RULES

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    json.loads(line)
    for line in (ROOT / "contracts/fixtures/cases.jsonl").read_text().splitlines()
]
TIER_CASES = [c for c in CASES if c["expected"]["rule_id"].startswith(("R-P", "R-ABS-HAZ"))]
BASE = next(c for c in CASES if c["case_id"] == "C08")  # P3 evacuation, nothing else


def inp(case):
    return {"extracted": case["extracted"], "context": case["context"]}


def make(hazards=(), vulnerable=(), needs=(), kind="other", **thresholds):
    """BASE with the given signals. hazards/vulnerable: (kind, confidence) pairs."""
    case = copy.deepcopy(BASE)
    ex = case["extracted"]
    ex["hazards"] = [{"kind": k, "confidence": c} for k, c in hazards]
    ex["vulnerable"] = [{"kind": k, "confidence": c} for k, c in vulnerable]
    ex["needs"] = [{"kind": k, "hours_without": h} for k, h in needs]
    ex["request_kind"] = kind
    case["context"]["thresholds"].update(thresholds)
    return inp(case)


def decided(**kw):
    out = decide_one(make(**kw))
    return out["outcome"], out["priority_tier"], out["rule_id"], out["follow_up_question"]


def test_fixture_coverage():
    ids = {c["case_id"] for c in TIER_CASES}
    assert ids == {
        "C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09", "C16", "C17", "C25",
    }  # fmt: skip


@pytest.mark.parametrize("case", TIER_CASES, ids=lambda c: c["case_id"])
def test_fixture_cases(case):
    assert decide_one(inp(case)) == {**case["expected"], "rules_version": RULES_VERSION}


# One firm example per rubric row; each must hit exactly its own rule id.
ROWS = {
    "R-P1-01": dict(hazards=[("rising_water", 0.9), ("trapped", 0.9)]),
    "R-P1-02": dict(hazards=[("injury", 0.9)]),
    "R-P1-03": dict(hazards=[("medical_device_no_power", 0.9)]),
    "R-P1-04": dict(hazards=[("rising_water", 0.9)], vulnerable=[("disabled", 0.9)]),
    "R-P1-05": dict(hazards=[("fire", 0.9)]),
    "R-P2-01": dict(hazards=[("on_roof", 0.9)]),
    "R-P2-02": dict(vulnerable=[("pregnant", 0.9)]),
    "R-P2-03": dict(needs=[("meds", 25)]),
    "R-P2-04": dict(hazards=[("rising_water", 0.9)]),
    "R-P3-01": dict(kind="evacuation"),
    "R-P3-02": dict(kind="welfare_check"),
    "R-P3-03": dict(kind="supply"),
    "R-P3-04": dict(kind="other"),
}


def test_every_rule_has_a_row_test():
    assert [r[0] for r in TIER_RULES] == list(ROWS)


@pytest.mark.parametrize("rule_id", ROWS)
def test_each_rubric_row(rule_id):
    assert decided(**ROWS[rule_id]) == ("prioritized", rule_id[2:4], rule_id, None)


def test_rising_water_with_on_roof_is_p1_01():
    assert decided(hazards=[("rising_water", 0.9), ("on_roof", 0.8)])[2] == "R-P1-01"


def test_rescue_kind_alone_is_p2_04():
    assert decided(kind="rescue")[1:3] == ("P2", "R-P2-04")


def test_vulnerable_outranks_evacuation_kind():
    assert decided(vulnerable=[("elderly", 0.9)], kind="evacuation")[2] == "R-P2-02"


def test_p1_04_needs_child_elderly_or_disabled():
    out = decided(hazards=[("rising_water", 0.9)], vulnerable=[("medical", 0.9)])
    assert out[2] == "R-P2-02"  # P2-01 does not apply, P2-02 (any vulnerable) does


@pytest.mark.parametrize("need,rule", [
    (("water", 24), "R-P3-03"),  # exactly 24 h is not > 24 h
    (("water", 24.5), "R-P2-03"),
    (("food", None), "R-P3-03"),  # unknown hours never triggers
])
def test_need_hours_boundary(need, rule):
    assert decided(needs=[need], kind="supply")[2] == rule


def test_need_hours_threshold_from_context():
    assert decided(needs=[("food", 30)], kind="supply", need_hours_over=48)[2] == "R-P3-03"


# --- uncertainty -------------------------------------------------------------------------

@pytest.mark.parametrize("conf", [0.5, 0.6, 0.74])
def test_uncertain_hazard_raises_tier_with_confirm(conf):
    assert decided(hazards=[("fire", conf)], kind="supply") == (
        "prioritized", "P1", "R-P1-05", Q_CONFIRM)


def test_uncertain_vulnerable_raises_tier_with_confirm():
    out = decided(hazards=[("rising_water", 0.9)], vulnerable=[("child", 0.6)])
    assert out == ("prioritized", "P1", "R-P1-04", Q_CONFIRM)


def test_uncertain_vulnerable_raises_p3_to_p2():
    assert decided(vulnerable=[("elderly", 0.55)], kind="welfare_check") == (
        "prioritized", "P2", "R-P2-02", Q_CONFIRM)


def test_firm_at_075_needs_no_confirm():
    assert decided(hazards=[("fire", 0.75)])[3] is None


def test_uncertain_signal_that_does_not_raise_adds_no_question():
    # injury is firm P1; an uncertain on_roof adds nothing.
    out = decided(hazards=[("injury", 0.9), ("on_roof", 0.6)])
    assert out == ("prioritized", "P1", "R-P1-02", None)


def test_never_lowers_tier_for_uncertainty():
    # A firm P1 stays P1 whatever uncertain/weak signals come with it.
    out = decided(hazards=[("fire", 0.9), ("trapped", 0.3)], vulnerable=[("child", 0.6)])
    assert out[:3] == ("prioritized", "P1", "R-P1-05")


@pytest.mark.parametrize("conf", [0.25, 0.4, 0.49])
def test_weak_raising_signal_abstains(conf):
    out = decide_one(make(hazards=[("injury", conf)], kind="supply"))
    assert (out["outcome"], out["rule_id"], out["abstain_reason"], out["priority_tier"]) == (
        "abstain", "R-ABS-HAZ", "low_confidence_hazard", None)
    assert out["follow_up_question"] == Q_HAZARD


def test_weak_signal_that_does_not_raise_is_ignored():
    out = decided(hazards=[("fire", 0.9), ("trapped", 0.4)])
    assert out == ("prioritized", "P1", "R-P1-05", None)


def test_below_weak_min_is_ignored():
    assert decided(hazards=[("fire", 0.24)], kind="supply") == (
        "prioritized", "P3", "R-P3-03", None)


def test_weak_signal_above_uncertain_raise():
    # uncertain trapped -> P2; weak rising_water would make it P1. Changed 2026-11-07 (R2):
    # a P2 stays prioritized with the hazard question instead of abstaining.
    out = decided(hazards=[("trapped", 0.6), ("rising_water", 0.3)])
    assert out == ("prioritized", "P2", "R-P2-01", Q_HAZARD)


def test_bands_follow_context_thresholds():
    # Raise the firm edge: 0.8 becomes uncertain -> confirm question.
    assert decided(hazards=[("fire", 0.8)], hazard_firm_min=0.85)[3] == Q_CONFIRM
