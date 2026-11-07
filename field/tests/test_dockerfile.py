"""Static checks that field/Dockerfile and the root .dockerignore keep the F15 contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "field" / "Dockerfile").read_text()
DOCKERIGNORE = (ROOT / ".dockerignore").read_text().split()


def test_base_image_and_constraints():
    assert "FROM python:3.12-slim-trixie" in DOCKERFILE
    assert "-c constraints.txt" in DOCKERFILE


def test_pythonpath_and_runtime_env():
    assert "PYTHONPATH=/app/shared:/app/field:/app/engine" in DOCKERFILE
    assert "ENGINE_CMD=/app/engine/dist/${ENGINE_BUILD}/bin/mercury_engine" in DOCKERFILE
    assert "RULES_VERSION=${ENGINE_BUILD}" in DOCKERFILE
    names = ("DEVICE_ID", "DEVICE_TOKEN", "CLOUD_URL", "FIELD_DATA_DIR=/data", "DEMO_MODE", "PORT")
    for name in names:
        assert name in DOCKERFILE


def test_runs_uvicorn_on_port():
    assert "uvicorn rescue_field.api:app --host 0.0.0.0 --port ${PORT:-8000}" in DOCKERFILE


def test_copies_engine_and_contracts():
    for d in ("shared", "field", "contracts"):
        assert f"COPY {d} {d}" in DOCKERFILE


def test_dockerignore_excludes():
    entries = (".git", ".venv", "__pycache__", "cloud/_build", "cloud/deps")
    entries += ("node_modules", "*.db", ".env")
    for entry in entries:
        assert entry in DOCKERIGNORE


def test_engine_build_arg_matches_dist_folder():
    folders = [p.name for p in (ROOT / "engine" / "dist").iterdir() if p.is_dir()]
    assert len(folders) == 1
    assert f"ARG ENGINE_BUILD={folders[0]}\n" in DOCKERFILE


def test_copies_frozen_build_and_checks_manifest():
    assert "COPY engine/dist/${ENGINE_BUILD}/ /app/engine/dist/${ENGINE_BUILD}/" in DOCKERFILE
    assert "test -f /app/engine/dist/${ENGINE_BUILD}/manifest.json" in DOCKERFILE
    assert "grep -q '\"engine_kind\": \"mercury\"'" in DOCKERFILE
    assert "COPY engine engine" not in DOCKERFILE


def test_dockerignore_keeps_engine_dist():
    assert not any(e.rstrip("/").startswith("engine") for e in DOCKERIGNORE)
