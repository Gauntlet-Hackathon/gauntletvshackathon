import pytest

from rescue_shared import logic
from rescue_shared.contracts_io import CONTRACTS, load_json
from rescue_shared.ulid import is_ulid

VECTORS = CONTRACTS / "vectors"


# ---- shared vectors (the Elixir cloud must pass the same files) ----


@pytest.mark.parametrize("case", load_json(VECTORS / "normalize_location.json")["cases"])
def test_normalize_location_vectors(case):
    assert logic.normalize_location(case["input"]) == case["expected"]


@pytest.mark.parametrize(
    "case", load_json(VECTORS / "duplicate_groups.json")["cases"], ids=lambda c: c["name"]
)
def test_duplicate_vectors(case):
    requests, window = case["requests"], case["window_hours"]
    assert all(is_ulid(r["request_id"]) for r in requests)
    assert logic.duplicate_candidates(requests, window) == case["expected"]["candidates"]
    assert logic.duplicate_groups(requests, window) == case["expected"]["groups"]


# ---- other logic ----


def test_normalize_contact():
    assert logic.normalize_contact("+1 (713) 555-0123") == "7135550123"
    assert logic.normalize_contact("713.555.0123") == "7135550123"
    assert logic.normalize_contact("12345") == "12345"
    assert logic.normalize_contact(None) == ""


STREETS = [
    "Westheimer Road", "Main Street", "I-45", "NRG Stadium", "Memorial Park", "Memorial Drive",
]  # fmt: skip


@pytest.mark.parametrize(
    "text, hits",
    [
        ("behind the Kroger on Westheimer", ["Westheimer Road"]),
        ("Main St & 5th Ave", ["Main Street"]),
        ("I-45 N at Beltway 8", ["I-45"]),
        ("by the Nrg stadium parking lot", ["NRG Stadium"]),
        # "Memorial Drive" also hits: a street name alone is enough (see contracts/logic.md).
        ("Memorial Park trailhead", ["Memorial Park", "Memorial Drive"]),
        ("Memorial Dr and Shepherd", ["Memorial Drive"]),
        ("behind the old Shell on 5th", []),
        ("", []),
        (None, []),
    ],
)
def test_street_list_hits(text, hits):
    assert logic.street_list_hits(text, STREETS) == hits


def test_hoax_marker_hits_counts_distinct_whole_word_markers():
    markers = ["prank", "just kidding", "jk", "LOL"]
    assert logic.hoax_marker_hits("Send a boat lol, it's a PRANK", markers) == 2
    assert logic.hoax_marker_hits("lol lol lol", markers) == 1
    assert logic.hoax_marker_hits("Just   kidding!!", markers) == 1
    assert logic.hoax_marker_hits("pranksters on the roof, jkl", markers) == 0
    assert logic.hoax_marker_hits("water is rising", markers) == 0
    assert logic.hoax_marker_hits("prank", []) == 0
    assert logic.hoax_marker_hits("prank", ["", "  "]) == 0


def _extracted(vulnerable=(), people=None):
    return {
        "vulnerable": [{"kind": k, "confidence": c} for k, c in vulnerable],
        "people_count": {"value": people, "confidence": 0.9},
    }


def test_within_tier_sort_key_orders_vulnerable_then_people_then_oldest():
    rows = {
        "few-people-old": (_extracted(people=1), "2026-11-07T06:00:00Z", "r1"),
        "many-people": (_extracted(people=6), "2026-11-07T07:00:00Z", "r2"),
        "one-vulnerable": (_extracted([("child", 0.9)], people=2), "2026-11-07T08:00:00Z", "r3"),
        "two-vulnerable": (
            _extracted([("child", 0.9), ("elderly", 0.6)], people=2),
            "2026-11-07T09:00:00Z",
            "r4",
        ),
        "weak-vulnerable-ignored": (
            _extracted([("child", 0.3)], people=6),
            "2026-11-07T05:00:00Z",
            "r5",
        ),
        "unknown-people": (_extracted(people=None), "2026-11-07T04:00:00Z", "r6"),
    }
    order = sorted(rows, key=lambda name: logic.within_tier_sort_key(*rows[name]))
    assert order == [
        "two-vulnerable",
        "one-vulnerable",
        "weak-vulnerable-ignored",  # 6 people, older than many-people
        "many-people",
        "few-people-old",
        "unknown-people",
    ]


def test_candidates_ignore_requests_without_a_key_and_handle_one_request():
    one = {
        "request_id": "01JCB00000000000000000H001",
        "received_at": "2026-11-07T06:00:00Z",
        "location_text": "5 Elm St",
        "people_count": None,
        "contact": None,
        "priority_tier": None,
    }
    assert logic.duplicate_candidates([one], 12) == {one["request_id"]: []}
    assert logic.duplicate_groups([one], 12) == []
    assert logic.duplicate_groups([], 12) == []
