"""Send someone and close the loop after a rescue (UI-3, UC-02 and UC-05).

Two server-rendered screens over the existing `POST /requests/{id}/status` logic
(`status.apply_status`); outcomes use the `rescued` fields from #306 (#309).

- `GET /requests/{id}/send`: crew picker (the boat roster with a seat warning, or free text) and a
  drafted message built only from the extracted fields and the callback number (copy button).
  `POST` "On the way" writes one `assigned` event with the boat as `crew`.
- `GET /requests/{id}/update` (a row in Out now): four outcomes, each one event:
  - Got everyone out -> `rescued`, `outcome: "all"`, `rescued_count`, `destination` (a
    `SHELTER_NAMES` entry).
  - Got some out -> `escalated`, reason `Got N out, others still there` (people remain, so the
    card goes back to the top of Next and can get a second crew).
  - Nobody there -> `rescued`, `outcome: "nobody"`, `rescued_count: 0`.
  - Couldn't get there -> `escalated` with the typed reason. The board tier comes from the
    engine's decision, so the card goes back to the top of Next at the same priority.

A refused move (409, or a 422 for a bad count) re-renders the screen with the message and
everything that was typed. Status events are written on the group's first request (the card), so
the other posts folded into that card leave the lists with it; a rescue also writes one
`closed_duplicate` event per folded post (`status.apply_status`).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from rescue_field import words
from rescue_field.board import load_board, templates
from rescue_field.boats import list_boats, seat_warning
from rescue_field.shell import shell_config, shell_context
from rescue_field.status import MAX_RESCUED_COUNT, StatusError, apply_status
from rescue_field.volunteer import tier_card

router = APIRouter()

OUTCOMES = (
    ("all", "Got everyone out"),
    ("some", "Got some out"),
    ("nobody", "Nobody there"),
    ("noreach", "Couldn't get there"),
)
CLOSED_TOO = "Other posts about this house were closed too, so nobody else gets sent."
MOVED_TOO = "Other posts about this house went with it, so nobody else gets sent."


def _people(row: dict[str, Any]) -> int | None:
    n = (row["extracted"].get("people_count") or {}).get("value")
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def draft_message(row: dict[str, Any]) -> str:
    """The message for the crew: only extracted fields and the callback number, never raw text."""
    e = row["extracted"]
    lines = [f"Rescue {words.incident(row)}: {words.headline(e)}."]
    address = (e.get("location") or {}).get("text")
    lines.append(f"Where: {address}" if address else "Where: no address yet, call first")
    lines += words.details(e)
    if row.get("contact"):
        lines.append(f"Call back: {row['contact']}")
    return "\n".join(lines)


def find_row(board: dict[str, Any], request_id: str) -> dict[str, Any] | None:
    """The tier row (a card) for `request_id`, or None (unknown, a folded post, not on a tier)."""
    for tier in board["tiers"]:
        for row in tier["rows"]:
            if row["request_id"] == request_id:
                return row
    return None


def outcome_status(outcome: str, form: dict[str, Any]) -> dict[str, Any]:
    """The `POST /requests/{id}/status` body for one Update outcome (StatusError when invalid)."""
    if outcome == "all":
        shelter = (form.get("shelter") or "").strip()
        data: dict[str, Any] = {
            "status": "rescued",
            "outcome": "all",
            "rescued_count": form.get("rescued_count"),
        }
        if data["rescued_count"] in (None, ""):
            raise StatusError(422, "invalid_input", "How many people did you get out?")
        if shelter:
            data["destination"] = shelter
        return data
    if outcome == "some":
        raw = str(form.get("got_count") or "").strip()
        if not re.fullmatch(r"[0-9]{1,7}", raw) or not 1 <= int(raw) <= MAX_RESCUED_COUNT:
            raise StatusError(422, "invalid_input", "How many people did you get out? (1 or more)")
        return {"status": "escalated", "reason": f"Got {int(raw)} out, others still there"}
    if outcome == "nobody":
        return {"status": "rescued", "outcome": "nobody", "rescued_count": 0}
    if outcome == "noreach":
        reason = (form.get("reason") or "").strip()
        if not reason:
            raise StatusError(422, "invalid_input", "Say why you couldn't get there.")
        return {"status": "escalated", "reason": reason}
    raise StatusError(422, "invalid_input", "Pick what happened.")


def confirmation(status: str, card: dict[str, Any], crew: str | None = None) -> list[str]:
    """What changed, in words, after a status was written."""
    others = bool(card.get("other_posts"))
    if status == "assigned":
        lines = [f"{crew} is on the way. This request is now in Out now."]
        return lines + ([MOVED_TOO] if others else [])
    if status == "rescued":
        lines = ["Done. This house is off every list."]
        return lines + ([CLOSED_TOO] if others else [])
    pill = (card.get("pill") or {}).get("text", "")
    return [f"Saved. It is back at the top of Next, still {pill}."]


def _write(request: Request, request_id: str, data: dict[str, Any]) -> dict[str, Any]:
    state = request.app.state
    if state.store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    with state.intake_lock:
        return apply_status(state.store, request_id, data, state.config)


def _error_text(exc: StatusError) -> str:
    if exc.code == 409:
        return f"Could not save: someone already updated this request ({exc.detail})."
    return exc.detail


async def _render(
    request: Request, name: str, ctx: dict[str, Any], status_code: int = 200
) -> HTMLResponse:
    ctx |= await run_in_threadpool(shell_context, request)
    ctx["mode"] = "queue"
    ctx["couldnt"] = words.COULDNT_GET_THERE
    return await run_in_threadpool(
        lambda: templates.TemplateResponse(request, name, ctx, status_code=status_code)
    )


async def _load(request: Request, request_id: str) -> dict[str, Any] | None:
    return find_row(await load_board(request), request_id)


async def _not_found(request: Request) -> HTMLResponse:
    ctx = {"lines": ["We could not find that request on the list."], "error": True}
    return await _render(request, "app/done.html", ctx, 404)


async def _form(request: Request) -> dict[str, Any]:
    try:
        return {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    except ValueError:
        return {}


def _boats(request: Request, row: dict[str, Any]) -> list[dict[str, Any]]:
    people = _people(row)
    return [
        b | {"warning": seat_warning(b["seats"], people)}
        for b in list_boats(request.app.state.store)
    ]


async def _send_page(
    request: Request, row: dict[str, Any], form: dict[str, Any], error: str | None, code: int
) -> HTMLResponse:
    ctx = {
        "c": tier_card(row),
        "boats": await run_in_threadpool(_boats, request, row),
        "draft": draft_message(row),
        "form": form,
        "error": error,
    }
    return await _render(request, "app/send.html", ctx, code)


@router.get("/requests/{request_id}/send", response_class=HTMLResponse)
async def send_page(request_id: str, request: Request) -> HTMLResponse:
    row = await _load(request, request_id)
    if row is None:
        return await _not_found(request)
    return await _send_page(request, row, {}, None, 200)


@router.post("/requests/{request_id}/send", response_class=HTMLResponse)
async def send_post(request_id: str, request: Request) -> HTMLResponse:
    form = await _form(request)
    row = await _load(request, request_id)
    if row is None:
        return await _not_found(request)
    crew = (form.get("crew_other") or "").strip() or (form.get("crew") or "").strip()
    if not crew:
        return await _send_page(request, row, form, "Pick a boat or type who is going.", 422)
    data = {"status": "assigned", "crew": crew}
    try:
        await run_in_threadpool(_write, request, request_id, data)
    except StatusError as exc:
        return await _send_page(request, row, form, _error_text(exc), exc.code)
    lines = confirmation("assigned", tier_card(row), crew)
    return await _render(request, "app/done.html", {"lines": lines, "error": False})


async def _update_page(
    request: Request, row: dict[str, Any], form: dict[str, Any], error: str | None, code: int
) -> HTMLResponse:
    ctx = {
        "c": tier_card(row),
        "outcomes": OUTCOMES,
        "shelters": shell_config()["shelter_names"],
        "people": _people(row),
        "form": form,
        "error": error,
    }
    return await _render(request, "app/update.html", ctx, code)


@router.get("/requests/{request_id}/update", response_class=HTMLResponse)
async def update_page(request_id: str, request: Request) -> HTMLResponse:
    row = await _load(request, request_id)
    if row is None:
        return await _not_found(request)
    return await _update_page(request, row, {}, None, 200)


@router.post("/requests/{request_id}/update", response_class=HTMLResponse)
async def update_post(request_id: str, request: Request) -> HTMLResponse:
    form = await _form(request)
    row = await _load(request, request_id)
    if row is None:
        return await _not_found(request)
    try:
        data = outcome_status(form.get("outcome") or "", form)
        await run_in_threadpool(_write, request, request_id, data)
    except StatusError as exc:
        return await _update_page(request, row, form, _error_text(exc), exc.code)
    lines = confirmation(data["status"], tier_card(row))
    return await _render(request, "app/done.html", {"lines": lines, "error": False})
