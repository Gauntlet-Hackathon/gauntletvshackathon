import json
import logging

import pytest

from rescue_evals.loaders import (
    EvalSetError,
    NeedsReviewError,
    SealedHoldoutError,
    load_cases,
    load_sets,
    main,
)

EXTRACTED = {
    "location": {"text": "12 Oak St", "type": "address", "confidence": 0.9},
    "people_count": {"value": 3, "confidence": 0.9},
    "vulnerable": [{"kind": "child", "confidence": 0.8}],
    "hazards": [{"kind": "rising_water", "confidence": 0.9}],
    "time_ref": {"text": None, "is_stale_post": False, "confidence": 0.9},
    "is_request": {"value": True, "confidence": 0.95},
    "spans": [],
    "needs": [],
    "request_kind": "rescue",
}


def row(**kw):
    base = {
        "id": "c1",
        "raw_text": "water rising at 12 Oak St, 3 of us",
        "gold_extracted": EXTRACTED,
        "gold_decision": {"outcome": "prioritized", "priority_tier": "P1"},
        "gold_duplicate_group": None,
        "set": "B",
        "split": "test",
    }
    base.update(kw)
    return base


def write(tmp_path, rows, name="b.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_loads_valid_rows(tmp_path):
    p = write(tmp_path, [row(), row(id="c2", split="dev", gold_extracted=None)])
    cases = load_cases(p)
    assert [c.id for c in cases] == ["c1", "c2"]
    assert cases[0].gold_extracted.people_count.value == 3
    assert [c.id for c in load_cases(p, split="test")] == ["c1"]


@pytest.mark.parametrize(
    "bad",
    [
        row(id=None),
        row(set="F"),
        row(split="holdout"),
        row(extra=1),
        row(gold_decision={"outcome": "prioritized"}),
        row(gold_decision={"outcome": "abstain", "priority_tier": None}),
        row(gold_decision={"outcome": "not_a_request", "priority_tier": "P2"}),
        row(gold_extracted={**EXTRACTED, "request_kind": "bogus"}),
    ],
)
def test_invalid_row_refused_with_line(tmp_path, bad):
    p = write(tmp_path, [row(id="ok"), bad])
    with pytest.raises(EvalSetError, match=r":2:"):
        load_cases(p)


def test_bad_json_and_duplicate_ids(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("{nope\n")
    with pytest.raises(EvalSetError, match="invalid JSON"):
        load_cases(p)
    with pytest.raises(EvalSetError, match="duplicate id"):
        load_cases(write(tmp_path, [row(), row()]))


def test_set_e_refused_unless_open_sealed(tmp_path, caplog):
    p = write(tmp_path, [row(set="E")], name="misc.jsonl")
    with pytest.raises(SealedHoldoutError):
        load_cases(p)
    with caplog.at_level(logging.WARNING):
        assert len(load_cases(p, open_sealed=True)) == 1
    assert "SEALED HOLDOUT OPENED" in caplog.text


def test_sealed_filename_refused_before_reading(tmp_path):
    p = tmp_path / "sealed_holdout.jsonl"
    p.write_text("not even json\n")
    with pytest.raises(SealedHoldoutError):
        load_cases(p)


def test_set_e_row_late_in_file_refuses_file(tmp_path):
    p = write(tmp_path, [row(), row(id="c2", set="E")])
    with pytest.raises(SealedHoldoutError):
        load_cases(p)


def test_needs_review_refused(tmp_path):
    p = write(tmp_path, [row(), row(id="c2", needs_review=True)])
    with pytest.raises(NeedsReviewError):
        load_cases(p)
    # even if the row would otherwise be invalid, drafts are refused first
    p = write(tmp_path, [{"needs_review": True}])
    with pytest.raises(NeedsReviewError):
        load_cases(p, open_sealed=True)
    # explicit false is a normal row
    assert len(load_cases(write(tmp_path, [row(needs_review=False)], name="ok.jsonl"))) == 1


def test_load_sets_groups_and_guards(tmp_path):
    write(tmp_path, [row(), row(id="d", set="D")], name="mix.jsonl")
    got = load_sets(tmp_path)
    assert sorted(got) == ["B", "D"]
    write(tmp_path, [row(id="e1", set="E")], name="e.jsonl")
    with pytest.raises(SealedHoldoutError):
        load_sets(tmp_path)
    assert "E" in load_sets(tmp_path, open_sealed=True)


def test_cli(tmp_path, capsys):
    p = write(tmp_path, [row(set="E")], name="m.jsonl")
    assert main([str(p)]) == 1
    assert "sealed" in capsys.readouterr().err
    assert main([str(p), "--open-sealed"]) == 0
