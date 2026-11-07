"""Intake pipeline: raw text -> received, extracted, decided events in one transaction.

The engine only ever sees `{extracted, context}`; raw text stays in the `received` event.
Context is built with rescue_shared.logic so field, cloud and evals agree on it.
"""

from __future__ import annotations

import logging
import math
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rescue_field import engine_client
from rescue_field.slm import Extraction, Extractor, FixtureExtractor, KeywordExtractor
from rescue_field.store import EventStore
from rescue_shared import frozen_config, logic
from rescue_shared.contracts_io import CONTRACTS, validator_for
from rescue_shared.ulid import new_ulid

# Defaults from contracts/engine_rules.md (the values the fixtures use), frozen with the engine
# build in contracts/fixtures/thresholds.json (DECISIONS H12 / R15).
DEFAULT_THRESHOLDS_PATH = frozen_config.FIXTURES_DIR / frozen_config.CONFIG_FILES["thresholds"]
DEFAULT_THRESHOLDS: dict[str, float | int] = frozen_config.read_thresholds(DEFAULT_THRESHOLDS_PATH)
DEFAULT_STREETS_PATH = CONTRACTS / "fixtures" / "streets.txt"
DEFAULT_HOAX_MARKERS_PATH = CONTRACTS / "fixtures" / "hoax_markers.txt"
# The one default device id for the field app (DECISIONS A5): the pipeline stamps it on events
# and the sync worker authenticates as it, so the two must agree (CLAUDE.md dev device).
DEFAULT_DEVICE_ID = "laptop-dev-01"
# Incident numbers (#308): PREFIX-0001, PREFIX-0002, ... from a per-laptop SQLite counter.
INCIDENT_COUNTER = "incident"
_PREFIX_RE = re.compile(r"^[A-Z0-9]{1,4}$")
log = logging.getLogger(__name__)

Decider = Callable[[dict[str, Any]], dict[str, Any]]
BatchDecider = Callable[[Sequence[dict[str, Any]]], list[dict[str, Any]]]


def read_list(path: str | Path) -> tuple[str, ...]:
    """Non-empty, non-comment lines of a text file (the parsing config_hash covers)."""
    return frozen_config.read_list(path)


@dataclass(frozen=True)
class PipelineConfig:
    device_id: str = DEFAULT_DEVICE_ID
    thresholds: Mapping[str, float | int] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    streets: tuple[str, ...] = field(default_factory=lambda: read_list(DEFAULT_STREETS_PATH))
    hoax_markers: tuple[str, ...] = field(
        default_factory=lambda: read_list(DEFAULT_HOAX_MARKERS_PATH)
    )
    # None: derived from device_id (derive_incident_prefix).
    incident_prefix: str | None = None

    def __post_init__(self) -> None:
        if self.incident_prefix is not None and not _PREFIX_RE.fullmatch(self.incident_prefix):
            raise ValueError("INCIDENT_PREFIX must be 1-4 characters of A-Z and 0-9")

    @property
    def prefix(self) -> str:
        """The incident number prefix this laptop uses."""
        return self.incident_prefix or derive_incident_prefix(self.device_id)

    def incident(self, number: int) -> str:
        return f"{self.prefix}-{number:04d}"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> PipelineConfig:
        """DEVICE_ID (or FIELD_DEVICE_ID), INCIDENT_PREFIX (upper-cased), FIELD_STREETS_PATH,
        FIELD_HOAX_MARKERS_PATH override the defaults."""
        env = os.environ if env is None else env
        prefix = (env.get("INCIDENT_PREFIX") or "").strip().upper() or None
        return cls(
            incident_prefix=prefix,
            device_id=env.get("DEVICE_ID") or env.get("FIELD_DEVICE_ID") or DEFAULT_DEVICE_ID,
            streets=read_list(env.get("FIELD_STREETS_PATH", DEFAULT_STREETS_PATH)),
            hoax_markers=read_list(env.get("FIELD_HOAX_MARKERS_PATH", DEFAULT_HOAX_MARKERS_PATH)),
        )


def derive_incident_prefix(device_id: str) -> str:
    """The default incident prefix for a laptop, from its DEVICE_ID (stable, 1-4 of A-Z0-9).

    Split the upper-cased id on anything that is not A-Z or 0-9. If the last part is all digits,
    the prefix is the first letter of the part before it (if any) plus the last 3 digits
    (`laptop-dev-01` -> `D01`, `laptop-7` -> `L7`); otherwise it is the first 4 characters of
    the last part (`boat` -> `BOAT`). An id with no letters or digits gives `A`."""
    parts = [p for p in re.split(r"[^A-Z0-9]+", device_id.upper()) if p]
    if not parts:
        return "A"
    last = parts[-1]
    if last.isdigit():
        head = parts[-2][0] if len(parts) > 1 else ""
        return head + last[-3:]
    return last[:4]


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


def _context(
    candidates: list[dict[str, Any]],
    raw_text: str,
    extracted: Mapping[str, Any],
    config: PipelineConfig,
) -> dict[str, Any]:
    return {
        "duplicate_candidates": candidates,
        "street_list_hits": logic.street_list_hits(extracted["location"]["text"], config.streets),
        # Markers are counted on the raw text here; only the count reaches the engine.
        "hoax_marker_hits": logic.hoax_marker_hits(raw_text, config.hoax_markers),
        "thresholds": dict(config.thresholds),
    }


def _record(
    request_id: str, received_at: str, contact: str | None, extracted: Mapping[str, Any]
) -> dict[str, Any]:
    """A request in the shape logic.duplicate_candidates expects (tier not known yet)."""
    return {
        "request_id": request_id,
        "received_at": received_at,
        "location_text": extracted["location"]["text"],
        "people_count": extracted["people_count"]["value"],
        "contact": contact,
        "priority_tier": None,
    }


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
    current = _record(request_id, received_at, contact, extracted)
    others = [p for p in prior if p["request_id"] != request_id]
    candidates = logic.duplicate_candidates(
        [*others, current], config.thresholds["duplicate_window_hours"]
    )[request_id]
    return _context(candidates, raw_text, extracted, config)


def _validate_event(event: dict[str, Any]) -> None:
    errors = list(validator_for("event.schema.json").iter_errors(event))
    if errors:
        raise ValueError(f"{event['type']} event failed schema validation: {errors[0].message}")


class EngineInputError(ValueError):
    """An item's engine input is not valid; `row` is its 1-based position in the batch."""

    def __init__(self, row: int, detail: str) -> None:
        super().__init__(f"row {row}: {detail}")
        self.row = row
        self.detail = detail


def _non_finite(value: Any, path: str = "") -> str | None:
    """Path of the first NaN/Infinity float anywhere in `value` (jsonschema accepts them)."""
    if isinstance(value, float) and not math.isfinite(value):
        return path or "(root)"
    if isinstance(value, Mapping):
        for k, v in value.items():
            if (found := _non_finite(v, f"{path}/{k}")) is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            if (found := _non_finite(v, f"{path}/{i}")) is not None:
                return found
    return None


def _check_engine_input(row: int, engine_input: dict[str, Any]) -> None:
    bad = _non_finite(engine_input)
    if bad is not None:
        raise EngineInputError(row, f"engine input has a non-finite number at {bad}")
    errors = list(validator_for("engine_input.schema.json").iter_errors(engine_input))
    if errors:
        where = "/".join(str(p) for p in errors[0].absolute_path)
        raise EngineInputError(row, f"{errors[0].message}" + (f" (at {where})" if where else ""))


@dataclass(frozen=True)
class IntakeItem:
    """One raw request waiting to be processed."""

    raw: str
    channel: str
    contact: str | None = None
    lang_hint: str | None = None
    sample: bool | None = None  # True for grader-mode samples (#301, #308)


def _extract(extractor: Extractor, raw: str) -> Extraction:
    """The extractor's reading of `raw`; if it raises, the keyword extractor's, stamped with its
    own model_version, so a model error never loses the request (review M-05). The keyword
    extractor itself is not wrapped: its errors propagate."""
    try:
        return extractor.extract(raw)
    except Exception:
        if isinstance(extractor, (FixtureExtractor, KeywordExtractor)):
            raise
        log.exception("extractor failed; using the keyword extractor for this request")
        return KeywordExtractor().extract(raw)


class FairLock:
    """A first-come, first-served mutex (a ticket lock). With a plain `threading.Lock` the bulk
    upload that releases it between rows usually takes it straight back, so a single request
    waiting for the model would still sit behind the whole upload."""

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._next = 0  # next ticket to hand out
        self._serving = 0  # ticket that holds the lock

    def __enter__(self) -> None:
        with self._cond:
            ticket = self._next
            self._next += 1
            self._cond.wait_for(lambda: self._serving == ticket)

    def __exit__(self, *exc: object) -> None:
        with self._cond:
            self._serving += 1
            self._cond.notify_all()

    def locked(self) -> bool:
        with self._cond:
            return self._serving != self._next


def extract_items(
    items: Sequence[IntakeItem],
    extractor: Extractor | None = None,
    *,
    lock: AbstractContextManager[Any] | None = None,
) -> list[Extraction]:
    """The extractor's reading of each item, in order (the model step of `process_batch`).

    It needs no store access, so the API runs it before taking the intake lock (review O-02:
    a bulk paste ran the model on every row while holding it). `lock`, held for one row at a
    time, keeps the model to one caller at a time without making a single request wait for a
    whole bulk upload to finish."""
    extractor = extractor or FixtureExtractor()
    lock = lock if lock is not None else nullcontext()
    out = []
    for item in items:
        with lock:
            out.append(_extract(extractor, item.raw))
    return out


def _relevant_prior(
    prior: list[dict[str, Any]], records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The earlier requests that can be a duplicate candidate of any record: a candidate needs
    the same non-empty normalised location, so the rest cannot change the result and are left
    out (`logic.duplicate_candidates` compares every pair; review M-04)."""
    locs = {logic.normalize_location(r["location_text"]) for r in records} - {""}
    return [p for p in prior if logic.normalize_location(p["location_text"]) in locs]


def _ordered_ids(n: int) -> list[str]:
    """n ULIDs that sort in the given order (plain ULIDs made in the same ms do not)."""
    ms = int(time.time() * 1000)
    base = secrets.randbits(79)
    return [new_ulid(timestamp_ms=ms, randomness=base + i) for i in range(n)]


def process_batch(
    items: Sequence[IntakeItem],
    *,
    store: EventStore,
    extractor: Extractor | None = None,
    decide_batch: BatchDecider | None = None,
    config: PipelineConfig | None = None,
    now: str | None = None,
    extractions: Sequence[Extraction] | None = None,
) -> list[list[dict[str, Any]]]:
    """Extract, decide and store many requests, in order. Returns [received, extracted,
    decided] per item. `extractions` (from `extract_items`, one per item) skips the model step.

    Earlier items are duplicate candidates for later ones, exactly as if each had been
    submitted on its own. The engine is called in batch mode: one call per "wave", where a
    wave holds every item whose in-batch candidates are already decided (so a candidate's
    priority_tier is always known). Without in-batch duplicates that is a single call.
    Nothing is stored unless every event is valid and every engine call succeeded; all
    events go to the store in one transaction.
    """
    if not items:
        return []
    config = config or PipelineConfig()
    extractor = extractor or FixtureExtractor()
    decide_batch = decide_batch or engine_client.decide_batch
    if extractions is None:
        extractions = extract_items(items, extractor)
    elif len(extractions) != len(items):
        raise ValueError(f"{len(extractions)} extractions for {len(items)} items")
    # Stamped after extraction, under the caller's intake lock: arrival order and store order
    # must agree, or two concurrent intakes would miss each other as duplicates.
    received_at = now or _now()
    ids = _ordered_ids(len(items))

    records = [
        _record(rid, received_at, item.contact, ex.payload)
        for rid, item, ex in zip(ids, items, extractions, strict=True)
    ]
    # ids are fresh, so none of these is in the batch
    prior = _relevant_prior(_known_requests(store), records)
    window = config.thresholds["duplicate_window_hours"]
    all_candidates = logic.duplicate_candidates([*prior, *records], window)

    position = {rid: i for i, rid in enumerate(ids)}
    waves: list[list[int]] = []
    wave_of: list[int] = []
    for i, rid in enumerate(ids):
        cands = all_candidates[rid]
        deps = [position[c["request_id"]] for c in cands if c["request_id"] in position]
        w = 1 + max((wave_of[d] for d in deps), default=-1)
        wave_of.append(w)
        if w == len(waves):
            waves.append([])
        waves[w].append(i)

    # Check every row before the engine runs, so a bad row is named and nothing is stored.
    # In-batch candidates still have no tier here; the engine waves fill them in below.
    for n, (rid, item, ex) in enumerate(zip(ids, items, extractions, strict=True), 1):
        _check_engine_input(
            n,
            {
                "extracted": ex.payload,
                "context": _context(all_candidates[rid], item.raw, ex.payload, config),
            },
        )

    tiers: dict[str, str | None] = {}
    engine_inputs: list[dict[str, Any] | None] = [None] * len(items)
    engine_outputs: list[dict[str, Any] | None] = [None] * len(items)
    for wave in waves:
        batch = []
        for i in wave:
            candidates = [
                {**c, "priority_tier": tiers[c["request_id"]]} if c["request_id"] in tiers else c
                for c in all_candidates[ids[i]]
            ]
            context = _context(candidates, items[i].raw, extractions[i].payload, config)
            engine_inputs[i] = {"extracted": extractions[i].payload, "context": context}
            batch.append(engine_inputs[i])
        outputs = decide_batch(batch)
        if len(outputs) != len(batch):
            raise engine_client.EngineError(
                f"engine batch returned {len(outputs)} results for {len(batch)} inputs"
            )
        for i, output in zip(wave, outputs, strict=True):
            engine_outputs[i] = output
            tiers[ids[i]] = output["priority_tier"]

    # Numbers are reserved only once every engine call succeeded, under the caller's intake
    # lock, so they increase in arrival order; they are never reused (a failed store leaves a gap).
    first_number = store.reserve(INCIDENT_COUNTER, len(items))
    results = []
    for i, item in enumerate(items):
        engine_output = engine_outputs[i]
        assert engine_output is not None
        received: dict[str, Any] = {
            "channel": item.channel,
            "raw_text": item.raw,
            "lang_hint": item.lang_hint,
            "contact": item.contact,
            "incident": config.incident(first_number + i),
        }
        if item.sample is not None:
            received["sample"] = item.sample
        payloads = [
            ("received", received),
            ("extracted", extractions[i].payload),
            ("decided", {"engine_input": engine_inputs[i], "engine_output": engine_output}),
        ]
        events = [
            {
                "event_id": new_ulid(),
                "request_id": ids[i],
                "type": kind,
                "device_id": config.device_id,
                "occurred_at": received_at,
                "rules_version": engine_output["rules_version"],
                "model_version": extractions[i].model_version,
                "payload": payload,
            }
            for kind, payload in payloads
        ]
        for ev in events:
            _validate_event(ev)
        results.append(events)
    store.append_events([ev for events in results for ev in events])
    return results


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
    one = decide or engine_client.decide
    [events] = process_batch(
        [IntakeItem(raw, channel, contact, lang_hint)],
        store=store,
        extractor=extractor,
        decide_batch=lambda inputs: [one(x) for x in inputs],
        config=config,
        now=now,
    )
    return events
