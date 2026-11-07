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


def test_dockerfile_exists_from_repo_root():
    assert (ROOT / CFG["build"]["dockerfile"]).is_file()
