import os
import re
import shlex
import sys
from collections import Counter
from pathlib import Path

from rescue_field import seed
from rescue_field.api import default_decide_batch
from rescue_field.slm import KEYWORD_MODEL_VERSION, FixtureExtractor
from rescue_field.store import EventStore
from rescue_shared.contracts_io import validator_for

ADDRESS = re.compile(r"\b\d{2,4} (?:" + "|".join(seed.STREETS) + r")\b", re.I)
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


def real_decider():
    env = {k: v for k, v in os.environ.items() if k != "ENGINE_CMD"}
    return default_decide_batch(env)  # python -m rescue_engine.fallback


def test_200_seeded_requests_are_not_all_abstain(tmp_path):
    store = EventStore(tmp_path / "real.db")
    assert seed.seed_store(store, 200, 7, decide_batch=real_decider()) == 200
    decided = [e for e in store.all_events() if e["type"] == "decided"]
    assert len(decided) == 200
    outs = [e["payload"]["engine_output"] for e in decided]
    outcomes = Counter(o["outcome"] for o in outs)
    # Tiers of real decisions only: an abstain's provisional_tier is never a decision.
    decisions = [o for o in outs if o["outcome"] in ("prioritized", "duplicate")]
    tiers = Counter(o["priority_tier"] for o in decisions)
    reasons = Counter(o["abstain_reason"] for o in outs if o["outcome"] == "abstain")
    assert {"P1", "P2", "P3"} <= set(tiers), tiers
    assert any(o["outcome"] == "prioritized" and o["priority_tier"] == "P3" for o in outs)
    assert outcomes["duplicate"] >= 1, outcomes
    assert len(reasons) >= 2 and reasons["no_usable_location"] >= 1, reasons
    # The no-model keyword extractor reads almost every row; only the dry fixture texts are not.
    models = Counter(e["model_version"] for e in decided)
    assert models[KEYWORD_MODEL_VERSION] >= 180, models


def test_repeat_report_reuses_first_contact():
    items = seed.generate(200, 7)
    first: dict[str, str | None] = {}
    repeats = 0
    for item in items:
        m = ADDRESS.search(item.raw)
        if not m or item.raw in seed.DRY_REPORTS:
            continue
        addr = m.group(0).lower()
        if addr in first:
            repeats += 1
            if first[addr]:
                assert item.contact == first[addr], item.raw
        else:
            first[addr] = item.contact
    assert repeats >= 5


def test_dry_reports_are_known_fixture_texts():
    assert any(i.raw in seed.DRY_REPORTS for i in seed.generate(200, 7))
    for raw in seed.DRY_REPORTS:
        assert FixtureExtractor().extract(raw).payload["request_kind"] == "evacuation"
