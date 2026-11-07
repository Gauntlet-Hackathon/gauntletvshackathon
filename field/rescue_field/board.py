"""Coordinator board (the technical view): GET /board ranks requests P1, P2, P3 and lists
abstains in a follow-up queue. `GET /` is the volunteer app (`volunteer.py`).

Everything is derived from the event store. Within a tier the order is
`rescue_shared.logic.within_tier_sort_key` (04). Requests the engine marked `duplicate` are
collapsed under the earliest request of their group, which shows the group's highest tier.
Not-a-request items stay off the tiers and the queue (listed newest first in `not_requests`).
The follow-up queue is sorted by the engine's
`provisional_tier` (P1 first; DECISIONS R1), then by the same within-tier key; an abstain
without one (older engine output) sorts after P3. An abstain whose group a later prioritized
duplicate lifted onto a tier (review O-08) shows its question on that tier row and keeps its
follow-up entry, marked with the group's tier (`grouped_tier`). `GET /board` returns the full
page, or just the board fragment for an HTMX request (`HX-Request` header), so a page can swap
it in place.

Cost (review M-04): the event log is read once under the intake lock (one SELECT); the board is
built from that snapshot in one pass and rendered in a worker thread, both outside the lock, so a
large board never holds up intake, status changes, the sync worker or the health check.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from rescue_field.shell import model_banner
from rescue_field.status import ORDER, RANK
from rescue_field.store import EventStore
from rescue_field.words_es import spanish_line
from rescue_shared import logic

TIERS = ("P1", "P2", "P3")
# The `possible_hoax` follow-up text (contracts/engine_rules.md, "Follow-up questions").
Q_HOAX = "Can you confirm this request is real and tell us exactly what help you need?"
templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
# "En español" line under a follow-up question on a Spanish request (UI-10).
templates.env.globals["spanish_line"] = spanish_line
router = APIRouter()


def _requests(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One record per request, in arrival order, from its received/extracted/decided events,
    with its current status (the highest status event, as `status.current_status`). One pass
    over the events (review M-04: a per-request rescan made the board O(requests x events))."""
    recs: dict[str, dict[str, Any]] = {}
    for ev in events:
        rec = recs.setdefault(ev["request_id"], {"request_id": ev["request_id"], "status": None})
        kind = ev["type"]
        if kind == "received":
            rec["received_at"] = ev["occurred_at"]
            rec["raw_text"] = ev["payload"].get("raw_text")
            rec["channel"] = ev["payload"].get("channel")
            rec["lang_hint"] = ev["payload"].get("lang_hint")
            rec["contact"] = ev["payload"].get("contact")
            rec["incident"] = ev["payload"].get("incident")
        elif kind == "extracted":
            rec["extracted"] = ev["payload"]
            rec["model_version"] = ev["model_version"]
        elif kind == "decided":
            rec["decision"] = ev["payload"]["engine_output"]
            rec["rules_version"] = ev["rules_version"]
        elif kind in RANK and (rec["status"] is None or RANK[kind] > RANK[rec["status"]]):
            rec["status"] = kind
    return {
        rid: r for rid, r in recs.items() if {"received_at", "extracted", "decision"} <= r.keys()
    }


def _root_finder(recs: dict[str, dict[str, Any]]):
    """find(rid) -> earliest request of the duplicate group containing rid."""
    parent = {rid: rid for rid in recs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def order(rid: str) -> tuple[str, str]:
        return (recs[rid]["received_at"], rid)

    for rid, rec in recs.items():
        target = rec["decision"].get("duplicate_of")
        if rec["decision"]["outcome"] == "duplicate" and target in recs:
            a, b = find(rid), find(target)
            if a != b:
                keep, drop = sorted((a, b), key=order)
                parent[drop] = keep
    return find


def _provisional_tier(group: list[dict[str, Any]]) -> str | None:
    """Highest provisional_tier in a follow-up group, or None when no member carries one."""
    tiers = [m["decision"].get("provisional_tier") for m in group]
    return min((t for t in tiers if t in TIERS), default=None)


def _follow_up_key(r: dict[str, Any]) -> tuple[int, tuple[int, int, str, str]]:
    tier = r["provisional_tier"]
    rank = TIERS.index(tier) if tier in TIERS else len(TIERS)  # missing sorts last
    return rank, logic.within_tier_sort_key(r["extracted"], r["received_at"], r["request_id"])


def _label(decision: dict[str, Any]) -> str:
    q = decision.get("follow_up_question")
    hoax = decision.get("abstain_reason") == "possible_hoax" or q == Q_HOAX
    return "Possible hoax" if hoax else "Needs confirmation"


def _questions(rec: dict[str, Any], members: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The follow-up questions to show on a tier row headed by `rec`.

    First the root's own question when it abstained (review O-08: the row is headed by its
    text, so a suspected hoax or a count conflict must stay flagged there). Then the first
    member (the one that set the tier first) whose `prioritized` or `duplicate` decision carries
    one (DECISIONS R9: a duplicate whose own signal set the group tier keeps its question)."""
    out: list[dict[str, str]] = []
    d = rec["decision"]
    if d["outcome"] == "abstain" and d.get("follow_up_question"):
        out.append({"text": d["follow_up_question"], "label": _label(d)})
    for m in members:
        d = m["decision"]
        if d["outcome"] in ("prioritized", "duplicate") and d.get("follow_up_question"):
            q = {"text": d["follow_up_question"], "label": _label(d)}
            if q not in out:
                out.append(q)
            break
    return out


def allowed_statuses(current: str | None) -> list[str]:
    """Statuses the order accepts next: strictly above `current` (any when it is None)."""
    floor = RANK.get(current, 0) if current else 0
    return [s for s in ORDER if RANK[s] > floor]


def build_board(store: EventStore) -> dict[str, Any]:
    """{"tiers": [{"tier", "rows"}...], "follow_ups": [...], "counts": {...}}."""
    return build_board_from_events(store.all_events())


def build_board_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """`build_board` from a snapshot of the event log (store order)."""
    recs = _requests(events)
    find = _root_finder(recs)
    members: dict[str, list[dict[str, Any]]] = {}
    for rid, rec in recs.items():
        members.setdefault(find(rid), []).append(rec)

    by_tier: dict[str, list[dict[str, Any]]] = {t: [] for t in TIERS}
    follow_ups: list[dict[str, Any]] = []
    not_requests: list[dict[str, Any]] = []
    for root, group in members.items():
        rec = recs[root]
        dups = sorted((m for m in group if m is not rec), key=lambda m: m["received_at"])
        tiers = [m["decision"]["priority_tier"] for m in group if m["decision"]["priority_tier"]]
        tier = min(tiers, default=None)  # "P1" < "P2" < "P3"
        outcome = rec["decision"]["outcome"]
        if tier is None and outcome == "abstain":
            follow_ups.append(
                {**rec, "duplicates": dups, "provisional_tier": _provisional_tier(group)}
            )
            continue
        if tier is None:
            not_requests.append(rec)
            continue
        # Show the decision of the member that set the group's tier (the root when it ties).
        shown = next(
            (m for m in [rec, *dups] if m["decision"]["priority_tier"] == tier), rec
        )
        questions = _questions(rec, [shown, rec, *dups])
        by_tier[tier].append(
            {
                **rec,
                "tier": tier,
                "tier_source": shown,
                "question": questions[0] if questions else None,
                "questions": questions,
                "duplicates": dups,
                "allowed": allowed_statuses(rec["status"]),
            }
        )
        # An abstain lifted onto a tier by a duplicate still needs its callback (O-08).
        for m in (m for m in [rec, *dups] if m["decision"]["outcome"] == "abstain"):
            follow_ups.append(
                {
                    **m,
                    "duplicates": [x for x in [rec, *dups] if x is not m],
                    "provisional_tier": _provisional_tier([m]),
                    "grouped_tier": tier,
                }
            )
    for rows in by_tier.values():
        rows.sort(
            key=lambda r: logic.within_tier_sort_key(
                r["extracted"], r["received_at"], r["request_id"]
            )
        )
    follow_ups.sort(key=_follow_up_key)
    not_requests.sort(key=lambda r: (r["received_at"], r["request_id"]), reverse=True)
    return {
        "tiers": [{"tier": t, "rows": by_tier[t]} for t in TIERS],
        "follow_ups": follow_ups,
        "not_requests": not_requests,
        "counts": {t: len(by_tier[t]) for t in TIERS} | {"follow_ups": len(follow_ups)},
    }


async def load_board(request: Request) -> dict[str, Any]:
    """`build_board_from_events` for the app's store: one SELECT under the intake lock, the
    build in a worker thread outside it. Shared with the volunteer app (UI-2)."""
    store = request.app.state.store
    if store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    lock = request.app.state.intake_lock

    def snapshot() -> list[dict[str, Any]]:
        with lock:  # one SELECT; building and rendering happen outside the lock
            return store.all_events()

    events = await run_in_threadpool(snapshot)
    return await run_in_threadpool(build_board_from_events, events)


@router.get("/board", response_class=HTMLResponse)
async def board(request: Request) -> HTMLResponse:
    ctx = await load_board(request)
    ctx["model_version"] = getattr(request.app.state.extractor, "model_version", None) or "unknown"
    ctx["model_banner"] = model_banner(request)
    ctx["demo_mode"] = os.environ.get("DEMO_MODE") == "1"
    # The state /sync/offline-toggle flips, so the button label survives a reload.
    worker = getattr(request.app.state, "sync_worker", None)
    ctx["sync_paused"] = bool(getattr(worker, "paused", False))
    name = "_board.html" if request.headers.get("HX-Request") else "board.html"
    # Rendering a large board takes a while: do it in a worker thread, never on the event loop
    # (which would stall every other request, /healthz included).
    return await run_in_threadpool(templates.TemplateResponse, request, name, ctx)
