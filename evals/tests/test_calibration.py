import copy
import json
import random
import struct
import zlib

from rescue_evals import calibration, consistency
from rescue_evals.loaders import load_cases
from rescue_evals.runner import SYSTEMS, GoldExtractionSystem

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


def make_rows(n):
    return [
        {
            "id": f"c{i}",
            "raw_text": f"text {i}",
            "gold_extracted": EX,
            "gold_decision": {"outcome": "prioritized", "priority_tier": "P1"},
            "gold_duplicate_group": None,
            "set": "B",
            "split": "test",
        }
        for i in range(n)
    ]


def load(tmp_path, n=20):
    p = tmp_path / "b.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in make_rows(n)) + "\n")
    return load_cases(p)


class Overconfident:
    """Says 0.9 on everything but gets people_count wrong half the time."""

    name = "overconfident"
    model_version = "stub"

    def __init__(self, cases):
        self.cases = cases

    def extract(self, raw_text):
        d = copy.deepcopy(EX)
        if int(raw_text.split()[1]) % 2:
            d["people_count"]["value"] = 99
        return d

    def decide(self, engine_input):
        return GoldExtractionSystem([]).decide(engine_input)


def test_ece_per_field_and_png(tmp_path):
    cases = load(tmp_path)
    s = calibration.summarize(calibration.collect(Overconfident(cases), cases))
    assert set(s) == set(calibration.FIELDS)
    assert s["people_count"]["ece"] == 0.4  # confidence 0.9, accuracy 0.5
    assert abs(s["location"]["ece"] - 0.1) < 1e-9  # confidence 0.9, accuracy 1.0
    assert s["people_count"]["n"] == 20

    paths = calibration.write_outputs(s, tmp_path / "out")
    assert json.loads((tmp_path / "out" / "calibration.json").read_text())["hazards"]["n"] == 20
    for f in calibration.FIELDS:
        data = (tmp_path / "out" / f"reliability_{f}.png").read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", data[16:24])
        assert (w, h) == (400, 400)
        idat = data.index(b"IDAT")
        n = struct.unpack(">I", data[idat - 4 : idat])[0]
        assert len(zlib.decompress(data[idat + 4 : idat + 4 + n])) == 400 * (1 + 400 * 3)
    assert len(paths) == 1 + len(calibration.FIELDS)


def test_perfect_calibration_ece_zero(tmp_path):
    cases = load(tmp_path, 10)
    s = calibration.summarize(calibration.collect(GoldExtractionSystem(cases), cases))
    assert s["people_count"]["ece"] == 0.0 or abs(s["people_count"]["ece"] - 0.1) < 1e-9


def test_empty_field_has_no_ece():
    s = calibration.summarize({"hazards": ([], [])})
    assert s["hazards"]["ece"] is None and s["hazards"]["bins"] == []


class Flaky:
    name = "flaky"
    model_version = "stub"

    def __init__(self, cases):
        self.rng = random.Random(1)

    def extract(self, raw_text):
        return copy.deepcopy(EX)

    def decide(self, engine_input):
        return {"outcome": self.rng.choice(["a", "b"])}


def test_consistency_deterministic_vs_flaky(tmp_path):
    cases = load(tmp_path, 5)
    det = consistency.run_consistency(GoldExtractionSystem(cases), cases, runs=10)
    assert det["pct_cases_changed"] == 0.0 and det["runs"] == 10
    flaky = consistency.run_consistency(Flaky(cases), cases, runs=10)
    assert flaky["pct_cases_changed"] > 0
    assert flaky["cases_with_any_change"] == len(flaky["changed_ids"])


def test_consistency_cli(tmp_path):
    sets = tmp_path / "sets"
    sets.mkdir()
    (sets / "b.jsonl").write_text("\n".join(json.dumps(r) for r in make_rows(3)) + "\n")
    rc = consistency.main(
        [
            "--system",
            "gold-extraction",
            "--sets",
            "B",
            "--sets-dir",
            str(sets),
            "--results-dir",
            str(tmp_path / "r"),
        ]
    )
    assert rc == 0
    res = json.loads((tmp_path / "r" / "consistency__gold-extraction.json").read_text())
    assert res["pct_cases_changed"] == 0.0 and res["n"] == 3
    rc = calibration.main(
        [
            "--system",
            "gold-extraction",
            "--sets",
            "B",
            "--sets-dir",
            str(sets),
            "--out-dir",
            str(tmp_path / "cal"),
        ]
    )
    assert rc == 0 and (tmp_path / "cal" / "reliability_location.png").exists()
    assert "gold-extraction" in SYSTEMS
