import json

from rescue_evals.baselines.regex_baseline import extract
from rescue_evals.baselines.stored import StoredSystem, load_stored, run_stored_set
from rescue_evals.loaders import load_cases
from rescue_evals.runner import SYSTEMS, main

TEXT = "HELP trapped on the roof at 12 Oak St, water rising fast. 3 people incl. a baby"


def _case(cid, text=TEXT):
    return {
        "id": cid,
        # a day apart: the same household text within the 12 h window would be a duplicate
        "received_at": f"2026-11-{ord(cid) - ord('a') + 1:02d}T09:00:00Z",
        "raw_text": text,
        "gold_extracted": extract(TEXT).model_dump(mode="json"),
        "gold_decision": {"outcome": "prioritized", "priority_tier": "P1"},
        "gold_duplicate_group": None,
        "set": "B",
        "split": "test",
    }


def _setup(tmp_path, stored_lines):
    sets = tmp_path / "sets"
    sets.mkdir()
    rows = [_case("a"), _case("b", TEXT + " "), _case("c", TEXT + "  "), _case("d", TEXT + "   ")]
    (sets / "b.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    stored = tmp_path / "opus.jsonl"
    stored.write_text("\n".join(stored_lines) + "\n")
    return sets, stored


def _good(cid):
    return json.dumps({"id": cid, "extracted": extract(TEXT).model_dump(mode="json")})


def test_scores_stored_rows_and_reports_missing_and_malformed(tmp_path):
    lines = [
        _good("a"),
        "{not json",
        json.dumps({"id": "c", "extracted": {"location": 1}}),
        json.dumps([1]),
        json.dumps({"extracted": {}}),
        _good("zzz"),
    ]  # "d" missing, "b" absent
    sets, stored = _setup(tmp_path, lines)
    cases = load_cases(sets / "b.jsonl")
    res = run_stored_set(cases, "B", stored)
    rep = res["stored"]
    assert rep["rows_ok"] == 2 and rep["malformed"] == 4
    assert rep["missing"] == 2 and rep["missing_ids"] == ["b", "d"]
    assert rep["unused"] == ["zzz"]
    assert "c" in rep["malformed_ids"]
    assert set(res["errors"]) == {"b", "c", "d"}
    assert res["n"] == 4 and res["model_version"] == "stored:opus.jsonl"
    assert "a" not in res["errors"]


def test_duplicate_id_is_malformed(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(_good("a") + "\n" + _good("a") + "\n")
    good, bad = load_stored(f)
    assert "a" in good and bad == {"a": "duplicate id"}


def test_runs_through_runner_cli(tmp_path, monkeypatch):
    sets, stored = _setup(tmp_path, [_good(c) for c in "abcd"])
    monkeypatch.setenv("STORED_OUTPUTS", str(stored))
    assert "stored" in SYSTEMS
    rc = main(["--system", "stored", "--sets", "B", "--sets-dir", str(sets),
               "--results-dir", str(tmp_path / "r")])  # fmt: skip
    assert rc == 0
    out = json.loads((tmp_path / "r" / "stored__B.json").read_text())
    assert out["errors"] == {} and out["metrics"]["decision.outcome_accuracy"] == 1.0
    cases = load_cases(sets / "b.jsonl")
    assert StoredSystem(cases, stored).report()["missing"] == 0
