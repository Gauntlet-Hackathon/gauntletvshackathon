"""Volunteer wording (UI-2): pills, one sentence per rule ID, and card text built from fields.

Everything a volunteer reads in queue mode comes from here. Headlines and details are composed
from the extracted fields (hazards, vulnerable people, needs, people count): never a model
translation. Rule IDs, versions and confidences never leave this module as text.
"""

from __future__ import annotations

from typing import Any

# Pills (docs/design/communityrescue/volunteer-app.html).
TIER_PILLS = {"P1": "Needs help now", "P2": "Needs help soon", "P3": "Can wait"}
CHECK_FIRST = "Check first"
SET_ASIDE = "Set aside"
ESCALATED = "Couldn't get there"


def tier_pill(tier: str) -> str:
    return TIER_PILLS[tier]


def provisional_pill(tier: str | None) -> str | None:
    """The dashed pill on a Check first card: the engine's most cautious reading, as a maybe."""
    return f"maybe {TIER_PILLS[tier]}" if tier in TIER_PILLS else None


def other_posts(n: int) -> str | None:
    """Repeats are folded into one card: "+1 other post", "+3 other posts"."""
    if n <= 0:
        return None
    return f"+{n} other post" if n == 1 else f"+{n} other posts"


# "Why is this one first?": one plain sentence per rule ID (contracts/engine_rules.md, "Rule ids").
RULE_SENTENCES = {
    "R-P1-01": "Water is rising and people are trapped or on a roof.",
    "R-P1-02": "Someone is hurt.",
    "R-P1-03": "Someone's medical equipment has no power.",
    "R-P1-04": "Water is rising and a child, an older person or a disabled person is there.",
    "R-P1-05": "There is a fire.",
    "R-P2-01": "People are trapped or on a roof.",
    "R-P2-02": "Someone who needs extra care is there.",
    "R-P2-03": "They have gone a long time without food, water or medicine.",
    "R-P2-04": "They are in danger or stuck and asked to be rescued.",
    "R-P3-01": "They asked for help leaving, with no danger reported.",
    "R-P3-02": "Someone asked us to check on a person, with no danger reported.",
    "R-P3-03": "They asked for supplies, with no danger reported.",
    "R-P3-04": "They asked for help, with no danger reported.",
    "R-DUP-01": "Several posts are about the same people; this card counts the most urgent one.",
    "R-ABS-REQ": "We are not sure this is a current request for help, so ask first.",
    "R-ABS-HOAX": "This post might not be real, so ask first.",
    "R-ABS-LOC": "We don't know where they are yet, so ask first.",
    "R-ABS-HAZ": "We are not sure how serious it is, so ask first.",
    "R-ABS-CNT": "Posts about this place give different numbers of people, so ask first.",
    "R-NAR-01": "This doesn't look like a request for help.",
    "R-NAR-02": "This looks like an old post that is not current.",
}
WHY_ORDER = "Cards that need help now come first; among them, more people and older posts go first."


def why(rule_id: str | None) -> str:
    return RULE_SENTENCES.get(rule_id or "", "The rules put this one here.")


# Labels for the follow-up questions /board shows on a tier row (board._label).
QUESTION_LABELS = {"Possible hoax": "Might not be real", "Needs confirmation": "Needs confirmation"}

_HAZARDS = {
    "rising_water": "water rising",
    "on_roof": "on a roof",
    "injury": "someone hurt",
    "medical_device_no_power": "medical equipment without power",
    "fire": "fire",
    "trapped": "trapped",
}
_VULNERABLE = {
    "child": "a child",
    "elderly": "an older person",
    "disabled": "a disabled person",
    "medical": "someone with a medical need",
    "pregnant": "a pregnant person",
}
_NEEDS = {"food": "food", "water": "drinking water", "meds": "medicine"}
_KINDS = {
    "rescue": "Needs rescue",
    "evacuation": "Wants help leaving",
    "welfare_check": "Asks for a check on someone",
    "supply": "Needs supplies",
    "other": "Asks for help",
}
_CHANNELS = {
    "sms": "Text message",
    "social": "Social post",
    "form": "Web form",
    "call_note": "Phone call",
    "csv": "Imported list",
}


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _people(e: dict[str, Any]) -> str | None:
    n = (e.get("people_count") or {}).get("value")
    if n is None:
        return None
    return "1 person" if n == 1 else f"{n} people"


def hazards(e: dict[str, Any]) -> list[str]:
    return _unique([_HAZARDS[h["kind"]] for h in e.get("hazards", []) if h["kind"] in _HAZARDS])


def vulnerable(e: dict[str, Any]) -> list[str]:
    return _unique(
        [_VULNERABLE[v["kind"]] for v in e.get("vulnerable", []) if v["kind"] in _VULNERABLE]
    )


def needs(e: dict[str, Any]) -> list[str]:
    return _unique([_NEEDS[n["kind"]] for n in e.get("needs", []) if n["kind"] in _NEEDS])


def headline(e: dict[str, Any]) -> str:
    """e.g. "3 people, water rising and on a roof, with a child". Built only from fields."""
    hz, vu, nd = hazards(e), vulnerable(e), needs(e)
    parts = []
    lead = _people(e)
    if hz:
        parts.append(_join(hz))
    if vu:
        parts.append("with " + _join(vu))
    if nd and not hz:
        parts.append("needs " + _join(nd))
    if not parts:
        parts.append(_KINDS.get(e.get("request_kind"), "Asks for help").lower())
    text = ", ".join(([lead] if lead else []) + parts)
    return text[0].upper() + text[1:]


def details(e: dict[str, Any]) -> list[str]:
    """Short bullets under the headline."""
    out = []
    people = _people(e)
    out.append(f"{people} reported" if people else "Number of people not given")
    if hz := hazards(e):
        out.append("Danger: " + _join(hz))
    if vu := vulnerable(e):
        out.append("With " + _join(vu))
    for n in e.get("needs", []):
        if n["kind"] not in _NEEDS:
            continue
        hours = n.get("hours_without")
        if hours is None:
            out.append(f"Needs {_NEEDS[n['kind']]}")
        else:
            out.append(f"Without {_NEEDS[n['kind']]} for {round(hours):g} h")
    kind = _KINDS.get(e.get("request_kind"))
    if kind:
        out.append(kind)
    when = (e.get("time_ref") or {}).get("text")
    if when:
        out.append(f"Says: {when}")
    return _unique(out)


def address(e: dict[str, Any]) -> str | None:
    loc = e.get("location") or {}
    if loc.get("type") == "none" or not (loc.get("text") or "").strip():
        return None
    return loc["text"].strip()


def contact_line(channel: str | None, contact: str | None, ago: str | None) -> str:
    """"Text message from 555-0100 · 12 min ago"."""
    how = _CHANNELS.get(channel or "", "Message")
    text = f"{how} from {contact}" if contact else how
    return f"{text} · {ago}" if ago else text
