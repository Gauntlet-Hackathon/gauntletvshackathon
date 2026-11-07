"""Logic that field, cloud, engine and evals must agree on exactly.

Pure stdlib, no I/O. The spec is contracts/logic.md; the Elixir cloud re-implements
normalize_location and duplicate grouping and must pass contracts/vectors/*.json.
Timestamps are the contract format: UTC, seconds precision, trailing Z (2026-11-07T14:02:11Z).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

_COORDS_RE = re.compile(r"^\s*(-?\d{1,3}\.\d+)\s*[,;\s]\s*(-?\d{1,3}\.\d+)\s*$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# Whole-token replacements applied after lowercasing and punctuation removal.
TOKEN_MAP: dict[str, str] = {
    "st": "street", "ave": "avenue", "av": "avenue", "blvd": "boulevard", "rd": "road",
    "dr": "drive", "ln": "lane", "hwy": "highway", "fwy": "freeway", "pkwy": "parkway",
    "ct": "court", "cir": "circle", "pl": "place", "trl": "trail", "ter": "terrace",
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
}  # fmt: skip

# Dropped from locations: filler words that say where *around* a place someone is.
STOPWORDS: frozenset[str] = frozenset(
    "the a an at near by behind in on off next to across from of around outside inside "
    "front beside between just past over under close".split()
)

# Street-type words. Ignored on the street-list side of street_list_hits().
SUFFIX_WORDS: frozenset[str] = frozenset(
    "street avenue boulevard road drive lane highway freeway parkway court circle place trail "
    "terrace".split()
)


def _tokens(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c)).casefold()
    folded = folded.replace("'", "").replace("’", "").replace("&", " and ")
    words = _NON_ALNUM_RE.sub(" ", folded).split()
    return [t for t in (TOKEN_MAP.get(w, w) for w in words) if t not in STOPWORDS]


def normalize_location(text: str | None) -> str:
    """Canonical key for a location text; equal keys mean "same place". "" means no usable key.

    Coordinates (two decimal numbers) become "geo:<lat 4dp>,<lon 4dp>". Anything else: strip
    accents, lowercase, drop apostrophes, "&" -> "and", punctuation -> space, expand street and
    direction abbreviations, drop filler words, sort the remaining tokens, join with single spaces.
    """
    if text is None:
        return ""
    coords = _COORDS_RE.match(text)
    if coords:
        return f"geo:{float(coords[1]):.4f},{float(coords[2]):.4f}"
    return " ".join(sorted(_tokens(text)))


def normalize_contact(contact: str | None) -> str:
    """Digits only; an 11-digit number starting with 1 loses the 1. "" means no contact."""
    digits = "".join(c for c in (contact or "") if c.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def street_list_hits(location_text: str | None, streets: Iterable[str]) -> list[str]:
    """Street/landmark list entries that the location text mentions, in list order.

    An entry hits when all of its name tokens (street-type words removed) are among the
    location's tokens.
    """
    location_tokens = set(_tokens(location_text or ""))
    hits = []
    for entry in streets:
        name_tokens = {t for t in _tokens(entry) if t not in SUFFIX_WORDS}
        if name_tokens and name_tokens <= location_tokens:
            hits.append(entry)
    return hits


def hoax_marker_hits(text: str, markers: Iterable[str]) -> int:
    """How many distinct markers occur in the text (case-insensitive, whole words or phrases)."""
    folded = " ".join(text.casefold().split())
    seen = set()
    for marker in markers:
        key = " ".join(marker.casefold().split())
        if key and key not in seen and re.search(rf"(?<!\w){re.escape(key)}(?!\w)", folded):
            seen.add(key)
    return len(seen)


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _order(event: Mapping[str, Any]) -> tuple[str, str]:
    return (event["received_at"], event["request_id"])


def duplicate_candidates(
    events: Sequence[Mapping[str, Any]], window_hours: float
) -> dict[str, list[dict[str, Any]]]:
    """For each event, the earlier events that are candidate duplicates of it (engine_input shape).

    Each event: request_id, received_at, location_text, people_count (int|None),
    contact (str|None), priority_tier (P1|P2|P3|None). B is a candidate for A when B is earlier
    than A (by received_at, then request_id), at most window_hours before it, both normalized
    locations are equal and non-empty, and either the normalized contacts are equal and non-empty
    (contact_match) or both people counts are known and equal. Candidates are sorted oldest
    first. Every event id is a key (maybe with []).
    """
    keyed = [
        (e, normalize_location(e.get("location_text")), normalize_contact(e.get("contact")))
        for e in events
    ]
    out: dict[str, list[dict[str, Any]]] = {}
    for a, a_loc, a_contact in keyed:
        found = []
        for b, b_loc, b_contact in keyed:
            if not a_loc or a_loc != b_loc or _order(b) >= _order(a):
                continue
            gap = (_parse_ts(a["received_at"]) - _parse_ts(b["received_at"])).total_seconds()
            if gap > window_hours * 3600:
                continue
            contact_match = bool(a_contact) and a_contact == b_contact
            a_count = a.get("people_count")
            same_count = a_count is not None and a_count == b.get("people_count")
            if contact_match or same_count:
                found.append((b, contact_match))
        found.sort(key=lambda pair: _order(pair[0]))
        out[a["request_id"]] = [
            {
                "request_id": b["request_id"],
                "received_at": b["received_at"],
                "people_count": b.get("people_count"),
                "contact_match": contact_match,
                "priority_tier": b.get("priority_tier"),
            }
            for b, contact_match in found
        ]
    return out


def duplicate_groups(events: Sequence[Mapping[str, Any]], window_hours: float) -> list[list[str]]:
    """Connected components of the candidate relation with 2+ members, as request ids.

    Members are sorted oldest first and groups are sorted by their oldest member.
    """
    by_id = {e["request_id"]: e for e in events}
    parent = {rid: rid for rid in by_id}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for rid, candidates in duplicate_candidates(events, window_hours).items():
        for cand in candidates:
            parent[find(rid)] = find(cand["request_id"])
    groups: dict[str, list[str]] = {}
    for rid in by_id:
        groups.setdefault(find(rid), []).append(rid)
    ordered = [sorted(g, key=lambda r: _order(by_id[r])) for g in groups.values() if len(g) > 1]
    return sorted(ordered, key=lambda g: _order(by_id[g[0]]))


def vulnerable_count(extracted: Mapping[str, Any], min_confidence: float = 0.5) -> int:
    """Number of vulnerable entries with confidence >= min_confidence."""
    return sum(1 for v in extracted.get("vulnerable", []) if v["confidence"] >= min_confidence)


def within_tier_sort_key(
    extracted: Mapping[str, Any], received_at: str, request_id: str = ""
) -> tuple[int, int, str, str]:
    """Sort ascending within one priority tier (04): more vulnerable people first, then more
    people (unknown counts as 0), then older first; request_id breaks ties."""
    people = (extracted.get("people_count") or {}).get("value") or 0
    return (-vulnerable_count(extracted), -people, received_at, request_id)
