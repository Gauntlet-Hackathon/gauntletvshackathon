"""Eval runner: scores a system at two points, SLM extraction and the final decision.

    python -m rescue_evals.runner --system <name> --sets A,B,C,D [--results-dir DIR]

Writes ``<results-dir>/<system>__<set>.json`` (format in docs/DECISIONS.md: ``system``, ``set``,
``run_at``, ``n``, ``metrics`` name -> number) plus ``model_version``, ``rules_version``,
``failures`` (metric name -> failing case ids, for the C11 drill-down) and ``latency``.
``metrics["calibration.ece"]`` is the pooled extracted-field calibration error the cloud promotion
gate reads (DECISIONS A3); the follow-up-field rate derives the field from ``abstain_reason`` (A7).
Only the ``test`` split is scored. Set E needs ``--open-sealed``.

Review PR-3 (definitions in evals/README.md):

* Cases are decided in arrival order (``received_at``, else file order) with the context the field
  pipeline builds: street-list and hoax-marker hits, and duplicate candidates among the set's
  earlier requests (``logic.duplicate_candidates``), so duplicate F1 is a real number (H-01).
* ``metric_n`` holds each metric's denominator and ``metric_counts`` the tp/fp/fn behind each F1.
  A metric whose denominator is 0 is left out of ``metrics`` (and ``failures``) instead of being
  written as a placeholder 0.0 or 1.0, so pooling never mixes in an empty set (H-03).
* ``decision.p1_misses_per_1000`` is per 1,000 urgent (gold P1) requests, the plan's "P1 recall"
  headline (O-03); ``decision.p1_misses_per_1000_requests`` keeps the per-request rate.
* ``decision.top20_accuracy``: the first 20 rows of the board (tier, then
  ``logic.within_tier_sort_key``), predicted vs gold (H-04); left out when a root that could be
  in the gold top 20 has no gold extraction to sort by.
* Systems get the case, not just its text, so identical texts are not conflated (F-04).
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Protocol

from rescue_evals import metrics as M
from rescue_evals import stats as S
from rescue_evals.loaders import EvalCase, EvalSetError, load_cases
from rescue_shared import frozen_config, logic
from rescue_shared.models import Extracted

log = logging.getLogger("rescue_evals.runner")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETS_DIR = REPO_ROOT / "evals" / "sets"
DEFAULT_RESULTS_DIR = REPO_ROOT / "evals" / "results"


class System(Protocol):
    """Anything scoreable: an extractor (raw text -> extracted) plus a decision step.

    A system that looks its answer up per case (stored outputs, the gold oracle) also defines
    ``extract_case(case)``; the runner prefers it, so two cases with the same text are never
    scored against each other's row (review F-04)."""

    name: str
    model_version: str

    def extract(self, raw_text: str) -> Extracted | dict[str, Any]: ...

    def decide(self, engine_input: dict[str, Any]) -> dict[str, Any]: ...


def extract_for(system: System, case: EvalCase) -> Extracted | dict[str, Any]:
    """The system's extraction for one case: ``extract_case(case)`` when it has one, else
    ``extract(case.raw_text)``."""
    by_case = getattr(system, "extract_case", None)
    return by_case(case) if callable(by_case) else system.extract(case.raw_text)


SystemFactory = Callable[[Sequence[EvalCase]], System]
SYSTEMS: dict[str, SystemFactory] = {}


def register_system(name: str, factory: SystemFactory) -> None:
    SYSTEMS[name] = factory


# Modules that register a system when imported. The CLI imports them before looking up --system.
SYSTEM_MODULES = (
    "rescue_evals.systems.ours",
    "rescue_evals.baselines.stored",
    "rescue_evals.baselines.regex_baseline",
)


def available_systems() -> dict[str, SystemFactory]:
    """Every registered system, after importing SYSTEM_MODULES.

    Under `python -m rescue_evals.runner` this file runs as `__main__`, while the system modules
    register into the importable `rescue_evals.runner`. So read that module's registry, not this
    file's globals. Importing `ours` does not import torch (HFExtractor loads it lazily).
    """
    import importlib

    for name in SYSTEM_MODULES:
        importlib.import_module(name)
    registry = importlib.import_module("rescue_evals.runner").SYSTEMS
    return {**SYSTEMS, **registry}


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

    def extract_case(self, case: EvalCase) -> Extracted:
        if case.gold_extracted is None:
            raise LookupError(f"no gold extraction for {case.id}")
        return case.gold_extracted

    def decide(self, engine_input: dict[str, Any]) -> dict[str, Any]:
        from rescue_engine.fallback.engine import decide_one

        return decide_one(engine_input)


register_system("gold-extraction", lambda cases: GoldExtractionSystem(cases))


@functools.cache
def _frozen_lists() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(streets, hoax_markers) from contracts/fixtures/, the lists the field app loads."""
    cfg = frozen_config.load_config()
    return tuple(cfg["streets"]), tuple(cfg["hoax_markers"])


def default_context(
    raw_text: str | None = None,
    extracted: dict[str, Any] | None = None,
    duplicate_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The engine context for one case. With the case's text and extraction, the street-list and
    hoax-marker hits are computed exactly as the field pipeline does (``rescue_field.pipeline``),
    so a landmark on the street list stays usable and a prank can abstain as ``possible_hoax``.
    ``duplicate_candidates`` come from ``DuplicateIndex`` (run_set); without them the case is
    decided as if it were the first request."""
    from rescue_engine.fallback.rules import DEFAULT_THRESHOLDS

    streets, hoax_markers = _frozen_lists()
    location = (extracted or {}).get("location") or {}
    return {
        "duplicate_candidates": list(duplicate_candidates or []),
        "street_list_hits": logic.street_list_hits(location.get("text"), streets),
        "hoax_marker_hits": logic.hoax_marker_hits(raw_text, hoax_markers) if raw_text else 0,
        "thresholds": dict(DEFAULT_THRESHOLDS),
    }


# Synthetic arrival times for a set without received_at: one second apart, in file order, so
# every case falls inside the duplicate window and the order is the file's.
SYNTHETIC_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def arrival_times(cases: Sequence[EvalCase]) -> dict[str, str]:
    """``received_at`` per case id: the case's own, else a synthetic time from its file position.
    A set should give ``received_at`` for all of its cases or for none."""
    return {
        c.id: c.received_at or (SYNTHETIC_EPOCH + timedelta(seconds=i)).strftime(TS_FORMAT)
        for i, c in enumerate(cases)
    }


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class DuplicateIndex:
    """The set's earlier requests, for ``logic.duplicate_candidates``, as the field builds them
    (``rescue_field.pipeline``: location text, people count, contact, arrival time and the tier
    the engine gave each one). Feed cases in arrival order: ``candidates`` before deciding a case,
    ``add`` after. Only requests with the same normalised location can match, so the index is
    keyed by it (the field's ``_relevant_prior``)."""

    def __init__(self, window_hours: float) -> None:
        self.window_hours = window_hours
        self._by_location: dict[str, list[dict[str, Any]]] = {}
        self._case_of: dict[str, str] = {}

    @staticmethod
    def request_id(case_id: str) -> str:
        """A ULID-shaped stand-in for a case id. The engine contract types request ids (and so
        ``duplicate_of``) as ULIDs, and the engine client validates its output against it, so a
        case id like ``B-077`` would fail validation on every duplicate outcome."""
        digest = int.from_bytes(hashlib.sha256(case_id.encode()).digest()[:17], "big")
        return "".join(_CROCKFORD[(digest >> (5 * i)) & 31] for i in range(25, -1, -1))

    def case_id(self, request_id: str | None) -> str | None:
        """The case id behind a stand-in request id (``duplicate_of`` in an engine output)."""
        return self._case_of.get(request_id, request_id) if request_id else request_id

    def _record(
        self, case_id: str, received_at: str, contact: str | None, extracted: dict[str, Any]
    ) -> dict[str, Any]:
        rid = self.request_id(case_id)
        self._case_of[rid] = case_id
        return {
            "request_id": rid,
            "received_at": received_at,
            "location_text": extracted["location"]["text"],
            "people_count": extracted["people_count"]["value"],
            "contact": contact,
            "priority_tier": None,
        }

    def candidates(
        self, case_id: str, received_at: str, contact: str | None, extracted: dict[str, Any]
    ) -> list[dict[str, Any]]:
        rec = self._record(case_id, received_at, contact, extracted)
        key = logic.normalize_location(rec["location_text"])
        if not key:
            return []
        prior = self._by_location.get(key, [])
        return logic.duplicate_candidates([*prior, rec], self.window_hours)[rec["request_id"]]

    def add(
        self,
        case_id: str,
        received_at: str,
        contact: str | None,
        extracted: dict[str, Any],
        priority_tier: str | None,
    ) -> None:
        rec = self._record(case_id, received_at, contact, extracted)
        rec["priority_tier"] = priority_tier
        key = logic.normalize_location(rec["location_text"])
        if key:
            self._by_location.setdefault(key, []).append(rec)


# The field a follow-up question asks for, by abstain_reason (DECISIONS A7; the EV5 labels).
FOLLOW_UP_FIELD = {
    "no_usable_location": "location",
    "conflicting_counts": "people_count",
    "low_confidence_request": "is_request",
    "possible_hoax": "is_request",
    "low_confidence_hazard": "hazard",
}


def follow_up_field(engine_output: dict[str, Any]) -> str | None:
    """The missing field an abstain's follow-up asks for, derived from its abstain_reason
    (engine_output has no field of its own); None for any other outcome."""
    if engine_output.get("outcome") != "abstain":
        return None
    return FOLLOW_UP_FIELD.get(engine_output.get("abstain_reason") or "")


def _dump(extracted: Extracted | dict[str, Any]) -> dict[str, Any]:
    if isinstance(extracted, Extracted):
        return extracted.model_dump(mode="json")
    return Extracted.model_validate(extracted).model_dump(mode="json")


# --- scoring -----------------------------------------------------------------------------------
#
# Every scorer returns a ``Scored``: metric values, failing case ids, each metric's denominator
# (``n``) and, for F1 metrics, the tp/fp/fn behind it. ``_emit`` drops a metric whose
# denominator is 0, so an empty set never writes a placeholder 0.0 / 1.0 (review H-03).

TIERS = ("P1", "P2", "P3")
TOP_K = 20


@dataclass
class Scored:
    metrics: dict[str, float]
    failures: dict[str, list[str]]
    n: dict[str, int]
    counts: dict[str, dict[str, int]]

    @classmethod
    def empty(cls) -> Scored:
        return cls({}, {}, {}, {})

    def emit(
        self,
        name: str,
        value: float | None,
        n: int,
        failures: list[str],
        counts: dict[str, int] | None = None,
    ) -> None:
        if not n or value is None:
            return
        self.metrics[name] = value
        self.failures[name] = failures
        self.n[name] = n
        if counts is not None:
            self.counts[name] = counts

    def emit_rate(self, name: str, flags: dict[str, bool]) -> None:
        """A per-case pass rate over ``flags`` (case id -> passed)."""
        n = len(flags)
        value = sum(flags.values()) / n if n else None
        self.emit(name, value, n, [i for i, ok in flags.items() if not ok])

    def emit_f1(self, name: str, counts: dict[str, int], failures: list[str]) -> None:
        """An F1 from tp/fp/fn; its ``n`` is tp + fp + fn (0 means nothing to score)."""
        self.emit(name, M.f1_from_counts(counts), sum(counts.values()), failures, counts)


def _score_extraction(rows: list[dict[str, Any]]) -> Scored:
    """Over the cases with a gold extraction. A case whose extraction failed is a miss for every
    row: wrong on the exact-match rows, and all its gold labels are false negatives in the F1
    rows (it used to drop out of the F1 denominator, so crashing raised F1, review H-03)."""
    rows = [r for r in rows if r["gold_ex"] is not None]
    sc = Scored.empty()

    def per_case(name: str, ok: Callable[[dict[str, Any], dict[str, Any]], bool]) -> None:
        sc.emit_rate(
            name,
            {r["id"]: r["pred_ex"] is not None and ok(r["gold_ex"], r["pred_ex"]) for r in rows},
        )

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
        gold = [[x["kind"] for x in r["gold_ex"][field]] for r in rows]
        pred = [[x["kind"] for x in (r["pred_ex"] or {}).get(field, [])] for r in rows]
        # the number is micro-F1 over label sets; the failures are the cases with any difference
        fails = [r["id"] for r, g, p in zip(rows, gold, pred, strict=True) if set(g) != set(p)]
        sc.emit_f1(key, M.set_f1_counts(gold, pred), fails)
    return sc


def board_tier(outcome: str | None, tier: str | None) -> str | None:
    """The tier a request is shown at on the board: its own when prioritized, its group's when
    a duplicate; None for abstain / not_a_request (review L-11: a gold-P1 duplicate merged into
    a P1 group is on the P1 tier, not missed)."""
    return tier if outcome in ("prioritized", "duplicate") and tier in TIERS else None


def _gold_board(rows: list[dict[str, Any]]) -> list[tuple[int, str, dict[str, Any]]]:
    """(tier index, household, root row) per gold tier row, in board order. A household is a
    gold duplicate group (or a lone case); its row sits at the group's highest gold tier, headed
    by its earliest member and sorted with that member's gold extraction, like the field
    board."""
    by_household: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_household.setdefault(r["household"], []).append(r)
    entries = []
    for household, members in by_household.items():
        tiers = [
            t for m in members if (t := board_tier(m["gold"].outcome, m["gold"].priority_tier))
        ]
        if not tiers:
            continue
        root = min(members, key=lambda m: (m["received_at"], m["id"]))
        key = logic.within_tier_sort_key(root["gold_ex"] or {}, root["received_at"], root["id"])
        entries.append((TIERS.index(min(tiers)), key, household, root))
    entries.sort(key=lambda e: e[:3])
    return [(tier, h, root) for tier, _, h, root in entries]


def _pred_board(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(gold household of the row's root, root case id) per predicted tier row, in board order,
    built like ``rescue_field.board``: requests the engine marked ``duplicate`` collapse under the
    earliest request of their group, the row shows the group's highest tier, and rows sort by
    ``logic.within_tier_sort_key`` on the root's predicted extraction."""
    by_id = {r["id"]: r for r in rows if r["pred_outcome"] is not None}
    parent = {rid: rid for rid in by_id}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def order(rid: str) -> tuple[str, str]:
        return (by_id[rid]["received_at"], rid)

    for rid, r in by_id.items():
        target = r["duplicate_of"]
        if r["pred_outcome"] == "duplicate" and target in by_id:
            a, b = find(rid), find(target)
            if a != b:
                keep, drop = sorted((a, b), key=order)
                parent[drop] = keep
    groups: dict[str, list[dict[str, Any]]] = {}
    for rid, r in by_id.items():
        groups.setdefault(find(rid), []).append(r)
    entries = []
    for root_id, members in groups.items():
        tiers = [m["pred_priority_tier"] for m in members if m["pred_priority_tier"] in TIERS]
        if not tiers:
            continue
        root = by_id[root_id]
        key = logic.within_tier_sort_key(root["pred_ex"] or {}, root["received_at"], root_id)
        entries.append((TIERS.index(min(tiers)), key, root["household"], root_id))
    return [(h, rid) for *_, h, rid in sorted(entries)]


def _top_k(rows: list[dict[str, Any]], k: int = TOP_K) -> tuple[float | None, int, list[str]]:
    """Top-20 accuracy (plan metric 2, review H-04): of the first k rows of the gold board, the
    share whose household is also among the first k rows of the predicted board. Rows are
    households, so a missed duplicate that takes two predicted slots counts once. n is the size
    of the gold top k (smaller than k on a small set). Failures: the gold top-k rows missing
    from the predicted top k, by their earliest case id.

    Left out (None) when the gold order is unknown: a root that could be in the gold top k (a row
    of the top k, or any row on the tier of its last row) has no gold extraction to sort by
    (set A style), so the gold top k itself would be a guess."""
    board = _gold_board(rows)
    gold = board[:k]
    if not gold:
        return None, 0, []
    cut = gold[-1][0]
    if any(root["gold_ex"] is None for tier, _, root in board if tier <= cut):
        return None, 0, []
    pred = _pred_board(rows)[:k]
    value = M.top_k_accuracy([h for _, h, _ in gold], [h for h, _ in pred], k)
    shown = {h for h, _ in pred}
    return value, len(gold), [root["id"] for _, h, root in gold if h not in shown]


def _score_decision(rows: list[dict[str, Any]]) -> Scored:
    sc = Scored.empty()
    ids = [r["id"] for r in rows]
    gold_tier = [board_tier(r["gold"].outcome, r["gold"].priority_tier) for r in rows]
    pred_tier = [r["pred_tier"] for r in rows]

    sc.emit_rate(
        "outcome_accuracy", {r["id"]: r["pred_outcome"] == r["gold"].outcome for r in rows}
    )
    sc.emit_rate(
        "tier_accuracy",
        {r["id"]: p == t for r, t, p in zip(rows, gold_tier, pred_tier, strict=True) if t},
    )

    urgent = sum(1 for t in gold_tier if t == "P1")
    missed = [
        i for i, t, p in zip(ids, gold_tier, pred_tier, strict=True) if t == "P1" and p != "P1"
    ]
    sc.emit("p1_misses_per_1000", M.p1_misses_per_1000(gold_tier, pred_tier), urgent, missed)
    sc.emit(
        "p1_misses_per_1000_requests",
        M.p1_misses_per_1000(gold_tier, pred_tier, per="request"),
        len(rows),
        missed,
    )
    # A miss the engine still sent to a human at the top of the follow-up queue (abstain with
    # provisional_tier P1, DECISIONS R1) is reported apart (review L-11); which one is the
    # headline is a DECISIONS call, so both are written.
    by_id = {r["id"]: r for r in rows}
    hard = [
        i
        for i in missed
        if not (by_id[i]["pred_outcome"] == "abstain" and by_id[i]["provisional_tier"] == "P1")
    ]
    sc.emit("p1_hard_misses_per_1000", 1000 * len(hard) / urgent if urgent else None, urgent, hard)
    non_p1 = sum(1 for t in gold_tier if t != "P1")
    sc.emit(
        "false_alarm_rate",
        M.false_alarm_rate(gold_tier, pred_tier),
        non_p1,
        [i for i, t, p in zip(ids, gold_tier, pred_tier, strict=True) if t != "P1" and p == "P1"],
    )

    t_abs = {r["id"]: r["gold"].outcome == "abstain" for r in rows}
    p_abs = {r["id"]: r["pred_outcome"] == "abstain" for r in rows}
    sc.emit_rate("abstain_precision", {i: t_abs[i] for i in ids if p_abs[i]})
    sc.emit_rate("abstain_recall", {i: p_abs[i] for i in ids if t_abs[i]})
    sc.emit_rate(
        "follow_up_field_rate",
        {
            r["id"]: r["gold"].follow_up_field == r["pred_field"]
            for r in rows
            if t_abs[r["id"]] and p_abs[r["id"]]
        },
    )

    gold_groups: dict[str, list[str]] = {}
    for r in rows:
        if r["group"]:
            gold_groups.setdefault(r["group"], []).append(r["id"])
    dup = M.duplicate_pairwise_f1(gold_groups.values(), _pred_groups(rows))
    wrong_dup = sorted(
        r["id"]
        for r in rows
        if (r["pred_outcome"] == "duplicate") != (r["gold"].outcome == "duplicate")
    )
    sc.emit_f1("duplicate_pairwise_f1", {k: int(dup[k]) for k in ("tp", "fp", "fn")}, wrong_dup)

    value, n, fails = _top_k(rows)
    sc.emit("top20_accuracy", value, n, fails)
    return sc


def _pred_groups(rows: list[dict[str, Any]]) -> list[list[str]]:
    """Predicted duplicate groups: requests joined by ``duplicate_of``, transitively (a chain
    C -> B -> A is one group of three, as on the board)."""
    known = {r["id"] for r in rows}
    parent = {i: i for i in known}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in rows:
        if r["pred_outcome"] == "duplicate" and r["duplicate_of"] in known:
            parent[find(r["id"])] = find(r["duplicate_of"])
    groups: dict[str, list[str]] = {}
    for i in known:
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1]


OVERCONFIDENT_MIN = 0.75  # a wrong prediction at or above this confidence is a calibration failure


def _score_calibration(rows: list[dict[str, Any]]) -> Scored:
    """``ece``: the calibration error of the confidences the decision is made from, i.e. every
    extracted-field confidence pooled (calibration.py's samples, one ECE over all fields; the
    promotion gate reads it as ``calibration.ece``). Left out when no case has both a gold and a
    predicted extraction, so the gate reports it missing. Failures: cases with a wrong
    prediction stated with confidence >= 0.75. ``n``: the cases that gave samples."""
    from rescue_evals.calibration import FIELDS, case_samples, pooled_ece  # imports this module

    samples: dict[str, tuple[list[float], list[bool]]] = {f: ([], []) for f in FIELDS}
    overconfident: list[str] = []
    n = 0
    for r in rows:
        if r["gold_ex"] is None or r["pred_ex"] is None:
            continue
        n += 1
        case = case_samples(r["gold_ex"], r["pred_ex"])
        for field, conf, ok in case:
            samples[field][0].append(conf)
            samples[field][1].append(ok)
        if any(not ok and conf >= OVERCONFIDENT_MIN for _, conf, ok in case):
            overconfident.append(r["id"])
    sc = Scored.empty()
    sc.emit("ece", pooled_ece(samples), n, overconfident)
    return sc


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
        "median_ms_per_request": median(ms) if n else 0.0,  # the plan's row (review L-11)
        "p95_ms_per_request": ms[min(int(0.95 * n), n - 1)] if n else 0.0,
        "seconds_per_1000_requests": 1000 * mean,
    }


SEALED_SET = "E"
# metrics that are plain per-case pass rates (the rest are F1 / per-1000 rates: no Wilson interval)
PASS_RATE_METRICS = (
    "extraction.location_exact",
    "extraction.location_partial",
    "extraction.people_count_exact",
    "extraction.is_request_accuracy",
    "decision.outcome_accuracy",
    "decision.tier_accuracy",
    "decision.abstain_precision",
    "decision.abstain_recall",
    "decision.follow_up_field_rate",
)


def _intervals(metric_n: dict[str, int], failures: dict[str, list[str]]) -> dict[str, Any]:
    return {
        m: S.pass_rate_summary(metric_n[m] - len(failures.get(m, [])), metric_n[m])
        for m in PASS_RATE_METRICS
        if m in metric_n
    }


def refuse_sealed(cases: Sequence[EvalCase], set_name: str, open_sealed: bool = False) -> None:
    """Guard: the sealed holdout (set E) is never scored without an explicit, logged opt-in."""
    if open_sealed:
        return
    if set_name.upper() == SEALED_SET or any(c.set == SEALED_SET for c in cases):
        from rescue_evals.loaders import SealedHoldoutError

        raise SealedHoldoutError("set E is the sealed holdout; pass open_sealed=True")


def _duplicate_window_hours() -> float:
    from rescue_engine.fallback.rules import DEFAULT_THRESHOLDS

    return float(DEFAULT_THRESHOLDS["duplicate_window_hours"])


def run_set(
    system: System,
    cases: Sequence[EvalCase],
    set_name: str,
    *,
    open_sealed: bool = False,
    judge_id: str | None = None,
    judge_version: str | None = None,
) -> dict[str, Any]:
    """Score ``system`` on the test-split cases. Returns the result document (not yet written).

    Cases are decided in arrival order (``received_at``, then case id, as the field orders
    requests), each with duplicate candidates from the earlier cases of this set; the result
    lists them in the given order."""
    refuse_sealed(cases, set_name, open_sealed)
    times = arrival_times(cases)
    index = DuplicateIndex(_duplicate_window_hours())
    by_id: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    rules_version = None
    for c in sorted(cases, key=lambda c: (times[c.id], c.id)):
        t0 = time.perf_counter()
        pred_ex: dict[str, Any] | None = None
        try:
            pred_ex = _dump(extract_for(system, c))
        except Exception as e:  # a failing extraction is a scored failure, not a crash
            errors[c.id] = f"extract: {type(e).__name__}: {e}"
        t1 = time.perf_counter()
        out: dict[str, Any] = {}
        if pred_ex is not None:
            try:
                candidates = index.candidates(c.id, times[c.id], c.contact, pred_ex)
                out = system.decide(
                    {
                        "extracted": pred_ex,
                        "context": default_context(c.raw_text, pred_ex, candidates),
                    }
                )
                index.add(c.id, times[c.id], c.contact, pred_ex, out.get("priority_tier"))
            except Exception as e:
                out = {}
                errors[c.id] = f"decide: {type(e).__name__}: {e}"
        t2 = time.perf_counter()
        rules_version = out.get("rules_version") or rules_version
        by_id[c.id] = {
            "id": c.id,
            "received_at": times[c.id],
            "gold": c.gold_decision,
            "gold_ex": c.gold_extracted.model_dump(mode="json") if c.gold_extracted else None,
            "group": c.gold_duplicate_group,
            "household": c.gold_duplicate_group or f"case:{c.id}",
            "pred_ex": pred_ex,
            "pred_outcome": out.get("outcome"),
            "pred_priority_tier": out.get("priority_tier"),
            "pred_tier": board_tier(out.get("outcome"), out.get("priority_tier")),
            "provisional_tier": out.get("provisional_tier"),
            "pred_field": follow_up_field(out),
            "duplicate_of": index.case_id(out.get("duplicate_of")),
            "t_extract": t1 - t0,
            "t_decide": t2 - t1,
        }
    rows = [by_id[c.id] for c in cases]
    metrics: dict[str, float] = {}
    failures: dict[str, list[str]] = {}
    metric_n: dict[str, int] = {}
    metric_counts: dict[str, dict[str, int]] = {}
    for prefix, sc in (
        ("extraction", _score_extraction(rows)),
        ("decision", _score_decision(rows)),
        ("calibration", _score_calibration(rows)),
    ):
        metrics.update({f"{prefix}.{k}": v for k, v in sc.metrics.items()})
        failures.update({f"{prefix}.{k}": v for k, v in sc.failures.items()})
        metric_n.update({f"{prefix}.{k}": v for k, v in sc.n.items()})
        metric_counts.update({f"{prefix}.{k}": v for k, v in sc.counts.items()})
    return {
        "system": system.name,
        "set": set_name,
        "run_at": datetime.now(UTC).strftime(TS_FORMAT),
        "n": len(rows),
        "model_version": system.model_version,
        "rules_version": rules_version,
        "judge_id": judge_id,
        "judge_version": judge_version,
        "metrics": metrics,
        "metric_n": metric_n,
        "metric_counts": metric_counts,
        "intervals": _intervals(metric_n, failures),
        "case_ids": [r["id"] for r in rows],
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
    systems = available_systems()
    if args.system not in systems:
        print(f"error: unknown system {args.system!r}; known: {sorted(systems)}", file=sys.stderr)
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
            result = run_set(
                systems[args.system](cases), cases, set_name, open_sealed=args.open_sealed
            )
            print(f"wrote {write_result(result, args.results_dir)} (n={result['n']})")
    except EvalSetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
