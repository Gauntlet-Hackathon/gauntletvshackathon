"""POST /requests/{id}/status: assigned / rescued / escalated, forward only.

Order (contracts/logic.md): (none) < assigned < escalated < rescued, with `closed_duplicate`
ranked equal to `rescued`. A move must go strictly up, with one exception (#306, #309):
`escalated -> assigned` is also a move, so a second crew can be sent after "Couldn't get there".
A request's current status is its status events folded in order with that rule
(`current_status`); repeats, backward moves and anything after `rescued` / `closed_duplicate`
get 409. Skipping ahead (e.g. none -> rescued) is allowed. Each accepted move appends one event.

`rescued` takes optional `outcome` (`all` / `some` / `nobody`) and `destination` (#309). Rescuing
the first request of a duplicate group (the board card) also appends one `closed_duplicate`
event per other open post folded into that card, in the same write.
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
# Every status event type with its rank; `closed_duplicate` is terminal like `rescued`.
STATUS_RANK = RANK | {"closed_duplicate": RANK["rescued"]}
OUTCOMES = ("all", "some", "nobody")
MAX_DESTINATION = 200  # contracts/payloads/rescued.schema.json
# Far above any real rescue, far below the cloud's int4 columns (review L-05).
MAX_RESCUED_COUNT = 100_000
router = APIRouter()


def moves(now: str | None, new: str) -> bool:
    """True when a `new` status event changes a request whose status is `now`."""
    if new not in STATUS_RANK:
        return False
    if now is None:
        return True
    return STATUS_RANK[new] > STATUS_RANK[now] or (now == "escalated" and new == "assigned")


def current_status(events: list[dict[str, Any]]) -> str | None:
    """The status after folding the status events in order (`closed_duplicate` included)."""
    now: str | None = None
    for e in events:
        if moves(now, e["type"]):
            now = e["type"]
    return now


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
    out: dict[str, Any] = {
        "rescued_count": None if count in (None, "") else _count(count),
        "note": note,
    }
    outcome = data.get("outcome")
    if outcome not in (None, ""):
        if outcome not in OUTCOMES:
            raise StatusError(422, "invalid_input", f"outcome must be one of {', '.join(OUTCOMES)}")
        out["outcome"] = outcome
    destination = _text(data.get("destination"))
    if destination is not None:
        if len(destination) > MAX_DESTINATION:
            detail = f"destination must be at most {MAX_DESTINATION} characters"
            raise StatusError(422, "invalid_input", detail)
        out["destination"] = destination
    return out


def folded_posts(store: EventStore, request_id: str) -> list[str]:
    """The other open requests folded into the card headed by `request_id` (the earliest
    request of its duplicate group, as the board groups them), oldest first. Empty when
    `request_id` is not a group's first request."""
    from rescue_field.board import _requests, _root_finder  # board imports this module

    recs = _requests(store.all_events())
    if request_id not in recs:
        return []
    find = _root_finder(recs)
    if find(request_id) != request_id:
        return []
    others = [
        r for rid, r in recs.items()
        if rid != request_id and find(rid) == request_id and moves(r["status"], "closed_duplicate")
    ]
    others.sort(key=lambda r: (r["received_at"], r["request_id"]))
    return [r["request_id"] for r in others]


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
    if not moves(now, status):
        raise StatusError(409, "invalid_transition", f"cannot move {now} -> {status}")
    at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def make(rid: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": new_ulid(),
            "request_id": rid,
            "type": kind,
            "device_id": config.device_id,
            "occurred_at": at,
            "rules_version": decided[-1]["rules_version"],
            "model_version": decided[-1]["model_version"],
            "payload": payload,
        }

    event = make(request_id, status, _payload(status, data))
    batch = [event]
    if status == "rescued":
        payload = {"duplicate_of": request_id, "note": None}
        batch += [make(rid, "closed_duplicate", payload) for rid in folded_posts(store, request_id)]
    validator = validator_for("event.schema.json")
    for ev in batch:
        errors = list(validator.iter_errors(ev))
        if errors:
            raise StatusError(422, "invalid_input", errors[0].message)
    store.append_events(batch)
    result = {"request_id": request_id, "status": status, "event_id": event["event_id"]}
    if len(batch) > 1:
        result["closed_duplicates"] = [ev["request_id"] for ev in batch[1:]]
    return result


@router.post("/requests/{request_id}/status")
async def post_status(request_id: str, request: Request) -> Any:
    """JSON or form: status (required); crew (assigned), reason (escalated),
    rescued_count, outcome and destination (rescued, optional), note (optional)."""
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
