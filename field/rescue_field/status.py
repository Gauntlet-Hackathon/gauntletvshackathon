"""POST /requests/{id}/status: assigned / rescued / escalated, forward only.

Order: (none) < assigned < escalated < rescued. A request's current status is the highest
status among its events; a move must go strictly up, so repeats and backward moves get 409.
Skipping ahead (e.g. none -> rescued) is allowed. Each accepted move appends one event.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from rescue_field.pipeline import PipelineConfig
from rescue_field.store import EventStore
from rescue_shared.contracts_io import validator_for
from rescue_shared.ulid import new_ulid

ORDER = ("assigned", "escalated", "rescued")
RANK = {s: i for i, s in enumerate(ORDER, 1)}
router = APIRouter()


def current_status(events: list[dict[str, Any]]) -> str | None:
    best = [e["type"] for e in events if e["type"] in RANK]
    return max(best, key=RANK.__getitem__, default=None)


class StatusError(Exception):
    def __init__(self, code: int, error: str, detail: str) -> None:
        self.code, self.error, self.detail = code, error, detail


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


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
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise StatusError(422, "invalid_input", "rescued_count must be an integer") from None
    if count < 0:
        raise StatusError(422, "invalid_input", "rescued_count must not be negative")
    return {"rescued_count": count, "note": note}


def apply_status(
    store: EventStore, request_id: str, data: dict[str, Any], config: PipelineConfig
) -> dict[str, Any]:
    status = data.get("status")
    if status not in RANK:
        raise StatusError(422, "invalid_input", f"status must be one of {', '.join(ORDER)}")
    events = [e for e in store.all_events() if e["request_id"] == request_id]
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


@router.post("/requests/{request_id}/status")
async def post_status(request_id: str, request: Request) -> Any:
    """JSON or form: status (required); crew (assigned), reason (escalated),
    rescued_count (rescued, optional), note (optional)."""
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    try:
        if ctype in ("application/x-www-form-urlencoded", "multipart/form-data"):
            data: Any = dict(await request.form())
        else:
            data = await request.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return JSONResponse(
            {"error": "invalid_input", "detail": "expected a JSON object or form"}, 422
        )
    state = request.app.state

    def work() -> dict[str, Any]:
        if state.store is None:
            raise RuntimeError("event store is not open (app lifespan not running)")
        with state.intake_lock:
            return apply_status(state.store, request_id, data, state.config)

    try:
        return await run_in_threadpool(work)
    except StatusError as exc:
        return JSONResponse({"error": exc.error, "detail": exc.detail}, exc.code)
