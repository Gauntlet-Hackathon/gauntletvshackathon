"""Volunteer app queue mode (UI-2): GET / shows Next, Waiting and Out now.

A view model over `board.build_board_from_events`: same snapshot, same order, no ranking of its
own. Next is the first escalated tier row ("Couldn't get there"), else the first tier row with no
status, so with no escalations it is always the first unassigned tier row of `/board`. "Skip for
now" is a client-side cursor and writes nothing. Waiting lists the other Next candidates, then
the Check first cards (follow-ups), then the Set aside items (not a request), collapsed. Out now
lists assigned rows. Rescued rows leave the queue.

A follow-up that /board keeps under a tier row (`grouped_tier`, review O-08) is a repeat of that
row, so it gets no card of its own: its question is shown on the tier card instead, with every
question /board puts on that row (`row["questions"]`), word for word.

The page never shows rule IDs, rules or model versions or confidences (words.py turns them into
plain sentences). `GET /` with an `HX-Request` header returns only the queue fragment; the page
refreshes it every few seconds.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from rescue_field import words
from rescue_field.board import _label, build_board_from_events
from rescue_field.shell import _ago, shell_context

templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
router = APIRouter()
REFRESH_SECONDS = 5


def _spanish(text: str) -> str | None:
    """The Spanish line for a follow-up question, when words_es.py (#303) provides one."""
    try:
        es = importlib.import_module("rescue_field.words_es")
    except ImportError:
        return None
    fn = getattr(es, "question_es", None)
    if callable(fn):
        return fn(text)
    table = getattr(es, "QUESTIONS", None)
    return table.get(text) if isinstance(table, dict) else None


def _question(text: str, label: str | None, lang: str | None) -> dict[str, Any]:
    return {
        "text": text,
        "label": words.QUESTION_LABELS.get(label or "", label),
        "es": _spanish(text) if lang == "es" else None,
    }


def _base(rec: dict[str, Any], kind: str, now: Any = None) -> dict[str, Any]:
    e = rec["extracted"]
    return {
        "id": rec["request_id"],
        "incident": rec["request_id"][-4:],
        "kind": kind,
        "headline": words.headline(e),
        "address": words.address(e),
        "details": words.details(e),
        "contact": words.contact_line(
            rec.get("channel"), rec.get("contact"), _ago(rec.get("received_at"), now)
        ),
        "raw_text": rec.get("raw_text"),
        "other_posts": words.other_posts(len(rec.get("duplicates") or [])),
        "questions": [],
        "provisional": None,
        "escalated": False,
    }


def _tier_card(row: dict[str, Any], now: Any = None) -> dict[str, Any]:
    card = _base(row, "tier", now)
    lang = row.get("lang_hint")
    card.update(
        tier=row["tier"],
        pill=words.tier_pill(row["tier"]),
        why=words.why(row["tier_source"]["decision"].get("rule_id")),
        escalated=row["status"] == "escalated",
        status=row["status"],
        questions=[_question(q["text"], q.get("label"), lang) for q in row.get("questions", [])],
    )
    return card


def _check_card(rec: dict[str, Any], now: Any = None) -> dict[str, Any]:
    card = _base(rec, "check", now)
    d = rec["decision"]
    q = d.get("follow_up_question")
    card.update(
        pill=words.CHECK_FIRST,
        provisional=words.provisional_pill(rec.get("provisional_tier")),
        why=words.why(d.get("rule_id")),
        questions=[_question(q, None, rec.get("lang_hint"))] if q else [],
    )
    return card


def _aside_card(rec: dict[str, Any], now: Any = None) -> dict[str, Any]:
    card = _base(rec, "aside", now)
    card.update(pill=words.SET_ASIDE, why=words.why(rec["decision"].get("rule_id")))
    return card


def build_queue(board: dict[str, Any], now: Any = None) -> dict[str, Any]:
    """{"next": [...], "check_first": [...], "set_aside": [...], "out_now": [...]}.

    `next` holds every Next candidate in order (escalated first, then unassigned, board order);
    the page shows the first one not skipped and lists the rest under Waiting."""
    rows = [r for t in board["tiers"] for r in t["rows"]]
    cards = {r["request_id"]: _tier_card(r, now) for r in rows}
    group = {
        m["request_id"]: cards[r["request_id"]] for r in rows for m in [r, *r["duplicates"]]
    }
    check_first = []
    for f in board["follow_ups"]:
        if f.get("grouped_tier"):
            # Already part of a tier card: show its question there, never a card of its own.
            card = group.get(f["request_id"])
            q = f["decision"].get("follow_up_question")
            if card is not None and q and all(x["text"] != q for x in card["questions"]):
                card["questions"].append(_question(q, _label(f["decision"]), f.get("lang_hint")))
            continue
        check_first.append(_check_card(f, now))
    escalated = [cards[r["request_id"]] for r in rows if r["status"] == "escalated"]
    unassigned = [cards[r["request_id"]] for r in rows if r["status"] is None]
    return {
        "next": escalated + unassigned,
        "check_first": check_first,
        "set_aside": [_aside_card(r, now) for r in board["not_requests"]],
        "out_now": [cards[r["request_id"]] for r in rows if r["status"] == "assigned"],
    }


def _has_recheck(request: Request) -> bool:
    """True when POST /requests/{id}/recheck (#302) is registered."""
    for r in request.app.routes:
        methods = getattr(r, "methods", None) or ()
        if getattr(r, "path", "").endswith("/recheck") and "POST" in methods:
            return True
    return False


@router.get("/", response_class=HTMLResponse)
async def queue(request: Request) -> HTMLResponse:
    store = request.app.state.store
    if store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    lock = request.app.state.intake_lock

    def snapshot() -> list[dict[str, Any]]:
        with lock:  # one SELECT; building and rendering happen outside the lock
            return store.all_events()

    events = await run_in_threadpool(snapshot)
    board = await run_in_threadpool(build_board_from_events, events)
    ctx: dict[str, Any] = {"queue": build_queue(board), "recheck": _has_recheck(request)}
    ctx["refresh_seconds"] = REFRESH_SECONDS
    if request.headers.get("HX-Request"):
        name = "app/_queue.html"
    else:
        ctx |= await run_in_threadpool(shell_context, request)
        ctx["mode"] = "queue"
        name = "app/queue.html"
    return await run_in_threadpool(templates.TemplateResponse, request, name, ctx)
