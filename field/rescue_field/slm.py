"""Extractor interface and the fixture stub used until the real SLM exists (decision 1).

An extractor turns raw text into an `extracted` payload (fields only, never a decision).
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from functools import cache
from typing import Any, Protocol

from rescue_shared.contracts_io import CONTRACTS, validator_for

STUB_MODEL_VERSION = "stub:fixture-extractor"
KEYWORD_MODEL_VERSION = "stub:keyword-extractor"
LOW = 0.3  # nothing found
MID = 0.6  # weak hint
HIGH = 0.8  # explicit phrase

# Each table maps kind -> (explicit phrases, weak hints). Explicit hit -> HIGH, weak hit -> MID.
_HAZARDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "rising_water": (
        ("water rising", "water is rising", "water's rising", "rising water", "sube el agua"),
        ("flood", "water", "inundad", "agua"),
    ),
    "on_roof": (("on the roof", "on our roof", "en el techo", "en la azotea"), ("roof", "techo")),
    "injury": (
        ("injured", "bleeding", "broken leg", "herido", "herida"),
        ("injur", "broke", "herid"),
    ),
    "fire": (("on fire", "house fire", "incendio"), ("fire", "fuego")),
    "trapped": (("trapped", "atrapad"), ("stuck", "can't get out", "cannot get out")),
}
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
_REQUEST_WORDS = (
    "help",
    "need",
    "rescue",
    "stuck",
    "trapped",
    "ayuda",
    "necesit",
    "socorro",
    "auxilio",
)
_STALE_RE = re.compile(
    r"last year|old post|\b(?:from|in)\s+(?:19|20)\d\d\b"
    r"|hace (?:un )?año|año pasado|publicación antigua",
    re.I,
)
_STREET_SUFFIX = (
    r"(?:st|street|ave|avenue|rd|road|dr|drive|blvd|boulevard|ln|lane|way|ct|court|pkwy|parkway|"
    r"calle|avenida|av|camino)"
)
_ADDRESS_RE = re.compile(
    rf"\b\d{{1,5}}(?:\s+[A-Za-z][\w'.-]*){{1,4}}?\s+{_STREET_SUFFIX}\b\.?", re.I
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
    lines = (CONTRACTS / "fixtures" / "streets.txt").read_text(encoding="utf-8").splitlines()
    names = {ln.strip() for ln in lines if ln.strip()}
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
        low = raw_text.lower()

        def kinds(table: dict[str, tuple[str, ...]]) -> list[str]:
            return [k for k, words in table.items() if any(w in low for w in words)]

        is_req = any(w in low for w in _REQUEST_WORDS)
        stale = _STALE_RE.search(raw_text)
        # The contract has no contact field, so a phone number is only recorded as a span.
        spans = []
        if phone := _PHONE_RE.search(raw_text):
            spans.append({"start": phone.start(), "end": phone.end(), "label": "CONTACT"})
        return {
            "location": _location(raw_text),
            "people_count": _people(low),
            "vulnerable": _graded(_VULNERABLES, low),
            "hazards": _graded(_HAZARDS, low),
            "time_ref": {
                "text": stale.group(0) if stale else None,
                "is_stale_post": bool(stale),
                "confidence": 0.7 if stale else LOW,
            },
            "is_request": {"value": is_req, "confidence": HIGH if is_req else 0.4},
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
