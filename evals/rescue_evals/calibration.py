"""Calibration: reliability diagram (PNG) and ECE per extracted-field confidence.

    python -m rescue_evals.calibration --system <name> --sets A,B,C,D [--out-dir DIR]

For each field the model reports a confidence for, a prediction is "correct" when it matches the
gold extraction. ``vulnerable`` and ``hazards`` are scored per predicted item (is the kind in the
gold set). Writes ``calibration.json`` (ECE, bins per field) and ``reliability_<field>.png``.
The PNG is drawn with numpy + zlib so no plotting dependency is needed (no text labels: bars are
observed accuracy per confidence bin, the diagonal is perfect calibration).
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from rescue_evals import metrics as M
from rescue_evals.loaders import EvalCase, EvalSetError, load_cases
from rescue_evals.runner import (
    DEFAULT_RESULTS_DIR,
    DEFAULT_SETS_DIR,
    SYSTEMS,
    System,
    _dump,
    _set_files,
)

FIELDS = ("location", "people_count", "is_request", "vulnerable", "hazards")
Samples = dict[str, tuple[list[float], list[bool]]]


def collect(system: System, cases: Sequence[EvalCase]) -> Samples:
    """(confidences, correct) per field; cases without a gold extraction or a prediction skip."""
    out: Samples = {f: ([], []) for f in FIELDS}

    def add(field: str, conf: float, ok: bool) -> None:
        out[field][0].append(float(conf))
        out[field][1].append(bool(ok))

    for c in cases:
        if c.gold_extracted is None:
            continue
        try:
            p = _dump(system.extract(c.raw_text))
        except Exception:
            continue
        g = c.gold_extracted.model_dump(mode="json")
        add(
            "location",
            p["location"]["confidence"],
            M.location_partial(g["location"]["text"], p["location"]["text"]),
        )
        add(
            "people_count",
            p["people_count"]["confidence"],
            g["people_count"]["value"] == p["people_count"]["value"],
        )
        add(
            "is_request",
            p["is_request"]["confidence"],
            g["is_request"]["value"] == p["is_request"]["value"],
        )
        for field in ("vulnerable", "hazards"):
            gold_kinds = {x["kind"] for x in g[field]}
            for item in p[field]:
                add(field, item["confidence"], item["kind"] in gold_kinds)
    return out


def summarize(samples: Samples, n_bins: int = 10) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for field, (conf, ok) in samples.items():
        res[field] = {
            "n": len(conf),
            "ece": M.expected_calibration_error(conf, ok, n_bins) if conf else None,
            "bins": M.reliability_bins(conf, ok, n_bins),
        }
    return res


# --- tiny PNG renderer ---------------------------------------------------------------------------

_BG, _BAR, _DIAG, _AXIS = (255, 255, 255), (70, 130, 200), (200, 60, 60), (0, 0, 0)


def render_reliability_png(
    bins: Sequence[dict[str, float]], n_bins: int = 10, size: int = 400
) -> bytes:
    """Square reliability diagram: x = confidence, y = observed accuracy, both 0..1."""
    m = 20  # margin
    plot = size - 2 * m
    img = np.empty((size, size, 3), dtype=np.uint8)
    img[:] = _BG

    def y_of(v: float) -> int:
        return m + plot - int(round(v * plot))

    for b in bins:
        x0 = m + int(b["lower"] * plot) + 1
        x1 = m + int(b["upper"] * plot) - 1
        img[y_of(b["accuracy"]) : m + plot, x0 : max(x1, x0 + 1)] = _BAR
    for i in range(plot + 1):  # diagonal
        img[y_of(i / plot), m + i] = _DIAG
    img[m : m + plot + 1, m] = _AXIS
    img[m + plot, m : m + plot + 1] = _AXIS
    raw = b"".join(b"\x00" + img[r].tobytes() for r in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def write_outputs(summary: dict[str, Any], out_dir: Path, n_bins: int = 10) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    j = out_dir / "calibration.json"
    j.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths.append(j)
    for field, s in summary.items():
        p = out_dir / f"reliability_{field}.png"
        p.write_bytes(render_reliability_png(s["bins"], n_bins))
        paths.append(p)
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reliability diagrams + ECE per field")
    ap.add_argument("--system", required=True)
    ap.add_argument("--sets", default="A,B,C,D")
    ap.add_argument("--sets-dir", type=Path, default=DEFAULT_SETS_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_RESULTS_DIR / "calibration")
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
    summary = summarize(collect(SYSTEMS[args.system](cases), cases))
    for p in write_outputs(summary, args.out_dir):
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
