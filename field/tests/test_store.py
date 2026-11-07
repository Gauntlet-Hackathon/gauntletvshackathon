import pytest

from rescue_field.store import EventStore


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


def test_rejected_leave_pending_and_persist(tmp_path):
    path = tmp_path / "r.db"
    s = EventStore(path)
    s.append_events([ev(1), ev(2), ev(3)])
    at = "2026-11-07T14:00:00Z"
    assert s.mark_rejected([("E002", "invalid_payload"), ("nope", "x")], at) == 1
    assert s.mark_rejected([("E002", "again")], "2026-11-07T15:00:00Z") == 0  # first one kept
    assert [e["event_id"] for e in s.pending_outbox()] == ["E001", "E003"]
    assert s.outbox_count() == 2 and s.rejected_count() == 1
    s.close()
    s = EventStore(path)
    assert [e["event_id"] for e in s.pending_outbox()] == ["E001", "E003"]
    assert s.rejected() == [{"event_id": "E002", "error": "invalid_payload", "rejected_at": at}]
    assert len(s.all_events()) == 3  # the event itself stays
    s.close()


OLD_SCHEMA = """
CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE, request_id TEXT NOT NULL, type TEXT NOT NULL,
    occurred_at TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id));
"""


def test_opens_and_migrates_a_store_without_rejection_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(OLD_SCHEMA)
    old.execute(
        "INSERT INTO events(event_id, request_id, type, occurred_at, body) VALUES (?,?,?,?,?)",
        ("E001", "R1", "received", "2026-11-07T14:02:11Z", '{"event_id":"E001"}'),
    )
    old.execute("INSERT INTO outbox(event_id) VALUES ('E001')")
    old.commit()
    old.close()
    s = EventStore(path)
    assert [e["event_id"] for e in s.pending_outbox()] == ["E001"]
    assert s.mark_rejected([("E001", "invalid_payload")], "2026-11-07T14:00:00Z") == 1
    assert s.pending_outbox() == [] and s.rejected_count() == 1
    s.close()
    s = EventStore(path)  # migrating again is a no-op
    assert s.rejected_count() == 1
    s.close()


def test_shared_connection_is_safe_without_caller_lock(tmp_path):
    """Regression (CI on main): a sync worker built without the app lock raced request threads
    on the one connection and outbox_count() got a None row. The store serializes itself."""
    import threading

    s = EventStore(tmp_path / "t.db", check_same_thread=False)
    errors: list[BaseException] = []

    def writer(base):
        try:
            for i in range(150):
                ids = s.append_events([ev(base + i)])
                s.ack(ids)
                s.append_events([ev(base + 1000 + i)])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def reader():
        try:
            for _ in range(600):
                assert isinstance(s.outbox_count(), int)
                assert isinstance(s.rejected_count(), int)
                s.pending_outbox(5)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(b,)) for b in (0, 5000)]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert errors == []
        assert s.outbox_count() == 300
    finally:
        s.close()
