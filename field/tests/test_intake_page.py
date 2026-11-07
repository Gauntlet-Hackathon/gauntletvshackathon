"""UI-7: the ECC intake simulator at /intake and its DB-backed log at /intake/log.json."""

import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from rescue_field import api
from rescue_field.api import create_app, default_decide_batch
from rescue_field.intake_page import LOG_LIMIT, intake_log, result_words
from rescue_field.store import EventStore


def make_client(path):
    store = EventStore(path, check_same_thread=False)
    return store, TestClient(create_app(store=store, decide_batch=default_decide_batch()))


@pytest.fixture
def db(tmp_path):
    return tmp_path / "f.db"


@pytest.fixture
def client(db):
    store, c = make_client(db)
    with c:
        yield c
    store.close()


def page_config(html):
    return json.loads(
        html.split('id="intake-config" type="application/json">', 1)[1].split("</script>", 1)[0]
    )


def core_script(html):
    return html.split('<script id="intake-core">', 1)[1].split("</script>", 1)[0]


def test_page_served_with_ecc_name_and_no_external_urls(client, monkeypatch):
    monkeypatch.setenv("ECC_NAME", "ECC North")
    r = client.get("/intake")
    assert r.status_code == 200
    html = r.text
    assert "Intake simulator" in html and "ECC North" in html
    assert "[ECC NAME]" not in html
    # Works offline: no external URL of any kind, fonts from /static.
    assert not re.search(r"https?:|//fonts|googleapis|gstatic", html)
    assert "/static/fonts/atkinson-hyperlegible-regular.woff2" in html
    assert client.get("/static/fonts/atkinson-hyperlegible-regular.woff2").status_code == 200
    # Log from the DB, append-only.
    assert "localStorage" not in html
    assert "Clear log" not in html and "data-del" not in html
    assert "/intake/log.json" in html and "'/requests'" in html and "'/requests/bulk'" in html
    for el in (
        "raw",
        "channel",
        "contact",
        "lang",
        "samples",
        "paste",
        "copy",
        "save-csv",
        "template",
        "base",
    ):
        assert f'id="{el}"' in html
    cfg = page_config(html)
    assert cfg["max_text"] == api.MAX_TEXT_CHARS == 5000
    assert cfg["max_rows"] == api.MAX_BULK_ROWS == 1000
    assert cfg["max_contact"] == api.MAX_CONTACT_CHARS
    assert cfg["channels"] == list(api.CHANNELS)
    assert cfg["log_limit"] == LOG_LIMIT == 200


def test_default_ecc_name(client, monkeypatch):
    monkeypatch.delenv("ECC_NAME", raising=False)
    assert "ECC 1" in client.get("/intake").text


def test_log_json_lists_requests_newest_first_with_result(client, db):
    assert client.get("/intake/log.json").json() == {"requests": []}
    one = client.post(
        "/requests",
        json={
            "raw_text": "help we are trapped, water rising, 3 people",
            "channel": "sms",
            "contact": "+17135550199",
            "lang_hint": "en",
        },
    )
    assert one.status_code == 200, one.text
    csv_body = (
        "raw_text,channel,contact,lang_hint\n"
        '"We are on the roof at 12 Oak Street, 3 people, please help",sms,+17135550199,\n'
        "anyone know if the HEB on Kirby is open?,social,,\n"
    )
    bulk = client.post("/requests/bulk", content=csv_body, headers={"content-type": "text/csv"})
    assert bulk.status_code == 200, bulk.text
    ids = [one.json()["request_id"]] + [r["request_id"] for r in bulk.json()["results"]]

    log = client.get("/intake/log.json").json()["requests"]
    assert [e["request_id"] for e in log] == ids[::-1]
    first = log[-1]
    assert first["raw_text"] == "help we are trapped, water rising, 3 people"
    assert (
        first["channel"] == "sms"
        and first["contact"] == "+17135550199"
        and first["lang_hint"] == "en"
    )
    assert [e["incident"] for e in log] == ["D01-0003", "D01-0002", "D01-0001"]
    assert first["received_at"].endswith("Z")
    assert set(first) == {
        "request_id",
        "incident",
        "received_at",
        "raw_text",
        "channel",
        "contact",
        "lang_hint",
        "result",
    }
    words = {
        "Needs help now",
        "Needs help soon",
        "Can wait",
        "Check first",
        "Set aside",
        "Folded into another card",
    }
    assert all(e["result"] in words for e in log)
    decisions = [one.json()["decision"]] + [r["decision"] for r in bulk.json()["results"]]
    assert [e["result"] for e in log] == [result_words(d) for d in decisions][::-1]

    # Every add went through the normal pipeline: received, extracted, decided events.
    store = client.app.state.store
    for rid in ids:
        types = [e["type"] for e in store.request_events(rid)]
        assert types[:3] == ["received", "extracted", "decided"]
    assert store.outbox_count() >= 9
    assert "12 Oak Street" in client.get("/").text  # the board

    # Reloading shows the same log: it comes from the DB file, not the browser.
    store2, c2 = make_client(db)
    with c2:
        assert c2.get("/intake/log.json").json()["requests"] == log
    store2.close()


def test_api_error_adds_nothing(client):
    r = client.post("/requests", json={"raw_text": "x" * 5001})
    assert r.status_code == 422 and "5000" in r.json()["detail"]
    bad = client.post(
        "/requests/bulk",
        content="raw_text,channel\nok,sms\nbad,fax\n",
        headers={"content-type": "text/csv"},
    )
    assert bad.status_code == 422 and bad.json()["row"] == 2
    assert client.get("/intake/log.json").json()["requests"] == []


def test_intake_log_pending_result_and_limit():
    def ev(seq, typ, rid, payload):
        return {
            "type": typ,
            "request_id": rid,
            "occurred_at": f"2026-11-07T10:00:{seq:02d}Z",
            "payload": payload,
        }

    events = [
        ev(1, "received", "REQ000000000000000000000AAAA", {"raw_text": "a", "channel": "sms"}),
        ev(
            2,
            "decided",
            "REQ000000000000000000000AAAA",
            {"engine_output": {"outcome": "prioritized", "priority_tier": "P1"}},
        ),
        ev(3, "received", "REQ000000000000000000000BBBB", {"raw_text": "b", "channel": "form"}),
    ]
    log = intake_log(events)
    assert [e["incident"] for e in log] == ["BBBB", "AAAA"]
    assert log[0]["result"] is None and log[1]["result"] == "Needs help now"
    assert [e["incident"] for e in intake_log(events, limit=1)] == ["BBBB"]
    assert result_words({"outcome": "abstain"}) == "Check first"
    assert result_words({"outcome": "weird"}) is None


NODE_HARNESS = r"""
const fs = require("fs");
const [, , corePath, cfgJson] = process.argv;
const make = require(corePath);
const core = make(JSON.parse(cfgJson));
const scenario = core.SAMPLES.find((s) => s.items.length === 3);
const pasted = core.readBulk(
  "raw_text,channel,contact,lang_hint\n" +
    '"help, ""quoted"" text, 3 people",,+1713,ES\n\nplain line,social,,\n',
  "sms", false);
const lines = core.readBulk("first message\n\nsecond, with comma\n", "call_note", false);
const many = core.readBulk("raw_text\n" + "x\n".repeat(1001), "csv", false);
const v = (f) => core.validate(f, "form").error || null;
console.log(JSON.stringify({
  scenarioCsv: core.toCSV(scenario.items),
  pastedCsv: core.toCSV(pasted.rows.filter((r) => r.item).map((r) => r.item)),
  linesCsv: core.toCSV(lines.rows.map((r) => r.item)),
  linesMode: lines.mode,
  tooMany: many.fatal,
  errors: {
    long: v({ raw_text: "x".repeat(5001) }),
    longEmoji: v({ raw_text: "\u{1F30A}".repeat(5000) }),
    channel: v({ raw_text: "hi", channel: "fax" }),
    lang: v({ raw_text: "hi", lang_hint: "eng" }),
    contact: v({ raw_text: "hi", contact: "1".repeat(201) }),
    empty: v({ raw_text: "   " }),
  },
}));
"""


def run_core(tmp_path, html):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    core = tmp_path / "core.js"
    core.write_text(core_script(html))
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS)
    done = subprocess.run(
        [node, str(harness), str(core), json.dumps(page_config(html))],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(done.stdout)


def test_csv_the_page_builds_is_accepted_by_bulk(client, tmp_path):
    out = run_core(tmp_path, client.get("/intake").text)
    assert out["linesMode"] == "lines"
    assert (
        out["tooMany"] == "The field app takes at most 1,000 rows per upload. This file has 1,001."
    )
    errs = out["errors"]
    assert errs["long"] == "Message is longer than 5,000 characters."
    assert errs["longEmoji"] is None  # counted in characters, like api.py
    assert errs["channel"] == "Channel must be one of: sms, social, form, call_note, csv."
    assert errs["lang"] == "Language must be a two-letter code, like en or es."
    assert errs["contact"] == "Contact is longer than 200 characters."
    assert errs["empty"] == "Message is required."

    posted = []
    for key, n in (("scenarioCsv", 3), ("pastedCsv", 2), ("linesCsv", 2)):
        r = client.post(
            "/requests/bulk", content=out[key].encode(), headers={"content-type": "text/csv"}
        )
        assert r.status_code == 200, (key, r.text)
        assert r.json()["count"] == n
        posted += [x["request_id"] for x in r.json()["results"]]
    log = client.get("/intake/log.json").json()["requests"]
    assert [e["request_id"] for e in log] == posted[::-1]
    by_text = {e["raw_text"]: e for e in log}
    quoted = by_text['help, "quoted" text, 3 people']
    assert (
        quoted["channel"] == "sms" and quoted["lang_hint"] == "es" and quoted["contact"] == "+1713"
    )
    assert by_text["plain line"]["channel"] == "social"
    assert by_text["second, with comma"]["channel"] == "call_note"
    assert all(e["result"] for e in log)
