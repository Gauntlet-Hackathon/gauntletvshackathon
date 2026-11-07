"""ECC intake simulator: GET /intake and GET /intake/log.json (#300)."""

import re
import shlex
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field import api
from rescue_field.api import create_app, default_decide_batch
from rescue_field.intake_page import intake_log, result_words
from rescue_field.store import EventStore

FAKE = str(Path(__file__).with_name("fake_engine.py"))


def _client(tmp_path, mode="ok"):
    store = EventStore(tmp_path / "f.db", check_same_thread=False)
    decide = default_decide_batch({"ENGINE_CMD": shlex.join([sys.executable, FAKE, mode])})
    return store, TestClient(create_app(store=store, decide_batch=decide))


@pytest.fixture
def client(tmp_path):
    store, c = _client(tmp_path)
    with c:
        yield c
    store.close()


def _csv_cell(v):
    """Python mirror of the page's csvCell()."""
    v = "" if v is None else str(v)
    return '"' + v.replace('"', '""') + '"' if re.search(r'[",\n\r]', v) else v


def _page_csv(items):
    """Python mirror of the page's toCSV(): what it posts to /requests/bulk."""
    rows = [
        ",".join(_csv_cell(i.get(k)) for k in ("raw_text", "channel", "contact", "lang_hint"))
        for i in items
    ]
    return "raw_text,channel,contact,lang_hint\n" + "\n".join(rows) + "\n"


def test_page_served_with_ecc_name(client, monkeypatch):
    monkeypatch.setenv("ECC_NAME", "Harris County <ECC>")
    r = client.get("/intake")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Harris County &lt;ECC&gt;" in r.text
    assert "[ECC NAME]" not in r.text


def test_page_default_ecc_name(client, monkeypatch):
    monkeypatch.delenv("ECC_NAME", raising=False)
    assert "ECC 1" in client.get("/intake").text


def test_page_has_no_external_urls_and_no_local_storage(client):
    html = client.get("/intake").text
    assert not re.search(r"https?://", html)
    assert "//fonts." not in html
    assert "localStorage" not in html
    # The log is append-only: no remove or clear buttons.
    assert "data-del" not in html and 'id="clear"' not in html
    # Every add goes to the field app on the same origin.
    assert "'/requests'" in html and "'/requests/bulk'" in html and "'/intake/log.json'" in html
    assert "{{" not in html and "{%" not in html


def test_page_validation_matches_api_limits(client):
    html = client.get("/intake").text
    assert f"MAX_TEXT = {api.MAX_TEXT_CHARS}" in html
    assert f"MAX_CONTACT = {api.MAX_CONTACT_CHARS}" in html
    assert f"MAX_ROWS = {api.MAX_BULK_ROWS}" in html
    channels = ", ".join(f"'{c}'" for c in api.CHANNELS)
    assert f"var CHANNELS = [{channels}]" in html
    assert "/^[a-z]{2}$/" in html


def test_log_empty(client):
    assert client.get("/intake/log.json").json() == {"count": 0, "requests": []}


def test_log_lists_single_and_bulk_newest_first_with_result(client):
    one = client.post(
        "/requests",
        json={
            "raw_text": "water rising at 12 Oak St",
            "channel": "sms",
            "contact": "+17135550199",
            "lang_hint": "en",
        },
    )
    assert one.status_code == 200
    items = [
        {"raw_text": "roof at 12 Oak Street, 3 people", "channel": "sms", "contact": "+1713"},
        {"raw_text": 'she said "help", then, nothing\nsecond line', "channel": "social"},
    ]
    bulk = client.post(
        "/requests/bulk",
        content=_page_csv(items).encode(),
        headers={"content-type": "text/csv"},
    )
    assert bulk.status_code == 200, bulk.text
    bulk_ids = [r["request_id"] for r in bulk.json()["results"]]

    log = client.get("/intake/log.json").json()
    assert log["count"] == 3
    reqs = log["requests"]
    assert [r["request_id"] for r in reqs] == [bulk_ids[1], bulk_ids[0], one.json()["request_id"]]
    assert reqs[0]["raw_text"] == items[1]["raw_text"]
    assert reqs[0]["channel"] == "social" and reqs[0]["contact"] is None
    assert reqs[2]["contact"] == "+17135550199"
    assert reqs[2]["lang_hint"] == "en" and reqs[2]["channel"] == "sms"
    assert all(r["result"] == "Needs help soon" for r in reqs)  # fake engine: prioritized P2
    assert all(r["received_at"] for r in reqs)

    # The pipeline ran: received, extracted and decided for each request, and the board shows them.
    store = client.app.state.store
    for rid in [one.json()["request_id"], *bulk_ids]:
        kinds = [e["type"] for e in store.request_events(rid)]
        assert {"received", "extracted", "decided"} <= set(kinds)
    board = client.get("/").text
    assert "water rising at 12 Oak St" in board


def test_scenario_csv_accepted_by_bulk(client):
    items = [
        {
            "raw_text": "grandma on oxygen telephone rd 2210 power out",
            "channel": "sms",
            "contact": "+17135550142",
            "lang_hint": None,
        },
        {
            "raw_text": "My mom’s neighbor on Telephone Rd needs help, oxygen machine",
            "channel": "social",
            "contact": "+17135550142",
            "lang_hint": None,
        },
    ]
    r = client.post(
        "/requests/bulk", content=_page_csv(items).encode(), headers={"content-type": "text/csv"}
    )
    assert r.status_code == 200 and r.json()["count"] == 2


def test_bulk_error_adds_nothing_to_log(client):
    items = [{"raw_text": "ok row", "channel": "sms"}, {"raw_text": "bad", "channel": "pigeon"}]
    r = client.post(
        "/requests/bulk", content=_page_csv(items).encode(), headers={"content-type": "text/csv"}
    )
    assert r.status_code == 422 and r.json()["row"] == 2 and "channel" in r.json()["detail"]
    assert client.get("/intake/log.json").json()["count"] == 0


def test_log_survives_a_new_app(tmp_path):
    store, c = _client(tmp_path)
    with c:
        rid = c.post("/requests", json={"raw_text": "help at Elm"}).json()["request_id"]
    store.close()
    store, c = _client(tmp_path)
    with c:
        reqs = c.get("/intake/log.json").json()["requests"]
    store.close()
    assert [r["request_id"] for r in reqs] == [rid]


def test_result_words():
    assert result_words(None) is None
    assert result_words({"outcome": "prioritized", "priority_tier": "P1"}) == "Needs help now"
    assert result_words({"outcome": "prioritized", "priority_tier": "P3"}) == "Can wait"
    assert result_words({"outcome": "abstain"}) == "Check first"
    assert result_words({"outcome": "not_a_request"}) == "Set aside"


def test_intake_log_limit_and_undecided():
    events = [
        {
            "type": "received",
            "request_id": f"R{i}",
            "occurred_at": f"t{i}",
            "payload": {"raw_text": f"m{i}", "channel": "sms", "contact": None, "lang_hint": None},
        }
        for i in range(5)
    ]
    events.append(
        {
            "type": "decided",
            "request_id": "R4",
            "payload": {
                "engine_output": {"outcome": "abstain", "follow_up_question": "Where are you?"}
            },
        }
    )
    out = intake_log(events, limit=3)
    assert [r["request_id"] for r in out] == ["R4", "R3", "R2"]
    assert out[0]["result"] == "Check first" and out[0]["follow_up_question"] == "Where are you?"
    assert out[1]["result"] is None
