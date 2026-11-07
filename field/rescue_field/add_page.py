"""Add messages screen (UI-6, UC-01): GET /add.

Paste box, CSV upload and a single message. The page's script (static/add.js) sends pasted lines
to the existing POST /requests/bulk in chunks of CHUNK_LINES ("Sorted 12 of 50"), a CSV as one
upload and a single message to POST /requests, then shows one count per group. `group_of` and
`summarize` are the grouping the script mirrors (GROUP_KEYS is handed to it from here, so the
labels and order live in one place).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from rescue_field.shell import shell_context, templates

router = APIRouter()

CHUNK_LINES = 10
# (key, label) in display order.
GROUPS: tuple[tuple[str, str], ...] = (
    ("P1", "Needs help now"),
    ("P2", "Needs help soon"),
    ("P3", "Can wait"),
    ("abstain", "Check first"),
    ("not_a_request", "Set aside"),
    ("duplicate", "Folded into another card"),
)
GROUP_KEYS = tuple(k for k, _ in GROUPS)


def group_of(decision: Mapping[str, Any]) -> str:
    """The summary group for one engine output (the `decision` of a bulk result row)."""
    outcome = decision.get("outcome")
    if outcome in ("abstain", "not_a_request", "duplicate"):
        return str(outcome)
    if outcome == "prioritized" and decision.get("priority_tier") in ("P1", "P2", "P3"):
        return str(decision["priority_tier"])
    raise ValueError(f"unexpected engine output: outcome={outcome!r}")


def summarize(decisions: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Count per group, every group present (zero included), in display order."""
    counts = dict.fromkeys(GROUP_KEYS, 0)
    for d in decisions:
        counts[group_of(d)] += 1
    return counts


def limits() -> dict[str, Any]:
    """The single-message limits and channels from api.py (imported here: api imports us)."""
    from rescue_field import api

    return {
        "max_text": api.MAX_TEXT_CHARS,
        "max_contact": api.MAX_CONTACT_CHARS,
        "max_rows": api.MAX_BULK_ROWS,
        "channels": list(api.CHANNELS),
        "single_channel": api.SINGLE_DEFAULT_CHANNEL,
    }


@router.get("/add", response_class=HTMLResponse)
async def add_page(request: Request) -> HTMLResponse:
    ctx = await run_in_threadpool(shell_context, request)
    ctx["mode"] = "queue"
    ctx["limits"] = lim = limits()
    ctx["config_json"] = json.dumps(
        {"chunk": CHUNK_LINES, "groups": [list(g) for g in GROUPS], **lim}
    )
    return templates.TemplateResponse(request, "add.html", ctx)
