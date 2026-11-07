// Respond mode (UI-5). Remembers the boat in this browser, copies the radio line, and posts each
// outcome to POST /requests/{id}/status once (buttons are disabled while a post is in flight).
(function () {
  "use strict";
  var KEY = "respond-boat";
  var cfg = JSON.parse(document.getElementById("respond-config").textContent);
  var params = new URLSearchParams(location.search);
  var store = null;
  try { store = window.localStorage; } catch (e) { store = null; }

  if (cfg.boat) {
    if (store) store.setItem(KEY, cfg.boat);
  } else if (params.get("pick") === "1") {
    if (store) store.removeItem(KEY);
  } else if (store && store.getItem(KEY)) {
    location.replace("/respond?boat=" + encodeURIComponent(store.getItem(KEY)));
    return;
  }

  var copy = document.querySelector(".copy");
  if (copy) {
    copy.addEventListener("click", function () {
      var text = copy.dataset.copy;
      function fallback() {
        var t = document.createElement("textarea");
        t.value = text; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove();
      }
      if (navigator.clipboard) navigator.clipboard.writeText(text).catch(fallback); else fallback();
      copy.textContent = "Copied";
      setTimeout(function () { copy.textContent = "Copy"; }, 2000);
    });
  }

  var errorBox = document.getElementById("respond-error");
  var saved = document.getElementById("respond-saved");
  var forms = Array.prototype.slice.call(document.querySelectorAll("form.status-form"));
  var busy = false;

  function setBusy(on) {
    busy = on;
    forms.forEach(function (f) {
      Array.prototype.forEach.call(f.querySelectorAll("button"), function (b) { b.disabled = on; });
    });
  }

  // The status API body for one outcome form (mirrors the hidden fields in respond.html).
  function bodyFor(form) {
    var data = new FormData(form);
    var body = new URLSearchParams();
    if (form.dataset.outcome === "some") {
      body.set("status", "escalated");
      body.set("reason", "Got " + String(data.get("got_count")).trim() + " out, others still there");
      return body;
    }
    data.forEach(function (v, k) { body.set(k, v); });
    return body;
  }

  forms.forEach(function (form) {
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (busy) return;
      setBusy(true);
      errorBox.hidden = true;
      fetch(form.action, { method: "POST", body: bodyFor(form) })
        .then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; });
        })
        .then(function (res) {
          if (!res.ok) throw new Error(res.j.detail || "Could not save.");
          saved.textContent = "Saved.";
          saved.hidden = false;
          location.assign("/respond?boat=" + encodeURIComponent(cfg.boat));
        })
        .catch(function (e) {
          errorBox.textContent = "Could not save: " + e.message;
          errorBox.hidden = false;
          setBusy(false);
        });
    });
  });

  // No job yet: check every 10 s and reload when dispatch sends one.
  if (cfg.boat && document.getElementById("no-job")) {
    setInterval(function () {
      fetch("/respond/job.json?boat=" + encodeURIComponent(cfg.boat))
        .then(function (r) { return r.json(); })
        .then(function (j) { if (j.job) location.reload(); })
        .catch(function () {});
    }, 10000);
  }
})();
