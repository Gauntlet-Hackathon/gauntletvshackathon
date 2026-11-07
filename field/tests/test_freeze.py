"""The field app refuses to start when its decision config differs from the frozen engine build
(DECISIONS H12 / R15)."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field import freeze
from rescue_field.api import create_app
from rescue_field.pipeline import DEFAULT_THRESHOLDS, PipelineConfig
from rescue_field.store import EventStore
from rescue_shared import frozen_config

ROOT = Path(__file__).resolve().parents[2]
[FROZEN] = [p for p in (ROOT / "engine" / "dist").iterdir() if p.is_dir()]
MANIFEST = json.loads((FROZEN / "manifest.json").read_text())


def _env(build: Path = FROZEN, **extra):
    return {"ENGINE_CMD": str(build / MANIFEST["entrypoint"]), **extra}


def _defaults():
    c = PipelineConfig()
    return c.thresholds, c.hoax_markers, c.streets


def test_field_defaults_match_the_committed_build():
    assert freeze.check(*_defaults(), env=_env(RULES_VERSION=MANIFEST["rules_version"]))


def test_no_build_means_nothing_to_check():
    assert freeze.check(*_defaults(), env={}) is None
    source = {"ENGINE_CMD": "python -m rescue_engine.fallback"}
    assert freeze.check(*_defaults(), env=source) is None


@pytest.mark.parametrize("part", ["thresholds", "hoax_markers", "streets"])
def test_changed_config_is_refused(part):
    thresholds, markers, streets = _defaults()
    if part == "thresholds":
        thresholds = {**thresholds, "hazard_firm_min": 0.7}
    elif part == "hoax_markers":
        markers = (*markers, "fake news")
    else:
        streets = streets[:-1]
    with pytest.raises(freeze.FrozenConfigMismatch, match=f"changed: {part}"):
        freeze.check(thresholds, markers, streets, env=_env())


def test_wrong_rules_version_is_refused():
    with pytest.raises(freeze.FrozenConfigMismatch, match="RULES_VERSION"):
        freeze.check(*_defaults(), env=_env(RULES_VERSION="rules@abcdef1+bin:0123456789abcdef"))


def test_build_without_config_hash_is_refused(tmp_path):
    build = tmp_path / "old"
    (build / "bin").mkdir(parents=True)
    old = {k: v for k, v in MANIFEST.items() if k not in ("config_hash", "artifact_hash")}
    (build / "manifest.json").write_text(json.dumps(old))
    (build / "bin" / "mercury_engine").write_text("")
    with pytest.raises(freeze.FrozenConfigMismatch, match="no config_hash"):
        freeze.check(*_defaults(), env=_env(build))


def test_tampered_manifest_is_refused(tmp_path):
    build = tmp_path / "b"
    shutil.copytree(FROZEN, build)
    m = {**MANIFEST, "config_hash": frozen_config.config_hash({}, (), ())}
    (build / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(freeze.FrozenConfigMismatch, match="inconsistent"):
        freeze.check(*_defaults(), env=_env(build))


def test_app_does_not_start_on_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_CMD", str(FROZEN / MANIFEST["entrypoint"]))
    monkeypatch.delenv("CLOUD_URL", raising=False)
    store = EventStore(tmp_path / "f.db", check_same_thread=False)
    bad = PipelineConfig(thresholds={**DEFAULT_THRESHOLDS, "request_confidence_min": 0.5})
    app = create_app(store=store, decide_batch=lambda inputs: [], config=bad)
    with pytest.raises(freeze.FrozenConfigMismatch):
        with TestClient(app):
            pass
    good = create_app(store=store, decide_batch=lambda inputs: [], config=PipelineConfig())
    with TestClient(good) as client:
        assert client.get("/sync/status").status_code == 200
