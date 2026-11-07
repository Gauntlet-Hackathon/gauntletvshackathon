"""Every field page carries the same CommunityRescue logo and favicon."""

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import app

PAGES = ["/", "/app", "/app?mode=respond", "/board", "/intake", "/add", "/boats"]


@pytest.mark.parametrize("path", PAGES)
def test_page_has_brand_favicon_and_logo(path, tmp_path, monkeypatch):
    monkeypatch.setenv("FIELD_DATA_DIR", str(tmp_path))
    with TestClient(app) as client:
        r = client.get(path)
    assert r.status_code == 200, path
    html = r.text
    assert 'rel="icon"' in html and "/static/icons/communityrescue-icon" in html
    assert 'rel="apple-touch-icon" href="/static/icons/communityrescue-icon-180.png"' in html


ICONS = ["communityrescue-icon.svg", "communityrescue-icon-32.png", "communityrescue-icon-180.png"]


@pytest.mark.parametrize("name", ICONS)
def test_icon_files_are_served(name, tmp_path, monkeypatch):
    monkeypatch.setenv("FIELD_DATA_DIR", str(tmp_path))
    with TestClient(app) as client:
        assert client.get(f"/static/icons/{name}").status_code == 200
