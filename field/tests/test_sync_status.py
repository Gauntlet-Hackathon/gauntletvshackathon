"""F9: /sync/status, offline toggle (DEMO_MODE), live badge on the board."""

import httpx
import pytest
from fastapi.testclient import TestClient

from rescue_field import sync
from rescue_field.api import create_app
from tests.test_api_intake import fake_engine
from tests.test_sync import FakeCloud


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUD_URL", "http://cloud:4000")
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("FIELD_DATA_DIR", str(tmp_path))


def build(cloud):
    def factory(st, url):
        return sync.SyncWorker(
            st, url, device_id="laptop-dev-01", token="tok",
            versions=lambda: ("rules@abc1234+bin:12345678", "stub:x"),
            transport=httpx.MockTransport(cloud), idle_interval=0.05,
        )

    return create_app(decide_batch=fake_engine(), sync_worker_factory=factory)


def test_toggle_offline_then_requests_grow_backlog(env):
    cloud = FakeCloud()
    with TestClient(build(cloud)) as client:
        assert client.post("/sync/offline-toggle").json() == {"paused": True}
        for _ in range(3):
            assert client.post("/requests", json={"raw_text": "help, flooding"}).status_code == 200
        st = client.get("/sync/status").json()
        assert st["paused"] is True and st["outbox_backlog"] > 0
        before = st["outbox_backlog"]
        badge = client.get("/sync/badge").text
        assert 'data-state="offline"' in badge and f"{before} queued" in badge
        assert "/sync/badge" in client.get("/").text
        client.post("/requests", json={"raw_text": "another"})
        assert client.get("/sync/status").json()["outbox_backlog"] > before
        # reconnect: backlog drains
        assert client.post("/sync/offline-toggle").json() == {"paused": False}
        import time

        for _ in range(60):
            if client.get("/sync/status").json()["outbox_backlog"] == 0:
                break
            time.sleep(0.1)
        st = client.get("/sync/status").json()
        assert st["outbox_backlog"] == 0 and st["last_sync_at"]
        assert 'data-state="online"' in client.get("/sync/badge").text


def test_toggle_requires_demo_mode(env, monkeypatch):
    monkeypatch.delenv("DEMO_MODE")
    with TestClient(build(FakeCloud())) as client:
        assert client.post("/sync/offline-toggle").status_code == 404
        assert client.get("/sync/status").json()["paused"] is False


def test_app_with_cloud_url_starts_worker(env):
    app = build(FakeCloud())
    with TestClient(app):
        assert app.state.sync_worker.running


def test_badge_without_cloud_url(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUD_URL", raising=False)
    monkeypatch.setenv("FIELD_DATA_DIR", str(tmp_path))
    with TestClient(create_app(decide_batch=fake_engine())) as client:
        assert 'data-state="no-sync"' in client.get("/sync/badge").text
        assert "/sync/badge" in client.get("/").text


def test_badge_without_worker_shows_store_backlog(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUD_URL", raising=False)
    monkeypatch.setenv("FIELD_DATA_DIR", str(tmp_path))
    with TestClient(create_app(decide_batch=fake_engine())) as client:
        for _ in range(2):
            assert client.post("/requests", json={"raw_text": "help, flooding"}).status_code == 200
        n = client.get("/sync/status").json()["outbox_backlog"]
        assert n > 0
        assert f"{n} queued" in client.get("/sync/badge").text


def test_badge_markup_is_accessible_status(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUD_URL", raising=False)
    monkeypatch.setenv("FIELD_DATA_DIR", str(tmp_path))
    with TestClient(create_app(decide_batch=fake_engine())) as client:
        page = client.get("/").text
        assert 'role="status"' in page and 'aria-live="polite"' in page
        assert "Sync status unavailable" in page
        assert "Sync not configured" in client.get("/sync/badge").text


BTN = '<button type="button" id="offline-toggle">Simulate offline</button>'


def test_toggle_button_only_in_demo_mode(env, monkeypatch):
    with TestClient(build(FakeCloud())) as client:
        assert BTN in client.get("/").text
        monkeypatch.delenv("DEMO_MODE")
        assert BTN not in client.get("/").text
