"""The runner CLI finds every registered system when run as `python -m rescue_evals.runner`."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    lanes = ("shared", "field", "engine", "evals")
    env["PYTHONPATH"] = os.pathsep.join(str(REPO / d) for d in lanes)
    env.pop("HF_REPO", None)
    env.pop("HF_REVISION", None)
    return subprocess.run(
        [sys.executable, "-m", "rescue_evals.runner", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
        timeout=120,
    )


def test_unknown_system_lists_every_registered_system(tmp_path):
    r = _run("--system", "nope", "--sets-dir", str(tmp_path))
    assert r.returncode == 2
    for name in ("gold-extraction", "ours", "stored", "regex-baseline"):
        assert name in r.stderr, r.stderr


def test_ours_is_found_under_python_m(tmp_path):
    # Found (not "unknown system"); it then stops on the empty sets dir before loading a model.
    r = _run("--system", "ours", "--sets", "B", "--sets-dir", str(tmp_path))
    assert "unknown system" not in r.stderr, r.stderr
    assert r.returncode == 1
    assert "no files for set B" in r.stderr
