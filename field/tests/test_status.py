import pytest
from fastapi.testclient import TestClient
from field.tests.test_board import add, output

from rescue_field.api import create_app
from rescue_field.store import EventStore


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    add(s, "01JC8Y00000000000000000001", output("P1", "R-P1-01"))
    yield s
    s.close()


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


RID = "01JC8Y00000000000000000001"
BODIES = {
    "assigned": {"status": "assigned", "crew": "boat-2"},
    "escalated": {"status": "escalated", "reason": "needs helicopter"},
    "rescued": {"status": "rescued", "rescued_count": 2},
}


def post(client, name, rid=RID, **extra):
    return client.post(f"/requests/{rid}/status", json={**BODIES[name], **extra})


@pytest.mark.parametrize("name", list(BODIES))
def test_accepts_each_status_and_appends_event(client, store, name):
    r = post(client, name)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == name
    last = store.all_events()[-1]
    assert last["type"] == name and last["request_id"] == RID
    assert store.pending_outbox()[-1]["event_id"] == r.json()["event_id"]


def test_backward_and_repeat_return_409(client, store):
    assert post(client, "assigned").status_code == 200
    assert post(client, "rescued").status_code == 200
    n = len(store.all_events())
    assert post(client, "assigned").status_code == 409
    assert post(client, "escalated").status_code == 409
    assert post(client, "rescued").status_code == 409
    assert len(store.all_events()) == n


def test_assigned_to_escalated_to_rescued_ok(client):
    for name in ("assigned", "escalated", "rescued"):
        assert post(client, name).status_code == 200
    assert post(client, "escalated").status_code == 409


def test_validation_and_unknown(client):
    assert client.post(f"/requests/{RID}/status", json={"status": "closed"}).status_code == 422
    assert client.post(f"/requests/{RID}/status", json={"status": "assigned"}).status_code == 422
    assert client.post(f"/requests/{RID}/status", json=[1]).status_code == 422
    assert post(client, "assigned", rid="nope").status_code == 404


def test_form_input(client):
    r = client.post(f"/requests/{RID}/status", data={"status": "assigned", "crew": "a"})
    assert r.status_code == 200


def test_board_reflects_status(client):
    assert 'data-status="new"' in client.get("/board").text
    post(client, "assigned")
    assert 'data-status="assigned"' in client.get("/board").text
    post(client, "rescued")
    html = client.get("/board").text
    assert 'data-status="rescued"' in html and "[rescued]" in html
