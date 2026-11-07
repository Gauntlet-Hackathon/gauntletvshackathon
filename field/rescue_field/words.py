"""Volunteer wording (UI-2): plain sentences for the queue. Nothing here is a model translation.

No rule id, version or confidence ever reaches the volunteer HTML; rule ids are only keys here.
"""

from __future__ import annotations

from typing import Any

PILL = {"P1": "Needs help now", "P2": "Needs help soon", "P3": "Can wait"}
PILL_CHECK = "Check first"
PILL_SET_ASIDE = "Set aside"
ESCALATED_LABEL = "Couldn't get there"


def pill_text(tier: str | None, provisional: bool = False) -> str:
    """Pill label. A provisional tier reads "maybe Needs help now"; no tier is "Check first"."""
    if tier not in PILL:
        return PILL_CHECK
    return f"maybe {PILL[tier]}" if provisional else PILL[tier]


def other_posts(n: int) -> str | None:
    return f"+{n} other post{'s' if n != 1 else ''}" if n else None


# One sentence per rule id in contracts/engine_rules.md ("Why is this one first?").
RULE_SENTENCES: dict[str, str] = {
    "R-P1-01": "Water is rising and people are trapped or on a roof.",
    "R-P1-02": "Someone is hurt or in a medical emergency.",
    "R-P1-03": "Someone depends on a medical device that has lost power.",
    "R-P1-04": "Water is rising and a child, an older or disabled person is there.",
    "R-P1-05": "There is a fire.",
    "R-P2-01": "People are trapped or on a roof.",
    "R-P2-02": "A child, an older person or someone who needs extra care is with them.",
    "R-P2-03": "They have gone more than a day without food, water or medicine.",
    "R-P2-04": "They are in a dangerous spot or need to be rescued.",
    "R-P3-01": "They are asking to be evacuated, with no urgent danger reported.",
    "R-P3-02": "They are asking for someone to check on a person, with no urgent danger reported.",
    "R-P3-03": "They are asking for supplies, with no urgent danger reported.",
    "R-P3-04": "No urgent danger was reported.",
    "R-DUP-01": "Several posts describe the same place, so they are grouped under one.",
    "R-ABS-REQ": "We are not sure this is a request for help, so someone should ask first.",
    "R-ABS-LOC": "We could not find a place to send a crew, so someone should ask first.",
    "R-ABS-HAZ": "The danger is unclear and could be serious, so someone should ask first.",
    "R-ABS-HOAX": "This may not be a real request, so someone should ask first.",
    "R-ABS-CNT": "Posts about this place give different counts, so someone should ask first.",
    "R-NAR-01": "This does not look like a request for help.",
    "R-NAR-02": "This looks like an old post, not a new request.",
}
DEFAULT_SENTENCE = "It ranks above the others waiting."


def rule_sentence(rule_id: str | None) -> str:
    return RULE_SENTENCES.get(rule_id or "", DEFAULT_SENTENCE)


_KIND = {
    "rising_water": "Rising water",
    "on_roof": "On a roof",
    "trapped": "Trapped",
    "injury": "Someone hurt",
    "medical_device_no_power": "Medical device without power",
    "fire": "Fire",
    "child": "Child",
    "elderly": "Older person",
    "disabled": "Person with a disability",
    "medical": "Medical condition",
    "pregnant": "Pregnant",
    "food": "food",
    "water": "water",
    "meds": "medicine",
}
HAZARD_MIN = 0.5  # hazards below the engine's "uncertain" band are not stated as fact


def kind_word(kind: str) -> str:
    return _KIND.get(kind, kind.replace("_", " "))


def _sure(items: list[dict[str, Any]]) -> list[str]:
    return [kind_word(i["kind"]) for i in items if i.get("confidence", 1) >= HAZARD_MIN]


def headline(e: dict[str, Any]) -> str:
    """Composed from extracted hazards, vulnerable people and needs."""
    parts = _sure(e.get("hazards", [])) + _sure(e.get("vulnerable", []))
    needs = [kind_word(n["kind"]) for n in e.get("needs", [])]
    if needs:
        parts.append("Needs " + ", ".join(needs))
    return " · ".join(parts) if parts else "Request for help"


def details(e: dict[str, Any]) -> list[str]:
    out: list[str] = []
    n = (e.get("people_count") or {}).get("value")
    if n is None:
        out.append("Number of people unknown")
    else:
        out.append(f"{n} {'person' if n == 1 else 'people'}")
    for h in e.get("hazards", []):
        if 0.25 <= h.get("confidence", 1) < HAZARD_MIN:
            out.append(f"Possibly: {kind_word(h['kind']).lower()}")
    for v in e.get("vulnerable", []):
        if 0.25 <= v.get("confidence", 1) < HAZARD_MIN:
            out.append(f"Possibly: {kind_word(v['kind']).lower()} with them")
    for need in e.get("needs", []):
        hrs = need.get("hours_without")
        if hrs is not None:
            out.append(f"No {kind_word(need['kind'])} for {hrs} h")
    t = e.get("time_ref") or {}
    if t.get("text"):
        out.append(f"When: {t['text']}")
    return out
