"""Grader mode (UI-8): badge and ? only in grader mode, hint data, samples through intake."""

import re
import shlex
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app, default_decide_batch
from rescue_field.grader import FLOOD_LINES, USE_CASES, grader_mode, scoreboard_url
from rescue_field.store import EventStore

FAKE = str(Path(__file__).with_name("fake_engine.py"))
UC_IDS = [f"UC-{n:02d}" for n in range(16)]


def make_client(store=None):
    store = store or EventStore(":memory:", check_same_thread=False)
    decide = default_decide_batch({"ENGINE_CMD": shlex.join([sys.executable, FAKE, "ok"])})
    return TestClient(create_app(store=store, decide_batch=decide)), store


@pytest.fixture(autouse=True)
def _no_grader_env(monkeypatch):
    monkeypatch.delenv("GRADER_MODE", raising=False)
    monkeypatch.delenv("CLOUD_URL", raising=False)


def has_grader(html):
    return all(s in html for s in ('id="grader-badge"', 'id="grader-help"', "/static/grader.js"))


def has_no_grader(html):
    return not any(s in html for s in ("grader-badge", "grader-help", "grader.js", "grader.css"))


@pytest.mark.parametrize("path", ["/app", "/app?mode=respond", "/add", "/boats"])
def test_off_for_volunteers(path):
    c, _ = make_client()
    r = c.get(path)
    assert r.status_code == 200
    assert has_no_grader(r.text)


@pytest.mark.parametrize("path", ["/app", "/add", "/boats"])
def test_on_with_env(monkeypatch, path):
    monkeypatch.setenv("GRADER_MODE", "1")
    c, _ = make_client()
    html = c.get(path).text
    assert has_grader(html) and "Grader mode" in html and "/static/grader.css" in html


def test_on_with_query_and_remembered_cookie():
    c, _ = make_client()
    assert has_grader(c.get("/app?grader=1").text)
    c.cookies.set("grader", "1")
    assert has_grader(c.get("/app").text)
    off = c.get("/app?grader=0").text
    assert has_no_grader(off) and "grader=; Max-Age=0" in off


def test_grader_mode_rules():
    assert grader_mode({}, {}, {"GRADER_MODE": "1"})
    assert grader_mode({"grader": "0"}, {}, {"GRADER_MODE": "1"})
    assert not grader_mode({}, {}, {})
    assert not grader_mode({}, {}, {"GRADER_MODE": "0"})
    assert grader_mode({"grader": "1"}, {}, {})
    assert grader_mode({}, {"grader": "1"}, {})
    assert not grader_mode({"grader": "0"}, {"grader": "1"}, {})


def test_static_files_served():
    c, _ = make_client()
    for path in ("/static/grader.js", "/static/grader.css"):
        r = c.get(path)
        assert r.status_code == 200 and r.content


def test_hints_cover_every_use_case_with_three_levels():
    c, store = make_client()
    data = c.get("/grader/hints.json").json()
    assert [u["id"] for u in data["use_cases"]] == UC_IDS
    for uc in data["use_cases"]:
        assert uc["title"] and uc["try"] and uc["expect"], uc["id"]
        s = uc["sample"]
        assert s["label"] and s["note"], uc["id"]
        assert s["kind"] in ("requests", "bulk", "scoreboard", "none"), uc["id"]
    assert store.all_events() == []  # hints write nothing


def test_scoreboard_url_from_cloud_url(monkeypatch):
    assert scoreboard_url({}) is None
    assert scoreboard_url({"CLOUD_URL": "https://cloud.example/"}) == "https://cloud.example/evals"
    monkeypatch.setenv("CLOUD_URL", "http://cloud:4000")
    c, _ = make_client()
    assert c.get("/grader/hints.json").json()["scoreboard_url"] == "http://cloud:4000/evals"
    assert next(u for u in USE_CASES if u["id"] == "UC-14")["sample"]["kind"] == "scoreboard"


def test_samples_are_synthetic():
    texts = list(FLOOD_LINES)
    for uc in USE_CASES:
        texts += [r["raw_text"] for r in uc["sample"].get("requests", [])]
        for r in uc["sample"].get("requests", []):
            assert r["contact"] is None or re.fullmatch(r"\+1713555\d{4}", r["contact"])
    assert len(FLOOD_LINES) == 50
    for t in texts:
        assert not re.search(r"https?://|t\.co/|@\w|#\w", t), t
    for t in texts:
        for phone in re.findall(r"\d{3}-\d{3}-\d{4}", t):
            assert phone[4:7] == "555", t


@pytest.mark.parametrize(
    "uc",
    [u for u in USE_CASES if u["sample"]["kind"] in ("requests", "bulk")],
    ids=lambda u: u["id"],
)
def test_each_sample_posts_through_intake(uc):
    c, store = make_client()
    s = uc["sample"]
    with c:
        if s["kind"] == "bulk":
            r = c.post("/requests/bulk", json=s["bulk"])
            assert r.status_code == 200, r.text
            assert r.json()["count"] == len(s["bulk"]["text"].splitlines())
            n = r.json()["count"]
        else:
            for item in s["requests"]:
                r = c.post("/requests", json=item)
                assert r.status_code == 200, r.text
            n = len(s["requests"])
    received = [e for e in store.all_events() if e["type"] == "received"]
    assert len(received) == n
