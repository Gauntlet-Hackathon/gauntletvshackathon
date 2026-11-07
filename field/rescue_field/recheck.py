"""Check again (UI-9, UC-13): re-run the engine on a stored decision and compare.

`POST /requests/{request_id}/recheck` takes the request's latest `decided` event, sends its
stored `payload.engine_input` through the app's engine client (the same `ENGINE_CMD` intake
uses) and compares the decision fields. It writes no event: it only answers "would this laptop
decide the same way now?". When the engine's rules version differs from the one the decision
was made with, it says so instead of comparing outputs of two different rule sets.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from rescue_field import engine_client

FIELDS = (
    "outcome",
    "priority_tier",
    "provisional_tier",
    "rule_id",
    "duplicate_of",
    "follow_up_question",
)
LABELS = {
    "outcome": "Outcome",
    "priority_tier": "Priority tier",
    "provisional_tier": "Provisional tier",
    "rule_id": "Rule",
    "duplicate_of": "Duplicate of",
    "follow_up_question": "Follow-up question",
}
VERSION_MISMATCH = "This laptop has a different rules version"

router = APIRouter()


def _pick(output: dict[str, Any]) -> dict[str, Any]:
    return {f: output.get(f) for f in FIELDS}


def differences(stored: dict[str, Any], now: dict[str, Any]) -> list[str]:
    """Plain-words lines, one per field that changed, e.g. "Priority tier: P2, now P1"."""
    return [
        f"{LABELS[f]}: {stored[f] if stored[f] is not None else 'none'}, "
        f"now {now[f] if now[f] is not None else 'none'}"
        for f in FIELDS
        if stored[f] != now[f]
    ]


def recheck(events: list[dict[str, Any]], decide_batch) -> dict[str, Any] | None:
    """The recheck answer for one request's events, or None when it has no decision."""
    decided = [ev for ev in events if ev["type"] == "decided"]
    if not decided:
        return None
    event = decided[-1]
    stored_output = event["payload"]["engine_output"]
    stored_version = event.get("rules_version") or stored_output.get("rules_version")
    [now_output] = decide_batch([event["payload"]["engine_input"]])
    stored, now = _pick(stored_output), _pick(now_output)
    base = {
        "stored": stored,
        "now": now,
        "stored_rules_version": stored_version,
        "now_rules_version": now_output.get("rules_version"),
    }
    if now_output.get("rules_version") != stored_version:
        return {"same": None, "detail": VERSION_MISMATCH, **base}
    diffs = differences(stored, now)
    return {"same": not diffs, "differences": diffs, **base}


@router.post("/requests/{request_id}/recheck")
async def recheck_request(request_id: str, request: Request) -> Any:
    state = request.app.state
    if state.store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")

    def work() -> dict[str, Any] | None:
        events = state.store.request_events(request_id)  # indexed read, no intake lock
        return recheck(events, state.decide_batch)

    try:
        result = await run_in_threadpool(work)
    except engine_client.EngineError as exc:
        return JSONResponse({"error": "engine_failed", "detail": str(exc)}, status_code=502)
    if result is None:
        return JSONResponse(
            {"error": "not_found", "detail": f"no decision for request {request_id}"},
            status_code=404,
        )
    return result
