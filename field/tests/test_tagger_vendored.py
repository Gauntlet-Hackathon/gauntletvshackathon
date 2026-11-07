"""Torch-free guard: local edits in the vendored tagger/infer.py must survive re-vendoring."""

import ast
from pathlib import Path

INFER = Path(__file__).resolve().parents[1] / "rescue_field" / "tagger" / "infer.py"


def _source() -> str:
    return INFER.read_text(encoding="utf-8")


def test_header_lists_local_changes():
    assert "Local changes" in "\n".join(_source().splitlines()[:6])


def test_extractor_assigns_calibrated():
    tree = ast.parse(_source())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Extractor")
    assigned = [
        t.attr
        for n in ast.walk(cls)
        if isinstance(n, (ast.Assign, ast.AnnAssign))
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
        if isinstance(t, ast.Attribute)
    ]
    assert "calibrated" in assigned


def test_assemble_location_imported_and_called():
    tree = ast.parse(_source())
    imported = any(
        isinstance(n, ast.ImportFrom) and any(a.name == "assemble_location" for a in n.names)
        for n in ast.walk(tree)
    )
    called = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "assemble_location"
        for n in ast.walk(tree)
    )
    assert imported and called


def test_load_rejects_non_positive_or_non_finite_temperatures():
    # A calibration temperature of 0 made every text "not a request" with confidence 1.0.
    tree = ast.parse(_source())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Extractor")
    load = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "load")
    calls = {
        f"{n.func.value.id}.{n.func.attr}"
        for n in ast.walk(load)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
    }
    assert "math.isfinite" in calls
    compares = [
        n for n in ast.walk(load)
        if isinstance(n, ast.Compare) and any(isinstance(o, ast.LtE) for o in n.ops)
    ]
    assert compares, "load() must drop temperatures <= 0"
