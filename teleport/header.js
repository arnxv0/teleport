/* Shared header status for every Teleport tab: run pill, mode chip, live clock.
   One implementation so Overview, Swarm 3D, Teleports and Connect always read the same. */
(function () {
  var CHECK = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" style="margin-right:2px"><path d="M20 6 9 17l-5-5"/></svg>';
  var STARTING = ['planning', 'ingesting', 'launching'];
  var lastOk = 0, S = null;
  function $(id) { return document.getElementById(id); }

  function render() {
    if (!S) return;
    var rp = $('hdrPill'), ph = (S.run && S.run.phase) || 'idle';
    var total = +S.total || 0, active = (+S.todo || 0) + (+S.claimed || 0);
    if (STARTING.indexOf(ph) >= 0) { rp.className = 'pill run'; rp.innerHTML = '<span class="dot pulse"></span>Starting'; }
    else if (ph === 'failed') { rp.className = 'pill'; rp.innerHTML = '<span class="dot"></span>Failed'; }
    else if (!total) { rp.className = 'pill'; rp.innerHTML = '<span class="dot"></span>Ready'; }
    else if (active > 0) { rp.className = 'pill run'; rp.innerHTML = '<span class="dot pulse"></span>Running'; }
    else { rp.className = 'pill done'; rp.innerHTML = CHECK + 'Complete'; }
    var tail = S.country || (S.run && S.run.id) || '';
    $('hdrMode').textContent = (S.mode === 'archil' ? 'Archil' : 'Local') + (tail ? ' · ' + tail : '');
  }

  function tick() {
    var c = $('hdrClock');
    if (!c) return;
    if (!lastOk) { c.textContent = ''; return; }
    var s = Math.round((Date.now() - lastOk) / 1000);
    c.textContent = new Date().toLocaleTimeString([], { hour12: false }) + ' · ' + (s <= 2 ? 'live' : s + 's ago');
    c.style.color = s > 6 ? 'var(--warning)' : '';
  }

  function poll() {
    fetch('/api/state').then(function (r) { return r.json(); }).then(function (st) {
      S = st; lastOk = Date.now(); render(); tick();
    }).catch(function () {}).then(function () { setTimeout(poll, 1500); });
  }

  function start() {
    try { var c = sessionStorage.getItem('tp_state'); if (c) { S = JSON.parse(c); render(); } } catch (e) {}
    poll();
    setInterval(tick, 1000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
