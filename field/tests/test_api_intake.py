import json
import shlex
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field import api
from rescue_field.api import create_app, default_decide_batch
from rescue_field.store import EventStore
from rescue_shared.contracts_io import CONTRACTS

FAKE = str(Path(__file__).with_name("fake_engine.py"))
CASES = {
    c["case_id"]: c
    for c in (
        json.loads(ln)
        for ln in (CONTRACTS / "fixtures/cases.jsonl").read_text().splitlines()
        if ln.strip()
    )
}
C01 = CASES["C01"]["raw_text"]


def fake_engine(mode="ok"):
    """The fake engine as a real subprocess, through the app's default engine wiring."""
    return default_decide_batch({"ENGINE_CMD": shlex.join([sys.executable, FAKE, mode])})


class CountingBatch:
    """Wraps a batch decider and records every batch it is called with."""

    def __init__(self, inner):
        self.inner = inner
        self.batches = []

    def __call__(self, inputs):
        self.batches.append(json.loads(json.dumps(list(inputs))))
        return self.inner(inputs)


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


def client_for(store, decide_batch=None):
    app = create_app(store=store, decide_batch=decide_batch or fake_engine())
    return TestClient(app)


def count(store):
    return len(store.all_events())


# ---- POST /requests -----------------------------------------------------------------


def test_single_json_returns_decision(store):
    with client_for(store) as client:
        r = client.post(
            "/requests",
            json={"raw_text": C01, "channel": "sms", "contact": "+17135550101", "lang_hint": "EN"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"]["outcome"] == "prioritized"
    assert body["decision"]["rule_id"] == "R-P2-01"
    events = store.all_events()
    assert [e["type"] for e in events] == ["received", "extracted", "decided"]
    assert {e["request_id"] for e in events} == {body["request_id"]}
    assert events[0]["payload"] == {
        "channel": "sms",
        "raw_text": C01,
        "lang_hint": "en",
        "contact": "+17135550101",
    }
    assert events[2]["payload"]["engine_output"] == body["decision"]


def test_single_form_defaults_to_form_channel(store):
    with client_for(store) as client:
        r = client.post("/requests", data={"raw_text": "need water, 2 people at Elm"})
    assert r.status_code == 200, r.text
    assert r.json()["decision"]["priority_tier"] == "P2"
    received = store.all_events()[0]["payload"]
    assert received["channel"] == "form" and received["contact"] is None


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"json": {}}, "raw_text"),
        ({"json": {"raw_text": "   "}}, "raw_text"),
        ({"json": {"raw_text": "help", "channel": "pigeon"}}, "channel"),
        ({"json": {"raw_text": "help", "lang_hint": "english"}}, "lang_hint"),
        ({"json": {"raw_text": "help", "contact": 5}}, "contact"),
        ({"json": {"raw_text": "x" * 5001}}, "longer"),
        ({"json": ["help"]}, "object"),
        ({"content": b"{not json", "headers": {"content-type": "application/json"}}, "JSON"),
        ({"content": b"help", "headers": {"content-type": "text/plain"}}, "form"),
        ({"data": {"channel": "sms"}}, "raw_text"),
    ],
)
def test_single_invalid_returns_422_and_writes_nothing(store, kwargs, reason):
    with client_for(store) as client:
        r = client.post("/requests", **kwargs)
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"
    assert reason in r.json()["detail"]
    assert count(store) == 0 and store.pending_outbox(10) == []


def test_engine_failure_returns_502_and_writes_nothing(store):
    with client_for(store, fake_engine("fail")) as client:
        r = client.post("/requests", json={"raw_text": C01})
    assert r.status_code == 502
    assert "engine exploded" in r.json()["detail"]
    assert count(store) == 0


def test_default_engine_is_the_real_fallback(store, monkeypatch):
    monkeypatch.delenv("ENGINE_CMD", raising=False)
    with TestClient(create_app(store=store)) as client:
        r = client.post("/requests", json={"raw_text": C01})
    assert r.status_code == 200, r.text
    decision = r.json()["decision"]
    assert (decision["outcome"], decision["priority_tier"]) == ("prioritized", "P1")


# ---- POST /requests/bulk ------------------------------------------------------------


def test_bulk_csv_upload(store):
    csv_text = (
        "raw_text,channel,contact,lang_hint\n"
        f'"{C01}",sms,+17135550101,en\n'
        "\n"
        '"Necesitamos agua, 4 personas",social,,es\n'
    )
    with client_for(store) as client:
        r = client.post(
            "/requests/bulk", files={"file": ("reqs.csv", csv_text.encode(), "text/csv")}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert [x["row"] for x in body["results"]] == [1, 2]
    assert all(x["decision"]["outcome"] == "prioritized" for x in body["results"])
    received = [e["payload"] for e in store.all_events() if e["type"] == "received"]
    assert [(p["channel"], p["contact"], p["lang_hint"]) for p in received] == [
        ("sms", "+17135550101", "en"),
        ("social", None, "es"),
    ]
    assert received[1]["raw_text"] == "Necesitamos agua, 4 personas"


def test_bulk_csv_body_and_text_column_alias(store):
    with client_for(store) as client:
        r = client.post(
            "/requests/bulk",
            content=b"text\nhelp at Elm\nwater please\n",
            headers={"content-type": "text/csv"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2
    channels = {e["payload"]["channel"] for e in store.all_events() if e["type"] == "received"}
    assert channels == {"csv"}


def test_bulk_paste_one_request_per_line(store):
    paste = "help on Elm street\n\n   \nwater rising at Main\nstuck in attic\n"
    with client_for(store) as client:
        r = client.post("/requests/bulk", data={"text": paste, "channel": "social"})
        r2 = client.post(
            "/requests/bulk", content=b"one\ntwo\n", headers={"content-type": "text/plain"}
        )
    assert r.status_code == 200 and r.json()["count"] == 3
    assert r2.status_code == 200 and r2.json()["count"] == 2
    received = [e["payload"] for e in store.all_events() if e["type"] == "received"]
    assert [p["raw_text"] for p in received] == [
        "help on Elm street",
        "water rising at Main",
        "stuck in attic",
        "one",
        "two",
    ]
    assert [p["channel"] for p in received] == ["social"] * 3 + ["csv"] * 2


def test_bulk_rows_are_stored_in_order(store):
    lines = [f"help {n} people at Elm" for n in range(1, 21)]
    with client_for(store) as client:
        r = client.post("/requests/bulk", data={"text": "\n".join(lines)})
    assert r.status_code == 200
    received = [e for e in store.all_events() if e["type"] == "received"]
    assert [e["payload"]["raw_text"] for e in received] == lines
    ids = [x["request_id"] for x in r.json()["results"]]
    assert ids == [e["request_id"] for e in received]
    assert ids == sorted(ids)  # ULIDs order the same way as the rows


def test_bulk_calls_engine_in_batch_mode(store):
    batch = CountingBatch(fake_engine())
    lines = ["help at Elm", "water at Main", "stuck at Oak"]
    with client_for(store, batch) as client:
        r = client.post("/requests/bulk", data={"text": "\n".join(lines)})
    assert r.status_code == 200
    assert len(batch.batches) == 1 and len(batch.batches[0]) == 3


def test_bulk_earlier_rows_are_duplicate_candidates_for_later_rows(store):
    batch = CountingBatch(fake_engine("tier=P1"))
    paste = f"{C01}\nunrelated: need diapers on Elm\n{C01}\n{C01}\n"
    with client_for(store, batch) as client:
        r = client.post("/requests/bulk", data={"text": paste})
    assert r.status_code == 200, r.text
    ids = [x["request_id"] for x in r.json()["results"]]
    decided = {
        e["request_id"]: e["payload"]["engine_input"]["context"]["duplicate_candidates"]
        for e in store.all_events()
        if e["type"] == "decided"
    }
    assert decided[ids[0]] == [] and decided[ids[1]] == []
    assert [c["request_id"] for c in decided[ids[2]]] == [ids[0]]
    assert [c["request_id"] for c in decided[ids[3]]] == [ids[0], ids[2]]
    # Each candidate carries the tier the engine gave it, as if submitted one by one.
    assert all(c["priority_tier"] == "P1" for c in decided[ids[2]] + decided[ids[3]])
    # Waves: rows 1-2 together, then row 3 (needs row 1), then row 4 (needs row 3).
    assert [len(b) for b in batch.batches] == [2, 1, 1]


def test_bulk_rows_see_earlier_uploads(store):
    with client_for(store) as client:
        first = client.post("/requests", json={"raw_text": C01}).json()
        r = client.post("/requests/bulk", data={"text": C01})
    rid = r.json()["results"][0]["request_id"]
    decided = [e for e in store.all_events() if e["type"] == "decided" and e["request_id"] == rid]
    candidates = decided[0]["payload"]["engine_input"]["context"]["duplicate_candidates"]
    assert [c["request_id"] for c in candidates] == [first["request_id"]]
    assert candidates[0]["priority_tier"] == "P2"


def test_bulk_200_lines_under_10s_with_fake_engine(store):
    lines = [f"Request {n}: {n % 7 + 1} people need help at {n} Elm Street" for n in range(200)]
    with client_for(store, fake_engine()) as client:
        t = time.monotonic()
        r = client.post("/requests/bulk", data={"text": "\n".join(lines)})
        elapsed = time.monotonic() - t
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 200
    assert elapsed < 10
    assert count(store) == 600


@pytest.mark.parametrize(
    ("kwargs", "reason", "row"),
    [
        ({"data": {"text": "  \n \n"}}, "no requests", None),
        ({"data": {"text": "help", "channel": "pigeon"}}, "channel", 1),
        ({"data": {"text": "ok\n" + "x" * 5001}}, "longer", 2),
        ({"data": {}}, "file", None),
        ({"files": {"file": ("a.csv", b"contact\n+1713\n", "text/csv")}}, "raw_text column", None),
        ({"files": {"file": ("a.csv", b"raw_text,channel\nhi,sms\n,sms\n")}}, "raw_text", 2),
        ({"files": {"file": ("a.csv", b"raw_text,lang_hint\nhi,xyz\n")}}, "lang_hint", 1),
        ({"files": {"file": ("a.csv", b"", "text/csv")}}, "empty", None),
        ({"files": {"file": ("a.csv", b"raw_text\n\xff\xfe\n", "text/csv")}}, "UTF-8", None),
        ({"json": {"rows": []}}, "text", None),
        ({"content": b"x", "headers": {"content-type": "application/xml"}}, "multipart", None),
    ],
)
def test_bulk_invalid_returns_422_and_writes_nothing(store, kwargs, reason, row):
    with client_for(store) as client:
        r = client.post("/requests/bulk", **kwargs)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"] == "invalid_input"
    assert reason in body["detail"]
    assert body.get("row") == row
    assert count(store) == 0 and store.pending_outbox(10) == []


def test_bulk_too_many_rows(store, monkeypatch):
    monkeypatch.setattr(api, "MAX_BULK_ROWS", 3)
    with client_for(store) as client:
        r = client.post("/requests/bulk", data={"text": "a\nb\nc\nd"})
    assert r.status_code == 422 and "at most 3" in r.json()["detail"]
    assert count(store) == 0


def test_bulk_engine_failure_writes_nothing(store):
    with client_for(store, fake_engine("short")) as client:
        r = client.post("/requests/bulk", data={"text": "a\nb\nc"})
    assert r.status_code == 502
    assert count(store) == 0


# ---- wiring -------------------------------------------------------------------------


def test_store_opens_in_field_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "nested" / "data"
    monkeypatch.setenv("FIELD_DATA_DIR", str(data))
    app = create_app(decide_batch=fake_engine())
    with TestClient(app) as client:
        r = client.post("/requests", json={"raw_text": C01})
        assert r.status_code == 200
    assert (data / "field.db").exists()
    reopened = EventStore(data / "field.db")
    assert len(reopened.all_events()) == 3
    reopened.close()


def test_module_app_exists_and_import_touches_no_files():
    assert api.app.state.store is None
    paths = api.app.openapi()["paths"]
    assert "/requests" in paths and "/requests/bulk" in paths


class FakeWorker:
    def __init__(self, store, cloud_url):
        self.store, self.cloud_url = store, cloud_url
        self.events = []

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")


def test_sync_worker_starts_with_cloud_url(store, monkeypatch):
    monkeypatch.setenv("CLOUD_URL", "http://cloud:4000")
    workers = []

    def factory(s, url):
        workers.append(FakeWorker(s, url))
        return workers[-1]

    app = create_app(store=store, decide_batch=fake_engine(), sync_worker_factory=factory)
    with TestClient(app):
        assert len(workers) == 1
        assert workers[0].events == ["start"]
        assert workers[0].store is store and workers[0].cloud_url == "http://cloud:4000"
        assert app.state.sync_worker is workers[0]
    assert workers[0].events == ["start", "stop"]


def test_no_sync_worker_without_cloud_url(store, monkeypatch):
    monkeypatch.delenv("CLOUD_URL", raising=False)
    workers = []
    app = create_app(
        store=store, decide_batch=fake_engine(), sync_worker_factory=lambda *a: workers.append(a)
    )
    with TestClient(app):
        assert workers == [] and app.state.sync_worker is None
