"""Respond mode for the boat crew (UI-5): GET /respond and GET /respond/job.json.

The crew picks their boat (the roster from `boats.py`, or a crew name typed in; `respond.js`
remembers it in the browser). Their job is the open tier card (latest status `assigned`, not
escalated or rescued) whose latest `assigned` crew matches the boat, read from the same board
snapshot as the queue (`board.build_board_from_events`, no ranking of its own).

The OSM chips (Sent to you, On the way, There, Done) only pick the radio line to read out; they
are not saved (responder-status events are deferred). The outcome buttons, shown after "There"
or "Done", and "I can't take this one" post to the existing `POST /requests/{id}/status`, one
event per tap; a refused move (409) shows the API's message. Radio lines use only extracted
fields, the incident label and the volunteer name. No rule ID, version or confidence reaches
the page. Everything is served by the field app, so it works with no internet.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from rescue_field import words
from rescue_field.board import build_board_from_events, templates
from rescue_field.boats import list_boats
from rescue_field.shell import shell_config, shell_context

router = APIRouter()

# (step key, chip label); "There" and "Done" open the outcome buttons.
STEPS = (
    ("assigned", "Sent to you"),
    ("enroute", "On the way"),
    ("onscene", "There"),
    ("done", "Done"),
)
OUTCOME_STEPS = ("onscene", "done")
MAX_BOAT = 200


def _key(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def crews(events: list[dict[str, Any]]) -> dict[str, str]:
    """request_id -> crew of its latest `assigned` event (store order: the latest wins)."""
    out: dict[str, str] = {}
    for ev in events:
        if ev["type"] == "assigned":
            crew = (ev.get("payload") or {}).get("crew")
            if crew:
                out[ev["request_id"]] = crew
    return out


def find_job(events: list[dict[str, Any]], boat: str) -> dict[str, Any] | None:
    """The first open tier card (board order) assigned to `boat`, or None.

    Open means its current status is `assigned` (an escalated or rescued card is not a job).
    Crew names match ignoring case and repeated spaces."""
    want = _key(boat)
    if not want:
        return None
    crew_of = crews(events)
    board = build_board_from_events(events)
    for tier in board["tiers"]:
        for row in tier["rows"]:
            if row["status"] == "assigned" and _key(crew_of.get(row["request_id"])) == want:
                return row | {"crew": crew_of[row["request_id"]]}
    return None


def radio_lines(row: dict[str, Any], volunteer: str) -> dict[str, str]:
    """The line to read on the radio for each OSM step (design: "Jordan K. here. On my way to
    2210 Telephone Road, incident 0147.")."""
    number = row["request_id"][-4:]
    address = ((row["extracted"].get("location") or {}).get("text") or "").strip()
    me = f"{volunteer} here."
    if address:
        return {
            "assigned": f"{me} Got incident {number}, {address}.",
            "enroute": f"{me} On my way to {address}, incident {number}.",
            "onscene": f"{me} At {address} now, incident {number}.",
            "done": f"{me} Incident {number} done.",
        }
    return {
        "assigned": f"{me} Got incident {number}.",
        "enroute": f"{me} On my way to incident {number}.",
        "onscene": f"{me} At incident {number} now.",
        "done": f"{me} Incident {number} done.",
    }


def job_card(row: dict[str, Any], volunteer: str) -> dict[str, Any]:
    """The job card in plain words: no rule ID, version or confidence."""
    e = row["extracted"]
    people = (e.get("people_count") or {}).get("value")
    return {
        "request_id": row["request_id"],
        "crew": row["crew"],
        "pill": words.tier_pill(row["tier"]),
        "incident": words.incident(row["request_id"]),
        "headline": words.headline(e),
        "address": (e.get("location") or {}).get("text"),
        "details": words.details(e),
        "call": f"Call when close: {row['contact']}" if row.get("contact") else None,
        "other_posts": words.other_posts(len(row.get("duplicates") or [])),
        "people": people if isinstance(people, int) and not isinstance(people, bool) else None,
        "radio": radio_lines(row, volunteer),
    }


def _boat(value: str | None) -> str:
    return " ".join((value or "").split())[:MAX_BOAT]


async def _snapshot(request: Request) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    state = request.app.state
    if state.store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")

    def read() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with state.intake_lock:  # one SELECT; matching happens outside the lock
            events = state.store.all_events()
        return events, list_boats(state.store)

    return await run_in_threadpool(read)


async def _job(request: Request, boat: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    events, boats = await _snapshot(request)
    row = await run_in_threadpool(find_job, events, boat) if boat else None
    return (job_card(row, shell_config()["volunteer_name"]) if row else None), boats


@router.get("/respond/job.json")
async def respond_job(request: Request, boat: str = "") -> JSONResponse:
    boat = _boat(boat)
    job, _ = await _job(request, boat)
    return JSONResponse({"boat": boat or None, "job": job})


@router.get("/respond", response_class=HTMLResponse)
async def respond_page(request: Request, boat: str = "", step: str = "") -> HTMLResponse:
    boat = _boat(boat)
    job, boats = await _job(request, boat)
    keys = [k for k, _ in STEPS]
    ctx = await run_in_threadpool(shell_context, request)
    ctx |= {
        "mode": "respond",
        "boat": boat,
        "boats": boats,
        "job": job,
        "steps": STEPS,
        "step": step if step in keys else None,
        "show_outcomes": step in OUTCOME_STEPS,
        "shelters": ctx["shelter_names"],
    }
    return await run_in_threadpool(
        lambda: templates.TemplateResponse(request, "app/respond.html", ctx)
    )
