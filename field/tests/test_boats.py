from fastapi.testclient import TestClient

from rescue_field.api import create_app
from rescue_field.boats import seat_warning
from rescue_field.store import EventStore


def client(path=":memory:"):
    return TestClient(create_app(store=EventStore(path, check_same_thread=False)))


def test_crud():
    c = client()
    assert c.get("/boats.json").json() == {"boats": []}
    r = c.post("/boats", json={"name": "Boat 1", "seats": 6})
    assert r.status_code == 200 and r.json()["status"] == "ready"
    assert c.post("/boats", json={"name": "Boat 1", "seats": 6}).status_code == 409
    r = c.post("/boats/Boat 1", json={"seats": 8, "status": "out"})
    assert r.json()["seats"] == 8 and r.json()["status_text"] == "Out on a job"
    assert c.post("/boats/Nope", json={"seats": 3}).status_code == 404
    assert c.post("/boats/Boat 1/delete").status_code == 200
    assert c.get("/boats.json").json() == {"boats": []}
    assert c.post("/boats/Boat 1/delete").status_code == 404


def test_validation():
    c = client()
    for body in (
        {"name": "", "seats": 4},
        {"name": "  ", "seats": 4},
        {"name": "A", "seats": 0},
        {"name": "A", "seats": 101},
        {"name": "A", "seats": "x"},
        {"name": "A", "seats": 4, "status": "sunk"},
    ):
        assert c.post("/boats", json=body).status_code == 422, body
    c.post("/boats", json={"name": "A", "seats": 4})
    assert c.post("/boats/A", json={"seats": 500}).status_code == 422
    assert c.get("/boats.json").json()["boats"][0]["seats"] == 4


def test_form_flow_and_screen():
    c = client()
    r = c.post("/boats", data={"name": "Skiff", "seats": "5"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/boats"
    page = c.get("/boats")
    assert "Kept on this laptop only" in page.text and "Skiff" in page.text
    assert "At the launch, ready to go" in page.text
    assert "Back at the shelter soon" in page.text
    bad = c.post("/boats", data={"name": "", "seats": "5"})
    assert bad.status_code == 422 and "Boat name is required" in bad.text
    c.post("/boats/Skiff/delete", data={})
    assert "No boats yet" in c.get("/boats").text


def test_seat_warning():
    assert seat_warning(4, 5) == "Not enough room for 5"
    assert seat_warning(5, 5) is None
    assert seat_warning(4, None) is None


def test_persists_and_never_synced(tmp_path):
    db = tmp_path / "field.db"
    store = EventStore(db, check_same_thread=False)
    c = TestClient(create_app(store=store))
    c.post("/boats", json={"name": "Boat 1", "seats": 6, "status": "returning"})
    assert store._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    store.close()
    c2 = client(db)
    boat = c2.get("/boats.json").json()["boats"][0]
    assert (boat["name"], boat["seats"], boat["status"]) == ("Boat 1", 6, "returning")
