import pytest

from field_app.store import EventStore


def ev(n, request_id="R1"):
    return {
        "event_id": f"E{n:03d}",
        "request_id": request_id,
        "type": "received",
        "device_id": "field-01",
        "occurred_at": "2026-11-07T14:02:11Z",
        "payload": {},
    }


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db")
    yield s
    s.close()


def test_wal_mode(store):
    assert store.journal_mode() == "wal"


def test_append_and_pending_oldest_first(store):
    store.append_events([ev(2), ev(1), ev(3)])
    assert [e["event_id"] for e in store.pending_outbox(10)] == ["E002", "E001", "E003"]
    assert [e["event_id"] for e in store.pending_outbox(2)] == ["E002", "E001"]


def test_failure_mid_write_leaves_neither(store):
    bad = {"event_id": "E099"}  # missing keys -> KeyError after E001 written
    with pytest.raises(KeyError):
        store.append_events([ev(1), bad])
    assert store.all_events() == []
    assert store.pending_outbox() == []
    store.append_events([ev(1)])  # store still usable
    assert len(store.pending_outbox()) == 1


def test_failure_injected_in_outbox_insert(store):
    store._conn.execute("DROP TABLE outbox")
    store._conn.execute("CREATE TABLE outbox (seq INTEGER PRIMARY KEY, x NOT NULL)")
    with pytest.raises(Exception):  # noqa: B017
        store.append_events([ev(1)])
    assert store.all_events() == []


def test_ack_deletes_only_given_ids(store):
    store.append_events([ev(1), ev(2), ev(3)])
    assert store.ack(["E001", "E003", "nope"]) == 2
    assert [e["event_id"] for e in store.pending_outbox()] == ["E002"]
    assert len(store.all_events()) == 3


def test_duplicate_event_id_ignored(store):
    assert store.append_events([ev(1), ev(1)]) == ["E001"]
    assert store.append_events([ev(1)]) == []
    assert len(store.all_events()) == 1
    assert len(store.pending_outbox()) == 1
    store.ack(["E001"])
    store.append_events([ev(1)])  # already synced: not re-queued
    assert store.pending_outbox() == []
