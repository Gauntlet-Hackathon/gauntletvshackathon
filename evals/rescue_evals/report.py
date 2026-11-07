"""Results table + eval-results publisher.

    python -m rescue_evals.report [--results-dir DIR]

Reads ``<system>__<set>.json`` files (written by the runner) and writes, next to them:

* ``summary.json``: the feed the cloud panel (C10/C11) reads.
* ``TABLE.md``: the one table the judges see.

Rows are metrics, columns are regex / Opus 5.5 / generic SLM / ours. Sets A-D are pooled
(weighted by ``n``) into the main table; the sealed holdout (set E) is reported separately,
never pooled.
The best cell per row is marked ``**bold**`` (ties all marked); rows where "ours" loses are kept
and flagged. A system with no results for a row shows ``n/a``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rescue_evals.runner import DEFAULT_RESULTS_DIR

# column key -> (header, runner system name)
COLUMNS: dict[str, tuple[str, str]] = {
    "regex": ("Regex", "regex-baseline"),
    "opus": ("Opus 5.5", "opus-5-5"),
    "generic_slm": ("Generic SLM", "generic-slm"),
    "ours": ("Ours", "ours"),
}
OURS = "ours"
MAIN_SETS = ("A", "B", "C", "D")
SEALED_SET = "E"
LOWER_IS_BETTER = {"decision.p1_misses_per_1000", "decision.false_alarm_rate"}
EPS = 1e-9


def load_results(results_dir: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(results_dir.glob("*__*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and {"system", "set", "metrics"} <= doc.keys():
            out.append(doc)
    return out


def _pool(docs: list[dict[str, Any]]) -> tuple[dict[str, float], int]:
    """n-weighted mean per metric over the given result docs."""
    num: dict[str, float] = {}
    den: dict[str, int] = {}
    for d in docs:
        n = int(d.get("n") or 0)
        for k, v in d["metrics"].items():
            if isinstance(v, (int, float)):
                num[k] = num.get(k, 0.0) + v * n
                den[k] = den.get(k, 0) + n
    return {k: num[k] / den[k] for k in num if den[k]}, sum(int(d.get("n") or 0) for d in docs)


def _block(docs: list[dict[str, Any]], sets: Sequence[str]) -> dict[str, Any]:
    pooled: dict[str, dict[str, float]] = {}
    ns: dict[str, int] = {}
    versions: dict[str, Any] = {}
    for col, (_, system) in COLUMNS.items():
        mine = [d for d in docs if d["system"] == system and d["set"].upper() in sets]
        if not mine:
            continue
        pooled[col], ns[col] = _pool(mine)
        versions[col] = {
            "model_version": mine[-1].get("model_version"),
            "rules_version": mine[-1].get("rules_version"),
        }
    metrics = sorted({m for col in pooled.values() for m in col})
    rows = []
    for m in metrics:
        cells = {c: pooled.get(c, {}).get(m) for c in COLUMNS}
        vals = {c: v for c, v in cells.items() if v is not None}
        lower = m in LOWER_IS_BETTER
        best_v = (min if lower else max)(vals.values())
        best = sorted(c for c, v in vals.items() if abs(v - best_v) <= EPS)
        ours = cells.get(OURS)
        ours_loses = ours is not None and OURS not in best
        rows.append(
            {
                "metric": m,
                "lower_is_better": lower,
                "values": cells,
                "best": best,
                "ours_loses": ours_loses or (ours is None and bool(vals)),
            }
        )
    return {"sets": list(sets), "n": ns, "versions": versions, "rows": rows}


def build_summary(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "columns": {c: {"label": h, "system": s} for c, (h, s) in COLUMNS.items()},
        "main": _block(docs, MAIN_SETS),
        "sealed": _block(docs, (SEALED_SET,)),
        "results": [
            {k: d.get(k) for k in ("system", "set", "run_at", "n", "model_version", "metrics")}
            for d in docs
        ],
    }


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def _table(block: dict[str, Any]) -> list[str]:
    if not block["rows"]:
        return ["_No results yet._", ""]
    head = ["Metric", *(h for h, _ in COLUMNS.values()), "Ours vs best"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in block["rows"]:
        cells = []
        for c in COLUMNS:
            s = _fmt(r["values"][c])
            cells.append(f"**{s}**" if c in r["best"] else s)
        arrow = "lower is better" if r["lower_is_better"] else "higher is better"
        flag = "LOSES" if r["ours_loses"] else "ok"
        lines.append(f"| `{r['metric']}` ({arrow}) | " + " | ".join(cells) + f" | {flag} |")
    lines.append("")
    return lines


def render_table(summary: dict[str, Any]) -> str:
    note = "Best cell per row in **bold**. Rows where ours loses are flagged."
    lines = ["# Results", "", note, ""]
    for key, title in (("main", "Development sets A-D"), ("sealed", "Sealed holdout (set E)")):
        block = summary[key]
        lines += [f"## {title}", ""]
        if block["n"]:
            lines += ["n: " + ", ".join(f"{c}={n}" for c, n in block["n"].items()), ""]
        lines += _table(block)
    return "\n".join(lines)


def publish(results_dir: Path) -> dict[str, Any]:
    summary = build_summary(load_results(results_dir))
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (results_dir / "TABLE.md").write_text(render_table(summary), encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build TABLE.md and summary.json from results")
    ap.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    args = ap.parse_args(argv)
    s = publish(args.results_dir)
    print(f"wrote summary.json and TABLE.md ({len(s['main']['rows'])} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
