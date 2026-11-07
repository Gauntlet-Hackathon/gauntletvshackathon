"""Volunteer wording (UI-2): pills, one plain sentence per rule, headlines, details, contact.

The volunteer app never shows a rule ID, a rules or model version or a confidence number. Every
word here is composed from the extracted fields and the engine's decision; nothing is a model
translation. The follow-up questions are the engine's own strings, shown elsewhere word for word.
`field/tests/test_volunteer.py` parses `contracts/engine_rules.md`, so a new rule ID without a
sentence fails the tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from rescue_field.shell import _ago
from rescue_shared import logic

TIER_WORDS = {"P1": "Needs help now", "P2": "Needs help soon", "P3": "Can wait"}
CHECK_FIRST = "Check first"
SET_ASIDE = "Set aside"
COULDNT_GET_THERE = "Couldn't get there"

# "Why is this one first?" (or "Why check first?"): one plain sentence per engine rule ID.
RULE_SENTENCES: dict[str, str] = {
    "R-P1-01": "Water is rising and people are trapped or on a roof.",
    "R-P1-02": "Someone is hurt or has a medical emergency.",
    "R-P1-03": "Someone relies on a medical device and has no power.",
    "R-P1-04": "Water is rising and a child, an older person or a disabled person is there.",
    "R-P1-05": "There is a fire.",
    "R-P2-01": "People are trapped or on a roof.",
    "R-P2-02": "Someone who needs extra care is there (a child, an older, disabled, pregnant "
    "or sick person).",
    "R-P2-03": "They have been without food, water or medicine for more than a day.",
    "R-P2-04": "There is flood water, or people are stranded and asking to be rescued.",
    "R-P3-01": "They are asking to be moved somewhere safer, with no danger mentioned.",
    "R-P3-02": "Someone is asking us to check on a person, with no danger mentioned.",
    "R-P3-03": "They are asking for supplies, with no danger mentioned.",
    "R-P3-04": "They asked for help, but no danger or special need was mentioned.",
    "R-DUP-01": "Several posts describe the same people; this card counts the most urgent of them.",
    "R-ABS-REQ": "We are not sure this message is asking for help.",
    "R-ABS-LOC": "We don't know where they are yet.",
    "R-ABS-HAZ": "It might be serious, but the message is not clear about the danger.",
    "R-ABS-HOAX": "Parts of this message look like it might not be real.",
    "R-ABS-CNT": "Messages from the same person give different numbers of people.",
    "R-NAR-01": "This message is not asking for help.",
    "R-NAR-02": "This looks like an old post being shared again.",
}

CHANNEL_WORDS = {
    "sms": "Text message",
    "social": "Social media post",
    "form": "Web form",
    "call_note": "Phone call",
    "csv": "Imported list",
}

_HAZARDS = {
    "rising_water": "Water rising",
    "injury": "Someone hurt",
    "medical_device_no_power": "Medical device without power",
    "fire": "Fire",
}
_POSITION = {"on_roof": "on a roof", "trapped": "trapped"}
_VULNERABLE = {
    "child": "a child",
    "elderly": "an older person",
    "disabled": "a disabled person",
    "medical": "someone with a medical need",
    "pregnant": "a pregnant person",
}
_NEEDS = {"food": "food", "water": "drinking water", "meds": "medicine"}
# The engine's `hazard_firm_min` (contracts/engine_rules.md): below it a hazard is uncertain,
# so the words say "maybe" instead of a number.
_FIRM = 0.75


def sentence_for(rule_id: str | None) -> str | None:
    return RULE_SENTENCES.get(rule_id) if rule_id else None


def tier_pill(tier: str | None) -> dict[str, str] | None:
    return {"text": TIER_WORDS[tier], "cls": f"pill-{tier}"} if tier in TIER_WORDS else None


def maybe_pill(provisional_tier: str | None) -> dict[str, str] | None:
    """The dashed "maybe ..." pill for an abstain's provisional tier."""
    if provisional_tier not in TIER_WORDS:
        return None
    return {"text": f"maybe {TIER_WORDS[provisional_tier]}", "cls": "pill-maybe"}


def other_posts(n: int) -> str | None:
    if n <= 0:
        return None
    return f"+{n} other post" if n == 1 else f"+{n} other posts"


def incident_number(rec: Mapping[str, Any]) -> str:
    """The request's incident number (`D01-0147`, from its `received` event, #308), or the last 4
    characters of the request ID for requests stored before incident numbers existed."""
    return rec.get("incident") or rec["request_id"][-4:]


def incident(rec: Mapping[str, Any]) -> str:
    """Badge text: the incident number, or `#` and the request-ID suffix for older requests."""
    return rec.get("incident") or "#" + rec["request_id"][-4:]


def _people(e: dict[str, Any]) -> str:
    n = (e.get("people_count") or {}).get("value")
    if n is None:
        return "People"
    return "1 person" if n == 1 else f"{n} people"


def _maybe(text: str, conf: float | None) -> str:
    return text if conf is None or conf >= _FIRM else f"maybe {text[0].lower()}{text[1:]}"


def phrases(e: dict[str, Any]) -> list[str]:
    """Short phrases from the extracted fields, most urgent first."""
    hazards = e.get("hazards") or []
    kinds = {h["kind"]: h.get("confidence") for h in hazards}
    out: list[str] = []
    where = [_POSITION[k] for k in ("trapped", "on_roof") if k in kinds]
    subject = _people(e)
    if where:
        firm = all((kinds[k] or 0) >= _FIRM for k in ("trapped", "on_roof") if k in kinds)
        subject += (" " if firm else " maybe ") + " ".join(where)
    out.append(subject)
    for kind, words in _HAZARDS.items():
        if kind in kinds:
            out.append(_maybe(words, kinds[kind]))
    vulnerable: list[str] = []
    for v in e.get("vulnerable") or []:
        words = _VULNERABLE.get(v.get("kind"), "someone who needs extra care")
        if words not in vulnerable:
            vulnerable.append(words)
    if vulnerable:
        out.append("With " + _join(vulnerable))
    for n in e.get("needs") or []:
        what = _NEEDS.get(n.get("kind"), n.get("kind"))
        hours = n.get("hours_without")
        out.append(f"No {what} for {hours} hours" if hours is not None else f"Needs {what}")
    return out


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def headline(e: dict[str, Any]) -> str:
    """One line for the card, from hazards, vulnerable people and needs (never model text)."""
    parts = phrases(e)
    if len(parts) == 1:
        return f"{parts[0]} asking for help"
    return ", ".join(parts[:3])


def details(e: dict[str, Any]) -> list[str]:
    """Detail bullets: everything the headline left out, plus the time the message gives."""
    bullets = phrases(e)[3:]
    t = e.get("time_ref") or {}
    if t.get("text"):
        bullets.append(f"They said: {t['text']}")
    if t.get("is_stale_post"):
        bullets.append("This may be an old post")
    return bullets


def contact_line(rec: dict[str, Any], now: datetime | None = None) -> str:
    """Channel, contact and age, e.g. "Text message · 555-0101 · 12 min ago"."""
    parts = [CHANNEL_WORDS.get(rec.get("channel") or "", "Message")]
    if rec.get("contact"):
        parts.append(rec["contact"])
    ago = _ago(rec.get("received_at"), now)
    if ago:
        parts.append(ago)
    return " · ".join(parts)


def queue_reasons(rec: dict[str, Any], now: datetime | None = None) -> list[str]:
    """Why a request sits where it does inside its priority level, in the order the queue sorts
    on (rubric 04, `logic.within_tier_sort_key`): vulnerable people, then people, then age."""
    e = rec.get("extracted") or {}
    reasons = []
    vulnerable = logic.vulnerable_count(e)
    if vulnerable:
        reasons.append(f"{vulnerable} vulnerable" if vulnerable > 1 else "1 vulnerable")
    people = (e.get("people_count") or {}).get("value") or 0
    if people:
        reasons.append("1 person" if people == 1 else f"{people} people")
    ago = _ago(rec.get("received_at"), now)
    if ago:
        reasons.append(f"sent {ago}")
    return reasons
