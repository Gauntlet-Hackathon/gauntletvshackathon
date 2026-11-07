"""Rule ids, follow-up questions and defaults (see contracts/engine_rules.md)."""

from __future__ import annotations

# Placeholder until the build pipeline stamps a real git sha + binary hash.
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

Q_LOCATION = "What's the street address, or the nearest cross streets or landmark?"
Q_REQUEST = "Is this a request for help? If so, what do you need and where are you?"
