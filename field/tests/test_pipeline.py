import json
import sys
from functools import partial
from pathlib import Path

import pytest

from field_app.engine_client import EngineError
from field_app.engine_client import decide as subprocess_decide
from field_app.slm import STUB_MODEL_VERSION, FixtureExtractor
from field_app.store import EventStore
from rescue_field.pipeline import (
    DEFAULT_THRESHOLDS,
    PipelineConfig,
    process_request,
    read_list,
)
from rescue_shared.contracts_io import CONTRACTS, validator_for
from rescue_shared.ulid import is_ulid

RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
FAKE = str(Path(__file__).with_name("fake_engine.py"))
CASES = {
    c["case_id"]: c
    for c in (
        json.loads(ln)
        for ln in (CONTRACTS / "fixtures/cases.jsonl").read_text().splitlines()
        if ln.strip()
    )
}


class FakeDecider:
    """In-process engine double: records every input, returns a fixed tier."""

    def __init__(self, tier="P1"):
        self.inputs = []
        self.tier = tier

    def __call__(self, engine_input):
        self.inputs.append(json.loads(json.dumps(engine_input)))
        return {
            "outcome": "prioritized",
            "priority_tier": self.tier,
            "rule_id": "R-P1-01",
            "duplicate_of": None,
            "abstain_reason": None,
            "follow_up_question": None,
            "rules_version": RULES,
        }


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db")
    yield s
    s.close()


def run(store, case_id, decider, **kw):
    c = CASES[case_id]
    return process_request(
        c["raw_text"],
        "sms",
        store=store,
        extractor=FixtureExtractor(),
        decide=decider,
        contact=c["contact"],
        now=c["received_at"],
        **kw,
    )


def test_emits_three_events_with_ids_and_versions(store):
    events = run(store, "C01", FakeDecider(), config=PipelineConfig(device_id="field-07"))
    assert [e["type"] for e in events] == ["received", "extracted", "decided"]
    assert len({e["event_id"] for e in events}) == 3
    assert len({e["request_id"] for e in events}) == 1
    for e in events:
        assert is_ulid(e["event_id"]) and is_ulid(e["request_id"])
        assert e["device_id"] == "field-07"
        assert e["rules_version"] == RULES
        assert e["model_version"] == STUB_MODEL_VERSION
        assert e["occurred_at"] == CASES["C01"]["received_at"]
        assert not list(validator_for("event.schema.json").iter_errors(e))
    received = events[0]["payload"]
    assert received == {
        "channel": "sms",
        "raw_text": CASES["C01"]["raw_text"],
        "lang_hint": None,
        "contact": CASES["C01"]["contact"],
    }
    assert events[1]["payload"] == CASES["C01"]["extracted"]


def test_raw_text_never_reaches_engine(store):
    decider = FakeDecider()
    run(store, "C01", decider)
    run(store, "C20", decider)  # hoax markers are counted from the raw text
    for engine_input, cid in zip(decider.inputs, ["C01", "C20"], strict=True):
        assert set(engine_input) == {"extracted", "context"}
        dumped = json.dumps(engine_input, ensure_ascii=False)
        assert CASES[cid]["raw_text"] not in dumped
        assert "raw_text" not in dumped
        assert not list(validator_for("engine_input.schema.json").iter_errors(engine_input))


def test_events_and_outbox_committed_together(store, monkeypatch):
    calls = []
    real = store.append_events
    monkeypatch.setattr(store, "append_events", lambda evs: calls.append(list(evs)) or real(evs))
    events = run(store, "C01", FakeDecider())
    assert len(calls) == 1 and calls[0] == events
    assert store.all_events() == events
    assert store.pending_outbox(10) == events


def test_engine_failure_stores_nothing(store):
    def boom(_):
        raise EngineError("engine timed out")

    with pytest.raises(EngineError):
        run(store, "C01", boom)
    assert store.all_events() == [] and store.pending_outbox(10) == []


def test_invalid_channel_stores_nothing(store):
    with pytest.raises(ValueError):
        process_request("help", "pigeon", store=store, decide=FakeDecider())
    assert store.all_events() == []


@pytest.mark.parametrize("case_id", ["C01", "C15", "C20"])
def test_context_matches_fixture(store, case_id):
    decider = FakeDecider()
    run(store, case_id, decider)
    context = decider.inputs[0]["context"]
    expected = CASES[case_id]["context"]
    assert context["street_list_hits"] == expected["street_list_hits"]
    assert context["hoax_marker_hits"] == expected["hoax_marker_hits"]
    assert context["thresholds"] == expected["thresholds"] == DEFAULT_THRESHOLDS
    assert context["duplicate_candidates"] == []


def test_duplicate_candidates_from_earlier_requests(store):
    decider = FakeDecider(tier="P1")
    first = run(store, "C01", decider)
    run(store, "C22", decider)
    candidates = decider.inputs[1]["context"]["duplicate_candidates"]
    assert candidates == [
        {
            "request_id": first[0]["request_id"],
            "received_at": CASES["C01"]["received_at"],
            "people_count": 3,
            "contact_match": False,
            "priority_tier": "P1",
        }
    ]


def test_duplicate_window_comes_from_config(store):
    thresholds = {**DEFAULT_THRESHOLDS, "duplicate_window_hours": 1}
    config = PipelineConfig(thresholds=thresholds)
    decider = FakeDecider()
    run(store, "C01", decider, config=config)
    run(store, "C22", decider, config=config)  # 1h45m later: outside a 1h window
    assert decider.inputs[1]["context"]["duplicate_candidates"] == []
    assert decider.inputs[1]["context"]["thresholds"]["duplicate_window_hours"] == 1


def test_configured_lists(store, tmp_path):
    markers = tmp_path / "markers.txt"
    markers.write_text("# comment\nkroger\nhurry\n")
    config = PipelineConfig(hoax_markers=read_list(markers), streets=("Kirby Drive",))
    decider = FakeDecider()
    run(store, "C22", decider, config=config)
    context = decider.inputs[0]["context"]
    assert context["hoax_marker_hits"] == 2
    assert context["street_list_hits"] == []


def test_from_env(tmp_path):
    streets = tmp_path / "s.txt"
    streets.write_text("Elm Street\n")
    config = PipelineConfig.from_env(
        {"FIELD_DEVICE_ID": "field-09", "FIELD_STREETS_PATH": str(streets)}
    )
    assert config.device_id == "field-09"
    assert config.streets == ("Elm Street",)
    assert "prank" in config.hoax_markers


def test_decided_payload_is_input_plus_output(store):
    decider = FakeDecider()
    events = run(store, "C01", decider)
    decided = events[2]["payload"]
    assert set(decided) == {"engine_input", "engine_output"}
    assert decided["engine_input"] == decider.inputs[0]
    assert decided["engine_input"]["extracted"] == events[1]["payload"]
    assert decided["engine_output"] == decider(decider.inputs[0])


def test_with_subprocess_engine(store):
    decider = partial(subprocess_decide, cmd=[sys.executable, FAKE, "ok"])
    events = run(store, "C01", decider)
    assert events[2]["payload"]["engine_output"]["rule_id"] == "R-P2-01"
    assert len(store.pending_outbox(10)) == 3
