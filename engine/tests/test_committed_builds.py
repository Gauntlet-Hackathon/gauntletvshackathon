"""E9: every committed engine build in engine/dist/ is executed on the fixtures.

CI parity tests compile Mercury from source; nothing else ran the binary the Docker images ship.
`check_build` runs a build's entrypoint on contracts/fixtures/cases.jsonl and the valid example
and fails on a stale, corrupted or mis-stamped binary. Mercury builds are Linux x86_64 only.
"""

from __future__ import annotations

import json
import platform
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
