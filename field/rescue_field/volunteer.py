"""Volunteer queue (UI-2): GET / (and /app) shows Next, Waiting and Out now.

A view over `board.build_board_from_events`: the same snapshot and the same order, no ranking
logic of its own. Skipping a card is a client-side cursor and writes nothing.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from rescue_field import board as board_module
from rescue_field import words
from rescue_field.shell import _ago, shell_context, templates

router = APIRouter()


def _words_es() -> Any:
    try:
        return importlib.import_module("rescue_field.words_es")
    except ImportError:
        return None


def _spanish(text: str, lang: str | None) -> str | None:
    """The Spanish line for a question, when words_es (#303) is installed and has one."""
    mod = _words_es()
    fn = getattr(mod, "question_es", None) if mod else None
    return fn(text) if fn and lang == "es" else None


def _card(row: dict[str, Any], *, tier: str | None, provisional: bool, now=None) -> dict[str, Any]:
    e = row["extracted"]
    decision = row.get("tier_source", row)["decision"]
    q = row["decision"].get("follow_up_question")
    return {
        "request_id": row["request_id"],
        "badge": row["request_id"][-4:],
        "tier": tier,
        "pill": words.pill_text(tier, provisional),
        "provisional": provisional,
        "headline": words.headline(e),
        "address": (e.get("location") or {}).get("text") or "No address",
        "details": words.details(e),
        "contact": row.get("contact") or "No contact given",
        "channel": row.get("channel"),
        "age": _ago(row.get("received_at"), now),
        "why": words.rule_sentence(decision.get("rule_id")),
        "more": words.other_posts(len(row.get("duplicates", []))),
        "status": row.get("status"),
        "question": q if provisional else None,
        "question_es": _spanish(q, row.get("lang_hint")) if q and provisional else None,
        "escalated": row.get("status") == "escalated",
    }


def build_queue(board: dict[str, Any], now=None) -> dict[str, Any]:
    """Keys: next, waiting, out_now, check_first, set_aside (lists of cards).

    `next` lists every unassigned ranked card in order (escalated first, then the board's order);
    the page shows the first one and the client cursor moves through the rest.
    """
    unassigned: list[dict[str, Any]] = []
    out_now: list[dict[str, Any]] = []
    for t in board["tiers"]:
        for row in t["rows"]:
            card = _card(row, tier=row["tier"], provisional=False, now=now)
            if row["status"] == "assigned":
                out_now.append(card)
            elif row["status"] != "rescued":
                unassigned.append(card)
    # Stable sort: escalated rows first, otherwise the board's tier order.
    unassigned.sort(key=lambda c: not c["escalated"])
    check_first = []
    for r in board["follow_ups"]:
        if r.get("grouped_tier") or r["status"] == "rescued":
            continue  # already part of a ranked card
        prov = r.get("provisional_tier")
        card = _card(r, tier=None, provisional=True, now=now)
        card["maybe"] = words.pill_text(prov, True) if prov else None
        card["maybe_tier"] = prov
        check_first.append(card)
    set_aside = [
        {
            "request_id": r["request_id"],
            "badge": r["request_id"][-4:],
            "text": r["raw_text"],
            "age": _ago(r.get("received_at"), now),
        }
        for r in board["not_requests"]
    ]
    return {
        "next": unassigned,
        "waiting": unassigned[1:],
        "out_now": out_now,
        "check_first": check_first,
        "set_aside": set_aside,
    }


async def queue_page(request: Request) -> HTMLResponse:
    store = request.app.state.store
    if store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    lock = request.app.state.intake_lock

    def snapshot() -> list[dict[str, Any]]:
        with lock:
            return store.all_events()

    events = await run_in_threadpool(snapshot)

    def build() -> dict[str, Any]:
        return build_queue(board_module.build_board_from_events(events))

    ctx = await run_in_threadpool(build)
    ctx["recheck"] = any(
        getattr(r, "path", None) == "/requests/{request_id}/recheck" for r in request.app.routes
    )
    if request.headers.get("HX-Request"):
        return await run_in_threadpool(templates.TemplateResponse, request, "app/_queue.html", ctx)
    ctx.update(await run_in_threadpool(shell_context, request))
    ctx["mode"] = "queue"
    ctx["demo_mode"] = os.environ.get("DEMO_MODE") == "1"
    return await run_in_threadpool(templates.TemplateResponse, request, "app/queue.html", ctx)


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return await queue_page(request)
