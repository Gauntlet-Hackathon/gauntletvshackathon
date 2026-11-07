import json

import pytest

from rescue_engine import build as b
from rescue_shared import frozen_config
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
    assert b.hash_files(b.source_files(package), package)[:16] == manifest["artifact_hash"]
    assert manifest["bin_hash"] == frozen_config.combined_bin_hash(
        manifest["artifact_hash"], manifest["config_hash"]
    )


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


# --- frozen config (H12 extension, R15) ------------------------------------------------------


def _config_dir(tmp_path, **changes):
    d = tmp_path / "cfg"
    d.mkdir()
    for name in frozen_config.CONFIG_FILES.values():
        (d / name).write_bytes((frozen_config.FIXTURES_DIR / name).read_bytes())
    for name, text in changes.items():
        (d / name).write_text(text)
    return d


def test_build_copies_and_hashes_the_frozen_config(tmp_path):
    target = b.build(tmp_path / "dist")
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["config_hash"] == frozen_config.config_hash_of()
    assert frozen_config.config_hash_of(target / "config") == manifest["config_hash"]
    for name in frozen_config.CONFIG_FILES.values():
        assert (target / "config" / name).read_bytes() == (
            frozen_config.FIXTURES_DIR / name
        ).read_bytes()


@pytest.mark.parametrize(
    "name,text",
    [
        ("thresholds.json", json.dumps({**frozen_config.load_config()["thresholds"],
                                        "hazard_firm_min": 0.7})),
        ("hoax_markers.txt", "prank\n"),
        ("streets.txt", "Main Street\n"),
    ],
)
def test_any_config_change_changes_the_rules_version(tmp_path, name, text):
    base = b.build(tmp_path / "a", rules_sha="abcdef1")
    changed = b.build(tmp_path / "b", rules_sha="abcdef1",
                      config_dir=_config_dir(tmp_path, **{name: text}))
    mb = json.loads((base / "manifest.json").read_text())
    mc = json.loads((changed / "manifest.json").read_text())
    assert mb["artifact_hash"] == mc["artifact_hash"]
    assert mb["config_hash"] != mc["config_hash"]
    assert base.name != changed.name


def test_whitespace_and_comments_do_not_change_the_config_hash(tmp_path):
    markers = (frozen_config.FIXTURES_DIR / "hoax_markers.txt").read_text()
    d = _config_dir(tmp_path, **{"hoax_markers.txt": "# comment\n\n" + markers + "\n\n"})
    assert frozen_config.config_hash_of(d) == frozen_config.config_hash_of()


def test_committed_build_is_frozen_with_the_current_config():
    """The demo build in engine/dist names the thresholds, hoax markers and street list in
    contracts/fixtures/. Editing any of them without a new build fails here (DECISIONS H12)."""
    [build] = [p for p in (b.REPO_ROOT / "engine" / "dist").iterdir() if p.is_dir()]
    manifest = json.loads((build / "manifest.json").read_text())
    validator_for("engine_manifest.schema.json").validate(manifest)
    assert build.name == manifest["rules_version"]
    assert manifest["config_hash"] == frozen_config.config_hash_of(), (
        "contracts/fixtures/{thresholds.json,hoax_markers.txt,streets.txt} changed after the "
        "demo freeze; restore them or make a new build"
    )
    assert frozen_config.config_hash_of(build / "config") == manifest["config_hash"]
    assert manifest["bin_hash"] == frozen_config.combined_bin_hash(
        manifest["artifact_hash"], manifest["config_hash"]
    )
    binary = build / manifest["entrypoint"]
    raw = b.unstamped(binary.read_bytes(), manifest["rules_version"], manifest["engine_kind"])
    assert b.hashlib.sha256(binary.name.encode() + b"\0" + raw + b"\0").hexdigest()[:16] == (
        manifest["artifact_hash"]
    )


def test_thresholds_file_matches_the_engine_defaults():
    from rescue_engine.fallback.rules import DEFAULT_THRESHOLDS

    assert frozen_config.load_config()["thresholds"] == DEFAULT_THRESHOLDS


def test_rebuild_from_republishes_the_same_artifact_with_new_config(tmp_path):
    """--rebuild-from on a fake mercury build: same artifact bytes, new config, new version."""
    old_dist = tmp_path / "old"
    binary_src = tmp_path / "src" / b.MERCURY_BINARY
    binary_src.parent.mkdir()
    binary_src.write_bytes(b"ELF..." + b.version_slot(b.PLACEHOLDER_VERSION) + b"...end")
    old = b._publish_binary(old_dist, "abcdef1", binary_src)
    new_cfg = _config_dir(tmp_path, **{"streets.txt": "Main Street\n"})
    new = b.rebuild(old, tmp_path / "new", config_dir=new_cfg)
    mo = json.loads((old / "manifest.json").read_text())
    mn = json.loads((new / "manifest.json").read_text())
    assert mn["artifact_hash"] == mo["artifact_hash"] and mn["rules_sha"] == "abcdef1"
    assert mn["config_hash"] == frozen_config.config_hash_of(new_cfg) != mo["config_hash"]
    data = (new / "bin" / b.MERCURY_BINARY).read_bytes()
    assert b.version_slot(mn["rules_version"]) in data


def test_rebuild_from_refuses_a_tampered_binary(tmp_path):
    src = tmp_path / "src" / b.MERCURY_BINARY
    src.parent.mkdir()
    src.write_bytes(b"ELF" + b.version_slot(b.PLACEHOLDER_VERSION))
    old = b._publish_binary(tmp_path / "old", "abcdef1", src)
    target = old / "bin" / b.MERCURY_BINARY
    target.write_bytes(target.read_bytes() + b"x")
    with pytest.raises(RuntimeError, match="does not match"):
        b.rebuild(old, tmp_path / "new")
