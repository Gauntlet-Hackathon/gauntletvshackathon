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
