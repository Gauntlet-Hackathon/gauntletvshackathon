import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _field_block() -> str:
    text = COMPOSE.read_text()
    m = re.search(r"^  field:\n(.*?)(?=^\S|^  \S)", text, re.S | re.M)
    assert m, "field service not found"
    return m.group(1)


def test_field_service_enables_demo_mode():
    block = _field_block()
    assert 'DEMO_MODE: "1"' in block
    assert "DEVICE_ID: laptop-dev-01" in block
    assert "CLOUD_URL: http://cloud:4000" in block


def test_field_build_passes_hf_args():
    block = _field_block()
    assert "HF_REPO: ${HF_REPO:-}" in block
    assert "HF_REVISION: ${HF_REVISION:-}" in block
    assert "HF_TOKEN" not in block
