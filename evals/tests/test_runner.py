import json

from rescue_evals.loaders import load_cases
from rescue_evals.runner import SYSTEMS, GoldExtractionSystem, main, register_system, run_set
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


def test_sealed_set_refused(tmp_path):
    p = tmp_path / "sets"
    p.mkdir()
    (p / "e.jsonl").write_text("")
    args = ["--system", "gold-extraction", "--sets", "E", "--sets-dir", str(p)]
    assert main([*args, "--results-dir", str(tmp_path / "r")]) == 1
