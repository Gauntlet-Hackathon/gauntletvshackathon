"""Coordinator board: GET / ranks requests P1, P2, P3 and lists abstains in a follow-up queue.

Everything is derived from the event store. Within a tier the order is
`rescue_shared.logic.within_tier_sort_key` (04). Requests the engine marked `duplicate` are
collapsed under the earliest request of their group, which shows the group's highest tier.
Not-a-request items are left off the board. `GET /` returns the full page, or just the board
fragment for an HTMX request (`HX-Request` header), so a page can swap it in place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from rescue_field.status import current_status
from rescue_field.store import EventStore
from rescue_shared import logic

TIERS = ("P1", "P2", "P3")
templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
router = APIRouter()


def _requests(store: EventStore) -> dict[str, dict[str, Any]]:
    """One record per request, in arrival order, from its received/extracted/decided events."""
    recs: dict[str, dict[str, Any]] = {}
    evs = store.all_events()
    for ev in evs:
        rec = recs.setdefault(ev["request_id"], {"request_id": ev["request_id"]})
        if ev["type"] == "received":
            rec["received_at"] = ev["occurred_at"]
            rec["raw_text"] = ev["payload"].get("raw_text")
            rec["channel"] = ev["payload"].get("channel")
        elif ev["type"] == "extracted":
            rec["extracted"] = ev["payload"]
            rec["model_version"] = ev["model_version"]
        elif ev["type"] == "decided":
            rec["decision"] = ev["payload"]["engine_output"]
            rec["rules_version"] = ev["rules_version"]
    for rid, rec in recs.items():
        rec["status"] = current_status([e for e in evs if e["request_id"] == rid])
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


def build_board(store: EventStore) -> dict[str, Any]:
    """{"tiers": [{"tier", "rows"}...], "follow_ups": [...], "counts": {...}}."""
    recs = _requests(store)
    find = _root_finder(recs)
    members: dict[str, list[dict[str, Any]]] = {}
    for rid, rec in recs.items():
        members.setdefault(find(rid), []).append(rec)

    by_tier: dict[str, list[dict[str, Any]]] = {t: [] for t in TIERS}
    follow_ups: list[dict[str, Any]] = []
    for root, group in members.items():
        rec = recs[root]
        dups = sorted((m for m in group if m is not rec), key=lambda m: m["received_at"])
        tiers = [m["decision"]["priority_tier"] for m in group if m["decision"]["priority_tier"]]
        tier = min(tiers, default=None)  # "P1" < "P2" < "P3"
        outcome = rec["decision"]["outcome"]
        if tier is None and outcome == "abstain":
            follow_ups.append({**rec, "duplicates": dups})
            continue
        if tier is None:
            continue  # not_a_request
        # Show the decision of the member that set the group's tier (the root when it ties).
        shown = next(
            (m for m in [rec, *dups] if m["decision"]["priority_tier"] == tier), rec
        )
        by_tier[tier].append({**rec, "tier": tier, "tier_source": shown, "duplicates": dups})
    for rows in by_tier.values():
        rows.sort(
            key=lambda r: logic.within_tier_sort_key(
                r["extracted"], r["received_at"], r["request_id"]
            )
        )
    follow_ups.sort(key=lambda r: (r["received_at"], r["request_id"]))
    return {
        "tiers": [{"tier": t, "rows": by_tier[t]} for t in TIERS],
        "follow_ups": follow_ups,
        "counts": {t: len(by_tier[t]) for t in TIERS} | {"follow_ups": len(follow_ups)},
    }


@router.get("/", response_class=HTMLResponse)
async def board(request: Request) -> HTMLResponse:
    store = request.app.state.store
    if store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    lock = request.app.state.intake_lock

    def read() -> dict[str, Any]:
        with lock:  # the store connection is shared across threads
            return build_board(store)

    ctx = await run_in_threadpool(read)
    name = "_board.html" if request.headers.get("HX-Request") else "board.html"
    return templates.TemplateResponse(request, name, ctx)
