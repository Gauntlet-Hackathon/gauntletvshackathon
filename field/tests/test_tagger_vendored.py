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
