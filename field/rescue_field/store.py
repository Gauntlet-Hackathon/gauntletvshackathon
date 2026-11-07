"""SQLite event store with a transactional outbox.

Events and their outbox rows are written in one transaction, so an event is never
stored without being queued for sync (and vice versa). An outbox row the cloud rejected is
kept with `rejected_at`/`reject_error` set and is never sent again (events never change, so a
resend would be "unchanged", which contracts/sync_api.md forbids).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_request ON events(request_id);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
    rejected_at TEXT,
    reject_error TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
# Columns added after the first release: (table, column, type). Added on open to older files.
_MIGRATIONS = (
    ("outbox", "rejected_at", "TEXT"),
    ("outbox", "reject_error", "TEXT"),
)


class EventStore:
    def __init__(self, path: str | Path = ":memory:", *, check_same_thread: bool = True) -> None:
        # isolation_level=None: we manage transactions explicitly.
        # check_same_thread=False lets a web server use the store from worker threads; the
        # caller must then serialize access (the field API holds a lock).
        self._conn = sqlite3.connect(
            str(path), isolation_level=None, check_same_thread=check_same_thread
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        for table, column, kind in _MIGRATIONS:
            cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def close(self) -> None:
        self._conn.close()

    def journal_mode(self) -> str:
        return self._conn.execute("PRAGMA journal_mode").fetchone()[0]

    def append_events(self, events: Iterable[dict[str, Any]]) -> list[str]:
        """Store events and queue them in the outbox atomically.

        Events whose event_id already exists are ignored. Returns the ids newly stored.
        """
        stored: list[str] = []
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for ev in events:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO events(event_id, request_id, type, occurred_at, body)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        ev["event_id"],
                        ev["request_id"],
                        ev["type"],
                        ev["occurred_at"],
                        json.dumps(ev, separators=(",", ":"), allow_nan=False),
                    ),
                )
                if cur.rowcount:
                    self._conn.execute(
                        "INSERT INTO outbox(event_id) VALUES (?)", (ev["event_id"],)
                    )
                    stored.append(ev["event_id"])
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        return stored

    def pending_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        """Unsynced events the cloud has not rejected, oldest first."""
        rows = self._conn.execute(
            "SELECT e.body FROM outbox o JOIN events e ON e.event_id = o.event_id"
            " WHERE o.rejected_at IS NULL ORDER BY o.seq LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def outbox_count(self) -> int:
        """Events still to send (rejected events are not counted: they will not be sent)."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM outbox WHERE rejected_at IS NULL"
        ).fetchone()[0]

    def rejected_count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM outbox WHERE rejected_at IS NOT NULL"
        ).fetchone()[0]

    def rejected(self) -> list[dict[str, str]]:
        """[{"event_id", "error", "rejected_at"}] for rejected events, oldest first."""
        rows = self._conn.execute(
            "SELECT event_id, reject_error, rejected_at FROM outbox"
            " WHERE rejected_at IS NOT NULL ORDER BY seq"
        ).fetchall()
        return [{"event_id": i, "error": e, "rejected_at": t} for i, e, t in rows]

    def mark_rejected(self, rejections: Iterable[tuple[str, str]], at: str) -> int:
        """Record (event_id, error) pairs as rejected at `at`; they leave pending_outbox for
        good. Ids not in the outbox (or already rejected) are ignored. Returns rows marked."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            marked = 0
            for event_id, error in rejections:
                marked += self._conn.execute(
                    "UPDATE outbox SET rejected_at = ?, reject_error = ?"
                    " WHERE event_id = ? AND rejected_at IS NULL",
                    (at, error, event_id),
                ).rowcount
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        return marked

    def ack(self, event_ids: Iterable[str]) -> int:
        """Remove the given ids from the outbox (events stay). Returns rows removed."""
        ids = [(i,) for i in event_ids]
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            removed = 0
            for row in ids:
                removed += self._conn.execute(
                    "DELETE FROM outbox WHERE event_id = ?", row
                ).rowcount
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        return removed

    def all_events(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT body FROM events ORDER BY seq").fetchall()
        return [json.loads(r[0]) for r in rows]

    def latest_rules_version(self, device_id: str | None = None) -> str | None:
        """rules_version of the newest `decided` event (from `device_id` when given), i.e. the
        engine build that made the most recent decision; None before any decision."""
        sql = "SELECT body FROM events WHERE type = 'decided'"
        args: tuple[str, ...] = ()
        if device_id is not None:
            sql += " AND json_extract(body, '$.device_id') = ?"
            args = (device_id,)
        row = self._conn.execute(sql + " ORDER BY seq DESC LIMIT 1", args).fetchone()
        return json.loads(row[0]).get("rules_version") if row else None

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, values: dict[str, str]) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._upsert_meta(values)
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def _upsert_meta(self, values: dict[str, str]) -> None:
        for key, value in values.items():
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def append_remote_events(
        self, events: Iterable[dict[str, Any]], meta: dict[str, str] | None = None
    ) -> list[str]:
        """Store events that came from the cloud (down-sync): no outbox row, they are not
        ours to send. `meta` (e.g. the down-sync cursor) is written in the same transaction.
        Existing event_ids are ignored. Returns the ids newly stored."""
        stored: list[str] = []
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for ev in events:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO events(event_id, request_id, type, occurred_at, body)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        ev["event_id"],
                        ev["request_id"],
                        ev["type"],
                        ev["occurred_at"],
                        json.dumps(ev, separators=(",", ":")),
                    ),
                )
                if cur.rowcount:
                    stored.append(ev["event_id"])
            self._upsert_meta(meta or {})
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        return stored

    def request_events(self, request_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT body FROM events WHERE request_id = ? ORDER BY seq", (request_id,)
        ).fetchall()
        return [json.loads(r[0]) for r in rows]
