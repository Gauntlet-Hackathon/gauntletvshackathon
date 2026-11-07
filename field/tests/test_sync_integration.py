"""F14: sync worker end to end against an in-process, contract-faithful fake cloud.

The fake implements contracts/sync_api.md: bearer auth (401), device_mismatch (403), 400 on bad
batches, per-event validation against event.schema.json + the payload schema, idempotent dedupe on
event_id (duplicates are acked), and arrival-order storage. Request and response bodies are checked
against contracts/sync/*.schema.json, and canned responses start from contracts/examples.
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import httpx
import pytest

from rescue_field import sync
from rescue_field.store import EventStore
from rescue_shared.contracts_io import load_json, validator_for

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples"
DEVICE = "laptop-dev-01"
TOKEN = "dev-token-laptop-dev-01"
RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
MODEL = "hf:rescue-org/rescue-tagger@4d5e6f7a8b"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def example(name: str):
    return copy.deepcopy(load_json(EXAMPLES / f"{name}.json"))


def ulid(i: int) -> str:
    return "01JCB" + "0" * 19 + _CROCKFORD[i // 32 % 32] + _CROCKFORD[i % 32]


def make_event(i: int) -> dict:
    ev = example("event.valid")
    ev["event_id"] = ulid(i)
    ev["request_id"] = ulid(1000 + i)
    ev["device_id"] = DEVICE
    return ev


class ContractCloud:
    """httpx handler (MockTransport) standing in for the Phoenix sync API."""

    def __init__(self) -> None:
        self.up = True
        self.fail_events = 0  # next N POST /sync/events return 500
        self.drop_ack_responses = 0  # store the batch, then answer 500 (ack lost in flight)
        self.valid_tokens = {TOKEN: DEVICE}
        self.stored: list[dict] = []  # arrival order, deduped on event_id
        self.event_posts = 0
        self.unauthorized = 0

    def _err(self, status: int, code: str) -> httpx.Response:
        body = {"error": code}
        assert validator_for("sync/error_response.schema.json").is_valid(body)
        return httpx.Response(status, json=body)

    def _ok(self, schema: str, body: dict) -> httpx.Response:
        validator_for(f"sync/{schema}.schema.json").validate(body)
        return httpx.Response(200, json=body)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("offline")
        path = request.url.path
        if path == "/health":
            return self._ok("health_response", example("sync-health_response.valid"))
        device = self.valid_tokens.get(request.headers.get("authorization", "")[7:])
        if not request.headers.get("authorization", "").startswith("Bearer ") or device is None:
            self.unauthorized += 1
            return self._err(401, "unauthorized")
        try:
            body = json.loads(request.content)
        except ValueError:
            return self._err(400, "bad_request")
        if body.get("device_id") != device:
            return self._err(403, "device_mismatch")
        if path == "/sync/heartbeat":
            validator_for("sync/heartbeat_request.schema.json").validate(body)
            return self._ok("heartbeat_response", example("sync-heartbeat_response.valid"))
        assert path == "/sync/events" and request.method == "POST"
        self.event_posts += 1
        if not 1 <= len(body.get("events", [])) <= 200:
            return self._err(400, "bad_request")
        if any(e.get("device_id") != device for e in body["events"]):
            return self._err(403, "device_mismatch")
        validator_for("sync/events_request.schema.json").validate(body)
        if self.fail_events:
            self.fail_events -= 1
            return self._err(500, "server_error")
        acked = []
        for ev in body["events"]:
            if ev["event_id"] not in {s["event_id"] for s in self.stored}:
                self.stored.append(ev)
            acked.append(ev["event_id"])
        if self.drop_ack_responses:
            self.drop_ack_responses -= 1
            return self._err(500, "server_error")
        return self._ok("events_response", {"acked": acked, "rejected": []})


@pytest.fixture
def store():
    s = EventStore(check_same_thread=False)
    yield s
    s.close()


@pytest.fixture
def cloud():
    return ContractCloud()


def make_worker(store, cloud, *, token=TOKEN, **kw):
    return sync.SyncWorker(
        store, "http://cloud", device_id=DEVICE, token=token,
        versions=lambda: (RULES, MODEL),
        transport=httpx.MockTransport(cloud), rng=random.Random(1), **kw,
    )


def queue(store, ids):
    store.append_events([make_event(i) for i in ids])


def test_offline_requests_stay_queued_then_drain_on_reconnect(store, cloud):
    worker = make_worker(store, cloud)
    cloud.up = False
    queue(store, range(5))
    with pytest.raises(sync.SyncError):
        worker.sync_once()
    assert store.outbox_count() == 5
    assert cloud.stored == []
    queue(store, range(5, 8))  # more arrive while still offline
    assert worker.run_cycle() >= 1 and not worker.online
    assert store.outbox_count() == 8

    cloud.up = True
    worker.resume()
    result = worker.sync_once()
    assert result.acked == 8
    assert store.outbox_count() == 0
    assert [e["event_id"] for e in cloud.stored] == [ulid(i) for i in range(8)]


def test_drain_spans_multiple_batches_in_order(store, cloud):
    worker = make_worker(store, cloud, batch_size=3)
    queue(store, range(10))
    assert worker.sync_once().acked == 10
    assert store.outbox_count() == 0
    assert cloud.event_posts == 4
    assert [e["event_id"] for e in cloud.stored] == [ulid(i) for i in range(10)]


def test_retry_after_500_creates_no_duplicates(store, cloud):
    worker = make_worker(store, cloud)
    queue(store, range(4))
    cloud.fail_events = 1
    with pytest.raises(sync.SyncError):
        worker.sync_once()
    assert store.outbox_count() == 4 and cloud.stored == []
    worker.sync_once()
    assert store.outbox_count() == 0
    ids = [e["event_id"] for e in cloud.stored]
    assert ids == [ulid(i) for i in range(4)]


def test_lost_ack_resend_is_deduped_and_acked(store, cloud):
    """Cloud stored the batch but the 500 hid the ack: the resend must not duplicate."""
    worker = make_worker(store, cloud)
    queue(store, range(4))
    cloud.drop_ack_responses = 1
    with pytest.raises(sync.SyncError):
        worker.sync_once()
    assert store.outbox_count() == 4 and len(cloud.stored) == 4
    result = worker.sync_once()
    assert result.acked == 4 and store.outbox_count() == 0
    assert [e["event_id"] for e in cloud.stored] == [ulid(i) for i in range(4)]


def test_401_stops_sync_without_dropping_rows(store, cloud):
    worker = make_worker(store, cloud, token="wrong")
    queue(store, range(5))
    with pytest.raises(sync.SyncError, match="401"):
        worker.sync_once()
    assert worker.run_cycle() >= 1 and not worker.online
    assert "401" in (worker.last_error or "")
    assert store.outbox_count() == 5
    assert cloud.stored == [] and cloud.event_posts == 0 and cloud.unauthorized >= 2

    # fixing the token (cloud now knows it) resumes cleanly with nothing lost
    cloud.valid_tokens["wrong"] = DEVICE
    assert worker.sync_once().acked == 5
    assert store.outbox_count() == 0


def test_order_preserved_across_failures_and_new_events(store, cloud):
    worker = make_worker(store, cloud, batch_size=2)
    queue(store, range(3))
    cloud.fail_events = 1
    with pytest.raises(sync.SyncError):
        worker.sync_once()
    queue(store, range(3, 6))
    worker.sync_once()
    assert [e["event_id"] for e in cloud.stored] == [ulid(i) for i in range(6)]
