import pytest

from rescue_evals.loaders import EvalCase, SealedHoldoutError
from rescue_evals.runner import GoldExtractionSystem, refuse_sealed, run_set
from rescue_evals.stats import detection_floor, paired_comparison, wilson_interval


def test_wilson_known_values():
    lo, hi = wilson_interval(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3)
    assert hi == pytest.approx(0.9433, abs=1e-3)
    assert wilson_interval(0, 10)[0] == 0.0
    assert wilson_interval(10, 10)[1] == pytest.approx(1.0)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_interval_narrows_with_n():
    a = wilson_interval(50, 100)
    b = wilson_interval(500, 1000)
    assert (b[1] - b[0]) < (a[1] - a[0])


def test_detection_floor():
    assert detection_floor(100) == pytest.approx(0.198, abs=1e-3)
    assert detection_floor(400) < detection_floor(100)
    assert detection_floor(0) is None
    assert detection_floor(1) == 1.0


def test_paired_counts():
    a = {"1": True, "2": False, "3": False, "4": True, "x": True}
    b = {"1": True, "2": True, "3": False, "4": False, "y": True}
    r = paired_comparison(a, b)
    assert (r["improved"], r["worsened"], r["unchanged"], r["n"]) == (1, 1, 2, 4)
    assert r["unpaired"] == 2
    assert r["sign_test_p"] == 1.0
    big = paired_comparison({str(i): False for i in range(10)}, {str(i): True for i in range(10)})
    assert big["improved"] == 10 and big["sign_test_p"] < 0.01


def test_holdout_guard():
    case = EvalCase.model_construct(id="e1", set="E", raw_text="x")
    with pytest.raises(SealedHoldoutError):
        refuse_sealed([case], "B")
    with pytest.raises(SealedHoldoutError):
        refuse_sealed([], "e")
    with pytest.raises(SealedHoldoutError):
        run_set(GoldExtractionSystem([]), [case], "B")
    refuse_sealed([case], "E", open_sealed=True)


def test_result_records_versions_and_intervals():
    r = run_set(GoldExtractionSystem([]), [], "B", judge_id="j", judge_version="1")
    assert r["judge_id"] == "j" and r["judge_version"] == "1"
    # an empty set has no denominators: no metric and no interval (review H-03)
    assert r["model_version"] and r["metrics"] == {} and r["intervals"] == {}
    case = EvalCase.model_validate(
        {
            "id": "b1",
            "raw_text": "x",
            "gold_extracted": None,
            "gold_decision": {"outcome": "not_a_request"},
            "gold_duplicate_group": None,
            "set": "B",
            "split": "test",
        }
    )
    r = run_set(GoldExtractionSystem([case]), [case], "B")
    assert r["intervals"]["decision.outcome_accuracy"]["n"] == 1
