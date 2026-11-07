"""Down-sync (contracts/sync_api.md `GET /sync/down`): other sites' assigned / rescued.

`pull` asks the cloud for events after the stored cursor, keeps only valid `assigned` and
`rescued` events from other devices that move a request strictly forward (the same
forward-only order as `rescue_field.status`: none < assigned < escalated < rescued), and
stores them without outbox rows (they are not ours to send). In the same transaction it
persists `next_since` and the approved rules/model versions: a newly approved version
becomes `approved_*` (read at the next restart) and the one it replaces moves to
`previous_*`, kept for rollback.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from functools import cache
from typing import Any

import httpx
from jsonschema import Draft202012Validator

from rescue_field.status import RANK, current_status
from rescue_field.store import EventStore
from rescue_shared.contracts_io import validator_for

CURSOR_KEY = "downsync_since"
DOWN_TYPES = ("assigned", "rescued")
VERSION_KINDS = ("rules_version", "model_version")


class DownSyncError(Exception):
    """The down-sync call failed or returned a body we cannot use."""


@cache
def _event_validator() -> Draft202012Validator:
    return validator_for("event.schema.json")


def cursor(store: EventStore) -> int:
    value = store.get_meta(CURSOR_KEY)
    return int(value) if value is not None else 0


def approved_versions(store: EventStore) -> dict[str, str | None]:
    """Approved and previous (rollback) versions recorded by down-sync."""
    out: dict[str, str | None] = {}
    for kind in VERSION_KINDS:
        out[f"approved_{kind}"] = store.get_meta(f"approved_{kind}")
        out[f"previous_{kind}"] = store.get_meta(f"previous_{kind}")
    return out


def _usable(ev: Any, device_id: str | None) -> bool:
    if not isinstance(ev, dict) or ev.get("type") not in DOWN_TYPES:
        return False
    if device_id is not None and ev.get("device_id") == device_id:
        return False  # the cloud does not echo our own events; ignore them if it does
    return _event_validator().is_valid(ev)


def forward_only(store: EventStore, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events that move their request strictly forward, in order (repeats and backward
    moves against local or earlier-in-batch status are dropped)."""
    status: dict[str, str | None] = {}
    keep = []
    for ev in events:
        rid = ev["request_id"]
        if rid not in status:
            status[rid] = current_status(store.request_events(rid))
        now = status[rid]
        if now is not None and RANK[ev["type"]] <= RANK[now]:
            continue
        status[rid] = ev["type"]
        keep.append(ev)
    return keep


def _version_meta(store: EventStore, approved: dict[str, Any]) -> dict[str, str]:
    meta: dict[str, str] = {}
    for kind in VERSION_KINDS:
        new = approved.get(kind)
        if not isinstance(new, str) or not new:
            continue  # null = nothing approved yet; keep what we have
        old = store.get_meta(f"approved_{kind}")
        if new == old:
            continue
        meta[f"approved_{kind}"] = new
        if old is not None:
            meta[f"previous_{kind}"] = old
    return meta


def _fetch(client: httpx.Client, since: int) -> dict[str, Any]:
    try:
        resp = client.get("/sync/down", params={"since": since})
    except httpx.HTTPError as exc:
        raise DownSyncError(f"/sync/down: {exc}") from exc
    if resp.status_code != 200:
        raise DownSyncError(f"/sync/down: HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise DownSyncError("/sync/down: response is not JSON") from exc
    if not isinstance(data, dict):
        raise DownSyncError("/sync/down: response is not an object")
    nxt = data.get("next_since")
    if isinstance(nxt, bool) or not isinstance(nxt, int) or nxt < 0:
        raise DownSyncError("/sync/down: next_since must be a non-negative integer")
    if not isinstance(data.get("events"), list):
        raise DownSyncError("/sync/down: events must be a list")
    if not isinstance(data.get("approved"), dict):
        data["approved"] = {}
    return data


def pull(
    client: httpx.Client,
    store: EventStore,
    lock: AbstractContextManager[Any] | None = None,
    *,
    device_id: str | None = None,
) -> list[str]:
    """One down-sync round. Returns the event ids stored. Raises DownSyncError."""
    lock = lock if lock is not None else nullcontext()
    with lock:
        since = cursor(store)
    data = _fetch(client, since)
    events = [ev for ev in data["events"] if _usable(ev, device_id)]
    with lock:
        keep = forward_only(store, events)
        meta = _version_meta(store, data["approved"])
        meta[CURSOR_KEY] = str(data["next_since"])
        return store.append_remote_events(keep, meta)
