import json

import pytest

from rescue_evals.loaders import load_cases
from rescue_evals.metrics import expected_calibration_error
from rescue_evals.runner import (
    SYSTEMS,
    GoldExtractionSystem,
    follow_up_field,
    main,
    register_system,
    run_set,
)
from rescue_shared.models import Extracted

EX = {
    "location": {"text": "12 Oak St", "type": "address", "confidence": 0.9},
    "people_count": {"value": 3, "confidence": 0.9},
    "vulnerable": [{"kind": "child", "confidence": 0.8}],
    "hazards": [{"kind": "rising_water", "confidence": 0.9}],
    "time_ref": {"text": None, "is_stale_post": False, "confidence": 0.9},
    "is_request": {"value": True, "confidence": 0.95},
    "spans": [],
    "needs": [],
    "request_kind": "rescue",
}


def row(cid, text, tier="P1", ex=EX, outcome="prioritized"):
    d = {"outcome": outcome}
    if tier:
        d["priority_tier"] = tier
    return {
        "id": cid,
        "raw_text": text,
        "gold_extracted": ex,
        "gold_decision": d,
        "gold_duplicate_group": None,
        "set": "B",
        "split": "test",
    }


def write(tmp_path, rows):
    p = tmp_path / "sets"
    p.mkdir(exist_ok=True)
    (p / "b.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


class Wrong:
    """Extracts a wrong location and drops hazards, so P1 cases are missed."""

    name = "wrong"
    model_version = "stub:wrong"

    def extract(self, raw_text):
        d = json.loads(json.dumps(EX))
        d["location"]["text"] = "elsewhere"
        d["hazards"] = []
        d["vulnerable"] = []
        return Extracted.model_validate(d)

    def decide(self, engine_input):
        return GoldExtractionSystem([]).decide(engine_input)


def test_two_points_and_failures(tmp_path):
    sets = write(tmp_path, [row("a", "t1"), row("b", "t2", tier="P3", ex=None)])
    cases = load_cases(sets / "b.jsonl")
    res = run_set(Wrong(), cases, "B")
    assert res["n"] == 2
    assert res["metrics"]["extraction.location_exact"] == 0.0
    assert res["failures"]["extraction.location_exact"] == ["a"]  # b has no gold extraction
    assert res["failures"]["decision.p1_misses_per_1000"] == ["a"]
    assert res["metrics"]["decision.p1_misses_per_1000"] == 500.0
    assert set(res["failures"]) == set(res["metrics"])
    assert res["model_version"] == "stub:wrong"
    assert res["rules_version"].startswith("rules@")
    lat = res["latency"]
    assert lat["total_ms_per_request"] >= 0
    assert lat["seconds_per_1000_requests"] >= 0


def test_gold_system_scores_perfect(tmp_path):
    sets = write(tmp_path, [row("a", "t1")])
    cases = load_cases(sets / "b.jsonl")
    res = run_set(GoldExtractionSystem(cases), cases, "B")
    assert res["metrics"]["extraction.location_exact"] == 1.0
    assert res["metrics"]["decision.outcome_accuracy"] == 1.0
    assert res["failures"]["decision.outcome_accuracy"] == []
    assert res["errors"] == {}


def test_cli_writes_files(tmp_path):
    sets = write(tmp_path, [row("a", "t1")])
    out = tmp_path / "results"
    args = ["--system", "gold-extraction", "--sets", "B", "--sets-dir", str(sets)]
    assert main([*args, "--results-dir", str(out)]) == 0
    doc = json.loads((out / "gold-extraction__B.json").read_text())
    assert {"system", "set", "run_at", "n", "metrics", "model_version", "rules_version"} <= set(doc)
    assert doc["run_at"].endswith("Z")


def test_cli_unknown_system_and_registry(tmp_path):
    assert main(["--system", "nope", "--sets-dir", str(tmp_path)]) == 2
    register_system("wrong", lambda cases: Wrong())
    assert "wrong" in SYSTEMS
    sets = write(tmp_path, [row("a", "t1")])
    args = ["--system", "wrong", "--sets", "B", "--sets-dir", str(sets)]
    assert main([*args, "--results-dir", str(tmp_path / "r")]) == 0
    SYSTEMS.pop("wrong")


def test_follow_up_field_derived_from_abstain_reason():
    def out(reason, outcome="abstain"):
        return {"outcome": outcome, "abstain_reason": reason}

    assert follow_up_field(out("no_usable_location")) == "location"
    assert follow_up_field(out("conflicting_counts")) == "people_count"
    assert follow_up_field(out("low_confidence_request")) == "is_request"
    assert follow_up_field(out("possible_hoax")) == "is_request"
    assert follow_up_field(out("low_confidence_hazard")) == "hazard"
    assert follow_up_field(out(None, "prioritized")) is None
    assert follow_up_field({}) is None
    # a stray follow_up_field key is not read
    assert follow_up_field({"outcome": "abstain", "follow_up_field": "location"}) is None


def abstain_row(cid, text, gold_field):
    ex = json.loads(json.dumps(EX))
    ex["location"] = {"text": None, "type": "none", "confidence": 0.9}
    r = row(cid, text, tier=None, ex=ex, outcome="abstain")
    r["gold_decision"]["abstain_reason"] = "no_usable_location"
    r["gold_decision"]["follow_up_field"] = gold_field
    return r


def test_follow_up_field_rate_scores_engine_abstains(tmp_path):
    rows = [abstain_row("a", "t1", "location"), abstain_row("b", "t2", "people_count")]
    cases = load_cases(write(tmp_path, rows) / "b.jsonl")
    res = run_set(GoldExtractionSystem(cases), cases, "B")
    assert res["metrics"]["decision.abstain_recall"] == 1.0
    assert res["metrics"]["decision.follow_up_field_rate"] == 0.5  # engine asks for location
    assert res["failures"]["decision.follow_up_field_rate"] == ["b"]


def test_result_carries_calibration_ece(tmp_path):
    # gold extraction: every prediction is right, so ECE = mean(1 - confidence)
    sets = write(tmp_path, [row("a", "t1")])
    cases = load_cases(sets / "b.jsonl")
    res = run_set(GoldExtractionSystem(cases), cases, "B")
    confs = [0.9, 0.9, 0.95, 0.8, 0.9]  # location, people, is_request, vulnerable, hazard
    expected = expected_calibration_error(confs, [True] * 5)
    assert res["metrics"]["calibration.ece"] == pytest.approx(expected)
    assert res["failures"]["calibration.ece"] == []
    # a wrong location at 0.9 is an overconfident failure and raises the error
    wrong = run_set(Wrong(), cases, "B")
    assert wrong["metrics"]["calibration.ece"] > res["metrics"]["calibration.ece"]
    assert wrong["failures"]["calibration.ece"] == ["a"]


def test_calibration_ece_omitted_without_extraction_gold(tmp_path):
    sets = write(tmp_path, [row("a", "t1", ex=None)])
    cases = load_cases(sets / "b.jsonl")
    res = run_set(Wrong(), cases, "B")
    assert "calibration.ece" not in res["metrics"]


def test_summary_json_feeds_the_promotion_gate(tmp_path):
    from rescue_evals.report import LOWER_IS_BETTER, publish

    sets = write(tmp_path, [row("a", "t1")])
    out = tmp_path / "results"
    args = ["--system", "gold-extraction", "--sets", "B", "--sets-dir", str(sets)]
    assert main([*args, "--results-dir", str(out)]) == 0
    doc = json.loads((out / "gold-extraction__B.json").read_text())
    assert isinstance(doc["metrics"]["calibration.ece"], float)
    publish(out)
    # models.ex reads summary["results"][*]["metrics"]["calibration.ece"] (a number)
    [result] = json.loads((out / "summary.json").read_text())["results"]
    assert result["metrics"]["calibration.ece"] == doc["metrics"]["calibration.ece"]
    assert result["model_version"] == "stub:gold-extraction" and result["set"] == "B"
    assert "calibration.ece" in LOWER_IS_BETTER  # TABLE.md bolds the lowest ECE


def test_sealed_set_refused(tmp_path):
    p = tmp_path / "sets"
    p.mkdir()
    (p / "e.jsonl").write_text("")
    args = ["--system", "gold-extraction", "--sets", "E", "--sets-dir", str(p)]
    assert main([*args, "--results-dir", str(tmp_path / "r")]) == 1
