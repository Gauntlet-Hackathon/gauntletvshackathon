"""Extractor interface and the fixture stub used until the real SLM exists (decision 1).

An extractor turns raw text into an `extracted` payload (fields only, never a decision).
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from functools import cache
from typing import Any, Protocol

from rescue_shared import frozen_config
from rescue_shared.contracts_io import CONTRACTS, validator_for

STUB_MODEL_VERSION = "stub:fixture-extractor"
KEYWORD_MODEL_VERSION = "stub:keyword-extractor"
LOW = 0.3  # nothing found
MID = 0.6  # weak hint
HIGH = 0.8  # explicit phrase
CLEAR_REQUEST = 0.9  # explicit request phrase: above hoax_request_confidence_max (0.85)

# Acute medical emergencies are extracted as injury (DECISIONS R6, contracts/engine_rules.md).
_ACUTE_MEDICAL = (
    "unconscious", "not breathing", "can't breathe", "cannot breathe", "chest pain",
    "heart attack", "severe bleeding", "drowning", "seizure", "inconsciente", "no respira",
    "ahogando", "se está ahogando",
)  # fmt: skip
# Each table maps kind -> (explicit phrases, weak hints). Explicit hit -> HIGH, weak hit -> MID.
_HAZARDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "rising_water": (
        ("water rising", "water is rising", "water's rising", "rising water", "sube el agua"),
        ("flood", "water", "inundad", "agua"),
    ),
    "on_roof": (("on the roof", "on our roof", "en el techo", "en la azotea"), ("roof", "techo")),
    "injury": (
        ("injured", "bleeding", "broken leg", "herido", "herida", *_ACUTE_MEDICAL),
        ("injur", "broke", "herid"),
    ),
    "fire": (("on fire", "house fire", "incendio"), ("fire", "fuego")),
    "trapped": (("trapped", "atrapad"), ("stuck", "can't get out", "cannot get out")),
}
# medical_device_no_power needs a device word plus a power-loss phrase (explicit) or the
# device word with "power"/"luz" anywhere (weak); see _medical_device().
_DEVICE_WORDS = (
    "oxygen", "concentrator", "ventilator", "cpap", "dialysis", "oxígeno", "diálisis",
)  # fmt: skip
_POWER_LOSS = (
    "no power", "power is out", "power out", "lost power", "no electricity", "battery dying",
    "se fue la luz", "sin luz", "sin electricidad",
)  # fmt: skip
_POWER_HINTS = ("power", "luz")
_VULNERABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "child": (("baby", "infant", "bebé", "bebe", "toddler"), ("kid", "child", "niñ")),
    "elderly": (
        ("elderly", "grandma", "grandpa", "abuela", "abuelo", "anciano"),
        ("old man", "old woman", "abuel"),
    ),
    "pregnant": (("pregnant", "embarazada"), ()),
    "disabled": (("wheelchair", "silla de ruedas", "disabled", "discapacitad"), ("bedridden",)),
    "medical": (("oxygen", "oxígeno", "dialysis", "diálisis"), ("insulin", "medical")),
}
_NEED_KEYWORDS = {
    "water": ("water", "agua"),
    "food": ("food", "comida", "diaper"),
    "meds": ("medic", "insulin", "meds"),
}
# Explicit request phrases -> CLEAR_REQUEST; weak request words (or a firm hazard) -> HIGH.
_REQUEST_EXPLICIT = (
    "help",
    "rescue",
    "rescate",
    "ayuda",
    "socorro",
    "auxilio",
    "send someone",
    "come get",
    "save us",
    "emergency",
)
_REQUEST_WEAK = (
    "need",
    "stuck",
    "trapped",
    "necesit",
    "please",
    "por favor",
)
# Short tokens only count as whole words ("sos" is not "sospecha", "911" not "19112").
_REQUEST_EXPLICIT_RE = re.compile(r"\b(?:hlp|sos|911)\b", re.I)
_REQUEST_WEAK_RE = re.compile(r"\b(?:plz|pls)\b", re.I)
_STREET_SUFFIX = (
    r"(?:st|street|ave|avenue|rd|road|dr|drive|blvd|boulevard|ln|lane|way|ct|court|pkwy|parkway|"
    r"calle|avenida|av|camino)"
)
# A year or "last year" is a stale marker only when it describes the post itself ("photo from
# 2017", "this is from last year"), never the events or a house number ("in 2019 we flooded too",
# "from 2008 Ike to now", "in 2014 Elm Street", "last year we evacuated").
_POST_WORD = r"(?:posted|post|photo|picture|pic|video|tweet|footage|image)"
_STALE_RE = re.compile(
    rf"old post|\b{_POST_WORD}\s+(?:from|in|of)\s+(?:19|20)\d\d\b"
    r"|\bfrom last year\b"
    rf"|\b{_POST_WORD}\s+(?:from\s+|of\s+)?last year\b"
    r"|publicaci[oó]n antigua|\bdel año pasado\b|\bde hace (?:un )?año\b",
    re.I,
)
# Street-name words between the number and the suffix are never counts or prepositions,
# so "3 people at Main St" is not an address.
_NOT_STREET_WORD = (
    r"(?:people|persons|personas|ppl|adults|adultos|kids|children|niños|of|us|at|in|on)\b"
)
_ADDRESS_RE = re.compile(
    rf"\b\d{{1,5}}(?:\s+(?!{_NOT_STREET_WORD})[A-Za-z][\w'.-]*){{1,4}}?\s+{_STREET_SUFFIX}\b\.?",
    re.I,
)
_COORDS_RE = re.compile(r"-?\d{1,3}\.\d{3,},\s*-?\d{1,3}\.\d{3,}")
_PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10,
}  # fmt: skip
_COUNT_NOUN = r"(?:people|persons|personas|adults|adultos|kids|children|niños|of us)"
_COUNT_RE = re.compile(rf"\b(\d{{1,3}}|{'|'.join(_NUMBER_WORDS)})\s+{_COUNT_NOUN}\b", re.I)
_WE_ARE_RE = re.compile(
    rf"\b(?:we are|there are|there's|somos|hay)\s+(\d{{1,3}}|{'|'.join(_NUMBER_WORDS)})\b", re.I
)


@dataclass(frozen=True)
class Extraction:
    payload: dict[str, Any]
    model_version: str


class Extractor(Protocol):
    def extract(self, raw_text: str) -> Extraction:
        """Return an extracted payload (schema-valid) and the model version that made it."""
        ...


def validate_extracted(payload: dict[str, Any]) -> None:
    errors = list(validator_for("payloads/extracted.schema.json").iter_errors(payload))
    if errors:
        raise ValueError(f"extracted payload failed schema validation: {errors[0].message}")


@cache
def _fixtures() -> dict[str, dict[str, Any]]:
    path = CONTRACTS / "fixtures" / "cases.jsonl"
    cases = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return {c["raw_text"]: c["extracted"] for c in cases}


def _norm(text: str) -> str:
    return " ".join(text.split())


@cache
def _streets() -> tuple[str, ...]:
    # Same file and parsing as PipelineConfig.streets, which the frozen config_hash covers (R15).
    path = os.environ.get("FIELD_STREETS_PATH") or (CONTRACTS / "fixtures" / "streets.txt")
    names = set(frozen_config.read_list(path))
    return tuple(
        sorted(names, key=len, reverse=True)
    )  # longest first: "Katy Freeway" before "I-10"


def _location(text: str) -> dict[str, Any]:
    if m := _COORDS_RE.search(text):
        return {"text": m.group(0), "type": "coords", "confidence": HIGH}
    if m := _ADDRESS_RE.search(text):
        return {"text": m.group(0).rstrip("."), "type": "address", "confidence": HIGH}
    low = text.lower()
    for name in _streets():
        at = low.find(name.lower())
        if at >= 0:
            return {"text": text[at : at + len(name)], "type": "landmark", "confidence": HIGH}
    return {"text": None, "type": "none", "confidence": LOW}


def _graded(table: dict[str, tuple[tuple[str, ...], tuple[str, ...]]], low: str) -> list[dict]:
    out = []
    for kind, (explicit, weak) in table.items():
        if any(w in low for w in explicit):
            out.append({"kind": kind, "confidence": HIGH})
        elif any(w in low for w in weak):
            out.append({"kind": kind, "confidence": MID})
    return out


def _medical_device(low: str) -> list[dict]:
    if not any(w in low for w in _DEVICE_WORDS):
        return []
    if any(w in low for w in _POWER_LOSS):
        return [{"kind": "medical_device_no_power", "confidence": HIGH}]
    if any(w in low for w in _POWER_HINTS):
        return [{"kind": "medical_device_no_power", "confidence": MID}]
    return []


def _request_confidence(
    raw_text: str, low: str, hazards: list[dict], hoax_markers: tuple[str, ...] = ()
) -> float | None:
    """CLEAR_REQUEST for an explicit request phrase, HIGH for a weak one, None for none.

    A hoax marker outside a negation ("fake lol", but not "not fake") caps an explicit request at
    HIGH, below hoax_request_confidence_max, so the engine's hoax check (R3) still applies."""
    if any(w in low for w in _REQUEST_EXPLICIT) or _REQUEST_EXPLICIT_RE.search(raw_text):
        return HIGH if _hoax_hint(low, hoax_markers) else CLEAR_REQUEST
    weak = any(w in low for w in _REQUEST_WEAK) or _REQUEST_WEAK_RE.search(raw_text)
    # A firm (explicit-phrase) hazard is itself a cry for help.
    if weak or any(h["confidence"] == HIGH for h in hazards):
        return HIGH
    return None


_NEGATION = r"(?:not|no|isn't|nothing|never|no es|nada de|ni)"


def _hoax_hint(low: str, markers: tuple[str, ...]) -> bool:
    """True when a hoax marker appears outside a negation ("not fake", "not trolling", "this is
    not a test post", "nothing fake about this", "no es broma")."""
    for m in markers:
        word = re.escape(m.lower())
        rest = re.sub(rf"\b{_NEGATION}\s+(?:a\s+|an\s+)?{word}\b", " ", low)
        if re.search(rf"\b{word}\b", rest):
            return True
    return False


@cache
def _hoax_markers() -> tuple[str, ...]:
    # Same file and parsing as PipelineConfig.hoax_markers (frozen config_hash, R15).
    default = CONTRACTS / "fixtures" / "hoax_markers.txt"
    path = os.environ.get("FIELD_HOAX_MARKERS_PATH") or default
    return frozen_config.read_list(path)


def _people(low: str) -> dict[str, Any]:
    m = _COUNT_RE.search(low) or _WE_ARE_RE.search(low)
    if not m:
        return {"value": None, "confidence": LOW}
    tok = m.group(1)
    return {"value": int(tok) if tok.isdigit() else _NUMBER_WORDS[tok], "confidence": HIGH}


class KeywordExtractor:
    """No-model extractor: regexes and keyword tables with graded confidences."""

    model_version = KEYWORD_MODEL_VERSION

    def extract(self, raw_text: str) -> Extraction:
        payload = self.payload(raw_text)
        validate_extracted(payload)
        return Extraction(payload, self.model_version)

    @staticmethod
    def payload(raw_text: str) -> dict[str, Any]:
        # Phone keyboards send a curly apostrophe ("can’t breathe").
        low = raw_text.lower().replace("\u2019", "'")

        def kinds(table: dict[str, tuple[str, ...]]) -> list[str]:
            return [k for k, words in table.items() if any(w in low for w in words)]

        hazards = _graded(_HAZARDS, low) + _medical_device(low)
        req_conf = _request_confidence(raw_text, low, hazards, _hoax_markers())
        is_req = req_conf is not None
        stale = _STALE_RE.search(raw_text)
        # The contract has no contact field, so a phone number is only recorded as a span.
        spans = []
        if phone := _PHONE_RE.search(raw_text):
            spans.append({"start": phone.start(), "end": phone.end(), "label": "CONTACT"})
        return {
            "location": _location(raw_text),
            "people_count": _people(low),
            "vulnerable": _graded(_VULNERABLES, low),
            "hazards": hazards,
            "time_ref": {
                "text": stale.group(0) if stale else None,
                "is_stale_post": bool(stale),
                "confidence": 0.7 if stale else LOW,
            },
            "is_request": {"value": is_req, "confidence": req_conf if is_req else 0.4},
            "spans": spans,
            "needs": [{"kind": k, "hours_without": None} for k in kinds(_NEED_KEYWORDS)],
            "request_kind": "rescue" if is_req else "other",
        }


class FixtureExtractor:
    """Known fixture texts get their fixture payload; anything else goes to KeywordExtractor."""

    model_version = KEYWORD_MODEL_VERSION  # what handles live text (heartbeat)

    def __init__(self) -> None:
        self._keyword = KeywordExtractor()

    def extract(self, raw_text: str) -> Extraction:
        known = {_norm(k): v for k, v in _fixtures().items()}
        hit = known.get(_norm(raw_text))
        if hit is None:
            return self._keyword.extract(raw_text)
        payload = copy.deepcopy(hit)
        validate_extracted(payload)
        return Extraction(payload, STUB_MODEL_VERSION)
