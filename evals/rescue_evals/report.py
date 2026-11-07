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

# column key -> (header, accepted system names; the first is the canonical one)
COLUMNS: dict[str, tuple[str, list[str]]] = {
    "regex": ("Regex", ["regex-baseline"]),
    "opus": ("Opus 5.5", ["opus", "opus-5-5"]),
    "generic_slm": ("Generic SLM", ["slm", "generic-slm"]),
    "ours": ("Ours", ["ours"]),
}
# stored baselines replay saved outputs, so the runner's latency is not the model's (EV14 fixes it)
STORED_BASELINES = {"opus", "generic_slm"}
WORKS_OFFLINE = {"regex": 1, "opus": 0, "generic_slm": 1, "ours": 1}
ROW_MS = "running.mean_ms_per_request"
ROW_S1000 = "running.seconds_per_1000_requests"
ROW_OFFLINE = "running.works_offline"
ROW_CONSISTENCY = "consistency.pct_cases_changed"
OURS = "ours"
MAIN_SETS = ("A", "B", "C", "D")
SEALED_SET = "E"
LOWER_IS_BETTER = {
    "decision.p1_misses_per_1000",
    "decision.false_alarm_rate",
    ROW_MS,
    ROW_S1000,
    ROW_CONSISTENCY,
}
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


def _pool_latency(docs: list[dict[str, Any]], key: str) -> float | None:
    num = den = 0
    for d in docs:
        v = (d.get("latency") or {}).get(key)
        n = int(d.get("n") or 0)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            num += v * n
            den += n
    return num / den if den else None


def _consistency(results_dir: Path | None, col: str) -> float | None:
    if results_dir is None:
        return None
    for system in COLUMNS[col][1]:
        try:
            doc = json.loads((results_dir / f"consistency__{system}.json").read_text("utf-8"))
        except (OSError, ValueError):
            continue
        v = doc.get("pct_cases_changed") if isinstance(doc, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _row(metric: str, cells: dict[str, float | None], rank: bool = True) -> dict[str, Any]:
    vals = {c: v for c, v in cells.items() if v is not None}
    lower = metric in LOWER_IS_BETTER
    best: list[str] = []
    if vals and rank:
        best_v = (min if lower else max)(vals.values())
        best = sorted(c for c, v in vals.items() if abs(v - best_v) <= EPS)
    ours = cells.get(OURS)
    ours_loses = rank and ((ours is not None and OURS not in best) or (ours is None and bool(vals)))
    return {
        "metric": metric,
        "lower_is_better": lower,
        "values": cells,
        "best": best,
        "ours_loses": bool(ours_loses),
    }


def _block(
    docs: list[dict[str, Any]],
    sets: Sequence[str],
    results_dir: Path | None = None,
    extra: bool = False,
) -> dict[str, Any]:
    pooled: dict[str, dict[str, float]] = {}
    ns: dict[str, int] = {}
    versions: dict[str, Any] = {}
    mine_by_col: dict[str, list[dict[str, Any]]] = {}
    for col, (_, systems) in COLUMNS.items():
        mine = [d for d in docs if d["system"] in systems and d["set"].upper() in sets]
        if not mine:
            continue
        mine_by_col[col] = mine
        pooled[col], ns[col] = _pool(mine)
        versions[col] = {
            "model_version": mine[-1].get("model_version"),
            "rules_version": mine[-1].get("rules_version"),
        }
    metrics = sorted({m for col in pooled.values() for m in col})
    rows = [_row(m, {c: pooled.get(c, {}).get(m) for c in COLUMNS}) for m in metrics]
    if extra and pooled:

        def lat(key: str) -> dict[str, float | None]:
            return {
                c: None
                if c in STORED_BASELINES or c not in mine_by_col
                else _pool_latency(mine_by_col[c], key)
                for c in COLUMNS
            }

        rows.append(_row(ROW_MS, lat("total_ms_per_request")))
        rows.append(_row(ROW_S1000, lat("seconds_per_1000_requests")))
        rows.append(_row(ROW_OFFLINE, dict(WORKS_OFFLINE), rank=False))
        rows.append(_row(ROW_CONSISTENCY, {c: _consistency(results_dir, c) for c in COLUMNS}))
    return {"sets": list(sets), "n": ns, "versions": versions, "rows": rows}


def build_summary(docs: list[dict[str, Any]], results_dir: Path | None = None) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "columns": {c: {"label": h, "system": s[0], "aliases": s} for c, (h, s) in COLUMNS.items()},
        "main": _block(docs, MAIN_SETS, results_dir, extra=True),
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
            if r["metric"] == ROW_OFFLINE:
                s = "yes" if r["values"][c] else "no"
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
    summary = build_summary(load_results(results_dir), results_dir)
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
