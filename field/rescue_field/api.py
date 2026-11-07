"""Field FastAPI app: intake endpoints (POST /requests, POST /requests/bulk).

`app` is what the Dockerfile serves (`uvicorn rescue_field.api:app`). The event store lives in
`$FIELD_DATA_DIR/field.db` (default `./data`) and is opened in the lifespan, so importing this
module touches no files. Later field modules (board, status, sync) add their routers in
`create_app`. When `CLOUD_URL` is set and a sync worker factory is given, the worker starts in
the lifespan and stops on shutdown.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shlex
import sys
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from rescue_field import board as board_module
from rescue_field import engine_client
from rescue_field import status as status_module
from rescue_field import sync as sync_module
from rescue_field.pipeline import BatchDecider, IntakeItem, PipelineConfig, process_batch
from rescue_field.slm import Extractor, FixtureExtractor
from rescue_field.store import EventStore

CHANNELS = ("sms", "social", "form", "call_note", "csv")
SINGLE_DEFAULT_CHANNEL = "form"
BULK_DEFAULT_CHANNEL = "csv"
MAX_TEXT_CHARS = 5000
MAX_BULK_ROWS = 1000
DEFAULT_DATA_DIR = "./data"
DB_FILENAME = "field.db"
_LANG_RE = re.compile(r"^[a-z]{2}$")
_REPO_ROOT = Path(__file__).resolve().parents[2]


class SyncWorkerLike(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


SyncWorkerFactory = Callable[[EventStore, str], SyncWorkerLike]


class InputError(Exception):
    """Bad client input: answered with 422 before anything is processed."""

    def __init__(self, detail: str, row: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.row = row


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("FIELD_DATA_DIR") or DEFAULT_DATA_DIR)


def open_store(env: Mapping[str, str] | None = None) -> EventStore:
    """Open (creating if needed) `$FIELD_DATA_DIR/field.db`."""
    directory = data_dir(env)
    directory.mkdir(parents=True, exist_ok=True)
    return EventStore(directory / DB_FILENAME, check_same_thread=False)


def default_decide_batch(env: Mapping[str, str] | None = None) -> BatchDecider:
    """The engine subprocess in batch mode: `$ENGINE_CMD`, default `python -m
    rescue_engine.fallback` with engine/ and shared/ on PYTHONPATH."""
    env = dict(os.environ if env is None else env)
    cmd = env.get("ENGINE_CMD") or f"{shlex.quote(sys.executable)} -m rescue_engine.fallback"
    paths = [str(_REPO_ROOT / "engine"), str(_REPO_ROOT / "shared")]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return partial(engine_client.decide_batch, cmd=cmd, env=env)


# ---- input parsing ---------------------------------------------------------------------


def _optional(fields: Mapping[str, Any], key: str, row: int | None) -> str | None:
    value = fields.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputError(f"{key} must be a string", row)
    return value.strip() or None


def _item(fields: Mapping[str, Any], default_channel: str, row: int | None = None) -> IntakeItem:
    raw = fields.get("raw_text")
    if not isinstance(raw, str) or not raw.strip():
        raise InputError("raw_text is required and must be non-empty text", row)
    if len(raw) > MAX_TEXT_CHARS:
        raise InputError(f"raw_text is longer than {MAX_TEXT_CHARS} characters", row)
    channel = _optional(fields, "channel", row) or default_channel
    if channel not in CHANNELS:
        raise InputError(f"channel must be one of {', '.join(CHANNELS)}", row)
    contact = _optional(fields, "contact", row)
    lang_hint = _optional(fields, "lang_hint", row)
    if lang_hint is not None:
        lang_hint = lang_hint.lower()
        if not _LANG_RE.fullmatch(lang_hint):
            raise InputError("lang_hint must be a two-letter language code", row)
    return IntakeItem(raw=raw, channel=channel, contact=contact, lang_hint=lang_hint)


def _content_type(request: Request) -> str:
    return request.headers.get("content-type", "").split(";")[0].strip().lower()


def _is_form(ctype: str) -> bool:
    return ctype in ("application/x-www-form-urlencoded", "multipart/form-data")


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        body = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InputError(f"body is not valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise InputError("JSON body must be an object")
    return body


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError("upload must be UTF-8 text") from exc


def _paste_items(text: str, channel: str) -> list[IntakeItem]:
    """One request per non-blank line; rows are numbered by line."""
    return [
        _item({"raw_text": line.strip(), "channel": channel}, channel, row=n)
        for n, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    ]


def _csv_items(text: str, channel: str) -> list[IntakeItem]:
    """CSV with a header row: raw_text (or text) required; channel, contact, lang_hint
    optional. Rows are numbered by data row (the header is not counted)."""
    try:
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if header is None:
            raise InputError("CSV is empty")
        names = [h.strip().lower() for h in header]
        if "raw_text" not in names and "text" in names:
            names[names.index("text")] = "raw_text"
        if "raw_text" not in names:
            raise InputError("CSV header needs a raw_text column")
        items = []
        for n, values in enumerate(reader, start=1):
            if not any(v.strip() for v in values):
                continue
            fields = {k: v for k, v in zip(names, values, strict=False) if k}
            items.append(_item(fields, channel, row=n))
    except csv.Error as exc:
        raise InputError(f"CSV is malformed: {exc}") from exc
    return items


async def _single_input(request: Request) -> IntakeItem:
    ctype = _content_type(request)
    if ctype == "application/json":
        return _item(await _json_object(request), SINGLE_DEFAULT_CHANNEL)
    if _is_form(ctype):
        form = await request.form()
        fields = {k: v for k, v in form.items() if isinstance(v, str)}
        return _item(fields, SINGLE_DEFAULT_CHANNEL)
    raise InputError("send JSON or a form (application/json, x-www-form-urlencoded, multipart)")


async def _bulk_input(request: Request) -> list[IntakeItem]:
    ctype = _content_type(request)
    if _is_form(ctype):
        form = await request.form()
        channel = _optional(form, "channel", None) or BULK_DEFAULT_CHANNEL
        upload = form.get("file")
        text = form.get("text")
        if isinstance(upload, UploadFile):
            items = _csv_items(_decode(await upload.read()), channel)
        elif isinstance(text, str):
            items = _paste_items(text, channel)
        else:
            raise InputError("send a CSV file in field 'file' or pasted lines in field 'text'")
    elif ctype == "text/csv":
        items = _csv_items(_decode(await request.body()), BULK_DEFAULT_CHANNEL)
    elif ctype == "text/plain":
        items = _paste_items(_decode(await request.body()), BULK_DEFAULT_CHANNEL)
    elif ctype == "application/json":
        body = await _json_object(request)
        text = body.get("text")
        if not isinstance(text, str):
            raise InputError("JSON body needs 'text' with one request per line")
        channel = _optional(body, "channel", None) or BULK_DEFAULT_CHANNEL
        items = _paste_items(text, channel)
    else:
        raise InputError("send multipart (file or text), text/csv, text/plain or JSON")
    if not items:
        raise InputError("no requests found in the upload")
    if len(items) > MAX_BULK_ROWS:
        raise InputError(f"at most {MAX_BULK_ROWS} rows per upload")
    return items


# ---- endpoints -------------------------------------------------------------------------


def _error(status: int, code: str, detail: str, row: int | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": code, "detail": detail}
    if row is not None:
        body["row"] = row
    return JSONResponse(body, status_code=status)


async def _run(request: Request, items: list[IntakeItem]) -> list[list[dict[str, Any]]]:
    state = request.app.state

    def work() -> list[list[dict[str, Any]]]:
        if state.store is None:
            raise RuntimeError("event store is not open (app lifespan not running)")
        # One intake at a time: each request must see the previous ones as duplicates.
        with state.intake_lock:
            return process_batch(
                items,
                store=state.store,
                extractor=state.extractor,
                decide_batch=state.decide_batch,
                config=state.config,
            )

    return await run_in_threadpool(work)


async def _intake(request: Request, items: list[IntakeItem]) -> list[dict[str, Any]] | JSONResponse:
    try:
        results = await _run(request, items)
    except engine_client.EngineError as exc:
        return _error(502, "engine_failed", str(exc))
    except ValueError as exc:  # an event failed schema validation
        return _error(422, "invalid_input", str(exc))
    return [
        {
            "request_id": events[0]["request_id"],
            "received_at": events[0]["occurred_at"],
            "decision": events[2]["payload"]["engine_output"],
        }
        for events in results
    ]


router = APIRouter()


@router.post("/requests")
async def create_request(request: Request) -> Any:
    """One request as JSON or a form: raw_text (required), channel (default form), contact,
    lang_hint. Returns the engine decision."""
    try:
        item = await _single_input(request)
    except InputError as exc:
        return _error(422, "invalid_input", exc.detail)
    out = await _intake(request, [item])
    return out if isinstance(out, JSONResponse) else out[0]


@router.post("/requests/bulk")
async def create_bulk(request: Request) -> Any:
    """A CSV upload or multi-line paste. Rows are processed in order, so earlier rows are
    duplicate candidates for later ones. Any bad row rejects the whole upload (422)."""
    try:
        items = await _bulk_input(request)
    except InputError as exc:
        return _error(422, "invalid_input", exc.detail, exc.row)
    out = await _intake(request, items)
    if isinstance(out, JSONResponse):
        return out
    return {"count": len(out), "results": [{"row": i, **r} for i, r in enumerate(out, 1)]}


def create_app(
    *,
    store: EventStore | None = None,
    extractor: Extractor | None = None,
    decide_batch: BatchDecider | None = None,
    config: PipelineConfig | None = None,
    sync_worker_factory: SyncWorkerFactory | None = None,
) -> FastAPI:
    """Build the field app. A given store must allow cross-thread use
    (`check_same_thread=False`); without one, `$FIELD_DATA_DIR/field.db` opens at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        opened = None
        if app.state.store is None:
            opened = app.state.store = open_store()
        cloud_url = os.environ.get("CLOUD_URL")
        worker = None
        if cloud_url:
            factory = sync_worker_factory or (
                lambda st, url: sync_module.worker_from_env(
                    st,
                    url,
                    model_version=getattr(app.state.extractor, "model_version", "unknown"),
                    lock=app.state.intake_lock,
                )
            )
            worker = factory(app.state.store, cloud_url)
            worker.start()
        app.state.sync_worker = worker
        try:
            yield
        finally:
            if worker is not None:
                worker.stop()
            app.state.sync_worker = None
            if opened is not None:
                opened.close()
                app.state.store = None

    app = FastAPI(title="Rescue triage field app", lifespan=lifespan)
    app.state.store = store
    app.state.extractor = extractor or FixtureExtractor()
    app.state.decide_batch = decide_batch or default_decide_batch()
    app.state.config = config or PipelineConfig.from_env()
    app.state.intake_lock = threading.Lock()
    app.state.sync_worker = None
    app.include_router(router)
    app.include_router(sync_module.router)
    app.include_router(board_module.router)
    app.include_router(status_module.router)
    return app


app = create_app()
