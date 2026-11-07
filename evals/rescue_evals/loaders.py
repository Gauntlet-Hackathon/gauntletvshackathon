"""Loader for eval sets A-E (JSONL, one case per line).

Every row is validated; any problem raises ``EvalSetError`` naming file and line.
Refused: set E (sealed holdout) unless ``open_sealed=True`` (logged), and any file
containing a row with ``needs_review: true`` (drafts never count).
Format: see evals/sets/README.md, plus two optional fields described in evals/README.md:
``contact`` (the sender's phone or handle, as the field app receives it) and ``received_at``
(arrival time, ``YYYY-MM-DDTHH:MM:SSZ``). The runner builds the duplicate context from them, in
arrival order, exactly as the field pipeline does (review H-01).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from rescue_shared.models import AbstainReason, Extracted, Tier

log = logging.getLogger("rescue_evals.loaders")

SET_NAMES = ("A", "B", "C", "D", "E")
SEALED_SET = "E"
SPLITS = ("train", "dev", "test")


class EvalSetError(ValueError):
    """The file cannot be used for scoring."""


class SealedHoldoutError(EvalSetError):
    """Set E was requested without --open-sealed."""


class NeedsReviewError(EvalSetError):
    """The file is a draft."""


class GoldDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: Literal["prioritized", "abstain", "not_a_request", "duplicate"]
    priority_tier: Tier | None = None
    abstain_reason: AbstainReason | None = None
    follow_up_field: str | None = None  # the missing field the follow-up should ask for


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    raw_text: str
    gold_extracted: Extracted | None  # null when a set labels only the decision (e.g. set A)
    gold_decision: GoldDecision
    gold_duplicate_group: str | None  # cases sharing a value are the same household
    set: Literal["A", "B", "C", "D", "E"]
    split: Literal["train", "dev", "test"]
    needs_review: Literal[False] | None = None
    # Optional (review H-01): what the field app receives next to the text. Duplicate detection
    # matches on the same location plus the same contact or the same people count (>= 3), among
    # earlier requests in arrival order. Without received_at the runner uses file order.
    contact: str | None = None
    received_at: str | None = None


def _check_decision(case: EvalCase) -> None:
    if case.received_at is not None:
        try:
            datetime.strptime(case.received_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            raise ValueError(
                "received_at must be UTC, seconds precision: YYYY-MM-DDTHH:MM:SSZ"
            ) from None
    d = case.gold_decision
    if d.outcome == "prioritized" and d.priority_tier is None:
        raise ValueError("prioritized needs priority_tier")
    if d.outcome != "prioritized" and d.outcome != "duplicate" and d.priority_tier is not None:
        raise ValueError(f"{d.outcome} must not have priority_tier")
    if (d.outcome == "abstain") != (d.abstain_reason is not None):
        raise ValueError("abstain_reason is set exactly when outcome is abstain")


def _is_sealed_name(path: Path) -> bool:
    stem = path.stem.lower()
    return stem in ("e", "set_e", "set-e", "sete") or "sealed" in stem or "holdout" in stem


def _refuse_sealed(where: str, open_sealed: bool) -> None:
    if not open_sealed:
        raise SealedHoldoutError(
            f"{where}: set E is the sealed holdout; pass open_sealed=True (--open-sealed)"
        )
    log.warning("SEALED HOLDOUT OPENED: %s", where)


def load_cases(
    path: str | Path,
    *,
    open_sealed: bool = False,
    split: str | None = None,
) -> list[EvalCase]:
    """Load and validate one JSONL file. ``split`` filters after validation."""
    path = Path(path)
    if split is not None and split not in SPLITS:
        raise ValueError(f"unknown split {split!r}")
    if _is_sealed_name(path):
        _refuse_sealed(str(path), open_sealed)
        open_sealed = True  # already logged for this file

    cases: list[EvalCase] = []
    seen: set[str] = set()
    sealed_logged = _is_sealed_name(path)
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            where = f"{path}:{n}"
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise EvalSetError(f"{where}: invalid JSON: {e}") from e
            if isinstance(row, dict) and row.get("needs_review") is True:
                raise NeedsReviewError(f"{where}: needs_review is true; drafts never count")
            if isinstance(row, dict) and row.get("set") == SEALED_SET and not sealed_logged:
                _refuse_sealed(where, open_sealed)
                sealed_logged = True
            try:
                case = EvalCase.model_validate(row)
                _check_decision(case)
            except (ValidationError, ValueError) as e:
                raise EvalSetError(f"{where}: {e}") from e
            if case.id in seen:
                raise EvalSetError(f"{where}: duplicate id {case.id!r}")
            seen.add(case.id)
            cases.append(case)
    # a needs_review row anywhere refuses the whole file (checked above while reading);
    # sealed rows after the first valid ones also refuse the whole file (raised above).
    if split is not None:
        cases = [c for c in cases if c.split == split]
    return cases


def load_sets(
    directory: str | Path, *, open_sealed: bool = False, split: str | None = None
) -> dict[str, list[EvalCase]]:
    """Load every ``*.jsonl`` in a directory, grouped by the ``set`` field.

    The sealed file is skipped silently only if it is not requested; without
    ``open_sealed`` a sealed file raises, so a stray holdout never slips into a run.
    """
    out: dict[str, list[EvalCase]] = {}
    for p in sorted(Path(directory).glob("*.jsonl")):
        for c in load_cases(p, open_sealed=open_sealed, split=split):
            out.setdefault(c.set, []).append(c)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate eval set files")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--open-sealed", action="store_true", help="allow set E (logged)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        for f in args.files:
            print(f"{f}: {len(load_cases(f, open_sealed=args.open_sealed))} cases ok")
    except EvalSetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
