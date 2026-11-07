"""Volunteer app, Respond mode for the boat crew (UI-5): GET /respond, GET /respond/job.json.

The crew picks their boat (from the laptop's boat roster, or a name typed in; the browser
remembers it). Their job is the open request whose current status is `assigned` (not escalated
or rescued) and whose latest `assigned` event names that crew. Requests come from
`board.build_board_from_events` on one snapshot of the event log, in board order: no ranking here.

The OSM chips (Sent to you, On the way, There, Done) only pick a radio line to read out; they
save nothing (responder-status events are deferred). The outcome buttons and "I can't take this
one" post to the existing `POST /requests/{id}/status`, one event per tap. Radio lines use only
the extracted address, the incident label and the volunteer name. No rule ID, version or
confidence number reaches the HTML.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from rescue_field import boats as boats_module
from rescue_field import words
from rescue_field.board import build_board_from_events, templates
from rescue_field.shell import shell_config, shell_context

router = APIRouter()
STEPS = (
    ("assigned", "Sent to you"),
    ("enroute", "On the way"),
    ("onscene", "There"),
    ("done", "Done"),
)
NO_JOB = "No job right now"
MAX_CREW = 60


def _key(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def latest_crews(events: list[dict[str, Any]]) -> dict[str, str]:
    """request_id -> crew of its latest `assigned` event (log order: a later assign wins)."""
    crews: dict[str, str] = {}
    for ev in events:
        if ev["type"] == "assigned":
            crew = (ev.get("payload") or {}).get("crew")
            if crew:
                crews[ev["request_id"]] = crew
    return crews


def find_job(events: list[dict[str, Any]], crew: str) -> dict[str, Any] | None:
    """The first board row (board order) still `assigned` whose latest crew matches `crew`
    (case and spacing ignored)."""
    want = _key(crew)
    if not want:
        return None
    crews = latest_crews(events)
    board = build_board_from_events(events)
    for tier in board["tiers"]:
        for row in tier["rows"]:
            if row["status"] == "assigned" and _key(crews.get(row["request_id"])) == want:
                return row
    return None


def radio_lines(name: str, address: str | None, incident: str) -> dict[str, str]:
    """What to read on the radio for each OSM chip: volunteer name, address, incident only."""
    place = address or "the address on the job"
    num = incident.lstrip("#")
    return {
        "assigned": f"{name} here. Got incident {num}, {place}.",
        "enroute": f"{name} here. On my way to {place}, incident {num}.",
        "onscene": f"{name} here. At {place} now, incident {num}.",
        "done": f"{name} here. Incident {num} done.",
    }


def job_card(row: dict[str, Any], volunteer_name: str) -> dict[str, Any]:
    e = row["extracted"]
    incident = words.incident(row["request_id"])
    address = (e.get("location") or {}).get("text")
    lines = radio_lines(volunteer_name, address, incident)
    return {
        "request_id": row["request_id"],
        "pill": words.tier_pill(row["tier"]),
        "incident": incident,
        "headline": words.headline(e),
        "address": address,
        "details": words.details(e),
        "contact": row.get("contact"),
        "people": (e.get("people_count") or {}).get("value"),
        "steps": [{"key": k, "label": label, "line": lines[k]} for k, label in STEPS],
    }


def respond_view(events: list[dict[str, Any]], crew: str | None) -> dict[str, Any]:
    """{"crew", "job": card|None, "shelters"}: the JSON route and the page share it."""
    config = shell_config()
    crew = " ".join((crew or "").split())[:MAX_CREW] or None
    row = find_job(events, crew) if crew else None
    return {
        "crew": crew,
        "job": job_card(row, config["volunteer_name"]) if row else None,
        "shelters": config["shelter_names"],
    }


async def _view(request: Request, crew: str | None) -> dict[str, Any]:
    state = request.app.state
    if state.store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")

    def snapshot() -> list[dict[str, Any]]:
        with state.intake_lock:  # one SELECT; the build happens outside the lock
            return state.store.all_events()

    events = await run_in_threadpool(snapshot)
    return await run_in_threadpool(respond_view, events, crew)


@router.get("/respond/job.json")
async def respond_job(request: Request, crew: str | None = None) -> dict[str, Any]:
    return await _view(request, crew)


@router.get("/respond", response_class=HTMLResponse)
async def respond_page(request: Request, crew: str | None = None) -> HTMLResponse:
    ctx = await _view(request, crew)
    ctx["boats"] = await run_in_threadpool(boats_module.list_boats, request.app.state.store)
    ctx |= await run_in_threadpool(shell_context, request)
    ctx["mode"] = "respond"
    ctx["no_job"] = NO_JOB
    return await run_in_threadpool(templates.TemplateResponse, request, "app/respond.html", ctx)
