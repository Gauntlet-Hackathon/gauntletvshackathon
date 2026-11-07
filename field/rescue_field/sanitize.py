"""Text clean-up for everything a client types into the field app (intake and status).

Two kinds of character are accepted by Python and by the contracts but break what comes after
(review H-02, F-03): U+0000 (PostgreSQL jsonb and the Mercury JSON reader reject it) and a lone
UTF-16 surrogate such as half an emoji from an SMS gateway (strict UTF-8 encoders refuse it, so
the board answers 500 and the sync batch never encodes). They are removed at intake, before
anything is stored; all other text passes through unchanged.
"""

from __future__ import annotations

import re

# json.loads joins an escaped surrogate pair into one code point, so any surrogate left in a
# Python str is a lone one.
_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def clean_text(value: str) -> str:
    """`value` without NUL characters, each lone surrogate replaced by U+FFFD."""
    return _SURROGATE_RE.sub("�", value.replace("\x00", ""))
