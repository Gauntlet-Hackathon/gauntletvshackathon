"""Regex / keyword baseline: the honest "why not plain code?" column.

Extraction is hand-written regexes and keyword lists (no model, no network, deterministic);
the decision is the real fallback engine. Registered with the runner as ``regex-baseline``.

    python -m rescue_evals.runner --system regex-baseline --sets A,B
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from rescue_evals.runner import register_system
from rescue_shared.models import Extracted

CONF = 0.9  # regexes are either sure or silent
WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "a": 1, "an": 1,
}  # fmt: skip
_NUM = r"(\d+|" + "|".join(WORDS) + r")"
_PEOPLE = r"(?:people|persons?|of us|adults?|kids?|children|child|family members|residents)"
COUNT_RE = re.compile(rf"\b{_NUM}\s+(?:\w+\s+)?{_PEOPLE}\b", re.I)
WE_ARE_RE = re.compile(rf"\bwe(?:'re| are)\s+{_NUM}\b", re.I)

COORDS_RE = re.compile(r"-?\d{1,3}\.\d{2,}\s*,\s*-?\d{1,3}\.\d{2,}")
_SUFFIX = r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court)\b\.?"
ADDRESS_RE = re.compile(rf"\b\d+\s+(?:[A-Z][\w'-]*\s+){{1,3}}{_SUFFIX}")
LANDMARK_RE = re.compile(
    r"\b(?:at|near|behind|outside|opposite|by)\s+(?:the\s+)?"
    r"((?:[A-Z][\w'-]*\s*){1,4}(?:School|Church|Bridge|Park|Hospital|Mall|Station|Market|Center|"
    r"Centre|Library|Mosque|Temple|Stadium|Apartments|Motel|Hotel))"
)

HAZARDS = {
    "rising_water": r"rising|flood|water (?:is )?(?:coming|up|in)|waist[- ]deep|knee[- ]deep|"
    r"neck[- ]deep|submerged",
    "on_roof": r"\bon (?:the |our |my )?roof|\broof\b|attic",
    "injury": r"injur|bleed|broken (?:leg|arm|bone)|wound|hurt",
    "medical_device_no_power": r"(?:oxygen|ventilator|dialysis|concentrator|cpap|medical device)"
    r".{0,40}(?:no power|power (?:is )?out|battery|died|dying)|no power.{0,40}"
    r"(?:oxygen|ventilator|dialysis|concentrator)",
    "fire": r"\bfire\b|smoke|burning",
    "trapped": r"trapped|stuck|can'?t (?:get out|leave|open)|blocked in",
}
VULNERABLE = {
    "child": r"\bchild(?:ren)?\b|\bkids?\b|\bbab(?:y|ies)\b|\binfant|\btoddler|\bnewborn",
    "elderly": r"elderly|\bgrand(?:ma|pa|mother|father|parents?)\b|\bseniors?\b|\b[789]\d[- ]year",
    "disabled": r"disabled|disability|wheelchair|bedridden|paraly|\bblind\b",
    "medical": r"dialysis|insulin|diabet|oxygen|ventilator|chemo|heart (?:condition|problem)",
    "pregnant": r"pregnan|in labou?r|expecting",
}
NEEDS = {
    "food": r"food|hungry|eat",
    "water": r"drinking water|no water|thirst",
    "meds": r"meds|medic",
}
HOURS_RE = re.compile(r"(?:for|over|about)\s+(\d+)\s*(hours?|hrs?|days?)", re.I)

REQUEST_RE = re.compile(
    r"help|rescue|need|trapped|stuck|please|send|evacuat|boat|urgent|sos|emergency|stranded", re.I
)
NOT_REQUEST_RE = re.compile(
    r"thank|safe now|we(?:'re| are) (?:ok|okay|safe|fine)|rescued|no longer need|"
    r"donat|volunteer(?:s|ing)? (?:needed|wanted)|road (?:closed|open)|news|update:",
    re.I,
)
STALE_RE = re.compile(r"\b(?:yesterday|last (?:night|week)|days? ago|earlier this week)\b", re.I)
TIME_RE = re.compile(
    r"\b(?:yesterday|last (?:night|week)|\d+\s*(?:hours?|hrs?|days?) ago|since \w+|tonight|now)\b",
    re.I,
)


def _num(tok: str) -> int:
    return int(tok) if tok.isdigit() else WORDS[tok.lower()]


def _location(text: str) -> dict[str, Any]:
    for rx, kind in ((COORDS_RE, "coords"), (ADDRESS_RE, "address")):
        m = rx.search(text)
        if m:
            return {"text": m.group(0).strip(), "type": kind, "confidence": CONF}
    m = LANDMARK_RE.search(text)
    if m:
        return {"text": m.group(1).strip(), "type": "landmark", "confidence": CONF}
    return {"text": None, "type": "none", "confidence": CONF}


def _people(text: str) -> dict[str, Any]:
    counts = {_num(m.group(1)) for m in COUNT_RE.finditer(text)}
    m = WE_ARE_RE.search(text)
    if m:
        counts.add(_num(m.group(1)))
    # one clear number -> that number; none or several (could be conflicting) -> unknown
    if len(counts) == 1:
        return {"value": counts.pop(), "confidence": CONF}
    return {"value": None, "confidence": 0.0 if not counts else 0.3}


def _kinds(table: dict[str, str], text: str) -> list[dict[str, Any]]:
    return [
        {"kind": k, "confidence": CONF} for k, rx in table.items() if re.search(rx, text, re.I)
    ]


def _needs(text: str) -> list[dict[str, Any]]:
    hours = HOURS_RE.search(text)
    h: float | None = None
    if hours:
        h = float(hours.group(1)) * (24 if hours.group(2).lower().startswith("d") else 1)
    return [
        {"kind": k, "hours_without": h} for k, rx in NEEDS.items() if re.search(rx, text, re.I)
    ]


def _request_kind(text: str, hazards: list[dict[str, Any]]) -> str:
    low = text.lower()
    if hazards:
        return "rescue"
    if re.search(r"evacuat|shelter|ride out", low):
        return "evacuation"
    if re.search(r"check on|welfare|haven'?t (?:heard|seen)|no word from", low):
        return "welfare_check"
    if re.search(r"food|water|meds|supplies|diapers|blankets", low):
        return "supply"
    return "rescue" if re.search(r"rescue|boat|trapped", low) else "other"


def extract(raw_text: str) -> Extracted:
    text = raw_text or ""
    hazards = _kinds(HAZARDS, text)
    is_req = bool(REQUEST_RE.search(text)) and not NOT_REQUEST_RE.search(text)
    t = TIME_RE.search(text)
    return Extracted.model_validate(
        {
            "location": _location(text),
            "people_count": _people(text),
            "vulnerable": _kinds(VULNERABLE, text),
            "hazards": hazards,
            "time_ref": {
                "text": t.group(0) if t else None,
                "is_stale_post": bool(STALE_RE.search(text)),
                "confidence": CONF,
            },
            "is_request": {"value": is_req, "confidence": CONF},
            "spans": [],
            "needs": _needs(text),
            "request_kind": _request_kind(text, hazards),
        }
    )


class RegexBaselineSystem:
    """Regex/keyword extraction + the real fallback engine for the decision."""

    name = "regex-baseline"
    model_version = "stub:regex-baseline"

    def extract(self, raw_text: str) -> Extracted:
        return extract(raw_text)

    def decide(self, engine_input: dict[str, Any]) -> dict[str, Any]:
        from rescue_engine.fallback.engine import decide_one

        return decide_one(engine_input)


def _factory(_cases: Sequence[Any]) -> RegexBaselineSystem:
    return RegexBaselineSystem()


register_system("regex-baseline", _factory)
