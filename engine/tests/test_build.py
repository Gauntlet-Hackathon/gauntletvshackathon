import json

import pytest

from rescue_engine import build as b
from rescue_shared.contracts_io import validator_for
from rescue_shared.versions import is_rules_version, parse_rules_version


def test_build_writes_valid_manifest(tmp_path):
    target = b.build(tmp_path)
    manifest = json.loads((target / "manifest.json").read_text())
    validator_for("engine_manifest.schema.json").validate(manifest)
    assert target.name == manifest["rules_version"]
    assert is_rules_version(manifest["rules_version"])
    assert parse_rules_version(manifest["rules_version"]) == (
        manifest["rules_sha"],
        manifest["bin_hash"],
    )
    assert (target / "rescue_engine" / "fallback" / "engine.py").exists()


def test_refuses_overwrite(tmp_path):
    target = b.build(tmp_path)
    before = (target / "manifest.json").read_text()
    with pytest.raises(b.VersionExists):
        b.build(tmp_path)
    assert (target / "manifest.json").read_text() == before


def test_cli_refuses_second_build(tmp_path, capsys):
    assert b.main(["--dist", str(tmp_path)]) == 0
    assert b.main(["--dist", str(tmp_path)]) == 1
    assert "already exists" in capsys.readouterr().err


def _engine_input():
    case = json.loads(
        (b.REPO_ROOT / "contracts/fixtures/cases.jsonl").read_text().splitlines()[0]
    )
    return {"extracted": case["extracted"], "context": case["context"]}


def test_built_engine_emits_its_real_rules_version(tmp_path):
    """A2: the copy in dist/ is stamped, so its decisions name the build that made them."""
    from rescue_engine.fallback.rules import RULES_VERSION
    from rescue_engine.replay_diff import run_engine

    target = b.build(tmp_path)
    manifest = json.loads((target / "manifest.json").read_text())
    [out] = run_engine(target, [_engine_input()])
    assert out["rules_version"] == manifest["rules_version"] != RULES_VERSION
    validator_for("engine_output.schema.json").validate(out)
    # The source tree keeps the placeholder.
    assert b.python_version_line(b.PLACEHOLDER_VERSION) in (b.SOURCE_DIR / "rules.py").read_text()


def test_bin_hash_covers_the_unstamped_sources(tmp_path):
    target = b.build(tmp_path)
    manifest = json.loads((target / "manifest.json").read_text())
    package = target / "rescue_engine" / "fallback"
    stamped = (package / "rules.py").read_bytes()
    assert b.python_version_line(manifest["rules_version"]).encode() in stamped
    (package / "rules.py").write_bytes(
        b.unstamped(stamped, manifest["rules_version"], "python")
    )
    assert b.hash_files(b.source_files(package), package)[:16] == manifest["bin_hash"]


def test_stamp_needs_exactly_one_placeholder(tmp_path):
    rules_py = tmp_path / "rules.py"
    rules_py.write_text("x = 1\n")
    with pytest.raises(RuntimeError, match="found 0"):
        b.stamp_python(rules_py, "rules@abcdef1+bin:0123456789abcdef")
    line = b.python_version_line(b.PLACEHOLDER_VERSION)
    rules_py.write_text(f"{line}\n{line}\n")
    with pytest.raises(RuntimeError, match="found 2"):
        b.stamp_python(rules_py, "rules@abcdef1+bin:0123456789abcdef")


def test_binary_stamp_keeps_length(tmp_path):
    binary = tmp_path / "engine"
    data = b"\x00head" + b.version_slot(b.PLACEHOLDER_VERSION) + b"tail\x00"
    binary.write_bytes(data)
    version = "rules@" + "a" * 40 + "+bin:" + "b" * 64  # the longest valid version
    b.stamp_binary(binary, version)
    stamped = binary.read_bytes()
    assert len(stamped) == len(data)
    assert b.version_slot(version) in stamped
    assert b.unstamped(stamped, version, "mercury") == data


def test_hash_changes_with_sources(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n")
    v1 = b.build(tmp_path / "d", source=src, rules_sha="abcdef1").name
    (src / "a.py").write_text("x = 2\n")
    v2 = b.build(tmp_path / "d", source=src, rules_sha="abcdef1").name
    assert v1 != v2
