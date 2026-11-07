"""ULIDs: 26-character, lexicographically sortable ids (48-bit ms timestamp + 80 random bits).

Event ids and request ids are created on the laptop, so no network or database is needed.
Crockford base32 alphabet (no I, L, O, U). The first character is at most "7" (48 bits of time).
"""

from __future__ import annotations

import re
import secrets
import time

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
_MAX_TIME_MS = (1 << 48) - 1


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(timestamp_ms: int | None = None, randomness: int | None = None) -> str:
    """Return a new ULID. Pass timestamp_ms and randomness for deterministic tests."""
    ms = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    rand = secrets.randbits(80) if randomness is None else randomness
    if not 0 <= ms <= _MAX_TIME_MS:
        raise ValueError(f"timestamp_ms out of range: {ms}")
    if not 0 <= rand < (1 << 80):
        raise ValueError("randomness must fit in 80 bits")
    return _encode(ms, 10) + _encode(rand, 16)


def is_ulid(value: object) -> bool:
    return isinstance(value, str) and ULID_RE.fullmatch(value) is not None


def ulid_timestamp_ms(value: str) -> int:
    """Milliseconds since the Unix epoch encoded in the first 10 characters."""
    if not is_ulid(value):
        raise ValueError(f"not a ULID: {value!r}")
    ms = 0
    for char in value[:10]:
        ms = (ms << 5) | ALPHABET.index(char)
    return ms
