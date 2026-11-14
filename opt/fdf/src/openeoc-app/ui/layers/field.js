// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/field.js -- [RT_LAYERS_V1] [RT_FIELD_V1] what teams do in the field (John, 2026-11-01: "Deploy Sensor Platform,
// Request Services, etc. Be creative, but realistic."): status, a sensor platform deployed, services requested, a hazard
// marked, an area searched -- on the map, on the Actions screen ('field'), and the open service requests on Requests;
// the EOC acknowledges, dispatches, closes them.
'use strict'
RT.layer('field', (RT) => {
  S.field = {}; S.fieldActs = {}
  RT.kind('platform', { label: 'Sensor platforms', shape: 'dia', size: 16, color: '#00897b', ch: 'S' })
  RT.kind('service',  { label: 'Service requests', shape: 'sq', size: 16, color: '#f9a825', ch: 'R', fg: '#222' })
  RT.kind('hazard',   { label: 'Hazards',    shape: 'dia', size: 18, color: '#d50000', ch: '⚠' })
  RT.kind('search',   { label: 'Searched areas', shape: '', size: 14, color: '#546e7a', ch: '✓' })
  const STATUSES = ['Available', 'En route', 'On scene', 'Need assistance', 'Out of service']
  const PLATFORMS = ['Air quality (PM2.5 / gas)', 'Weather station', 'Water level / flood gauge', 'Gas detector (CH4 / CO / H2S)', 'Radiation monitor', 'Seismic / structural', 'Camera (fixed)', 'Mesh relay node']
  const SERVICES = ['Medical evacuation', 'Ambulance', 'Water / food resupply', 'Fuel', 'Generator / power', 'Heavy equipment', 'Shelter space', 'Comms / radio relief', 'Relief crew', 'Hazmat team', 'Search dog team', 'Utility shut-off']
  const HAZARDS = ['Gas leak', 'Downed power line', 'Flooding', 'Structural collapse', 'Fire', 'Road blocked', 'Hazardous materials', 'Unstable slope']
  const fieldActs = (fid) => Object.entries(S.fieldActs).filter(([k]) => k.startsWith(fid + '~')).map(([, a]) => a).sort((a, b) => (b.at || 0) - (a.at || 0))
  const fieldState = RT.fieldState = (fid) => { const a = fieldActs(fid)[0]; return a ? a.action : '' }

  RT.on('field', (m) => { S.field = m.items || {}; S.fieldActs = m.acts || {}; redraw(); refreshSheet('field') })
  RT.hook('redraw', (want, place) => {
    for (const fid in S.field) {
      const b = S.field[fid], k = b.what
      if (!KINDS[k] || !S.on[k] || typeof b.lat !== 'number' || !visibleInView({ kind: k })) continue
      if (k === 'service' && fieldState(fid) === 'done') continue
      const id = 'field:' + fid; want[id] = 1
      place(id, { kind: k, name: (b.type || k) + (b.team ? ' · ' + b.team : ''), lat: b.lat, lng: b.lng,
                  status: [b.priority, fieldState(fid), b.note, 'by ' + (b.name || '')].filter(Boolean).join(' · ') }, null)
    }
  })

  RT.screen('field', () => {
    const mineIds = Object.keys(S.field).filter(f => S.field[f].by === S.cfg.dev).sort().reverse()
    sheet('field', 'Team actions', `
      <h3>My status</h3><div class="row">${STATUSES.map(x => `<button type="button" onclick="setStatus('${x}')">${x}</button>`).join(' ')}</div>
      <div class="meta">Now: ${esc(S.myStatus || 'Available')}</div>
      <h3>Deploy a sensor platform</h3><form class="ff" data-what="platform"><label>Platform<select name="type">${opts(PLATFORMS)}</select></label>
        <label>Note<input name="note" placeholder="mounted on the pole at the corner"></label><button class="go">Deploy here</button></form>
      <h3>Request services</h3><form class="ff" data-what="service"><label>Service<select name="type">${opts(SERVICES)}</select></label>
        <label>Priority<select name="priority"><option>Routine</option><option>Priority</option><option>Urgent</option></select></label>
        <label>Details<input name="note" placeholder="how many, how much, for whom"></label><button class="go">Send to the Command Center</button></form>
      <h3>Mark a hazard</h3><form class="ff" data-what="hazard"><label>Hazard<select name="type">${opts(HAZARDS)}</select></label>
        <label>Note<input name="note"></label><button class="go">Mark it here</button></form>
      <h3>Area searched</h3><form class="ff" data-what="search"><label>Result<select name="type"><option>Searched, no one found</option><option>Searched, people found</option><option>Partially searched</option><option>Unsafe to enter</option></select></label>
        <label>Radius (m)<input name="radius" type="number" value="100"></label><label>Note<input name="note"></label><button class="go">Mark searched here</button></form>
      <div class="meta">"Here" is your location; without one, the centre of the map.</div>
      <h3>Mine</h3>${mineIds.map(f => fieldCard(f)).join('') || '<div class="meta">none yet</div>'}`)
    document.querySelectorAll('#sheet .ff').forEach(f => f.onsubmit = (e) => {
      e.preventDefault(); const h = here()
      const bag = { what: f.dataset.what, type: f.type.value, note: f.note.value.trim(), lat: h.lat, lng: h.lng }
      if (f.priority) bag.priority = f.priority.value
      if (f.radius) bag.radius = +f.radius.value || 100
      if (bag.what === 'platform') bag.status = 'deployed'
      send({ op: 'field', bag }); f.note.value = ''
    })
  })
  // a field item's card; the EOC (a view that acks, RT.role's acks) acknowledges, dispatches, closes a service request
  const fieldCard = RT.fieldCard = (fid) => {
    const b = S.field[fid], acts = fieldActs(fid)
    return `<div class="card"><b>${esc(b.type)}</b> <span class="pill">${esc(b.what)}</span>${b.priority ? ` <span class="pill">${esc(b.priority)}</span>` : ''}
      <div class="meta">${esc(b.name)}${b.team ? ' · ' + esc(b.team) : ''} · ${b.at ? new Date(b.at * 1000).toLocaleTimeString() : ''}</div>
      ${b.note ? `<div>${esc(b.note)}</div>` : ''}${acts.map(a => `<div class="meta">↳ ${esc(a.action)} by ${esc(a.unit)}${a.note ? ' — ' + esc(a.note) : ''}</div>`).join('')}
      ${RT.me().acks && b.what === 'service' ? `<div><button type="button" onclick="fact('${esc(fid)}','acknowledged')">Acknowledge</button> <button type="button" onclick="fact('${esc(fid)}','dispatched')">Dispatched</button> <button type="button" onclick="fact('${esc(fid)}','done')">Done</button></div>` : ''}
      ${typeof b.lat === 'number' ? `<button type="button" onclick="showOnMap(${b.lat},${b.lng})">Map</button>` : ''}</div>`
  }
  window.fact = (id, action) => send({ op: 'fieldAction', id, action })
  // Requests: the teams' open service requests, most urgent first
  RT.section('requests', () => {
    const rank = (f) => ({ Urgent: 0, Priority: 1, Routine: 2 }[S.field[f].priority] ?? 3)
    const svc = Object.keys(S.field).filter(f => S.field[f].what === 'service' && fieldState(f) !== 'done').sort((a, b) => rank(a) - rank(b))
    return `<h3>Service requests from the teams (${svc.length})</h3>` + (svc.map(fieldCard).join('') || '<div class="meta">none open</div>')
  })
  RT.hook('refresh', (what) => {
    if (S.sheet === 'field' && (what === 'field' || what === 'status')) RT.open('field')
    if (S.sheet === 'reqs' && what === 'field') RT.open('requests')
  })
})
