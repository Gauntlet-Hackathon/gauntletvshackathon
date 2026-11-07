"""Volunteer app, Queue mode (UI-2): GET / shows Next, Waiting and Out now.

A view model over `board.build_board_from_events`: the same snapshot in the same order, no
ranking of its own. The technical board lives at `GET /board`.

- **Next**: the first tier row (P1, then P2, then P3, in board order) with no status. Rows a
  crew could not reach (`escalated`) go first, labelled "Couldn't get there". "Skip for now" is a
  client-side cursor over this list and writes nothing.
- **Waiting**: the other open tier rows, then Check first (the engine's abstains, question word
  for word, no Send button), then Set aside (not a request), collapsed.
- **Out now**: rows with a crew assigned, each with its boat's latest responder status ("On the
  way · 4 min ago", #310), from this laptop or another one via down-sync.

Duplicates never get their own card: they fold into their group's card as "+N other posts". An
abstain a duplicate lifted onto a tier (review O-08) is shown on that tier card, with the
engine's question and its label, instead of a second Check first card. No rule ID, rules or
model version or confidence number reaches the HTML (`words.py` turns them into plain words).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from rescue_field import words
from rescue_field.board import build_board_from_events, templates
from rescue_field.shell import _ago, shell_context
from rescue_field.status import RESPONDER_LABELS, latest_responder_status
from rescue_field.words_es import spanish_line

router = APIRouter()
OPEN = (None, "escalated")  # statuses that still need a crew
REFRESH_SECONDS = 5
_RECHECK = re.compile(r"^/requests/\{[^}]+\}/recheck$")


def _base(rec: dict[str, Any], kind: str) -> dict[str, Any]:
    e = rec["extracted"]
    return {
        "request_id": rec["request_id"],
        "kind": kind,
        "incident": words.incident(rec["request_id"]),
        "headline": words.headline(e),
        "address": (e.get("location") or {}).get("text"),
        "details": words.details(e),
        "contact": words.contact_line(rec),
        "other_posts": words.other_posts(len(rec.get("duplicates") or [])),
        "questions": [],
    }


def _question(rec: dict[str, Any], text: str, label: str | None) -> dict[str, Any]:
    """The engine's question verbatim, with the Spanish line for a Spanish request (UI-10)."""
    return {"text": text, "label": label, "es": spanish_line(rec, text)}


def tier_card(row: dict[str, Any]) -> dict[str, Any]:
    """A ranked board row: tier pill, why sentence, and any follow-up question the board shows
    on that row (an abstained root lifted by a duplicate, or an R9 question), verbatim."""
    card = _base(row, "tier")
    card["pill"] = words.tier_pill(row["tier"])
    card["status"] = row["status"]
    card["escalated"] = row["status"] == "escalated"
    card["why"] = words.sentence_for(row["tier_source"]["decision"].get("rule_id"))
    card["questions"] = [_question(row, q["text"], q["label"]) for q in row.get("questions") or []]
    return card


def check_card(rec: dict[str, Any]) -> dict[str, Any]:
    d = rec["decision"]
    card = _base(rec, "check")
    card["pill"] = {"text": words.CHECK_FIRST, "cls": "pill-check"}
    card["maybe"] = words.maybe_pill(rec.get("provisional_tier"))
    card["why"] = words.sentence_for(d.get("rule_id"))
    if d.get("follow_up_question"):
        card["questions"] = [_question(rec, d["follow_up_question"], None)]
    return card


def aside_card(rec: dict[str, Any]) -> dict[str, Any]:
    card = _base(rec, "aside")
    card["pill"] = {"text": words.SET_ASIDE, "cls": "pill-aside"}
    card["why"] = words.sentence_for(rec["decision"].get("rule_id"))
    return card


def responder_line(st: dict[str, Any] | None, now: datetime | None = None) -> dict[str, str] | None:
    """{"crew": ..., "text": "On the way · 4 min ago"} for a `latest_responder_status` entry."""
    if not st:
        return None
    parts = [RESPONDER_LABELS.get(st.get("status"), "")]
    ago = _ago(st.get("occurred_at"), now)
    if ago:
        parts.append(ago)
    return {"crew": st.get("crew") or "", "text": " · ".join(p for p in parts if p)}


def out_card(row: dict[str, Any], responder: dict[str, Any] | None) -> dict[str, Any]:
    card = tier_card(row)
    card["responder"] = responder_line(responder)
    return card


def build_queue(
    board: dict[str, Any], responders: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """{"next": card|None, "queue": [cards], "waiting": [...], "check_first": [...],
    "set_aside": [...], "out_now": [...]} from a `build_board` result. `responders` is
    `status.latest_responder_status` of the same snapshot (Out now's status line)."""
    responders = responders or {}
    rows = [r for t in board["tiers"] for r in t["rows"]]
    escalated = [r for r in rows if r["status"] == "escalated"]
    fresh = [r for r in rows if r["status"] is None]
    queue = [tier_card(r) for r in escalated + fresh]
    check_first = [
        check_card(f)
        for f in board["follow_ups"]
        # A grouped abstain is already on its tier card (with its question): never a repeat.
        if not f.get("grouped_tier") and f.get("status") != "rescued"
    ]
    return {
        "next": queue[0] if queue else None,
        "queue": queue,
        "waiting": queue[1:],
        "check_first": check_first,
        "set_aside": [aside_card(r) for r in board["not_requests"]],
        "out_now": [
            out_card(r, responders.get(r["request_id"])) for r in rows if r["status"] == "assigned"
        ],
    }


def _has_recheck(request: Request) -> bool:
    return any(_RECHECK.match(getattr(r, "path", "")) for r in request.app.routes)


@router.get("/", response_class=HTMLResponse)
async def queue_page(request: Request) -> HTMLResponse:
    store = request.app.state.store
    if store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    lock = request.app.state.intake_lock

    def snapshot() -> list[dict[str, Any]]:
        with lock:  # one SELECT; building and rendering happen outside the lock
            return store.all_events()

    events = await run_in_threadpool(snapshot)

    def build() -> dict[str, Any]:
        return build_queue(build_board_from_events(events), latest_responder_status(events))

    ctx = await run_in_threadpool(build)
    ctx["recheck"] = _has_recheck(request)
    ctx["refresh_seconds"] = REFRESH_SECONDS
    ctx["couldnt"] = words.COULDNT_GET_THERE
    if request.headers.get("HX-Request"):
        return await run_in_threadpool(
            templates.TemplateResponse, request, "app/_queue.html", ctx
        )
    ctx |= await run_in_threadpool(shell_context, request)
    ctx["mode"] = "queue"
    return await run_in_threadpool(templates.TemplateResponse, request, "app/queue.html", ctx)
