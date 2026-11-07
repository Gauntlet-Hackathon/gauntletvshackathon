"""Dev-set error analysis: failures grouped by the output field that differs from gold.

    PYTHONPATH=shared:field:engine:evals python -m rescue_evals.error_analysis [--system NAME]

Runs a system (default ``gold-extraction``: gold extraction + the fallback engine; any registered
system, e.g. a mocked model, via ``--system``) on the ``dev`` split of sets A-D only and writes
``evals/reports/error_analysis.md``: a count per group with up to 3 examples each. The test split,
golden files and the sealed holdout (set E) are never read. The report has no timestamp, so the
same inputs give the same bytes.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rescue_evals.loaders import EvalCase, EvalSetError, load_cases
from rescue_evals.runner import DEFAULT_SETS_DIR, REPO_ROOT, SYSTEMS, System, run_set

DEFAULT_OUT = REPO_ROOT / "evals" / "reports" / "error_analysis.md"
MAX_EXAMPLES = 3
DEV_SETS = ("A", "B", "C", "D")
# only the sets that the dev split may come from; names containing these are never opened
_FORBIDDEN = ("test", "golden", "holdout", "sealed")


def load_dev_cases(sets_dir: Path, sets: Sequence[str] = DEV_SETS) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for f in sorted(Path(sets_dir).glob("*.jsonl")):
        if any(w in f.stem.lower() for w in _FORBIDDEN):
            continue
        for c in load_cases(f, split="dev"):
            if c.set in sets and c.split == "dev":
                cases.append(c)
    return sorted(cases, key=lambda c: (c.set, c.id))


def group_failures(result: dict[str, Any]) -> dict[str, list[str]]:
    """Group name (output field/metric) -> sorted failing case ids; empty groups dropped."""
    groups = {k: sorted(v) for k, v in result.get("failures", {}).items() if v}
    if result.get("errors"):
        groups["system_error"] = sorted(result["errors"])
    return dict(sorted(groups.items()))


def render(groups: dict[str, list[str]], cases: Sequence[EvalCase], system: str) -> str:
    by_id = {c.id: c for c in cases}
    lines = [
        "# Dev-set error analysis",
        "",
        f"System: `{system}`. Cases (dev split only): {len(cases)}.",
        "",
        "| Group (field that differs from gold) | Failures | Share of dev |",
        "|---|---:|---:|",
    ]
    n = len(cases) or 1
    for g, ids in groups.items():
        lines.append(f"| `{g}` | {len(ids)} | {100 * len(ids) / n:.1f}% |")
    if not groups:
        lines.append("| (none) | 0 | 0.0% |")
    for g, ids in groups.items():
        lines += ["", f"## `{g}` ({len(ids)})", ""]
        for i in ids[:MAX_EXAMPLES]:
            text = " ".join(by_id[i].raw_text.split()) if i in by_id else ""
            lines.append(f"- `{i}`: {text[:200]}")
    return "\n".join(lines) + "\n"


def analyse(system: System, cases: Sequence[EvalCase]) -> str:
    result = run_set(system, cases, "dev")
    return render(group_failures(result), cases, system.name)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dev-set error analysis report")
    ap.add_argument("--system", default="gold-extraction")
    ap.add_argument("--sets-dir", type=Path, default=DEFAULT_SETS_DIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    if args.system not in SYSTEMS:
        print(f"error: unknown system {args.system!r}; known: {sorted(SYSTEMS)}", file=sys.stderr)
        return 2
    try:
        cases = load_dev_cases(args.sets_dir)
    except EvalSetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    report = analyse(SYSTEMS[args.system](cases), cases)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"wrote {args.out} (n={len(cases)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
