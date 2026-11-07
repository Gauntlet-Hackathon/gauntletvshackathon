"""Stored-baseline harness: score outputs produced ahead of time (Opus, a generic SLM), never live.

Input is a JSONL file, one row per case: ``{"id": "<case id>", "extracted": {<Extracted>}}``.
The stored extraction is replayed through the runner like any system; the decision is the real
fallback engine. Missing and malformed rows are counted and reported (``report()``), and show up
in the runner result's ``errors`` as scored extraction failures. Never silently dropped.

    STORED_OUTPUTS=evals/baselines/opus.jsonl python -m rescue_evals.runner --system stored
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rescue_evals.loaders import EvalCase
from rescue_evals.runner import register_system, run_set
from rescue_shared.models import Extracted

ENV_VAR = "STORED_OUTPUTS"


def load_stored(path: Path) -> tuple[dict[str, Extracted], dict[str, str]]:
    """Return (valid extractions by case id, malformed reasons by key).

    Malformed keys are the case id when known, else ``line<N>``.
    """
    good: dict[str, Extracted] = {}
    bad: dict[str, str] = {}
    for n, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        key = f"line{n}"
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("row is not a JSON object")
            if not isinstance(row.get("id"), str):
                raise ValueError("missing string 'id'")
            key = row["id"]
            if key in good or key in bad:
                raise ValueError("duplicate id")
            good[key] = Extracted.model_validate(row.get("extracted"))
        except (ValueError, ValidationError) as e:  # JSONDecodeError is a ValueError
            reason = str(e).splitlines()[0]
            bad[key] = reason
    return good, bad


class StoredSystem:
    """Replays stored extractions keyed by case id, then decides with the fallback engine."""

    def __init__(
        self,
        cases: Sequence[EvalCase],
        path: Path,
        name: str = "stored",
    ) -> None:
        self.name = name
        self.model_version = f"stored:{Path(path).name}"
        self._good, self._bad = load_stored(path)
        self._by_text = {c.raw_text: c.id for c in cases}
        self._case_ids = {c.id for c in cases}
        self._missing: set[str] = set()

    def extract(self, raw_text: str) -> Extracted:
        cid = self._by_text.get(raw_text)
        if cid is None:
            raise LookupError("text is not a known case")
        if cid in self._bad:
            raise ValueError(f"malformed stored row for {cid}: {self._bad[cid]}")
        if cid not in self._good:
            self._missing.add(cid)
            raise LookupError(f"missing stored row for {cid}")
        return self._good[cid]

    def decide(self, engine_input: dict[str, Any]) -> dict[str, Any]:
        from rescue_engine.fallback.engine import decide_one

        return decide_one(engine_input)

    def report(self) -> dict[str, Any]:
        missing = sorted(self._case_ids - set(self._good) - set(self._bad))
        return {
            "rows_ok": len(self._good),
            "malformed": len(self._bad),
            "malformed_ids": dict(self._bad),
            "missing": len(missing),
            "missing_ids": missing,
            "unused": sorted(set(self._good) - self._case_ids),
        }


def run_stored_set(
    cases: Sequence[EvalCase], set_name: str, path: Path, name: str = "stored"
) -> dict[str, Any]:
    """run_set plus a ``stored`` section with the missing/malformed counts."""
    system = StoredSystem(cases, path, name)
    result = run_set(system, cases, set_name)
    result["stored"] = system.report()
    return result


def _factory(cases: Sequence[EvalCase]) -> StoredSystem:
    path = os.environ.get(ENV_VAR)
    if not path:
        raise SystemExit(f"error: set {ENV_VAR} to the stored-outputs JSONL")
    return StoredSystem(cases, Path(path))


register_system("stored", _factory)
