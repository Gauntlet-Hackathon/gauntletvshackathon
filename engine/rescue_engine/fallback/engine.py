"""Decision engine (contracts/engine_rules.md): request check, location, tier, hoax, duplicates.

Pure and deterministic: reads an engine_input dict, returns an engine_output dict.
Raises ValueError/TypeError on input the contract does not allow (unknown enum values,
unordered hazard thresholds); the CLI turns that into exit 1.
"""

from __future__ import annotations

from typing import Any

from . import dedupe, rules


def _output(
    outcome: str,
    rule_id: str,
    abstain_reason: str | None = None,
    follow_up_question: str | None = None,
    priority_tier: str | None = None,
    provisional_tier: str | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "priority_tier": priority_tier,
        "provisional_tier": provisional_tier,
        "rule_id": rule_id,
        "duplicate_of": None,
        "abstain_reason": abstain_reason,
        "follow_up_question": follow_up_question,
        "rules_version": rules.RULES_VERSION,
    }


def _abstain(rule_id: str, reason: str, question: str, provisional_tier: str) -> dict[str, Any]:
    return _output("abstain", rule_id, reason, question, provisional_tier=provisional_tier)


def _thresholds(context: dict[str, Any]) -> dict[str, float]:
    th = {**rules.DEFAULT_THRESHOLDS, **(context.get("thresholds") or {})}
    weak, uncertain, firm = th["hazard_weak_min"], th["hazard_uncertain_min"], th["hazard_firm_min"]
    if not weak <= uncertain <= firm:  # R8
        raise ValueError(
            "thresholds must satisfy hazard_weak_min <= hazard_uncertain_min <= hazard_firm_min"
        )
    return th


def _enum(field: str, value: Any, allowed: frozenset[str]) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field}: unknown value {value!r}")


def _objects(field: str, value: Any) -> list[dict[str, Any]]:
    """A list of objects; missing or null is an empty list."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(i, dict) for i in value):
        raise TypeError(f"{field!r} must be an array of objects")
    return value


def check_enums(extracted: dict[str, Any], context: dict[str, Any]) -> None:
    """Reject enum values outside the contract (A9), like the Mercury engine."""
    location = extracted["location"]
    if not isinstance(location, dict):
        raise TypeError("'location' must be an object")
    _enum("location.type", location.get("type"), rules.LOCATION_TYPES)
    text = location.get("text")
    if text is not None and not isinstance(text, str):
        raise TypeError("'text' must be a string")
    for item in _objects("hazards", extracted.get("hazards")):
        _enum("hazards.kind", item.get("kind"), rules.HAZARD_KINDS)
    for item in _objects("vulnerable", extracted.get("vulnerable")):
        _enum("vulnerable.kind", item.get("kind"), rules.VULNERABLE_KINDS)
    for item in _objects("needs", extracted.get("needs")):
        _enum("needs.kind", item.get("kind"), rules.NEED_KINDS)
    if extracted.get("request_kind") is not None:
        _enum("request_kind", extracted["request_kind"], rules.REQUEST_KINDS)
    for c in _objects("duplicate_candidates", context.get("duplicate_candidates")):
        if c.get("priority_tier") is not None:
            _enum("priority_tier", c["priority_tier"], rules.TIERS)


def check_request(
    extracted: dict[str, Any], th: dict[str, float], tier: dict[str, Any]
) -> dict[str, Any] | None:
    """Step 1. Returns an output when the request check decides, else None."""
    is_request = extracted["is_request"]
    if is_request["confidence"] < th["request_confidence_min"]:
        return _abstain(rules.R_ABS_REQ, "low_confidence_request", rules.Q_REQUEST, tier["weak"])
    if not is_request["value"]:
        return _output("not_a_request", rules.R_NAR_NOT_REQUEST)
    time_ref = extracted.get("time_ref") or {}
    stale_conf = time_ref.get("confidence", 0)
    if time_ref.get("is_stale_post") and stale_conf >= th["stale_confidence_min"]:
        if tier["weak"] == "P1":  # R5: a stale-looking P1 goes to a human, never dropped
            return _abstain(rules.R_ABS_REQ, "low_confidence_request", rules.Q_REQUEST, "P1")
        return _output("not_a_request", rules.R_NAR_OLD)
    return None


def check_location(
    extracted: dict[str, Any], context: dict[str, Any], th: dict[str, float], tier: dict[str, Any]
) -> dict[str, Any] | None:
    """Step 2. Returns an abstain output when the location is unusable, else None."""
    location = extracted["location"]
    text = location.get("text") or ""
    usable = (
        location["type"] != "none"
        and location["confidence"] >= th["location_confidence_min"]
        and text.strip(rules.BLANK_CHARS) != ""  # R11: nowhere to send a crew
    )
    if usable and location["type"] == "landmark" and not context.get("street_list_hits"):
        usable = False
    if usable:
        return None
    return _abstain(rules.R_ABS_LOC, "no_usable_location", rules.Q_LOCATION, tier["weak"])


def check_tier(tier: dict[str, Any]) -> dict[str, Any]:
    """Step 3. Prioritized, or abstain when a weak signal would raise a P3 (R2)."""
    question = tier["hazard_question"]
    if question is not None and tier["uncertain"] == "P3":
        return _abstain(rules.R_ABS_HAZ, "low_confidence_hazard", question, tier["weak"])
    if question is None and rules.higher(tier["uncertain"], tier["firm"]):
        question = rules.Q_CONFIRM
    return _output("prioritized", tier["rule_id"], None, question, tier["uncertain"])


def check_hoax(
    extracted: dict[str, Any],
    context: dict[str, Any],
    th: dict[str, float],
    tier: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Step 4. Hoax markers plus a shaky is_request: a P1 stays prioritized with the hoax
    question (R3); anything else is routed to a human, never discarded."""
    if not (
        context.get("hoax_marker_hits", 0) >= th["hoax_marker_min"]
        and extracted["is_request"]["confidence"] < th["hoax_request_confidence_max"]
    ):
        return result
    if result["outcome"] == "prioritized" and result["priority_tier"] == "P1":
        return {**result, "follow_up_question": rules.Q_HOAX}
    return _abstain(rules.R_ABS_HOAX, "possible_hoax", rules.Q_HOAX, tier["weak"])


def check_duplicates(
    extracted: dict[str, Any],
    candidates: list[dict[str, Any]],
    own: dict[str, Any],
    tier: dict[str, Any],
) -> dict[str, Any]:
    """Step 5 on a prioritized request that has duplicate candidates."""
    if dedupe.conflicting_counts(extracted, candidates):  # R4: keeps the group's tier
        provisional = dedupe.group_tier(tier["weak"], candidates)
        return _abstain(rules.R_ABS_CNT, "conflicting_counts", rules.Q_COUNTS, provisional)
    own_tier = own["priority_tier"]
    group = dedupe.group_tier(own_tier, candidates)
    # R9: keep the request's own question when its own signals set the group tier, or when
    # its weak signals would raise it above the group tier (a weak P1 on a P2 group keeps
    # the hazard question, so a human still asks it).
    keep = dedupe.sets_group_tier(own_tier, candidates) or rules.higher(tier["weak"], group)
    question = own["follow_up_question"] if keep else None
    out = _output(
        "duplicate",
        rules.R_DUP,
        follow_up_question=question,
        priority_tier=group,
    )
    out["duplicate_of"] = dedupe.earliest(candidates)
    return out


def decide_one(engine_input: dict[str, Any]) -> dict[str, Any]:
    """Decide one engine_input."""
    extracted = engine_input["extracted"]
    context = engine_input.get("context") or {}
    check_enums(extracted, context)
    th = _thresholds(context)
    tier = rules.assess_tier(extracted, th)
    result = check_request(extracted, th, tier) or check_location(extracted, context, th, tier)
    if result is not None:
        return result
    result = check_hoax(extracted, context, th, tier, check_tier(tier))
    candidates = context.get("duplicate_candidates")
    if candidates and result["outcome"] == "prioritized":
        return check_duplicates(extracted, candidates, result, tier)
    return result


def decide(
    engine_input: dict[str, Any] | list[dict[str, Any]],
) -> dict[str, Any] | list[dict[str, Any]]:
    """Accept one engine_input or a list of them; return the matching shape."""
    if isinstance(engine_input, list):
        return [decide_one(item) for item in engine_input]
    return decide_one(engine_input)
