// dc.js -- [DC_APPS_V1] what every DisasterComm page shares: reading, watching and writing cells through ram.php,
// the device's own id, distances, ages, and the location a person gives.
const DC = (() => {
  async function call(url, opts) {
    const r = await fetch(url, opts);
    const j = await r.json().catch(() => ({ ok: false, why: 'answer was not JSON (HTTP ' + r.status + ')' }));
    if (!r.ok || j.ok === false) throw new Error(j.why ? j.why + (j.output ? ': ' + j.output : '') : 'HTTP ' + r.status);
    return j;
  }
  const q = (o) => Object.entries(o).map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&');
  async function read(s, v) { return (await call('ram.php?' + q({ op: 'read', s, v }))).rows || []; }
  async function write(s, v, i, bag) { return call('ram.php?op=write', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ s, v, i, bag }) }); }

  // watch(s, v, onChange): every cell of s.v now, then each newer cell as it is written (held reads, no polling).
  // onChange(cells) gets the whole picture: a Map instance -> {id, instance, bag}, the newest of each.
  function watch(s, v, onChange, onError) {
    const cells = new Map(); let after = 0; let stopped = false;
    const take = (rows) => { for (const r of rows) { after = Math.max(after, r.id); const o = cells.get(r.instance); if (!o || o.id < r.id) cells.set(r.instance, r); } };
    (async () => {
      take(await read(s, v)); onChange(cells);
      while (!stopped) {
        try {
          const rows = (await call('ram.php?' + q({ op: 'read', s, v, after, wait: 25 }))).rows || [];
          if (rows.length) { take(rows); onChange(cells); }
        } catch (e) { (onError || console.error)(e); await new Promise(r => setTimeout(r, 2000)); }   // the RAM is unreachable: try again on the next turn
      }
    })().catch(e => (onError || console.error)(e));
    return () => { stopped = true; };
  }

  function myId(kind) {   // this device's own name for what it publishes (kept in the browser; a new one if storage is gone)
    const key = 'dc.id.' + kind;
    try { let v = localStorage.getItem(key); if (!v) { v = kind + '-' + Math.random().toString(36).slice(2, 8); localStorage.setItem(key, v); } return v; }
    catch (e) { return kind + '-' + Math.random().toString(36).slice(2, 8); }
  }
  function remember(key, val) { try { if (val === undefined) return JSON.parse(localStorage.getItem('dc.' + key) || 'null'); localStorage.setItem('dc.' + key, JSON.stringify(val)); } catch (e) { return null; } }

  function km(a, b) {
    if (!a || !b || a.lat === '' || b.lat === '' || a.lat == null || b.lat == null || isNaN(+a.lat) || isNaN(+b.lat)) return null;
    const R = 6371, rad = (x) => x * Math.PI / 180, dl = rad(b.lat - a.lat), dn = rad(b.lon - a.lon);
    const h = Math.sin(dl / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dn / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  const dist = (k) => k == null ? '' : k < 1 ? Math.round(k * 1000) + ' m' : k.toFixed(1) + ' km';
  function age(at) { if (!at) return ''; const s = Math.max(0, Date.now() / 1000 - at); return s < 90 ? Math.round(s) + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago'; }
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const now = () => Math.round(Date.now() / 1000);

  // the location fields of a form: lat, lon, place text; "Use my location" fills lat/lon where the browser allows it
  // (browsers give a position only to https pages or localhost; otherwise the person types the place)
  function locationFrom(form) { return { lat: form.lat.value === '' ? '' : +form.lat.value, lon: form.lon.value === '' ? '' : +form.lon.value, text: form.place.value.trim() }; }
  function useMyLocation(form, say) {
    if (!navigator.geolocation || !window.isSecureContext) { say('This page cannot read the position (it needs https). Type the place, or lat/lon.'); return; }
    navigator.geolocation.getCurrentPosition(p => { form.lat.value = p.coords.latitude.toFixed(5); form.lon.value = p.coords.longitude.toFixed(5); say('Position filled in.'); },
                                             e => say('No position: ' + e.message));
  }
  const where = (l) => l ? [l.text, (l.lat !== '' && l.lat != null) ? (+l.lat).toFixed(4) + ', ' + (+l.lon).toFixed(4) : ''].filter(Boolean).join(' · ') : '';
  return { read, write, watch, myId, remember, km, dist, age, esc, now, locationFrom, useMyLocation, where };
})();
