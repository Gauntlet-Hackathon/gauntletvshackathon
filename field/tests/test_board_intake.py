import re
import shlex
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app, default_decide_batch
from rescue_field.store import EventStore

FAKE = str(Path(__file__).with_name("fake_engine.py"))


@pytest.fixture
def client(tmp_path):
    store = EventStore(tmp_path / "f.db", check_same_thread=False)
    decide = default_decide_batch({"ENGINE_CMD": shlex.join([sys.executable, FAKE, "ok"])})
    with TestClient(create_app(store=store, decide_batch=decide)) as c:
        yield c
    store.close()


def rows(html):
    return len(re.findall(r'class="row"', html))


def test_page_has_three_labelled_intake_forms(client):
    html = client.get("/board").text
    forms = re.findall(r"<form\b[^>]*>", html)
    assert len(forms) == 3
    assert any('action="/requests"' in f for f in forms)
    assert sum('action="/requests/bulk"' in f for f in forms) == 2
    assert 'enctype="multipart/form-data"' in html
    for field in ("single-raw-text", "single-contact", "single-channel", "paste-text", "csv-file"):
        assert f'<label for="{field}">' in html
        assert f'id="{field}"' in html
    for name in (
        'name="raw_text"',
        'name="contact"',
        'name="channel"',
        'name="text"',
        'name="file"',
    ):
        assert name in html
    assert 'role="alert"' in html


def test_fragment_after_bulk_post_has_new_rows(client):
    before = rows(client.get("/board", headers={"HX-Request": "true"}).text)
    r = client.post(
        "/requests/bulk",
        content=b"help at Elm\nwater at Main\nattic at Oak\n",
        headers={"content-type": "text/plain"},
    )
    assert r.status_code == 200 and r.json()["count"] == 3
    frag = client.get("/board", headers={"HX-Request": "true"})
    assert "<html" not in frag.text
    assert rows(frag.text) > before


def test_bad_csv_returns_json_error_for_script(client):
    r = client.post("/requests/bulk", files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "invalid_input" and body["detail"]
