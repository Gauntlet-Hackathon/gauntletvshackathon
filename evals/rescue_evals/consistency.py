"""Consistency: run every case N times; report the % of cases whose output changed at all.

    python -m rescue_evals.consistency --system <name> --sets A,B,C,D [--runs 10]

Compared per run: the extraction and the engine decision (``rules_version`` included). Writes
``<results-dir>/consistency__<system>.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rescue_evals.loaders import EvalCase, EvalSetError, load_cases
from rescue_evals.runner import (
    DEFAULT_RESULTS_DIR,
    DEFAULT_SETS_DIR,
    SYSTEMS,
    System,
    _dump,
    _set_files,
    default_context,
)


def _once(system: System, text: str) -> str:
    try:
        ex = _dump(system.extract(text))
    except Exception as e:
        return f"extract-error:{type(e).__name__}"
    try:
        out = system.decide({"extracted": ex, "context": default_context()})
    except Exception as e:
        return f"decide-error:{type(e).__name__}"
    return json.dumps({"extracted": ex, "decision": out}, sort_keys=True, default=str)


def run_consistency(system: System, cases: Sequence[EvalCase], runs: int = 10) -> dict[str, Any]:
    changed = [c.id for c in cases if len({_once(system, c.raw_text) for _ in range(runs)}) > 1]
    n = len(cases)
    return {
        "system": system.name,
        "runs": runs,
        "n": n,
        "cases_with_any_change": len(changed),
        "pct_cases_changed": 100 * len(changed) / n if n else 0.0,
        "changed_ids": changed,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rerun every case N times; % of cases that change")
    ap.add_argument("--system", required=True)
    ap.add_argument("--sets", default="A,B,C,D")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--sets-dir", type=Path, default=DEFAULT_SETS_DIR)
    ap.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    ap.add_argument("--open-sealed", action="store_true")
    args = ap.parse_args(argv)
    if args.system not in SYSTEMS:
        print(f"error: unknown system {args.system!r}; known: {sorted(SYSTEMS)}", file=sys.stderr)
        return 2
    try:
        cases: list[EvalCase] = []
        for s in [x.strip().upper() for x in args.sets.split(",") if x.strip()]:
            files = _set_files(args.sets_dir, s)
            if not files:
                print(f"error: no files for set {s} in {args.sets_dir}", file=sys.stderr)
                return 1
            cases += [
                c
                for f in files
                for c in load_cases(f, open_sealed=args.open_sealed, split="test")
                if c.set == s
            ]
    except EvalSetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    res = run_consistency(SYSTEMS[args.system](cases), cases, args.runs)
    args.results_dir.mkdir(parents=True, exist_ok=True)
    path = args.results_dir / f"consistency__{res['system']}.json"
    path.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path} ({res['pct_cases_changed']:.1f}% of {res['n']} cases changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
