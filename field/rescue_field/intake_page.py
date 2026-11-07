"""GET /intake: the ECC intake simulator, one self-contained page (no external assets).

The page builds CSV from its log and posts it to this app's own POST /requests/bulk. The header
shows `ECC_NAME` (default `ECC 1`). Incident prefix and numbers are display-only.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

DEFAULT_ECC_NAME = "ECC 1"

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))


@router.get("/intake", response_class=HTMLResponse)
def intake_page(request: Request) -> HTMLResponse:
    ecc_name = (os.environ.get("ECC_NAME") or "").strip() or DEFAULT_ECC_NAME
    return templates.TemplateResponse(request, "intake.html", {"ecc_name": ecc_name})
