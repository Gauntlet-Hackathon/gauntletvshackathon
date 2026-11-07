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
import logging
import os
import re
import sys
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from rescue_field import board as board_module
from rescue_field import boats as boats_module
from rescue_field import engine_client, freeze
from rescue_field import shell as shell_module
from rescue_field import status as status_module
from rescue_field import sync as sync_module
from rescue_field.pipeline import (
    BatchDecider,
    EngineInputError,
    FairLock,
    IntakeItem,
    PipelineConfig,
    extract_items,
    process_batch,
)
from rescue_field.sanitize import clean_text
from rescue_field.slm import Extractor, FixtureExtractor
from rescue_field.store import EventStore

CHANNELS = ("sms", "social", "form", "call_note", "csv")
SINGLE_DEFAULT_CHANNEL = "form"
BULK_DEFAULT_CHANNEL = "csv"
MAX_TEXT_CHARS = 5000
# A contact is a phone number, handle or short note; longer values once wedged the sync outbox
# (a single event over the cloud's 8 MB body limit, review O-01).
MAX_CONTACT_CHARS = 200
MAX_BULK_ROWS = 1000
# Request body caps (review F-02), answered with 413 before the body is parsed. Every route but
# bulk takes one small JSON object or form: 64 KB. A bulk upload is capped at 8 MB as well as at
# MAX_BULK_ROWS rows (1000 rows of 5000 ASCII characters is 5 MB); whichever limit is hit first
# applies. Read at request time, so tests can lower them.
MAX_BODY_BYTES = 64 * 1024
MAX_BULK_BODY_BYTES = 8 * 1024 * 1024
BULK_PATH = "/requests/bulk"
DEFAULT_DATA_DIR = "./data"
DB_FILENAME = "field.db"
_LANG_RE = re.compile(r"^[a-z]{2}$")
_REPO_ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger(__name__)


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


class _BodyTooLarge(Exception):
    """Raised from the wrapped `receive` once a streamed body passes the cap (never a
    ValueError, so no route's own error handling swallows it)."""


def _body_limit(path: str) -> int:
    return MAX_BULK_BODY_BYTES if path == BULK_PATH else MAX_BODY_BYTES


class BodySizeLimit:
    """ASGI middleware: 413 for a request body over the cap, judged by Content-Length when it
    is sent and by counting the chunks as they arrive otherwise (chunked uploads), so an
    oversized body is never read into memory whole."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = _body_limit(scope.get("path", ""))

        async def too_large() -> None:
            detail = f"request body is over {limit} bytes"
            await _error(413, "payload_too_large", detail)(scope, receive, send)

        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.strip().isdigit() and int(value) > limit:
                await too_large()
                return
        seen = 0
        started = False

        async def limited_receive() -> Any:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Any) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if started:
                raise
            await too_large()


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("FIELD_DATA_DIR") or DEFAULT_DATA_DIR)


def open_store(env: Mapping[str, str] | None = None) -> EventStore:
    """Open (creating if needed) `$FIELD_DATA_DIR/field.db`."""
    directory = data_dir(env)
    directory.mkdir(parents=True, exist_ok=True)
    return EventStore(directory / DB_FILENAME, check_same_thread=False)


def _info(extractor: Extractor, expected: str | None, reason: str | None) -> dict[str, Any]:
    return {
        "extractor": "fixture" if isinstance(extractor, FixtureExtractor) else "hf",
        "model_version": getattr(extractor, "model_version", "unknown"),
        "expected_model": expected,
        "fallback_reason": reason,
    }


def select_extractor_with_info(
    env: Mapping[str, str] | None = None,
) -> tuple[Extractor, dict[str, Any]]:
    """HFExtractor when HF_REPO and HF_REVISION are both set and it loads; else the fixture
    extractor. Never raises: the app must start whatever the model does. Also returns what was
    chosen and why (`app.state.extractor_info`), so a silent fallback is visible (F37)."""
    env = os.environ if env is None else env
    if not (env.get("HF_REPO") and env.get("HF_REVISION")):
        log.info("extractor: FixtureExtractor (HF_REPO/HF_REVISION not both set)")
        fixture = FixtureExtractor()
        return fixture, _info(fixture, None, None)
    expected = f"hf:{env['HF_REPO']}@{env['HF_REVISION']}"
    try:
        from rescue_field.slm_hf import HFExtractor

        chosen = HFExtractor()
    except Exception as exc:  # incl. ImportError when torch is not installed
        reason = f"{type(exc).__name__}: {exc}"
        log.error("extractor: FixtureExtractor (HFExtractor failed: %s)", reason)
        fixture = FixtureExtractor()
        return fixture, _info(fixture, expected, reason)
    log.info("extractor: HFExtractor %s", chosen.model_version)
    return chosen, _info(chosen, expected, None)


def select_extractor(env: Mapping[str, str] | None = None) -> Extractor:
    return select_extractor_with_info(env)[0]


def default_decide_batch(env: Mapping[str, str] | None = None) -> BatchDecider:
    """The engine subprocess in batch mode: `$ENGINE_CMD`, default `python -m
    rescue_engine.fallback` with engine/ and shared/ on PYTHONPATH."""
    env = dict(os.environ if env is None else env)
    cmd: str | list[str] = env.get("ENGINE_CMD") or [sys.executable, "-m", "rescue_engine.fallback"]
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
    return clean_text(value).strip() or None


def _item(fields: Mapping[str, Any], default_channel: str, row: int | None = None) -> IntakeItem:
    raw = fields.get("raw_text")
    if isinstance(raw, str):
        raw = clean_text(raw)  # no NUL or lone surrogate ever reaches the store (H-02)
    if not isinstance(raw, str) or not raw.strip():
        raise InputError("raw_text is required and must be non-empty text", row)
    if len(raw) > MAX_TEXT_CHARS:
        raise InputError(f"raw_text is longer than {MAX_TEXT_CHARS} characters", row)
    channel = _optional(fields, "channel", row) or default_channel
    if channel not in CHANNELS:
        raise InputError(f"channel must be one of {', '.join(CHANNELS)}", row)
    contact = _optional(fields, "contact", row)
    if contact is not None and len(contact) > MAX_CONTACT_CHARS:
        raise InputError(f"contact is longer than {MAX_CONTACT_CHARS} characters", row)
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
        # The model needs no store access, so it runs before the intake lock (review O-02),
        # one row at a time under its own lock: the board, status changes, the sync worker and
        # /sync/status are not held up for a whole bulk upload.
        extractions = extract_items(items, state.extractor, lock=state.extract_lock)
        # One intake at a time: each request must see the previous ones as duplicates.
        with state.intake_lock:
            return process_batch(
                items,
                store=state.store,
                extractor=state.extractor,
                decide_batch=state.decide_batch,
                config=state.config,
                extractions=extractions,
            )

    return await run_in_threadpool(work)


async def _intake(request: Request, items: list[IntakeItem]) -> list[dict[str, Any]] | JSONResponse:
    try:
        results = await _run(request, items)
    except engine_client.EngineError as exc:
        return _error(502, "engine_failed", str(exc))
    except EngineInputError as exc:
        return _error(422, "invalid_input", exc.detail, exc.row)
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


@router.get("/healthz")
async def healthz(request: Request) -> Any:
    """Liveness for the Fly health check: answered on the event loop, without the intake lock
    or the database, so a long board render or bulk upload never makes it time out (review
    M-04, O-02). 503 while the event store is not open."""
    if request.app.state.store is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    return {"status": "ok"}


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
        # Refuse to start when the decision config differs from the frozen engine build (R15).
        cfg = app.state.config
        freeze.check(cfg.thresholds, cfg.hoax_markers, cfg.streets)
        opened = None
        if app.state.store is None:
            opened = app.state.store = open_store()
        if extractor is None:  # none passed: pick at startup, never at import
            app.state.extractor, app.state.extractor_info = await run_in_threadpool(
                select_extractor_with_info
            )
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
    app.state.extractor = extractor or FixtureExtractor()  # replaced in the lifespan if none passed
    _env = os.environ
    app.state.extractor_info = _info(
        app.state.extractor,
        f"hf:{_env['HF_REPO']}@{_env['HF_REVISION']}"
        if _env.get("HF_REPO") and _env.get("HF_REVISION")
        else None,
        None,
    )
    app.state.decide_batch = decide_batch or default_decide_batch()
    app.state.config = config or PipelineConfig.from_env()
    app.state.intake_lock = threading.Lock()
    app.state.extract_lock = FairLock()  # one model call at a time, outside intake_lock
    app.state.sync_worker = None
    app.add_middleware(BodySizeLimit)
    app.include_router(router)
    app.include_router(sync_module.router)
    app.include_router(board_module.router)
    app.include_router(status_module.router)
    app.include_router(shell_module.router)
    app.include_router(boats_module.router)
    app.mount("/static", StaticFiles(directory=str(shell_module.STATIC_DIR)), name="static")
    return app


app = create_app()
