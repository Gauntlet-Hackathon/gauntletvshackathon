"""Every JSON Schema in contracts/ and every Pydantic model in rescue_shared.models are checked
against the same example files, contracts/examples/<key>.valid.json and <key>.invalid.json.
The Elixir cloud tests read the same files."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from rescue_shared import models as m
from rescue_shared.contracts_io import CONTRACTS, load_json, validator_for

# example key -> (schema path under contracts/, Pydantic model)
CONTRACT_MAP = {
    "event": ("event.schema.json", m.Event),
    "payload-received": ("payloads/received.schema.json", m.Received),
    "payload-extracted": ("payloads/extracted.schema.json", m.Extracted),
    "payload-decided": ("payloads/decided.schema.json", m.Decided),
    "payload-assigned": ("payloads/assigned.schema.json", m.Assigned),
    "payload-rescued": ("payloads/rescued.schema.json", m.Rescued),
    "payload-corrected": ("payloads/corrected.schema.json", m.Corrected),
    "payload-escalated": ("payloads/escalated.schema.json", m.Escalated),
    "payload-closed_duplicate": ("payloads/closed_duplicate.schema.json", m.ClosedDuplicate),
    "engine_input": ("engine_input.schema.json", m.EngineInput),
    "engine_output": ("engine_output.schema.json", m.EngineOutput),
    "engine_manifest": ("engine_manifest.schema.json", m.EngineManifest),
    "sync-health_response": ("sync/health_response.schema.json", m.HealthResponse),
    "sync-heartbeat_request": ("sync/heartbeat_request.schema.json", m.HeartbeatRequest),
    "sync-heartbeat_response": ("sync/heartbeat_response.schema.json", m.HeartbeatResponse),
    "sync-events_request": ("sync/events_request.schema.json", m.EventsRequest),
    "sync-events_response": ("sync/events_response.schema.json", m.EventsResponse),
    "sync-down_response": ("sync/down_response.schema.json", m.DownResponse),
    "sync-error_response": ("sync/error_response.schema.json", m.ErrorResponse),
}
KEYS = sorted(CONTRACT_MAP)


def example(key: str, kind: str):
    return load_json(CONTRACTS / "examples" / f"{key}.{kind}.json")


def test_every_schema_is_mapped_and_has_both_examples():
    schemas = {p.relative_to(CONTRACTS).as_posix() for p in CONTRACTS.rglob("*.schema.json")}
    assert schemas - {"common.schema.json"} == {path for path, _ in CONTRACT_MAP.values()}
    files = {p.name for p in (CONTRACTS / "examples").glob("*.json")}
    expected = {f"{key}.{kind}.json" for key in KEYS for kind in ("valid", "invalid")}
    assert files == expected


@pytest.mark.parametrize("key", KEYS)
def test_schema_is_a_valid_draft_2020_12_schema(key):
    validator_for(CONTRACT_MAP[key][0]).check_schema(load_json(CONTRACTS / CONTRACT_MAP[key][0]))


@pytest.mark.parametrize("key", KEYS)
def test_valid_example_passes_schema(key):
    validator = validator_for(CONTRACT_MAP[key][0])
    assert list(validator.iter_errors(example(key, "valid"))) == []


@pytest.mark.parametrize("key", KEYS)
def test_invalid_example_fails_schema(key):
    validator = validator_for(CONTRACT_MAP[key][0])
    assert list(validator.iter_errors(example(key, "invalid"))) != []


@pytest.mark.parametrize("key", KEYS)
def test_valid_example_round_trips_through_model(key):
    data = example(key, "valid")
    model = CONTRACT_MAP[key][1].model_validate(data)
    assert model.model_dump(mode="json") == data


@pytest.mark.parametrize("key", KEYS)
def test_invalid_example_fails_model(key):
    with pytest.raises(ValidationError):
        CONTRACT_MAP[key][1].model_validate(example(key, "invalid"))


PAYLOAD_TYPES = [
    "received", "extracted", "decided", "assigned", "rescued", "corrected", "escalated",
    "closed_duplicate",
]  # fmt: skip


@pytest.mark.parametrize("event_type", PAYLOAD_TYPES)
def test_envelope_accepts_each_payload_type_and_rejects_the_wrong_one(event_type):
    validator = validator_for("event.schema.json")
    envelope = example("event", "valid")
    envelope["type"] = event_type
    envelope["payload"] = example(f"payload-{event_type}", "valid")
    assert list(validator.iter_errors(envelope)) == []
    assert m.Event.model_validate(envelope).parsed_payload() is not None

    other = PAYLOAD_TYPES[(PAYLOAD_TYPES.index(event_type) + 1) % len(PAYLOAD_TYPES)]
    envelope["payload"] = example(f"payload-{other}", "valid")
    assert list(validator.iter_errors(envelope)) != []
    with pytest.raises(ValidationError):
        m.Event.model_validate(envelope)


def _output(**changes):
    data = copy.deepcopy(example("engine_output", "valid"))
    data.update(changes)
    return data


# Engine outputs that break the per-outcome rules: both the schema and the model must reject them.
BAD_OUTPUTS = {
    "prioritized without tier": _output(
        outcome="prioritized", duplicate_of=None, priority_tier=None
    ),
    "prioritized with duplicate_of": _output(outcome="prioritized"),
    "duplicate without duplicate_of": _output(duplicate_of=None),
    "duplicate without tier": _output(priority_tier=None),
    "abstain with tier": _output(
        outcome="abstain", duplicate_of=None, abstain_reason="possible_hoax",
        follow_up_question="Real?",
    ),  # fmt: skip
    "abstain without reason": _output(
        outcome="abstain", priority_tier=None, duplicate_of=None, follow_up_question="Real?"
    ),
    "abstain without question": _output(
        outcome="abstain", priority_tier=None, duplicate_of=None, abstain_reason="possible_hoax"
    ),
    "not_a_request with tier": _output(outcome="not_a_request", duplicate_of=None),
    "abstain without provisional_tier": _output(
        outcome="abstain", priority_tier=None, duplicate_of=None, abstain_reason="possible_hoax",
        follow_up_question="Real?",
    ),  # fmt: skip
    "duplicate with provisional_tier": _output(provisional_tier="P1"),
    "missing provisional_tier": {
        k: v for k, v in example("engine_output", "valid").items() if k != "provisional_tier"
    },
    "unknown abstain reason": _output(
        outcome="abstain", priority_tier=None, duplicate_of=None,
        abstain_reason="vibes", follow_up_question="Real?",
    ),  # fmt: skip
}


@pytest.mark.parametrize("name", sorted(BAD_OUTPUTS))
def test_engine_output_outcome_rules(name):
    data = BAD_OUTPUTS[name]
    assert list(validator_for("engine_output.schema.json").iter_errors(data)) != []
    with pytest.raises(ValidationError):
        m.EngineOutput.model_validate(data)


def test_engine_output_keeps_priority_tier_on_duplicates():
    out = m.EngineOutput.model_validate(example("engine_output", "valid"))
    assert out.outcome == "duplicate" and out.priority_tier == "P1"
