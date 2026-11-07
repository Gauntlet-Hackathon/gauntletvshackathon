"""The runner builds the engine context like the field pipeline (street list, hoax markers)."""

from rescue_engine.fallback.engine import decide_one
from rescue_evals.runner import default_context
from rescue_shared import frozen_config, logic


def _extracted(location_text, location_type="landmark"):
    return {
        "location": {"text": location_text, "type": location_type, "confidence": 0.9},
        "people_count": {"value": 2, "confidence": 0.9},
        "vulnerable": [],
        "hazards": [{"kind": "rising_water", "confidence": 0.9}],
        "time_ref": {"text": None, "is_stale_post": False, "confidence": 0.9},
        "is_request": {"value": True, "confidence": 0.95},
        "spans": [],
        "needs": [],
        "request_kind": "rescue",
    }


def test_no_arguments_keeps_the_empty_context():
    ctx = default_context()
    assert ctx["street_list_hits"] == [] and ctx["hoax_marker_hits"] == 0
    assert ctx["duplicate_candidates"] == []


def test_landmark_on_the_street_list_is_usable():
    ex = _extracted("behind the Kroger on Westheimer Road")
    ctx = default_context("help, behind the Kroger on Westheimer Road, water rising", ex)
    assert ctx["street_list_hits"] == ["Westheimer Road"]
    assert decide_one({"extracted": ex, "context": ctx})["outcome"] == "prioritized"
    # without the hit the same extraction abstains, as before this change
    assert decide_one({"extracted": ex, "context": default_context()})["outcome"] == "abstain"


def test_hoax_markers_counted_on_the_raw_text_like_the_field():
    marker = frozen_config.load_config()["hoax_markers"][0]
    text = f"{marker} at 12 Oak Street"
    ctx = default_context(text, _extracted("12 Oak Street", "address"))
    assert ctx["hoax_marker_hits"] == logic.hoax_marker_hits(text, [marker]) >= 1
