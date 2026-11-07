"""Step 3 review fixes (docs/DECISIONS.md, "Step 3 review fixes (2026-11-07)").

One block per finding: R1-R6, R8-R11, R13 and A9. The Mercury engine is held to the same
behaviour by test_parity.py (REVIEW_INPUTS there reuses the inputs built here).
"""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rescue_engine.fallback import decide_one
from rescue_engine.fallback.rules import (
    Q_CONFIRM,
    Q_COUNTS,
    Q_HAZARD,
    Q_HAZARD_VULNERABLE,
    Q_HOAX,
    Q_LOCATION,
    Q_REQUEST,
)
from rescue_shared.contracts_io import validator_for

ROOT = Path(__file__).resolve().parents[2]
CASES = {
    c["case_id"]: c
    for c in (
        json.loads(line)
        for line in (ROOT / "contracts/fixtures/cases.jsonl").read_text().splitlines()
        if line.strip()
    )
}
VALIDATOR = validator_for("engine_output.schema.json")


def inp(case_id: str) -> dict:
    c = copy.deepcopy(CASES[case_id])
    return {"extracted": c["extracted"], "context": c["context"]}


def signals(base: str, hazards=(), vulnerable=(), kind=None) -> dict:
    """A fixture's input with its hazards/vulnerable entries replaced by (kind, conf) pairs."""
    item = inp(base)
    ex = item["extracted"]
    ex["hazards"] = [{"kind": k, "confidence": c} for k, c in hazards]
    ex["vulnerable"] = [{"kind": k, "confidence": c} for k, c in vulnerable]
    if kind is not None:
        ex["request_kind"] = kind
    return item


def decide(item: dict) -> dict:
    out = decide_one(item)
    VALIDATOR.validate(out)
    return out


def cand(rid="01JCB00000000000000000X001", count=None, contact=False, tier="P3"):
    return {
        "request_id": rid, "received_at": "2026-11-07T05:00:00Z", "people_count": count,
        "contact_match": contact, "priority_tier": tier,
    }  # fmt: skip


# --- R1: provisional_tier ------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_id,tier",
    [("C12", "P3"), ("C13", "P3"), ("C14", "P1"), ("C15", "P2"), ("C16", "P2"),
     ("C17", "P1"), ("C18", "P2"), ("C19", "P1"), ("C20", "P2"), ("C21", "P2")],
)  # fmt: skip
def test_every_abstain_fixture_carries_its_provisional_tier(case_id, tier):
    out = decide(inp(case_id))
    assert out["outcome"] == "abstain"
    assert (out["priority_tier"], out["provisional_tier"]) == (None, tier)


def test_non_abstain_outcomes_have_null_provisional_tier():
    for case_id, case in CASES.items():
        out = decide(inp(case_id))
        assert out == case["expected"]
        if out["outcome"] != "abstain":
            assert out["provisional_tier"] is None, case_id


def test_provisional_tier_counts_weak_signals():
    # No location; a weak injury is the only P1 hint. The cautious reading is P1.
    item = signals("C14", hazards=[("injury", 0.3)], kind="supply")
    assert decide(item)["provisional_tier"] == "P1"
    # Below the weak band it is ignored.
    item = signals("C14", hazards=[("injury", 0.2)], kind="supply")
    assert decide(item)["provisional_tier"] == "P3"


def test_schema_requires_provisional_tier_exactly_on_abstain():
    abstain = decide(inp("C14"))
    assert not list(VALIDATOR.iter_errors(abstain))
    assert list(VALIDATOR.iter_errors({**abstain, "provisional_tier": None}))
    assert list(VALIDATOR.iter_errors({k: v for k, v in abstain.items()
                                       if k != "provisional_tier"}))
    for case_id in ("C01", "C10", "C22"):  # prioritized, not_a_request, duplicate
        out = decide(inp(case_id))
        assert list(VALIDATOR.iter_errors({**out, "provisional_tier": "P1"})), case_id


# --- R2: weak raising signal no longer lowers a P2 ------------------------------------------


@pytest.mark.parametrize(
    "hazards",
    [[("trapped", 0.95), ("rising_water", 0.3)],  # firm P2 trapped, weak P1 combination
     [("rising_water", 0.95), ("fire", 0.4)]],
)  # fmt: skip
def test_weak_raise_on_p2_stays_prioritized_with_hazard_question(hazards):
    out = decide(signals("C05", hazards=hazards))
    assert (out["outcome"], out["priority_tier"], out["follow_up_question"]) == (
        "prioritized", "P2", Q_HAZARD)
    assert out["rule_id"].startswith("R-P2-")


def test_weak_raise_on_p3_still_abstains():
    out = decide(signals("C08", hazards=[("fire", 0.3)]))  # evacuation P3, weak fire P1
    assert (out["outcome"], out["rule_id"], out["provisional_tier"]) == (
        "abstain", "R-ABS-HAZ", "P1")


def test_weak_raise_question_wins_over_confirm():
    # firm P3, uncertain trapped -> P2 (would add confirm), weak rising_water -> P1.
    out = decide(signals("C08", hazards=[("trapped", 0.6), ("rising_water", 0.3)]))
    assert (out["priority_tier"], out["follow_up_question"]) == ("P2", Q_HAZARD)


# --- R3: hoax check after tiering -----------------------------------------------------------


def test_p1_possible_hoax_stays_prioritized_with_hoax_question():
    item = inp("C01")  # firm R-P1-01
    item["context"]["hoax_marker_hits"] = 1
    item["extracted"]["is_request"]["confidence"] = 0.84
    out = decide(item)
    assert (out["outcome"], out["priority_tier"], out["rule_id"], out["follow_up_question"]) == (
        "prioritized", "P1", "R-P1-01", Q_HOAX)


def test_p2_possible_hoax_still_abstains_with_tier():
    out = decide(inp("C21"))
    assert (out["outcome"], out["abstain_reason"], out["provisional_tier"]) == (
        "abstain", "possible_hoax", "P2")


def test_hoax_over_low_confidence_hazard():
    item = signals("C08", hazards=[("fire", 0.3)])
    item["context"]["hoax_marker_hits"] = 2
    item["extracted"]["is_request"]["confidence"] = 0.7
    out = decide(item)
    assert (out["abstain_reason"], out["provisional_tier"]) == ("possible_hoax", "P1")


def test_location_is_checked_before_hoax():
    item = inp("C20")
    item["extracted"]["location"] = {"text": None, "type": "none", "confidence": 0.9}
    assert decide(item)["abstain_reason"] == "no_usable_location"


def test_p1_hoax_duplicate_keeps_hoax_question_when_it_sets_the_group_tier():
    item = inp("C01")
    item["context"]["hoax_marker_hits"] = 1
    item["extracted"]["is_request"]["confidence"] = 0.8
    item["context"]["duplicate_candidates"] = [cand(tier="P3")]
    out = decide(item)
    assert (out["outcome"], out["priority_tier"], out["follow_up_question"]) == (
        "duplicate", "P1", Q_HOAX)


# --- R4: conflicting_counts carries the group tier ------------------------------------------


def test_conflicting_counts_takes_highest_of_request_and_candidates():
    # C19 vs C03: both P1.
    assert decide(inp("C19"))["provisional_tier"] == "P1"
    # A firm P1 update against a P3 original: the P1 is kept.
    item = inp("C19")
    item["context"]["duplicate_candidates"][0]["priority_tier"] = "P3"
    assert decide(item)["provisional_tier"] == "P1"
    # A P2 update against a P1 original: the original's P1 is kept.
    item = inp("C18")
    item["context"]["duplicate_candidates"][0]["priority_tier"] = "P1"
    out = decide(item)
    assert (out["abstain_reason"], out["provisional_tier"], out["follow_up_question"]) == (
        "conflicting_counts", "P1", Q_COUNTS)


# --- R5: a stale P1 goes to a human ---------------------------------------------------------


def test_stale_p1_abstains_low_confidence_request():
    item = inp("C01")
    item["extracted"]["time_ref"] = {"text": "Aug 2017", "is_stale_post": True, "confidence": 0.6}
    out = decide(item)
    assert (out["outcome"], out["rule_id"], out["abstain_reason"], out["provisional_tier"],
            out["follow_up_question"]) == (
        "abstain", "R-ABS-REQ", "low_confidence_request", "P1", Q_REQUEST)  # fmt: skip


def test_stale_p1_by_weak_signal_also_abstains():
    item = signals("C11", hazards=[("rising_water", 0.8), ("on_roof", 0.3)])
    assert decide(item)["provisional_tier"] == "P1"


def test_stale_p2_is_still_not_a_request():
    out = decide(inp("C11"))  # rising_water + rescue -> P2
    assert (out["outcome"], out["rule_id"]) == ("not_a_request", "R-NAR-02")


# --- R6: acute medical emergency extracted as injury ----------------------------------------


def test_acute_medical_emergency_fixture_is_p1():
    case = CASES["C25"]
    assert [h["kind"] for h in case["extracted"]["hazards"]] == ["injury"]
    assert decide(inp("C25"))["rule_id"] == "R-P1-02"


# --- R8: threshold order ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "th",
    [{"hazard_firm_min": 0.4},  # firm < uncertain (0.5)
     {"hazard_weak_min": 0.6},  # weak > uncertain
     {"hazard_firm_min": 0.4, "hazard_uncertain_min": 0.5, "hazard_weak_min": 0.25}],
)  # fmt: skip
def test_unordered_hazard_thresholds_are_rejected(th):
    item = inp("C01")
    item["context"]["thresholds"].update(th)
    with pytest.raises(ValueError, match="hazard_weak_min"):
        decide_one(item)


def test_equal_hazard_thresholds_are_allowed():
    item = inp("C01")
    item["context"]["thresholds"].update(
        hazard_firm_min=0.5, hazard_uncertain_min=0.5, hazard_weak_min=0.5)
    assert decide(item)["priority_tier"] == "P1"


# --- R9: duplicate keeps the confirm question when its own signal set the group tier ----------


def _c04_with(candidate_tier):
    item = inp("C04")  # firm rising_water, uncertain on_roof -> P1 with confirm
    item["context"]["duplicate_candidates"] = [cand(tier=candidate_tier)]
    return item


@pytest.mark.parametrize("tier", ["P3", "P2", None])
def test_duplicate_keeps_confirm_when_it_sets_group_tier(tier):
    out = decide(_c04_with(tier))
    assert (out["outcome"], out["priority_tier"], out["follow_up_question"]) == (
        "duplicate", "P1", Q_CONFIRM)


def test_duplicate_drops_confirm_when_group_already_has_the_tier():
    out = decide(_c04_with("P1"))
    assert (out["priority_tier"], out["follow_up_question"]) == ("P1", None)


def test_duplicate_fixtures_unchanged():
    for case_id in ("C22", "C23", "C24"):
        assert decide(inp(case_id))["follow_up_question"] is None


# --- R11: null or blank location text --------------------------------------------------------


@pytest.mark.parametrize("loc_type", ["address", "coords", "landmark"])
@pytest.mark.parametrize("text", [None, "", "   ", "\t\n"])
def test_blank_location_text_is_unusable(loc_type, text):
    item = inp("C01")
    item["extracted"]["location"] = {"text": text, "type": loc_type, "confidence": 0.9}
    out = decide(item)
    assert (out["abstain_reason"], out["follow_up_question"], out["provisional_tier"]) == (
        "no_usable_location", Q_LOCATION, "P1")


def test_location_text_with_content_is_usable():
    item = inp("C02")
    item["extracted"]["location"]["text"] = " 4410 Bissonnet St "
    assert decide(item)["outcome"] == "prioritized"


# --- R13: the hazard question asks about the vulnerable person when that is what is weak ------


def test_weak_vulnerable_raise_asks_about_the_person():
    # firm rising_water (P2), weak child: only the child would make it P1.
    out = decide(signals("C05", hazards=[("rising_water", 0.95)], vulnerable=[("child", 0.3)]))
    assert (out["outcome"], out["priority_tier"], out["follow_up_question"]) == (
        "prioritized", "P2", Q_HAZARD_VULNERABLE)


def test_weak_vulnerable_raise_from_p3_abstains_with_person_question():
    out = decide(signals("C08", vulnerable=[("elderly", 0.3)]))  # evacuation P3 -> P2
    assert (out["abstain_reason"], out["follow_up_question"], out["provisional_tier"]) == (
        "low_confidence_hazard", Q_HAZARD_VULNERABLE, "P2")


def test_weak_hazard_raise_keeps_hazard_question():
    out = decide(signals("C08", hazards=[("fire", 0.3)], vulnerable=[("elderly", 0.3)]))
    assert out["follow_up_question"] == Q_HAZARD  # fire alone already gives the raise


# --- A9: unknown enum values -----------------------------------------------------------------


def _with(path: str, value) -> dict:
    item = inp("C01")
    node = item
    *parents, last = path.split(".")
    for key in parents:
        node = node[int(key)] if isinstance(node, list) else node[key]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return item


def _with_candidate_tier(tier):
    item = inp("C22")
    item["context"]["duplicate_candidates"][0]["priority_tier"] = tier
    return item


BAD_ENUMS = {
    "location_type": _with("extracted.location.type", "street"),
    "hazard_kind": _with("extracted.hazards.0.kind", "zombies"),
    "vulnerable_kind": _with("extracted.vulnerable", [{"kind": "infant", "confidence": 0.9}]),
    "need_kind": _with("extracted.needs", [{"kind": "shelter", "hours_without": 3}]),
    "request_kind": _with("extracted.request_kind", "boat"),
    "request_kind_empty": _with("extracted.request_kind", ""),
    "request_kind_case": _with("extracted.request_kind", "Rescue"),
    "candidate_tier": _with_candidate_tier("P0"),
}


@pytest.mark.parametrize("item", BAD_ENUMS.values(), ids=BAD_ENUMS.keys())
def test_unknown_enum_values_are_rejected(item):
    with pytest.raises(ValueError, match="unknown value"):
        decide_one(item)


def _cli(payload) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(ROOT / d) for d in ("engine", "shared"))}
    return subprocess.run(
        [sys.executable, "-m", "rescue_engine.fallback"],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60,
        check=False,
    )  # fmt: skip


@pytest.mark.parametrize(
    "payload",
    [BAD_ENUMS["hazard_kind"], [inp("C01"), BAD_ENUMS["request_kind"]]],
    ids=["single", "batch"],
)
def test_cli_rejects_unknown_enum_with_exit_1(payload):
    proc = _cli(payload)
    assert proc.returncode == 1 and proc.stdout == ""
    lines = proc.stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith("rescue_engine.fallback: ValueError: ")


def test_cli_rejects_unordered_thresholds_with_exit_1():
    item = inp("C01")
    item["context"]["thresholds"]["hazard_firm_min"] = 0.4
    proc = _cli(item)
    assert proc.returncode == 1 and proc.stdout == ""
    assert "hazard_weak_min" in proc.stderr


def test_null_request_kind_and_candidate_tier_still_mean_default():
    item = _with("extracted.request_kind", None)
    assert decide(item)["outcome"] == "prioritized"
    assert decide(_with_candidate_tier(None))["outcome"] == "duplicate"
