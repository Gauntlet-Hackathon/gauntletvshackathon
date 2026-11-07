import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from engine.fallback import decide, decide_one
from engine.fallback.rules import Q_LOCATION, RULES_VERSION

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    json.loads(line)
    for line in (ROOT / "contracts/fixtures/cases.jsonl").read_text().splitlines()
]
STEP_1_2 = [c for c in CASES if c["expected"]["rule_id"] in
            {"R-NAR-01", "R-NAR-02", "R-ABS-REQ", "R-ABS-LOC"}]
BASE = next(c for c in CASES if c["case_id"] == "C01")


def inp(case):
    return {"extracted": case["extracted"], "context": case["context"]}


def variant(**changes):
    case = copy.deepcopy(BASE)
    for path, value in changes.items():
        target = case["extracted"] if not path.startswith("ctx_") else case["context"]
        keys = path.removeprefix("ctx_").split("__")
        for k in keys[:-1]:
            target = target[k]
        target[keys[-1]] = value
    return inp(case)


def test_fixture_coverage():
    assert {c["case_id"] for c in STEP_1_2} == {"C10", "C11", "C12", "C13", "C14", "C15"}


@pytest.mark.parametrize("case", STEP_1_2, ids=lambda c: c["case_id"])
def test_fixture_cases(case):
    out = decide_one(inp(case))
    expected = {**case["expected"], "rules_version": RULES_VERSION}
    assert out == expected


def test_every_output_has_rule_id_and_version():
    for case in STEP_1_2:
        out = decide_one(inp(case))
        assert re.fullmatch(r"R-[A-Z0-9-]+", out["rule_id"])
        assert re.fullmatch(r"rules@[0-9a-f]{7,40}\+bin:[0-9a-f]{8,64}", out["rules_version"])


def test_request_confidence_boundary():
    not_req = variant(is_request__confidence=0.6, is_request__value=False)
    assert decide_one(not_req)["rule_id"] == "R-NAR-01"
    out = decide_one(variant(is_request__confidence=0.59))
    assert (out["outcome"], out["abstain_reason"]) == ("abstain", "low_confidence_request")


def test_location_boundary_and_question():
    out = decide_one(variant(location__confidence=0.59))
    assert out["abstain_reason"] == "no_usable_location"
    assert out["follow_up_question"] == Q_LOCATION
    with pytest.raises(NotImplementedError):  # 0.6 passes steps 1-2
        decide_one(variant(location__confidence=0.6))


def test_landmark_needs_street_list_hit():
    assert decide_one(variant(ctx_street_list_hits=[]))["rule_id"] == "R-ABS-LOC"
    with pytest.raises(NotImplementedError):
        decide_one(variant(location__type="address", ctx_street_list_hits=[]))


def test_list_input_returns_list():
    outs = decide([inp(c) for c in STEP_1_2])
    assert [o["rule_id"] for o in outs] == [c["expected"]["rule_id"] for c in STEP_1_2]


def run_cli(payload):
    return subprocess.run(
        [sys.executable, "-m", "engine.fallback"],
        input=json.dumps(payload), capture_output=True, text=True, cwd=ROOT,
    )


def test_cli_object_and_list():
    case = STEP_1_2[0]
    one = run_cli(inp(case))
    assert one.returncode == 0
    assert json.loads(one.stdout)["rule_id"] == case["expected"]["rule_id"]
    many = run_cli([inp(c) for c in STEP_1_2])
    assert len(json.loads(many.stdout)) == len(STEP_1_2)


def test_cli_bad_input_fails():
    proc = subprocess.run([sys.executable, "-m", "engine.fallback"], input="nope",
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 1
