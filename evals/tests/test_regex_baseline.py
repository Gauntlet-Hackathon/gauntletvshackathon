import json

from rescue_evals.baselines.regex_baseline import RegexBaselineSystem, extract
from rescue_evals.loaders import load_cases
from rescue_evals.runner import SYSTEMS, main, run_set
from rescue_shared.models import EngineOutput, Extracted

TEXT = "HELP trapped on the roof at 12 Oak St, water rising fast. 3 people incl. a baby"


def test_extracts_fields():
    ex = extract(TEXT)
    assert isinstance(ex, Extracted)
    assert ex.location.text == "12 Oak St" and ex.location.type == "address"
    assert ex.people_count.value == 3
    assert {h.kind for h in ex.hazards} >= {"rising_water", "on_roof", "trapped"}
    assert {v.kind for v in ex.vulnerable} == {"child"}
    assert ex.is_request.value is True


def test_not_a_request_and_no_location():
    assert extract("Thanks everyone, we are safe now").is_request.value is False
    ex = extract("please help, water is rising")
    assert ex.location.type == "none" and ex.location.text is None


def test_deterministic_and_decides_with_fallback_engine():
    s = RegexBaselineSystem()
    assert s.extract(TEXT) == s.extract(TEXT)
    out = s.decide({"extracted": s.extract(TEXT).model_dump(mode="json"), "context": _ctx()})
    EngineOutput.model_validate(out)
    assert out["outcome"] == "prioritized" and out["priority_tier"] == "P1"


def _ctx():
    from rescue_evals.runner import default_context

    return default_context()


def test_runs_through_runner(tmp_path):
    row = {
        "id": "a",
        "raw_text": TEXT,
        "gold_extracted": extract(TEXT).model_dump(mode="json"),
        "gold_decision": {"outcome": "prioritized", "priority_tier": "P1"},
        "gold_duplicate_group": None,
        "set": "B",
        "split": "test",
    }
    sets = tmp_path / "sets"
    sets.mkdir()
    (sets / "b.jsonl").write_text(json.dumps(row) + "\n")
    assert "regex-baseline" in SYSTEMS
    cases = load_cases(sets / "b.jsonl")
    res = run_set(SYSTEMS["regex-baseline"](cases), cases, "B")
    assert res["errors"] == {}
    assert res["metrics"]["decision.outcome_accuracy"] == 1.0
    assert main(
        ["--system", "regex-baseline", "--sets", "B", "--sets-dir", str(sets),
         "--results-dir", str(tmp_path / "r")]
    ) == 0  # fmt: skip
    assert (tmp_path / "r" / "regex-baseline__B.json").exists()
