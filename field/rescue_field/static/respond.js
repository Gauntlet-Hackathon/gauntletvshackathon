// Respond mode (UI-5): boat memory, OSM chips with the radio line, and outcome buttons that post
// one status event each to POST /requests/{id}/status. The chips save nothing.

// The status body for an outcome button; a string is a message to show instead of posting.
function respondBody(action, v) {
  switch (action) {
    case "all": return {status: "rescued", rescued_count: v.allCount, note: "to: " + v.shelter};
    case "some":
      if (v.someCount === "") return "Say how many you got out.";
      return {status: "escalated", reason: "Got " + v.someCount + " out, others still there"};
    case "nobody": return {status: "rescued", rescued_count: 0, note: "nobody there"};
    case "noreach": return {status: "escalated", reason: v.noreachReason || "Couldn't get there"};
    case "decline": return {status: "escalated", reason: v.declineReason};
  }
  return "Unknown button.";
}

function initRespond() {
  const KEY = "respond.crew";
  const root = document.getElementById("respond");
  const crew = root.dataset.crew;
  const params = new URLSearchParams(location.search);
  try {
    if (crew) localStorage.setItem(KEY, crew);
    else if (!params.has("crew") && localStorage.getItem(KEY)) {
      location.replace("/respond?crew=" + encodeURIComponent(localStorage.getItem(KEY)));
      return;
    }
  } catch (e) { /* no storage: the crew picks each time */ }

  const job = root.querySelector(".job-card");
  const msg = document.getElementById("respond-msg");
  const tell = document.getElementById("tell");
  const line = document.getElementById("tell-line");
  const outcomes = document.getElementById("outcomes");
  root.querySelectorAll(".osm").forEach((chip) => chip.addEventListener("click", () => {
    root.querySelectorAll(".osm").forEach((c) => {
      c.setAttribute("aria-pressed", c === chip ? "true" : "false");
      c.classList.toggle("selected", c === chip);
    });
    line.textContent = chip.dataset.line;
    tell.hidden = false;
    outcomes.hidden = !["onscene", "done"].includes(chip.dataset.step);
  }));
  const copyBtn = document.getElementById("copy-line");
  if (copyBtn) copyBtn.addEventListener("click", async () => {
    const text = line.textContent;
    try { await navigator.clipboard.writeText(text); }
    catch (e) {
      const t = document.createElement("textarea");
      t.value = text; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove();
    }
    copyBtn.textContent = "Copied";
    setTimeout(() => { copyBtn.textContent = "Copy"; }, 2000);
  });

  const val = (id) => document.getElementById(id).value.trim();
  const body = (btn) => respondBody(btn.dataset.action, {
    allCount: val("all-count"), shelter: val("shelter"), someCount: val("some-count"),
    noreachReason: val("noreach-reason"), declineReason: btn.dataset.reason,
  });
  let busy = false;
  root.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button.outcome");
    if (!btn || !job || busy) return;
    const data = body(btn);
    if (typeof data === "string") { msg.textContent = data; return; }
    busy = true;
    root.querySelectorAll("button.outcome").forEach((b) => { b.disabled = true; });
    try {
      const r = await fetch("/requests/" + encodeURIComponent(job.dataset.requestId) + "/status", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data),
      });
      const out = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(out.detail || ("HTTP " + r.status));
      msg.textContent = "Saved. Dispatch will see it when the laptop syncs.";
      setTimeout(() => location.reload(), 1500);
    } catch (e) {
      msg.textContent = "Not saved: " + e.message;
      busy = false;
      root.querySelectorAll("button.outcome").forEach((b) => { b.disabled = false; });
    }
  });
}

if (typeof module !== "undefined") module.exports = {respondBody};
else initRespond();
