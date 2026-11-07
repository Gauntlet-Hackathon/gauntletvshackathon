import re
import shlex
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field import api
from rescue_field.api import create_app, default_decide_batch
from rescue_field.store import EventStore

FAKE = str(Path(__file__).with_name("fake_engine.py"))


def fake_engine(mode="ok"):
    return default_decide_batch({"ENGINE_CMD": shlex.join([sys.executable, FAKE, mode])})


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


def client_for(store):
    return TestClient(create_app(store=store, decide_batch=fake_engine()))


def test_page_served_with_default_ecc_name(store, monkeypatch):
    monkeypatch.delenv("ECC_NAME", raising=False)
    with client_for(store) as client:
        r = client.get("/intake")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<span>ECC 1</span>" in r.text
    assert "[ECC NAME]" not in r.text


def test_page_shows_ecc_name_from_env_escaped(store, monkeypatch):
    monkeypatch.setenv("ECC_NAME", "Harris <County>")
    with client_for(store) as client:
        text = client.get("/intake").text
    assert "<span>Harris &lt;County&gt;</span>" in text
    assert "<County>" not in text


def test_page_has_no_external_urls(store):
    with client_for(store) as client:
        text = client.get("/intake").text
    assert not re.findall(r"""(?:src|href|action)\s*=\s*["']?(?:https?:)?//""", text)
    assert not re.findall(r"url\(\s*[\"']?(?:https?:)?//", text)
    assert "@import" not in text
    assert "fonts.googleapis" not in text
    assert "<link" not in text
    assert "Send to this laptop" in text
    assert "'/requests/bulk'" in text


def test_page_limits_match_api(store):
    with client_for(store) as client:
        text = client.get("/intake").text
    assert f"MAX_TEXT = {api.MAX_TEXT_CHARS}" in text
    assert f"MAX_ROWS = {api.MAX_BULK_ROWS}" in text
    assert "['sms', 'social', 'form', 'call_note', 'csv']" in text
    assert list(api.CHANNELS) == ["sms", "social", "form", "call_note", "csv"]
    assert "/^[a-z]{2}$/" in text


def page_csv(rows):
    """The CSV the page's toCSV() builds: header, then one quoted-as-needed line per entry."""

    def cell(v):
        v = "" if v is None else str(v)
        return '"' + v.replace('"', '""') + '"' if re.search(r'[",\n\r]', v) else v

    lines = ["raw_text,channel,contact,lang_hint"]
    keys = ("raw_text", "channel", "contact", "lang_hint")
    lines += [",".join(cell(r.get(k)) for k in keys) for r in rows]
    return "\n".join(lines) + "\n"


def test_send_to_this_laptop_adds_requests(store):
    rows = [
        {
            "raw_text": "water coming in fast 2210 telephone rd, 3 of us",
            "channel": "sms",
            "contact": "+17135550142",
            "lang_hint": "en",
        },
        {"raw_text": 'help, "trapped" at 14 Elm Street', "channel": "social"},
    ]
    with client_for(store) as client:
        r = client.post(
            "/requests/bulk",
            files={"file": ("communityrescue-intake.csv", page_csv(rows), "text/csv")},
        )
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 2
    assert len(store.all_events()) >= 2


def test_send_error_reports_row(store):
    rows = [
        {"raw_text": "ok at 14 Elm Street", "channel": "sms"},
        {"raw_text": "bad channel", "channel": "pigeon"},
    ]
    with client_for(store) as client:
        r = client.post("/requests/bulk", files={"file": ("x.csv", page_csv(rows), "text/csv")})
    assert r.status_code == 422
    body = r.json()
    assert body["row"] == 2 and "channel" in body["detail"]
