import json

import pytest

from rescue_field.slm import FixtureExtractor
from rescue_field.slm_hf import HFExtractor

SHA = "a" * 40


def _payload():
    return FixtureExtractor().extract("trapped on roof, 3 kids, water rising").payload


def test_maps_output_and_version():
    out = "Here: " + json.dumps(_payload())
    ex = HFExtractor("org/model", SHA, generate=lambda t: out)
    r = ex.extract("x")
    assert r.model_version == f"hf:org/model@{SHA}"
    assert r.payload == _payload()


@pytest.mark.parametrize("rev", [None, "main", "abc123", "A" * 40])
def test_rejects_unpinned_revision(rev, monkeypatch):
    monkeypatch.delenv("HF_REVISION", raising=False)
    with pytest.raises(ValueError):
        HFExtractor("org/model", rev, generate=lambda t: "{}")


def test_env_config(monkeypatch):
    monkeypatch.setenv("HF_REPO", "o/m")
    monkeypatch.setenv("HF_REVISION", SHA)
    assert HFExtractor(generate=lambda t: "{}").model_version == f"hf:o/m@{SHA}"


@pytest.mark.parametrize("bad", ["no json", '{"location": 1}', "[1]"])
def test_bad_output_raises(bad):
    with pytest.raises(ValueError):
        HFExtractor("o/m", SHA, generate=lambda t: bad).extract("x")


@pytest.mark.slow
def test_real_model(monkeypatch):
    import os

    if not (os.environ.get("HF_REPO") and os.environ.get("HF_REVISION")):
        pytest.skip("HF_REPO/HF_REVISION not set")
    r = HFExtractor().extract("trapped on roof, 3 kids, water rising, near the Shell on 5th")
    assert r.model_version.startswith("hf:")
