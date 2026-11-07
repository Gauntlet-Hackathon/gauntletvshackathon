"""WINPATH-1: every tracked path must be checkable-out on Windows.

Found by: `git pull` on a Windows laptop (2026-11-07) refused all of main because
engine/dist/rules@<sha>+bin:<hash>/ has a ':' in its name.
"""

import subprocess
from pathlib import Path

import pytest

from rescue_shared.versions import dist_dirname, rules_version

ROOT = Path(__file__).resolve().parents[2]
# Characters Windows does not allow in a file or folder name (':' opens an NTFS data stream).
WINDOWS_FORBIDDEN = set('<>:"|?*\\')


def tracked_paths() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.decode("utf-8").split("\0") if p]


def test_no_tracked_path_has_a_character_windows_cannot_store():
    bad = [
        p for p in tracked_paths() if WINDOWS_FORBIDDEN & set(p) or any(ord(c) < 32 for c in p)
    ]
    assert not bad, f"paths Windows cannot check out: {bad}"


def test_dist_dirname_keeps_the_version_but_drops_the_colon():
    version = rules_version("abcdef1", "0123456789abcdef")
    assert dist_dirname(version) == "rules@abcdef1+bin-0123456789abcdef"
    assert not WINDOWS_FORBIDDEN & set(dist_dirname(version))


@pytest.mark.parametrize("bad", ["../etc", "rules@abcdef1+bin:XYZ", "", "rules@abcdef1"])
def test_dist_dirname_refuses_anything_but_a_rules_version(bad):
    # the result is joined onto the dist folder, so it must never be a path that escapes it
    with pytest.raises(ValueError):
        dist_dirname(bad)
