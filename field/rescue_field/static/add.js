// Add messages screen (UI-6). Pasted lines go to POST /requests/bulk in chunks; a CSV goes as one
// upload; a single message goes to POST /requests. Grouping mirrors add_page.group_of.
(function () {
  "use strict";
  var cfg = JSON.parse(document.getElementById("add-config").textContent);
  var progress = document.getElementById("add-progress");
  var errorBox = document.getElementById("add-error");
  var summary = document.getElementById("add-summary");
  var groupList = document.getElementById("add-groups");
  var skippedLine = document.getElementById("add-skipped");
  var busy = false;
  // The same line breaks as Python's str.splitlines (what /requests/bulk numbers rows by).
  var LINE_BREAK = /\r\n|[\n\v\f\r\x1c\x1d\x1e\x85\u2028\u2029]/;
  // Blank as Python's str.strip sees it (JS \s lacks \x1c-\x1f and \x85).
  var BLANK = /^[\s\x1c-\x1f\x85]*$/;
  var TRAILING_BREAK = /(?:\r\n|[\n\v\f\r\x1c\x1d\x1e\x85\u2028\u2029])$/;

  function groupOf(d) {
    if (d.outcome === "abstain" || d.outcome === "not_a_request" || d.outcome === "duplicate") return d.outcome;
    if (d.outcome === "prioritized" && /^P[123]$/.test(d.priority_tier)) return d.priority_tier;
    throw new Error("unexpected engine output: " + d.outcome);
  }

  function reset() {
    errorBox.hidden = true;
    errorBox.textContent = "";
    summary.hidden = true;
    progress.textContent = "";
  }

  function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = false;
  }

  function showSummary(decisions, skipped) {
    var counts = {};
    cfg.groups.forEach(function (g) { counts[g[0]] = 0; });
    decisions.forEach(function (d) { counts[groupOf(d)] += 1; });
    groupList.textContent = "";
    cfg.groups.forEach(function (g) {
      var li = document.createElement("li");
      li.dataset.group = g[0];
      var strong = document.createElement("strong");
      strong.textContent = String(counts[g[0]]);
      li.appendChild(strong);
      li.appendChild(document.createTextNode(" " + g[1]));
      groupList.appendChild(li);
    });
    skippedLine.textContent = skipped ? skipped + (skipped === 1 ? " blank line skipped." : " blank lines skipped.") : "";
    summary.hidden = false;
  }

  async function post(url, body, headers) {
    var resp = await fetch(url, { method: "POST", body: body, headers: headers || {} });
    var data = null;
    try { data = await resp.json(); } catch (e) { data = null; }
    return { ok: resp.ok, status: resp.status, data: data };
  }

  function detail(r) {
    return (r.data && r.data.detail) || ("the laptop answered " + r.status);
  }

  async function sendPaste(text) {
    var lines = [];
    var skipped = 0;
    text.split(LINE_BREAK).forEach(function (line, i) {
      if (!BLANK.test(line)) lines.push({ n: i + 1, text: line }); else skipped += 1;
    });
    if (!lines.length) { showError("Nothing to sort: every line is blank."); return; }
    if (lines.length > cfg.max_rows) { showError("At most " + cfg.max_rows + " messages at a time."); return; }
    // A trailing newline is not a skipped blank line.
    if (TRAILING_BREAK.test(text)) skipped -= 1;
    var decisions = [];
    var total = lines.length;
    progress.textContent = "Sorted 0 of " + total;
    for (var start = 0; start < total; start += cfg.chunk) {
      var chunk = lines.slice(start, start + cfg.chunk);
      var r;
      try {
        r = await post("/requests/bulk", JSON.stringify({ text: chunk.map(function (l) { return l.text; }).join("\n") }),
          { "Content-Type": "application/json" });
      } catch (e) {
        r = { ok: false, status: 0, data: { detail: "could not reach the laptop app" } };
      }
      var first = chunk[0].n, last = chunk[chunk.length - 1].n;
      if (r.ok && r.data.results.length !== chunk.length) {
        r = { ok: false, status: r.status, data: { detail: "the laptop sorted " + r.data.results.length +
          " of " + chunk.length + " lines" } };
      }
      if (!r.ok) {
        var where = r.data && r.data.row && chunk[r.data.row - 1] ? "Row " + chunk[r.data.row - 1].n + ": " : "";
        showError(where + detail(r) + ". Rows " + first + " to " + last + " and every row after them were not added" +
          (decisions.length ? "; the " + decisions.length + " messages before them were." : "."));
        if (decisions.length) showSummary(decisions, skipped);
        return;
      }
      r.data.results.forEach(function (row) { decisions.push(row.decision); });
      progress.textContent = "Sorted " + decisions.length + " of " + total;
    }
    showSummary(decisions, skipped);
  }

  async function sendCsv(file) {
    var form = new FormData();
    form.append("file", file);
    progress.textContent = "Sorting the file…";
    var r = await post("/requests/bulk", form);
    if (!r.ok) {
      progress.textContent = "";
      showError((r.data && r.data.row ? "CSV row " + r.data.row + ": " : "") + detail(r) + ". Nothing from this file was added.");
      return;
    }
    var decisions = r.data.results.map(function (row) { return row.decision; });
    progress.textContent = "Sorted " + decisions.length + " of " + decisions.length;
    showSummary(decisions, 0);
  }

  async function sendSingle(form) {
    var body = { raw_text: form.raw_text.value, channel: form.channel.value };
    if (form.contact.value.trim()) body.contact = form.contact.value;
    var r = await post("/requests", JSON.stringify(body), { "Content-Type": "application/json" });
    if (!r.ok) { showError("Not added: " + detail(r) + "."); return; }
    progress.textContent = "Sorted 1 of 1";
    showSummary([r.data.decision], 0);
    form.reset();
  }

  function wire(id, fn) {
    document.getElementById(id).addEventListener("submit", async function (ev) {
      ev.preventDefault();
      if (busy) return;
      busy = true;
      reset();
      try { await fn(ev.target); } catch (e) { showError(String(e.message || e)); }
      finally { busy = false; }
    });
  }

  wire("paste-form", function (f) { return sendPaste(f.text.value); });
  wire("csv-form", function (f) { return sendCsv(f.file.files[0]); });
  wire("single-form", sendSingle);
})();
