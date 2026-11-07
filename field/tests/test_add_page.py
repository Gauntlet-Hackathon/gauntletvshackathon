import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescue_field.add_page import CHUNK_LINES, GROUP_KEYS, GROUPS, group_of, summarize
from rescue_field.api import create_app, default_decide_batch
from rescue_field.store import EventStore
from rescue_shared.contracts_io import CONTRACTS

ADD_JS = Path(__file__).resolve().parents[1] / "rescue_field" / "static" / "add.js"
CASES = [
    json.loads(ln)
    for ln in (CONTRACTS / "fixtures/cases.jsonl").read_text().splitlines()
    if ln.strip()
]


@pytest.fixture
def client(tmp_path):
    store = EventStore(tmp_path / "f.db", check_same_thread=False)
    with TestClient(create_app(store=store, decide_batch=default_decide_batch())) as c:
        yield c
    store.close()


def out(outcome, tier=None):
    return {"outcome": outcome, "priority_tier": tier}


def test_page_served(client):
    r = client.get("/add")
    assert r.status_code == 200
    html = r.text
    assert "Add messages" in html and "CommunityRescue" in html  # inside the app shell
    for el in ("paste-text", "csv-file", "single-text", "single-contact", "single-channel"):
        assert f'id="{el}"' in html
    assert 'maxlength="5000"' in html and 'maxlength="200"' in html
    assert 'href="/">Show the most urgent' in html
    assert '<script src="/static/add.js">' in html
    cfg = json.loads(html.split('type="application/json">', 1)[1].split("</script>", 1)[0])
    assert cfg["chunk"] == CHUNK_LINES == 10
    assert [g[1] for g in cfg["groups"]] == [
        "Needs help now",
        "Needs help soon",
        "Can wait",
        "Check first",
        "Set aside",
        "Folded into another card",
    ]
    assert "call_note" in cfg["channels"]
    for value, words in (("sms", "Text message"), ("social", "Social post"), ("form", "Web form"),
                         ("call_note", "Call note"), ("csv", "CSV")):
        if value in cfg["channels"]:
            assert re.search(rf'<option value="{value}"( selected)?>{words}</option>', html), value
    assert client.get("/static/add.js").status_code == 200


def test_group_of_and_summarize():
    assert group_of(out("prioritized", "P1")) == "P1"
    assert group_of(out("prioritized", "P3")) == "P3"
    assert group_of(out("abstain")) == "abstain"
    assert group_of(out("not_a_request")) == "not_a_request"
    # a duplicate keeps its tier but is counted as folded in, never twice
    assert group_of(out("duplicate", "P1")) == "duplicate"
    with pytest.raises(ValueError):
        group_of(out("nonsense"))
    counts = summarize([out("prioritized", "P1"), out("duplicate", "P1"), out("abstain")])
    assert list(counts) == list(GROUP_KEYS)
    assert counts == {
        "P1": 1,
        "P2": 0,
        "P3": 0,
        "abstain": 1,
        "not_a_request": 0,
        "duplicate": 1,
    }


def test_chunked_paste_counts_add_up(client):
    """The flow add.js runs, with the real engine: the bulk response shape it relies on
    (results[].decision with outcome/priority_tier) and counts summing to the lines sent."""
    lines = [c["raw_text"].replace("\n", " ") for c in CASES]
    decisions = []
    for start in range(0, len(lines), CHUNK_LINES):
        chunk = lines[start : start + CHUNK_LINES]
        r = client.post("/requests/bulk", json={"text": "\n".join(chunk)})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == len(chunk) == len(body["results"])
        assert [row["row"] for row in body["results"]] == list(range(1, len(chunk) + 1))
        decisions += [row["decision"] for row in body["results"]]
    counts = summarize(decisions)
    assert sum(counts.values()) == len(lines)
    assert sum(1 for v in counts.values() if v) >= 2  # fixtures cover more than one group


def test_failing_row_reports_its_row(client):
    r = client.post("/requests/bulk", json={"text": "fine\n" + "x" * 5001 + "\nfine"})
    assert r.status_code == 422 and r.json()["row"] == 2


NODE_HARNESS = textwrap.dedent(
    r"""
    const fs = require("fs");
    const [js, cfg, paste, failChunk, failRow] = process.argv.slice(2);
    const els = {};
    function el(id) {
      return els[id] || (els[id] = {
        id, hidden: true, textContent: "", children: [], dataset: {}, listeners: {},
        appendChild(c) { this.children.push(c); },
        addEventListener(t, f) { this.listeners[t] = f; },
      });
    }
    el("add-config").textContent = cfg;
    global.document = {
      getElementById: el,
      createElement: () => ({ children: [], dataset: {}, textContent: "",
        appendChild(c) { this.children.push(c); } }),
      createTextNode: (t) => ({ textContent: t }),
    };
    const sent = [];
    let call = 0;
    global.fetch = async (url, opts) => {
      const n = ++call;
      const lines = JSON.parse(opts.body).text.split("\n");
      sent.push(lines);
      if (n === Number(failChunk)) {
        return { ok: false, status: 422,
          json: async () => ({ error: "invalid_input", detail: "bad", row: Number(failRow) }) };
      }
      const tiers = ["P1", "P2", "P3"];
      return { ok: true, status: 200, json: async () => ({ count: lines.length, results: lines.map(
        (l, i) => ({ row: i + 1, decision: l.startsWith("dup") ? { outcome: "duplicate",
          priority_tier: "P1" } : { outcome: "prioritized", priority_tier: tiers[i % 3] } })) }) };
    };
    eval(fs.readFileSync(js, "utf8"));
    const form = { text: { value: paste } };
    el("paste-form").listeners.submit({ preventDefault() {}, target: form }).then(() => {
      const groups = {};
      el("add-groups").children.forEach((li) => {
        groups[li.dataset.group] = Number(li.children[0].textContent);
      });
      console.log(JSON.stringify({
        sent, groups, progress: el("add-progress").textContent,
        error: el("add-error").hidden ? null : el("add-error").textContent,
        summary: !el("add-summary").hidden, skipped: el("add-skipped").textContent,
      }));
    });
    """
)


def run_js(tmp_path, paste, fail_chunk=0, fail_row=0):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS)
    cfg = json.dumps({"chunk": CHUNK_LINES, "groups": [list(g) for g in GROUPS], "max_rows": 1000})
    args = [node, str(harness), str(ADD_JS), cfg, paste, str(fail_chunk), str(fail_row)]
    done = subprocess.run(args, capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout)


def test_js_chunks_progress_and_summary(tmp_path):
    lines = [f"dup {n}" if n % 7 == 0 else f"msg {n}" for n in range(1, 51)]
    paste = "\n".join(lines[:12]) + "\n\n  \n" + "\n".join(lines[12:]) + "\n"
    res = run_js(tmp_path, paste)
    assert [len(c) for c in res["sent"]] == [10, 10, 10, 10, 10]
    assert sum(res["sent"], []) == lines
    assert res["progress"] == "Sorted 50 of 50"
    assert res["error"] is None and res["summary"]
    assert sum(res["groups"].values()) == 50
    assert res["groups"]["duplicate"] == 7
    assert list(res["groups"]) == list(GROUP_KEYS)
    assert res["skipped"] == "2 blank lines skipped."


def test_js_failing_chunk_shows_row_of_whole_paste(tmp_path):
    # two blank lines before the second chunk shift its paste line numbers by 2
    lines = [f"msg {n}" for n in range(1, 26)]
    paste = "\n".join(lines[:10]) + "\n\n\n" + "\n".join(lines[10:])
    res = run_js(tmp_path, paste, fail_chunk=2, fail_row=3)
    # chunk 2 holds messages 11-20 on paste lines 13-22; its row 3 is paste line 15
    assert res["error"].startswith("Row 15: bad.")
    assert "Rows 13 to 22" in res["error"] and "the 10 messages before them were" in res["error"]
    assert len(res["sent"]) == 2  # stops after the failing chunk
    assert res["summary"] and sum(res["groups"].values()) == 10
    assert res["progress"] == "Sorted 10 of 25"
