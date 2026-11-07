"""The 'ours' column: the real HFExtractor plus the engine CLI, scored through the runner.

The extractor is imported from the field app (``rescue_field.slm_hf.HFExtractor``), not copied,
and the decision goes through the same subprocess client the field app uses (``ENGINE_CMD``,
default ``python -m rescue_engine.fallback`` with this repo's lane folders on PYTHONPATH).
``model_version`` comes from the extractor; ``rules_version`` is recorded by the runner from
the engine output.

    HF_REPO=... HF_REVISION=<sha> python -m rescue_evals.runner --system ours
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from rescue_evals.loaders import EvalCase
from rescue_evals.runner import REPO_ROOT, register_system
from rescue_shared.models import Extracted


def engine_env() -> dict[str, str]:
    """Environment for the engine subprocess: inherit, plus the lane folders on PYTHONPATH."""
    env = dict(os.environ)
    lanes = [str(REPO_ROOT / d) for d in ("engine", "shared")]
    if env.get("PYTHONPATH"):
        lanes.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(lanes)
    return env


class OursSystem:
    """HF-extractor + engine CLI. Both halves are injectable so tests need no model."""

    def __init__(
        self,
        extractor: Any,
        decide_fn: Callable[..., dict[str, Any]] | None = None,
        engine_cmd: str | Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
        name: str = "ours",
    ) -> None:
        self.name = name
        self._extractor = extractor
        self.model_version: str = extractor.model_version
        self._decide = decide_fn
        self._cmd = engine_cmd or os.environ.get("ENGINE_CMD") or [
            sys.executable,
            "-m",
            "rescue_engine.fallback",
        ]
        self._env = env

    def extract(self, raw_text: str) -> Extracted:
        result = self._extractor.extract(raw_text)
        return Extracted.model_validate(getattr(result, "payload", result))

    def decide(self, engine_input: dict[str, Any]) -> dict[str, Any]:
        if self._decide is None:
            from rescue_field.engine_client import decide

            return decide(engine_input, cmd=self._cmd, env=self._env or engine_env())
        return self._decide(engine_input)


def _factory(cases: Sequence[EvalCase]) -> OursSystem:
    from rescue_field.slm_hf import HFExtractor

    return OursSystem(HFExtractor())


register_system("ours", _factory)
