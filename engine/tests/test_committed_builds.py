"""E9: every committed engine build in engine/dist/ is executed on the fixtures.

CI parity tests compile Mercury from source; nothing else ran the binary the Docker images ship.
`check_build` runs a build's entrypoint on contracts/fixtures/cases.jsonl and the valid example
and fails on a stale, corrupted or mis-stamped binary. Mercury builds are Linux x86_64 only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from rescue_engine.fallback import decide
from rescue_shared.contracts_io import validator_for

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "engine/dist"
CASES = [
    json.loads(line)
    for line in (ROOT / "contracts/fixtures/cases.jsonl").read_text().splitlines()
    if line.strip()
]
EXAMPLE = json.loads((ROOT / "contracts/examples/engine_input.valid.json").read_text())
MANIFESTS = sorted(
    m
    for m in DIST.glob("*/manifest.json")
    if json.loads(m.read_text()).get("engine_kind") == "mercury"
)
SUPPORTED = platform.system() == "Linux" and platform.machine() == "x86_64"


def _run(binary: Path, payload) -> list[dict]:
    proc = subprocess.run(
        [str(binary)], input=json.dumps(payload), capture_output=True, text=True,
        timeout=120, check=False,
    )
    assert proc.returncode == 0, f"{binary} exited {proc.returncode}: {proc.stderr[:300]}"
    out = json.loads(proc.stdout)
    return out if isinstance(out, list) else [out]


def _strip(output: dict) -> dict:
    return {k: v for k, v in output.items() if k != "rules_version"}


def check_build(dist_dir: Path) -> None:
    """Raise AssertionError if the build in dist_dir misbehaves on the fixtures."""
    manifest = json.loads((dist_dir / "manifest.json").read_text())
    binary = dist_dir / manifest["entrypoint"]
    stamped = manifest["rules_version"]

    inputs = [{"extracted": c["extracted"], "context": c["context"]} for c in CASES]
    outputs = _run(binary, inputs)
    assert len(outputs) == len(CASES)
    for case, inp, out in zip(CASES, inputs, outputs, strict=True):
        assert out["rules_version"] == stamped, case["case_id"]
        assert _strip(out) == _strip(case["expected"]), f"{case['case_id']}: differs from expected"
        assert _strip(out) == _strip(decide(inp)), f"{case['case_id']}: differs from fallback"

    example = _run(binary, EXAMPLE)
    assert len(example) == 1
    validator_for("engine_output.schema.json").validate(example[0])
    assert example[0]["rules_version"] == stamped
    assert _strip(example[0]) == _strip(decide(EXAMPLE))


def test_there_is_a_committed_mercury_build():
    assert MANIFESTS, "no engine_kind=mercury manifest under engine/dist/"


@pytest.mark.skipif(not SUPPORTED, reason="committed Mercury builds run on Linux x86_64 only")
@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda m: m.parent.name[:30])
def test_committed_build_runs_on_fixtures(manifest: Path):
    check_build(manifest.parent)


@pytest.mark.skipif(not SUPPORTED, reason="committed Mercury builds run on Linux x86_64 only")
@pytest.mark.parametrize("mode", ["truncate", "restamp"])
def test_check_build_rejects_corrupt_copy(tmp_path: Path, mode: str):
    src = MANIFESTS[0].parent
    copy = tmp_path / src.name
    shutil.copytree(src, copy)
    manifest = json.loads((copy / "manifest.json").read_text())
    if mode == "truncate":
        binary = copy / manifest["entrypoint"]
        binary.write_bytes(binary.read_bytes()[: binary.stat().st_size // 2])
    else:
        manifest["rules_version"] = manifest["rules_version"][:-1] + "0"
        (copy / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises((AssertionError, OSError)):
        check_build(copy)


# --- review PR-9 (F-08, L-13): the shipped binary on the parity inputs -------------------------


def _runnable(binary: Path) -> bool:
    """False when this machine cannot execute the binary at all (wrong libc or loader)."""
    try:
        proc = subprocess.run(
            [str(binary)], input="[]", capture_output=True, text=True, timeout=60, check=False
        )
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "[]"


@pytest.fixture(scope="module")
def shipped() -> Path:
    if not SUPPORTED or not MANIFESTS:
        pytest.skip("committed Mercury builds run on Linux x86_64 only")
    dist_dir = MANIFESTS[-1].parent
    binary = dist_dir / json.loads((dist_dir / "manifest.json").read_text())["entrypoint"]
    if not _runnable(binary):
        if os.environ.get("CI"):
            pytest.fail(f"{binary} does not run on the CI runner")
        pytest.skip(f"{binary} does not run on this machine")
    return binary


def _parity_module():
    """test_parity.py's input generators (tests are not importable as modules)."""
    spec = importlib.util.spec_from_file_location(
        "parity_inputs", Path(__file__).with_name("test_parity.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shipped_binary_matches_fallback_beyond_the_fixtures(shipped: Path):
    """The frozen binary (not a fresh compile) agrees with the Python fallback on the E4 grid,
    3,000 random schema-valid inputs and the review-fix inputs, so a binary that is stale
    against the rules fails here even where CI has no Mercury toolchain."""
    m = _parity_module()
    rng = random.Random(66)
    for name, inputs in (
        ("e4_grid", m.load_e4_grid()),
        ("random", [m._random_input(rng) for _ in range(3000)]),
        ("review_fixes", m.load_review_inputs()),
    ):
        outs = [_strip(o) for o in _run(shipped, inputs)]
        expected = [_strip(o) for o in decide(inputs)]
        assert len(outs) == len(inputs), name
        bad = [i for i, (o, e) in enumerate(zip(outs, expected, strict=True)) if o != e]
        assert not bad, f"{name}: {len(bad)} differ, first {inputs[bad[0]]}"


@pytest.mark.parametrize("stdin", ["", "{", "[1, 2]", '{"extracted": {}}'])
def test_shipped_binary_rejects_bad_input_with_one_stderr_line(shipped: Path, stdin: str):
    proc = subprocess.run(
        [str(shipped)], input=stdin, capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 1 and proc.stdout == ""
    lines = proc.stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith("rescue_engine.mercury: ")
