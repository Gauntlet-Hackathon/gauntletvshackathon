"""Locate and load the files in contracts/, and build JSON Schema validators that resolve
cross-file $refs. Used by the contract tests of every lane (needs the `jsonschema` package)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "contracts"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def registry() -> Registry:
    reg = Registry()
    for path in sorted(CONTRACTS.rglob("*.schema.json")):
        schema = load_json(path)
        reg = reg.with_resource(schema["$id"], Resource.from_contents(schema, DRAFT202012))
    return reg


def validator_for(relative_path: str) -> Draft202012Validator:
    """Validator for a schema file, e.g. "payloads/received.schema.json"."""
    return Draft202012Validator(load_json(CONTRACTS / relative_path), registry=registry())
