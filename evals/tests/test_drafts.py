"""Guards for the hard/abstain draft candidate files (sets C and D, for human review)."""

import json
from pathlib import Path

import pytest

from rescue_evals.loaders import EvalCase, NeedsReviewError, _check_decision, load_cases

ROOT = Path(__file__).resolve().parents[1]
DRAFTS = ROOT / "drafts"
FILES = {"hard_candidates.jsonl": "C", "abstain_candidates.jsonl": "D"}


def rows(name):
    return [
        json.loads(x) for x in (DRAFTS / name).read_text(encoding="utf-8").splitlines() if x.strip()
    ]


@pytest.mark.parametrize("name", FILES)
def test_loader_refuses_drafts(name):
    with pytest.raises(NeedsReviewError):
        load_cases(DRAFTS / name)


@pytest.mark.parametrize("name,sset", FILES.items())
def test_every_row_needs_review_and_valid_once_cleared(name, sset):
    data = rows(name)
    ids = [r["id"] for r in data]
    assert len(ids) == len(set(ids))
    for r in data:
        assert r["needs_review"] is True
        assert r["set"] == sset
        assert r["raw_text"].strip()
        r2 = dict(r, needs_review=None)  # what a human clearing the flag produces
        _check_decision(EvalCase.model_validate(r2))


def test_counts():
    assert 95 <= len(rows("hard_candidates.jsonl")) <= 110
    assert 55 <= len(rows("abstain_candidates.jsonl")) <= 70


def test_abstain_rows_all_abstain_with_follow_up():
    data = rows("abstain_candidates.jsonl")
    for r in data:
        d = r["gold_decision"]
        assert d["outcome"] == "abstain" and d["follow_up_field"]
    assert {r["gold_decision"]["abstain_reason"] for r in data} == {
        "no_usable_location",
        "conflicting_counts",
        "possible_hoax",
        "low_confidence_request",
        "low_confidence_hazard",
    }


def test_hard_covers_planned_categories():
    data = rows("hard_candidates.jsonl")
    text = "\n".join(r["raw_text"] for r in data).lower()
    for marker in ("watr", "ayuda", "cứu", "kroger", "repost", "last year", "lol"):
        assert marker in text, marker
    outcomes = {r["gold_decision"]["outcome"] for r in data}
    assert {"prioritized", "duplicate", "not_a_request"} <= outcomes
    assert any(r["gold_extracted"]["location"]["type"] == "landmark" for r in data)
    groups = [r["gold_duplicate_group"] for r in data if r["gold_duplicate_group"]]
    assert groups and all(groups.count(g) == 2 for g in set(groups))


def test_nothing_under_sets():
    for p in (ROOT / "sets").glob("*.jsonl"):
        assert all(
            json.loads(x).get("needs_review") is not True
            for x in p.read_text(encoding="utf-8").splitlines()
            if x.strip()
        )
        assert p.name not in FILES
