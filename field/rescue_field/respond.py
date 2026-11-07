"""Respond mode for the boat crew (UI-5): GET /respond, GET /respond/job.json.

The crew picks their boat (from the boat roster or typed in; the page remembers it in the
browser). Their job is the open request (current status `assigned`, so not escalated or rescued)
whose latest `assigned` event names that crew, taken from the board built from the event store
(`board.build_board_from_events`, no ranking changes). When several open requests name the crew,
the one assigned most recently wins.

The OSM chips (Sent to you, On the way, There, Done) only pick a radio line to read out; they are
not saved. The outcome buttons post to the existing POST /requests/{id}/status. The job card and
the radio lines use only extracted fields, the incident label and the volunteer name: never rule
IDs, versions or confidence.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from rescue_field.board import build_board_from_events
from rescue_field.boats import list_boats
from rescue_field.shell import shell_context, templates

router = APIRouter()

PILL_WORDS = {"P1": "Needs help now", "P2": "Needs help soon", "P3": "Can wait"}
CHECK_FIRST = "Check first"
# (key, chip label) in order.
OSM_STEPS: tuple[tuple[str, str], ...] = (
    ("assigned", "Sent to you"),
    ("enroute", "On the way"),
    ("onscene", "There"),
    ("done", "Done"),
)
VULNERABLE_WORDS = {
    "child": "Child",
    "elderly": "Older person",
    "disabled": "Person with a disability",
    "medical": "Medical need",
    "pregnant": "Pregnant person",
}
HAZARD_WORDS = {
    "rising_water": "Water rising",
    "on_roof": "On the roof",
    "injury": "Someone is hurt",
    "medical_device_no_power": "Medical device without power",
    "fire": "Fire",
    "trapped": "Trapped",
}
NEED_WORDS = {"food": "Needs food", "water": "Needs drinking water", "meds": "Needs medicine"}
NO_PLACE = "the location in the message"


def _norm(crew: str | None) -> str:
    return " ".join((crew or "").split()).casefold()


def incident_label(request_id: str) -> str:
    """The incident badge: the last 4 characters of the request ID."""
    return request_id[-4:]


def details(extracted: dict[str, Any]) -> list[str]:
    """Detail bullets in plain words, from extracted fields only."""
    out: list[str] = []
    people = (extracted.get("people_count") or {}).get("value")
    if people is not None:
        out.append("1 person" if people == 1 else f"{people} people")
    for key, words in (("vulnerable", VULNERABLE_WORDS), ("hazards", HAZARD_WORDS)):
        for item in extracted.get(key) or []:
            line = words.get(item.get("kind"))
            if line and line not in out:
                out.append(line)
    for need in extracted.get("needs") or []:
        line = NEED_WORDS.get(need.get("kind"))
        if line is None:
            continue
        hours = need.get("hours_without")
        if hours is not None:
            line += f" (none for {hours:g} hours)"
        if line not in out:
            out.append(line)
    when = (extracted.get("time_ref") or {}).get("text")
    if when:
        out.append(f"Said: {when}")
    return out


def radio_lines(volunteer: str, place: str, incident: str) -> dict[str, str]:
    """What to read on the radio for each OSM chip."""
    return {
        "assigned": f"{volunteer} here. Got incident {incident}, {place}.",
        "enroute": f"{volunteer} here. On my way to {place}, incident {incident}.",
        "onscene": f"{volunteer} here. At {place} now, incident {incident}.",
        "done": f"{volunteer} here. Incident {incident} done.",
    }


def _latest_assignments(events: list[dict[str, Any]]) -> dict[str, tuple[int, str]]:
    """request_id -> (position in the log, crew) of its latest `assigned` event."""
    out: dict[str, tuple[int, str]] = {}
    for i, ev in enumerate(events):
        if ev["type"] == "assigned":
            out[ev["request_id"]] = (i, (ev.get("payload") or {}).get("crew") or "")
    return out


def _contacts(events: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for ev in events:
        if ev["type"] == "received":
            contact = (ev.get("payload") or {}).get("contact")
            if contact:
                out[ev["request_id"]] = contact
    return out


def find_job(
    events: list[dict[str, Any]], crew: str | None, volunteer: str = "Volunteer"
) -> dict[str, Any] | None:
    """The crew's open job as a card dict, or None when nothing is assigned to them."""
    want = _norm(crew)
    if not want:
        return None
    board = build_board_from_events(events)
    rows = [r for t in board["tiers"] for r in t["rows"]]
    seen = {r["request_id"] for r in rows}
    rows += [r for r in board["follow_ups"] if r["request_id"] not in seen]
    assigned = _latest_assignments(events)
    open_rows = [
        r
        for r in rows
        if r["status"] == "assigned"
        and r["request_id"] in assigned
        and _norm(assigned[r["request_id"]][1]) == want
    ]
    if not open_rows:
        return None
    row = max(open_rows, key=lambda r: assigned[r["request_id"]][0])
    rid = row["request_id"]
    tier = row.get("tier") or row.get("grouped_tier") or row.get("provisional_tier")
    extracted = row["extracted"]
    location = extracted.get("location") or {}
    place = (location.get("text") or "").strip()
    incident = incident_label(rid)
    contacts = _contacts(events)
    ids = [rid, *(d["request_id"] for d in row.get("duplicates", []))]
    contact = next((contacts[i] for i in ids if i in contacts), None)
    return {
        "request_id": rid,
        "incident": incident,
        "tier": tier if tier in PILL_WORDS else None,
        "pill": PILL_WORDS.get(tier, CHECK_FIRST),
        "place": place or None,
        "place_kind": location.get("type") if place else None,
        "details": details(extracted),
        "people": (extracted.get("people_count") or {}).get("value"),
        "contact": contact,
        "crew": assigned[rid][1],
        "radio": radio_lines(volunteer, place or NO_PLACE, incident),
    }


def _store(request: Request):
    store = request.app.state.store
    if store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    return store


def _snapshot(request: Request) -> list[dict[str, Any]]:
    with request.app.state.intake_lock:  # one SELECT; the job is built outside the lock
        return _store(request).all_events()


@router.get("/respond/job.json")
async def respond_job(request: Request, crew: str = "") -> Any:
    events = await run_in_threadpool(_snapshot, request)
    volunteer = (await run_in_threadpool(shell_context, request))["volunteer_name"]
    job = await run_in_threadpool(find_job, events, crew, volunteer)
    return {"crew": crew.strip() or None, "job": job}


@router.get("/respond", response_class=HTMLResponse)
async def respond_page(request: Request, crew: str = "") -> HTMLResponse:
    def work() -> dict[str, Any]:
        ctx = shell_context(request)
        events = _snapshot(request)
        ctx.update(
            mode="respond",
            crew=crew.strip(),
            boats=[b["name"] for b in list_boats(_store(request))],
            job=find_job(events, crew, ctx["volunteer_name"]),
            steps=OSM_STEPS,
        )
        return ctx

    ctx = await run_in_threadpool(work)
    return templates.TemplateResponse(request, "app/respond.html", ctx)
