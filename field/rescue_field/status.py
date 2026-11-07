"""POST /requests/{id}/status: assigned / rescued / escalated, forward only.
POST /requests/{id}/responder-status: a boat's OSM step (#310), never a status.

Order: (none) < assigned < escalated < rescued. A request's current status is the highest
status among its events; a move must go strictly up, so repeats and backward moves get 409.
Skipping ahead (e.g. none -> rescued) is allowed. Each accepted move appends one event.

`responder_status` events (contracts/payloads/responder_status.schema.json: crew, status in
sent / on_the_way / there / done, note = the radio line) are not in that order: they never
change the request's status. A tap that repeats the crew's latest step on that request adds
nothing. `latest_responder_status` folds them (local and down-synced) for Out now.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from rescue_field.pipeline import PipelineConfig
from rescue_field.sanitize import clean_text
from rescue_field.store import EventStore
from rescue_shared.contracts_io import validator_for
from rescue_shared.ulid import new_ulid

ORDER = ("assigned", "escalated", "rescued")
RANK = {s: i for i, s in enumerate(ORDER, 1)}
# Far above any real rescue, far below the cloud's int4 columns (review L-05).
MAX_RESCUED_COUNT = 100_000
RESPONDER_STEPS = ("sent", "on_the_way", "there", "done")
RESPONDER_LABELS = {"sent": "Sent", "on_the_way": "On the way", "there": "There", "done": "Done"}
router = APIRouter()


def current_status(events: list[dict[str, Any]]) -> str | None:
    best = [e["type"] for e in events if e["type"] in RANK]
    return max(best, key=RANK.__getitem__, default=None)


class StatusError(Exception):
    def __init__(self, code: int, error: str, detail: str) -> None:
        self.code, self.error, self.detail = code, error, detail


def _text(value: Any) -> str | None:
    return clean_text(value).strip() or None if isinstance(value, str) else None


def _count(value: Any) -> int:
    """A whole, non-negative, bounded number: an int, an integral float (JS `Number()` sends
    2.0 as 2, but 1e30 is a float too) or digits from a form. Never a bool, a fraction or
    a non-finite value (review L-05: 1e999 was a 500, 1e30 and true were stored)."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    elif isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            value = None
    if isinstance(value, bool) or not isinstance(value, int):
        raise StatusError(422, "invalid_input", "rescued_count must be an integer")
    if value < 0:
        raise StatusError(422, "invalid_input", "rescued_count must not be negative")
    if value > MAX_RESCUED_COUNT:
        detail = f"rescued_count must be at most {MAX_RESCUED_COUNT}"
        raise StatusError(422, "invalid_input", detail)
    return value


def _payload(status: str, data: dict[str, Any]) -> dict[str, Any]:
    note = _text(data.get("note"))
    if status == "assigned":
        crew = _text(data.get("crew"))
        if crew is None:
            raise StatusError(422, "invalid_input", "assigned needs a non-empty crew")
        return {"crew": crew, "note": note}
    if status == "escalated":
        reason = _text(data.get("reason"))
        if reason is None:
            raise StatusError(422, "invalid_input", "escalated needs a non-empty reason")
        return {"reason": reason, "note": note}
    count = data.get("rescued_count")
    if count in (None, ""):
        return {"rescued_count": None, "note": note}
    return {"rescued_count": _count(count), "note": note}


def apply_status(
    store: EventStore, request_id: str, data: dict[str, Any], config: PipelineConfig
) -> dict[str, Any]:
    status = data.get("status")
    if status not in RANK:
        raise StatusError(422, "invalid_input", f"status must be one of {', '.join(ORDER)}")
    events = store.request_events(request_id)  # indexed, not a scan of the whole log
    decided = [e for e in events if e["type"] == "decided"]
    if not decided:
        raise StatusError(404, "not_found", f"unknown request {request_id}")
    now = current_status(events)
    if now is not None and RANK[status] <= RANK[now]:
        raise StatusError(409, "invalid_transition", f"cannot move {now} -> {status}")
    event = {
        "event_id": new_ulid(),
        "request_id": request_id,
        "type": status,
        "device_id": config.device_id,
        "occurred_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rules_version": decided[-1]["rules_version"],
        "model_version": decided[-1]["model_version"],
        "payload": _payload(status, data),
    }
    errors = list(validator_for("event.schema.json").iter_errors(event))
    if errors:
        raise StatusError(422, "invalid_input", errors[0].message)
    store.append_events([event])
    return {"request_id": request_id, "status": status, "event_id": event["event_id"]}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def crew_key(name: Any) -> str:
    """Crew names match ignoring case and repeated spaces."""
    return " ".join(name.split()).casefold() if isinstance(name, str) else ""


def _decided(events: list[dict[str, Any]], request_id: str) -> dict[str, Any]:
    decided = [e for e in events if e["type"] == "decided"]
    if not decided:
        raise StatusError(404, "not_found", f"unknown request {request_id}")
    return decided[-1]


def _validated(event: dict[str, Any]) -> dict[str, Any]:
    errors = list(validator_for("event.schema.json").iter_errors(event))
    if errors:
        raise StatusError(422, "invalid_input", errors[0].message)
    return event


def latest_responder_status(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """request_id -> the latest `responder_status` payload (plus `occurred_at`) of the crew
    currently assigned to it (its latest `assigned` event). Events from other laptops arrive in
    cloud order, so the latest is the one with the greatest `occurred_at` (store order breaks
    ties). A status from a crew the request is no longer assigned to is ignored."""
    crew: dict[str, str] = {}
    latest: dict[str, dict[str, Any]] = {}
    for ev in events:
        payload = ev.get("payload") or {}
        if ev["type"] == "assigned":
            crew[ev["request_id"]] = crew_key(payload.get("crew"))
        elif ev["type"] == "responder_status":
            prev = latest.get(ev["request_id"])
            if prev is None or ev["occurred_at"] >= prev["occurred_at"]:
                latest[ev["request_id"]] = {**payload, "occurred_at": ev["occurred_at"]}
    return {
        rid: st
        for rid, st in latest.items()
        if crew.get(rid) and crew_key(st.get("crew")) == crew[rid]
    }


def apply_responder_status(
    store: EventStore, request_id: str, data: dict[str, Any], config: PipelineConfig
) -> dict[str, Any]:
    """Append one `responder_status` event, unless the crew's latest step on this request is
    already `status` (a repeated tap): then nothing is written and `created` is False."""
    status = data.get("status")
    if status not in RESPONDER_STEPS:
        detail = f"status must be one of {', '.join(RESPONDER_STEPS)}"
        raise StatusError(422, "invalid_input", detail)
    crew = _text(data.get("crew"))
    if crew is None:
        raise StatusError(422, "invalid_input", "responder status needs a non-empty crew")
    events = store.request_events(request_id)
    decided = _decided(events, request_id)
    last = None  # the crew's latest step here: greatest occurred_at, store order breaks ties
    for e in events:
        if e["type"] == "responder_status" and crew_key(e["payload"].get("crew")) == crew_key(crew):
            if last is None or e["occurred_at"] >= last["occurred_at"]:
                last = e
    if last is not None and last["payload"]["status"] == status:
        return {
            "request_id": request_id,
            "status": status,
            "event_id": last["event_id"],
            "created": False,
        }
    event = _validated(
        {
            "event_id": new_ulid(),
            "request_id": request_id,
            "type": "responder_status",
            "device_id": config.device_id,
            "occurred_at": _now(),
            "rules_version": decided["rules_version"],
            "model_version": decided["model_version"],
            "payload": {"crew": crew, "status": status, "note": _text(data.get("note"))},
        }
    )
    store.append_events([event])
    return {
        "request_id": request_id,
        "status": status,
        "event_id": event["event_id"],
        "created": True,
    }


async def _body(request: Request) -> Any:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    try:
        if ctype in ("application/x-www-form-urlencoded", "multipart/form-data"):
            return dict(await request.form())
        return await request.json()
    except ValueError:
        return None


async def _run(request: Request, request_id: str, data: Any, apply: Any) -> Any:
    if not isinstance(data, dict):
        return JSONResponse(
            {"error": "invalid_input", "detail": "expected a JSON object or form"}, 422
        )
    state = request.app.state

    def work() -> dict[str, Any]:
        if state.store is None:
            raise RuntimeError("event store is not open (app lifespan not running)")
        with state.intake_lock:
            return apply(state.store, request_id, data, state.config)

    try:
        return await run_in_threadpool(work)
    except StatusError as exc:
        return JSONResponse({"error": exc.error, "detail": exc.detail}, exc.code)


@router.post("/requests/{request_id}/responder-status")
async def post_responder_status(request_id: str, request: Request) -> Any:
    """JSON or form: crew, status (sent / on_the_way / there / done), note (the radio line).
    Queued in the outbox like every local event, so it works offline."""
    return await _run(request, request_id, await _body(request), apply_responder_status)


@router.post("/requests/{request_id}/status")
async def post_status(request_id: str, request: Request) -> Any:
    """JSON or form: status (required); crew (assigned), reason (escalated),
    rescued_count (rescued, optional), note (optional)."""
    return await _run(request, request_id, await _body(request), apply_status)
