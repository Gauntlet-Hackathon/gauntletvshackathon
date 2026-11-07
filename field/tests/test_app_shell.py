import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from rescue_field.api import create_app
from rescue_field.shell import connection_line, initials, shell_config
from rescue_field.store import EventStore


def client(banner=None):
    app = create_app(store=EventStore(":memory:", check_same_thread=False))
    if banner:
        app.state.extractor_info = {
            "expected_model": "hf:x@1",
            "extractor": "keyword",
            "fallback_reason": banner,
        }
    return TestClient(app)


def test_shell_renders_without_banner():
    r = client().get("/app")
    assert r.status_code == 200
    assert "model-banner" not in r.text
    assert "Queue" in r.text and "Respond" in r.text
    assert "Not connected to the ECC · 0 updates saved on this laptop" in r.text
    assert "ECC 1" in r.text and "Volunteer" in r.text


def test_shell_renders_with_banner_on_both_modes():
    c = client("weights missing")
    for url in ("/app", "/app?mode=respond"):
        assert "Real model not loaded, using keyword fallback: weights missing" in c.get(url).text


def test_no_external_urls():
    html = client("x").get("/app").text
    css = client().get("/static/app.css").text
    assert not re.search(r"""(?:src|href)=["'](?:https?:)?//""", html)
    assert not re.search(r"url\(\s*[\"']?(?:https?:)?//", css)


def test_static_files_served():
    c = client()
    for path in (
        "static/fonts/atkinson-hyperlegible-regular.woff2",
        "static/fonts/atkinson-hyperlegible-bold.woff2",
        "static/fonts/OFL.txt",
        "static/icons/communityrescue-icon.svg",
        "static/icons/communityrescue-icon-180.png",
        "static/icons/communityrescue-icon-64.png",
        "static/icons/communityrescue-icon-32.png",
        "static/app.css",
    ):
        r = c.get("/" + path)
        assert r.status_code == 200 and r.content, path


def test_env_defaults_and_overrides():
    d = shell_config({"DEVICE_ID": "laptop-dev-01"})
    assert d["ecc_name"] == "ECC 1" and d["volunteer_role"] == "Volunteer"
    assert re.fullmatch(r"[A-Z][a-z]+ [A-Z]\.", d["volunteer_name"])
    assert d["volunteer_name"] == shell_config({"DEVICE_ID": "laptop-dev-01"})["volunteer_name"]
    assert d["shelter_names"][0] == "Shelter 1 (Lincoln High)"
    o = shell_config(
        {
            "ECC_NAME": "ECC 9",
            "VOLUNTEER_NAME": "Pat Q.",
            "VOLUNTEER_ROLE": "Boat lead",
            "SHELTER_NAMES": "A, B",
        }
    )
    assert (o["ecc_name"], o["volunteer_name"], o["volunteer_role"], o["shelter_names"]) == (
        "ECC 9", "Pat Q.", "Boat lead", ["A", "B"])


def test_connection_lines():
    now = datetime(2026, 11, 7, 12, 2, 0, tzinfo=UTC)
    online = {"state": "online", "last_sync_at": "2026-11-07T12:00:00Z", "backlog": 0}
    assert connection_line(online, now)["text"] == "Online · last sync 2 min ago"
    offline = connection_line({"state": "offline", "backlog": 3})
    assert offline["text"] == "No Internet: Local Store, Sync when online · 3 updates waiting"
    nosync = connection_line({"state": "no-sync", "backlog": 1})
    assert nosync["text"] == "Not connected to the ECC · 1 update saved on this laptop"
    busy = {"state": "online", "last_sync_at": "2026-11-07T12:00:00Z", "backlog": 4}
    assert connection_line(busy, now)["text"] == "Online · last sync 2 min ago · 4 updates to send"


def test_initials():
    assert initials("Sam K.") == "SK"
    assert initials("Jordan Lee Kim") == "JK"
    assert initials("riley") == "R"
    assert shell_config({"VOLUNTEER_NAME": "Pat Q."})["initials"] == "PQ"


def test_header_who_block_and_segmented_toggle():
    html = client().get("/app?mode=respond").text
    assert '<span class="avatar" aria-hidden="true">' in html
    assert '<span class="who-sub">Volunteer</span><span class="who-sub">ECC 1</span>' in html
    assert '<a href="/respond" class="selected" aria-current="page">Respond</a>' in html
    css = client().get("/static/app.css").text
    assert "#E9EBEF" in css and "#0A3A8C" in css
