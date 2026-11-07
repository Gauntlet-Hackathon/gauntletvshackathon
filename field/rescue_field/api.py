"""Field FastAPI app: intake endpoints (F5).

POST /requests        one request, JSON or form -> its decision
POST /requests/bulk   CSV upload or multi-line paste -> one decision per row, in order

All store and engine work runs on one worker thread: the SQLite connection stays on the
thread that opened it, requests are processed one at a time (so each sees every earlier
request as a duplicate candidate), and the event loop is never blocked by the engine.
Input is fully validated before anything runs, so invalid input returns 422 and writes nothing.
"""

from __future__ import annotations

import asyncio
import csv
import io
import os
import re
from collections.abc import AsyncIterator, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypeVar

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from rescue_field import engine_client
from rescue_field.pipeline import (
    BatchDecider,
    Decider,
    IntakeItem,
    PipelineConfig,
    process_batch,
    process_request,
)
from rescue_field.slm import Extractor
from rescue_field.store import EventStore

DEFAULT_DB_PATH = "field.db"
CHANNELS = ("sms", "social", "form", "call_note", "csv")
MAX_TEXT_CHARS = 5000
MAX_BULK_ROWS = 1000
CSV_COLUMNS = ("raw_text", "channel", "contact", "lang_hint")
_LANG_RE = re.compile(r"^[a-z]{2}$")

T = TypeVar("T")


class InvalidInput(Exception):
    """Client input that must be rejected with 422 (the message is the reason)."""


def _opt(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidInput(f"{name} must be a string")
    return value.strip() or None


def parse_item(fields: Mapping[str, Any], default_channel: str, where: str = "") -> IntakeItem:
    """Validate one request's fields (raw_text, channel, contact, lang_hint)."""
    prefix = f"{where}: " if where else ""
    try:
        raw = fields.get("raw_text")
        if not isinstance(raw, str) or not raw.strip():
            raise InvalidInput("raw_text is required and must be non-empty text")
        raw = raw.strip()
        if len(raw) > MAX_TEXT_CHARS:
            raise InvalidInput(f"raw_text is longer than {MAX_TEXT_CHARS} characters")
        channel = _opt(fields.get("channel"), "channel") or default_channel
        if channel not in CHANNELS:
            raise InvalidInput(f"channel must be one of {', '.join(CHANNELS)}")
        lang_hint = _opt(fields.get("lang_hint"), "lang_hint")
        if lang_hint is not None and not _LANG_RE.fullmatch(lang_hint):
            raise InvalidInput("lang_hint must be a two-letter lowercase code")
        contact = _opt(fields.get("contact"), "contact")
    except InvalidInput as exc:
        raise InvalidInput(f"{prefix}{exc}") from exc
    return IntakeItem(raw=raw, channel=channel, contact=contact, lang_hint=lang_hint)


def parse_csv(text: str, default_channel: str = "csv") -> list[IntakeItem]:
    """CSV with a header row; `raw_text` is required, channel/contact/lang_hint optional."""
    reader = csv.DictReader(io.StringIO(text), strict=True)
    try:
        header = [h.strip() for h in (reader.fieldnames or [])]
    except csv.Error as exc:
        raise InvalidInput(f"malformed CSV header: {exc}") from exc
    if "raw_text" not in header:
        raise InvalidInput("CSV needs a header row with a raw_text column")
    unknown = [h for h in header if h not in CSV_COLUMNS]
    if unknown:
        raise InvalidInput(f"unknown CSV column(s): {', '.join(unknown)}")
    reader.fieldnames = header
    items = []
    try:
        for row in reader:
            if None in row:
                raise InvalidInput(f"line {reader.line_num}: more cells than header columns")
            if not any((v or "").strip() for v in row.values()):
                continue
            items.append(parse_item(row, default_channel, f"line {reader.line_num}"))
    except csv.Error as exc:
        raise InvalidInput(f"line {reader.line_num}: malformed CSV: {exc}") from exc
    return items


def parse_paste(text: str, channel: str | None = None) -> list[IntakeItem]:
    """One request per non-empty line."""
    default = channel or "form"
    return [
        parse_item({"raw_text": line, "channel": default}, default, f"line {n}")
        for n, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    ]


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidInput("input must be UTF-8 text") from exc


def _media_type(request: Request) -> str:
    return request.headers.get("content-type", "").split(";")[0].strip().lower()


async def _form_fields(request: Request) -> dict[str, Any]:
    form = await request.form()
    return {k: v for k, v in form.items() if not isinstance(v, UploadFile)}


async def _single_fields(request: Request) -> dict[str, Any]:
    media = _media_type(request)
    if media == "application/json":
        try:
            body = await request.json()
        except ValueError as exc:
            raise InvalidInput("body is not valid JSON") from exc
        if not isinstance(body, dict):
            raise InvalidInput("JSON body must be an object")
        return body
    if media in ("application/x-www-form-urlencoded", "multipart/form-data"):
        return await _form_fields(request)
    raise InvalidInput("send JSON or form data")


async def _bulk_items(request: Request) -> list[IntakeItem]:
    media = _media_type(request)
    if media == "text/csv":
        items = parse_csv(_decode(await request.body()))
    elif media == "text/plain":
        items = parse_paste(_decode(await request.body()))
    elif media in ("application/x-www-form-urlencoded", "multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        text = form.get("text")
        channel = _opt(form.get("channel"), "channel")
        if isinstance(upload, UploadFile):
            items = parse_csv(_decode(await upload.read()), channel or "csv")
        elif isinstance(text, str) and text.strip():
            items = parse_paste(text, channel)
        else:
            raise InvalidInput("send a CSV file in 'file' or pasted lines in 'text'")
    else:
        raise InvalidInput("send a CSV file, text/csv, text/plain, or a form with 'text'")
    if not items:
        raise InvalidInput("no requests found in the input")
    if len(items) > MAX_BULK_ROWS:
        raise InvalidInput(f"at most {MAX_BULK_ROWS} requests per upload")
    return items


def create_app(
    *,
    db_path: str | Path | None = None,
    extractor: Extractor | None = None,
    decide: Decider | None = None,
    decide_batch: BatchDecider | None = None,
    config: PipelineConfig | None = None,
) -> FastAPI:
    """Build the app. Defaults: FIELD_DB_PATH (else field.db), ENGINE_CMD engine, fixture SLM."""
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="field-store")
    state: dict[str, Any] = {}

    def store() -> EventStore:
        # Opened lazily on the worker thread, the only thread that ever touches it.
        if "store" not in state:
            path = db_path or os.environ.get("FIELD_DB_PATH", DEFAULT_DB_PATH)
            state["store"] = EventStore(path)
        return state["store"]

    def pipeline_config() -> PipelineConfig:
        if "config" not in state:
            state["config"] = config or PipelineConfig.from_env()
        return state["config"]

    async def on_worker(fn: Callable[[], T]) -> T:
        try:
            return await asyncio.get_running_loop().run_in_executor(worker, fn)
        except engine_client.EngineError as exc:
            raise HTTPException(status_code=502, detail=f"engine failed: {exc}") from exc
        except ValueError as exc:
            # Schema validation of the events failed: nothing was written.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if "store" in state:
            worker.submit(state.pop("store").close).result()
        worker.shutdown()

    app = FastAPI(title="Rescue triage field app", lifespan=lifespan)

    @app.exception_handler(InvalidInput)
    async def invalid_input(_request: Request, exc: InvalidInput) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.post("/requests")
    async def submit_request(request: Request) -> dict[str, Any]:
        item = parse_item(await _single_fields(request), "form")

        def run() -> list[dict[str, Any]]:
            return process_request(
                item.raw,
                item.channel,
                store=store(),
                extractor=extractor,
                decide=decide,
                config=pipeline_config(),
                contact=item.contact,
                lang_hint=item.lang_hint,
            )

        events = await on_worker(run)
        return {
            "request_id": events[0]["request_id"],
            "decision": events[2]["payload"]["engine_output"],
        }

    @app.post("/requests/bulk")
    async def submit_bulk(request: Request) -> dict[str, Any]:
        items = await _bulk_items(request)

        def run() -> list[list[dict[str, Any]]]:
            return process_batch(
                items,
                store=store(),
                extractor=extractor,
                decide_batch=decide_batch,
                config=pipeline_config(),
            )

        results = await on_worker(run)
        return {
            "count": len(results),
            "results": [
                {
                    "row": n,
                    "request_id": events[0]["request_id"],
                    "decision": events[2]["payload"]["engine_output"],
                }
                for n, events in enumerate(results, start=1)
            ],
        }

    return app


app = create_app()
