import json
import shlex
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app, default_decide_batch
from rescue_field.screens import grader
from rescue_field.store import EventStore

FAKE = str(Path(__file__).with_name("fake_engine.py"))
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("GRADER_MODE", raising=False)
    monkeypatch.delenv("CLOUD_URL", raising=False)
    store = EventStore(tmp_path / "f.db", check_same_thread=False)
    decide = default_decide_batch({"ENGINE_CMD": shlex.join([sys.executable, FAKE, "ok"])})
    with TestClient(create_app(store=store, decide_batch=decide)) as c:
        yield c
    store.close()


SCREENS = ("/app", "/app?mode=respond")


def test_badge_and_help_only_in_grader_mode(client, monkeypatch):
    for url in SCREENS:
        html = client.get(url).text
        assert 'id="grader-badge"' not in html and 'id="grader-help"' not in html
        assert "grader.js" not in html and "grader.css" not in html
    monkeypatch.setenv("GRADER_MODE", "1")
    for url in SCREENS:
        html = client.get(url).text
        assert 'id="grader-badge"' in html and "Grader mode" in html
        assert 'id="grader-help"' in html
        assert "/static/grader.js" in html and "/static/grader.css" in html


def test_settings_link_and_cookie(client):
    assert 'id="grader-badge"' in client.get("/app?grader=1").text
    assert 'id="grader-badge"' not in client.get("/app").text  # the JS sets the cookie
    r = client.get("/grader/mode?on=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert 'id="grader-badge"' in client.get("/app").text
    assert 'id="grader-badge"' not in client.get("/app?grader=0").text
    client.get("/grader/mode?on=0")
    assert 'id="grader-badge"' not in client.get("/app").text


def test_mode_redirect_stays_local(client):
    r = client.get("/grader/mode?on=1&next=//evil.example", follow_redirects=False)
    assert r.headers["location"] == "/"


def test_static_files_served(client):
    for path in ("/static/grader.js", "/static/grader.css"):
        r = client.get(path)
        assert r.status_code == 200 and r.text


def test_hints_cover_every_use_case_with_three_levels(client):
    body = client.get("/grader/hints.json").json()
    ids = [uc["id"] for uc in body["use_cases"]]
    assert ids == [f"UC-{n:02d}" for n in range(16)]
    for uc in body["use_cases"]:
        assert [h["level"] for h in uc["hints"]] == [1, 2, 3], uc["id"]
        assert [h["label"] for h in uc["hints"]] == list(grader.LEVELS)
        assert all(h["text"].strip() for h in uc["hints"]), uc["id"]
    assert body["scoreboard_url"] is None


def test_hint_texts_match_use_cases_doc():
    doc = (REPO / "docs/design/communityrescue/use-cases.md").read_text()
    for uc in grader.USE_CASES:
        assert uc["hints"][0]["text"] in doc, uc["id"]
        assert uc["hints"][2]["text"] in doc, uc["id"]


def test_scoreboard_url_from_cloud_url(monkeypatch, client):
    monkeypatch.setenv("CLOUD_URL", "http://cloud:4000/")
    assert client.get("/grader/hints.json").json()["scoreboard_url"] == "http://cloud:4000/evals"


def _samples():
    return [
        (uc["id"], h["sample"])
        for uc in grader.USE_CASES
        for h in uc["hints"]
        if h.get("sample")
    ]


def test_flood_sample_is_fifty_lines():
    assert len(grader.FLOOD_TEXT.splitlines()) == 50
    assert all(line.strip() for line in grader.FLOOD_TEXT.splitlines())


def test_samples_are_synthetic():
    """No tweet text: no links, handles or hashtags, and contacts are 555 numbers."""
    for uc_id, sample in _samples():
        texts = [i["raw_text"] for i in sample.get("items", [])] + [sample.get("text") or ""]
        for t in texts:
            assert "http" not in t and "#" not in t and "@" not in t, uc_id
        for item in sample.get("items", []):
            if "contact" in item:
                assert item["contact"].startswith("+1713555"), uc_id


def test_each_sample_posts_through_intake(client):
    """The same requests grader.js sends: only the normal intake and status routes."""
    seen = set()
    for uc_id, sample in _samples():
        kind = sample["kind"]
        seen.add(kind)
        if kind == "bulk":
            r = client.post(
                "/requests/bulk", json={"text": sample["text"], "channel": sample["channel"]}
            )
            assert r.status_code == 200, (uc_id, r.text)
            assert r.json()["count"] == len(sample["text"].splitlines())
        elif kind == "requests":
            ids = []
            for item in sample["items"]:
                r = client.post("/requests", json=item)
                assert r.status_code == 200, (uc_id, r.text)
                ids.append(r.json()["request_id"])
            if "then" in sample:
                r = client.post(f"/requests/{ids[-1]}/status", json=sample["then"])
                assert r.status_code == 200, (uc_id, r.text)
        else:
            assert kind in ("offline", "scoreboard"), uc_id
    assert seen == {"bulk", "requests", "offline", "scoreboard"}


def test_js_uses_only_normal_routes():
    js = (REPO / "field/rescue_field/static/grader.js").read_text()
    posts = set(__import__("re").findall(r"post\('([^']+)'", js))
    assert posts == {"/sync/offline-toggle", "/requests/bulk", "/requests", "/requests/"}
    assert "http://" not in js and "https://" not in js


def test_hints_json_is_plain_data():
    json.dumps(grader.USE_CASES)
