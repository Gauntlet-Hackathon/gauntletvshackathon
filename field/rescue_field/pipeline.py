"""Intake pipeline: raw text -> received, extracted, decided events in one transaction.

The engine only ever sees `{extracted, context}`; raw text stays in the `received` event.
Context is built with rescue_shared.logic so field, cloud and evals agree on it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from field_app import engine_client
from field_app.slm import Extractor, FixtureExtractor
from field_app.store import EventStore
from rescue_shared import logic
from rescue_shared.contracts_io import CONTRACTS, validator_for
from rescue_shared.ulid import new_ulid

# Defaults from contracts/engine_rules.md (the values the fixtures use).
DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "request_confidence_min": 0.6,
    "stale_confidence_min": 0.6,
    "hoax_marker_min": 1,
    "hoax_request_confidence_max": 0.85,
    "location_confidence_min": 0.6,
    "hazard_firm_min": 0.75,
    "hazard_uncertain_min": 0.5,
    "hazard_weak_min": 0.25,
    "need_hours_over": 24,
    "duplicate_window_hours": 12,
}
DEFAULT_STREETS_PATH = CONTRACTS / "fixtures" / "streets.txt"
DEFAULT_HOAX_MARKERS_PATH = CONTRACTS / "fixtures" / "hoax_markers.txt"
DEFAULT_DEVICE_ID = "field-01"

Decider = Callable[[dict[str, Any]], dict[str, Any]]


def read_list(path: str | Path) -> tuple[str, ...]:
    """Non-empty, non-comment lines of a text file."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return tuple(s for s in (ln.strip() for ln in lines) if s and not s.startswith("#"))


@dataclass(frozen=True)
class PipelineConfig:
    device_id: str = DEFAULT_DEVICE_ID
    thresholds: Mapping[str, float | int] = field(
        default_factory=lambda: dict(DEFAULT_THRESHOLDS)
    )
    streets: tuple[str, ...] = field(default_factory=lambda: read_list(DEFAULT_STREETS_PATH))
    hoax_markers: tuple[str, ...] = field(
        default_factory=lambda: read_list(DEFAULT_HOAX_MARKERS_PATH)
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> PipelineConfig:
        """FIELD_DEVICE_ID, FIELD_STREETS_PATH, FIELD_HOAX_MARKERS_PATH override the defaults."""
        env = os.environ if env is None else env
        return cls(
            device_id=env.get("FIELD_DEVICE_ID", DEFAULT_DEVICE_ID),
            streets=read_list(env.get("FIELD_STREETS_PATH", DEFAULT_STREETS_PATH)),
            hoax_markers=read_list(env.get("FIELD_HOAX_MARKERS_PATH", DEFAULT_HOAX_MARKERS_PATH)),
        )


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _known_requests(store: EventStore) -> list[dict[str, Any]]:
    """Earlier requests in the shape logic.duplicate_candidates expects."""
    by_id: dict[str, dict[str, Any]] = {}
    for ev in store.all_events():
        rec = by_id.setdefault(ev["request_id"], {"request_id": ev["request_id"]})
        payload = ev["payload"]
        if ev["type"] == "received":
            rec["received_at"] = ev["occurred_at"]
            rec["contact"] = payload.get("contact")
        elif ev["type"] == "extracted":
            rec["location_text"] = payload["location"]["text"]
            rec["people_count"] = payload["people_count"]["value"]
        elif ev["type"] == "decided":
            rec["priority_tier"] = payload["engine_output"]["priority_tier"]
    return [r for r in by_id.values() if "received_at" in r and "location_text" in r]


def build_context(
    *,
    request_id: str,
    received_at: str,
    raw_text: str,
    contact: str | None,
    extracted: Mapping[str, Any],
    prior: list[dict[str, Any]],
    config: PipelineConfig,
) -> dict[str, Any]:
    thresholds = dict(config.thresholds)
    current = {
        "request_id": request_id,
        "received_at": received_at,
        "location_text": extracted["location"]["text"],
        "people_count": extracted["people_count"]["value"],
        "contact": contact,
        "priority_tier": None,
    }
    others = [p for p in prior if p["request_id"] != request_id]
    candidates = logic.duplicate_candidates(
        [*others, current], thresholds["duplicate_window_hours"]
    )[request_id]
    return {
        "duplicate_candidates": candidates,
        "street_list_hits": logic.street_list_hits(extracted["location"]["text"], config.streets),
        # Markers are counted on the raw text here; only the count reaches the engine.
        "hoax_marker_hits": logic.hoax_marker_hits(raw_text, config.hoax_markers),
        "thresholds": thresholds,
    }


def _validate_event(event: dict[str, Any]) -> None:
    errors = list(validator_for("event.schema.json").iter_errors(event))
    if errors:
        raise ValueError(f"{event['type']} event failed schema validation: {errors[0].message}")


def process_request(
    raw: str,
    channel: str,
    *,
    store: EventStore,
    extractor: Extractor | None = None,
    decide: Decider | None = None,
    config: PipelineConfig | None = None,
    contact: str | None = None,
    lang_hint: str | None = None,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Extract, decide and store one request. Returns [received, extracted, decided].

    Nothing is stored unless all three events are valid and the engine succeeded
    (engine_client.EngineError and ValueError propagate).
    """
    config = config or PipelineConfig()
    extractor = extractor or FixtureExtractor()
    decide = decide or engine_client.decide
    received_at = now or _now()
    request_id = new_ulid()

    extraction = extractor.extract(raw)
    extracted = extraction.payload
    context = build_context(
        request_id=request_id,
        received_at=received_at,
        raw_text=raw,
        contact=contact,
        extracted=extracted,
        prior=_known_requests(store),
        config=config,
    )
    engine_input = {"extracted": extracted, "context": context}
    engine_output = decide(engine_input)

    def event(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": new_ulid(),
            "request_id": request_id,
            "type": kind,
            "device_id": config.device_id,
            "occurred_at": received_at,
            "rules_version": engine_output["rules_version"],
            "model_version": extraction.model_version,
            "payload": payload,
        }

    events = [
        event(
            "received",
            {"channel": channel, "raw_text": raw, "lang_hint": lang_hint, "contact": contact},
        ),
        event("extracted", extracted),
        event("decided", {"engine_input": engine_input, "engine_output": engine_output}),
    ]
    for ev in events:
        _validate_event(ev)
    store.append_events(events)
    return events
