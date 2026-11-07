"""Build an immutable engine artifact: engine/dist/<rules_version>/.

    python -m rescue_engine.build [--dist DIR] [--kind python|mercury]
    python -m rescue_engine.build [--dist DIR] --rebuild-from DIR

python (default): copies the fallback engine sources into the version directory.
mercury: builds engine/mercury with make (needs mmc and MERCURY_JSON_SRC) and copies the binary
to bin/mercury_engine in the version directory.
--rebuild-from: republishes an existing build's artifact (unstamped back to its compiled bytes)
with the current frozen config, without recompiling; used when only the config changes.
All write manifest.json (contracts/engine_manifest.schema.json). rules_sha is the git SHA of the
last commit touching the rule sources. artifact_hash is the sha256 (first 16 hex) over the copied
engine files as they are before stamping, i.e. with the placeholder version in them.

Frozen config (H12 extension, R15): the thresholds, hoax markers and street list
(contracts/fixtures/, see rescue_shared.frozen_config) are copied to config/ in the version
directory, their config_hash goes into the manifest, and
bin_hash = combined_bin_hash(artifact_hash, config_hash), so the rules_version changes whenever
the engine or any of those inputs changes.

Stamping (A2, docs/DECISIONS.md): the published copy then carries the real rules_version, so every
decision it emits names the build that made it. For python, the `RULES_VERSION = "<placeholder>"`
line of rules.py is rewritten. For mercury, the binary holds the placeholder in a fixed-width,
space-padded slot (rules.m `version_slot`), which is overwritten in place with the real version
padded the same way. Undoing the stamp (see `unstamped`) gives back the bytes artifact_hash covers.
An existing version directory is never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rescue_shared import frozen_config
from rescue_shared.versions import rules_version

ENGINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = ENGINE_DIR.parent.parent
SOURCE_DIR = ENGINE_DIR / "fallback"
DEFAULT_DIST = ENGINE_DIR.parent / "dist"
ENTRYPOINT = "python -m rescue_engine.fallback"
MERCURY_DIR = ENGINE_DIR.parent / "mercury"
MERCURY_BINARY = "mercury_engine"
MERCURY_ENTRYPOINT = f"bin/{MERCURY_BINARY}"

PLACEHOLDER_VERSION = "rules@0000000+bin:00000000"
VERSION_SLOT_WIDTH = 120  # rules.m version_slot; a rules_version is at most 115 characters
RULES_PY = "rules.py"


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


# --- version stamping -------------------------------------------------------------------------


def python_version_line(version: str) -> str:
    return f'RULES_VERSION = "{version}"'


def version_slot(version: str) -> bytes:
    """The bytes of the Mercury binary's version slot holding version."""
    if len(version) > VERSION_SLOT_WIDTH:
        raise RuntimeError(f"rules_version longer than {VERSION_SLOT_WIDTH} characters")
    return version.ljust(VERSION_SLOT_WIDTH).encode("ascii")


def _replace_once(data: bytes, old: bytes, new: bytes, what: str) -> bytes:
    count = data.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one {what} to stamp, found {count}")
    return data.replace(old, new)


def stamp_python(rules_py: Path, version: str) -> None:
    """Rewrite the RULES_VERSION placeholder line of a copied rules.py."""
    old = python_version_line(PLACEHOLDER_VERSION).encode()
    new = python_version_line(version).encode()
    rules_py.write_bytes(_replace_once(rules_py.read_bytes(), old, new, "RULES_VERSION line"))


def stamp_binary(binary: Path, version: str) -> None:
    """Overwrite the placeholder version slot of a copied Mercury binary (same length)."""
    data = _replace_once(
        binary.read_bytes(), version_slot(PLACEHOLDER_VERSION), version_slot(version),
        "version slot",
    )  # fmt: skip
    binary.write_bytes(data)


def unstamped(data: bytes, version: str, kind: str) -> bytes:
    """Undo a stamp: the bytes the manifest's artifact_hash was computed over."""
    if kind == "mercury":
        return _replace_once(data, version_slot(version), version_slot(PLACEHOLDER_VERSION),
                             "version slot")  # fmt: skip
    return _replace_once(
        data, python_version_line(version).encode(),
        python_version_line(PLACEHOLDER_VERSION).encode(), "RULES_VERSION line",
    )  # fmt: skip


# --- publishing -------------------------------------------------------------------------------


def _publish(
    dist: Path,
    sha: str,
    artifact_hash: str,
    kind: str,
    entrypoint: str,
    stage: Callable[[Path, str], None],
    config_dir: Path = frozen_config.FIXTURES_DIR,
) -> Path:
    """Fill a temp dir with stage(tmp, version) and the frozen config, add manifest.json, rename it
    to dist/<version>."""
    cfg_hash = frozen_config.config_hash_of(config_dir)
    bin_hash = frozen_config.combined_bin_hash(artifact_hash, cfg_hash)
    version = rules_version(sha, bin_hash)
    target = dist / version
    if target.exists():
        raise VersionExists(f"{target} already exists; versions are immutable")
    dist.mkdir(parents=True, exist_ok=True)
    tmp = dist / f".tmp-{bin_hash}"
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        tmp.mkdir()
        stage(tmp, version)
        (tmp / frozen_config.CONFIG_SUBDIR).mkdir()
        for name in frozen_config.CONFIG_FILES.values():
            shutil.copy2(config_dir / name, tmp / frozen_config.CONFIG_SUBDIR / name)
        if frozen_config.config_hash_of(tmp / frozen_config.CONFIG_SUBDIR) != cfg_hash:
            raise RuntimeError("config changed while building; try again")
        manifest = {
            "rules_version": version,
            "rules_sha": sha,
            "bin_hash": bin_hash,
            "artifact_hash": artifact_hash,
            "config_hash": cfg_hash,
            "engine_kind": kind,
            "entrypoint": entrypoint,
            "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        tmp.rename(target)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    return target


def build(
    dist: Path = DEFAULT_DIST,
    source: Path = SOURCE_DIR,
    rules_sha: str | None = None,
    config_dir: Path = frozen_config.FIXTURES_DIR,
) -> Path:
    files = source_files(source)
    artifact_hash = hash_files(files, source)[:16]
    sha = rules_sha or git_sha(source)

    def stage(tmp: Path, version: str) -> None:
        package = tmp / "rescue_engine" / "fallback"
        for f in files:
            dest = package / f.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
        (tmp / "rescue_engine" / "__init__.py").write_text((ENGINE_DIR / "__init__.py").read_text())
        if (package / RULES_PY).exists():
            stamp_python(package / RULES_PY, version)

    return _publish(dist, sha, artifact_hash, "python", ENTRYPOINT, stage, config_dir)


def compile_mercury(build_dir: Path, source: Path = MERCURY_DIR) -> Path:
    """Run engine/mercury's Makefile into build_dir; return the binary's path."""
    proc = subprocess.run(
        ["make", "-C", str(source), f"BUILD_DIR={build_dir.resolve()}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mercury build failed:\n{proc.stdout}\n{proc.stderr}")
    return build_dir / MERCURY_BINARY


def _publish_binary(
    dist: Path, sha: str, binary: Path, config_dir: Path = frozen_config.FIXTURES_DIR
) -> Path:
    """Publish an unstamped (as compiled) Mercury binary as dist/<version>/bin/mercury_engine."""
    artifact_hash = hash_files([binary], binary.parent)[:16]

    def stage(tmp: Path, version: str) -> None:
        (tmp / "bin").mkdir()
        shutil.copy2(binary, tmp / MERCURY_ENTRYPOINT)
        stamp_binary(tmp / MERCURY_ENTRYPOINT, version)

    return _publish(dist, sha, artifact_hash, "mercury", MERCURY_ENTRYPOINT, stage, config_dir)


def build_mercury(
    dist: Path = DEFAULT_DIST,
    source: Path = MERCURY_DIR,
    rules_sha: str | None = None,
    config_dir: Path = frozen_config.FIXTURES_DIR,
) -> Path:
    """Compile the Mercury engine and publish it as dist/<rules_version>/bin/mercury_engine."""
    sha = rules_sha or git_sha(source)
    with tempfile.TemporaryDirectory(prefix="mercury-build-") as work:
        binary = compile_mercury(Path(work), source)
        return _publish_binary(dist, sha, binary, config_dir)


def artifact_hash_of(manifest: dict) -> str:
    """The hash of the engine artifact alone. Builds made before the config freeze have no
    artifact_hash: their bin_hash covered only the artifact."""
    return manifest.get("artifact_hash") or manifest["bin_hash"]


def rebuild(
    existing: Path, dist: Path = DEFAULT_DIST, config_dir: Path = frozen_config.FIXTURES_DIR
) -> Path:
    """Republish an existing Mercury build's binary (same compiled bytes, same rules_sha) with the
    current frozen config. Checks the unstamped binary against the old manifest first."""
    manifest = json.loads((existing / "manifest.json").read_text())
    if manifest["engine_kind"] != "mercury":
        raise RuntimeError("--rebuild-from supports mercury builds only")
    data = unstamped((existing / manifest["entrypoint"]).read_bytes(), manifest["rules_version"],
                     "mercury")  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="mercury-rebuild-") as work:
        binary = Path(work) / MERCURY_BINARY
        binary.write_bytes(data)
        binary.chmod(0o755)
        if hash_files([binary], binary.parent)[:16] != artifact_hash_of(manifest):
            raise RuntimeError(f"{existing}: binary does not match its manifest")
        return _publish_binary(dist, manifest["rules_sha"], binary, config_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rescue_engine.build")
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--kind", choices=["python", "mercury"], default="python")
    parser.add_argument("--rebuild-from", type=Path, dest="rebuild_from")
    args = parser.parse_args(argv)
    try:
        if args.rebuild_from:
            target = rebuild(args.rebuild_from, args.dist)
        elif args.kind == "mercury":
            target = build_mercury(args.dist)
        else:
            target = build(args.dist)
    except (VersionExists, RuntimeError, OSError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
