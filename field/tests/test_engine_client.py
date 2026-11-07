import copy
import json
import sys
from pathlib import Path

import pytest

from rescue_field.engine_client import EngineError, decide, decide_batch

FAKE = str(Path(__file__).with_name("fake_engine.py"))
EXAMPLE = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts/examples/engine_input.valid.json").read_text()
)


def cmd(mode: str = "ok") -> list[str]:
    return [sys.executable, FAKE, mode]


def test_decide_returns_validated_output():
    assert decide(EXAMPLE, cmd=cmd())["rule_id"] == "R-P2-01"


def test_reads_engine_cmd_env(monkeypatch):
    monkeypatch.setenv("ENGINE_CMD", f"{sys.executable} {FAKE} ok")
    assert decide(EXAMPLE)["outcome"] == "prioritized"


def test_missing_engine_cmd(monkeypatch):
    monkeypatch.delenv("ENGINE_CMD", raising=False)
    with pytest.raises(EngineError):
        decide(EXAMPLE)


def test_nonzero_exit_attaches_stderr():
    with pytest.raises(EngineError) as ei:
        decide(EXAMPLE, cmd=cmd("fail"))
    assert "engine exploded" in ei.value.stderr


def test_timeout():
    with pytest.raises(EngineError, match="timed out"):
        decide(EXAMPLE, cmd=cmd("hang"), timeout=0.5)


def test_garbage_and_schema_invalid_output():
    with pytest.raises(EngineError, match="not JSON"):
        decide(EXAMPLE, cmd=cmd("garbage"))
    with pytest.raises(EngineError, match="schema"):
        decide(EXAMPLE, cmd=cmd("invalid"))


def test_batch_preserves_order():
    inputs = []
    for n in (3, 1, 2):
        item = copy.deepcopy(EXAMPLE)
        item["extracted"]["people_count"]["value"] = n
        inputs.append(item)
    outs = decide_batch(inputs, cmd=cmd("echo"))
    assert [o["rule_id"] for o in outs] == ["R-P2-3", "R-P2-1", "R-P2-2"]
    assert decide_batch([], cmd=cmd()) == []
