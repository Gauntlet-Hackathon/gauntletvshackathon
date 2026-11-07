"""Static checks on field/fly.toml (hosted field demo)."""

import tomllib
from pathlib import Path

from rescue_field.api import app

ROOT = Path(__file__).resolve().parents[2]
CFG = tomllib.loads((ROOT / "field" / "fly.toml").read_text())


def test_parses_with_placeholder_name():
    assert CFG["app"] == "rescue-field-demo"


def test_port_matches_internal_port():
    assert str(CFG["http_service"]["internal_port"]) == CFG["env"]["PORT"]


def test_demo_mode_on():
    assert CFG["env"]["DEMO_MODE"] == "1"


def test_mount_matches_data_dir():
    assert CFG["mounts"]["destination"] == CFG["env"]["FIELD_DATA_DIR"]


def test_check_path_is_get_route():
    for check in CFG["http_service"]["checks"]:
        assert check["method"] == "GET"
        assert "get" in app.openapi()["paths"][check["path"]]


def test_no_secrets_in_env():
    for key in CFG["env"]:
        assert "TOKEN" not in key.upper() and "SECRET" not in key.upper()


def test_dockerfile_is_relative_to_fly_toml():
    # flyctl resolves build.dockerfile relative to the fly.toml folder, not the repo root.
    assert CFG["build"]["dockerfile"] == "Dockerfile"
    assert (ROOT / "field" / CFG["build"]["dockerfile"]).is_file()


def test_vm_has_enough_memory_for_baked_model():
    vm = CFG["vm"][0]
    assert vm["memory"] == "4gb"
    assert vm["cpus"] == 2


def test_health_check_grace_covers_model_load():
    for check in CFG["http_service"]["checks"]:
        assert int(check["grace_period"].rstrip("s")) >= 60


def test_model_pin_is_org_repo_at_full_commit_sha():
    args = CFG["build"]["args"]
    assert args["HF_REPO"].count("/") == 1
    rev = args["HF_REVISION"]
    assert len(rev) == 40 and all(c in "0123456789abcdef" for c in rev)


def test_health_check_uses_lock_free_healthz():
    """Review M-04/O-02: /sync/status waits behind the intake lock (board renders, bulk
    uploads), so the Fly check uses /healthz, which never takes it."""
    assert [c["path"] for c in CFG["http_service"]["checks"]] == ["/healthz"]
