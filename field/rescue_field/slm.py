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
LOW = 0.3  # confidence for keyword guesses: below every engine threshold that matters

_HAZARD_KEYWORDS = {
    "rising_water": ("flood", "water rising", "water is rising", "inundad", "sube el agua"),
    "on_roof": ("roof", "techo"),
    "injury": ("injur", "bleeding", "broke", "herid"),
    "fire": ("fire", "fuego", "incendio"),
    "trapped": ("trapped", "stuck", "atrapad"),
}
_VULNERABLE_KEYWORDS = {
    "child": ("kid", "child", "baby", "niñ", "bebé"),
    "elderly": ("grandpa", "grandma", "elderly", "abuel"),
    "pregnant": ("pregnant", "embarazada"),
    "disabled": ("wheelchair", "disabled", "silla de ruedas"),
}
_NEED_KEYWORDS = {
    "water": ("water", "agua"),
    "food": ("food", "comida", "diaper"),
    "meds": ("medic", "insulin", "meds"),
}
_REQUEST_WORDS = ("help", "need", "rescue", "stuck", "trapped", "ayuda", "necesit", "socorro")


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


class FixtureExtractor:
    """Known fixture texts get their fixture payload; anything else a keyword guess."""

    model_version = STUB_MODEL_VERSION

    def extract(self, raw_text: str) -> Extraction:
        known = {_norm(k): v for k, v in _fixtures().items()}
        hit = known.get(_norm(raw_text))
        payload = copy.deepcopy(hit) if hit is not None else self._keyword_payload(raw_text)
        validate_extracted(payload)
        return Extraction(payload, self.model_version)

    @staticmethod
    def _keyword_payload(raw_text: str) -> dict[str, Any]:
        low = raw_text.lower()

        def kinds(table: dict[str, tuple[str, ...]]) -> list[str]:
            return [k for k, words in table.items() if any(w in low for w in words)]

        digits = re.search(r"\b(\d{1,3})\s+(?:people|persons|personas|kids|of us)\b", low)
        is_req = any(w in low for w in _REQUEST_WORDS)
        return {
            "location": {"text": None, "type": "none", "confidence": LOW},
            "people_count": {
                "value": int(digits.group(1)) if digits else None,
                "confidence": LOW,
            },
            "vulnerable": [{"kind": k, "confidence": LOW} for k in kinds(_VULNERABLE_KEYWORDS)],
            "hazards": [{"kind": k, "confidence": LOW} for k in kinds(_HAZARD_KEYWORDS)],
            "time_ref": {"text": None, "is_stale_post": False, "confidence": LOW},
            "is_request": {"value": is_req, "confidence": LOW},
            "spans": [],
            "needs": [{"kind": k, "hours_without": None} for k in kinds(_NEED_KEYWORDS)],
            "request_kind": "rescue" if is_req else "other",
        }
