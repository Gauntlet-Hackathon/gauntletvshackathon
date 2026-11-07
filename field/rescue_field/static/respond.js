// Respond mode (UI-5). The crew name is remembered in this browser. OSM chips only pick a radio
// line (not saved). Each outcome form posts one body to POST /requests/{id}/status; bodyFor
// mirrors respond.outcome_body in Python.
(function () {
  "use strict";
  var STORE_KEY = "rescue.respond.crew";

  function bodyFor(kind, v) {
    if (kind === "all") return { status: "rescued", rescued_count: v.count, note: "to: " + v.shelter };
    if (kind === "some") return { status: "escalated", reason: "Got " + v.count + " out, others still there" };
    if (kind === "nobody") return { status: "rescued", rescued_count: 0, note: "nobody there" };
    if (kind === "noreach") {
      var text = (v.reason || "").trim();
      return { status: "escalated", reason: text ? "Couldn't get there: " + text : "Couldn't get there" };
    }
    if (kind === "decline") return { status: "escalated", reason: v.boat + " can't take this one" };
    throw new Error("unknown outcome " + kind);
  }

  if (typeof module !== "undefined" && module.exports) module.exports = { bodyFor: bodyFor };
  if (typeof document === "undefined") return;

  var cfg = JSON.parse(document.getElementById("respond-config").textContent);
  var storage = null;
  try { storage = window.localStorage; } catch (e) { storage = null; }

  if (cfg.crew) {
    if (storage) storage.setItem(STORE_KEY, cfg.crew);
  } else if (storage && storage.getItem(STORE_KEY) && !/[?&]crew=/.test(window.location.search)) {
    window.location.replace("/respond?crew=" + encodeURIComponent(storage.getItem(STORE_KEY)));
    return;
  }
  if (!cfg.request_id) return;

  var errorBox = document.getElementById("respond-error");
  var okBox = document.getElementById("respond-ok");
  var radio = document.getElementById("radio");
  var radioLine = document.getElementById("radio-line");
  var outcomes = document.getElementById("outcomes");
  var headline = document.getElementById("respond-headline");
  var HEADLINES = { assigned: "Dispatch sent you a rescue", enroute: "Heading there",
    onscene: "You’re there. How’s it going?", done: "Nice work. How did it go?" };
  var busy = false;

  Array.prototype.forEach.call(document.querySelectorAll(".osm button"), function (btn, _i, all) {
    btn.addEventListener("click", function () {
      var step = btn.dataset.step;
      Array.prototype.forEach.call(all, function (b) { b.setAttribute("aria-pressed", String(b === btn)); });
      radioLine.textContent = cfg.radio[step] || "";
      radio.hidden = false;
      headline.textContent = HEADLINES[step];
      outcomes.hidden = !(step === "onscene" || step === "done");
    });
  });

  document.getElementById("radio-copy").addEventListener("click", function () {
    var text = radioLine.textContent;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(function () {});
    }
  });

  function setDisabled(on) {
    Array.prototype.forEach.call(document.querySelectorAll("form[data-kind] button"), function (b) {
      b.disabled = on;
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll("form[data-kind]"), function (form) {
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      if (busy) return;  // one tap, one event
      busy = true;
      setDisabled(true);
      errorBox.hidden = true;
      var f = form.elements;
      var body = bodyFor(form.dataset.kind, {
        boat: cfg.crew,
        count: f.count ? Number(f.count.value) : null,
        shelter: f.shelter ? f.shelter.value : null,
        reason: f.reason ? f.reason.value : null,
      });
      try {
        var res = await fetch("/requests/" + encodeURIComponent(cfg.request_id) + "/status", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        var data = await res.json().catch(function () { return {}; });
        if (!res.ok) throw new Error(data.detail || ("Status " + res.status));
        okBox.textContent = "Saved. Dispatch will see it when the laptop syncs.";
        okBox.hidden = false;
        window.setTimeout(function () { window.location.reload(); }, 1500);
      } catch (e) {
        errorBox.textContent = "Not saved: " + String(e.message || e);
        errorBox.hidden = false;
        busy = false;
        setDisabled(false);
      }
    });
  });
})();
