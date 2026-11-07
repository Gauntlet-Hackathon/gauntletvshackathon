"""F27: surge load test runs end to end against the real fallback engine and a fake cloud."""

from __future__ import annotations

import json

import httpx

from rescue_field import loadtest
from tests.test_sync_integration import DEVICE, TOKEN, ContractCloud  # noqa: F401


def test_loadtest_300_real_engine_drains_to_fake_cloud(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DEVICE_ID", DEVICE)
    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    cloud = ContractCloud()
    rc = loadtest.main(
        [
            "--count",
            "300",
            "--batch",
            "100",
            "--data-dir",
            str(tmp_path),
            "--cloud-url",
            "http://cloud",
        ],
        transport=httpx.MockTransport(cloud),
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    for key in (
        "count",
        "intake_seconds",
        "seconds_per_1000_intake",
        "sync_seconds",
        "seconds_per_1000_sync",
        "outbox_left",
        "tiers",
    ):
        assert key in out
    assert out["count"] == 300
    assert out["outbox_left"] == 0
    assert out["intake_seconds"] < 60
    assert sum(out["tiers"].values()) == 300
    decided = [e for e in cloud.stored if e["type"] == "decided"]
    assert len({e["request_id"] for e in decided}) == 300
    assert len(cloud.stored) == 900


def test_loadtest_without_cloud_leaves_outbox(tmp_path, capsys):
    assert loadtest.main(["--count", "20", "--data-dir", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sync_seconds"] is None
    assert out["outbox_left"] == 60
