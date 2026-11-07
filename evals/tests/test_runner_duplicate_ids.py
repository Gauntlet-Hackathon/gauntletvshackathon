"""Duplicate candidates reach the engine with ULID-shaped request ids (the engine contract), and
``duplicate_of`` comes back as the case id. Before this, every duplicate outcome from a system
whose engine client validates its output (``ours``) failed with a schema error."""

import json

from rescue_engine.fallback.engine import decide_one
from rescue_evals.loaders import load_cases
from rescue_evals.runner import DuplicateIndex, GoldExtractionSystem, run_set
from rescue_shared.contracts_io import validator_for


def ex(loc, people=4):
    return {
        "location": {"text": loc, "type": "address", "confidence": 0.9},
        "people_count": {"value": people, "confidence": 0.9},
        "vulnerable": [],
        "hazards": [{"kind": "fire", "confidence": 0.9}],
        "time_ref": {"text": None, "is_stale_post": False, "confidence": 0.9},
        "is_request": {"value": True, "confidence": 0.95},
        "spans": [],
        "needs": [],
        "request_kind": "rescue",
    }


class ValidatingSystem(GoldExtractionSystem):
    """Gold extraction, but the engine input and output are checked against the contracts, as
    the field engine client (used by ``ours``) does."""

    def decide(self, engine_input):
        validator_for("engine_input.schema.json").validate(engine_input)
        out = decide_one(engine_input)
        validator_for("engine_output.schema.json").validate(out)
        return out


def test_request_id_is_a_stable_ulid():
    rid = DuplicateIndex.request_id("B-077")
    assert rid == DuplicateIndex.request_id("B-077") != DuplicateIndex.request_id("B-078")
    validator_for("common.schema.json").evolve(
        schema={"$ref": "common.schema.json#/$defs/ulid"}
    ).validate(rid)


def test_duplicate_scored_through_a_validating_engine_client(tmp_path):
    rows = [
        {"id": "B-077", "raw_text": "fire at 4300 Dixie Dr, 4 of us",
         "gold_extracted": ex("4300 Dixie Dr"),
         "gold_decision": {"outcome": "prioritized", "priority_tier": "P1"},
         "gold_duplicate_group": "g1", "set": "B", "split": "test"},
        {"id": "B-078", "raw_text": "4300 Dixie Dr. fire, 4 people",
         "gold_extracted": ex("4300 Dixie Dr."),
         "gold_decision": {"outcome": "duplicate", "priority_tier": "P1"},
         "gold_duplicate_group": "g1", "set": "B", "split": "test"},
    ]
    p = tmp_path / "b.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    cases = load_cases(p)
    res = run_set(ValidatingSystem(cases), cases, "B")
    assert res["errors"] == {}
    assert res["metrics"]["decision.duplicate_pairwise_f1"] == 1.0
