import json
from types import SimpleNamespace

import pytest

from rescue_evals.baselines.regex_baseline import extract
from rescue_evals.loaders import load_cases
from rescue_evals.runner import SYSTEMS, run_set
from rescue_evals.systems.ours import OursSystem

TEXT = "HELP trapped on the roof at 12 Oak St, water rising fast. 3 people incl. a baby"


def _cases(tmp_path):
    row = {
        "id": "a",
        "raw_text": TEXT,
        "gold_extracted": extract(TEXT).model_dump(mode="json"),
        "gold_decision": {"outcome": "prioritized", "priority_tier": "P1"},
        "gold_duplicate_group": None,
        "set": "B",
        "split": "test",
    }
    f = tmp_path / "b.jsonl"
    f.write_text(json.dumps(row) + "\n")
    return load_cases(f, split="test")


class FakeExtractor:
    model_version = "hf:fake/model@" + "a" * 40

    def extract(self, raw_text):
        return SimpleNamespace(
            payload=extract(raw_text).model_dump(mode="json"), model_version=self.model_version
        )


def test_registered():
    assert "ours" in SYSTEMS


def test_mocked_extractor_with_real_engine_cli_records_versions(tmp_path):
    cases = _cases(tmp_path)
    result = run_set(OursSystem(FakeExtractor()), cases, "B")
    assert result["system"] == "ours"
    assert result["model_version"] == FakeExtractor.model_version
    assert result["rules_version"]
    assert result["errors"] == {}
    assert result["metrics"]


def test_extractor_failure_is_scored_not_crash(tmp_path):
    class Bad(FakeExtractor):
        def extract(self, raw_text):
            raise ValueError("model output invalid")

    result = run_set(OursSystem(Bad()), _cases(tmp_path), "B")
    assert "a" in result["errors"] and result["errors"]["a"].startswith("extract:")


@pytest.mark.slow
def test_real_model(tmp_path):
    from rescue_field.slm_hf import HFExtractor

    result = run_set(OursSystem(HFExtractor()), _cases(tmp_path), "B")
    assert result["model_version"].startswith("hf:")
