"""Build an immutable engine artifact: engine/dist/<rules_version>/.

    python -m rescue_engine.build [--dist DIR]

Copies the fallback engine sources into the version directory and writes manifest.json
(contracts/engine_manifest.schema.json). rules_sha is the git SHA of the last commit touching the
rule sources; bin_hash is the sha256 (first 16 hex) over the copied files. An existing version
directory is never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from rescue_shared.versions import rules_version

ENGINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = ENGINE_DIR.parent.parent
SOURCE_DIR = ENGINE_DIR / "fallback"
DEFAULT_DIST = ENGINE_DIR.parent / "dist"
ENTRYPOINT = "python -m rescue_engine.fallback"


class VersionExists(Exception):
    pass


def source_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def hash_files(files: list[Path], root: Path) -> str:
    h = hashlib.sha256()
    for f in files:
        h.update(f.relative_to(root).as_posix().encode() + b"\0")
        h.update(f.read_bytes() + b"\0")
    return h.hexdigest()


def git_sha(path: Path, repo: Path = REPO_ROOT) -> str:
    for args in (["log", "-1", "--format=%H", "--", str(path)], ["rev-parse", "HEAD"]):
        try:
            out = subprocess.run(
                ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        if out:
            return out
    raise RuntimeError("cannot determine git SHA of the rule sources")


def build(
    dist: Path = DEFAULT_DIST, source: Path = SOURCE_DIR, rules_sha: str | None = None
) -> Path:
    files = source_files(source)
    bin_hash = hash_files(files, source)[:16]
    sha = rules_sha or git_sha(source)
    version = rules_version(sha, bin_hash)
    target = dist / version
    if target.exists():
        raise VersionExists(f"{target} already exists; versions are immutable")
    dist.mkdir(parents=True, exist_ok=True)
    tmp = dist / f".tmp-{bin_hash}"
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        for f in files:
            dest = tmp / "rescue_engine" / "fallback" / f.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
        (tmp / "rescue_engine" / "__init__.py").write_text((ENGINE_DIR / "__init__.py").read_text())
        manifest = {
            "rules_version": version,
            "rules_sha": sha,
            "bin_hash": bin_hash,
            "engine_kind": "python",
            "entrypoint": ENTRYPOINT,
            "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        tmp.rename(target)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rescue_engine.build")
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    args = parser.parse_args(argv)
    try:
        target = build(args.dist)
    except VersionExists as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
