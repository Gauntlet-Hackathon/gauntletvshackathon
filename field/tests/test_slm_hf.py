import pytest

from rescue_field import slm_hf
from rescue_field.slm import validate_extracted
from rescue_field.slm_hf import HFExtractor

SHA = "a" * 40

# H11 sample payload from docs/DECISIONS.md
SAMPLE = {
    "location": {"text": "4512 Bellfort Ave", "type": "address", "confidence": 1.0},
    "people_count": {"value": 3, "confidence": 0.971},
    "vulnerable": [{"kind": "elderly", "confidence": 0.997}],
    "hazards": [
        {"kind": "rising_water", "confidence": 0.66},
        {"kind": "on_roof", "confidence": 0.961},
    ],
    "time_ref": {"text": "since last night", "is_stale_post": False, "confidence": 0.999},
    "is_request": {"value": True, "confidence": 0.998},
    "spans": [
        {"start": 40, "end": 57, "label": "LOCATION"},
        {"start": 95, "end": 102, "label": "COUNT"},
        {"start": 122, "end": 138, "label": "TIME"},
        {"start": 157, "end": 167, "label": "NEED_DURATION"},
    ],
    "needs": [
        {"kind": "food", "hours_without": None},
        {"kind": "water", "hours_without": 48.0},
    ],
    "request_kind": "supply",
}


class _Fake:
    def __init__(self, payload):
        self.payload = payload

    def extract(self, text):
        return self.payload


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("HF_MODEL_DIR", "HF_REPO", "HF_REVISION"):
        monkeypatch.delenv(k, raising=False)


def _no_download(monkeypatch):
    def boom(repo, revision):
        raise AssertionError("snapshot hook should not be called")

    monkeypatch.setattr(slm_hf, "_snapshot_download", boom)


def test_sample_payload_is_valid():
    validate_extracted(SAMPLE)


def test_fake_factory_output_and_version(monkeypatch):
    _no_download(monkeypatch)
    ex = HFExtractor("org/model", SHA, local_path="/x", extractor_factory=lambda f: _Fake(SAMPLE))
    r = ex.extract("x")
    assert r.model_version == f"hf:org/model@{SHA}"
    assert r.payload == SAMPLE


@pytest.mark.parametrize("rev", [None, "main", "abc123", "A" * 40])
def test_rejects_unpinned_revision(rev):
    with pytest.raises(ValueError):
        HFExtractor("org/model", rev, local_path="/x", extractor_factory=lambda f: _Fake({}))


def test_requires_repo():
    with pytest.raises(ValueError):
        HFExtractor(None, SHA, local_path="/x", extractor_factory=lambda f: _Fake({}))


def test_env_config(monkeypatch):
    monkeypatch.setenv("HF_REPO", "o/m")
    monkeypatch.setenv("HF_REVISION", SHA)
    ex = HFExtractor(local_path="/x", extractor_factory=lambda f: _Fake(SAMPLE))
    assert ex.model_version == f"hf:o/m@{SHA}"


def test_folder_resolution_order(monkeypatch):
    seen = []
    calls = []

    def hook(repo, revision):
        calls.append((repo, revision))
        return "/from-snapshot"

    monkeypatch.setattr(slm_hf, "_snapshot_download", hook)

    def factory(folder):
        seen.append(folder)
        return _Fake(SAMPLE)

    monkeypatch.setenv("HF_MODEL_DIR", "/from-env")
    HFExtractor("o/m", SHA, local_path="/from-arg", extractor_factory=factory)
    HFExtractor("o/m", SHA, extractor_factory=factory)
    monkeypatch.delenv("HF_MODEL_DIR")
    HFExtractor("o/m", SHA, extractor_factory=factory)
    assert seen == ["/from-arg", "/from-env", "/from-snapshot"]
    assert calls == [("o/m", SHA)]


def test_invalid_payload_raises(monkeypatch):
    _no_download(monkeypatch)
    bad = _Fake({"location": 1})
    ex = HFExtractor("o/m", SHA, local_path="/x", extractor_factory=lambda f: bad)
    with pytest.raises(ValueError):
        ex.extract("x")


def test_repo_without_org_fails_in_constructor(monkeypatch):
    _no_download(monkeypatch)
    called = []

    def factory(folder):
        called.append(folder)
        return _Fake(SAMPLE)

    with pytest.raises(ValueError, match="invalid Hugging Face repo"):
        HFExtractor("mymodel", SHA, local_path="/x", extractor_factory=factory)
    assert called == []


def test_select_extractor_falls_back_on_repo_without_org(monkeypatch):
    from rescue_field import api
    from rescue_field.slm import FixtureExtractor

    monkeypatch.setenv("HF_REPO", "mymodel")
    monkeypatch.setenv("HF_REVISION", SHA)
    _no_download(monkeypatch)
    ex, info = api.select_extractor_with_info({"HF_REPO": "mymodel", "HF_REVISION": SHA})
    assert isinstance(ex, FixtureExtractor)
    assert "invalid Hugging Face repo" in info["fallback_reason"]
    assert api.select_extractor({"HF_REPO": "mymodel", "HF_REVISION": SHA}).__class__ is (
        FixtureExtractor
    )


@pytest.mark.slow
def test_real_model():
    pytest.importorskip("torch")
    import os

    if not (os.environ.get("HF_REPO") and os.environ.get("HF_REVISION")):
        pytest.skip("HF_REPO/HF_REVISION not set")
    r = HFExtractor().extract("trapped on roof, 3 kids, water rising, near the Shell on 5th")
    assert r.model_version.startswith("hf:")
