"""Eval runner: scores a system at two points, SLM extraction and the final decision.

    python -m rescue_evals.runner --system <name> --sets A,B,C,D [--results-dir DIR]

Writes ``<results-dir>/<system>__<set>.json`` (format in docs/DECISIONS.md: ``system``, ``set``,
``run_at``, ``n``, ``metrics`` name -> number) plus ``model_version``, ``rules_version``,
``failures`` (metric name -> failing case ids, for the C11 drill-down) and ``latency``.
Only the ``test`` split is scored. Set E needs ``--open-sealed``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from rescue_evals import metrics as M
from rescue_evals.loaders import EvalCase, EvalSetError, load_cases
from rescue_shared.models import Extracted

log = logging.getLogger("rescue_evals.runner")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETS_DIR = REPO_ROOT / "evals" / "sets"
DEFAULT_RESULTS_DIR = REPO_ROOT / "evals" / "results"


class System(Protocol):
    """Anything scoreable: an extractor (raw text -> extracted) plus a decision step."""

    name: str
    model_version: str

    def extract(self, raw_text: str) -> Extracted | dict[str, Any]: ...

    def decide(self, engine_input: dict[str, Any]) -> dict[str, Any]: ...


SystemFactory = Callable[[Sequence[EvalCase]], System]
SYSTEMS: dict[str, SystemFactory] = {}


def register_system(name: str, factory: SystemFactory) -> None:
    SYSTEMS[name] = factory


@dataclass
class GoldExtractionSystem:
    """Reference system: returns the gold extraction, then the real fallback engine."""

    cases: Sequence[EvalCase]
    name: str = "gold-extraction"
    model_version: str = "stub:gold-extraction"

    def extract(self, raw_text: str) -> Extracted:
        for c in self.cases:
            if c.raw_text == raw_text and c.gold_extracted is not None:
                return c.gold_extracted
        raise LookupError("no gold extraction for this text")

    def decide(self, engine_input: dict[str, Any]) -> dict[str, Any]:
        from rescue_engine.fallback.engine import decide_one

        return decide_one(engine_input)


register_system("gold-extraction", lambda cases: GoldExtractionSystem(cases))


def default_context() -> dict[str, Any]:
    from rescue_engine.fallback.rules import DEFAULT_THRESHOLDS

    return {
        "duplicate_candidates": [],
        "street_list_hits": [],
        "hoax_marker_hits": 0,
        "thresholds": dict(DEFAULT_THRESHOLDS),
    }


def _dump(extracted: Extracted | dict[str, Any]) -> dict[str, Any]:
    if isinstance(extracted, Extracted):
        return extracted.model_dump(mode="json")
    return Extracted.model_validate(extracted).model_dump(mode="json")


# --- scoring -----------------------------------------------------------------------------------


def _frac(flags: dict[str, bool]) -> tuple[float, list[str]]:
    n = len(flags)
    return (sum(flags.values()) / n if n else 0.0), [i for i, ok in flags.items() if not ok]


def _score_extraction(rows: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, list[str]]]:
    rows = [r for r in rows if r["gold_ex"] is not None]
    out: dict[str, float] = {}
    fails: dict[str, list[str]] = {}

    def per_case(name: str, ok: Callable[[dict[str, Any], dict[str, Any]], bool]) -> None:
        flags = {}
        for r in rows:
            pred = r["pred_ex"]
            flags[r["id"]] = pred is not None and ok(r["gold_ex"], pred)
        out[name], fails[name] = _frac(flags)

    per_case(
        "location_exact",
        lambda g, p: M.location_exact(g["location"]["text"], p["location"]["text"]),
    )
    per_case(
        "location_partial",
        lambda g, p: M.location_partial(g["location"]["text"], p["location"]["text"]),
    )
    per_case(
        "people_count_exact",
        lambda g, p: g["people_count"]["value"] == p["people_count"]["value"],
    )
    per_case(
        "is_request_accuracy", lambda g, p: g["is_request"]["value"] == p["is_request"]["value"]
    )
    for key, field in (("vulnerable_f1", "vulnerable"), ("hazard_f1", "hazards")):
        per_case(
            key,
            lambda g, p, f=field: {x["kind"] for x in g[f]} == {x["kind"] for x in p[f]},
        )
        # the number is micro-F1 over label sets; the failures are the cases with any difference
        scored = [r for r in rows if r["pred_ex"] is not None]
        out[key] = M.set_f1(
            [[x["kind"] for x in r["gold_ex"][field]] for r in scored],
            [[x["kind"] for x in r["pred_ex"][field]] for r in scored],
        )
    return out, fails


def _score_decision(rows: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, list[str]]]:
    out: dict[str, float] = {}
    fails: dict[str, list[str]] = {}
    ids = [r["id"] for r in rows]
    gold_tier = [
        r["gold"].priority_tier if r["gold"].outcome == "prioritized" else None for r in rows
    ]
    pred_tier = [r["pred_tier"] for r in rows]

    out["outcome_accuracy"], fails["outcome_accuracy"] = _frac(
        {r["id"]: r["pred_outcome"] == r["gold"].outcome for r in rows}
    )
    both = {
        r["id"]: r["pred_tier"] == r["gold"].priority_tier
        for r in rows
        if r["gold"].outcome == "prioritized"
    }
    out["tier_accuracy"], fails["tier_accuracy"] = _frac(both)

    out["p1_misses_per_1000"] = M.p1_misses_per_1000(gold_tier, pred_tier)
    fails["p1_misses_per_1000"] = [
        i for i, t, p in zip(ids, gold_tier, pred_tier, strict=True) if t == "P1" and p != "P1"
    ]
    out["false_alarm_rate"] = M.false_alarm_rate(gold_tier, pred_tier)
    fails["false_alarm_rate"] = [
        i for i, t, p in zip(ids, gold_tier, pred_tier, strict=True) if t != "P1" and p == "P1"
    ]

    t_abs = [r["gold"].outcome == "abstain" for r in rows]
    p_abs = [r["pred_outcome"] == "abstain" for r in rows]
    out.update(
        M.abstain_metrics(
            t_abs,
            p_abs,
            [r["gold"].follow_up_field for r in rows],
            [r["pred_field"] for r in rows],
        )
    )
    fails["abstain_precision"] = [
        i for i, t, p in zip(ids, t_abs, p_abs, strict=True) if p and not t
    ]
    fails["abstain_recall"] = [i for i, t, p in zip(ids, t_abs, p_abs, strict=True) if t and not p]
    fails["follow_up_field_rate"] = [
        r["id"]
        for r in rows
        if r["gold"].outcome == "abstain"
        and r["pred_outcome"] == "abstain"
        and r["gold"].follow_up_field != r["pred_field"]
    ]

    groups: dict[str, list[str]] = {}
    for r in rows:
        if r["group"]:
            groups.setdefault(r["group"], []).append(r["id"])
    pred_groups: dict[str, list[str]] = {}
    known = set(ids)
    for r in rows:
        if r["pred_outcome"] == "duplicate" and r["duplicate_of"] in known:
            pred_groups.setdefault(r["duplicate_of"], [r["duplicate_of"]]).append(r["id"])
    out["duplicate_pairwise_f1"] = M.duplicate_pairwise_f1(groups.values(), pred_groups.values())[
        "f1"
    ]
    wrong_dup = {
        r["id"]
        for r in rows
        if (r["pred_outcome"] == "duplicate") != (r["gold"].outcome == "duplicate")
    }
    fails["duplicate_pairwise_f1"] = sorted(wrong_dup)
    return out, fails


def _latency(rows: list[dict[str, Any]]) -> dict[str, float]:
    n = len(rows)
    ext = sum(r["t_extract"] for r in rows)
    dec = sum(r["t_decide"] for r in rows)
    mean = (ext + dec) / n if n else 0.0
    ms = sorted((r["t_extract"] + r["t_decide"]) * 1000 for r in rows)
    return {
        "extract_ms_per_request": 1000 * ext / n if n else 0.0,
        "decide_ms_per_request": 1000 * dec / n if n else 0.0,
        "total_ms_per_request": 1000 * mean,
        "p95_ms_per_request": ms[min(int(0.95 * n), n - 1)] if n else 0.0,
        "seconds_per_1000_requests": 1000 * mean,
    }


def run_set(system: System, cases: Sequence[EvalCase], set_name: str) -> dict[str, Any]:
    """Score ``system`` on the test-split cases. Returns the result document (not yet written)."""
    rows: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    rules_version = None
    for c in cases:
        t0 = time.perf_counter()
        pred_ex: dict[str, Any] | None = None
        try:
            pred_ex = _dump(system.extract(c.raw_text))
        except Exception as e:  # a failing extraction is a scored failure, not a crash
            errors[c.id] = f"extract: {type(e).__name__}: {e}"
        t1 = time.perf_counter()
        out: dict[str, Any] = {}
        if pred_ex is not None:
            try:
                out = system.decide({"extracted": pred_ex, "context": default_context()})
            except Exception as e:
                errors[c.id] = f"decide: {type(e).__name__}: {e}"
        t2 = time.perf_counter()
        rules_version = out.get("rules_version") or rules_version
        rows.append(
            {
                "id": c.id,
                "gold": c.gold_decision,
                "gold_ex": c.gold_extracted.model_dump(mode="json") if c.gold_extracted else None,
                "group": c.gold_duplicate_group,
                "pred_ex": pred_ex,
                "pred_outcome": out.get("outcome"),
                "pred_tier": out.get("priority_tier")
                if out.get("outcome") == "prioritized"
                else None,
                "pred_field": out.get("follow_up_field"),
                "duplicate_of": out.get("duplicate_of"),
                "t_extract": t1 - t0,
                "t_decide": t2 - t1,
            }
        )
    ex_metrics, ex_fails = _score_extraction(rows)
    de_metrics, de_fails = _score_decision(rows)
    metrics = {f"extraction.{k}": v for k, v in ex_metrics.items()}
    metrics.update({f"decision.{k}": v for k, v in de_metrics.items()})
    failures = {f"extraction.{k}": v for k, v in ex_fails.items()}
    failures.update({f"decision.{k}": v for k, v in de_fails.items()})
    return {
        "system": system.name,
        "set": set_name,
        "run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n": len(rows),
        "model_version": system.model_version,
        "rules_version": rules_version,
        "metrics": metrics,
        "failures": failures,
        "latency": _latency(rows),
        "errors": errors,
    }


def write_result(result: dict[str, Any], results_dir: Path) -> Path:
    import json

    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{result['system']}__{result['set']}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _set_files(sets_dir: Path, set_name: str) -> list[Path]:
    files = sorted(sets_dir.glob(f"{set_name.lower()}*.jsonl")) + sorted(
        sets_dir.glob(f"set_{set_name.lower()}*.jsonl")
    )
    return list(dict.fromkeys(files))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score a system at extraction and decision")
    ap.add_argument("--system", required=True)
    ap.add_argument("--sets", default="A,B,C,D")
    ap.add_argument("--sets-dir", type=Path, default=DEFAULT_SETS_DIR)
    ap.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    ap.add_argument("--open-sealed", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.system not in SYSTEMS:
        print(f"error: unknown system {args.system!r}; known: {sorted(SYSTEMS)}", file=sys.stderr)
        return 2
    try:
        for set_name in [s.strip().upper() for s in args.sets.split(",") if s.strip()]:
            files = _set_files(args.sets_dir, set_name)
            if not files:
                print(f"error: no files for set {set_name} in {args.sets_dir}", file=sys.stderr)
                return 1
            cases = [
                c
                for f in files
                for c in load_cases(f, open_sealed=args.open_sealed, split="test")
                if c.set == set_name
            ]
            result = run_set(SYSTEMS[args.system](cases), cases, set_name)
            print(f"wrote {write_result(result, args.results_dir)} (n={result['n']})")
    except EvalSetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
