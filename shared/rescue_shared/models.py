"""Pydantic models for every contract in contracts/.

They mirror the JSON Schemas exactly: extra fields are rejected, nullable fields are
required-but-nullable, and a valid example round-trips
(`Model.model_validate(x).model_dump(mode="json") == x`).
Tests check the models and the schemas against the same files in contracts/examples/.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ULID = Annotated[str, StringConstraints(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")]
Timestamp = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")]
Confidence = Annotated[float, Field(ge=0, le=1)]
DeviceId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")]
RulesVersion = Annotated[
    str, StringConstraints(pattern=r"^rules@[0-9a-f]{7,40}\+bin:[0-9a-f]{8,64}$")
]
ModelVersion = Annotated[
    str,
    StringConstraints(
        pattern=r"^(hf:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{7,40}|stub:[a-z0-9_-]+)$"
    ),
]
RuleId = Annotated[str, StringConstraints(pattern=r"^R-(P[123]|ABS|NAR|DUP)-[A-Z0-9]+$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
Count = Annotated[int, Field(ge=0)]

Tier = Literal["P1", "P2", "P3"]
AbstainReason = Literal[
    "low_confidence_request",
    "no_usable_location",
    "low_confidence_hazard",
    "conflicting_counts",
    "possible_hoax",
]
EventType = Literal[
    "received",
    "extracted",
    "decided",
    "assigned",
    "rescued",
    "corrected",
    "escalated",
    "closed_duplicate",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# ---- extracted payload (SLM output contract) ----


class Location(Contract):
    text: str | None
    type: Literal["landmark", "address", "coords", "none"]
    confidence: Confidence


class PeopleCount(Contract):
    value: Count | None
    confidence: Confidence


class Vulnerable(Contract):
    kind: Literal["child", "elderly", "disabled", "medical", "pregnant"]
    confidence: Confidence


class Hazard(Contract):
    kind: Literal["rising_water", "on_roof", "injury", "medical_device_no_power", "fire", "trapped"]
    confidence: Confidence


class TimeRef(Contract):
    text: str | None
    is_stale_post: bool
    confidence: Confidence


class IsRequest(Contract):
    value: bool
    confidence: Confidence


class Span(Contract):
    start: Count
    end: Count
    label: Annotated[str, StringConstraints(pattern=r"^[A-Z_]+$")]


class Need(Contract):
    kind: Literal["food", "water", "meds"]
    hours_without: Annotated[float, Field(ge=0)] | None


class Extracted(Contract):
    location: Location
    people_count: PeopleCount
    vulnerable: list[Vulnerable]
    hazards: list[Hazard]
    time_ref: TimeRef
    is_request: IsRequest
    spans: list[Span]
    needs: list[Need]
    request_kind: Literal["rescue", "evacuation", "welfare_check", "supply", "other"]


# ---- engine ----


class Thresholds(Contract):
    request_confidence_min: Confidence
    stale_confidence_min: Confidence
    hoax_marker_min: Annotated[int, Field(ge=1)]
    hoax_request_confidence_max: Confidence
    location_confidence_min: Confidence
    hazard_firm_min: Confidence
    hazard_uncertain_min: Confidence
    hazard_weak_min: Confidence
    need_hours_over: Annotated[float, Field(ge=0)]
    duplicate_window_hours: Annotated[float, Field(gt=0)]


class DuplicateCandidate(Contract):
    request_id: ULID
    received_at: Timestamp
    people_count: Count | None
    contact_match: bool
    priority_tier: Tier | None


class EngineContext(Contract):
    duplicate_candidates: list[DuplicateCandidate]
    street_list_hits: list[NonEmpty]
    hoax_marker_hits: Count
    thresholds: Thresholds


class EngineInput(Contract):
    extracted: Extracted
    context: EngineContext


class EngineOutput(Contract):
    outcome: Literal["prioritized", "abstain", "not_a_request", "duplicate"]
    priority_tier: Tier | None
    provisional_tier: Tier | None
    rule_id: RuleId
    duplicate_of: ULID | None
    abstain_reason: AbstainReason | None
    follow_up_question: NonEmpty | None
    rules_version: RulesVersion

    @model_validator(mode="after")
    def _outcome_shape(self) -> EngineOutput:
        o = self.outcome
        if o in ("prioritized", "duplicate") and self.priority_tier is None:
            raise ValueError(f"{o} needs a priority_tier")
        if o in ("abstain", "not_a_request") and self.priority_tier is not None:
            raise ValueError(f"{o} must not have a priority_tier")
        if (o == "abstain") != (self.provisional_tier is not None):
            raise ValueError("provisional_tier is set exactly when outcome is abstain")
        if (o == "duplicate") != (self.duplicate_of is not None):
            raise ValueError("duplicate_of is set exactly when outcome is duplicate")
        if (o == "abstain") != (self.abstain_reason is not None):
            raise ValueError("abstain_reason is set exactly when outcome is abstain")
        if o == "abstain" and self.follow_up_question is None:
            raise ValueError("abstain needs a follow_up_question")
        if o == "not_a_request" and self.follow_up_question is not None:
            raise ValueError("not_a_request has no follow_up_question")
        return self


class EngineManifest(Contract):
    rules_version: RulesVersion
    rules_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{7,40}$")]
    bin_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8,64}$")]
    artifact_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8,64}$")] | None = None
    config_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8,64}$")] | None = None
    engine_kind: Literal["python", "mercury"]
    entrypoint: NonEmpty
    built_at: Timestamp

    @model_validator(mode="after")
    def _hashes_together(self) -> EngineManifest:
        if (self.artifact_hash is None) != (self.config_hash is None):
            raise ValueError("artifact_hash and config_hash are set together")
        return self


# ---- event payloads ----


class Received(Contract):
    channel: Literal["sms", "social", "form", "call_note", "csv"]
    raw_text: NonEmpty
    lang_hint: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}$")] | None
    contact: NonEmpty | None


class Decided(Contract):
    engine_input: EngineInput
    engine_output: EngineOutput


class Assigned(Contract):
    crew: NonEmpty
    note: str | None


class Rescued(Contract):
    rescued_count: Count | None
    note: str | None


class Escalated(Contract):
    reason: NonEmpty
    note: str | None


class Corrected(Contract):
    field: NonEmpty
    old_value: Any
    new_value: Any
    corrected_by: NonEmpty
    note: str | None


class ClosedDuplicate(Contract):
    duplicate_of: ULID
    note: str | None


PAYLOAD_MODELS: dict[str, type[Contract]] = {
    "received": Received,
    "extracted": Extracted,
    "decided": Decided,
    "assigned": Assigned,
    "rescued": Rescued,
    "corrected": Corrected,
    "escalated": Escalated,
    "closed_duplicate": ClosedDuplicate,
}


class Event(Contract):
    """The envelope. `payload` stays a plain dict (so it round-trips untouched) and is validated
    against the model for its `type`; use parsed_payload() for the typed object."""

    event_id: ULID
    request_id: ULID
    type: EventType
    device_id: DeviceId
    occurred_at: Timestamp
    rules_version: RulesVersion | None
    model_version: ModelVersion | None
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _payload_matches_type(self) -> Event:
        PAYLOAD_MODELS[self.type].model_validate(self.payload)
        return self

    def parsed_payload(self) -> Contract:
        return PAYLOAD_MODELS[self.type].model_validate(self.payload)


# ---- sync API ----


class HealthResponse(Contract):
    status: Literal["ok"]
    time: Timestamp


class HeartbeatRequest(Contract):
    device_id: DeviceId
    sent_at: Timestamp
    outbox_backlog: Count
    rules_version: RulesVersion
    model_version: ModelVersion


class HeartbeatResponse(Contract):
    ok: Literal[True]
    server_time: Timestamp


class EventsRequest(Contract):
    device_id: DeviceId
    events: Annotated[list[Event], Field(min_length=1, max_length=200)]


class Rejected(Contract):
    event_id: NonEmpty
    error: NonEmpty


class EventsResponse(Contract):
    acked: list[ULID]
    rejected: list[Rejected]


class Approved(Contract):
    rules_version: RulesVersion | None
    model_version: ModelVersion | None


class DownResponse(Contract):
    next_since: Count
    events: list[Event]
    approved: Approved


class ErrorResponse(Contract):
    error: Annotated[str, StringConstraints(pattern=r"^[a-z_]+$")]
    detail: str | None = None
