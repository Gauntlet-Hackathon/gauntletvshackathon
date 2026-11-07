"""Honest numbers: Wilson interval, detection floor, paired comparison. Pure stdlib."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

Z95 = 1.959964  # two-sided 95%
Z80_POWER = 0.841621  # 80% power


def wilson_interval(passes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """95% Wilson score interval for a pass rate. Empty sample -> (0, 1): nothing is known."""
    if n <= 0:
        return (0.0, 1.0)
    if not 0 <= passes <= n:
        raise ValueError("passes must be within 0..n")
    p = passes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def detection_floor(n: int, baseline: float = 0.5) -> float | None:
    """Smallest difference in pass rate two independent runs of ``n`` cases each can reliably
    detect (two-sided alpha 0.05, 80% power, normal approximation). ``baseline`` 0.5 is the
    worst case. Returns None for n <= 0. Capped at 1.0."""
    if n <= 0:
        return None
    var = baseline * (1 - baseline)
    return min(1.0, (Z95 + Z80_POWER) * math.sqrt(2 * var / n))


def pass_rate_summary(passes: int, n: int) -> dict[str, Any]:
    lo, hi = wilson_interval(passes, n)
    return {
        "passes": passes,
        "n": n,
        "pass_rate": passes / n if n else None,
        "ci95_low": lo,
        "ci95_high": hi,
        "detection_floor": detection_floor(n),
    }


def paired_comparison(a: Mapping[str, bool], b: Mapping[str, bool]) -> dict[str, Any]:
    """Compare system B against A on the cases both scored (per-case pass flags by id).

    improved = B passes where A fails; worsened = the reverse. Cases in only one system are
    counted in ``unpaired`` and otherwise ignored.
    """
    shared = a.keys() & b.keys()
    improved = sum(1 for i in shared if b[i] and not a[i])
    worsened = sum(1 for i in shared if a[i] and not b[i])
    return {
        "improved": improved,
        "worsened": worsened,
        "unchanged": len(shared) - improved - worsened,
        "n": len(shared),
        "unpaired": len(a.keys() ^ b.keys()),
        "sign_test_p": _sign_test_p(improved, worsened),
    }


def _sign_test_p(improved: int, worsened: int) -> float:
    """Exact two-sided sign test on the discordant pairs."""
    m = improved + worsened
    if m == 0:
        return 1.0
    k = min(improved, worsened)
    tail = sum(math.comb(m, i) for i in range(k + 1)) / 2**m
    return min(1.0, 2 * tail)


def case_flags(
    failures: Mapping[str, Sequence[str]], metric: str, case_ids: Sequence[str]
) -> dict[str, bool]:
    """Per-case pass flags for a metric, from a result's ``failures`` map."""
    bad = set(failures.get(metric, ()))
    return {i: i not in bad for i in case_ids}
