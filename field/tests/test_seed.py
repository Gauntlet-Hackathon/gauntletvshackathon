import shlex
import sys
from pathlib import Path

from rescue_field import seed
from rescue_field.api import default_decide_batch
from rescue_field.store import EventStore
from rescue_shared.contracts_io import validator_for

FAKE = str(Path(__file__).with_name("fake_engine.py"))


def decider():
    return default_decide_batch({"ENGINE_CMD": shlex.join([sys.executable, FAKE, "ok"])})


def run(tmp_path, count, seed_no):
    store = EventStore(tmp_path / "f.db")
    seed.seed_store(store, count, seed_no, decide_batch=decider())
    return store


def test_generate_is_deterministic_and_seed_sensitive():
    a, b, c = seed.generate(200, 7), seed.generate(200, 7), seed.generate(200, 8)
    assert a == b
    assert a != c
    assert len(a) == 200


def test_messiness_covered():
    raws = [i.raw for i in seed.generate(200, 7)]
    assert any(r.isupper() for r in raws)
    assert any("Ayuda" in r or "socorro" in r for r in raws)
    assert any(len(r) < 20 for r in raws)
    assert any(len(r) > 400 for r in raws)
    assert any(any(e in r for e in seed.EMOJI) for r in raws)
    assert any("ppl" in r or " u " in r for r in raws)
    assert any("street name" in r for r in raws)


def test_rows_validate_and_outbox_matches(tmp_path):
    store = run(tmp_path, 25, 7)
    events = store.all_events()
    check = validator_for("event.schema.json")
    for ev in events:
        check.validate(ev)
    received = [e for e in events if e["type"] == "received"]
    assert len(received) == 25
    assert len({e["request_id"] for e in events}) == 25
    assert store.outbox_count() == len(events) == 75


def test_same_seed_same_requests_in_db(tmp_path):
    def texts(sub):
        (tmp_path / sub).mkdir()
        s = run(tmp_path / sub, 30, 3)
        return [
            e["payload"]["raw_text"]
            for e in sorted(s.all_events(), key=lambda e: e["event_id"])
            if e["type"] == "received"
        ]

    assert texts("a") == texts("b")


def test_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_CMD", shlex.join([sys.executable, FAKE, "ok"]))
    assert seed.main(["--count", "12", "--seed", "1", "--data-dir", str(tmp_path)]) == 0
    store = EventStore(tmp_path / "field.db")
    assert sum(e["type"] == "received" for e in store.all_events()) == 12
