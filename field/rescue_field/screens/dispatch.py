"""Send someone and close the loop after a rescue (UI-3; UC-02, UC-05).

- `GET/POST /requests/{id}/send`: pick a boat from the roster (UI-4, with the seat warning) or
  type a crew, read the drafted message (copy button), then "On the way" writes one `assigned`
  event with the crew. The message is built only from extracted fields and the callback number:
  never the raw text.
- `GET/POST /requests/{id}/update`: four outcomes, each written as exactly one existing event
  through `status.apply_status` (the same code as `POST /requests/{id}/status`):
  Got everyone out -> `rescued` (count, note `to: <shelter>`); Got some out -> `escalated`
  (reason `Got N out, others still there`); Nobody there -> `rescued` (count 0, note
  `nobody there`); Couldn't get there -> `escalated` (the typed reason). Nothing is deleted; an
  escalated request goes back to the top of Next at the tier the engine gave it, so its
  priority never drops.

A refused move (409) or bad input (422) re-renders the form with the API's message and keeps
what was typed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from rescue_field import words
from rescue_field.board import load_board, templates
from rescue_field.boats import list_boats, seat_warning
from rescue_field.shell import shell_config, shell_context
from rescue_field.status import StatusError, _count, apply_status
from rescue_field.volunteer import tier_card

router = APIRouter()

OUTCOMES = {
    "all": "Got everyone out",
    "some": "Got some of them out",
    "nobody": "Nobody there, already gone",
    "couldnt": words.COULDNT_GET_THERE,
}
OTHERS_CLOSED = "Other posts about this house were closed too, so nobody else gets sent."
CREW_PLACEHOLDER = "[crew]"


def find_row(board: dict[str, Any], request_id: str) -> dict[str, Any] | None:
    """The ranked board row headed by `request_id` (a group's root), or None."""
    for tier in board["tiers"]:
        for row in tier["rows"]:
            if row["request_id"] == request_id:
                return row
    return None


def people(row: dict[str, Any]) -> int | None:
    return (row["extracted"].get("people_count") or {}).get("value")


def place(card: dict[str, Any]) -> str:
    return card["address"] or f"incident {card['incident']}"


def draft_message(row: dict[str, Any]) -> str:
    """The message for the crew, after "<crew>, ": extracted fields and the callback number
    only (incident, address, people, hazards, vulnerable people, needs, time said)."""
    e = row["extracted"]
    address = (e.get("location") or {}).get("text")
    parts = [f"incident {words.incident(row['request_id'])}"]
    parts.append(f"rescue at {address}" if address else "no address yet, call before you go")
    text = ", ".join(parts) + ". "
    text += "".join(f"{p}. " for p in words.phrases(e) + words.details(e))
    contact = row.get("contact")
    text += f"Call {contact} when close." if contact else "No callback number."
    return text


def _boats(request: Request, need: int | None) -> list[dict[str, Any]]:
    store = request.app.state.store
    if store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    return [b | {"warning": seat_warning(b["seats"], need)} for b in list_boats(store)]


def _save(request: Request, request_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """`apply_status` under the intake lock, exactly as `POST /requests/{id}/status`."""
    state = request.app.state
    if state.store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    with state.intake_lock:
        return apply_status(state.store, request_id, data, state.config)


async def _row(request: Request, request_id: str) -> dict[str, Any] | None:
    return find_row(await load_board(request), request_id)


async def _render(request: Request, name: str, ctx: dict[str, Any], code: int = 200) -> Any:
    ctx |= await run_in_threadpool(shell_context, request)
    ctx["mode"] = "queue"
    return await run_in_threadpool(
        lambda: templates.TemplateResponse(request, name, ctx, status_code=code)
    )


async def _not_found(request: Request, request_id: str) -> Any:
    ctx = {"error": f"No request {request_id} on the list."}
    return await _render(request, "app/not_found.html", ctx, 404)


async def _form(request: Request) -> dict[str, str]:
    form = await request.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


# --- Send someone ---------------------------------------------------------------------------


async def _send_page(
    request: Request, row: dict[str, Any], values: dict[str, str], error: str | None, code: int
) -> Any:
    need = people(row)
    ctx = {
        "c": tier_card(row),
        "people": need,
        "boats": await run_in_threadpool(_boats, request, need),
        "message": draft_message(row),
        "crew_placeholder": CREW_PLACEHOLDER,
        "values": values,
        "error": error,
    }
    return await _render(request, "app/send.html", ctx, code)


@router.get("/requests/{request_id}/send", response_class=HTMLResponse)
async def send_page(request_id: str, request: Request) -> Any:
    row = await _row(request, request_id)
    if row is None:
        return await _not_found(request, request_id)
    return await _send_page(request, row, {}, None, 200)


@router.post("/requests/{request_id}/send", response_class=HTMLResponse)
async def send_post(request_id: str, request: Request) -> Any:
    row = await _row(request, request_id)
    if row is None:
        return await _not_found(request, request_id)
    values = await _form(request)
    crew = (values.get("crew_other") or "").strip() or (values.get("crew") or "").strip()
    if not crew:
        return await _send_page(request, row, values, "Pick a boat or type who is going.", 422)
    try:
        await run_in_threadpool(_save, request, request_id, {"status": "assigned", "crew": crew})
    except StatusError as exc:
        return await _send_page(request, row, values, exc.detail, exc.code)
    card = tier_card(row)
    ctx = {
        "c": card,
        "title": f"{crew} is on the way to {place(card)}",
        "lines": ["When they're back, open Out now and tell us how it went."],
    }
    return await _render(request, "app/done.html", ctx)


# --- Out now -> Update ------------------------------------------------------------------------


class FormError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def outcome_event(values: dict[str, str], shelters: list[str]) -> dict[str, Any]:
    """The one status change an outcome writes, as a `POST /requests/{id}/status` body."""
    outcome = values.get("outcome")
    if outcome == "all":
        try:
            count = _count(values.get("count") or "")
        except StatusError:
            raise FormError("How many people are safe? Type a whole number.") from None
        shelter = values.get("shelter") or ""
        if shelter not in shelters:
            raise FormError("Where did they go? Pick a shelter.")
        return {"status": "rescued", "rescued_count": count, "note": f"to: {shelter}"}
    if outcome == "some":
        try:
            n = _count(values.get("some_count") or "")
        except StatusError:
            n = 0
        if n < 1:
            raise FormError("How many did they get out? Type a whole number, 1 or more.")
        return {"status": "escalated", "reason": f"Got {n} out, others still there"}
    if outcome == "nobody":
        return {"status": "rescued", "rescued_count": 0, "note": "nobody there"}
    if outcome == "couldnt":
        reason = (values.get("reason") or "").strip()
        if not reason:
            raise FormError("Why couldn't they get there? Type a reason.")
        return {"status": "escalated", "reason": reason}
    raise FormError("Pick how it went.")


def confirmation(event: dict[str, Any], outcome: str, card: dict[str, Any]) -> list[str]:
    """What the volunteer is told, including what else changed."""
    where = place(card)
    others = [OTHERS_CLOSED] if card["other_posts"] else []
    if outcome == "all":
        n = event["rescued_count"]
        safe = "1 person" if n == 1 else f"{n} people"
        shelter = event["note"].removeprefix("to: ")
        return [f"Marked safe: {safe} from {where}, taken to {shelter}.", *others]
    if outcome == "nobody":
        return [f"Closed: nobody was at {where}.", *others]
    if outcome == "some":
        return [
            f"Saved: {event['reason']}.",
            f"{where} is back at the top of Next at the same priority, so another team can go.",
        ]
    return [f"{where} is back at the top of Next at the same priority, so another team can try."]


async def _update_page(
    request: Request, row: dict[str, Any], values: dict[str, str], error: str | None, code: int
) -> Any:
    ctx = {
        "c": tier_card(row),
        "people": people(row),
        "outcomes": OUTCOMES,
        "values": values,
        "error": error,
    }
    return await _render(request, "app/update.html", ctx, code)


@router.get("/requests/{request_id}/update", response_class=HTMLResponse)
async def update_page(request_id: str, request: Request) -> Any:
    row = await _row(request, request_id)
    if row is None:
        return await _not_found(request, request_id)
    return await _update_page(request, row, {}, None, 200)


@router.post("/requests/{request_id}/update", response_class=HTMLResponse)
async def update_post(request_id: str, request: Request) -> Any:
    row = await _row(request, request_id)
    if row is None:
        return await _not_found(request, request_id)
    values = await _form(request)
    shelters = shell_config()["shelter_names"]
    try:
        event = outcome_event(values, shelters)
    except FormError as exc:
        return await _update_page(request, row, values, exc.detail, 422)
    try:
        await run_in_threadpool(_save, request, request_id, event)
    except StatusError as exc:
        return await _update_page(request, row, values, exc.detail, exc.code)
    card = tier_card(row)
    outcome = values["outcome"]
    ctx = {"c": card, "title": OUTCOMES[outcome], "lines": confirmation(event, outcome, card)}
    return await _render(request, "app/done.html", ctx)
