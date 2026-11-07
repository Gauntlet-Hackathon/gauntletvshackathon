import json

import pytest

from rescue_evals.baselines.llm import main, parse_output
from rescue_evals.baselines.regex_baseline import extract

TEXT = "HELP trapped on the roof at 12 Oak St, water rising fast. 3 people incl. a baby"


def _sets(tmp_path, set_name="B", n=8):
    sets = tmp_path / "sets"
    sets.mkdir(exist_ok=True)
    rows = [
        {
            "id": f"c{i}",
            "raw_text": TEXT + " " * i,
            "gold_extracted": extract(TEXT).model_dump(mode="json"),
            "gold_decision": {"outcome": "prioritized", "priority_tier": "P1"},
            "gold_duplicate_group": None,
            "set": set_name,
            "split": "dev",
        }
        for i in range(n)
    ]
    (sets / f"{set_name.lower()}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return sets


def _run(tmp_path, *extra, system="opus", env=None):
    return main(
        [
            "--system", system, "--split", "dev", "--sets-dir", str(_sets(tmp_path)),
            "--sets", "B", "--results-dir", str(tmp_path / "res"), *extra,
        ],
        env=env or {},
    )  # fmt: skip


def test_mock_writes_results_and_counts_invalid(tmp_path, capsys):
    assert _run(tmp_path, "--mock") == 0
    path = tmp_path / "res" / "opus" / "mock-opus.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 8
    assert all(r["model_id"] == "mock-opus" and r["model_version"] and r["timestamp"] for r in rows)
    invalid = [r for r in rows if not r["valid"]]
    assert invalid and all(r["extracted"] is None and r["error"] for r in invalid)
    assert len(invalid) < len(rows)
    assert f"{len(invalid)} schema-invalid" in capsys.readouterr().out
    result = json.loads((tmp_path / "res" / "opus__B.json").read_text())
    assert result["n"] == 8
    assert result["stored"]["malformed"] == len(invalid)  # scored as failures, not dropped


def test_limit(tmp_path):
    assert _run(tmp_path, "--mock", "--limit", "2", system="slm") == 0
    lines = (tmp_path / "res" / "slm" / "mock-slm.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_sealed_holdout_refused(tmp_path, capsys):
    sets = _sets(tmp_path, "E")
    rc = main(
        [
            "--system", "opus", "--split", "dev", "--mock", "--sets", "E",
            "--sets-dir", str(sets), "--results-dir", str(tmp_path / "res"),
        ]
    )  # fmt: skip
    assert rc == 2
    assert "sealed holdout" in capsys.readouterr().err
    assert not (tmp_path / "res").exists()


def test_missing_key_is_a_clear_error(tmp_path, capsys):
    assert _run(tmp_path, env={}) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err and "Traceback" not in err
    assert _run(tmp_path, system="slm", env={}) == 2
    assert "SLM_ENDPOINT" in capsys.readouterr().err


def test_parse_output_rejects_bad():
    with pytest.raises(ValueError):
        parse_output("no json here")
    with pytest.raises(ValueError):
        parse_output('{"location": 1}')
