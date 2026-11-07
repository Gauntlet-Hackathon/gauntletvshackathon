"""Torch-free assembly of the location object (local change to the vendored tagger)."""

from __future__ import annotations

import math
from typing import Any

from . import labels as L


def _r(x: float) -> float:
    """Truncate to 3 decimals, like infer._r."""
    return math.floor(float(x) * 1000) / 1000


def assemble_location(text: str, loc_span: dict[str, Any] | None,
                      type_probs: dict[str, float]) -> dict[str, Any]:
    loc_type = max(L.LOCATION_TYPES, key=lambda k: type_probs[k])
    if loc_span is None or loc_type == "none":
        # No usable span: report "none" with the model's own probability of no location.
        return {"text": None, "type": "none", "confidence": _r(type_probs["none"])}
    # Local change to the vendored file (the trainer's copy differs): a weak span caps confidence.
    conf = _r(min(type_probs[loc_type], loc_span["confidence"]))
    return {"text": text[loc_span["start"]:loc_span["end"]], "type": loc_type, "confidence": conf}
