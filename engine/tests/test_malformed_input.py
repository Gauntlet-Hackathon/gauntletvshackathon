"""The fallback CLI never crashes: schema-valid output or exit 1 with one stderr line."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
VALID = json.loads((CONTRACTS / "examples" / "engine_input.valid.json").read_text())


def _validator() -> Draft202012Validator:
    resources = [
        (p.name, Resource.from_contents(json.loads(p.read_text())))
        for p in CONTRACTS.glob("*.schema.json")
    ]
    schema = json.loads((CONTRACTS / "engine_output.schema.json").read_text())
    return Draft202012Validator(schema, registry=Registry().with_resources(resources))


def run(stdin: bytes | str) -> subprocess.CompletedProcess[bytes]:
    data = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(ROOT / d) for d in ("engine", "shared"))}
    return subprocess.run(
        [sys.executable, "-m", "rescue_engine.fallback"],
        input=data, capture_output=True, env=env, timeout=60, check=False,
    )


def mutated(**changes: object) -> dict:
    doc = copy.deepcopy(VALID)
    for path, value in changes.items():
        node = doc
        *parents, last = path.split("__")
        for key in parents:
            node = node[key]
        node[last] = value
    return doc


def drop(*path: str) -> dict:
    doc = copy.deepcopy(VALID)
    node = doc
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return doc


BAD_STDIN = {
    "empty": "",
    "whitespace": "  \n\t ",
    "invalid_json": "{not json",
    "truncated_json": '{"extracted": {',
    "bad_utf8": b"\xff\xfe\x00{",
    "null": "null",
    "string": '"hello"',
    "number": "42",
    "list_of_non_objects": "[1, 2]",
    "empty_object": "{}",
    "emoji_text_only": "🌊🆘 help 🌊",
    "non_english_text_only": "ayuda, el agua sube 水が来ている",
    "huge_text_only": "x" * 20_000,
    "deeply_nested": "[" * 100_000,
}

BAD_DOCS = {
    "missing_extracted": drop("extracted"),
    "missing_is_request": drop("extracted", "is_request"),
    "missing_location": drop("extracted", "location"),
    "extracted_is_string": mutated(extracted="oops"),
    "extracted_is_list": mutated(extracted=[]),
    "is_request_is_string": mutated(extracted__is_request="yes"),
    "confidence_is_string": mutated(extracted__is_request={"value": True, "confidence": "high"}),
    "confidence_is_null": mutated(extracted__is_request={"value": True, "confidence": None}),
    "location_is_number": mutated(extracted__location=7),
    "hazards_is_string": mutated(extracted__hazards="rising_water"),
    "hazards_entries_numbers": mutated(extracted__hazards=[1, 2]),
    "vulnerable_is_dict": mutated(extracted__vulnerable={"kind": "child"}),
    "context_is_string": mutated(context="x"),
    "thresholds_is_list": mutated(context__thresholds=[1]),
    "thresholds_strings": mutated(context__thresholds={"request_confidence_min": "a"}),
    "candidates_not_list": mutated(context__duplicate_candidates=5),
    "candidates_entries_strings": mutated(context__duplicate_candidates=["a"]),
    "list_with_bad_item": [VALID, {"extracted": 1}],
}

EDGE_DOCS = {
    "emoji_location": mutated(extracted__location__text="🌊🌊🌊 on 🏠"),
    "non_english_location": mutated(extracted__location__text="ул. Ленина 5, 水が来ている"),
    "long_location_text": mutated(extracted__location__text="m" * 20_000),
    "empty_location_text": mutated(extracted__location__text=""),
    "empty_list": [],
    "list_of_valid": [VALID, VALID],
}


def assert_clean_error(proc: subprocess.CompletedProcess[bytes]) -> None:
    assert proc.returncode == 1
    assert proc.stdout == b""
    err = proc.stderr.decode("utf-8", "replace")
    assert err.startswith("rescue_engine.fallback: ")
    assert len(err.strip().splitlines()) == 1
    assert "Traceback" not in err


@pytest.mark.parametrize("stdin", BAD_STDIN.values(), ids=BAD_STDIN.keys())
def test_bad_stdin_gives_error_shape(stdin: bytes | str) -> None:
    assert_clean_error(run(stdin))


@pytest.mark.parametrize("doc", BAD_DOCS.values(), ids=BAD_DOCS.keys())
def test_malformed_documents_never_traceback(doc: object) -> None:
    proc = run(json.dumps(doc))
    if proc.returncode == 0:
        # Tolerated input still has to produce a valid decision.
        out = json.loads(proc.stdout)
        for item in out if isinstance(out, list) else [out]:
            assert not list(_validator().iter_errors(item))
    else:
        assert_clean_error(proc)


@pytest.mark.parametrize("doc", EDGE_DOCS.values(), ids=EDGE_DOCS.keys())
def test_edge_inputs_give_schema_valid_output(doc: object) -> None:
    proc = run(json.dumps(doc))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert isinstance(out, list) == isinstance(doc, list)
    validator = _validator()
    for item in out if isinstance(out, list) else [out]:
        assert not list(validator.iter_errors(item))


def test_valid_example_passes() -> None:
    proc = run(json.dumps(VALID))
    assert proc.returncode == 0
    assert not list(_validator().iter_errors(json.loads(proc.stdout)))
