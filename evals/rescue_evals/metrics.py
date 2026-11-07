"""Pure-Python metrics for the evals table. Plain data in, numbers out; no I/O.

Tiers are "P1"/"P2"/"P3"; a prediction that is not a tier (abstain, duplicate, not a request)
is ``None``. Empty denominators return 0.0 so a table row never crashes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from itertools import combinations
from typing import Any

TIERS = ("P1", "P2", "P3")


def _ratio(num: float, den: float) -> float:
    return num / den if den else 0.0


def _f1(precision: float, recall: float) -> float:
    return _ratio(2 * precision * recall, precision + recall)


# --- P1 safety ---------------------------------------------------------------------------------


def p1_misses_per_1000(true_tiers: Sequence[str | None], pred_tiers: Sequence[str | None]) -> float:
    """True P1 requests the engine did not output as P1, per 1,000 requests scored."""
    _check_len(true_tiers, pred_tiers)
    misses = sum(1 for t, p in zip(true_tiers, pred_tiers, strict=True) if t == "P1" and p != "P1")
    return 1000 * _ratio(misses, len(true_tiers))


def false_alarm_rate(true_tiers: Sequence[str | None], pred_tiers: Sequence[str | None]) -> float:
    """Share of truly non-P1 requests that the engine output as P1."""
    _check_len(true_tiers, pred_tiers)
    non_p1 = [(t, p) for t, p in zip(true_tiers, pred_tiers, strict=True) if t != "P1"]
    return _ratio(sum(1 for _, p in non_p1 if p == "P1"), len(non_p1))


def sweep(
    items: Iterable[Mapping[str, Any]], thresholds: Iterable[float]
) -> list[dict[str, float]]:
    """Misses vs false alarms as the uncertainty-escalation threshold moves.

    Each item: ``true_tier``, ``tier`` (engine tier without escalation), ``escalated_tier`` (tier
    if escalated, usually one higher) and ``uncertainty`` in [0, 1]. An item is escalated when its
    uncertainty >= threshold, so a lower threshold escalates more: fewer misses, more false alarms.
    """
    rows = list(items)
    truth = [r["true_tier"] for r in rows]
    out = []
    for th in thresholds:
        pred = [r["escalated_tier"] if r["uncertainty"] >= th else r["tier"] for r in rows]
        out.append(
            {
                "threshold": th,
                "p1_misses_per_1000": p1_misses_per_1000(truth, pred),
                "false_alarm_rate": false_alarm_rate(truth, pred),
            }
        )
    return out


# --- ranking -----------------------------------------------------------------------------------


def top_k_accuracy(true_ranking: Sequence[str], pred_ranking: Sequence[str], k: int = 20) -> float:
    """Fraction of the true top-k request ids that appear in the predicted top-k."""
    true_top = set(true_ranking[:k])
    return _ratio(len(true_top & set(pred_ranking[:k])), len(true_top))


# --- field accuracy ----------------------------------------------------------------------------


def _tokens(text: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def location_exact(true: str | None, pred: str | None) -> bool:
    """Same tokens ignoring case and punctuation; both empty counts as a match."""
    return _tokens(true) == _tokens(pred)


def location_partial(true: str | None, pred: str | None) -> bool:
    """Exact, or one side's tokens are contained in the other's (non-empty)."""
    t, p = _tokens(true), _tokens(pred)
    if t == p:
        return True
    return bool(t and p) and (t <= p or p <= t)


def location_accuracy(pairs: Sequence[tuple[str | None, str | None]]) -> dict[str, float]:
    """``pairs`` are (true_text, predicted_text)."""
    return {
        "location_exact": _ratio(sum(location_exact(t, p) for t, p in pairs), len(pairs)),
        "location_partial": _ratio(sum(location_partial(t, p) for t, p in pairs), len(pairs)),
    }


def count_exact(pairs: Sequence[tuple[int | None, int | None]]) -> float:
    return _ratio(sum(1 for t, p in pairs if t == p), len(pairs))


def set_f1(true_sets: Sequence[Iterable[str]], pred_sets: Sequence[Iterable[str]]) -> float:
    """Micro-averaged F1 over per-request sets of labels (vulnerable kinds, hazard kinds)."""
    _check_len(true_sets, pred_sets)
    tp = fp = fn = 0
    for t, p in zip(true_sets, pred_sets, strict=True):
        ts, ps = set(t), set(p)
        tp += len(ts & ps)
        fp += len(ps - ts)
        fn += len(ts - ps)
    if tp + fp + fn == 0:
        return 1.0
    return _f1(_ratio(tp, tp + fp), _ratio(tp, tp + fn))


# --- duplicates --------------------------------------------------------------------------------


def _pairs(groups: Iterable[Iterable[str]]) -> set[frozenset[str]]:
    return {frozenset(pair) for g in groups for pair in combinations(sorted(set(g)), 2)}


def duplicate_pairwise_f1(
    true_groups: Iterable[Iterable[str]], pred_groups: Iterable[Iterable[str]]
) -> dict[str, float]:
    """Precision/recall/F1 over unordered request pairs placed in the same group."""
    true_pairs, pred_pairs = _pairs(true_groups), _pairs(pred_groups)
    tp = len(true_pairs & pred_pairs)
    if not true_pairs and not pred_pairs:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    precision, recall = _ratio(tp, len(pred_pairs)), _ratio(tp, len(true_pairs))
    return {"precision": precision, "recall": recall, "f1": _f1(precision, recall)}


# --- abstention --------------------------------------------------------------------------------


def abstain_metrics(
    true_abstain: Sequence[bool],
    pred_abstain: Sequence[bool],
    true_field: Sequence[str | None] | None = None,
    pred_field: Sequence[str | None] | None = None,
) -> dict[str, float]:
    """Abstain precision/recall, plus the right-follow-up-field rate.

    ``*_field`` is the field the follow-up question asks about (e.g. "location"). The rate is over
    requests that should abstain and did; 0.0 if the fields are not given.
    """
    _check_len(true_abstain, pred_abstain)
    tp = sum(1 for t, p in zip(true_abstain, pred_abstain, strict=True) if t and p)
    out = {
        "abstain_precision": _ratio(tp, sum(pred_abstain)),
        "abstain_recall": _ratio(tp, sum(true_abstain)),
        "follow_up_field_rate": 0.0,
    }
    if true_field is not None and pred_field is not None:
        flags = enumerate(zip(true_abstain, pred_abstain, strict=True))
        both = [i for i, (t, p) in flags if t and p]
        out["follow_up_field_rate"] = _ratio(
            sum(1 for i in both if true_field[i] == pred_field[i]), len(both)
        )
    return out


# --- calibration -------------------------------------------------------------------------------


def reliability_bins(
    confidences: Sequence[float], correct: Sequence[bool], n_bins: int = 10
) -> list[dict[str, float]]:
    """Equal-width bins on [0, 1] (1.0 goes in the last bin); empty bins are omitted."""
    _check_len(confidences, correct)
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for c, ok in zip(confidences, correct, strict=True):
        buckets[min(int(c * n_bins), n_bins - 1)].append((c, ok))
    return [
        {
            "lower": i / n_bins,
            "upper": (i + 1) / n_bins,
            "count": len(b),
            "confidence": sum(c for c, _ in b) / len(b),
            "accuracy": sum(ok for _, ok in b) / len(b),
        }
        for i, b in enumerate(buckets)
        if b
    ]


def expected_calibration_error(
    confidences: Sequence[float], correct: Sequence[bool], n_bins: int = 10
) -> float:
    """Sample-weighted mean of |accuracy - confidence| over the reliability bins."""
    total = len(confidences)
    return sum(
        b["count"] / total * abs(b["accuracy"] - b["confidence"])
        for b in reliability_bins(confidences, correct, n_bins)
    )


# --- consistency -------------------------------------------------------------------------------


def consistency_pct(groups: Iterable[Sequence[Any]]) -> float:
    """Percent of paraphrase groups whose outputs are all identical (groups of 1 are skipped)."""
    sized = [g for g in groups if len(g) > 1]
    agree = sum(1 for g in sized if all(x == g[0] for x in g))
    return 100 * _ratio(agree, len(sized))


def _check_len(a: Sequence[Any], b: Sequence[Any]) -> None:
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")



