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


def test_hash_changes_with_sources(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n")
    v1 = b.build(tmp_path / "d", source=src, rules_sha="abcdef1").name
    (src / "a.py").write_text("x = 2\n")
    v2 = b.build(tmp_path / "d", source=src, rules_sha="abcdef1").name
    assert v1 != v2
