"""Intake hardening (review PR-1): poison text, oversized fields and bodies, malformed numbers.

Each test names the review finding it pins (reviews 01-sonnet, 02-opus, 03-fable).
"""

import json
import random

import httpx
import pytest
from fastapi.testclient import TestClient

from rescue_field import api, sync
from rescue_field.api import create_app
from rescue_field.slm import KEYWORD_MODEL_VERSION, Extraction, KeywordExtractor
from rescue_field.store import EventStore
from rescue_field.tagger.durations import MAX_HOURS, parse_hours
from tests.test_api_intake import fake_engine

SURROGATE = "\ud83d"  # half of an emoji, as a truncating SMS gateway sends it
TEXT = "help roof 5 Elm Street 3 people"


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


@pytest.fixture
def client(store):
    app = create_app(store=store, decide_batch=fake_engine())
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def post_raw_json(client, path, body):
    """json.dumps keeps a lone surrogate as a \\ud83d escape, which the server decodes."""
    return client.post(
        path, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )


def received(store):
    return [e["payload"] for e in store.all_events() if e["type"] == "received"]


def assert_sendable(store):
    """Every stored event encodes the way the sync worker's httpx sends it."""
    for ev in store.all_events():
        httpx.Request("POST", "http://cloud/sync/events", json={"events": [ev]})


# ---- H-02 / F-03: NUL and lone surrogates never reach the store ---------------------------


@pytest.mark.parametrize(
    "body, field, expected",
    [
        ({"raw_text": f"{TEXT} {SURROGATE}"}, "raw_text", f"{TEXT} �"),
        ({"raw_text": f"{TEXT}\x00"}, "raw_text", TEXT),
        ({"raw_text": TEXT, "contact": f"555-0100{SURROGATE}"}, "contact", "555-0100�"),
        ({"raw_text": TEXT, "contact": "555\x000100"}, "contact", "5550100"),
    ],
)
def test_single_json_strips_nul_and_lone_surrogates(client, store, body, field, expected):
    r = post_raw_json(client, "/requests", body)
    assert r.status_code == 200, r.text
    [payload] = received(store)
    assert payload[field] == expected
    assert client.get("/board").status_code == 200  # the board was a 500 for good (H-02)
    assert_sendable(store)


def test_text_that_is_only_nul_is_empty(client, store):
    r = post_raw_json(client, "/requests", {"raw_text": "\x00\x00 "})
    assert r.status_code == 422 and "raw_text" in r.json()["detail"]
    assert store.all_events() == []


def test_form_nul_is_stripped(client, store):
    r = client.post(
        "/requests",
        content=b"raw_text=help%00+roof+5+Elm+Street&contact=555%000100",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200, r.text
    [payload] = received(store)
    assert payload["raw_text"] == "help roof 5 Elm Street" and payload["contact"] == "5550100"


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "content": f"{TEXT}\x00\nsecond {TEXT}".encode(),
            "headers": {"content-type": "text/plain"},
        },
        {
            "content": f"raw_text,contact\n{TEXT}\x00,555\x000100\n".encode(),
            "headers": {"content-type": "text/csv"},
        },
        {"files": {"file": ("r.csv", f"raw_text\n{TEXT}\x00\n".encode(), "text/csv")}},
        {
            "content": json.dumps({"text": f"{TEXT} {SURROGATE}\nsecond"}).encode(),
            "headers": {"content-type": "application/json"},
        },
    ],
)
def test_bulk_paths_strip_nul_and_lone_surrogates(client, store, kwargs):
    r = client.post("/requests/bulk", **kwargs)
    assert r.status_code == 200, r.text
    for payload in received(store):
        for value in (payload["raw_text"], payload["contact"] or ""):
            assert "\x00" not in value and SURROGATE not in value
    assert client.get("/board").status_code == 200
    assert_sendable(store)


def test_status_text_strips_nul_and_lone_surrogates(client, store):
    rid = client.post("/requests", json={"raw_text": TEXT}).json()["request_id"]
    r = post_raw_json(
        client,
        f"/requests/{rid}/status",
        {"status": "assigned", "crew": f"boat\x00-2{SURROGATE}", "note": "\x00"},
    )
    assert r.status_code == 200, r.text
    payload = store.all_events()[-1]["payload"]
    assert payload == {"crew": "boat-2�", "note": None}
    assert_sendable(store)


# ---- O-01 / F-02: bounded contact and request bodies ---------------------------------------


def test_contact_longer_than_limit_is_rejected(client, store):
    ok = "+1 713 555 0101 (ask for Maria)".ljust(api.MAX_CONTACT_CHARS, "x")
    assert client.post("/requests", json={"raw_text": TEXT, "contact": ok}).status_code == 200
    r = client.post("/requests", json={"raw_text": TEXT, "contact": ok + "x"})
    assert r.status_code == 422 and "contact" in r.json()["detail"]
    assert len(received(store)) == 1


def test_bulk_csv_contact_longer_than_limit_names_the_row(client, store):
    long_contact = "9" * (api.MAX_CONTACT_CHARS + 1)
    csv_text = f"raw_text,contact\n{TEXT},555-0100\n{TEXT},{long_contact}\n"
    r = client.post("/requests/bulk", content=csv_text, headers={"content-type": "text/csv"})
    assert r.status_code == 422 and r.json()["row"] == 2
    assert store.all_events() == []


def test_oversized_json_body_is_413_before_parsing(client, store):
    body = json.dumps({"raw_text": TEXT, "contact": "9" * (api.MAX_BODY_BYTES + 1)})
    r = client.post("/requests", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"] == "payload_too_large"
    assert store.all_events() == []


def test_oversized_chunked_body_is_413(client, store):
    def chunks():  # no Content-Length: the cap counts what arrives
        yield b'{"raw_text": "help", "contact": "'
        for _ in range(api.MAX_BODY_BYTES // 1024 + 1):
            yield b"9" * 1024
        yield b'"}'

    r = client.post("/requests", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert store.all_events() == []


def test_status_body_is_capped(client, store):
    rid = client.post("/requests", json={"raw_text": TEXT}).json()["request_id"]
    big = {"status": "assigned", "crew": "boat-2", "note": "n" * api.MAX_BODY_BYTES}
    assert client.post(f"/requests/{rid}/status", json=big).status_code == 413
    assert len(store.all_events()) == 3


def test_bulk_has_its_own_larger_cap(client, store, monkeypatch):
    # No location, so no row is a duplicate of another and the engine runs once.
    lines = "\n".join(f"please send water, message {i:04d}" for i in range(400)).encode()
    assert len(lines) > api.MAX_BODY_BYTES // 8  # well over what a single request may send
    monkeypatch.setattr(api, "MAX_BODY_BYTES", 1024)
    r = client.post("/requests/bulk", content=lines, headers={"content-type": "text/plain"})
    assert r.status_code == 200 and r.json()["count"] == 400
    monkeypatch.setattr(api, "MAX_BULK_BODY_BYTES", 1024)
    r = client.post("/requests/bulk", content=lines, headers={"content-type": "text/plain"})
    assert r.status_code == 413


# ---- O-06 / M-05: durations never raise or overflow; extractor errors keep the request ------


def test_parse_hours_huge_number_is_clamped_not_inf():
    assert parse_hours("for " + "9" * 400 + " hours") == MAX_HOURS
    assert parse_hours("for 9999 days") == MAX_HOURS
    assert parse_hours("for 30 hours") == 30.0  # normal input unchanged
    assert parse_hours("2 days") == 48.0
    assert parse_hours("for 1,000 hours") == 1000.0


@pytest.mark.parametrize("text", ["for 1.2.3 hours", "1,2,3 days", "no water since 12.30.45 hours"])
def test_parse_hours_unreadable_number_is_none(text):
    # Neither a ValueError (M-05) nor the lone unit's 1 h / 24 h.
    assert parse_hours(text) is None


def test_parse_hours_keeps_a_readable_number_next_to_an_unreadable_one():
    assert parse_hours("1.2.3 hours, maybe 2 days") == 48.0
    assert parse_hours("for hours") == 1.0  # a lone unit without a number is unchanged


def test_store_refuses_non_finite_numbers(store):
    ev = {"event_id": "e1", "request_id": "r1", "type": "received", "occurred_at": "x", "n": 1e999}
    with pytest.raises(ValueError):
        store.append_events([ev])
    assert store.all_events() == [] and store.outbox_count() == 0


class ExplodingExtractor:
    """A model that raises on one text, the way the tagger did on "1.2.3 hours" (M-05)."""

    model_version = "hf:org/model@0123456789abcdef0123456789abcdef01234567"

    def extract(self, raw_text):
        if "1.2.3" in raw_text:
            raise ValueError("could not convert string to float: '1.2.3'")
        return Extraction(KeywordExtractor.payload(raw_text), self.model_version)


def test_extractor_error_falls_back_to_keyword_extractor(store):
    app = create_app(store=store, extractor=ExplodingExtractor(), decide_batch=fake_engine())
    with TestClient(app) as client:
        r = client.post("/requests", json={"raw_text": f"{TEXT}, no water for 1.2.3 hours"})
        assert r.status_code == 200, r.text
        r = client.post(
            "/requests/bulk",
            content=f"{TEXT}\n{TEXT} 1.2.3 hours\nthird {TEXT}",
            headers={"content-type": "text/plain"},
        )
        assert r.status_code == 200 and r.json()["count"] == 3
    extracted = [e for e in store.all_events() if e["type"] == "extracted"]
    assert [e["model_version"] for e in extracted] == [
        KEYWORD_MODEL_VERSION,
        ExplodingExtractor.model_version,
        KEYWORD_MODEL_VERSION,
        ExplodingExtractor.model_version,
    ]


# ---- L-05: rescued_count is a bounded whole number -------------------------------------------


@pytest.mark.parametrize(
    "raw_count", [b"1e999", b"1e30", b"true", b"1.7", b"-0.5", b"-1", b'"lots"', b"100001"]
)
def test_rescued_count_rejects_non_integers_and_huge_values(client, store, raw_count):
    rid = client.post("/requests", json={"raw_text": TEXT}).json()["request_id"]
    body = b'{"status": "rescued", "rescued_count": ' + raw_count + b"}"
    r = client.post(
        f"/requests/{rid}/status", content=body, headers={"content-type": "application/json"}
    )
    assert r.status_code == 422, r.text
    assert "rescued_count" in r.json()["detail"]
    assert len(store.all_events()) == 3


@pytest.mark.parametrize(
    "kwargs, stored",
    [
        ({"json": {"status": "rescued", "rescued_count": 4}}, 4),
        ({"json": {"status": "rescued", "rescued_count": 4.0}}, 4),
        ({"data": {"status": "rescued", "rescued_count": " 4 "}}, 4),
        ({"json": {"status": "rescued", "rescued_count": 100000}}, 100000),
        ({"data": {"status": "rescued", "rescued_count": ""}}, None),
    ],
)
def test_rescued_count_accepts_whole_numbers(client, store, kwargs, stored):
    rid = client.post("/requests", json={"raw_text": TEXT}).json()["request_id"]
    r = client.post(f"/requests/{rid}/status", **kwargs)
    assert r.status_code == 200, r.text
    assert store.all_events()[-1]["payload"]["rescued_count"] == stored


# ---- L-04: landmark slicing with a character whose lowercase is longer ----------------------


@pytest.mark.parametrize("prefix", ["", "İ ", "İstanbul family: ", "İİİİİİ "])
def test_landmark_is_sliced_from_the_original_text(prefix):
    text = prefix + "need help, 3 people behind the Kroger on Westheimer Road, water rising"
    assert KeywordExtractor.payload(text)["location"]["text"] == "Westheimer Road"


# ---- sync worker: one poison event no longer holds back the outbox ---------------------------


def ev(i, **extra):
    return {
        "event_id": f"e{i:04d}",
        "request_id": f"r{i}",
        "type": "received",
        "occurred_at": "2026-11-07T10:00:00Z",
        **extra,
    }


class Cloud:
    """Acks every event; 413 for any body over `max_bytes` (or holding `too_large_id`);
    `status` overrides every answer."""

    def __init__(self, max_bytes=10_000_000, status=None, too_large_id=None):
        self.max_bytes = max_bytes
        self.status = status
        self.too_large_id = too_large_id
        self.acked = []
        self.batches = []
        self.posts = 0

    def __call__(self, request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/sync/heartbeat":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/sync/down":
            return httpx.Response(200, json={"events": [], "next_since": 0})
        self.posts += 1
        if self.status is not None:
            return httpx.Response(self.status, json={"error": "bad_request"})
        ids = [e["event_id"] for e in json.loads(request.content)["events"]]
        if len(request.content) > self.max_bytes or self.too_large_id in ids:
            return httpx.Response(413, text="too large")
        self.batches.append(len(ids))
        self.acked += ids
        return httpx.Response(200, json={"acked": ids, "rejected": []})


def worker(store, cloud, **kw):
    return sync.SyncWorker(
        store, "http://cloud", device_id="laptop-dev-01", token="tok",
        versions=lambda: ("rules@abc1234+bin:12345678", "stub:fixture-extractor"),
        transport=httpx.MockTransport(cloud), rng=random.Random(1), **kw,
    )  # fmt: skip


@pytest.mark.parametrize(
    "poison, error",
    [
        ({"raw_text": f"x{SURROGATE}"}, "not sendable"),
        ({"hours": float("inf")}, "not sendable"),
        ({"contact": "555\x000100"}, "U+0000"),  # the cloud's jsonb refuses it (H-02)
        ({"nested": [{"x\x00": 1}]}, "U+0000"),
    ],
)
def test_unsendable_event_is_rejected_locally_and_the_rest_drain(poison, error):
    # Events stored before intake hardening (or by any other path) must not wedge sync (H-02,
    # O-06): httpx cannot encode them, so they are quarantined, never sent.
    store = EventStore(check_same_thread=False)
    store.append_events([ev(0), ev(1)])
    store._conn.execute(  # bypass append_events, which now refuses non-finite numbers
        "INSERT INTO events(event_id, request_id, type, occurred_at, body) VALUES (?,?,?,?,?)",
        ("e0002", "r2", "received", "x", json.dumps(ev(2, **poison))),
    )
    store._conn.execute("INSERT INTO outbox(event_id) VALUES ('e0002')")
    store.append_events([ev(3)])
    cloud = Cloud()
    w = worker(store, cloud)
    w.run_cycle()
    assert w.online and w.last_error is None
    assert cloud.acked == ["e0000", "e0001", "e0003"]
    assert store.outbox_count() == 0
    [rej] = store.rejected()
    assert rej["event_id"] == "e0002" and error in rej["error"]
    assert rej["error"].startswith("local: ")


def test_413_bisects_and_rejects_only_the_oversized_event():
    # O-01: one event too large for the cloud's body limit made every batch 413 for ever.
    store = EventStore(check_same_thread=False)
    store.append_events([ev(i) for i in range(7)])
    store.append_events([ev(7, raw_text="9" * 5000)])
    store.append_events([ev(i) for i in range(8, 12)])
    cloud = Cloud(max_bytes=4000)
    w = worker(store, cloud, oversize_bytes=4000)  # the real threshold is 1 MB
    w.run_cycle()
    assert w.online and w.last_error is None
    assert sorted(cloud.acked) == [f"e{i:04d}" for i in range(12) if i != 7]
    assert [r["event_id"] for r in store.rejected()] == ["e0007"]
    assert "413" in store.rejected()[0]["error"]
    assert store.outbox_count() == 0


def test_413_on_many_small_events_only_shrinks_the_batch():
    store = EventStore(check_same_thread=False)
    store.append_events([ev(i, raw_text="x" * 100) for i in range(40)])
    cloud = Cloud(max_bytes=2000)  # about 10 of these events per request
    w = worker(store, cloud)
    w.run_cycle()
    assert len(cloud.acked) == 40 and store.rejected() == [] and store.outbox_count() == 0
    assert max(cloud.batches) < 40


@pytest.mark.parametrize("status", [400, 401, 403, 500])
def test_other_errors_still_back_off_and_reject_nothing(status):
    store = EventStore(check_same_thread=False)
    store.append_events([ev(i) for i in range(3)])
    w = worker(store, Cloud(status=status))
    assert w.run_cycle() > 0
    assert not w.online and f"HTTP {status}" in w.last_error
    assert store.outbox_count() == 3 and store.rejected() == []


def test_oversized_event_is_found_without_shrinking_the_rest():
    # After a quarantine the batch size is back to 200: 1 oversized + 399 normal events drain
    # in three POSTs (413, 200, 199), not one event at a time.
    store = EventStore(check_same_thread=False)
    store.append_events([ev(0, raw_text="9" * 5000)] + [ev(i) for i in range(1, 400)])
    cloud = Cloud(too_large_id="e0000")
    w = worker(store, cloud, oversize_bytes=4000)
    w.run_cycle()
    assert w.online and store.outbox_count() == 0
    assert [r["event_id"] for r in store.rejected()] == ["e0000"]
    assert cloud.posts == 3 and cloud.batches == [200, 199]


def test_cloud_answering_413_to_everything_rejects_nothing():
    # A proxy or captive portal that answers 413 to every POST must not empty the outbox: no
    # normal-sized event is ever quarantined on 413.
    store = EventStore(check_same_thread=False)
    store.append_events([ev(i, raw_text="help") for i in range(500)])
    w = worker(store, Cloud(status=413))
    assert w.run_cycle() > 0
    assert not w.online and "HTTP 413" in w.last_error
    assert store.outbox_count() == 500 and store.rejected() == []


# ---- F41: each row's engine input is checked, and the bad row is named -------------------------


class BadRowExtractor:
    """KeywordExtractor, except the text containing BAD gets a broken extraction."""

    model_version = KEYWORD_MODEL_VERSION

    def __init__(self, mutate):
        self.mutate = mutate

    def extract(self, raw_text):
        payload = KeywordExtractor.payload(raw_text)
        if "BAD" in raw_text:
            self.mutate(payload)
        return Extraction(payload, self.model_version)


def _nan_confidence(payload):
    payload["people_count"]["confidence"] = float("nan")


def _bad_hazard(payload):
    payload["hazards"] = [{"kind": "not-a-hazard", "confidence": 0.5}]


class NeverCalled:
    def __call__(self, inputs):
        raise AssertionError("the engine must not run for an invalid batch")


@pytest.mark.parametrize("mutate", [_nan_confidence, _bad_hazard])
def test_bulk_bad_engine_input_names_the_row(store, mutate):
    app = create_app(store=store, extractor=BadRowExtractor(mutate), decide_batch=NeverCalled())
    with TestClient(app) as client:
        r = client.post(
            "/requests/bulk",
            content=f"{TEXT}\n{TEXT} again\n{TEXT} BAD",
            headers={"content-type": "text/plain"},
        )
        assert r.status_code == 422, r.text
        body = r.json()
        assert body["error"] == "invalid_input" and body["row"] == 3
        assert body["detail"]
        single = client.post("/requests", json={"raw_text": f"{TEXT} BAD"})
        assert single.status_code == 422 and single.json()["error"] == "invalid_input"
    assert store.all_events() == [] and store.outbox_count() == 0
