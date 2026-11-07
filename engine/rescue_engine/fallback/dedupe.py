"""Steps 4-5: duplicates and contradictions across a request's duplicate candidates.

Candidates arrive sorted oldest first (contracts/engine_input.schema.json); locating them
(normalized location + overlapping contact within the window) is the caller's job.
"""

from __future__ import annotations

from typing import Any

from . import rules


def conflicting_counts(extracted: dict[str, Any], candidates: list[dict[str, Any]]) -> bool:
    """True when a contact-matched candidate has a known, different people count."""
    mine = (extracted.get("people_count") or {}).get("value")
    if mine is None:
        return False
    return any(
        c.get("contact_match") and c.get("people_count") is not None and c["people_count"] != mine
        for c in candidates
    )


def group_tier(own_tier: str, candidates: list[dict[str, Any]]) -> str:
    """Highest tier of the request and every candidate that has one."""
    best = own_tier
    for c in candidates:
        tier = c.get("priority_tier")
        if tier is not None and rules.higher(tier, best):
            best = tier
    return best


def earliest(candidates: list[dict[str, Any]]) -> str:
    """request_id of the earliest candidate (by received_at; input order breaks ties)."""
    return min(candidates, key=lambda c: c["received_at"])["request_id"]
