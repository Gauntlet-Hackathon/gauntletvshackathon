"""Review findings A14-A18 end to end: the default no-model extractor plus the real fallback
engine, through the real app, as in test_demo_challenges.py."""

import json
import os
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field import api
from rescue_field.pipeline import IntakeItem, process_batch
from rescue_field.store import EventStore

SEED_TXT = Path(__file__).parents[1] / "demo" / "seed_requests.txt"


def real_decider():
    env = {k: v for k, v in os.environ.items() if k != "ENGINE_CMD"}
    return api.default_decide_batch(env)  # python -m rescue_engine.fallback


@pytest.fixture
def client(tmp_path):
    store = EventStore(tmp_path / "f.db", check_same_thread=False)
    with TestClient(api.create_app(store=store, decide_batch=real_decider())) as c:
        yield c
    store.close()


def post(client, text, contact=None):
    body = {"raw_text": text, "channel": "sms"}
    if contact:
        body["contact"] = contact
    r = client.post("/requests", json=body)
    assert r.status_code == 200, r.text
    return r.json()["decision"]


# ---- A14: house numbers and past events are not stale posts ----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "stuck at home in 2014 Elm Street with my grandma, need help",
        "help, my mom is in a wheelchair at 33 Oak Street, last year we evacuated but now we can't",
        "trapped in 2015 Westheimer Rd, water rising, please help",
    ],
)
def test_live_requests_with_years_are_not_dropped(client, text):
    d = post(client, text)
    assert d["outcome"] == "prioritized", d


def test_trapped_on_westheimer_is_p1(client):
    d = post(client, "trapped in 2015 Westheimer Rd, water rising, please help")
    assert (d["outcome"], d["priority_tier"]) == ("prioritized", "P1"), d


def test_old_post_with_a_year_is_still_not_a_request(client):
    assert post(client, "this is an old post from 2017, we needed help")["outcome"] == (
        "not_a_request"
    )


# ---- A15: SMS shorthand and danger-only phrasing are requests ---------------------------


def test_plz_hlp_is_a_request(client):
    text = (
        "plz hlp the car is flooded & wi cannot get out @ 4699 lakeview court "
        "2 of us including a baby"
    )
    d = post(client, text)
    assert d["outcome"] == "prioritized", d


@pytest.mark.parametrize(
    "text",
    [
        "SOS 12 Oak Street on the roof water rising",
        "Water rising fast, we are on the roof at 12 Oak Street, 3 people",
    ],
)
def test_roof_and_rising_water_is_p1(client, text):
    d = post(client, text)
    assert (d["outcome"], d["priority_tier"]) == ("prioritized", "P1"), d


@pytest.mark.parametrize(
    "text, rule",
    [
        ("help, oxygen machine has no power at 12 Oak Street", "R-P1-03"),
        ("my dad is unconscious and not breathing at 12 Oak Street, please help", "R-P1-02"),
        ("help at 12 Oak Street, someone drowning", "R-P1-02"),
    ],
)
def test_acute_medical_is_p1(client, text, rule):
    d = post(client, text)
    assert (d["outcome"], d["priority_tier"], d["rule_id"]) == ("prioritized", "P1", rule), d


# ---- A18: a clear request with a hoax-marker word, and address capture ------------------


def test_clear_request_saying_not_fake_is_prioritized(client):
    d = post(client, "need help at 12 Oak Street, this is not fake, grandpa is bedridden")
    assert d["outcome"] == "prioritized", d
    assert d["rule_id"] != "R-ABS-HOAX"


def test_fake_request_without_location_is_not_p1(client):
    d = post(client, "need help, this is fake lol")
    assert not (d["outcome"] == "prioritized" and d["priority_tier"] == "P1"), d


def test_count_words_are_not_an_address(client, tmp_path):
    post(client, "3 people at Main St need help water rising")
    store = EventStore(tmp_path / "f.db")
    extracted = [e for e in store.all_events() if e["type"] == "extracted"]
    store.close()
    assert extracted
    assert extracted[-1]["payload"]["location"]["text"] != "3 people at Main St"


def test_seed_file_has_no_low_confidence_request_abstains(tmp_path):
    store = EventStore(tmp_path / "seed.db")
    lines = SEED_TXT.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200
    rows = process_batch(
        [IntakeItem(raw=ln, channel="sms") for ln in lines],
        store=store,
        decide_batch=real_decider(),
    )
    outs = [r[2]["payload"]["engine_output"] for r in rows]
    reasons = Counter(o["abstain_reason"] for o in outs if o["outcome"] == "abstain")
    assert reasons["low_confidence_request"] == 0, json.dumps(reasons)
