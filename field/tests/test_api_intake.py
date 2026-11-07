import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app
from rescue_field.engine_client import decide as subprocess_decide
from rescue_field.slm import STUB_MODEL_VERSION, Extraction
from rescue_field.store import EventStore
from rescue_shared.contracts_io import CONTRACTS

FAKE = str(Path(__file__).with_name("fake_engine.py"))


def fake_decide(engine_input):
    return subprocess_decide(engine_input, cmd=[sys.executable, FAKE, "ok"])


class SameExtractor:
    """Every line extracts to the same location and head count (duplicate candidates)."""

    def extract(self, raw_text):
        case = json.loads((CONTRACTS / "fixtures/cases.jsonl").read_text().splitlines()[0])
        return Extraction(case["extracted"], STUB_MODEL_VERSION)


def make_client(decide=fake_decide, extractor=None):
    store = EventStore(":memory:")
    # thread check: TestClient runs the app in a worker thread
    store._conn.close()
    import sqlite3

    store._conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    store._conn.execute("PRAGMA foreign_keys=ON")
    from rescue_field import store as store_mod

    store._conn.executescript(store_mod._SCHEMA)
    return TestClient(create_app(store=store, decide=decide, extractor=extractor)), store


def count(store):
    return len(store.all_events())


def test_single_json_returns_decision():
    client, store = make_client()
    r = client.post("/requests", json={"text": "Need water, stuck on Elm Street", "channel": "sms"})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"]["outcome"] == "prioritized"
    assert body["request_id"]
    assert count(store) == 3


def test_single_form():
    client, store = make_client()
    r = client.post("/requests", data={"raw_text": "help we are trapped", "contact": "555-0100"})
    assert r.status_code == 200
    assert store.all_events()[0]["payload"]["channel"] == "form"
    assert store.all_events()[0]["payload"]["contact"] == "555-0100"


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "   "},
        {},
        {"text": "help", "channel": "pigeon"},
        {"text": "help", "lang_hint": "english"},
        {"text": 5},
    ],
)
def test_invalid_single_is_422_and_writes_nothing(payload):
    client, store = make_client()
    r = client.post("/requests", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"]
    assert count(store) == 0


def test_invalid_body_type_and_bad_json():
    client, store = make_client()
    assert (
        client.post(
            "/requests", content=b"{", headers={"content-type": "application/json"}
        ).status_code
        == 422
    )
    assert (
        client.post("/requests", content=b"hi", headers={"content-type": "text/plain"}).status_code
        == 422
    )
    assert count(store) == 0


def test_bulk_csv_upload():
    client, store = make_client()
    csv_body = "text,contact\nNeed water on Elm Street,555-1\n\nStuck on roof,555-2\n"
    r = client.post("/requests/bulk", files={"file": ("in.csv", csv_body, "text/csv")})
    assert r.status_code == 200
    assert r.json()["count"] == 2
    received = [e for e in store.all_events() if e["type"] == "received"]
    assert [e["payload"]["contact"] for e in received] == ["555-1", "555-2"]
    assert received[0]["payload"]["channel"] == "csv"


def test_bulk_paste_and_text_plain():
    client, store = make_client()
    r = client.post("/requests/bulk", data={"paste": "help one\n\nhelp two\n"})
    assert r.status_code == 200 and r.json()["count"] == 2
    r = client.post(
        "/requests/bulk", content="a help\nb help", headers={"content-type": "text/plain"}
    )
    assert r.json()["count"] == 2
    assert count(store) == 12


def test_bulk_rows_in_order_earlier_rows_are_duplicate_candidates():
    seen = []

    def decide(engine_input):
        seen.append(engine_input["context"]["duplicate_candidates"])
        return fake_decide(engine_input)

    client, _ = make_client(decide, SameExtractor())
    line = "Need water, stuck on Elm Street, 4 people"
    r = client.post("/requests/bulk", data={"paste": f"{line}\n{line}"})
    assert r.status_code == 200
    ids = [x["request_id"] for x in r.json()["results"]]
    assert seen[0] == []
    assert seen[1] and ids[0] in json.dumps(seen[1])


def test_bulk_invalid_row_rejects_everything():
    client, store = make_client()
    body = "raw_text,channel\nhelp me,csv\nhelp you,pigeon\n"
    r = client.post("/requests/bulk", content=body, headers={"content-type": "text/csv"})
    assert r.status_code == 422
    assert "row 2" in r.json()["detail"]
    assert count(store) == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": "foo,bar\n1,2\n", "headers": {"content-type": "text/csv"}},
        {"content": "\n\n", "headers": {"content-type": "text/plain"}},
        {"data": {"x": "y"}},
    ],
)
def test_bulk_bad_input_422(kwargs):
    client, store = make_client()
    assert client.post("/requests/bulk", **kwargs).status_code == 422
    assert count(store) == 0


def test_bulk_200_lines_under_10s():
    client, store = make_client(
        lambda _i: {
            "outcome": "prioritized",
            "priority_tier": "P2",
            "rule_id": "R-P2-01",
            "duplicate_of": None,
            "abstain_reason": None,
            "follow_up_question": None,
            "rules_version": "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21",
        }
    )
    lines = "\n".join(f"help {i} need water at Oak Avenue" for i in range(200))
    t = time.monotonic()
    r = client.post("/requests/bulk", data={"paste": lines})
    assert r.status_code == 200 and r.json()["count"] == 200
    assert time.monotonic() - t < 10
    assert count(store) == 600
