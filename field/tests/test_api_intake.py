import csv
import io
import json
import sys
import time
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field import engine_client
from rescue_field.api import MAX_BULK_ROWS, create_app
from rescue_field.slm import FixtureExtractor
from rescue_field.store import EventStore
from rescue_shared.contracts_io import CONTRACTS, validator_for

FAKE = str(Path(__file__).with_name("fake_engine.py"))
CASES = {
    c["case_id"]: c
    for c in (
        json.loads(ln)
        for ln in (CONTRACTS / "fixtures/cases.jsonl").read_text().splitlines()
        if ln.strip()
    )
}


def fake_cmd(mode="ok"):
    return [sys.executable, FAKE, mode]


class Counting:
    """Wraps an engine function and counts how many times the engine is started."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def __call__(self, arg):
        self.calls += 1
        return self.fn(arg)


def make_client(tmp_path, mode="ok"):
    """App backed by the subprocess fake engine (tests/fake_engine.py) and a temp DB."""
    single = Counting(partial(engine_client.decide, cmd=fake_cmd(mode)))
    batch = Counting(partial(engine_client.decide_batch, cmd=fake_cmd(mode)))
    db = tmp_path / "field.db"
    app = create_app(
        db_path=db, extractor=FixtureExtractor(), decide=single, decide_batch=batch
    )
    return TestClient(app), db, single, batch


def events(db):
    store = EventStore(db)
    try:
        return store.all_events(), store.pending_outbox(10_000)
    finally:
        store.close()


def assert_nothing_written(db):
    if db.exists():
        stored, outbox = events(db)
        assert stored == [] and outbox == []


def csv_text(rows, header=("raw_text", "contact")):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


# --- POST /requests ---------------------------------------------------------------


def test_single_json_returns_decision(tmp_path):
    client, db, single, _ = make_client(tmp_path)
    c = CASES["C01"]
    r = client.post(
        "/requests", json={"raw_text": c["raw_text"], "channel": "sms", "contact": c["contact"]}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"]["rule_id"] == "R-P2-01"
    assert not list(validator_for("engine_output.schema.json").iter_errors(body["decision"]))
    stored, outbox = events(db)
    assert [e["type"] for e in stored] == ["received", "extracted", "decided"]
    assert {e["request_id"] for e in stored} == {body["request_id"]}
    assert stored[0]["payload"]["channel"] == "sms"
    assert stored[0]["payload"]["contact"] == c["contact"]
    assert len(outbox) == 3
    assert single.calls == 1


def test_single_form_defaults_to_form_channel(tmp_path):
    client, db, _, _ = make_client(tmp_path)
    r = client.post("/requests", data={"raw_text": "need water, 2 people", "lang_hint": "en"})
    assert r.status_code == 200, r.text
    assert r.json()["decision"]["outcome"] == "prioritized"
    received = events(db)[0][0]["payload"]
    assert received == {
        "channel": "form",
        "raw_text": "need water, 2 people",
        "lang_hint": "en",
        "contact": None,
    }


def test_single_sees_earlier_request_as_duplicate(tmp_path):
    client, db, _, _ = make_client(tmp_path, mode="dupes")
    first = client.post("/requests", json={"raw_text": CASES["C01"]["raw_text"]})
    second = client.post("/requests", json={"raw_text": CASES["C22"]["raw_text"]})
    assert first.json()["decision"]["rule_id"] == "R-P2-0"
    assert second.json()["decision"]["rule_id"] == "R-P2-1"
    decided = [e for e in events(db)[0] if e["type"] == "decided"]
    candidates = decided[1]["payload"]["engine_input"]["context"]["duplicate_candidates"]
    assert [c["request_id"] for c in candidates] == [first.json()["request_id"]]


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"json": {"channel": "sms"}}, "raw_text"),
        ({"json": {"raw_text": "   "}}, "raw_text"),
        ({"json": {"raw_text": 42}}, "raw_text"),
        ({"json": {"raw_text": "help", "channel": "pigeon"}}, "channel"),
        ({"json": {"raw_text": "help", "lang_hint": "english"}}, "lang_hint"),
        ({"json": {"raw_text": "help", "contact": 5}}, "contact"),
        ({"json": {"raw_text": "x" * 5001}}, "longer than"),
        ({"json": ["help"]}, "object"),
        ({"content": b"{not json", "headers": {"content-type": "application/json"}}, "JSON"),
        ({"content": b"help", "headers": {"content-type": "text/plain"}}, "JSON or form"),
        ({"data": {"channel": "sms"}}, "raw_text"),
    ],
)
def test_single_invalid_returns_422_and_writes_nothing(tmp_path, kwargs, reason):
    client, db, single, _ = make_client(tmp_path)
    r = client.post("/requests", **kwargs)
    assert r.status_code == 422
    assert reason in r.json()["detail"]
    assert single.calls == 0
    assert_nothing_written(db)


def test_single_engine_failure_writes_nothing(tmp_path):
    client, db, _, _ = make_client(tmp_path, mode="fail")
    r = client.post("/requests", json={"raw_text": "help, trapped"})
    assert r.status_code == 502
    assert "engine" in r.json()["detail"]
    assert_nothing_written(db)


# --- POST /requests/bulk ----------------------------------------------------------


def test_bulk_csv_upload_in_order(tmp_path):
    client, db, single, batch = make_client(tmp_path, mode="echo")
    rows = [["help 3 people trapped", "+1555"], ["help 1 people", ""], ["help 2 people", ""]]
    r = client.post(
        "/requests/bulk", files={"file": ("in.csv", csv_text(rows).encode(), "text/csv")}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3
    assert [x["row"] for x in body["results"]] == [1, 2, 3]
    assert [x["decision"]["rule_id"] for x in body["results"]] == ["R-P2-3", "R-P2-1", "R-P2-2"]
    stored, outbox = events(db)
    received = [e for e in stored if e["type"] == "received"]
    assert [e["payload"]["raw_text"] for e in received] == [row[0] for row in rows]
    assert [e["request_id"] for e in received] == [x["request_id"] for x in body["results"]]
    assert {e["payload"]["channel"] for e in received} == {"csv"}
    assert received[0]["payload"]["contact"] == "+1555"
    assert received[1]["payload"]["contact"] is None
    assert len(outbox) == 9
    assert batch.calls == 1 and single.calls == 0  # one engine call in batch mode


def test_bulk_csv_columns_and_raw_text_csv_body(tmp_path):
    client, db, _, _ = make_client(tmp_path)
    text = csv_text(
        [["roof, 2 people", "sms", "es"], ["", "", ""], ["need meds", "", ""]],
        header=("raw_text", "channel", "lang_hint"),
    )
    r = client.post("/requests/bulk", content=text, headers={"content-type": "text/csv"})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2  # the blank row is skipped
    received = [e["payload"] for e in events(db)[0] if e["type"] == "received"]
    assert [(p["channel"], p["lang_hint"]) for p in received] == [("sms", "es"), ("csv", None)]


def test_bulk_paste_form_and_text_plain(tmp_path):
    client, db, _, _ = make_client(tmp_path)
    r = client.post("/requests/bulk", data={"text": "first help\n\n  second help  \nthird\n"})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 3
    r = client.post(
        "/requests/bulk", content="fourth\nfifth", headers={"content-type": "text/plain"}
    )
    assert r.status_code == 200, r.text
    received = [e["payload"] for e in events(db)[0] if e["type"] == "received"]
    texts = [p["raw_text"] for p in received]
    assert texts == ["first help", "second help", "third", "fourth", "fifth"]
    assert {p["channel"] for p in received} == {"form"}


def test_bulk_paste_channel_override(tmp_path):
    client, db, _, _ = make_client(tmp_path)
    r = client.post("/requests/bulk", data={"text": "a\nb", "channel": "social"})
    assert r.status_code == 200, r.text
    received = [e["payload"] for e in events(db)[0] if e["type"] == "received"]
    assert {p["channel"] for p in received} == {"social"}


def test_bulk_earlier_rows_are_duplicate_candidates_for_later_rows(tmp_path):
    client, db, _, batch = make_client(tmp_path, mode="dupes")
    first = client.post("/requests", json={"raw_text": CASES["C01"]["raw_text"]})
    rows = [
        [CASES["C01"]["raw_text"], ""],
        ["unrelated, need food", ""],
        [CASES["C22"]["raw_text"], ""],
    ]
    r = client.post(
        "/requests/bulk", files={"file": ("in.csv", csv_text(rows).encode(), "text/csv")}
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    # row 1 duplicates the earlier single request; row 3 duplicates it and row 1
    assert [x["decision"]["rule_id"] for x in results] == ["R-P2-1", "R-P2-0", "R-P2-2"]
    decided = {
        e["request_id"]: e["payload"]["engine_input"]["context"]
        for e in events(db)[0]
        if e["type"] == "decided"
    }
    row3 = decided[results[2]["request_id"]]["duplicate_candidates"]
    assert [c["request_id"] for c in row3] == [first.json()["request_id"], results[0]["request_id"]]
    assert row3[0]["priority_tier"] == "P2"  # decided earlier
    assert row3[1]["priority_tier"] is None  # decided in the same batch call
    assert batch.calls == 1


def test_bulk_200_lines_under_10s_with_fake_engine(tmp_path):
    client, db, _, batch = make_client(tmp_path)
    lines = "\n".join(f"help, {i % 9 + 1} people trapped on roof, street {i}" for i in range(200))
    t = time.monotonic()
    r = client.post("/requests/bulk", data={"text": lines})
    elapsed = time.monotonic() - t
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 200
    assert elapsed < 10
    assert len(events(db)[0]) == 600
    assert batch.calls == 1


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"files": {"file": ("a.csv", b"text,contact\nhelp,\n", "text/csv")}}, "raw_text column"),
        ({"files": {"file": ("a.csv", b"raw_text,color\nhelp,red\n", "text/csv")}}, "unknown"),
        ({"files": {"file": ("a.csv", b"raw_text\nhelp\n\"\n", "text/csv")}}, "malformed CSV"),
        ({"files": {"file": ("a.csv", b"raw_text\nhelp,extra\n", "text/csv")}}, "more cells"),
        ({"files": {"file": ("a.csv", b"raw_text\n", "text/csv")}}, "no requests"),
        ({"files": {"file": ("a.csv", b"raw_text\n\xff\xfe\n", "text/csv")}}, "UTF-8"),
        (
            {"files": {"file": ("a.csv", b"raw_text,channel\nok,sms\nbad,pigeon\n", "text/csv")}},
            "line 3: channel",
        ),
        ({"data": {"text": "   \n  "}}, "'text'"),
        ({"data": {"text": "a\nb", "channel": "pigeon"}}, "channel"),
        ({"json": {"text": "a"}}, "CSV"),
        ({"content": b"", "headers": {"content-type": "text/plain"}}, "no requests"),
    ],
)
def test_bulk_invalid_returns_422_and_writes_nothing(tmp_path, kwargs, reason):
    client, db, _, batch = make_client(tmp_path)
    r = client.post("/requests/bulk", **kwargs)
    assert r.status_code == 422, r.text
    assert reason in r.json()["detail"]
    assert batch.calls == 0
    assert_nothing_written(db)


def test_bulk_one_bad_row_rejects_whole_upload(tmp_path):
    client, db, _, batch = make_client(tmp_path)
    text = "raw_text,lang_hint\nfirst ok,\nsecond ok,en\nthird,ENGLISH\n"
    r = client.post("/requests/bulk", content=text, headers={"content-type": "text/csv"})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("line 4: lang_hint")
    assert batch.calls == 0
    assert_nothing_written(db)


def test_bulk_too_many_rows(tmp_path):
    client, db, _, batch = make_client(tmp_path)
    text = "\n".join(f"help {i}" for i in range(MAX_BULK_ROWS + 1))
    r = client.post("/requests/bulk", content=text, headers={"content-type": "text/plain"})
    assert r.status_code == 422
    assert str(MAX_BULK_ROWS) in r.json()["detail"]
    assert batch.calls == 0
    assert_nothing_written(db)


@pytest.mark.parametrize("mode", ["fail", "short", "invalid"])
def test_bulk_engine_failure_writes_nothing(tmp_path, mode):
    client, db, _, _ = make_client(tmp_path, mode=mode)
    r = client.post("/requests/bulk", data={"text": "a help\nb help"})
    assert r.status_code == 502
    assert "engine" in r.json()["detail"]
    assert_nothing_written(db)


def test_module_app_exists():
    from rescue_field.api import app

    routes = {(r.path, tuple(sorted(r.methods))) for r in app.routes if hasattr(r, "methods")}
    assert ("/requests", ("POST",)) in routes
    assert ("/requests/bulk", ("POST",)) in routes
