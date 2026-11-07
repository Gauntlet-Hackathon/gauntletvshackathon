"""Down-sync: pull other sites' assigned/rescued events so two crews don't go to the same roof.

`GET /sync/down?since=<cursor>` (contracts/sync_api.md). Received events are stored without an
outbox row (never echoed back). Forward-only: an event is applied only if it moves its request
strictly past the local status (see status.py order), so local progress is never overwritten;
events for requests this laptop has not seen are skipped. The cursor (`next_since`) and the
approved rules/model versions are persisted; when an approved version changes, the old one is
kept as `previous_*` for rollback. The new versions take effect at the next restart.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

import httpx

from rescue_field.status import RANK, current_status
from rescue_field.store import EventStore

CURSOR_KEY = "down_cursor"
DOWN_TYPES = ("assigned", "rescued")


class DownSyncError(Exception):
    """The down-sync request failed; the worker backs off and retries."""


def get_cursor(store: EventStore) -> int:
    return int(store.get_meta(CURSOR_KEY, "0") or 0)


def approved_versions(store: EventStore) -> dict[str, str | None]:
    """Recorded approved/previous versions (applied at next restart)."""
    keys = ("rules_version", "model_version")
    out: dict[str, str | None] = {}
    for k in keys:
        out[k] = store.get_meta(f"approved_{k}")
        out[f"previous_{k}"] = store.get_meta(f"previous_{k}")
    return out


def _record_approved(store: EventStore, approved: Any) -> None:
    if not isinstance(approved, dict):
        return
    items: dict[str, str] = {}
    for k in ("rules_version", "model_version"):
        new = approved.get(k)
        old = store.get_meta(f"approved_{k}")
        if isinstance(new, str) and new and new != old:
            if old is not None:
                items[f"previous_{k}"] = old
            items[f"approved_{k}"] = new
    if items:
        store.set_meta(items)


def apply_events(store: EventStore, events: list[dict[str, Any]]) -> list[str]:
    """Store the forward-moving subset of `events`; returns ids stored."""
    by_request: dict[str, list[dict[str, Any]]] = {}
    for e in store.all_events():
        by_request.setdefault(e["request_id"], []).append(e)
    keep: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("type") not in DOWN_TYPES:
            continue
        local = by_request.get(ev["request_id"])
        if not local:
            continue
        now = current_status(local)
        if now is not None and RANK[ev["type"]] <= RANK[now]:
            continue
        local.append(ev)  # later events in this batch are judged against it
        keep.append(ev)
    return store.append_remote_events(keep) if keep else []


def pull(
    client: httpx.Client,
    store: EventStore,
    lock: AbstractContextManager[Any] | None = None,
) -> list[str]:
    """One poll: fetch, apply, persist cursor and approved versions. Returns stored event ids."""
    lock = lock if lock is not None else nullcontext()
    with lock:
        since = get_cursor(store)
    try:
        resp = client.get("/sync/down", params={"since": since})
    except httpx.HTTPError as exc:
        raise DownSyncError(f"/sync/down: {exc}") from exc
    if resp.status_code != 200:
        raise DownSyncError(f"/sync/down: HTTP {resp.status_code}")
    try:
        data = resp.json()
        events = data["events"]
        next_since = int(data["next_since"])
    except (ValueError, KeyError, TypeError) as exc:
        raise DownSyncError("/sync/down: bad response body") from exc
    if not isinstance(events, list):
        raise DownSyncError("/sync/down: bad response body")
    with lock:
        stored = apply_events(store, [e for e in events if isinstance(e, dict)])
        _record_approved(store, data.get("approved"))
        if next_since > since:
            store.set_meta({CURSOR_KEY: str(next_since)})
    return stored
