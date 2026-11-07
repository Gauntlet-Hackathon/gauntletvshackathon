"""Per-laptop incident numbers and the sample flag at intake (#308)."""

import json
import shlex
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app, default_decide_batch
from rescue_field.pipeline import PipelineConfig, derive_incident_prefix
from rescue_field.store import EventStore
from rescue_shared.contracts_io import CONTRACTS, validator_for

FAKE = str(Path(__file__).with_name("fake_engine.py"))
TEXT = "HELP! Trapped on the roof with 3 kids, water rising fast at 12 Elm St"


def engine():
    return default_decide_batch({"ENGINE_CMD": shlex.join([sys.executable, FAKE, "ok"])})


def client_for(store, config=None):
    return TestClient(create_app(store=store, decide_batch=engine(), config=config))


@pytest.fixture
def db(tmp_path):
    return tmp_path / "f.db"


@pytest.fixture
def store(db):
    s = EventStore(db, check_same_thread=False)
    yield s
    s.close()


def received(store):
    return [e for e in store.all_events() if e["type"] == "received"]


# ---- prefix and counter ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("device_id", "prefix"),
    [
        ("laptop-dev-01", "D01"),
        ("demo-field-01", "F01"),
        ("field-07", "F07"),
        ("laptop-7", "L7"),
        ("laptop-12345", "L345"),
        ("42", "42"),
        ("boat", "BOAT"),
        ("ecc.north_station", "STAT"),
        ("---", "A"),
    ],
)
def test_derived_prefix(device_id, prefix):
    assert derive_incident_prefix(device_id) == prefix
    assert PipelineConfig(device_id=device_id).prefix == prefix


def test_env_prefix_overrides_and_is_validated():
    cfg = PipelineConfig.from_env({"DEVICE_ID": "laptop-dev-01", "INCIDENT_PREFIX": " a "})
    assert cfg.prefix == "A" and cfg.incident(147) == "A-0147"
    assert PipelineConfig.from_env({"DEVICE_ID": "laptop-dev-01"}).prefix == "D01"
    for bad in ("TOOLONG", "A-1", "É"):
        with pytest.raises(ValueError, match="INCIDENT_PREFIX"):
            PipelineConfig.from_env({"INCIDENT_PREFIX": bad})


def test_counter_persists_and_never_repeats(db):
    s = EventStore(db)
    assert s.reserve("incident") == 1
    assert s.reserve("incident", 3) == 2
    assert s.reserve("other") == 1
    s.close()
    s = EventStore(db)
    assert s.reserve("incident") == 5
    with pytest.raises(ValueError):
        s.reserve("incident", 0)
    s.close()


# ---- intake ------------------------------------------------------------------------------


def test_numbers_unique_and_increasing_across_restarts(db):
    cfg = PipelineConfig(incident_prefix="A")
    s = EventStore(db, check_same_thread=False)
    with client_for(s, cfg) as client:
        one = client.post("/requests", json={"raw_text": TEXT})
        bulk = client.post("/requests/bulk", json={"text": "need water at 1 Oak St\nroof at 2 Elm"})
    assert one.json()["incident"] == "A-0001"
    assert [r["incident"] for r in bulk.json()["results"]] == ["A-0002", "A-0003"]
    s.close()

    s = EventStore(db, check_same_thread=False)  # the laptop restarts
    with client_for(s, cfg) as client:
        again = client.post("/requests", json={"raw_text": TEXT})
    assert again.json()["incident"] == "A-0004"
    numbers = [e["payload"]["incident"] for e in received(s)]
    assert numbers == ["A-0001", "A-0002", "A-0003", "A-0004"]
    s.close()


def test_failed_intake_does_not_reuse_numbers(store):
    with client_for(store) as client:
        assert client.post("/requests", json={"raw_text": TEXT}).json()["incident"] == "D01-0001"
    bad = create_app(store=store, decide_batch=default_decide_batch(
        {"ENGINE_CMD": shlex.join([sys.executable, FAKE, "fail"])}
    ))
    with TestClient(bad) as client:
        assert client.post("/requests", json={"raw_text": TEXT}).status_code == 502
    with client_for(store) as client:
        assert client.post("/requests", json={"raw_text": TEXT}).json()["incident"] == "D01-0002"


def test_sample_flag_single_and_bulk_json(store):
    with client_for(store) as client:
        assert client.post("/requests", json={"raw_text": TEXT, "sample": True}).status_code == 200
        assert client.post("/requests", json={"raw_text": TEXT}).status_code == 200
        assert client.post("/requests", json={"raw_text": TEXT, "sample": None}).status_code == 200
        body = {"text": "a at 1 Oak St\nb at 2 Elm", "sample": True}
        r = client.post("/requests/bulk", json=body)
        assert r.status_code == 200
    payloads = [e["payload"] for e in received(store)]
    assert [p.get("sample") for p in payloads] == [True, None, None, True, True]
    assert "sample" not in payloads[1] and "sample" not in payloads[2]
    schema = validator_for("payloads/received.schema.json")
    for e in store.all_events():
        assert not list(validator_for("event.schema.json").iter_errors(e))
        if e["type"] == "received":
            assert not list(schema.iter_errors(e["payload"]))


def test_sample_must_be_boolean(store):
    with client_for(store) as client:
        r = client.post("/requests", json={"raw_text": TEXT, "sample": "yes"})
        b = client.post("/requests/bulk", json={"text": "a at 1 Oak St", "sample": 1})
    assert r.status_code == b.status_code == 422
    assert "sample" in r.json()["detail"]
    assert store.all_events() == []


def test_sample_ignored_outside_json(store):
    with client_for(store) as client:
        f = client.post("/requests", data={"raw_text": TEXT, "sample": "true"})
        c = client.post(
            "/requests/bulk",
            content="raw_text,sample\nroof at 2 Elm,true\n",
            headers={"content-type": "text/csv"},
        )
    assert f.status_code == c.status_code == 200
    assert all("sample" not in e["payload"] for e in received(store))


# ---- display -----------------------------------------------------------------------------


def test_screens_show_the_incident_number(store):
    with client_for(store, PipelineConfig(incident_prefix="A")) as client:
        rid = client.post("/requests", json={"raw_text": TEXT}).json()["request_id"]
        queue = client.get("/").text
        board = client.get("/board").text
        log = client.get("/intake/log.json").json()["requests"]
        send = client.get(f"/requests/{rid}/send").text
    assert ">A-0001<" in queue and f"#{rid[-4:]}" not in queue
    assert '<span class="incident">A-0001</span>' in board
    assert log[0]["incident"] == "A-0001"
    assert "Rescue A-0001:" in send


def test_received_schema_has_the_fields():
    schema = json.loads((CONTRACTS / "payloads/received.schema.json").read_text())
    assert {"incident", "sample"} <= schema["properties"].keys()
