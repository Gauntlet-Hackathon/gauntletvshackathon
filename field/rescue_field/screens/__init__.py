"""Screen routers that register themselves.

Put a new screen in its own module here (e.g. `screens/grader.py`) with a module-level
`router = APIRouter()`. `create_app` includes every module's router, in name order, so a new
screen never edits `api.py`. Parallel PRs that each added an `include_router` line to the same
block of `api.py` kept closing each other with merge conflicts.
"""

from __future__ import annotations

import importlib
import pkgutil

from fastapi import FastAPI


def include_all(app: FastAPI) -> list[str]:
    """Include the `router` of every module in this package; return the module names."""
    names = sorted(m.name for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_"))
    for name in names:
        module = importlib.import_module(f"{__name__}.{name}")
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router)
    return names
