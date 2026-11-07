"""Rule ids, follow-up questions and defaults (see contracts/engine_rules.md)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Placeholder in the source tree. rescue_engine.build rewrites this line in the copy it
# publishes to engine/dist/<rules_version>/, so a built engine emits its real version (A2).
RULES_VERSION = "rules@0000000+bin:00000000"

DEFAULT_THRESHOLDS: dict[str, float] = {
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

R_NAR_OLD = "R-NAR-02"
R_NAR_NOT_REQUEST = "R-NAR-01"
R_ABS_REQ = "R-ABS-REQ"
R_ABS_LOC = "R-ABS-LOC"
R_ABS_HOAX = "R-ABS-HOAX"
R_ABS_CNT = "R-ABS-CNT"
R_DUP = "R-DUP-01"

Q_LOCATION = "What's the street address, or the nearest cross streets or landmark?"
Q_REQUEST = "Is this a request for help? If so, what do you need and where are you?"
Q_HAZARD = (
    "Can you confirm what is happening right now: is water rising, is anyone injured, "
    "trapped or in danger?"
)
# low_confidence_hazard when the weak signal that would raise the tier is a vulnerable
# person (R13).
Q_HAZARD_VULNERABLE = (
    "Can you confirm what is happening right now: is water rising, is anyone injured, "
    "trapped or in danger, and is a child, elderly or disabled person with you?"
)
Q_CONFIRM = (
    "Please confirm: is anyone in immediate danger (rising water, injury, fire, trapped), "
    "and is a child, elderly or disabled person with you?"
)

Q_HOAX = "Can you confirm this request is real and tell us exactly what help you need?"
Q_COUNTS = "We have different counts for this location. How many people need help right now?"

R_ABS_HAZ = "R-ABS-HAZ"

# Characters that count as blank in location.text (R11). ASCII only, so the Mercury engine
# (string.strip) agrees byte for byte.
BLANK_CHARS = " \t\n\r\f\v"

# --- Contract enums (payloads/extracted.schema.json, common.schema.json) -------------------
# Values outside them are rejected (A9), as the Mercury engine does.

LOCATION_TYPES = frozenset({"landmark", "address", "coords", "none"})
HAZARD_KINDS = frozenset(
    {"rising_water", "on_roof", "injury", "medical_device_no_power", "fire", "trapped"}
)
VULNERABLE_KINDS = frozenset({"child", "elderly", "disabled", "medical", "pregnant"})
NEED_KINDS = frozenset({"food", "water", "meds"})
REQUEST_KINDS = frozenset({"rescue", "evacuation", "welfare_check", "supply", "other"})

# --- Step 4 (contracts/engine_rules.md "Step 4: tier") -------------------------------------

TIER_RANK = {"P1": 1, "P2": 2, "P3": 3}  # lower rank = more urgent
TIERS = frozenset(TIER_RANK)
P1_VULNERABLE = frozenset({"child", "elderly", "disabled"})


class Signals:
    """The facts the tier table looks at: hazards and vulnerable kinds kept at one band."""

    def __init__(
        self,
        hazards: set[str],
        vulnerable: set[str],
        needs: list[dict[str, Any]],
        request_kind: str,
        need_hours_over: float,
    ) -> None:
        self.hazards = hazards
        self.vulnerable = vulnerable
        self.needs = needs
        self.request_kind = request_kind
        self.need_hours_over = need_hours_over


def _long_need(s: Signals) -> bool:
    return any(
        n.get("kind") in {"food", "water", "meds"}
        and n.get("hours_without") is not None
        and n["hours_without"] > s.need_hours_over
        for n in s.needs
    )


# (rule_id, tier, condition), tried in this order; the first match wins.
TIER_RULES: list[tuple[str, str, Callable[[Signals], bool]]] = [
    ("R-P1-01", "P1", lambda s: "rising_water" in s.hazards
        and bool(s.hazards & {"on_roof", "trapped"})),
    ("R-P1-02", "P1", lambda s: "injury" in s.hazards),
    ("R-P1-03", "P1", lambda s: "medical_device_no_power" in s.hazards),
    ("R-P1-04", "P1", lambda s: "rising_water" in s.hazards
        and bool(s.vulnerable & P1_VULNERABLE)),
    ("R-P1-05", "P1", lambda s: "fire" in s.hazards),
    ("R-P2-01", "P2", lambda s: bool(s.hazards & {"trapped", "on_roof"})),
    ("R-P2-02", "P2", lambda s: bool(s.vulnerable)),
    ("R-P2-03", "P2", _long_need),
    ("R-P2-04", "P2", lambda s: bool(s.hazards) or s.request_kind == "rescue"),
    ("R-P3-01", "P3", lambda s: s.request_kind == "evacuation"),
    ("R-P3-02", "P3", lambda s: s.request_kind == "welfare_check"),
    ("R-P3-03", "P3", lambda s: s.request_kind == "supply"),
    ("R-P3-04", "P3", lambda s: True),
]


def match_tier(s: Signals) -> tuple[str, str]:
    """Return (tier, rule_id) of the first tier rule that matches."""
    for rule_id, tier, cond in TIER_RULES:
        if cond(s):
            return tier, rule_id
    raise AssertionError("R-P3-04 always matches")  # pragma: no cover


def _kinds(items: list[dict[str, Any]], min_conf: float) -> set[str]:
    return {i["kind"] for i in items if i["confidence"] >= min_conf}


def signals_at(
    extracted: dict[str, Any],
    min_conf: float,
    th: dict[str, float],
    vulnerable_min: float | None = None,
) -> Signals:
    """Signals keeping hazards with confidence >= min_conf and vulnerable entries with
    confidence >= vulnerable_min (default: min_conf)."""
    return Signals(
        hazards=_kinds(extracted.get("hazards") or [], min_conf),
        vulnerable=_kinds(
            extracted.get("vulnerable") or [],
            min_conf if vulnerable_min is None else vulnerable_min,
        ),
        needs=extracted.get("needs") or [],
        request_kind=extracted.get("request_kind") or "other",
        need_hours_over=th["need_hours_over"],
    )


def higher(a: str, b: str) -> bool:
    """True when tier a is more urgent than tier b."""
    return TIER_RANK[a] < TIER_RANK[b]


def highest(own: str, others: list[str | None]) -> str:
    """The most urgent of own and every non-null tier in others."""
    best = own
    for tier in others:
        if tier is not None and higher(tier, best):
            best = tier
    return best


def assess_tier(extracted: dict[str, Any], th: dict[str, float]) -> dict[str, Any]:
    """Step 4 at the three confidence bands. Never lowers a tier because of uncertainty.

    Returns {"firm", "uncertain", "weak", "rule_id", "hazard_question"}:
    - uncertain / rule_id: the request's tier (firm + uncertain signals) and its rule;
    - weak: the most cautious reading (firm + uncertain + weak signals), which is the
      provisional_tier every abstain carries (R1);
    - hazard_question: set when a weak signal would raise the tier (R-ABS-HAZ, R2): the
      vulnerable-person text when the raise needs a weak vulnerable entry (R13), else Q_HAZARD.
    """
    weak_min, uncertain_min = th["hazard_weak_min"], th["hazard_uncertain_min"]
    firm, _ = match_tier(signals_at(extracted, th["hazard_firm_min"], th))
    uncertain, rule_id = match_tier(signals_at(extracted, uncertain_min, th))
    weak, _ = match_tier(signals_at(extracted, weak_min, th))
    question = None
    if higher(weak, uncertain):
        hazards_only, _ = match_tier(signals_at(extracted, weak_min, th, uncertain_min))
        question = Q_HAZARD_VULNERABLE if higher(weak, hazards_only) else Q_HAZARD
    return {
        "firm": firm,
        "uncertain": uncertain,
        "weak": weak,
        "rule_id": rule_id,
        "hazard_question": question,
    }
