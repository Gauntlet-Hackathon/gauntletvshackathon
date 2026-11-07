"""E7: the Mercury engine (engine/mercury) gives the same outputs as the Python fallback.

The binary is built once per session through rescue_engine.build.build_mercury, so these tests
also cover registering the Mercury build output in engine/dist/. Outputs are compared as parsed
JSON (docs/DECISIONS.md, W0-5). Needs `mmc` and MERCURY_JSON_SRC; without them the tests skip
locally, but fail in CI (CI installs Mercury in the python job).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rescue_engine import build as b
from rescue_engine.fallback import decide, decide_one, rules
from rescue_shared import frozen_config
from rescue_shared.contracts_io import validator_for
from rescue_shared.versions import dist_dirname, parse_rules_version

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    json.loads(line)
    for line in (ROOT / "contracts/fixtures/cases.jsonl").read_text().splitlines()
    if line.strip()
]


def _have_mercury() -> bool:
    return shutil.which("mmc") is not None and bool(os.environ.get("MERCURY_JSON_SRC"))


@pytest.fixture(scope="session")
def dist_dir(tmp_path_factory) -> Path:
    if not _have_mercury():
        if os.environ.get("CI"):
            pytest.fail("CI must provide mmc and MERCURY_JSON_SRC (install-mercury action)")
        pytest.skip("Mercury toolchain not installed (mmc / MERCURY_JSON_SRC)")
    return b.build_mercury(tmp_path_factory.mktemp("dist"))


@pytest.fixture(scope="session")
def binary(dist_dir: Path) -> Path:
    manifest = json.loads((dist_dir / "manifest.json").read_text())
    return dist_dir / manifest["entrypoint"]


def run(binary: Path, stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary)], input=stdin, capture_output=True, text=True, timeout=120, check=False
    )


def mercury(binary: Path, payload):
    """Run the built binary. It is stamped with its real rules_version (A2): check that, then
    put back the source placeholder so outputs compare equal to the in-process Python engine."""
    proc = run(binary, json.dumps(payload))
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    stamped = json.loads((binary.parent.parent / "manifest.json").read_text())["rules_version"]
    out = json.loads(proc.stdout)
    for item in out if isinstance(out, list) else [out]:
        assert item["rules_version"] == stamped
        item["rules_version"] = rules.RULES_VERSION
    return out


def engine_input(case: dict) -> dict:
    return {"extracted": case["extracted"], "context": case["context"]}


def load_e4_grid() -> list[dict]:
    """The E4 property grid (tests are not importable as modules under importlib mode)."""
    spec = importlib.util.spec_from_file_location(
        "e4_properties", Path(__file__).with_name("test_properties.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GRID


# --- build registration ---------------------------------------------------------------------


def test_build_registers_mercury_artifact(dist_dir: Path, binary: Path, tmp_path: Path):
    manifest = json.loads((dist_dir / "manifest.json").read_text())
    validator_for("engine_manifest.schema.json").validate(manifest)
    assert manifest["engine_kind"] == "mercury"
    assert manifest["entrypoint"] == b.MERCURY_ENTRYPOINT
    assert dist_dir.name == dist_dirname(manifest["rules_version"])  # WINPATH-1: no ':'
    sha, bin_hash = parse_rules_version(manifest["rules_version"])
    assert (sha, bin_hash) == (manifest["rules_sha"], manifest["bin_hash"])
    # artifact_hash covers the binary as compiled, before the real version was stamped in (A2);
    # bin_hash covers it together with the frozen config (H12 / R15).
    original = tmp_path / binary.name
    original.write_bytes(b.unstamped(binary.read_bytes(), manifest["rules_version"], "mercury"))
    assert b.hash_files([original], tmp_path)[:16] == manifest["artifact_hash"]
    assert bin_hash == frozen_config.combined_bin_hash(
        manifest["artifact_hash"], manifest["config_hash"]
    )
    assert os.access(binary, os.X_OK)


def test_built_binary_emits_its_real_rules_version(dist_dir: Path, binary: Path):
    manifest = json.loads((dist_dir / "manifest.json").read_text())
    proc = run(binary, json.dumps(engine_input(CASES[0])))
    assert proc.returncode == 0, proc.stderr
    version = json.loads(proc.stdout)["rules_version"]
    assert version == manifest["rules_version"] != rules.RULES_VERSION
    assert parse_rules_version(version)  # a well-formed version, no padding left


def test_source_binary_emits_the_placeholder(binary: Path, tmp_path: Path):
    compiled = b.compile_mercury(tmp_path / "out")
    proc = run(compiled, json.dumps(engine_input(CASES[0])))
    assert json.loads(proc.stdout)["rules_version"] == rules.RULES_VERSION


def test_mercury_build_refuses_overwrite(dist_dir: Path, binary: Path):
    # The build is reproducible, so a second build maps to the same immutable version.
    with pytest.raises(b.VersionExists):
        b.build_mercury(dist_dir.parent)


# --- parity -----------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_fixture_parity(binary: Path, case: dict):
    out = mercury(binary, engine_input(case))
    assert out == decide_one(engine_input(case)) == case["expected"]


def test_fixture_batch_parity(binary: Path):
    inputs = [engine_input(c) for c in CASES]
    assert mercury(binary, inputs) == decide(inputs)


def test_e4_grid_parity(binary: Path):
    grid = load_e4_grid()
    assert len(grid) >= 2000
    validator = validator_for("engine_output.schema.json")
    outs = mercury(binary, grid)
    assert len(outs) == len(grid)
    for item, out in zip(grid, outs, strict=True):
        validator.validate(out)
        assert out == decide_one(item), item


KINDS = ["rescue", "evacuation", "welfare_check", "supply", "other"]


def _random_input(rng: random.Random) -> dict:
    """Schema-valid inputs that also reach steps 4-5 and non-default thresholds."""
    conf = lambda: rng.choice([0.0, 0.1, 0.25, 0.3, 0.5, 0.55, 0.6, 0.75, 0.8, 0.85, 1.0])  # noqa: E731
    hazards = ["rising_water", "on_roof", "injury", "medical_device_no_power", "fire", "trapped"]
    vulnerable = ["child", "elderly", "disabled", "medical", "pregnant"]
    candidates = sorted(
        (
            {
                "request_id": f"01JCB00000000000000000C{n:03d}",
                "received_at": f"2026-11-07T0{rng.randint(0, 3)}:00:00Z",
                "people_count": rng.choice([None, 1, 2, 3]),
                "contact_match": rng.random() < 0.5,
                "priority_tier": rng.choice([None, "P1", "P2", "P3"]),
            }
            for n in range(rng.choice([0, 0, 1, 2, 3]))
        ),
        key=lambda c: c["received_at"],
    )
    thresholds = {
        "request_confidence_min": conf(), "stale_confidence_min": conf(),
        "hoax_marker_min": rng.randint(1, 3), "hoax_request_confidence_max": conf(),
        "location_confidence_min": conf(), "hazard_firm_min": 0.75,
        "hazard_uncertain_min": 0.5, "hazard_weak_min": 0.25,
        "need_hours_over": rng.choice([0, 12, 24, 48]), "duplicate_window_hours": 12,
    }
    if rng.random() < 0.3:
        thresholds["hazard_firm_min"], thresholds["hazard_uncertain_min"], \
            thresholds["hazard_weak_min"] = sorted((conf(), conf(), conf()), reverse=True)
    return {
        "extracted": {
            "location": {
                "text": rng.choice(["x", "x", "x", None, "", " \t"]),
                "type": rng.choice(["landmark", "address", "coords", "none"]),
                "confidence": conf(),
            },
            "people_count": {"value": rng.choice([None, 1, 2, 3]), "confidence": conf()},
            "vulnerable": [
                {"kind": k, "confidence": conf()} for k in rng.sample(vulnerable, rng.randint(0, 2))
            ],
            "hazards": [
                {"kind": k, "confidence": conf()} for k in rng.sample(hazards, rng.randint(0, 3))
            ],
            "time_ref": {"text": None, "is_stale_post": rng.random() < 0.2, "confidence": conf()},
            "is_request": {"value": rng.random() < 0.9, "confidence": conf()},
            "spans": [],
            "needs": [
                {"kind": k, "hours_without": rng.choice([None, 2, 12, 24, 30, 72])}
                for k in rng.sample(["food", "water", "meds"], rng.randint(0, 2))
            ],
            "request_kind": rng.choice(KINDS),
        },
        "context": {
            "duplicate_candidates": candidates,
            "street_list_hits": rng.choice([[], ["Main Street"]]),
            "hoax_marker_hits": rng.choice([0, 0, 1, 2, 3]),
            "thresholds": thresholds,
        },
    }


def test_random_inputs_parity(binary: Path):
    rng = random.Random(66)
    inputs = [_random_input(rng) for _ in range(3000)]
    validator = validator_for("engine_input.schema.json")
    for item in inputs[:200]:
        validator.validate(item)
    outs = mercury(binary, inputs)
    expected = decide(inputs)
    mismatches = [(i, o, e) for i, o, e in zip(inputs, outs, expected, strict=True) if o != e]
    assert not mismatches, mismatches[:3]
    assert {o["outcome"] for o in outs} == {"prioritized", "abstain", "not_a_request", "duplicate"}
    # The 2026-11-07 review paths are all reached (provisional tiers, R2/R3/R5/R9/R13).
    abstains = [o for o in outs if o["outcome"] == "abstain"]
    assert {o["provisional_tier"] for o in abstains} == {"P1", "P2", "P3"}
    questions = {(o["outcome"], o["follow_up_question"]) for o in outs}
    assert ("prioritized", rules.Q_HAZARD) in questions
    assert ("prioritized", rules.Q_HOAX) in questions
    assert ("duplicate", rules.Q_CONFIRM) in questions
    assert any(q == rules.Q_HAZARD_VULNERABLE for _, q in questions)
    assert any(o["rule_id"] == "R-ABS-REQ" and o["provisional_tier"] == "P1" for o in abstains)


def load_review_inputs() -> list[dict]:
    """Every engine_input built at module level in test_review_fixes.py (BAD_ENUMS excluded)."""
    spec = importlib.util.spec_from_file_location(
        "review_fixes", Path(__file__).with_name("test_review_fixes.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    m = module
    inputs = [m.inp(cid) for cid in m.CASES]
    inputs += [
        m.signals("C14", hazards=[("injury", 0.3)], kind="supply"),
        m.signals("C05", hazards=[("trapped", 0.95), ("rising_water", 0.3)]),
        m.signals("C05", hazards=[("rising_water", 0.95), ("fire", 0.4)]),
        m.signals("C05", hazards=[("rising_water", 0.95)], vulnerable=[("child", 0.3)]),
        m.signals("C08", hazards=[("fire", 0.3)]),
        m.signals("C08", hazards=[("trapped", 0.6), ("rising_water", 0.3)]),
        m.signals("C08", vulnerable=[("elderly", 0.3)]),
        m.signals("C08", hazards=[("fire", 0.3)], vulnerable=[("elderly", 0.3)]),
        m.signals("C11", hazards=[("rising_water", 0.8), ("on_roof", 0.3)]),
        *(m._c04_with(t) for t in ("P1", "P2", "P3", None)),
        *(m._weak_p1_dup(t) for t in ("P1", "P2", "P3", None)),
    ]
    for text in (None, "", "   ", "\t\n", " 4410 Bissonnet St "):
        for loc_type in ("address", "coords", "landmark"):
            item = m.inp("C02")
            item["extracted"]["location"] = {"text": text, "type": loc_type, "confidence": 0.9}
            inputs.append(item)
    for conf, hits, cands in ((0.84, 1, []), (0.8, 1, [m.cand(tier="P3")]), (0.7, 2, [])):
        item = m.inp("C01")
        item["extracted"]["is_request"]["confidence"] = conf
        item["context"]["hoax_marker_hits"] = hits
        item["context"]["duplicate_candidates"] = cands
        inputs.append(item)
    return inputs


def test_review_fix_inputs_parity(binary: Path):
    inputs = load_review_inputs()
    assert mercury(binary, inputs) == decide(inputs)


def test_optional_fields_default_like_python(binary: Path):
    base = engine_input(CASES[0])
    variants = []
    for drop in ("hazards", "vulnerable", "needs", "time_ref", "people_count", "request_kind"):
        v = copy.deepcopy(base)
        del v["extracted"][drop]
        variants.append(v)
    v = copy.deepcopy(base)
    del v["context"]
    variants.append(v)
    v = copy.deepcopy(base)
    v["context"]["thresholds"] = {"location_confidence_min": 0.95}
    variants.append(v)
    assert mercury(binary, variants) == decide(variants)


def test_single_object_in_single_object_out(binary: Path):
    out = mercury(binary, engine_input(CASES[0]))
    assert isinstance(out, dict)
    assert mercury(binary, []) == []


# --- error shape --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stdin",
    [
        "",
        "{",
        "[1, 2]",
        '"text"',
        '{"extracted": {}}',
        '{"a": 1} trailing',
        json.dumps({**engine_input(CASES[0]), "extracted": {
            **CASES[0]["extracted"], "hazards": [{"kind": "zombies", "confidence": 1}]}}),
    ],
)
def test_bad_input_exits_1_with_one_stderr_line(binary: Path, stdin: str):
    proc = run(binary, stdin)
    assert proc.returncode == 1
    assert proc.stdout == ""
    lines = proc.stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith("rescue_engine.mercury: ")


def _python_cli(stdin: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(ROOT / d) for d in ("engine", "shared"))}
    return subprocess.run(
        [sys.executable, "-m", "rescue_engine.fallback"],
        input=stdin, capture_output=True, text=True, env=env, timeout=60, check=False,
    )


def _contract_violations() -> dict[str, dict]:
    """Inputs outside the contract that both engines must reject (A9 enums, R8 thresholds)."""
    spec = importlib.util.spec_from_file_location(
        "review_fixes", Path(__file__).with_name("test_review_fixes.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bad = dict(module.BAD_ENUMS)
    for name, th in {
        "firm_below_uncertain": {"hazard_firm_min": 0.4},
        "weak_above_uncertain": {"hazard_weak_min": 0.6},
    }.items():
        item = copy.deepcopy(engine_input(CASES[0]))
        item["context"]["thresholds"].update(th)
        bad[name] = item
    bad["batch_with_one_bad"] = [engine_input(CASES[0]), module.BAD_ENUMS["need_kind"]]
    return bad


CONTRACT_VIOLATIONS = _contract_violations()


@pytest.mark.parametrize(
    "payload", CONTRACT_VIOLATIONS.values(), ids=CONTRACT_VIOLATIONS.keys()
)
def test_contract_violations_exit_1_in_both_engines(binary: Path, payload):
    stdin = json.dumps(payload)
    for proc, prefix in ((run(binary, stdin), "rescue_engine.mercury: "),
                         (_python_cli(stdin), "rescue_engine.fallback: ")):
        assert proc.returncode == 1
        assert proc.stdout == ""
        lines = proc.stderr.splitlines()
        assert len(lines) == 1 and lines[0].startswith(prefix), proc.stderr


# --- compiler-checked exhaustiveness ------------------------------------------------------------


def test_missing_switch_arm_is_a_compile_error(binary: Path, tmp_path: Path):
    """Dropping one tier rule's tier makes rule_tier/1 non-det: the build must fail."""
    src = tmp_path / "src"
    shutil.copytree(b.MERCURY_DIR, src, ignore=shutil.ignore_patterns("build"))
    rules = src / "rules.m"
    text = rules.read_text()
    assert "rule_tier(r_p3_04) = p3.\n" in text
    rules.write_text(text.replace("rule_tier(r_p3_04) = p3.\n", ""))
    with pytest.raises(RuntimeError, match="rule_tier"):
        b.compile_mercury(tmp_path / "out", src)
