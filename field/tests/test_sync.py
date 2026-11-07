import random

import httpx
import pytest
from fastapi.testclient import TestClient

from rescue_field import sync
from rescue_field.api import create_app
from rescue_field.store import EventStore
from tests.test_api_intake import fake_engine


def ev(i):
    return {
        "event_id": f"e{i:04d}",
        "request_id": f"r{i}",
        "type": "received",
        "occurred_at": "2026-11-07T10:00:00Z",
    }


class FakeCloud:
    def __init__(self):
        self.up = True
        self.health_status = 200
        self.events_failures = 0
        self.ack_limit = None  # ack only the first N of each batch
        self.reject = set()
        self.calls = []
        self.received = []
        self.heartbeats = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("offline")
        path = request.url.path
        self.calls.append(path)
        assert request.method == ("GET" if path == "/health" else "POST")
        if path == "/health":
            return httpx.Response(
                self.health_status,
                json={"status": "ok", "time": "2026-11-07T14:02:11Z"},
            )
        assert request.headers["authorization"] == "Bearer tok"
        import json

        body = json.loads(request.content)
        if path == "/sync/heartbeat":
            self.heartbeats.append(body)
            return httpx.Response(200, json={"ok": True, "server_time": "2026-11-07T14:02:11Z"})
        if self.events_failures:
            self.events_failures -= 1
            return httpx.Response(500, json={"error": "server_error"})
        events = body["events"]
        assert 1 <= len(events) <= 200
        ids = [e["event_id"] for e in events]
        rejected = [{"event_id": i, "error": "invalid_payload"} for i in ids if i in self.reject]
        ok = [i for i in ids if i not in self.reject]
        if self.ack_limit is not None:
            ok = ok[: self.ack_limit]
        self.received += ok
        return httpx.Response(200, json={"acked": ok, "rejected": rejected})


@pytest.fixture
def store():
    s = EventStore(check_same_thread=False)
    yield s
    s.close()


def make(store, cloud, **kw):
    return sync.SyncWorker(
        store, "http://cloud", device_id="laptop-dev-01", token="tok",
        versions=lambda: ("rules@abc1234+bin:12345678", "stub:fixture-extractor"),
        transport=httpx.MockTransport(cloud), rng=random.Random(1), **kw,
    )


def fill(store, n):
    store.append_events([ev(i) for i in range(n)])


def test_backoff_doubles_with_jitter_and_caps():
    rng = random.Random(0)
    for n, base in [(1, 2), (2, 4), (3, 8), (6, 64)]:
        for _ in range(20):
            assert base * 0.8 <= sync.backoff_delay(n, rng) <= base * 1.2
    assert all(sync.backoff_delay(50, rng) <= 300 for _ in range(50))
    assert sync.backoff_delay(50, rng) >= 240


def test_drains_in_batches_of_200_with_heartbeat_first(store):
    cloud = FakeCloud()
    fill(store, 450)
    result = make(store, cloud).sync_once()
    assert result.acked == 450 and store.outbox_count() == 0
    assert cloud.calls == ["/health", "/sync/heartbeat"] + ["/sync/events"] * 3
    assert cloud.received == [f"e{i:04d}" for i in range(450)]  # oldest first
    hb = cloud.heartbeats[0]
    assert hb["outbox_backlog"] == 450 and hb["device_id"] == "laptop-dev-01"
    assert hb["rules_version"].startswith("rules@")
    assert hb["model_version"] == "stub:fixture-extractor"


def test_partial_ack_keeps_the_rest(store):
    cloud = FakeCloud()
    cloud.ack_limit = 3
    fill(store, 10)
    w = make(store, cloud)
    delay = w.run_cycle()  # progress, then keeps draining 3 at a time
    assert store.outbox_count() == 0 and delay == w.idle_interval
    cloud2 = FakeCloud()
    cloud2.ack_limit = 0  # acks nothing: nothing is deleted
    store2 = EventStore()
    fill(store2, 4)
    make(store2, cloud2).sync_once()
    assert store2.outbox_count() == 4


def test_rejected_stay_in_outbox_but_do_not_block_others(store):
    cloud = FakeCloud()
    cloud.reject = {"e0001"}
    fill(store, 5)
    w = make(store, cloud)
    r = w.sync_once()
    assert r.rejected == 1
    assert [e["event_id"] for e in store.pending_outbox()] == ["e0001"]
    w.sync_once()  # rejected event is not resent unchanged
    assert cloud.received.count("e0001") == 0 and len(cloud.received) == 4


def test_unacked_ids_not_in_request_are_ignored(store):
    cloud = FakeCloud()
    fill(store, 2)
    w = make(store, cloud)
    orig = cloud.__call__
    w.client.close()
    w.client = httpx.Client(
        base_url="http://cloud", headers={"Authorization": "Bearer tok"},
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"acked": ["e0000", "bogus"], "rejected": []})
            if req.url.path == "/sync/events" else orig(req)
        ),
    )
    w.sync_once()
    assert [e["event_id"] for e in store.pending_outbox()] == ["e0001"]


def test_offline_keeps_outbox_and_backs_off_increasing(store):
    cloud = FakeCloud()
    cloud.up = False
    fill(store, 3)
    w = make(store, cloud)
    delays = [w.run_cycle() for _ in range(4)]
    assert store.outbox_count() == 3 and not w.online
    assert w.failures == 4
    assert 1.6 <= delays[0] <= 2.4 and 6.4 <= delays[2] <= 9.6
    cloud.up = True
    assert w.run_cycle() == w.idle_interval
    assert w.failures == 0 and w.online and store.outbox_count() == 0


def test_flaky_5xx_on_events_then_recovers(store):
    cloud = FakeCloud()
    cloud.events_failures = 2
    fill(store, 5)
    w = make(store, cloud)
    assert w.run_cycle() > 1 and store.outbox_count() == 5
    assert w.run_cycle() > 3 and store.outbox_count() == 5
    assert w.run_cycle() == w.idle_interval and store.outbox_count() == 0


def test_health_failure_skips_heartbeat(store):
    cloud = FakeCloud()
    cloud.health_status = 503
    fill(store, 1)
    w = make(store, cloud)
    w.run_cycle()
    assert cloud.calls == ["/health"] and store.outbox_count() == 1


def test_worker_thread_drains_and_stops(store):
    cloud = FakeCloud()
    fill(store, 3)
    w = make(store, cloud, idle_interval=0.05)
    w.start()
    try:
        for _ in range(100):
            if store.outbox_count() == 0:
                break
            import time

            time.sleep(0.05)
        assert store.outbox_count() == 0
    finally:
        w.stop()
    assert not w.running


def test_pause_stops_syncing(store):
    cloud = FakeCloud()
    w = make(store, cloud, idle_interval=0.05)
    w.pause()
    w.start()
    import time

    time.sleep(0.4)
    assert cloud.calls == []
    w.resume()
    time.sleep(0.5)
    w.stop()
    assert "/health" in cloud.calls


# ---- app wiring ------------------------------------------------------------------------


def test_app_starts_worker_with_cloud_url_and_toggle(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUD_URL", "http://cloud:4000")
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("FIELD_DATA_DIR", str(tmp_path))
    cloud = FakeCloud()
    cloud.up = False
    made = []

    def factory(st, url):
        w = sync.SyncWorker(
            st, url, device_id="laptop-dev-01", token="tok",
            versions=lambda: ("rules@abc1234+bin:12345678", "stub:x"),
            transport=httpx.MockTransport(cloud),
        )
        made.append(w)
        return w

    app = create_app(decide_batch=fake_engine(), sync_worker_factory=factory)
    with TestClient(app) as client:
        assert len(made) == 1 and made[0].running
        assert client.get("/sync/status").json()["running"] is True
        assert client.post("/sync/offline-toggle").json() == {"paused": True}
        assert made[0].paused
        assert client.post("/sync/offline-toggle").json() == {"paused": False}
    assert not made[0].running


def test_app_default_factory_builds_real_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUD_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("FIELD_DATA_DIR", str(tmp_path))
    app = create_app(decide_batch=fake_engine())
    with TestClient(app) as client:
        assert isinstance(app.state.sync_worker, sync.SyncWorker)
        assert app.state.sync_worker.running
        assert client.post("/sync/offline-toggle").status_code == 404  # DEMO_MODE off


def test_status_without_worker(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUD_URL", raising=False)
    monkeypatch.setenv("FIELD_DATA_DIR", str(tmp_path))
    with TestClient(create_app(decide_batch=fake_engine())) as client:
        assert client.get("/sync/status").json()["running"] is False
