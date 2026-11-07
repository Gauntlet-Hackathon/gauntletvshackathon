"""Replay a decision log with two engine versions and report what changed.

    python -m rescue_engine.replay_diff EVENTS.jsonl OLD NEW [--dist DIR] [--json OUT] [--md OUT]

EVENTS.jsonl holds event envelopes (one JSON per line). Every `decided` event carries the exact
engine_input, so each request is re-decided by both engines (latest `decided` event per request).
OLD / NEW are version names under --dist (engine/dist/<rules_version>/) or paths to a version dir.
A request counts as changed when its priority_tier, outcome or provisional_tier (the tier an
abstain sorts by on the follow-up board, DECISIONS R1) differs.

Config drift (DECISIONS H12 / R15): a build made after the config freeze carries its frozen
thresholds in config/thresholds.json. A stored engine_input whose context.thresholds set a value
other than them was decided with other inputs than the build was frozen with; such requests are
listed under `config_drift` (per build) and flagged per row, whether or not the outcome changed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from rescue_shared import frozen_config

from .build import DEFAULT_DIST

SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"


def resolve_version(version: str, dist: Path = DEFAULT_DIST) -> Path:
    for cand in (Path(version), dist / version):
        if (cand / "manifest.json").is_file():
            return cand
    raise FileNotFoundError(f"engine version not found: {version}")


def load_inputs(events_path: Path) -> dict[str, dict]:
    """request_id -> engine_input of the latest `decided` event (file order breaks ties)."""
    latest: dict[str, tuple[str, dict]] = {}
    for line in events_path.read_text().splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("type") != "decided":
            continue
        key = (ev["occurred_at"], ev["event_id"])
        rid = ev["request_id"]
        if rid not in latest or key >= latest[rid][0]:
            latest[rid] = (key, ev["payload"]["engine_input"])
    return {rid: inp for rid, (_, inp) in latest.items()}


def run_engine(version_dir: Path, inputs: list[dict]) -> list[dict]:
    manifest = json.loads((version_dir / "manifest.json").read_text())
    cmd = manifest["entrypoint"].split()
    if cmd[0] == "python":
        cmd[0] = sys.executable
    env = dict(os.environ)
    # Version dirs contain ':' (a PYTHONPATH separator), so put the artifact on the path via cwd
    # (`python -m` prepends it) instead of PYTHONPATH.
    env["PYTHONPATH"] = str(SHARED_DIR)
    proc = subprocess.run(
        cmd,
        input=json.dumps(inputs),
        capture_output=True,
        text=True,
        env=env,
        cwd=version_dir,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"engine {manifest['rules_version']} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def frozen_thresholds(version_dir: Path) -> dict | None:
    """The build's frozen thresholds, or None for a build made before the config freeze."""
    path = version_dir / frozen_config.CONFIG_SUBDIR / frozen_config.CONFIG_FILES["thresholds"]
    return frozen_config.read_thresholds(path) if path.is_file() else None


def thresholds_drift(engine_input: dict, frozen: dict | None) -> bool:
    """True when a threshold the input sets differs from the frozen one. A threshold the input
    leaves out falls back to the engine's default, which equals the frozen value (tested)."""
    if frozen is None:
        return False
    given = (engine_input.get("context") or {}).get("thresholds") or {}
    return any(k not in frozen or frozen[k] != v for k, v in given.items())


def _brief(out: dict) -> dict:
    return {k: out.get(k) for k in ("priority_tier", "outcome", "provisional_tier", "rule_id")}


def _changed(row: dict) -> bool:
    return row["tier_changed"] or row["outcome_changed"] or row["provisional_tier_changed"]


def diff(events_path: Path, old_dir: Path, new_dir: Path) -> dict:
    by_request = load_inputs(events_path)
    ids = sorted(by_request)
    inputs = [by_request[i] for i in ids]
    old = run_engine(old_dir, inputs) if inputs else []
    new = run_engine(new_dir, inputs) if inputs else []
    frozen_old, frozen_new = frozen_thresholds(old_dir), frozen_thresholds(new_dir)
    rows, changed = [], 0
    drift = {"old": [], "new": []}
    for rid, inp, a, b in zip(ids, inputs, old, new, strict=True):
        tier_changed = a.get("priority_tier") != b.get("priority_tier")
        outcome_changed = a.get("outcome") != b.get("outcome")
        provisional_changed = a.get("provisional_tier") != b.get("provisional_tier")
        row = {
            "request_id": rid,
            "before": _brief(a),
            "after": _brief(b),
            "tier_changed": tier_changed,
            "outcome_changed": outcome_changed,
            "provisional_tier_changed": provisional_changed,
            "config_drift_old": thresholds_drift(inp, frozen_old),
            "config_drift_new": thresholds_drift(inp, frozen_new),
        }
        for side in ("old", "new"):
            if row[f"config_drift_{side}"]:
                drift[side].append(rid)
        if _changed(row):
            changed += 1
        rows.append(row)
    return {
        "old_version": json.loads((old_dir / "manifest.json").read_text())["rules_version"],
        "new_version": json.loads((new_dir / "manifest.json").read_text())["rules_version"],
        "total": len(rows),
        "changed": changed,
        "config_drift": drift,
        "requests": rows,
    }


def to_markdown(report: dict) -> str:
    lines = [
        "# Replay diff",
        "",
        f"- before: `{report['old_version']}`",
        f"- after: `{report['new_version']}`",
        f"- requests: {report['total']}, changed: {report['changed']}",
    ]
    drift = report.get("config_drift") or {}
    for side, label in (("old", "before"), ("new", "after")):
        if drift.get(side):
            lines.append(
                f"- config drift vs {label} build: {len(drift[side])} request(s) were decided "
                "with thresholds other than the build's frozen ones"
            )
    lines.append("")
    changed = [r for r in report["requests"] if _changed(r)]
    if not changed:
        lines.append("No tier or outcome changes.")
    else:
        lines += ["| request | tier | outcome | provisional tier |", "|---|---|---|---|"]
        for r in changed:
            b, a = r["before"], r["after"]
            lines.append(
                f"| {r['request_id']} | {b['priority_tier']} → {a['priority_tier']} "
                f"| {b['outcome']} → {a['outcome']} "
                f"| {b['provisional_tier']} → {a['provisional_tier']} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rescue_engine.replay_diff")
    p.add_argument("events", type=Path)
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    p.add_argument("--json", type=Path, dest="json_out")
    p.add_argument("--md", type=Path, dest="md_out")
    args = p.parse_args(argv)
    try:
        report = diff(
            args.events, resolve_version(args.old, args.dist), resolve_version(args.new, args.dist)
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(report, indent=2) + "\n"
    if args.json_out:
        args.json_out.write_text(text)
    else:
        sys.stdout.write(text)
    md = to_markdown(report)
    if args.md_out:
        args.md_out.write_text(md)
    elif args.json_out:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
