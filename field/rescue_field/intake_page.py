"""ECC intake simulator (UI-7): GET /intake and GET /intake/log.json.

A port of `docs/design/communityrescue/intake-simulator.html`. Every add on the page goes through
the normal intake endpoints on the same origin (`POST /requests`, `POST /requests/bulk`), so the
request is extracted, decided, stored and queued for sync like any other. The log on the page is
read back from the field database (`log.json`), never kept in the browser: it is append-only like
the event store.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from rescue_field.add_page import GROUPS, group_of, limits
from rescue_field.shell import shell_config, templates

router = APIRouter()

LOG_LIMIT = 200
_WORDS = dict(GROUPS)


def result_words(decision: Mapping[str, Any] | None) -> str | None:
    """The volunteer words for one engine output ("Needs help now", "Check first", ...)."""
    if not decision:
        return None
    try:
        return _WORDS[group_of(decision)]
    except ValueError:
        return None


def intake_log(events: Iterable[Mapping[str, Any]], limit: int = LOG_LIMIT) -> list[dict[str, Any]]:
    """The most recent `received` requests (store order, newest first), each with the volunteer
    words of its latest `decided` event once there is one."""
    received: list[Mapping[str, Any]] = []
    decided: dict[str, Mapping[str, Any]] = {}
    for ev in events:
        if ev.get("type") == "received":
            received.append(ev)
        elif ev.get("type") == "decided":
            decided[ev["request_id"]] = (ev.get("payload") or {}).get("engine_output") or {}
    out = []
    for ev in reversed(received[-limit:] if limit else []):
        payload = ev.get("payload") or {}
        rid = ev["request_id"]
        decision = decided.get(rid)
        out.append(
            {
                "request_id": rid,
                "incident": payload.get("incident") or rid[-4:],
                "received_at": ev.get("occurred_at"),
                "raw_text": payload.get("raw_text"),
                "channel": payload.get("channel"),
                "contact": payload.get("contact"),
                "lang_hint": payload.get("lang_hint"),
                "result": result_words(decision),
            }
        )
    return out


@router.get("/intake/log.json")
async def intake_log_json(request: Request) -> dict[str, Any]:
    store = request.app.state.store
    events = await run_in_threadpool(store.all_events) if store is not None else []
    return {"requests": intake_log(events)}


@router.get("/intake", response_class=HTMLResponse)
async def intake_page(request: Request) -> HTMLResponse:
    lim = limits()
    cfg = {
        "max_text": lim["max_text"],
        "max_contact": lim["max_contact"],
        "max_rows": lim["max_rows"],
        "channels": lim["channels"],
        "single_channel": lim["single_channel"],
        "bulk_channel": "csv",
        "log_limit": LOG_LIMIT,
    }
    ctx = {
        "ecc_name": shell_config()["ecc_name"],
        "limits": lim,
        # "</" never appears in a JSON value here, but escape it anyway for the <script> tag.
        "config_json": json.dumps(cfg).replace("</", "<\\/"),
    }
    return templates.TemplateResponse(request, "intake.html", ctx)
