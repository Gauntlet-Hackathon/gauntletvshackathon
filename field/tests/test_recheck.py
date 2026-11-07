"""UI-9: POST /requests/{id}/recheck re-runs the engine on a stored decision and compares."""

import pytest
from fastapi.testclient import TestClient

from rescue_field.api import create_app
from rescue_field.recheck import VERSION_MISMATCH
from rescue_field.store import EventStore
from tests.test_api_intake import fake_engine
from tests.test_board import RULES, add, output

OTHER_RULES = "rules@0000000+bin:0000000000000000"


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


def post(store, rid, engine="ok"):
    with TestClient(create_app(store=store, decide_batch=fake_engine(engine))) as client:
        return client.post(f"/requests/{rid}/recheck")


def test_same_fields_and_rules_version_give_same_result(store):
    # fake_engine "ok" answers P2 / R-P2-01 with the RULES version.
    add(store, "R-1", output("P2", "R-P2-01"))
    before = len(store.all_events())
    r = post(store, "R-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["same"] is True
    assert body["differences"] == []
    assert body["stored"] == body["now"]
    assert body["stored"]["priority_tier"] == "P2"
    assert set(body["stored"]) == {
        "outcome",
        "priority_tier",
        "provisional_tier",
        "rule_id",
        "duplicate_of",
        "follow_up_question",
    }
    assert len(store.all_events()) == before  # writes no event


def test_changed_output_is_reported(store):
    add(store, "R-1", output("P1", "R-P1-02"))
    body = post(store, "R-1").json()
    assert body["same"] is False
    assert body["stored"]["priority_tier"] == "P1"
    assert body["now"]["priority_tier"] == "P2"
    assert body["differences"] == ["Priority tier: P1, now P2", "Rule: R-P1-02, now R-P2-01"]


def test_rules_version_mismatch_is_not_compared(store):
    add(store, "R-1", output("P1", "R-P1-02"), rules=OTHER_RULES)
    body = post(store, "R-1").json()
    assert body["same"] is None
    assert body["detail"] == VERSION_MISMATCH
    assert body["stored_rules_version"] == OTHER_RULES
    assert body["now_rules_version"] == RULES


def test_unknown_request_is_404(store):
    r = post(store, "nope")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_engine_failure_is_502(store):
    add(store, "R-1", output("P2", "R-P2-01"))
    r = post(store, "R-1", engine="fail")
    assert r.status_code == 502
    assert r.json()["error"] == "engine_failed"


def test_board_shows_check_again_button(store):
    add(store, "R-1", output("P2", "R-P2-01"))
    add(store, "R-2", output(None, "R-ABS-REQ", "abstain", question="Is this a request?"))
    with TestClient(create_app(store=store)) as client:
        html = client.get("/board").text
    assert 'class="recheck-btn" data-request-id="R-1"' in html
    assert 'class="recheck-btn" data-request-id="R-2"' in html
    assert "/recheck" in html  # the click handler
