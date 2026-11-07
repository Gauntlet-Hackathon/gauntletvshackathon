// Respond mode (UI-5): remember the boat, OSM chips pick a radio line, outcome buttons post
// one status event each to POST /requests/{id}/status. Everything is local: works offline.
(function () {
  "use strict";
  var KEY = "communityrescue.respond.crew";
  var params = new URLSearchParams(location.search);
  var crew = (params.get("crew") || "").trim();
  try {
    if (crew) localStorage.setItem(KEY, crew);
    else if (localStorage.getItem(KEY)) {
      location.replace("/respond?crew=" + encodeURIComponent(localStorage.getItem(KEY)));
      return;
    }
  } catch (e) { /* storage blocked: the form still works */ }

  var cfgEl = document.getElementById("respond-job");
  if (!cfgEl) return;
  var job = JSON.parse(cfgEl.textContent);
  var $ = function (id) { return document.getElementById(id); };
  var HEAD = { assigned: "Dispatch sent you a rescue", enroute: "Heading there",
    onscene: "You’re there. How’s it going?", done: "Nice work. Where did they go?" };
  var busy = false;

  document.querySelectorAll("#osm-chips [data-step]").forEach(function (chip) {
    chip.addEventListener("click", function () {
      var step = chip.getAttribute("data-step");
      document.querySelectorAll("#osm-chips [data-step]").forEach(function (c) {
        var on = c === chip;
        c.setAttribute("aria-pressed", on ? "true" : "false");
        c.classList.toggle("selected", on);
      });
      $("headline").textContent = HEAD[step];
      $("radio-line").textContent = job.radio[step];
      $("radio").hidden = false;
      $("outcomes").hidden = !(step === "onscene" || step === "done");
    });
  });

  $("copy-radio").addEventListener("click", function () {
    var text = $("radio-line").textContent;
    var done = function () { $("copy-radio").textContent = "Copied"; };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () {});
    }
  });

  function showError(msg) { $("respond-error").textContent = msg; $("respond-error").hidden = false; }

  function count() {
    var v = $("out-count").value.trim();
    return /^\d+$/.test(v) ? parseInt(v, 10) : null;
  }

  function body(kind) {
    if (kind === "all") {
      var n = count();
      if (n === null) return showError("Enter how many people you got out.");
      return { status: "rescued", rescued_count: n, note: "to: " + $("out-shelter").value };
    }
    if (kind === "some") {
      var m = count();
      if (m === null) return showError("Enter how many people you got out.");
      return { status: "escalated", reason: "Got " + m + " out, others still there" };
    }
    if (kind === "nobody") return { status: "rescued", rescued_count: 0, note: "nobody there" };
    if (kind === "noreach") {
      var why = $("out-reason").value.trim();
      return { status: "escalated", reason: why ? "Couldn't get there: " + why : "Couldn't get there" };
    }
    return { status: "escalated", reason: job.crew + " can't take this one" };
  }

  document.querySelectorAll("[data-outcome]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (busy) return;
      $("respond-error").hidden = true;
      var data = body(btn.getAttribute("data-outcome"));
      if (!data) return;
      busy = true;
      fetch("/requests/" + encodeURIComponent(job.request_id) + "/status", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data)
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (r.ok) { location.reload(); return; }
          busy = false;
          showError(j.detail || ("Could not save (" + r.status + ")"));
        });
      }, function () { busy = false; showError("Could not reach this laptop's app."); });
    });
  });
})();
