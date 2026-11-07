"""Respond mode for the boat crew (UI-5): GET /respond (screen), GET /respond/job.json.

The crew picks a boat from the laptop roster (boats.py) or types a crew name; the browser
remembers it. The job is the open request (current status `assigned`: not escalated or rescued)
whose latest `assigned` event names that crew, taken from `board.build_board_from_events` (no
ranking change). The OSM chips (Sent to you, On the way, There, Done) only pick a radio line; they
are not saved. The outcome buttons post to the existing POST /requests/{id}/status, one event per
tap (static/respond.js; `outcome_body` is the same mapping in Python).

Only extracted fields, the incident label and the volunteer name reach the page: no raw text, rule
IDs, versions or confidence.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from rescue_field import boats as boats_module
from rescue_field.board import build_board_from_events
from rescue_field.shell import shell_context, templates

router = APIRouter()

PILLS = {"P1": "Needs help now", "P2": "Needs help soon", "P3": "Can wait"}
OSM = (
    ("assigned", "Sent to you"),
    ("enroute", "On the way"),
    ("onscene", "There"),
    ("done", "Done"),
)
VULNERABLE_WORDS = {
    "child": "A child",
    "elderly": "An older person",
    "disabled": "Someone with a disability",
    "medical": "Someone with a medical need",
    "pregnant": "Someone pregnant",
}
HAZARD_WORDS = {
    "rising_water": "Water rising",
    "on_roof": "On the roof",
    "injury": "Someone hurt",
    "medical_device_no_power": "Medical device without power",
    "fire": "Fire",
    "trapped": "Trapped",
}
NEED_WORDS = {"food": "Needs food", "water": "Needs drinking water", "meds": "Needs medicine"}
NO_PLACE = "Location not given"


def _same_crew(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def incident_label(request_id: str) -> str:
    """The incident badge: the last 4 characters of the request ID."""
    return request_id[-4:]


def details(extracted: dict[str, Any]) -> list[str]:
    """Plain-word bullets from the extracted fields (people, vulnerable, hazards, needs)."""
    out: list[str] = []
    people = (extracted.get("people_count") or {}).get("value")
    if isinstance(people, int):
        out.append("1 person" if people == 1 else f"{people} people")
    for key, words in (
        ("vulnerable", VULNERABLE_WORDS),
        ("hazards", HAZARD_WORDS),
        ("needs", NEED_WORDS),
    ):
        for item in extracted.get(key) or []:
            text = words.get(item.get("kind"))
            if text and text not in out:
                out.append(text)
    return out


def radio_lines(job: dict[str, Any], volunteer: str) -> dict[str, str]:
    """One radio line per OSM chip, from the place, the incident label and the volunteer name."""
    place, inc = job["place"], job["incident"]
    return {
        "assigned": f"{volunteer} here. Got incident {inc}, {place}.",
        "enroute": f"{volunteer} here. On my way to {place}, incident {inc}.",
        "onscene": f"{volunteer} here. At {place} now, incident {inc}.",
        "done": f"{volunteer} here. Incident {inc} done.",
    }


def outcome_body(
    kind: str,
    *,
    boat: str,
    count: int | None = None,
    shelter: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """The POST /requests/{id}/status body for one Respond button (static/respond.js mirrors it)."""
    if kind == "all":
        return {"status": "rescued", "rescued_count": count, "note": f"to: {shelter}"}
    if kind == "some":
        return {"status": "escalated", "reason": f"Got {count} out, others still there"}
    if kind == "nobody":
        return {"status": "rescued", "rescued_count": 0, "note": "nobody there"}
    if kind == "noreach":
        text = (reason or "").strip()
        return {
            "status": "escalated",
            "reason": f"Couldn't get there: {text}" if text else "Couldn't get there",
        }
    if kind == "decline":
        return {"status": "escalated", "reason": f"{boat} can't take this one"}
    raise ValueError(f"unknown outcome {kind!r}")


def _job(row: dict[str, Any], crew: str, contacts: dict[str, str]) -> dict[str, Any]:
    ext = row["extracted"]
    place = ((ext.get("location") or {}).get("text") or "").strip() or NO_PLACE
    group = [row, *row.get("duplicates", [])]
    contact = next((contacts[m["request_id"]] for m in group if m["request_id"] in contacts), None)
    return {
        "request_id": row["request_id"],
        "incident": incident_label(row["request_id"]),
        "tier": row["tier"],
        "pill": PILLS[row["tier"]],
        "place": place,
        "details": details(ext),
        "people": (ext.get("people_count") or {}).get("value"),
        "contact": contact,
        "crew": crew,
    }


def find_job(events: list[dict[str, Any]], crew: str) -> dict[str, Any] | None:
    """The crew's open job, or None. A request belongs to the crew of its latest `assigned`
    event; several open jobs: the first in board order, with `more` counting the rest."""
    crew = crew.strip()
    if not crew:
        return None
    assigned_to: dict[str, str] = {}
    contacts: dict[str, str] = {}
    for ev in events:  # store order: a later assign overwrites an earlier one
        if ev["type"] == "assigned":
            assigned_to[ev["request_id"]] = ev["payload"].get("crew") or ""
        elif ev["type"] == "received" and ev["payload"].get("contact"):
            contacts[ev["request_id"]] = ev["payload"]["contact"]
    board = build_board_from_events(events)
    mine = [
        row
        for tier in board["tiers"]
        for row in tier["rows"]
        if row["status"] == "assigned" and _same_crew(assigned_to.get(row["request_id"], ""), crew)
    ]
    if not mine:
        return None
    job = _job(mine[0], crew, contacts)
    job["more"] = len(mine) - 1
    return job


def _snapshot(request: Request) -> list[dict[str, Any]]:
    state = request.app.state
    if state.store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    with state.intake_lock:  # one SELECT; the job is built outside the lock
        return state.store.all_events()


def _boat_names(request: Request) -> list[str]:
    return [b["name"] for b in boats_module.list_boats(request.app.state.store)]


@router.get("/respond/job.json")
async def respond_job(request: Request, crew: str = "") -> Any:
    def work() -> dict[str, Any]:
        job = find_job(_snapshot(request), crew)
        if job is not None:
            job["radio"] = radio_lines(job, shell_context(request)["volunteer_name"])
        return {"crew": crew.strip() or None, "job": job}

    return await run_in_threadpool(work)


@router.get("/respond", response_class=HTMLResponse)
async def respond_page(request: Request, crew: str = "") -> HTMLResponse:
    def work() -> dict[str, Any]:
        ctx = shell_context(request)
        job = find_job(_snapshot(request), crew)
        radio = radio_lines(job, ctx["volunteer_name"]) if job else {}
        ctx.update(
            mode="respond",
            crew=crew.strip(),
            boats=_boat_names(request),
            job=job,
            osm=OSM,
            radio=radio,
        )
        config = {"crew": crew.strip(), "request_id": job and job["request_id"], "radio": radio}
        # Safe inside <script>: no "</script>" can come out of an extracted place name.
        ctx["config_json"] = json.dumps(config).replace("<", "\\u003c")
        return ctx

    ctx = await run_in_threadpool(work)
    return templates.TemplateResponse(request, "app/respond.html", ctx)
