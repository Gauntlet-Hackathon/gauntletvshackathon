"""Startup check: the field app runs only with the config its engine build was frozen with.

The engine build's manifest carries `config_hash` over the thresholds, hoax markers and street
list (rescue_shared.frozen_config; DECISIONS H12 / R15), and the hash is folded into its
rules_version. If the field app's own copies differ, every decision would carry the frozen
rules_version while being made with other inputs, so the app refuses to start instead.

The build is found from `ENGINE_CMD`: when its program sits in `<build>/bin/` (or `<build>/`) next
to a `manifest.json`, that is the build. Without a manifest (the default `python -m
rescue_engine.fallback` from source, or a test double) there is nothing frozen to check.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rescue_shared import frozen_config


class FrozenConfigMismatch(RuntimeError):
    pass


def build_dir_for(engine_cmd: str | None) -> Path | None:
    """The engine build folder ENGINE_CMD runs from, or None."""
    if not engine_cmd:
        return None
    try:
        program = shlex.split(engine_cmd)[0]
    except (ValueError, IndexError):
        return None
    path = Path(program)
    if not path.is_absolute() and os.sep not in program and "/" not in program:
        return None  # a bare command on PATH (python, sh): not a build
    for cand in (path.parent.parent, path.parent):
        if (cand / "manifest.json").is_file():
            return cand
    return None


def check(
    thresholds: Mapping[str, float | int],
    hoax_markers: tuple[str, ...] | list[str],
    streets: tuple[str, ...] | list[str],
    env: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Raise FrozenConfigMismatch when the running config differs from the build's frozen config.

    Returns the build's manifest when a build was found and matches, None when there is no build.
    """
    env = os.environ if env is None else env
    build = build_dir_for(env.get("ENGINE_CMD"))
    if build is None:
        return None
    manifest = json.loads((build / "manifest.json").read_text(encoding="utf-8"))
    version = manifest.get("rules_version")
    expected_version = env.get("RULES_VERSION")
    if expected_version and expected_version != version:
        raise FrozenConfigMismatch(
            f"RULES_VERSION={expected_version} but ENGINE_CMD runs the build {version}"
        )
    want = manifest.get("config_hash")
    if not want:
        raise FrozenConfigMismatch(
            f"engine build {version} has no config_hash (built before the config freeze); "
            "rebuild it with `python -m rescue_engine.build --rebuild-from <build>`"
        )
    if frozen_config.combined_bin_hash(manifest.get("artifact_hash", ""), want) != manifest.get(
        "bin_hash"
    ):
        raise FrozenConfigMismatch(f"engine build {version}: manifest hashes are inconsistent")
    got = frozen_config.config_hash(thresholds, hoax_markers, streets)
    if got != want:
        running = {"thresholds": thresholds, "hoax_markers": hoax_markers, "streets": streets}
        parts = "thresholds, hoax markers or street list"
        frozen_dir = build / frozen_config.CONFIG_SUBDIR
        if frozen_dir.is_dir():
            diff = frozen_config.config_diff(running, frozen_config.load_config(frozen_dir))
            parts = ", ".join(diff) or parts
        raise FrozenConfigMismatch(
            f"decision config differs from the frozen engine build {version} "
            f"(config_hash {got} != {want}; changed: {parts}). The demo rules are frozen "
            "(docs/DECISIONS.md H12): restore contracts/fixtures/ or make a new build."
        )
    return manifest
