"""Sync worker: drain the outbox to the cloud whenever it is reachable (contracts/sync_api.md).

Cycle: `GET /health`; on success `POST /sync/heartbeat` (backlog + versions), then
`POST /sync/events` in batches of 200, oldest first, then (when `down_sync` is on)
`GET /sync/down` via `rescue_field.downsync`. Only event ids the cloud *acked* leave
the outbox. Rejected events are marked rejected in the store (with the cloud's error) and are
never sent again, across restarts too, so they do not block newer events; /sync/status shows
their count. Unacked events stay and are retried. Any failure (connection error,
non-2xx, bad body) backs off exponentially with jitter, 2 s, 4 s, ... capped at 300 s.

One poison event must not hold back every event behind it (review H-02, O-01, O-06), so some
failures are settled per event instead of retried for ever. An event the cloud can never take
(not encodable as JSON: a lone surrogate or a non-finite number; or holding U+0000, which the
cloud's database refuses) is marked rejected locally before sending. On HTTP 413 an event whose
encoded size is over OVERSIZE_EVENT_BYTES (intake keeps real events far below it) is marked
rejected; otherwise the batch is halved, and a single event of normal size that still gets 413
backs off like any other failure (a proxy that answers 413 to everything rejects nothing).
Other 4xx answers are not event-specific in the sync API (400 is a malformed envelope, 401/403
are auth) and keep backing off, so a cloud-side problem never rejects the whole outbox.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from rescue_field import downsync
from rescue_field.pipeline import DEFAULT_DEVICE_ID
from rescue_field.store import EventStore

BATCH_SIZE = 200
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 300.0
JITTER = 0.2  # +/- fraction of the delay
IDLE_INTERVAL_S = 10.0  # wait between cycles while online
REQUEST_TIMEOUT_S = 10.0
# An event this large is never a real request (intake caps bodies at 64 KB): on HTTP 413 it is
# marked rejected instead of retried. Smaller events that get 413 are retried in smaller batches.
OVERSIZE_EVENT_BYTES = 1_000_000
# placeholder until the first decision (or RULES_VERSION) names the engine build
DEFAULT_RULES_VERSION = "rules@0000000+bin:00000000"


def backoff_delay(failures: int, rng: random.Random | None = None) -> float:
    """Delay before retry number `failures` (1-based): 2, 4, 8 ... s, +/-20% jitter, max 300 s."""
    base = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** max(failures - 1, 0))
    r = rng or random
    return min(BACKOFF_CAP_S, base * (1 + r.uniform(-JITTER, JITTER)))


class SyncError(Exception):
    """A cycle failed; the worker backs off and retries. `status` is the HTTP status code when
    the cloud answered with one."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _has_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_nul(v) for v in value)
    return False


def _unsendable(event: dict[str, Any]) -> str | None:
    """Why the cloud could never take `event`, or None. httpx encodes strict UTF-8 JSON without
    NaN or Infinity; PostgreSQL jsonb refuses U+0000 (events stored before intake cleaned it)."""
    try:
        json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except ValueError as exc:  # UnicodeEncodeError is a ValueError too
        return f"local: not sendable as JSON ({type(exc).__name__}: {exc})"[:300]
    if _has_nul(event):
        return "local: contains U+0000, which the cloud's database refuses"
    return None


def _encoded_size(event: dict[str, Any]) -> int:
    return len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


@dataclass
class SyncResult:
    sent: int = 0
    acked: int = 0
    rejected: int = 0
    pulled: int = 0


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class SyncWorker:
    def __init__(
        self,
        store: EventStore,
        cloud_url: str,
        *,
        device_id: str,
        token: str,
        versions: Callable[[], tuple[str, str]],
        lock: AbstractContextManager[Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        rng: random.Random | None = None,
        idle_interval: float = IDLE_INTERVAL_S,
        batch_size: int = BATCH_SIZE,
        down_sync: bool = False,
        oversize_bytes: int = OVERSIZE_EVENT_BYTES,
    ) -> None:
        self.store = store
        self.device_id = device_id
        self.versions = versions
        self.lock = lock if lock is not None else nullcontext()
        self.rng = rng or random.Random()
        self.idle_interval = idle_interval
        self.batch_size = batch_size
        self.down_sync = down_sync
        self.oversize_bytes = oversize_bytes
        self.client = httpx.Client(
            base_url=cloud_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT_S,
            transport=transport,
        )
        self.failures = 0
        self.online = False
        self.last_sync_at: str | None = None
        self.last_error: str | None = None
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._wake = threading.Event()  # cuts a backoff/idle wait short (resume)
        self._thread: threading.Thread | None = None

    # ---- one cycle --------------------------------------------------------------------

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self.client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise SyncError(f"{path}: {exc}") from exc
        if resp.status_code != 200:
            raise SyncError(f"{path}: HTTP {resp.status_code}", resp.status_code)
        try:
            data = resp.json()
        except ValueError as exc:
            raise SyncError(f"{path}: response is not JSON") from exc
        if not isinstance(data, dict):
            raise SyncError(f"{path}: response is not an object")
        return data

    def backlog(self) -> int:
        with self.lock:
            return self.store.outbox_count()

    def rejected_count(self) -> int:
        with self.lock:
            return self.store.rejected_count()

    def ping(self) -> None:
        try:
            resp = self.client.get("/health")
        except httpx.HTTPError as exc:
            raise SyncError(f"/health: {exc}") from exc
        if resp.status_code != 200:
            raise SyncError(f"/health: HTTP {resp.status_code}")

    def _quarantine(self, rejections: list[tuple[str, str]], result: SyncResult) -> int:
        """Mark events rejected locally; they leave the outbox for good. Returns rows marked."""
        with self.lock:
            marked = self.store.mark_rejected(rejections, _utc_now())
        result.rejected += marked
        return marked

    def sync_once(self) -> SyncResult:
        """Ping, heartbeat, then drain the outbox. Raises SyncError on any failure."""
        self.ping()
        rules, model = self.versions()
        self._post(
            "/sync/heartbeat",
            {
                "device_id": self.device_id,
                "sent_at": _utc_now(),
                "outbox_backlog": self.backlog(),
                "rules_version": rules,
                "model_version": model,
            },
        )
        result = SyncResult()
        size = self.batch_size
        while not self._stop.is_set() and not self._paused.is_set():
            with self.lock:
                batch = self.store.pending_outbox(size)
            if not batch:
                break
            poison = [(e["event_id"], why) for e in batch if (why := _unsendable(e))]
            if poison:
                if not self._quarantine(poison, result):
                    break
                size = self.batch_size
                continue  # the next read skips them
            try:
                data = self._post("/sync/events", {"device_id": self.device_id, "events": batch})
            except SyncError as exc:
                if exc.status != 413:
                    raise
                too_large = [
                    (e["event_id"], f"local: {n} bytes, too large for the cloud (HTTP 413)")
                    for e in batch
                    if (n := _encoded_size(e)) > self.oversize_bytes
                ]
                if too_large:
                    if not self._quarantine(too_large, result):
                        break
                    size = self.batch_size
                    continue
                if len(batch) == 1:
                    raise  # a normal-sized event: not its fault, back off
                size = len(batch) // 2  # too large together: try fewer at a time
                continue
            sent_ids = {e["event_id"] for e in batch}
            acked = [i for i in data.get("acked") or [] if i in sent_ids]
            rejected = [
                (rej["event_id"], str(rej.get("error") or "rejected"))
                for rej in data.get("rejected") or []
                if isinstance(rej, dict) and rej.get("event_id") in sent_ids
            ]
            with self.lock:
                self.store.ack(acked)
                marked = self.store.mark_rejected(rejected, _utc_now())
            result.sent += len(batch)
            result.acked += len(acked)
            result.rejected += marked
            if not acked and not marked:
                break  # no progress this round (partial failure): back off rather than spin
        if self.down_sync and not self._stop.is_set() and not self._paused.is_set():
            try:
                result.pulled = len(
                    downsync.pull(self.client, self.store, self.lock, device_id=self.device_id)
                )
            except downsync.DownSyncError as exc:
                raise SyncError(str(exc)) from exc
        return result

    # ---- loop -------------------------------------------------------------------------

    def run_cycle(self) -> float:
        """One attempt; returns seconds to wait before the next."""
        try:
            self.sync_once()
        except Exception as exc:  # noqa: BLE001 - any failure must back off, never kill the worker
            self.failures += 1
            self.online = False
            name = "" if isinstance(exc, SyncError) else f"{type(exc).__name__}: "
            self.last_error = f"{name}{exc}"
            return backoff_delay(self.failures, self.rng)
        self.failures = 0
        self.online = True
        self.last_error = None
        self.last_sync_at = _utc_now()
        return self.idle_interval

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self.online = False
                self._stop.wait(0.2)
                continue
            delay = self.run_cycle()
            self._wake.wait(delay)
            self._wake.clear()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._loop, name="sync-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=REQUEST_TIMEOUT_S + 5)
            self._thread = None
        self.client.close()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        """Go back online and retry now, even if a long backoff wait is running."""
        self._paused.clear()
        self.failures = 0
        self._wake.set()

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "paused": self.paused,
            "online": self.online,
            "outbox_backlog": self.backlog(),
            "rejected": self.rejected_count(),
            "last_sync_at": self.last_sync_at,
            "last_error": self.last_error,
            "consecutive_failures": self.failures,
        }


def worker_from_env(
    store: EventStore,
    cloud_url: str,
    *,
    model_version: str,
    lock: AbstractContextManager[Any] | None = None,
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> SyncWorker:
    """Worker configured from the runtime env. The heartbeat reports the rules_version of the
    engine that made this device's most recent decision (the real build stamp, DECISIONS A2),
    falling back to `RULES_VERSION` (then a placeholder) before the first decision."""
    env = os.environ if env is None else env
    fallback_rules = env.get("RULES_VERSION") or DEFAULT_RULES_VERSION
    device_id = env.get("DEVICE_ID") or DEFAULT_DEVICE_ID
    store_lock = lock if lock is not None else nullcontext()

    def versions() -> tuple[str, str]:
        with store_lock:
            rules = store.latest_rules_version(device_id)
        return (rules or fallback_rules, model_version)

    return SyncWorker(
        store,
        cloud_url,
        device_id=device_id,
        token=env.get("DEVICE_TOKEN") or "dev-token-laptop-dev-01",
        versions=versions,
        lock=lock,
        transport=transport,
        down_sync=True,
    )


# ---- routes ----------------------------------------------------------------------------

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))


def _worker(request: Request) -> SyncWorker | None:
    return getattr(request.app.state, "sync_worker", None)


def extractor_status(request: Request) -> dict[str, Any]:
    """The `extractor` object of /sync/status: what the app chose, what it expected, and why."""
    app_state = request.app.state
    info = dict(getattr(app_state, "extractor_info", None) or {})
    for key in ("extractor", "model_version", "expected_model", "fallback_reason"):
        info.setdefault(key, None)
    calibrated = getattr(getattr(app_state, "extractor", None), "calibrated", None)
    info["calibrated"] = calibrated if isinstance(calibrated, bool) else None
    return info


@router.get("/sync/status")
async def sync_status(request: Request) -> Any:
    status = await _sync_status(request)
    status["extractor"] = extractor_status(request)
    return status


async def _sync_status(request: Request) -> dict[str, Any]:
    worker = _worker(request)
    if worker is None:
        store = getattr(request.app.state, "store", None)
        lock = getattr(request.app.state, "intake_lock", None) or nullcontext()

        def counts() -> dict[str, int]:
            if store is None:
                return {}
            with lock:
                return {"outbox_backlog": store.outbox_count(), "rejected": store.rejected_count()}

        counted = await run_in_threadpool(counts)
        return {"running": False, "paused": False, "online": False, **counted}
    return await run_in_threadpool(worker.status)


def _badge_ctx(request: Request) -> dict[str, Any]:
    worker = _worker(request)
    if worker is None:
        store = getattr(request.app.state, "store", None)
        lock = getattr(request.app.state, "intake_lock", None) or nullcontext()
        with lock:  # the store connection is shared across threads
            backlog = store.outbox_count() if store is not None else 0
        return {"state": "no-sync", "paused": False, "last_sync_at": None, "backlog": backlog}
    st = worker.status()
    online = st["online"] and not st["paused"]
    return {
        "state": "online" if online else "offline",
        "paused": st["paused"],
        "last_sync_at": st["last_sync_at"],
        "backlog": st["outbox_backlog"],
    }


@router.get("/sync/badge", response_class=HTMLResponse)
async def sync_badge(request: Request) -> HTMLResponse:
    """Live sync badge fragment (online/offline + outbox backlog) the board polls."""
    ctx = await run_in_threadpool(_badge_ctx, request)
    return _templates.TemplateResponse(request, "_sync_badge.html", ctx)


@router.post("/sync/offline-toggle")
async def offline_toggle(request: Request) -> Any:
    """Demo only (DEMO_MODE=1): pause/resume the running worker to simulate losing the link."""
    if os.environ.get("DEMO_MODE") != "1":
        return JSONResponse({"error": "not_found"}, status_code=404)
    worker = _worker(request)
    if worker is None:
        return JSONResponse({"error": "no_sync_worker", "detail": "CLOUD_URL is not set"}, 409)
    if worker.paused:
        worker.resume()
    else:
        worker.pause()
    return {"paused": worker.paused}
