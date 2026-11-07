"""ECC intake simulator: GET /intake (the page) and GET /intake/log.json (its log).

The page (port of `docs/design/communityrescue/intake-simulator.html`) adds requests through
the normal intake endpoints (`POST /requests`, `POST /requests/bulk`), so every add goes through
extract, decide, the event store and the sync outbox. The log is read back from the event store:
the most recent received requests, newest first (store order), with the words a volunteer sees
for the engine's decision once there is one. The header shows `$ECC_NAME` (default `ECC 1`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

DEFAULT_ECC_NAME = "ECC 1"
LOG_LIMIT = 200
# Contract value -> the words volunteers see (docs/design/communityrescue/README.md).
TIER_WORDS = {"P1": "Needs help now", "P2": "Needs help soon", "P3": "Can wait"}
OUTCOME_WORDS = {
    "abstain": "Check first",
    "not_a_request": "Set aside",
    "duplicate": "Same as an earlier request",
}

templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
router = APIRouter()


def result_words(decision: dict[str, Any] | None) -> str | None:
    """Volunteer words for an engine_output, or None before the request is decided."""
    if not decision:
        return None
    outcome = decision.get("outcome")
    if outcome == "prioritized":
        return TIER_WORDS.get(decision.get("priority_tier") or "", "Needs help")
    return OUTCOME_WORDS.get(outcome or "")


def intake_log(events: list[dict[str, Any]], limit: int = LOG_LIMIT) -> list[dict[str, Any]]:
    """The newest `limit` received requests (store order, newest first) with their result."""
    received: list[dict[str, Any]] = []
    decisions: dict[str, dict[str, Any]] = {}
    for ev in events:
        if ev["type"] == "received":
            received.append(ev)
        elif ev["type"] == "decided":
            decisions[ev["request_id"]] = ev["payload"]["engine_output"]
    out = []
    for ev in reversed(received[-limit:] if limit else []):
        payload = ev["payload"]
        decision = decisions.get(ev["request_id"])
        out.append(
            {
                "request_id": ev["request_id"],
                "received_at": ev["occurred_at"],
                "raw_text": payload.get("raw_text"),
                "channel": payload.get("channel"),
                "contact": payload.get("contact"),
                "lang_hint": payload.get("lang_hint"),
                "result": result_words(decision),
                "follow_up_question": (decision or {}).get("follow_up_question"),
            }
        )
    return out


@router.get("/intake", response_class=HTMLResponse)
async def intake_page(request: Request) -> HTMLResponse:
    ecc_name = os.environ.get("ECC_NAME", "").strip() or DEFAULT_ECC_NAME
    return templates.TemplateResponse(request, "intake.html", {"ecc_name": ecc_name})


@router.get("/intake/log.json")
async def intake_log_json(request: Request) -> dict[str, Any]:
    store = request.app.state.store
    if store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    lock = request.app.state.intake_lock

    def snapshot() -> list[dict[str, Any]]:
        with lock:
            return store.all_events()

    events = await run_in_threadpool(snapshot)
    requests = await run_in_threadpool(intake_log, events)
    return {"count": len(requests), "requests": requests}
