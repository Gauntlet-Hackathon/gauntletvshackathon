"""The decision config that is frozen together with an engine build (H12 extension, R15).

Three inputs change decisions without being part of the engine binary:
  thresholds    contracts/fixtures/thresholds.json  (context.thresholds the field sends)
  hoax_markers  contracts/fixtures/hoax_markers.txt (counted into context.hoax_marker_hits)
  streets       contracts/fixtures/streets.txt      (street list for location extraction)

`config_hash` is a hash over their *parsed* content (thresholds as canonical JSON, the two lists
as the non-empty, non-comment lines the field reads), so a whitespace-only edit does not count,
but any change to a value or a list entry does. `engine/rescue_engine/build.py` writes it into the
manifest (`config_hash`) and folds it into `bin_hash`, so the rules_version names the config too:

    bin_hash = combined_bin_hash(artifact_hash, config_hash)

The field app recomputes `config_hash` from what it loaded at startup and refuses to start when it
differs from the manifest of the build it runs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"  # same as contracts_io.CONTRACTS

FIXTURES_DIR = CONTRACTS / "fixtures"
CONFIG_FILES: dict[str, str] = {
    "thresholds": "thresholds.json",
    "hoax_markers": "hoax_markers.txt",
    "streets": "streets.txt",
}
# engine/dist/<dist_dirname(rules_version)>/config/ holds a copy of the frozen files
CONFIG_SUBDIR = "config"
HASH_CHARS = 16


def read_list(path: str | Path) -> tuple[str, ...]:
    """Non-empty, non-comment lines of a text file, stripped."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return tuple(s for s in (ln.strip() for ln in lines) if s and not s.startswith("#"))


def read_thresholds(path: str | Path) -> dict[str, float | int]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, int | float) and not isinstance(v, bool)
        for k, v in data.items()
    ):
        raise ValueError(f"{path}: thresholds must be a JSON object of numbers")
    return data


def load_config(directory: str | Path = FIXTURES_DIR) -> dict[str, Any]:
    """The three frozen inputs as read from a folder holding CONFIG_FILES."""
    d = Path(directory)
    return {
        "thresholds": read_thresholds(d / CONFIG_FILES["thresholds"]),
        "hoax_markers": read_list(d / CONFIG_FILES["hoax_markers"]),
        "streets": read_list(d / CONFIG_FILES["streets"]),
    }


def canonical(
    thresholds: Mapping[str, float | int],
    hoax_markers: Sequence[str],
    streets: Sequence[str],
) -> bytes:
    doc = {
        "thresholds": {k: thresholds[k] for k in sorted(thresholds)},
        "hoax_markers": list(hoax_markers),
        "streets": list(streets),
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def config_hash(
    thresholds: Mapping[str, float | int],
    hoax_markers: Sequence[str],
    streets: Sequence[str],
) -> str:
    return hashlib.sha256(canonical(thresholds, hoax_markers, streets)).hexdigest()[:HASH_CHARS]


def config_hash_of(directory: str | Path = FIXTURES_DIR) -> str:
    c = load_config(directory)
    return config_hash(c["thresholds"], c["hoax_markers"], c["streets"])


def combined_bin_hash(artifact_hash: str, cfg_hash: str) -> str:
    """bin_hash of a build: covers the engine artifact and the frozen config together."""
    data = f"artifact:{artifact_hash}\0config:{cfg_hash}".encode()
    return hashlib.sha256(data).hexdigest()[:HASH_CHARS]


def config_diff(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    """Names of the parts (thresholds / hoax_markers / streets) that differ between two configs."""
    out = []
    for key in CONFIG_FILES:
        av, bv = a.get(key), b.get(key)
        if key != "thresholds":
            av, bv = list(av or ()), list(bv or ())
        if av != bv:
            out.append(key)
    return out
