"""UI-10: Spanish lines for the engine's follow-up questions, shown only for Spanish requests."""

import re

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app
from rescue_field.store import EventStore
from rescue_field.words_es import SPANISH, is_spanish, spanish_for
from rescue_shared.contracts_io import CONTRACTS
from rescue_shared.ulid import new_ulid
from tests.test_board import extracted, output

RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
MODEL = "hf:rescue-org/rescue-tagger@4d5e6f7a8b"
Q_LOC = "What's the street address, or the nearest cross streets or landmark?"
Q_CONFIRM = (
    "Please confirm: is anyone in immediate danger (rising water, injury, fire, trapped), "
    "and is a child, elderly or disabled person with you?"
)
ES_TEXT = "Necesitamos ayuda, el agua está subiendo en la calle Elm, somos 4"
EN_TEXT = "We need help, water rising on Elm St, 4 of us"


def contract_questions() -> list[str]:
    """The exact strings in the "Follow-up questions (exact strings)" table of engine_rules.md."""
    text = (CONTRACTS / "engine_rules.md").read_text()
    section = text.split("## Follow-up questions (exact strings)", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|")][2:]
    return [re.findall(r"`([^`]*)`", row)[-1] for row in rows]


def test_every_engine_question_has_a_spanish_line():
    questions = contract_questions()
    assert len(questions) == 7
    assert set(questions) == set(SPANISH)
    for q in questions:
        es = spanish_for(q)
        assert es and es != q


def test_unknown_question_has_no_spanish_line():
    assert spanish_for("Something else?") is None
    assert spanish_for(None) is None


@pytest.mark.parametrize(
    ("hint", "text", "expected"),
    [
        ("es", "help", True),
        ("ES", "help", True),
        ("en", ES_TEXT, True),  # a wrong hint never hides the Spanish line
        ("en", EN_TEXT, False),
        (None, ES_TEXT, True),
        (None, EN_TEXT, False),
        (None, "Please help, our casa is on Main St", False),  # one stray word is not enough
        (None, "Please do us a favor, hay is in the barn", False),
        (None, None, False),
    ],
)
def test_is_spanish(hint, text, expected):
    assert is_spanish(hint, text) is expected


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


def add(store, rid, out, raw, lang_hint=None):
    ext = extracted()
    payloads = [
        ("received", {"channel": "sms", "raw_text": raw, "lang_hint": lang_hint, "contact": None}),
        ("extracted", ext),
        ("decided", {"engine_input": {"extracted": ext, "context": {}}, "engine_output": out}),
    ]
    store.append_events(
        {
            "event_id": new_ulid(),
            "request_id": rid,
            "type": kind,
            "device_id": "field-01",
            "occurred_at": "2026-11-07T10:00:00Z",
            "rules_version": RULES,
            "model_version": MODEL,
            "payload": payload,
        }
        for kind, payload in payloads
    )


def board_html(store) -> str:
    with TestClient(create_app(store=store)) as client:
        r = client.get("/board", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text
    return r.text


def test_spanish_shown_under_english_in_follow_up_queue(store):
    add(store, "R-ES", output(None, "R-ABS-LOC", "abstain", question=Q_LOC), ES_TEXT)
    add(store, "R-EN", output(None, "R-ABS-LOC", "abstain", question=Q_LOC), EN_TEXT)
    html = board_html(store)
    assert html.count(Q_LOC.replace("'", "&#39;")) == 2  # English kept on both rows
    assert html.count("En español: " + SPANISH[Q_LOC]) == 1  # Spanish only on the Spanish one


def test_spanish_shown_on_tier_row_question_by_hint(store):
    add(store, "R-1", output("P1", "R-P1-01", question=Q_CONFIRM), "help", lang_hint="es")
    html = board_html(store)
    assert Q_CONFIRM in html
    assert "En español: " + SPANISH[Q_CONFIRM] in html


def test_no_spanish_for_english_tier_row(store):
    add(store, "R-1", output("P1", "R-P1-01", question=Q_CONFIRM), EN_TEXT, lang_hint="en")
    html = board_html(store)
    assert Q_CONFIRM in html
    assert "En español" not in html
