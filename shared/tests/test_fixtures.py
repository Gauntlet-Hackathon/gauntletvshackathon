"""The 25 hand-written rubric cases validate against the contracts and are internally consistent.
`expected` is never checked here: it was written by hand from the rubric, and only an engine
(engine lane tests) is compared against it."""

from __future__ import annotations

import json
from collections import Counter

import pytest

from rescue_shared import logic
from rescue_shared.contracts_io import CONTRACTS, validator_for
from rescue_shared.models import EngineInput, EngineOutput, Event, Extracted, Received

FIXTURES = CONTRACTS / "fixtures"
CASES = [json.loads(line) for line in (FIXTURES / "cases.jsonl").read_text().splitlines() if line]
STREETS = [s for s in (FIXTURES / "streets.txt").read_text().splitlines() if s]
MARKERS = [s for s in (FIXTURES / "hoax_markers.txt").read_text().splitlines() if s]
IDS = [c["case_id"] for c in CASES]


def test_25_unique_cases():
    assert IDS == [f"C{n:02d}" for n in range(1, 26)]
    assert len({c["request_id"] for c in CASES}) == 25


def test_coverage_required_by_the_ticket():
    expected = [c["expected"] for c in CASES]
    tiers = Counter(e["priority_tier"] for e in expected if e["outcome"] == "prioritized")
    assert all(tiers[t] >= 2 for t in ("P1", "P2", "P3"))
    assert sum(e["outcome"] == "not_a_request" for e in expected) == 2
    reasons = Counter(e["abstain_reason"] for e in expected if e["outcome"] == "abstain")
    assert set(reasons) == {
        "low_confidence_request", "no_usable_location", "low_confidence_hazard",
        "conflicting_counts", "possible_hoax",
    }  # fmt: skip
    assert all(n == 2 for n in reasons.values())
    duplicates = [c for c in CASES if c["expected"]["outcome"] == "duplicate"]
    assert len({c["expected"]["duplicate_of"] for c in duplicates}) == 3
    assert sum("prompt injection" in c["description"].lower() for c in CASES) == 1


def test_street_and_marker_lists():
    assert 45 <= len(STREETS) <= 60 and len(set(STREETS)) == len(STREETS)
    assert len(set(MARKERS)) == len(MARKERS) >= 5


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_case_validates_against_schemas_and_models(case):
    engine_input = {"extracted": case["extracted"], "context": case["context"]}
    assert list(validator_for("engine_input.schema.json").iter_errors(engine_input)) == []
    assert list(validator_for("engine_output.schema.json").iter_errors(case["expected"])) == []
    extracted_validator = validator_for("payloads/extracted.schema.json")
    assert list(extracted_validator.iter_errors(case["extracted"])) == []
    EngineInput.model_validate(engine_input)
    Extracted.model_validate(case["extracted"])
    EngineOutput.model_validate(case["expected"])

    envelope = {
        "event_id": "01JCB00000000000000000EV01",
        "request_id": case["request_id"],
        "type": "received",
        "device_id": "field-03",
        "occurred_at": case["received_at"],
        "rules_version": case["expected"]["rules_version"],
        "model_version": "stub:fixture-extractor",
        "payload": {
            "channel": "sms",
            "raw_text": case["raw_text"],
            "lang_hint": None,
            "contact": case["contact"],
        },
    }
    assert list(validator_for("event.schema.json").iter_errors(envelope)) == []
    Event.model_validate(envelope)
    Received.model_validate(envelope["payload"])


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_spans_street_hits_and_hoax_counts_match_the_text(case):
    extracted, raw = case["extracted"], case["raw_text"]
    location = extracted["location"]
    for span in extracted["spans"]:
        if span["label"] == "LOCATION":
            assert raw[span["start"] : span["end"]] == location["text"]
    if location["text"] is not None:
        assert any(s["label"] == "LOCATION" for s in extracted["spans"])
    assert case["context"]["street_list_hits"] == logic.street_list_hits(location["text"], STREETS)
    assert case["context"]["hoax_marker_hits"] == logic.hoax_marker_hits(raw, MARKERS)


def test_duplicate_candidates_match_the_shared_logic_over_the_whole_set():
    requests = [
        {
            "request_id": c["request_id"],
            "received_at": c["received_at"],
            "location_text": c["extracted"]["location"]["text"],
            "people_count": c["extracted"]["people_count"]["value"],
            "contact": c["contact"],
            "priority_tier": c["expected"]["priority_tier"],
        }
        for c in CASES
    ]
    window = CASES[0]["context"]["thresholds"]["duplicate_window_hours"]
    candidates = logic.duplicate_candidates(requests, window)
    for case in CASES:
        found = candidates[case["request_id"]]
        assert case["context"]["duplicate_candidates"] == found, case["case_id"]
