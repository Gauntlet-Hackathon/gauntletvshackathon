import httpx
import pytest

from rescue_field import downsync
from rescue_field.store import EventStore


def ev(eid, rid, typ, dev="laptop-dev-01"):
    return {
        "event_id": eid, "request_id": rid, "type": typ, "device_id": dev,
        "occurred_at": "2026-11-07T10:00:00Z", "rules_version": "r", "model_version": "m",
        "payload": {},
    }


@pytest.fixture
def store():
    s = EventStore()
    s.append_events([ev("d1", "r1", "decided"), ev("d2", "r2", "decided")])
    s.ack(["d1", "d2"])
    yield s
    s.close()


def client(handler):
    return httpx.Client(base_url="http://c", transport=httpx.MockTransport(handler))


def resp(events, nxt, approved=None):
    return httpx.Response(200, json={
        "next_since": nxt, "events": events,
        "approved": approved or {"rules_version": None, "model_version": None},
    })


def types(store, rid):
    return [e["type"] for e in store.all_events() if e["request_id"] == rid]


def test_applies_remote_events_and_stores_cursor(store):
    seen = []

    def h(req):
        seen.append(dict(req.url.params))
        return resp([ev("a1", "r1", "assigned", "other"), ev("x", "r1", "decided", "o")], 7)

    c = client(h)
    assert downsync.pull(c, store) == ["a1"]
    assert types(store, "r1") == ["decided", "assigned"]
    assert store.outbox_count() == 0  # not echoed
    assert downsync.get_cursor(store) == 7
    downsync.pull(c, store)
    assert seen == [{"since": "0"}, {"since": "7"}]


def test_forward_only_never_overwrites_local(store):
    store.append_events([ev("l1", "r1", "rescued")])
    c = client(lambda r: resp([
        ev("a1", "r1", "assigned", "o"),
        ev("a2", "r1", "rescued", "o"),
        ev("a3", "r2", "rescued", "o"),
        ev("a4", "r2", "assigned", "o"),
        ev("a5", "unknown", "assigned", "o"),
    ], 5))
    assert downsync.pull(c, store) == ["a3"]
    assert types(store, "r1") == ["decided", "rescued"]
    assert types(store, "r2") == ["decided", "rescued"]
    assert downsync.get_cursor(store) == 5


def test_records_approved_versions_keeps_previous(store):
    def run(rules, model):
        ap = {"rules_version": rules, "model_version": model}
        downsync.pull(client(lambda r: resp([], 1, ap)), store)

    run("rules@1", "model@1")
    assert downsync.approved_versions(store) == {
        "rules_version": "rules@1", "previous_rules_version": None,
        "model_version": "model@1", "previous_model_version": None,
    }
    run("rules@2", "model@1")
    run("rules@2", None)
    got = downsync.approved_versions(store)
    assert got["rules_version"] == "rules@2" and got["previous_rules_version"] == "rules@1"
    assert got["model_version"] == "model@1" and got["previous_model_version"] is None


def test_failure_raises_and_keeps_cursor(store):
    with pytest.raises(downsync.DownSyncError):
        downsync.pull(client(lambda r: httpx.Response(500)), store)
    with pytest.raises(downsync.DownSyncError):
        downsync.pull(client(lambda r: httpx.Response(200, json={"x": 1})), store)
    assert downsync.get_cursor(store) == 0
