/* Grader mode (UI-8): the ? panel with three hint levels per use case and one-tap samples.
   Loaded only when grader mode is on. Hints come from the field app itself (/grader/hints.json),
   so they work with no internet. Samples go through the normal intake (POST /requests and
   POST /requests/bulk); nothing here changes fields, decisions or priority. */
(function () {
  'use strict';
  var YEAR = 60 * 60 * 24 * 365;
  if (new URLSearchParams(location.search).get('grader') === '1') {
    document.cookie = 'grader=1; Max-Age=' + YEAR + '; Path=/; SameSite=Lax';
  }

  var LEVELS = ['1 · What to try', '2 · Load a sample', '3 · What should happen'];
  var panel = null;

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text != null) node.textContent = text;
    return node;
  }

  function online() {
    var c = document.getElementById('connection');
    return navigator.onLine !== false && !!c && c.getAttribute('data-state') === 'online';
  }

  function postJSON(url, body) {
    return fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) {
        if (r.ok) return r.json();
        return r.json().catch(function () { return {}; }).then(function (b) {
          throw new Error(b.detail || ('intake answered ' + r.status));
        });
      });
  }

  function loadSample(sample, status, button) {
    button.disabled = true;
    status.textContent = 'Sending the sample through intake…';
    var chain = Promise.resolve();
    if (sample.kind === 'bulk') {
      chain = postJSON('/requests/bulk', sample.bulk);
    } else {
      (sample.requests || []).forEach(function (item) {
        chain = chain.then(function () { return postJSON('/requests', item); });
      });
    }
    chain.then(function () {
      status.textContent = 'Sample added. Reloading…';
      location.reload();
    }, function (err) {
      button.disabled = false;
      status.textContent = 'The sample could not be added: ' + err.message;
    });
  }

  function sampleBlock(uc, scoreboardUrl) {
    var s = uc.sample, box = el('div', { class: 'grader-sample' });
    box.appendChild(el('p', {}, s.note));
    var status = el('p', { class: 'grader-status', role: 'status', 'aria-live': 'polite' });
    if (s.kind === 'requests' || s.kind === 'bulk') {
      var b = el('button', { type: 'button', class: 'btn', 'data-sample': uc.id }, s.label);
      b.addEventListener('click', function () { loadSample(s, status, b); });
      box.appendChild(b);
    } else if (s.kind === 'scoreboard') {
      if (scoreboardUrl && online()) {
        box.appendChild(el('a', { class: 'btn', id: 'grader-scoreboard', href: scoreboardUrl, target: '_blank', rel: 'noopener' }, s.label));
      } else {
        box.appendChild(el('p', { class: 'grader-offline' }, 'The scoreboard needs a connection to the ECC cloud. Try again when the header says Online.'));
      }
    } else {
      box.appendChild(el('p', { class: 'muted' }, s.label));
    }
    box.appendChild(status);
    return box;
  }

  function useCase(uc, scoreboardUrl) {
    var sec = el('section', { class: 'grader-uc', id: 'grader-' + uc.id });
    sec.appendChild(el('h3', {}, uc.id + ' ' + uc.title));
    var bodies = [el('p', {}, uc.try), sampleBlock(uc, scoreboardUrl), el('p', {}, uc.expect)];
    var shown = 0;
    var next = el('button', { type: 'button', class: 'chip grader-next' });
    function reveal() {
      var level = el('div', { class: 'grader-level' });
      level.appendChild(el('h4', {}, LEVELS[shown]));
      level.appendChild(bodies[shown]);
      sec.insertBefore(level, next);
      shown += 1;
      if (shown >= LEVELS.length) next.remove();
      else next.textContent = 'Show hint ' + LEVELS[shown];
    }
    next.textContent = 'Show hint ' + LEVELS[0];
    next.addEventListener('click', reveal);
    sec.appendChild(next);
    return sec;
  }

  function build(data) {
    panel = el('dialog', { id: 'grader-panel', class: 'grader-panel', 'aria-labelledby': 'grader-title' });
    var head = el('div', { class: 'grader-head' });
    head.appendChild(el('h2', { id: 'grader-title' }, 'Grader hints'));
    var close = el('button', { type: 'button', class: 'chip', id: 'grader-close' }, 'Close');
    close.addEventListener('click', function () { panel.close(); });
    head.appendChild(close);
    panel.appendChild(head);
    panel.appendChild(el('p', { class: 'muted' }, 'Each use case has three hints. Reveal them one at a time.'));
    data.use_cases.forEach(function (uc) { panel.appendChild(useCase(uc, data.scoreboard_url)); });
    document.body.appendChild(panel);
  }

  function open() {
    if (panel) { panel.showModal(); return; }
    fetch('/grader/hints.json').then(function (r) { return r.json(); }).then(function (data) {
      build(data);
      panel.showModal();
    }, function () { alert('Grader hints could not be loaded from the field app.'); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('grader-help');
    if (btn) btn.addEventListener('click', open);
  });
})();
