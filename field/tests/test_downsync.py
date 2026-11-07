import json
import random

import httpx
import pytest

from rescue_field import downsync, sync
from rescue_field.store import EventStore

RULES_A = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
RULES_B = "rules@b2c3d4e+bin:1111222233334444"
MODEL_A = "hf:rescue-org/rescue-tagger@4d5e6f7a8b"
RQ = "01JCB00000000000000000RQ02"
OTHER = "field-07"


def remote(n, type_="assigned", request_id=RQ, device_id=OTHER):
    payload = {"crew": "boat-7", "note": None} if type_ == "assigned" else {
        "rescued_count": 2, "note": None}
    return {
        "event_id": f"01JCB00000000000000000EV{n:02d}",
        "request_id": request_id,
        "type": type_,
        "device_id": device_id,
        "occurred_at": "2026-11-07T14:02:11Z",
        "rules_version": RULES_A,
        "model_version": MODEL_A,
        "payload": payload,
    }


def local(n, type_, request_id=RQ):
    ev = remote(n, type_, request_id, device_id="laptop-dev-01")
    ev["event_id"] = f"01JCB00000000000000000LC{n:02d}"
    return ev


class DownCloud:
    """Answers /health, /sync/heartbeat, /sync/events and /sync/down."""

    def __init__(self, pages):
        self.pages = list(pages)  # successive /sync/down bodies (dict) or status codes (int)
        self.down_params = []
        self.calls = []
        self.uploaded = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "time": "2026-11-07T14:02:11Z"})
        assert request.headers["authorization"] == "Bearer tok"
        if path == "/sync/heartbeat":
            return httpx.Response(200, json={"ok": True, "server_time": "2026-11-07T14:02:11Z"})
        if path == "/sync/events":
            ids = [e["event_id"] for e in json.loads(request.content)["events"]]
            self.uploaded += ids
            return httpx.Response(200, json={"acked": ids, "rejected": []})
        assert path == "/sync/down" and request.method == "GET"
        self.down_params.append(dict(request.url.params))
        page = self.pages.pop(0)
        if isinstance(page, int):
            return httpx.Response(page, json={"error": "server_error"})
        return httpx.Response(200, json=page)


def page(next_since, events=(), rules=None, model=None):
    return {
        "next_since": next_since,
        "events": list(events),
        "approved": {"rules_version": rules, "model_version": model},
    }


@pytest.fixture
def store():
    s = EventStore(check_same_thread=False)
    yield s
    s.close()


def client(cloud):
    return httpx.Client(
        base_url="http://c", headers={"Authorization": "Bearer tok"},
        transport=httpx.MockTransport(cloud),
    )


def test_pull_applies_remote_events_and_stores_cursor(store):
    cloud = DownCloud([page(7, [remote(1)]), page(9, [remote(2, "rescued")])])
    c = client(cloud)
    assert downsync.pull(c, store) == [remote(1)["event_id"]]
    assert downsync.cursor(store) == 7
    assert downsync.pull(c, store) == [remote(2)["event_id"]]
    assert downsync.cursor(store) == 9
    assert cloud.down_params == [{"since": "0"}, {"since": "7"}]
    assert [e["type"] for e in store.all_events()] == ["assigned", "rescued"]
    assert store.outbox_count() == 0  # remote events are never sent back up


def test_cursor_survives_reopen(tmp_path):
    path = tmp_path / "field.db"
    s = EventStore(path)
    downsync.pull(client(DownCloud([page(42)])), s)
    s.close()
    s = EventStore(path)
    cloud = DownCloud([page(43)])
    downsync.pull(client(cloud), s)
    assert cloud.down_params == [{"since": "42"}]
    s.close()


def test_forward_only_never_overwrites_local_progress(store):
    store.append_events([local(1, "rescued")])
    cloud = DownCloud([page(3, [remote(1, "assigned"), remote(2, "rescued")])])
    assert downsync.pull(client(cloud), store) == []
    assert downsync.cursor(store) == 3  # cursor still advances past what we skipped
    assert [e["type"] for e in store.all_events()] == ["rescued"]


def test_forward_moves_apply_and_repeats_in_batch_drop(store):
    store.append_events([local(1, "assigned")])
    evs = [remote(1, "assigned"), remote(2, "rescued"), remote(3, "assigned")]
    stored = downsync.pull(client(DownCloud([page(5, evs)])), store)
    assert stored == [remote(2)["event_id"]]
    assert store.outbox_count() == 1  # only the local event


def test_unknown_request_is_still_recorded(store):
    other = "01JCB00000000000000000RQ99"
    stored = downsync.pull(client(DownCloud([page(1, [remote(1, request_id=other)])])), store)
    assert stored == [remote(1)["event_id"]]


def test_skips_malformed_wrong_type_and_own_events(store):
    bad_missing = {k: v for k, v in remote(1).items() if k != "request_id"}
    bad_type = dict(remote(2), type="decided")
    own = remote(3, device_id="laptop-dev-01")
    good = remote(4)
    evs = [bad_missing, bad_type, "nonsense", {"type": "assigned"}, own, good]
    stored = downsync.pull(
        client(DownCloud([page(2, evs)])), store, device_id="laptop-dev-01"
    )
    assert stored == [good["event_id"]]


@pytest.mark.parametrize(
    "body",
    [
        {"events": [], "approved": {}},
        {"next_since": "7", "events": [], "approved": {}},
        {"next_since": -1, "events": [], "approved": {}},
        {"next_since": True, "events": [], "approved": {}},
        {"next_since": 1, "events": {}, "approved": {}},
        [1, 2],
    ],
)
def test_bad_body_raises_and_keeps_cursor(store, body):
    with pytest.raises(downsync.DownSyncError):
        downsync.pull(client(DownCloud([body])), store)
    assert downsync.cursor(store) == 0


def test_http_error_raises(store):
    with pytest.raises(downsync.DownSyncError):
        downsync.pull(client(DownCloud([500])), store)


def test_approved_versions_recorded_with_previous_for_rollback(store):
    cloud = DownCloud([
        page(1, rules=RULES_A),
        page(2, rules=RULES_A, model=MODEL_A),
        page(3, rules=RULES_B, model=None),
    ])
    c = client(cloud)
    downsync.pull(c, store)
    assert downsync.approved_versions(store) == {
        "approved_rules_version": RULES_A, "previous_rules_version": None,
        "approved_model_version": None, "previous_model_version": None,
    }
    downsync.pull(c, store)  # same rules again: previous stays empty
    assert downsync.approved_versions(store)["previous_rules_version"] is None
    assert downsync.approved_versions(store)["approved_model_version"] == MODEL_A
    downsync.pull(c, store)  # new rules; null model keeps the approved model
    assert downsync.approved_versions(store) == {
        "approved_rules_version": RULES_B, "previous_rules_version": RULES_A,
        "approved_model_version": MODEL_A, "previous_model_version": None,
    }


# ---- worker integration ---------------------------------------------------------------


def make_worker(store, cloud, **kw):
    return sync.SyncWorker(
        store, "http://c", device_id="laptop-dev-01", token="tok",
        versions=lambda: (RULES_A, MODEL_A),
        transport=httpx.MockTransport(cloud), rng=random.Random(1), **kw,
    )


def test_worker_polls_down_after_upload(store):
    store.append_events([local(1, "assigned", request_id="01JCB00000000000000000RQ05")])
    cloud = DownCloud([page(7, [remote(1)]), page(8)])
    w = make_worker(store, cloud, down_sync=True)
    result = w.sync_once()
    assert result.pulled == 1 and result.acked == 1
    assert cloud.calls == ["/health", "/sync/heartbeat", "/sync/events", "/sync/down"]
    assert store.outbox_count() == 0
    assert any(e["event_id"] == remote(1)["event_id"] for e in store.all_events())
    assert downsync.cursor(store) == 7
    w.sync_once()
    assert cloud.down_params == [{"since": "0"}, {"since": "7"}]
    assert cloud.uploaded == [local(1, "assigned")["event_id"]]  # remote never re-sent
    assert downsync.cursor(store) == 8
    w.client.close()


def test_worker_down_sync_off_by_default(store):
    cloud = DownCloud([])
    w = make_worker(store, cloud)
    w.sync_once()
    assert "/sync/down" not in cloud.calls
    w.client.close()


def test_worker_down_sync_failure_backs_off(store):
    w = make_worker(store, DownCloud([500]), down_sync=True)
    delay = w.run_cycle()
    assert w.online is False and w.failures == 1 and delay > 0
    assert "/sync/down" in w.last_error
    w.client.close()


def test_worker_survives_malformed_down_events(store):
    w = make_worker(store, DownCloud([page(4, [{"type": "rescued"}, remote(1)])]), down_sync=True)
    w.run_cycle()
    assert w.online is True and w.failures == 0
    assert downsync.cursor(store) == 4
    w.client.close()


def test_worker_from_env_enables_down_sync(store):
    w = sync.worker_from_env(store, "http://c", model_version=MODEL_A, env={})
    assert w.down_sync is True
    w.client.close()
