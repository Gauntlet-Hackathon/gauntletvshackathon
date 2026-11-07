"""Field FastAPI app: intake endpoints (single, bulk paste, bulk CSV).

Handlers are `async def` on purpose: the SQLite store is bound to the thread that created it,
and async handlers all run on the event-loop thread. One laptop, one operator.
"""

from __future__ import annotations

import csv
import io
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from rescue_field import engine_client
from rescue_field.pipeline import Decider, PipelineConfig, process_request
from rescue_field.slm import Extractor, FixtureExtractor
from rescue_field.store import EventStore

CHANNELS = ("sms", "social", "form", "call_note", "csv")
TEXT_COLUMNS = ("raw_text", "text", "message")
MAX_BULK_ROWS = 1000


class IntakeError(Exception):
    """Invalid input: becomes a 422 with a reason. Raised before anything is written."""


def _clean(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise IntakeError(f"{name} must be a string")
    return value.strip() or None


def _fields(body: dict[str, Any], default_channel: str) -> dict[str, Any]:
    raw = body.get("raw_text", body.get("text"))
    if not isinstance(raw, str) or not raw.strip():
        raise IntakeError("raw_text is required and must not be empty")
    channel = _clean(body.get("channel"), "channel") or default_channel
    if channel not in CHANNELS:
        raise IntakeError(f"channel must be one of {', '.join(CHANNELS)}")
    lang = _clean(body.get("lang_hint"), "lang_hint")
    if lang is not None and not (len(lang) == 2 and lang.isalpha() and lang.islower()):
        raise IntakeError("lang_hint must be a two-letter lowercase language code")
    return {
        "raw": raw.strip(),
        "channel": channel,
        "contact": _clean(body.get("contact"), "contact"),
        "lang_hint": lang,
    }


async def _body(request: Request) -> dict[str, Any]:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype == "application/json":
        try:
            data = await request.json()
        except ValueError as exc:
            raise IntakeError("body is not valid JSON") from exc
        if not isinstance(data, dict):
            raise IntakeError("JSON body must be an object")
        return data
    if ctype in ("application/x-www-form-urlencoded", "multipart/form-data"):
        form = await request.form()
        return {k: v for k, v in form.items() if isinstance(v, str)}
    raise IntakeError("send JSON or form data")


def _csv_rows(content: bytes) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IntakeError("CSV must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    names = [n.strip().lower() for n in reader.fieldnames or []]
    column = next((c for c in TEXT_COLUMNS if c in names), None)
    if column is None:
        raise IntakeError(f"CSV needs a header with one of: {', '.join(TEXT_COLUMNS)}")
    rows = []
    for rec in reader:
        norm = {(k or "").strip().lower(): v for k, v in rec.items() if isinstance(v, str)}
        row = {**norm, "raw_text": norm.get(column, "")}
        if not any((v or "").strip() for v in norm.values()):
            continue  # fully blank line
        rows.append(row)
    return rows


def _summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "request_id": events[0]["request_id"],
        "extracted": events[1]["payload"],
        "decision": events[2]["payload"]["engine_output"],
    }


def create_app(
    *,
    store: EventStore | None = None,
    extractor: Extractor | None = None,
    decide: Decider | None = None,
    config: PipelineConfig | None = None,
) -> FastAPI:
    app = FastAPI(title="Rescue field")
    state: dict[str, Any] = {"store": store}

    def get_store() -> EventStore:
        if state["store"] is None:
            state["store"] = EventStore(os.environ.get("FIELD_DB_PATH", "field.db"))
        return state["store"]

    def run(item: dict[str, Any]) -> dict[str, Any]:
        events = process_request(
            item["raw"],
            item["channel"],
            store=get_store(),
            extractor=extractor or FixtureExtractor(),
            decide=decide or engine_client.decide,
            config=config or PipelineConfig.from_env(),
            contact=item["contact"],
            lang_hint=item["lang_hint"],
        )
        return _summary(events)

    @app.exception_handler(IntakeError)
    async def _intake_error(_: Request, exc: IntakeError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(engine_client.EngineError)
    async def _engine_error(_: Request, exc: engine_client.EngineError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": f"engine error: {exc}"})

    @app.post("/requests")
    async def post_request(request: Request) -> dict[str, Any]:
        return run(_fields(await _body(request), "form"))

    @app.post("/requests/bulk")
    async def post_bulk(request: Request) -> dict[str, Any]:
        """CSV upload (`file`) or multi-line paste (`paste` form field or text/plain body).

        Every row is validated before the first one is processed, so bad input writes nothing.
        Rows then run in order: each decision is stored before the next row's context is built,
        so earlier rows are duplicate candidates for later ones.
        """
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        raw_rows: list[dict[str, Any]]
        if ctype == "multipart/form-data" or ctype == "application/x-www-form-urlencoded":
            form = await request.form()
            upload = form.get("file")
            if upload is not None and not isinstance(upload, str):
                raw_rows = _csv_rows(await upload.read())
            elif isinstance(form.get("paste"), str):
                raw_rows = [{"raw_text": ln} for ln in form["paste"].splitlines()]
            else:
                raise IntakeError("send a CSV file in `file` or lines in `paste`")
        elif ctype == "text/csv":
            raw_rows = _csv_rows(await request.body())
        elif ctype == "text/plain":
            text = (await request.body()).decode("utf-8", errors="replace")
            raw_rows = [{"raw_text": ln} for ln in text.splitlines()]
        elif ctype == "application/json":
            data = await _body(request)
            lines = data.get("lines", data.get("paste"))
            if isinstance(lines, str):
                lines = lines.splitlines()
            if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
                raise IntakeError("JSON body needs `lines` (list of strings) or `paste`")
            raw_rows = [{"raw_text": ln} for ln in lines]
        else:
            raise IntakeError("send a CSV file, text/csv, text/plain or JSON")
        raw_rows = [r for r in raw_rows if r["raw_text"].strip()]
        if not raw_rows:
            raise IntakeError("no non-empty rows")
        if len(raw_rows) > MAX_BULK_ROWS:
            raise IntakeError(f"at most {MAX_BULK_ROWS} rows per upload")
        items = []
        for i, row in enumerate(raw_rows, start=1):
            try:
                items.append(_fields(row, "csv"))
            except IntakeError as exc:
                raise IntakeError(f"row {i}: {exc}") from exc
        results = [run(item) for item in items]
        return {"count": len(results), "results": results}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
