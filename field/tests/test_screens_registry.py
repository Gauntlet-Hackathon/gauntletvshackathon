import sys
import types

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from rescue_field import screens


def test_include_all_registers_each_module_router(monkeypatch, tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "alpha.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.get('/alpha')\ndef a():\n    return {'ok': 'alpha'}\n"
    )
    (pkg / "_private.py").write_text("router = None\n")
    monkeypatch.setattr(screens, "__path__", [str(pkg)])
    monkeypatch.setattr(screens, "__name__", "fake_screens")
    fake = types.ModuleType("fake_screens")
    fake.__path__ = [str(pkg)]
    monkeypatch.setitem(sys.modules, "fake_screens", fake)
    app = FastAPI()
    assert screens.include_all(app) == ["alpha"]
    assert TestClient(app).get("/alpha").json() == {"ok": "alpha"}


def test_empty_package_registers_nothing(monkeypatch, tmp_path):
    # An empty package (the real one holds screens now, e.g. respond.py).
    monkeypatch.setattr(screens, "__path__", [str(tmp_path)])
    app = FastAPI()
    before = len(app.routes)
    screens.include_all(app)
    assert len(app.routes) == before
    assert isinstance(APIRouter(), APIRouter)
