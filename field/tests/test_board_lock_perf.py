"""Review PR-4: board and lock performance (M-04, O-02, O-08; F-06 is in test_downsync.py).

The model is simulated by a slow fake extractor (a sleep, then the keyword extractor), never torch.
"""

import os
import threading
import time

import pytest
from fastapi.testclient import TestClient
from field.tests.test_board import Q_HOAX, add, extracted, output, row_html

from rescue_field import board as board_module
from rescue_field import pipeline
from rescue_field.api import create_app, default_decide_batch
from rescue_field.board import build_board
from rescue_field.pipeline import IntakeItem, process_batch
from rescue_field.slm import KeywordExtractor
from rescue_field.store import EventStore
from rescue_shared.ulid import new_ulid

RULES = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
FAST_S = 0.5  # a lock-free answer takes milliseconds; the old lock-holders took seconds


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "f.db", check_same_thread=False)
    yield s
    s.close()


class SlowExtractor(KeywordExtractor):
    """Stands in for the trained model (about 45 ms per message on the demo VM)."""

    def __init__(self, delay=0.02, on_call=None):
        self.delay = delay
        self.on_call = on_call
        self.calls = 0

    def extract(self, raw_text):
        self.calls += 1
        if self.on_call is not None:
            self.on_call(raw_text)
        time.sleep(self.delay)
        return super().extract(raw_text)


class RecordingDecider:
    """In-process engine stand-in: P2 for everything; keeps every engine input."""

    def __init__(self):
        self.inputs = []

    def __call__(self, inputs):
        self.inputs.extend(inputs)
        return [
            {
                "outcome": "prioritized",
                "priority_tier": "P2",
                "rule_id": "R-P2-01",
                "duplicate_of": None,
                "abstain_reason": None,
                "provisional_tier": None,
                "follow_up_question": None,
                "rules_version": RULES,
            }
            for _ in inputs
        ]


def timed_get(client, path):
    t = time.perf_counter()
    r = client.get(path)
    return r, time.perf_counter() - t


def paste(n, prefix="Elm"):
    lines = (f"help {i % 9 + 2} people at {i} {prefix} Street water rising" for i in range(n))
    return "\n".join(lines)


# ---- /healthz -----------------------------------------------------------------------------


def test_healthz_answers_while_the_intake_lock_is_held(store):
    app = create_app(store=store, decide_batch=RecordingDecider())
    with TestClient(app) as client:
        with app.state.intake_lock:
            r, took = timed_get(client, "/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert took < FAST_S


def test_healthz_is_503_without_a_store(store):
    app = create_app(store=store, decide_batch=RecordingDecider())
    with TestClient(app) as client:
        app.state.store = None
        try:
            assert client.get("/healthz").status_code == 503
        finally:
            app.state.store = store


# ---- O-02: bulk extraction outside the intake lock ---------------------------------------


def test_bulk_extraction_runs_outside_the_intake_lock(store):
    seen = []

    def spy(_raw):
        extract_lock = getattr(app.state, "extract_lock", None)
        seen.append((app.state.intake_lock.locked(), bool(extract_lock and extract_lock.locked())))

    extractor = SlowExtractor(delay=0, on_call=spy)
    app = create_app(store=store, extractor=extractor, decide_batch=RecordingDecider())
    with TestClient(app) as client:
        r = client.post("/requests/bulk", content=paste(5), headers={"content-type": "text/plain"})
        assert r.status_code == 200 and r.json()["count"] == 5
        single = client.post("/requests", json={"raw_text": "help at 1 Oak Street"})
        assert single.status_code == 200
    # Never under the intake lock; always under the extractor's own lock.
    assert seen == [(False, True)] * 6


def test_health_and_sync_status_answer_during_a_slow_bulk_paste(store):
    """Review O-02: a 200-row paste at 45 ms/row held /sync/status (the old Fly health check)
    for 9.7 s. Here 100 rows at 20 ms; both answer in well under a second mid-paste."""
    started = threading.Event()
    extractor = SlowExtractor(delay=0.02, on_call=lambda _r: started.set())
    app = create_app(store=store, extractor=extractor, decide_batch=RecordingDecider())
    with TestClient(app) as client:
        out = {}

        def bulk():
            out["r"] = client.post(
                "/requests/bulk", content=paste(100), headers={"content-type": "text/plain"}
            )

        t = threading.Thread(target=bulk)
        t.start()
        assert started.wait(5)
        time.sleep(0.1)
        status, status_s = timed_get(client, "/sync/status")
        health, health_s = timed_get(client, "/healthz")
        badge, badge_s = timed_get(client, "/sync/badge")
        mid_calls = extractor.calls
        t.join(30)
    assert status.status_code == health.status_code == badge.status_code == 200
    assert mid_calls < 100, "the probes ran after the paste finished"
    assert status_s < FAST_S and health_s < FAST_S and badge_s < FAST_S, (status_s, health_s)
    assert out["r"].status_code == 200 and out["r"].json()["count"] == 100
    assert store.outbox_count() == 300


def test_single_request_during_bulk_extraction_is_stored_first_and_seen_as_candidate(store):
    """A single POST that arrives mid-paste waits for one row's extraction, not the whole
    paste, and is stored first; the paste's matching row (stored later, stamped later) gets it
    as a duplicate candidate, so arrival order and store order still agree."""
    started = threading.Event()
    extractor = SlowExtractor(delay=0.02, on_call=lambda _r: started.set())
    decider = RecordingDecider()
    app = create_app(store=store, extractor=extractor, decide_batch=decider)
    text = paste(60, "Pine") + "\n4 people trapped at 12 Elm Street"
    single_body = {"raw_text": "4 people stuck at 12 Elm Street", "contact": "713-555-0142"}
    with TestClient(app) as client:
        out = {}

        def bulk():
            out["r"] = client.post(
                "/requests/bulk", content=text, headers={"content-type": "text/plain"}
            )

        t = threading.Thread(target=bulk)
        t.start()
        assert started.wait(5)
        time.sleep(0.1)
        t0 = time.perf_counter()
        single = client.post("/requests", json=single_body)
        single_s = time.perf_counter() - t0
        t.join(30)
    assert single.status_code == 200 and out["r"].status_code == 200
    assert single_s < FAST_S, single_s
    single_id = single.json()["request_id"]
    last = out["r"].json()["results"][-1]
    assert last["received_at"] >= single.json()["received_at"]
    events = store.all_events()
    order = [e["request_id"] for e in events if e["type"] == "received"]
    assert order.index(single_id) < order.index(last["request_id"])
    elm = [x for x in decider.inputs if x["extracted"]["location"]["text"] == "12 Elm Street"]
    assert len(elm) == 2
    assert [c["request_id"] for c in elm[1]["context"]["duplicate_candidates"]] == [single_id]


def test_fair_lock_serves_waiters_in_arrival_order():
    lock = pipeline.FairLock()
    order = []
    lock.__enter__()
    threads = []
    for i in range(5):
        def take(i=i):
            with lock:
                order.append(i)

        th = threading.Thread(target=take)
        th.start()
        threads.append(th)
        while lock._next != i + 2:  # wait until thread i holds its ticket
            time.sleep(0.001)
    assert lock.locked()
    lock.__exit__()
    for th in threads:
        th.join(5)
    assert order == [0, 1, 2, 3, 4] and not lock.locked()


def test_extractor_error_outside_the_lock_still_stores_nothing(store):
    """A keyword-extractor error (not wrapped by the M-05 fallback) still rejects the upload
    before anything is stored, now that extraction runs before the lock."""

    class Broken(KeywordExtractor):
        def extract(self, raw_text):
            raise ValueError("bad row")

    app = create_app(store=store, extractor=Broken(), decide_batch=RecordingDecider())
    with TestClient(app) as client:
        r = client.post("/requests/bulk", content=paste(3), headers={"content-type": "text/plain"})
    assert r.status_code == 422 and store.all_events() == []
    assert not app.state.intake_lock.locked() and not app.state.extract_lock.locked()


# ---- M-04: intake and board cost ----------------------------------------------------------


def test_intake_compares_only_prior_requests_at_the_same_location(store, monkeypatch):
    """logic.duplicate_candidates compares every pair, so intake gets only the earlier
    requests that share a normalised location (the others can never be candidates)."""
    decider = RecordingDecider()
    items = [IntakeItem(f"help at {i} Birch Street", "sms") for i in range(40)]
    items.append(IntakeItem("4 people at 12 Elm Street", "sms", contact="713-555-0142"))
    process_batch(items, store=store, extractor=KeywordExtractor(), decide_batch=decider)
    sizes = []
    real = pipeline.logic.duplicate_candidates
    monkeypatch.setattr(
        pipeline.logic,
        "duplicate_candidates",
        lambda events, window: sizes.append(len(events)) or real(events, window),
    )
    item = IntakeItem("4 people stuck at 12 elm street", "sms", contact="713-555-0142")
    [events] = process_batch(
        [item], store=store, extractor=KeywordExtractor(), decide_batch=decider
    )
    assert sizes and max(sizes) == 2  # the one earlier 12 Elm Street request, plus this one
    cands = decider.inputs[-1]["context"]["duplicate_candidates"]
    assert len(cands) == 1 and cands[0]["people_count"] == 4


class CountingEvents(list):
    def __init__(self, *args):
        super().__init__(*args)
        self.passes = 0

    def __iter__(self):
        self.passes += 1
        return super().__iter__()


def test_board_reads_the_event_log_in_one_pass(store):
    """Review M-04: every request rescanned every event (O(requests x events)): 3 s at 3,000."""
    for i in range(30):
        add(store, f"R{i:02d}", output("P2", "R-P2-01"), extracted(location=f"loc-{i}"))
    for rid, kind, payload in (
        ("R03", "assigned", {"crew": "boat 2", "note": None}),
        ("R03", "rescued", {"rescued_count": 2, "note": None}),
        ("R07", "escalated", {"reason": "needs helicopter", "note": None}),
    ):
        store.append_events(
            [
                {
                    "event_id": new_ulid(),
                    "request_id": rid,
                    "type": kind,
                    "device_id": "field-01",
                    "occurred_at": "2026-11-07T11:00:00Z",
                    "rules_version": RULES,
                    "model_version": "hf:rescue-org/rescue-tagger@4d5e6f7a8b",
                    "payload": payload,
                }
            ]
        )
    events = CountingEvents(store.all_events())

    class SnapshotStore:
        def all_events(self):
            return events

    board = build_board(SnapshotStore())
    assert events.passes <= 2, events.passes
    status = {r["request_id"]: r["status"] for r in board["tiers"][1]["rows"]}
    assert status["R03"] == "rescued" and status["R07"] == "escalated" and status["R00"] is None


def test_board_builds_and_renders_outside_the_intake_lock(store, monkeypatch):
    add(store, "A", output("P1", "R-P1-01"), extracted(location="loc-a"))
    app = create_app(store=store, decide_batch=RecordingDecider())
    held = []
    real_build = board_module.build_board_from_events
    real_render = board_module.templates.TemplateResponse

    def build(events):
        held.append(("build", app.state.intake_lock.locked()))
        return real_build(events)

    def render(*args, **kwargs):
        held.append(("render", app.state.intake_lock.locked()))
        time.sleep(0.8)  # a large board: rendering must not stall the event loop either
        return real_render(*args, **kwargs)

    monkeypatch.setattr(board_module, "build_board_from_events", build)
    monkeypatch.setattr(board_module.templates, "TemplateResponse", render)
    with TestClient(app) as client:
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("r", client.get("/board")))
        t.start()
        while not held:
            time.sleep(0.005)
        time.sleep(0.05)
        health, health_s = timed_get(client, "/healthz")
        status, status_s = timed_get(client, "/sync/status")
        t.join(10)
    assert held == [("build", False), ("render", False)]
    assert health.status_code == status.status_code == 200
    assert health_s < FAST_S and status_s < FAST_S, (health_s, status_s)
    assert out["r"].status_code == 200 and "loc-a" in out["r"].text


# ---- O-08: a prioritized duplicate joins an abstained root --------------------------------


def abstain(reason, question, provisional="P2"):
    return output(None, "R-ABS-02", "abstain", question=question) | {
        "abstain_reason": reason,
        "provisional_tier": provisional,
    }


def test_abstained_root_lifted_to_a_tier_keeps_its_flag_and_follow_up(store):
    add(store, "ROOT", abstain("possible_hoax", Q_HOAX), extracted(location="12 Elm Street"),
        at="2026-11-07T09:00:00Z", raw="lol 2 people stuck at 12 Elm Street")
    add(store, "DUP", output("P1", "R-P1-01", "duplicate", duplicate_of="ROOT"),
        extracted(location="12 Elm Street"), at="2026-11-07T09:05:00Z",
        raw="2 people trapped on the roof at 12 Elm Street, water rising fast, baby with us")
    board = build_board(store)
    [row] = board["tiers"][0]["rows"]
    assert row["request_id"] == "ROOT"
    assert row["question"] == {"text": Q_HOAX, "label": "Possible hoax"}
    [fu] = board["follow_ups"]
    assert fu["request_id"] == "ROOT" and fu["grouped_tier"] == "P1"
    assert fu["provisional_tier"] == "P2"
    assert [d["request_id"] for d in fu["duplicates"]] == ["DUP"]
    assert board["counts"]["follow_ups"] == 1
    page = TestClient(create_app(store=store)).get("/board").text
    p1 = row_html(page, "ROOT")
    assert '<strong class="confirm-label">Possible hoax</strong>' in p1
    queue = page[page.index('id="follow-ups"') :]
    assert f'<strong class="question">{Q_HOAX}</strong>' in queue
    assert 'data-grouped-tier="P1"' in queue


def test_abstained_root_question_and_duplicate_question_both_show(store):
    q_counts = "How many people are with you right now?"
    q_dup = "Is the water still rising?"
    add(store, "ROOT", abstain("conflicting_counts", q_counts, "P1"),
        extracted(location="loc-x"), at="2026-11-07T09:00:00Z")
    add(store, "DUP", output("P1", "R-P1-05", "duplicate", duplicate_of="ROOT", question=q_dup),
        extracted(location="loc-x"), at="2026-11-07T09:05:00Z")
    board = build_board(store)
    [row] = board["tiers"][0]["rows"]
    assert row["questions"] == [
        {"text": q_counts, "label": "Needs confirmation"},
        {"text": q_dup, "label": "Needs confirmation"},
    ]
    assert [f["request_id"] for f in board["follow_ups"]] == ["ROOT"]


def test_plain_abstain_and_plain_tier_rows_unchanged(store):
    add(store, "AB", abstain("no_usable_location", "Where exactly are you?"),
        extracted(location="loc-ab"))
    add(store, "P", output("P2", "R-P2-01"), extracted(location="loc-p"))
    board = build_board(store)
    [fu] = board["follow_ups"]
    assert fu["request_id"] == "AB" and "grouped_tier" not in fu
    [row] = board["tiers"][1]["rows"]
    assert row["question"] is None and row["questions"] == []


def test_hoax_root_with_prioritized_duplicate_through_the_real_engine(store):
    """The review's repro (board_abstain_root.py) with the keyword extractor and the engine."""
    decide = default_decide_batch({k: v for k, v in os.environ.items() if k != "ENGINE_CMD"})
    app = create_app(store=store, extractor=KeywordExtractor(), decide_batch=decide)
    with TestClient(app) as client:
        r1 = client.post("/requests", json={"raw_text": "lol 2 people stuck at 12 Elm Street",
                                            "contact": "713-555-0142"}).json()
        r2 = client.post("/requests", json={
            "raw_text": "2 people trapped on the roof at 12 Elm Street, water rising fast, "
                        "baby with us",
            "contact": "713-555-0142"}).json()
    assert r1["decision"]["outcome"] == "abstain", r1
    assert r1["decision"]["abstain_reason"] == "possible_hoax"
    assert (r2["decision"]["outcome"], r2["decision"]["priority_tier"]) == ("duplicate", "P1")
    board = build_board(store)
    [row] = board["tiers"][0]["rows"]
    assert row["request_id"] == r1["request_id"]
    assert row["question"]["label"] == "Possible hoax"
    assert [f["request_id"] for f in board["follow_ups"]] == [r1["request_id"]]
