"""Startup extractor selection (F25b): HFExtractor when configured, FixtureExtractor otherwise."""

import logging
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field import api, slm_hf
from rescue_field import sync as sync_module
from rescue_field.slm import Extraction, FixtureExtractor
from tests.test_api_intake import C01, fake_engine

SHA = "a" * 40
HF_VERSION = f"hf:org/model@{SHA}"


class FakeHF:
    model_version = HF_VERSION

    def __init__(self, *a, **k):
        pass

    def extract(self, raw_text):
        return Extraction(FixtureExtractor().extract(raw_text).payload, HF_VERSION)


class Boom:
    exc: Exception = RuntimeError("no weights")

    def __init__(self, *a, **k):
        raise self.exc


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FIELD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CLOUD_URL", raising=False)
    monkeypatch.delenv("HF_REPO", raising=False)
    monkeypatch.delenv("HF_REVISION", raising=False)
    return monkeypatch


def hf_env(monkeypatch):
    monkeypatch.setenv("HF_REPO", "org/model")
    monkeypatch.setenv("HF_REVISION", SHA)


def test_env_set_uses_hf_everywhere(env, monkeypatch):
    hf_env(monkeypatch)
    monkeypatch.setattr(slm_hf, "HFExtractor", FakeHF)
    monkeypatch.setenv("CLOUD_URL", "http://cloud:4000")
    seen = {}

    class W:
        def start(self):
            pass

        def stop(self):
            pass

    def fake_worker(store, url, *, model_version, **kw):
        seen["mv"] = model_version
        return W()

    monkeypatch.setattr(sync_module, "worker_from_env", fake_worker)
    app = api.create_app(decide_batch=fake_engine())
    with TestClient(app) as client:
        assert isinstance(app.state.extractor, FakeHF)
        assert seen["mv"] == HF_VERSION
        r = client.post("/requests", json={"raw_text": C01})
        assert r.status_code == 200
        with app.state.intake_lock:
            events = app.state.store.all_events()
        versions = {e["model_version"] for e in events if "model_version" in e}
        assert HF_VERSION in versions
        assert HF_VERSION in client.get("/board").text


def test_env_unset_uses_fixture(env):
    app = api.create_app(decide_batch=fake_engine())
    with TestClient(app) as client:
        assert isinstance(app.state.extractor, FixtureExtractor)
        assert "Model: " in client.get("/board").text


def test_partial_env_uses_fixture(env, monkeypatch):
    monkeypatch.setenv("HF_REPO", "org/model")
    app = api.create_app(decide_batch=fake_engine())
    with TestClient(app):
        assert isinstance(app.state.extractor, FixtureExtractor)


@pytest.mark.parametrize("exc", [RuntimeError("no weights"), ImportError("No module named torch")])
def test_constructor_failure_falls_back_and_logs(env, monkeypatch, caplog, exc):
    hf_env(monkeypatch)
    Boom.exc = exc
    monkeypatch.setattr(slm_hf, "HFExtractor", Boom)
    app = api.create_app(decide_batch=fake_engine())
    with caplog.at_level(logging.INFO, logger="rescue_field.api"):
        with TestClient(app) as client:
            assert isinstance(app.state.extractor, FixtureExtractor)
            assert client.post("/requests", json={"raw_text": C01}).status_code == 200
    lines = [r.getMessage() for r in caplog.records if "extractor:" in r.getMessage()]
    assert len(lines) == 1 and "FixtureExtractor" in lines[0] and type(exc).__name__ in lines[0]


def test_passed_extractor_is_not_replaced(env, monkeypatch):
    hf_env(monkeypatch)
    monkeypatch.setattr(slm_hf, "HFExtractor", FakeHF)
    mine = FixtureExtractor()
    app = api.create_app(extractor=mine, decide_batch=fake_engine())
    with TestClient(app):
        assert app.state.extractor is mine


def test_import_with_hf_env_touches_no_files(tmp_path):
    code = (
        "import os, sys; from rescue_field import api; "
        "assert api.app.state.store is None; assert not os.path.exists('data'); "
        "assert 'torch' not in sys.modules"
    )
    root = Path(__file__).resolve().parents[2]
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": f"{root / 'field'}:{root / 'shared'}:{root / 'engine'}",
        "HF_REPO": "org/model",
        "HF_REVISION": SHA,
    }
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert list(tmp_path.iterdir()) == []


# ---- F37: the fallback is loud --------------------------------------------------------------

BANNER = "Real model not loaded, using keyword fallback"


def test_fallback_is_visible_in_status_and_board(env, monkeypatch, caplog):
    hf_env(monkeypatch)
    Boom.exc = RuntimeError("no weights")
    monkeypatch.setattr(slm_hf, "HFExtractor", Boom)
    app = api.create_app(decide_batch=fake_engine())
    with caplog.at_level(logging.ERROR, logger="rescue_field.api"):
        with TestClient(app) as client:
            ex = client.get("/sync/status").json()["extractor"]
            html = client.get("/board").text
    assert ex["extractor"] == "fixture"
    assert ex["expected_model"] == HF_VERSION
    assert ex["fallback_reason"] == "RuntimeError: no weights"
    assert ex["calibrated"] is None
    assert f"{BANNER}: RuntimeError: no weights" in html and 'role="alert"' in html
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_no_env_no_banner(env):
    app = api.create_app(decide_batch=fake_engine())
    with TestClient(app) as client:
        ex = client.get("/sync/status").json()["extractor"]
        assert BANNER not in client.get("/board").text
    assert ex["extractor"] == "fixture" and ex["expected_model"] is None
    assert ex["fallback_reason"] is None


def test_load_success_no_banner(env, monkeypatch):
    hf_env(monkeypatch)
    monkeypatch.setattr(slm_hf, "HFExtractor", FakeHF)
    app = api.create_app(decide_batch=fake_engine())
    with TestClient(app) as client:
        ex = client.get("/sync/status").json()["extractor"]
        assert BANNER not in client.get("/board").text
    assert ex == {
        "extractor": "hf",
        "model_version": HF_VERSION,
        "expected_model": HF_VERSION,
        "fallback_reason": None,
        "calibrated": None,
    }


def test_calibrated_flag_reported(env, monkeypatch):
    hf_env(monkeypatch)

    class Uncal(FakeHF):
        calibrated = False

    monkeypatch.setattr(slm_hf, "HFExtractor", Uncal)
    app = api.create_app(decide_batch=fake_engine())
    with TestClient(app) as client:
        assert client.get("/sync/status").json()["extractor"]["calibrated"] is False


def test_hfextractor_exposes_calibrated(tmp_path):
    class Inner:
        calibrated = False

        def extract(self, t):
            return {}

    hf = slm_hf.HFExtractor(
        repo="org/model",
        revision=SHA,
        local_path=str(tmp_path),
        extractor_factory=lambda f: Inner(),
    )
    assert hf.calibrated is False
