import json

import pytest

from rescue_evals.baselines import llm
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


def _rows_of(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_mock_rows_have_usage_and_latency_and_result_has_latency_block(tmp_path):
    assert _run(tmp_path, "--mock") == 0
    rows = _rows_of(tmp_path / "res" / "opus" / "mock-opus.jsonl")
    assert all(r["input_tokens"] == 120 and r["output_tokens"] == 80 for r in rows)
    assert all(isinstance(r["latency_ms"], float) and r["latency_ms"] >= 0 for r in rows)
    result = json.loads((tmp_path / "res" / "opus__B.json").read_text())
    assert set(result["baseline_latency"]) == {
        "total_ms_per_request",
        "median_ms_per_request",
        "seconds_per_1000_requests",
    }
    assert "running" not in result  # no prices set -> no cost, never hard-coded


def test_cost_only_with_prices_and_not_in_metrics(tmp_path):
    env = {"OPUS_USD_PER_MTOK_IN": "10", "OPUS_USD_PER_MTOK_OUT": "50"}
    assert _run(tmp_path, "--mock", env=env) == 0
    result = json.loads((tmp_path / "res" / "opus__B.json").read_text())
    # (120*10 + 80*50)/1e6 USD per call * 1000
    assert abs(result["running"]["cost_per_1000_usd"] - 5.2) < 1e-6
    assert not any("cost" in k for k in result["metrics"])
    assert _run(tmp_path, "--mock", system="slm", env=env) == 0  # slm needs SLM_ prices
    assert "running" not in json.loads((tmp_path / "res" / "slm__B.json").read_text())


def test_usage_parsed_for_both_api_shapes():
    from rescue_evals.baselines.llm import _usage

    assert _usage(
        {"usage": {"input_tokens": 5, "output_tokens": 7}}, "input_tokens", "output_tokens"
    ) == (5, 7)
    assert _usage(
        {"usage": {"prompt_tokens": 3, "completion_tokens": 4}},
        "prompt_tokens",
        "completion_tokens",
    ) == (3, 4)
    assert _usage({}, "input_tokens", "output_tokens") == (None, None)


def test_run_baseline_records_none_usage_and_latency(tmp_path):
    from rescue_evals.baselines.llm import run_baseline
    from rescue_evals.loaders import load_cases

    cases = load_cases(_sets(tmp_path, n=2) / "b.jsonl", split="dev")
    out = tmp_path / "o.jsonl"
    run_baseline("opus", cases, lambda t: "no json", "m", out)
    rows = _rows_of(out)
    assert all(r["input_tokens"] is None and r["output_tokens"] is None for r in rows)
    assert all(r["latency_ms"] >= 0 for r in rows)


def test_opus_prompt_has_rubric_and_schema_and_no_temperature(monkeypatch):
    prompt = llm.build_prompt()
    assert "<decision_rubric>" in prompt and "Step 3" in prompt and "R-ABS-LOC" in prompt
    assert "<json_schema>" in prompt and '"people_count"' in prompt
    sent = {}

    def fake_post(url, headers, body):
        sent.update(body)
        return {"content": [{"type": "text", "text": "{}"}], "usage": {}}

    monkeypatch.setattr(llm, "_post", fake_post)
    _, generate = llm.opus_client({"ANTHROPIC_API_KEY": "k"})
    generate("help")
    assert sent["system"] == prompt
    assert "temperature" not in sent  # current Opus models reject it


def test_rescore_reuses_stored_rows_without_a_model(tmp_path):
    assert _run(tmp_path, "--mock") == 0
    stored = tmp_path / "res" / "opus" / "mock-opus.jsonl"
    before = stored.read_text()
    first = json.loads((tmp_path / "res" / "opus__B.json").read_text())
    (tmp_path / "res" / "opus__B.json").unlink()
    # no --mock and no API key: only the stored file is read
    assert _run(tmp_path, "--rescore", str(stored)) == 0
    again = json.loads((tmp_path / "res" / "opus__B.json").read_text())
    assert stored.read_text() == before
    assert again["metrics"] == first["metrics"] and again["model_version"] == "mock-opus"
    assert "median_ms_per_request" in again["baseline_latency"]
    assert _run(tmp_path, "--rescore", str(tmp_path / "nope.jsonl")) == 2


def test_mock_replies_follow_case_order_not_text():
    from rescue_evals.loaders import EvalCase

    def case(cid, loc):
        ex = extract(TEXT).model_dump(mode="json")
        ex["location"]["text"] = loc
        return EvalCase.model_validate(
            {
                "id": cid,
                "raw_text": "SOS",
                "gold_extracted": ex,
                "gold_decision": {"outcome": "prioritized", "priority_tier": "P1"},
                "gold_duplicate_group": None,
                "set": "B",
                "split": "dev",
            }
        )

    _, generate = llm.mock_client("opus", [case("a", "1 Oak St"), case("b", "99 Elm St")])
    # was the same reply twice (keyed by text)
    assert "1 Oak St" in generate("SOS") and "99 Elm St" in generate("SOS")


def test_slm_constrained_decoding_is_opt_in(monkeypatch):
    sent = []

    def fake_post(url, headers, body):
        sent.append(body)
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

    monkeypatch.setattr(llm, "_post", fake_post)
    env = {"SLM_ENDPOINT": "http://x/v1/chat/completions", "SLM_MODEL_ID": "m"}
    llm.slm_client(env)[1]("help")
    llm.slm_client({**env, "SLM_JSON_SCHEMA": "1"})[1]("help")
    assert "response_format" not in sent[0]
    rf = sent[1]["response_format"]
    assert rf["type"] == "json_object" and "$schema" not in rf["schema"]
    label = rf["schema"]["properties"]["spans"]["items"]["properties"]["label"]
    assert "pattern" not in label and all(x.isupper() for x in label["enum"])
    assert "$ref" not in json.dumps(rf["schema"])
    assert sent[0]["messages"] == sent[1]["messages"]  # same prompt either way
