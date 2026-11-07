"""Volunteer app shell (UI-1): config, connection line and the context every app screen shares.

Demo values come from the environment (CLAUDE.md runtime contract); nothing here touches the engine.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from rescue_field import sync as sync_module
from rescue_field.screens.grader import grader_mode

STATIC_DIR = Path(__file__).with_name("static")
templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
router = APIRouter()

_FIRST = ("Jordan", "Sam", "Riley", "Avery", "Morgan", "Casey", "Taylor", "Quinn")
_LAST = "ABCDEFGHJKLMNPRSTW"
_SCHOOLS = ("Lincoln High", "Riverside Elementary", "Oak Park Community Hall")


def _pick(device_id: str, salt: str, n: int) -> int:
    return hashlib.sha256(f"{salt}:{device_id}".encode()).digest()[0] % n


def shell_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """ECC, volunteer and shelter names for the header. Blank values fall back to the defaults."""
    env = os.environ if env is None else env

    def get(key: str) -> str:
        return (env.get(key) or "").strip()

    device = get("DEVICE_ID") or "laptop-dev-01"
    name = get("VOLUNTEER_NAME") or (
        f"{_FIRST[_pick(device, 'first', len(_FIRST))]} {_LAST[_pick(device, 'last', len(_LAST))]}."
    )
    shelters = [s.strip() for s in get("SHELTER_NAMES").split(",") if s.strip()] or [
        f"Shelter {i} ({school})" for i, school in enumerate(_SCHOOLS, 1)
    ]
    return {
        "ecc_name": get("ECC_NAME") or "ECC 1",
        "volunteer_name": name,
        "volunteer_role": get("VOLUNTEER_ROLE") or "Volunteer",
        "shelter_names": shelters,
    }


def _ago(stamp: str | None, now: datetime | None = None) -> str | None:
    if not stamp:
        return None
    try:
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    secs = max(0, int(((now or datetime.now(UTC)) - then).total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def connection_line(badge: Mapping[str, Any], now: datetime | None = None) -> dict[str, str]:
    """The one-line connection state in words, from the /sync/badge context."""
    waiting = badge.get("backlog") or 0
    state = badge.get("state")
    if state == "online":
        ago = _ago(badge.get("last_sync_at"), now)
        return {"state": "online", "text": f"Online · last sync {ago}" if ago else "Online"}
    if state == "offline":
        text = f"No Internet: Local Store, Sync when online · {waiting} waiting"
        return {"state": "offline", "text": text}
    return {"state": "no-sync", "text": f"Not connected to the ECC · {waiting} waiting"}


def model_banner(request: Request) -> str | None:
    """Why the keyword fallback is in use (None when the real model is loaded)."""
    info = getattr(request.app.state, "extractor_info", None) or {}
    if info.get("expected_model") and info.get("extractor") != "hf":
        return info.get("fallback_reason") or "unknown"
    return None


def shell_context(request: Request) -> dict[str, Any]:
    """Blocking (takes the store lock): call from a worker thread."""
    ctx = shell_config()
    ctx["connection"] = connection_line(sync_module._badge_ctx(request))
    ctx["model_banner"] = model_banner(request)
    ctx["grader_mode"] = grader_mode(request)
    return ctx


@router.get("/app", response_class=HTMLResponse)
async def app_home(request: Request, mode: str = "queue") -> HTMLResponse:
    ctx = await run_in_threadpool(shell_context, request)
    ctx["mode"] = "respond" if mode == "respond" else "queue"
    return templates.TemplateResponse(request, "app/home.html", ctx)
