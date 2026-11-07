import json

from rescue_evals.report import build_summary, load_results, main, publish, render_table


def _w(d, system, set_, metrics, n=10):
    run_at = "2026-01-01T00:00:00Z"
    doc = {"system": system, "set": set_, "run_at": run_at, "n": n, "metrics": metrics}
    (d / f"{system}__{set_}.json").write_text(json.dumps(doc))


def _fixture(d):
    _w(d, "regex-baseline", "A", {"decision.tier_accuracy": 0.5, "decision.p1_misses_per_1000": 90})
    _w(d, "opus-5-5", "A", {"decision.tier_accuracy": 0.9, "decision.p1_misses_per_1000": 10})
    _w(d, "ours", "A", {"decision.tier_accuracy": 0.8, "decision.p1_misses_per_1000": 0})
    _w(d, "ours", "B", {"decision.tier_accuracy": 0.6, "decision.p1_misses_per_1000": 0}, n=30)
    _w(d, "ours", "E", {"decision.tier_accuracy": 0.99, "decision.p1_misses_per_1000": 0})


def test_rows_columns_pooling_and_sealed_separate(tmp_path):
    _fixture(tmp_path)
    s = build_summary(load_results(tmp_path))
    rows = {r["metric"]: r for r in s["main"]["rows"]}
    assert list(s["columns"]) == ["regex", "opus", "generic_slm", "ours"]
    # weighted: (0.8*10 + 0.6*30) / 40 = 0.65; sealed E not pooled in
    assert abs(rows["decision.tier_accuracy"]["values"]["ours"] - 0.65) < 1e-9
    assert rows["decision.tier_accuracy"]["values"]["generic_slm"] is None
    assert s["sealed"]["rows"][0]["values"]["ours"] is not None
    assert s["sealed"]["rows"][0]["values"]["opus"] is None


def test_best_cell_and_ours_losing_row_kept(tmp_path):
    _fixture(tmp_path)
    s = build_summary(load_results(tmp_path))
    rows = {r["metric"]: r for r in s["main"]["rows"]}
    assert rows["decision.tier_accuracy"]["best"] == ["opus"]
    assert rows["decision.tier_accuracy"]["ours_loses"] is True
    assert rows["decision.p1_misses_per_1000"]["best"] == ["ours"]  # lower is better
    assert rows["decision.p1_misses_per_1000"]["ours_loses"] is False
    table = render_table(s)
    assert "`decision.tier_accuracy`" in table and "LOSES" in table
    assert "**0.900**" in table and "n/a" in table
    assert "Sealed holdout (set E)" in table


def test_publish_writes_files(tmp_path):
    _fixture(tmp_path)
    publish(tmp_path)
    assert json.loads((tmp_path / "summary.json").read_text())["main"]["rows"]
    assert (tmp_path / "TABLE.md").read_text().startswith("# Results")
    # re-running ignores its own summary.json
    assert main(["--results-dir", str(tmp_path)]) == 0


def test_empty_dir(tmp_path):
    publish(tmp_path)
    assert "_No results yet._" in (tmp_path / "TABLE.md").read_text()


def _rows(s):
    return {r["metric"]: r for r in s["main"]["rows"]}


def _lat(ms):
    return {"total_ms_per_request": ms, "seconds_per_1000_requests": ms}


def _wl(d, system, set_, metrics, latency, n=10):
    doc = {"system": system, "set": set_, "n": n, "metrics": metrics, "latency": latency}
    (d / f"{system}__{set_}.json").write_text(json.dumps(doc))


def _conditions(d):
    m = {"decision.tier_accuracy": 0.5}
    _wl(d, "opus", "A", m, _lat(1.0))
    _wl(d, "slm", "A", m, _lat(2.0))
    _wl(d, "regex-baseline", "A", m, _lat(3.0))
    _wl(d, "ours", "A", m, _lat(10.0), n=10)
    _wl(d, "ours", "B", m, _lat(40.0), n=30)
    (d / "consistency__ours.json").write_text(json.dumps({"pct_cases_changed": 0.0}))
    (d / "consistency__regex-baseline.json").write_text(json.dumps({"pct_cases_changed": 0}))


def test_aliases_fill_columns_and_running_rows(tmp_path):
    _conditions(tmp_path)
    s = build_summary(load_results(tmp_path), tmp_path)
    rows = _rows(s)
    assert rows["decision.tier_accuracy"]["values"]["opus"] == 0.5
    assert rows["decision.tier_accuracy"]["values"]["generic_slm"] == 0.5
    ms = rows["running.mean_ms_per_request"]
    assert ms["lower_is_better"] and ms["best"] == ["regex"]
    assert abs(ms["values"]["ours"] - 32.5) < 1e-9
    assert ms["values"]["opus"] is None and ms["values"]["generic_slm"] is None
    assert rows["running.seconds_per_1000_requests"]["values"]["opus"] is None
    assert rows["running.seconds_per_1000_requests"]["lower_is_better"]
    c = rows["consistency.pct_cases_changed"]
    assert c["lower_is_better"] and c["values"]["opus"] is None and c["values"]["ours"] == 0.0


def test_offline_row_numbers_and_yes_no(tmp_path):
    _conditions(tmp_path)
    s = build_summary(load_results(tmp_path), tmp_path)
    off = _rows(s)["running.works_offline"]
    assert off["values"] == {"regex": 1, "opus": 0, "generic_slm": 1, "ours": 1}
    assert off["best"] == []
    table = render_table(s)
    line = next(ln for ln in table.splitlines() if "running.works_offline" in ln)
    assert "| yes | no | yes | yes |" in line and "**" not in line


def test_summary_values_numeric_and_metrics_unchanged(tmp_path):
    _conditions(tmp_path)
    s = build_summary(load_results(tmp_path), tmp_path)
    for r in s["main"]["rows"]:
        for v in r["values"].values():
            assert v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))
    for res in s["results"]:
        assert set(res["metrics"]) == {"decision.tier_accuracy"}


def test_missing_consistency_file_is_null(tmp_path):
    _wl(tmp_path, "ours", "A", {"decision.tier_accuracy": 0.5}, _lat(5.0))
    s = build_summary(load_results(tmp_path), tmp_path)
    assert _rows(s)["consistency.pct_cases_changed"]["values"]["ours"] is None
