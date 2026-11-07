/* Grader mode overlay (UI-8). Loaded only when grader mode is on. Hints come from /grader/hints.json
   (served by this laptop, so they work offline). Samples go through the normal intake routes
   (POST /requests, POST /requests/bulk) and the status route; nothing here changes a decision. */
(function () {
  'use strict';
  var params = new URLSearchParams(location.search);
  if (params.get('grader') === '1') {
    document.cookie = 'grader=1; path=/; max-age=31536000; samesite=lax'; /* remember the Settings link */
  }
  var data = null, panel = null, shown = {};

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    return e;
  }
  function online() {
    var c = document.getElementById('connection');
    return navigator.onLine && c && c.getAttribute('data-state') === 'online';
  }
  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) throw new Error((j && (j.detail || j.error)) || ('HTTP ' + r.status));
          return j;
        });
      });
  }

  function runSample(sample, say) {
    if (sample.kind === 'scoreboard') {
      if (!data.scoreboard_url) { say('No cloud URL is set on this laptop, so there is no scoreboard to open.'); return; }
      if (!online()) { say('The scoreboard needs a connection. Try again when the header says Online.'); return; }
      window.open(data.scoreboard_url, '_blank', 'noopener');
      say('Opened the scoreboard in a new tab.');
      return;
    }
    var job;
    if (sample.kind === 'offline') {
      job = post('/sync/offline-toggle').then(function (j) { return j.paused ? 'Now simulating no internet.' : 'Back online.'; });
    } else if (sample.kind === 'bulk') {
      job = post('/requests/bulk', { text: sample.text, channel: sample.channel }).then(function (j) { return 'Sent ' + j.count + ' messages.'; });
    } else {
      job = sample.items.reduce(function (p, item) {
        return p.then(function (ids) { return post('/requests', item).then(function (j) { ids.push(j.request_id); return ids; }); });
      }, Promise.resolve([])).then(function (ids) {
        if (!sample.then) return 'Sent ' + ids.length + ' message' + (ids.length === 1 ? '' : 's') + '.';
        return post('/requests/' + encodeURIComponent(ids[ids.length - 1]) + '/status', sample.then)
          .then(function () { return 'Sent the message and the boat.'; });
      });
    }
    say('Sending…');
    job.then(function (msg) { say(msg + ' Reloading…'); setTimeout(function () { location.reload(); }, 600); },
      function (err) { say('Could not load the sample: ' + err.message); });
  }

  function renderCase(uc) {
    var li = el('li', { 'class': 'grader-case', id: 'grader-' + uc.id });
    var details = el('details');
    details.appendChild(el('summary', null, uc.id + ' · ' + uc.title));
    var list = el('ol', { 'class': 'grader-hints' });
    var status = el('p', { 'class': 'grader-status', role: 'status', 'aria-live': 'polite' });
    var more = el('button', { type: 'button', 'class': 'grader-more' });
    function say(msg) { status.textContent = msg; }
    function draw() {
      var n = shown[uc.id] || 0;
      list.textContent = '';
      uc.hints.slice(0, n).forEach(function (h) {
        var item = el('li', { 'data-level': h.level });
        item.appendChild(el('strong', null, h.level + ' · ' + h.label + ': '));
        item.appendChild(document.createTextNode(h.text));
        if (h.sample) {
          var b = el('button', { type: 'button', 'class': 'grader-sample' }, h.sample.label);
          b.addEventListener('click', function () { runSample(h.sample, say); });
          item.appendChild(el('br'));
          item.appendChild(b);
        }
        list.appendChild(item);
      });
      more.hidden = n >= uc.hints.length;
      if (!more.hidden) more.textContent = 'Show hint ' + (n + 1) + ': ' + uc.hints[n].label;
    }
    more.addEventListener('click', function () { shown[uc.id] = (shown[uc.id] || 0) + 1; draw(); });
    draw();
    details.appendChild(list);
    details.appendChild(more);
    details.appendChild(status);
    li.appendChild(details);
    return li;
  }

  function build() {
    panel = el('div', { id: 'grader-panel', 'class': 'grader-panel', role: 'dialog', 'aria-modal': 'false', 'aria-labelledby': 'grader-title' });
    var head = el('div', { 'class': 'grader-head' });
    head.appendChild(el('h2', { id: 'grader-title' }, 'Grader hints'));
    var close = el('button', { type: 'button', 'class': 'grader-close', 'aria-label': 'Close hints' }, '×');
    close.addEventListener('click', toggle);
    head.appendChild(close);
    panel.appendChild(head);
    panel.appendChild(el('p', { 'class': 'grader-note' }, 'Hints never change a result. Samples go through the normal intake.'));
    var ul = el('ul', { 'class': 'grader-cases' });
    data.use_cases.forEach(function (uc) { ul.appendChild(renderCase(uc)); });
    panel.appendChild(ul);
    var off = el('a', { href: '/grader/mode?on=0', 'class': 'grader-off' }, 'Turn off grader mode in this browser');
    panel.appendChild(off);
    document.body.appendChild(panel);
  }

  function toggle() {
    var btn = document.getElementById('grader-help');
    if (panel && !panel.hidden) { panel.hidden = true; btn && btn.setAttribute('aria-expanded', 'false'); btn && btn.focus(); return; }
    var open = function () { panel.hidden = false; btn && btn.setAttribute('aria-expanded', 'true'); panel.querySelector('button').focus(); };
    if (panel) { open(); return; }
    fetch('/grader/hints.json').then(function (r) { return r.json(); }).then(function (j) { data = j; build(); open(); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('grader-help');
    if (btn) btn.addEventListener('click', toggle);
    document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && panel && !panel.hidden) toggle(); });
  });
})();
