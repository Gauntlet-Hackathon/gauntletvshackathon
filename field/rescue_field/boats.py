"""Boat roster kept on the field laptop (UI-4).

A `boats` table in the field SQLite file, separate from the event log: not an event, never in the
sync outbox, never sent to the cloud. Routes: GET /boats (screen), GET /boats.json, POST /boats
(add), POST /boats/{name} (edit seats/status), POST /boats/{name}/delete.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from rescue_field.sanitize import clean_text
from rescue_field.shell import shell_context, templates
from rescue_field.store import EventStore

router = APIRouter()

STATUS_WORDS = {
    "ready": "At the launch, ready to go",
    "out": "Out on a job",
    "returning": "Back at the shelter soon",
}
MIN_SEATS, MAX_SEATS = 1, 100
MAX_NAME = 60


class BoatError(Exception):
    def __init__(self, code: int, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def seat_warning(seats: int, people_count: int | None) -> str | None:
    """'Not enough room for N' when the boat has fewer seats than people; None if unknown."""
    if people_count is None or seats >= people_count:
        return None
    return f"Not enough room for {people_count}"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(r: Any) -> dict[str, Any]:
    return {
        "name": r[0],
        "seats": r[1],
        "status": r[2],
        "status_text": STATUS_WORDS[r[2]],
        "last_check_in": r[3],
    }


def _seats(value: Any) -> int:
    if isinstance(value, bool):
        raise BoatError(422, "Seats must be a whole number")
    try:
        n = int(str(value).strip())
    except ValueError:
        raise BoatError(422, "Seats must be a whole number") from None
    if not MIN_SEATS <= n <= MAX_SEATS:
        raise BoatError(422, f"Seats must be between {MIN_SEATS} and {MAX_SEATS}")
    return n


def _status(value: Any) -> str:
    if value not in STATUS_WORDS:
        raise BoatError(422, "Status must be ready, out or returning")
    return value


def _name(value: Any) -> str:
    name = clean_text(value).strip() if isinstance(value, str) else ""
    if not name:
        raise BoatError(422, "Boat name is required")
    if len(name) > MAX_NAME:
        raise BoatError(422, f"Boat name is longer than {MAX_NAME} characters")
    return name


def list_boats(store: EventStore) -> list[dict[str, Any]]:
    with store._lock:
        rows = store._conn.execute(
            "SELECT name, seats, status, last_check_in FROM boats ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [_row(r) for r in rows]


def get_boat(store: EventStore, name: str) -> dict[str, Any] | None:
    with store._lock:
        r = store._conn.execute(
            "SELECT name, seats, status, last_check_in FROM boats WHERE name = ?", (name,)
        ).fetchone()
    return _row(r) if r else None


def add_boat(store: EventStore, data: dict[str, Any]) -> dict[str, Any]:
    name = _name(data.get("name"))
    seats = _seats(data.get("seats"))
    status = _status(data.get("status") or "ready")
    try:
        with store._lock:
            store._conn.execute(
                "INSERT INTO boats(name, seats, status, last_check_in) VALUES (?, ?, ?, ?)",
                (name, seats, status, _now()),
            )
    except sqlite3.IntegrityError:
        raise BoatError(409, f"A boat called {name} already exists") from None
    return get_boat(store, name) or {}


def update_boat(store: EventStore, name: str, data: dict[str, Any]) -> dict[str, Any]:
    current = get_boat(store, name)
    if current is None:
        raise BoatError(404, f"No boat called {name}")
    seats = _seats(data["seats"]) if data.get("seats") not in (None, "") else current["seats"]
    status = _status(data["status"]) if data.get("status") else current["status"]
    with store._lock:
        store._conn.execute(
            "UPDATE boats SET seats = ?, status = ?, last_check_in = ? WHERE name = ?",
            (seats, status, _now(), name),
        )
    return get_boat(store, name) or {}


def delete_boat(store: EventStore, name: str) -> None:
    with store._lock:
        cur = store._conn.execute("DELETE FROM boats WHERE name = ?", (name,))
    if not cur.rowcount:
        raise BoatError(404, f"No boat called {name}")


def _is_form(request: Request) -> bool:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    return ctype in ("application/x-www-form-urlencoded", "multipart/form-data")


async def _body(request: Request) -> dict[str, Any]:
    try:
        data: Any = dict(await request.form()) if _is_form(request) else await request.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise BoatError(422, "Expected a JSON object or form")
    return data


def _store(request: Request) -> EventStore:
    store = request.app.state.store
    if store is None:
        raise RuntimeError("event store is not open (app lifespan not running)")
    return store


async def _page(request: Request, error: str | None = None, status: int = 200) -> HTMLResponse:
    def work() -> dict[str, Any]:
        ctx = shell_context(request)
        ctx.update(mode="queue", boats=list_boats(_store(request)), error=error)
        ctx["status_words"] = STATUS_WORDS
        return ctx

    ctx = await run_in_threadpool(work)
    return templates.TemplateResponse(request, "boats.html", ctx, status_code=status)


async def _mutate(request: Request, action: Any, *, body: bool = True) -> Any:
    """Run `action(store, data)`; JSON in -> JSON out, form in -> redirect back to the screen."""
    form = _is_form(request)
    try:
        data = await _body(request) if body else {}
        result = await run_in_threadpool(lambda: action(_store(request), data))
    except BoatError as exc:
        if form:
            return await _page(request, exc.detail, exc.code)
        return JSONResponse({"error": "invalid_input", "detail": exc.detail}, exc.code)
    if form:
        return RedirectResponse("/boats", status_code=303)
    return result if isinstance(result, dict) else {"ok": True}


@router.get("/boats", response_class=HTMLResponse)
async def boats_page(request: Request) -> HTMLResponse:
    return await _page(request)


@router.get("/boats.json")
async def boats_json(request: Request) -> Any:
    boats = await run_in_threadpool(lambda: list_boats(_store(request)))
    return {"boats": boats}


@router.post("/boats")
async def post_boat(request: Request) -> Any:
    return await _mutate(request, add_boat)


@router.post("/boats/{name}/delete")
async def post_boat_delete(name: str, request: Request) -> Any:
    return await _mutate(request, lambda store, _data: delete_boat(store, name), body=False)


@router.post("/boats/{name}")
async def post_boat_update(name: str, request: Request) -> Any:
    return await _mutate(request, lambda store, data: update_boat(store, name, data))
