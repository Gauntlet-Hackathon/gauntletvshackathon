import json

from rescue_evals.error_analysis import analyse, load_dev_cases, main
from rescue_evals.runner import GoldExtractionSystem
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


def row(cid, text):
    ex = json.loads(json.dumps(EX))
    # one household per case: the runner now builds duplicate context, and identical location +
    # count >= 3 would make every later case a duplicate of the first
    ex["location"]["text"] = f"{len(cid) * 7 + sum(map(ord, cid))} Oak St"
    return {
        "id": cid,
        "raw_text": text,
        "gold_extracted": ex,
        "gold_decision": {"outcome": "prioritized", "priority_tier": "P1"},
        "gold_duplicate_group": None,
        "set": "B",
        "split": "test",
    }


class Wrong:
    name = "wrong"
    model_version = "stub:wrong"

    def extract(self, raw_text):
        d = json.loads(json.dumps(EX))
        d["location"]["text"] = "elsewhere"
        return Extracted.model_validate(d)

    def decide(self, engine_input):
        return GoldExtractionSystem([]).decide(engine_input)


def make(tmp_path):
    rows = []
    for i in range(5):
        r = row(f"d{i}", f"text {i}")
        r["split"] = "dev"
        rows.append(r)
    t = row("t0", "SECRET test text")
    rows.append(t)  # test split: must not appear
    p = tmp_path / "sets"
    p.mkdir()
    (p / "b.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_dev_only_grouping_counts_and_cap(tmp_path):
    sets = make(tmp_path)
    cases = load_dev_cases(sets)
    assert [c.id for c in cases] == [f"d{i}" for i in range(5)]
    report = analyse(Wrong(), cases)
    assert "| `extraction.location_exact` | 5 | 100.0% |" in report
    assert "| `extraction.people_count_exact`" not in report
    section = report.split("## `extraction.location_exact`")[1].split("\n## ")[0]
    assert section.count("- `d") == 3
    assert "SECRET" not in report
    assert analyse(Wrong(), cases) == report


def test_gold_system_has_no_failures_and_cli_deterministic(tmp_path):
    sets = make(tmp_path)
    outs = []
    for n in (1, 2):
        out = tmp_path / f"r{n}" / "error_analysis.md"
        assert main(["--sets-dir", str(sets), "--out", str(out)]) == 0
        outs.append(out.read_text())
    assert outs[0] == outs[1]
    assert "| (none) | 0 | 0.0% |" in outs[0]
