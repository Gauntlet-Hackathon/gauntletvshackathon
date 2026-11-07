"""Decision engine, steps 1-3: request check, location check, priority tier.

Pure and deterministic: reads an engine_input dict, returns an engine_output dict.
Later steps (hoax, duplicates, contradictions) are added by E3.
"""

from __future__ import annotations

from typing import Any

from . import rules


def _output(
    outcome: str,
    rule_id: str,
    abstain_reason: str | None = None,
    follow_up_question: str | None = None,
    priority_tier: str | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "priority_tier": priority_tier,
        "rule_id": rule_id,
        "duplicate_of": None,
        "abstain_reason": abstain_reason,
        "follow_up_question": follow_up_question,
        "rules_version": rules.RULES_VERSION,
    }


def _thresholds(context: dict[str, Any]) -> dict[str, float]:
    return {**rules.DEFAULT_THRESHOLDS, **(context.get("thresholds") or {})}


def check_request(extracted: dict[str, Any], th: dict[str, float]) -> dict[str, Any] | None:
    """Step 1. Returns an output when the request check decides, else None."""
    is_request = extracted["is_request"]
    if is_request["confidence"] < th["request_confidence_min"]:
        return _output("abstain", rules.R_ABS_REQ, "low_confidence_request", rules.Q_REQUEST)
    if not is_request["value"]:
        return _output("not_a_request", rules.R_NAR_NOT_REQUEST)
    time_ref = extracted.get("time_ref") or {}
    stale_conf = time_ref.get("confidence", 0)
    if time_ref.get("is_stale_post") and stale_conf >= th["stale_confidence_min"]:
        return _output("not_a_request", rules.R_NAR_OLD)
    return None


def check_location(
    extracted: dict[str, Any], context: dict[str, Any], th: dict[str, float]
) -> dict[str, Any] | None:
    """Step 2. Returns an abstain output when the location is unusable, else None."""
    location = extracted["location"]
    usable = location["type"] != "none" and location["confidence"] >= th["location_confidence_min"]
    if usable and location["type"] == "landmark" and not context.get("street_list_hits"):
        usable = False
    if usable:
        return None
    return _output("abstain", rules.R_ABS_LOC, "no_usable_location", rules.Q_LOCATION)


def check_tier(extracted: dict[str, Any], th: dict[str, float]) -> dict[str, Any]:
    """Step 3. Always decides: a prioritized output, or abstain on a weak raising signal."""
    tier = rules.assess_tier(extracted, th)
    if tier["abstain"]:
        return _output("abstain", rules.R_ABS_HAZ, "low_confidence_hazard", rules.Q_HAZARD)
    return _output(
        "prioritized", tier["rule_id"], None, tier["follow_up_question"], tier["tier"]
    )


def decide_one(engine_input: dict[str, Any]) -> dict[str, Any]:
    """Decide one engine_input. Raises NotImplementedError when duplicate candidates exist."""
    extracted = engine_input["extracted"]
    context = engine_input.get("context") or {}
    th = _thresholds(context)
    result = check_request(extracted, th) or check_location(extracted, context, th)
    if result is not None:
        return result
    result = check_tier(extracted, th)
    if context.get("duplicate_candidates") and result["outcome"] == "prioritized":
        raise NotImplementedError("duplicates and contradictions (E3) are not implemented yet")
    return result


def decide(
    engine_input: dict[str, Any] | list[dict[str, Any]],
) -> dict[str, Any] | list[dict[str, Any]]:
    """Accept one engine_input or a list of them; return the matching shape."""
    if isinstance(engine_input, list):
        return [decide_one(item) for item in engine_input]
    return decide_one(engine_input)
