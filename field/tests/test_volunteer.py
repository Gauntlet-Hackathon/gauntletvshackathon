import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field import words
from rescue_field.api import create_app
from rescue_field.board import build_board
from rescue_field.store import EventStore
from rescue_field.volunteer import build_queue
from rescue_shared.contracts_io import CONTRACTS
from rescue_shared.ulid import new_ulid
from tests.test_board import MODEL, RULES, add, extracted, output


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


def page(store, path="/", **kw):
    with TestClient(create_app(store=store)) as client:
        r = client.get(path, **kw)
    assert r.status_code == 200, r.text
    return r.text


def status(store, rid, kind):
    store.append_events(
        [
            {
                "event_id": new_ulid(),
                "request_id": rid,
                "type": kind,
                "device_id": "field-01",
                "occurred_at": "2026-11-07T12:00:00Z",
                "rules_version": RULES,
                "model_version": MODEL,
                "payload": {},
            }
        ]
    )


def seed(store):
    add(store, "R3", output("P3", "R-P3-01"), extracted(location="loc-three"))
    add(store, "R1", output("P1", "R-P1-01"), extracted(location="loc-one"))
    add(store, "R2", output("P2"), extracted(location="loc-two"))


def test_every_rule_id_has_a_sentence():
    text = (CONTRACTS / "engine_rules.md").read_text()
    ids = set(re.findall(r"R-(?:P[123]|ABS|DUP|NAR)-[A-Z0-9]+", text))
    assert ids and ids <= set(words.RULE_SENTENCES)
    assert all(words.RULE_SENTENCES[i].endswith(".") for i in ids)


def test_pills():
    assert words.pill_text("P1") == "Needs help now"
    assert words.pill_text("P2") == "Needs help soon"
    assert words.pill_text("P3") == "Can wait"
    assert words.pill_text(None) == "Check first"
    assert words.pill_text("P1", provisional=True) == "maybe Needs help now"


def test_next_equals_first_board_tier_row(store):
    seed(store)
    q = build_queue(build_board(store))
    board = build_board(store)
    first = next(r for t in board["tiers"] for r in t["rows"])
    assert q["next"][0]["request_id"] == first["request_id"] == "R1"
    assert [c["request_id"] for c in q["next"]] == ["R1", "R2", "R3"]
    assert [c["request_id"] for c in q["waiting"]] == ["R2", "R3"]


def test_escalated_first_and_assigned_out_now(store):
    seed(store)
    status(store, "R3", "escalated")
    status(store, "R2", "assigned")
    q = build_queue(build_board(store))
    assert [c["request_id"] for c in q["next"]] == ["R3", "R1"]
    assert q["next"][0]["escalated"]
    assert [c["request_id"] for c in q["out_now"]] == ["R2"]
    html = page(store)
    assert "Couldn't get there" in html


def test_set_aside_and_repeats_never_in_next(store):
    add(store, "N", output(None, "R-NAR-01", "not_a_request"), extracted(location="loc-n"))
    add(store, "P", output("P2"), extracted(location="loc-p"), at="2026-11-07T10:00:00Z")
    add(
        store,
        "D",
        output("P2", "R-DUP-01", "duplicate", duplicate_of="P"),
        extracted(location="loc-p"),
        at="2026-11-07T10:05:00Z",
    )
    q = build_queue(build_board(store))
    assert [c["request_id"] for c in q["next"]] == ["P"]
    assert q["next"][0]["more"] == "+1 other post"
    assert [r["request_id"] for r in q["set_aside"]] == ["N"]
    html = page(store)
    assert html.count('class="card req"') == 1
    assert "Set aside (1)" in html


def test_check_first_question_exact_and_no_send_form(store):
    qtext = "Can you confirm this request is real & tell us <exactly> what help you need?"
    ab = output(None, "R-ABS-HOAX", "abstain", question=qtext)
    ab["provisional_tier"] = "P1"
    add(store, "AB", ab, extracted(location="loc-ab"))
    q = build_queue(build_board(store))
    card = q["check_first"][0]
    assert card["question"] == qtext and card["pill"] == "Check first"
    html = page(store)
    assert "real &amp; tell us &lt;exactly&gt; what help you need?" in html
    assert "maybe Needs help now" in html and "pill-maybe" in html
    assert "<form" not in html and "Send" not in html
    assert "Check again" not in html  # no recheck route yet


def test_no_internal_details_in_html(store):
    seed(store)
    html = page(store)
    assert not re.search(r"R-(P\d|ABS|DUP|NAR)-", html)
    assert RULES not in html and MODEL not in html and "rules@" not in html
    assert "confidence" not in html.lower()
    assert "Water is rising and people are trapped" in html


def test_fragment_and_board_route(store):
    seed(store)
    frag = page(store, headers={"HX-Request": "true"})
    assert "<html" not in frag and 'id="next"' in frag
    assert "Rescue triage board" in page(store, "/board")
    assert 'id="queue"' in page(store, "/app")


def test_skip_is_client_side_only():
    src = Path(__file__).parent.parent / "rescue_field" / "templates" / "app" / "queue.html"
    js = src.read_text()
    assert "skipped" in js and "POST" not in js.replace('{method: "POST"}', "")
