import json

import pytest

from rescue_engine import build as b
from rescue_engine import replay_diff as rd

ULID = "01JCB000000000000000000{:03d}"


def _input(people=3, hazard="rising_water"):
    return {
        "extracted": {
            "location": {"text": "Main St", "type": "address", "confidence": 0.9},
            "people_count": {"value": people, "confidence": 0.9},
            "vulnerable": [],
            "hazards": [{"kind": hazard, "confidence": 0.9}],
            "time_ref": {"text": None, "is_stale_post": False, "confidence": 0.9},
            "is_request": {"value": True, "confidence": 0.97},
            "spans": [],
            "needs": [],
            "request_kind": "rescue",
        },
        "context": {},
    }


def _event(n, rid, inp, at="2026-11-07T13:00:00Z"):
    return {
        "event_id": ULID.format(n),
        "request_id": ULID.format(rid),
        "type": "decided",
        "device_id": "laptop-dev-01",
        "occurred_at": at,
        "rules_version": None,
        "model_version": None,
        "payload": {"engine_input": inp, "engine_output": {}},
    }


def _version(tmp_path, name, rules_py_suffix=""):
    """Copy a built engine into a fake version dir, optionally patching tier rules."""
    built = b.build(tmp_path / name)
    if rules_py_suffix:
        rules = built / "rescue_engine" / "fallback" / "engine.py"
        rules.write_text(rules.read_text() + rules_py_suffix)
    return built


PATCH = """

_orig = decide_one


def decide_one(engine_input):
    out = dict(_orig(engine_input))
    out["priority_tier"] = "P3"
    return out


def decide(engine_input):
    if isinstance(engine_input, list):
        return [decide_one(i) for i in engine_input]
    return decide_one(engine_input)
"""


@pytest.fixture
def events(tmp_path):
    path = tmp_path / "events.jsonl"
    evs = [
        _event(1, 1, _input()),
        _event(2, 2, _input(people=1)),
        {**_event(3, 1, _input()), "type": "received", "payload": {}},
    ]
    path.write_text("\n".join(json.dumps(e) for e in evs) + "\n")
    return path


def test_identical_versions_no_changes(tmp_path, events):
    v = _version(tmp_path, "a")
    report = rd.diff(events, v, v)
    assert report["total"] == 2
    assert report["changed"] == 0
    assert "No tier or outcome changes" in rd.to_markdown(report)


def test_changed_rules_reported(tmp_path, events):
    old = _version(tmp_path, "a")
    new = _version(tmp_path, "b", PATCH)
    report = rd.diff(events, old, new)
    row = report["requests"][0]
    assert row["before"]["priority_tier"] != "P3"
    assert row["after"]["priority_tier"] == "P3"
    assert row["tier_changed"] and not row["outcome_changed"]
    md = rd.to_markdown(report)
    assert ULID.format(1) in md and "→ P3" in md


def test_latest_decided_event_wins(tmp_path):
    path = tmp_path / "e.jsonl"
    evs = [
        _event(1, 1, _input(people=1), at="2026-11-07T13:00:00Z"),
        _event(2, 1, _input(people=9), at="2026-11-07T14:00:00Z"),
    ]
    path.write_text("\n".join(json.dumps(e) for e in evs))
    assert rd.load_inputs(path)[ULID.format(1)]["extracted"]["people_count"]["value"] == 9


def test_cli_writes_json_and_md(tmp_path, events, capsys):
    old = _version(tmp_path, "a")
    new = _version(tmp_path, "b", PATCH)
    jo, mo = tmp_path / "r.json", tmp_path / "r.md"
    argv = [str(events), str(old), str(new), "--json", str(jo), "--md", str(mo)]
    assert rd.main(argv) == 0
    assert json.loads(jo.read_text())["total"] == 2
    assert mo.read_text().startswith("# Replay diff")


def test_unknown_version_errors(tmp_path, events, capsys):
    assert rd.main([str(events), "nope", "nope2", "--dist", str(tmp_path)]) == 1
    assert "not found" in capsys.readouterr().err
