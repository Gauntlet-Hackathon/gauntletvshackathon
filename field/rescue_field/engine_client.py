"""Subprocess client for the decision engine.

ENGINE_CMD is any command that reads one engine_input JSON object on stdin and writes one
engine_output JSON object on stdout (the Python engine and Mercury are interchangeable).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Sequence
from typing import Any

from rescue_shared.contracts_io import validator_for

DEFAULT_TIMEOUT_S = 5.0


class EngineError(Exception):
    """The engine failed, timed out, or returned something invalid."""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(f"{message}: {stderr.strip()}" if stderr.strip() else message)
        self.stderr = stderr


def _command(cmd: str | Sequence[str] | None) -> list[str]:
    if cmd is None:
        cmd = os.environ.get("ENGINE_CMD", "")
    argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    if not argv:
        raise EngineError("ENGINE_CMD is not set")
    return argv


def decide(
    engine_input: dict[str, Any],
    *,
    cmd: str | Sequence[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Run the engine on one input and return the validated engine_output."""
    argv = _command(cmd)
    try:
        proc = subprocess.run(
            argv,
            input=json.dumps(engine_input),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        raise EngineError(f"engine timed out after {timeout}s", stderr or "") from exc
    except OSError as exc:
        raise EngineError(f"cannot run engine {argv[0]!r}: {exc}") from exc
    if proc.returncode != 0:
        raise EngineError(f"engine exited with code {proc.returncode}", proc.stderr)
    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise EngineError(f"engine stdout is not JSON: {exc}", proc.stderr) from exc
    errors = list(validator_for("engine_output.schema.json").iter_errors(output))
    if errors:
        msg = f"engine output failed schema validation: {errors[0].message}"
        raise EngineError(msg, proc.stderr)
    return output


def decide_batch(
    engine_inputs: Sequence[dict[str, Any]],
    *,
    cmd: str | Sequence[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Decide each input in order; the result list matches the input order."""
    return [decide(item, cmd=cmd, timeout=timeout) for item in engine_inputs]
