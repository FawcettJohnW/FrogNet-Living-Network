// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/ics.js -- [RT_LAYERS_V1] [RT_ICS_BOARDS_V1] the EOC's boards with the fields WebEOC's standard boards carry
// (from the county and state WebEOC guides: Palm Beach, Broward, Miami-Dade, Tampa, San Diego, New Hampshire, Washington
// EMD, Texas STAR, and Juvare's own): the incident, the activity log and significant events, resource requests
// (ICS-213RR), situation reports, shelters, road closures, damage assessment, position logs (ICS-214), press releases,
// sign-in/out. Each board is a definition below -- its fields, its list columns, its statuses -- drawn by one form and
// list engine. A record is never deleted; every update (status, comment, approval, deployment) is kept as its history.
// Live: everyone sees a record the moment it is filed, with no server and no Internet. Not yet: attachments.
'use strict'
RT.layer('ics', (RT) => {
  S.ics = {}; S.icsAct = {}
  RT.kind('closure', { label: 'Road closures', shape: 'sq', size: 16, color: '#b71c1c', ch: '⛔' })
  RT.kind('damage',  { label: 'Damage reports', shape: 'dia', size: 16, color: '#5d4037', ch: 'D' })
  RT.kind('event',   { label: 'Logged events', shape: '', size: 14, color: '#283593', ch: 'i' })

  // FEMA's community lifelines; WebEOC's priority sets (time-based, Palm Beach / New Hampshire; and the life-safety one)
  const LIFELINES = ['Safety and Security', 'Food, Hydration, Shelter', 'Health and Medical', 'Energy (Power and Fuel)', 'Communications', 'Transportation', 'Hazardous Materials', 'Water Systems']
  const RR_PRIORITY = ['Immediate (4 hours)', 'Priority (12 hours)', 'Routine (24 hours)', 'Long-Term (96 hours)', 'Extended (over 96 hours)']
  const RR_HOURS = { 'Immediate (4 hours)': 4, 'Priority (12 hours)': 12, 'Routine (24 hours)': 24, 'Long-Term (96 hours)': 96, 'Extended (over 96 hours)': 168 }
  const SECTIONS = ['Operations', 'Planning', 'Logistics', 'Finance/Admin', 'Command', 'Public Information', 'Safety', 'Liaison']
  const EV_PRIORITY = ['High: life safety / mission critical', 'Medium: injury / property / environment', 'Low: information']
  const B = {
    incident: { title: 'Incident', single: true, overviewWrites: true, fields: [
      ['name', 'Incident name', 'text'], ['number', 'Incident number', 'text'],
      ['type', 'Incident type', ['Activated', 'Exercise', 'Planned event', 'Training event', 'Not activated']],
      ['level', 'Activation level', ['IV: Routine readiness', 'III: Increased readiness', 'II: Limited activation', 'I: Full activation']],
      ['opFrom', 'Operational period from', 'dt'], ['opTo', 'Operational period to', 'dt'],
      ['agency', 'Agency', 'text'], ['location', 'Agency location', 'text'], ['poc', 'Point of contact', 'text'], ['phone', 'Contact number', 'text'],
      ['proclamation', 'Emergency proclamation', ['None', 'Local proclamation', 'State declaration', 'Federal declaration']],
      ['summary', 'Summary', 'area']] },
    events: { title: 'Activity log', kind: 'event', status: ['Not reviewed', 'Reviewed, not posted', 'Posted to significant events'], fields: [
      ['subject', 'Event / subject', 'text', true], ['type', 'Event type', ['Log entry', 'Fire', 'Flood', 'Medical', 'Law enforcement', 'Hazmat', 'Infrastructure', 'Utilities', 'Weather', 'Shelter', 'Transportation', 'Other']],
      ['priority', 'Priority', EV_PRIORITY], ['source', 'Information source', 'text'],
      ['infoStatus', 'Information status', ['Fact', 'Rumor', 'Unknown', 'Internal use only']],
      ['description', 'Description', 'area', true], ['where', 'Location name or address', 'text'], ['here', 'At my location', 'here'],
      ['significant', 'Post to significant events', 'check']],
      list: ['subject', 'priority', 'type', 'name'], updates: [['text', 'Update', 'area']] },
    rr: { title: 'Resource requests (ICS-213RR)', status: ['New request', 'Need more information', 'Assigned', 'In progress', 'In staging', 'Partially filled', 'On scene', 'On hold', 'Rejected', 'Complete', 'Demobilizing', 'Closed', 'Canceled'], fields: [
      ['title', 'Request / mission name', 'text', true], ['purpose', 'Purpose', 'text'], ['description', 'Detailed description', 'area', true],
      ['kind', 'Kind', ['Resource request', 'Task']],
      ['resource', 'Resource (name, or NIMS typed resource and type)', 'text'], ['quantity', 'Quantity', 'number'], ['unit', 'Unit of measure', ['Each', 'Personnel', 'Teams', 'Pallets', 'Cases', 'Gallons', 'Hours', 'Days', 'Other']],
      ['priority', 'Priority', RR_PRIORITY], ['due', 'Needed by (from the priority if blank)', 'dt'], ['howLong', 'For how long', ['Consumable', 'Returnable: demobilize when done']],
      ['lifeline', 'Community lifeline', LIFELINES], ['justification', 'Justification', 'area'],
      ['assigned', 'Assigned to', SECTIONS], ['deliverTo', 'Deliver to (address)', 'text'], ['here', 'Deliver to my location', 'here'],
      ['instructions', 'Delivery / coordinating instructions', 'area'], ['poc', 'On-scene point of contact', 'text'], ['pocPhone', 'Point of contact phone', 'text'],
      ['contact', 'Requestor contact (24-hour)', 'text']],
      list: ['title', 'priority', 'quantity', 'resource', 'assigned'],
      updates: [['approval', 'Approval', ['', 'Pending', 'Approved', 'Need more information', 'Disapproved']], ['assigned', 'Assign to', [''].concat(SECTIONS)],
        ['filled', 'Quantity filled', 'number'], ['eta', 'Estimated arrival', 'dt'], ['cost', 'Estimated cost ($)', 'number'], ['unitCost', 'Unit cost ($)', 'number'], ['po', 'Purchase order number', 'text'],
        ['deployment', 'Deployment (resource, quantity, status)', 'text'], ['text', 'Comment', 'area']] },
    sitrep: { title: 'Situation reports', overviewWrites: false, status: ['Draft', 'Complete', 'Approved'], fields: [
      ['opFrom', 'Operational period from', 'dt', true], ['opTo', 'Operational period to', 'dt'], ['current', 'Current situation', 'area', true],
      ['operations', 'Operations', 'area'], ['planning', 'Planning', 'area'], ['logistics', 'Logistics', 'area'], ['finance', 'Finance/Admin', 'area'],
      ['pio', 'Public information', 'area'], ['safety', 'Safety', 'area'], ['objectives', 'Objectives for the next period', 'area']],
      list: ['opFrom', 'opTo', 'name'], updates: [['text', 'Comment', 'area']] },
    shelters: { title: 'Shelters', kind: 'shelter', status: ['Open', 'Closed', 'Full', 'Standby'], fields: [
      ['shelter', 'Shelter name', 'text', true], ['type', 'Shelter type', ['General population', 'Special needs', 'Pet-friendly', 'Medical', 'Warming / cooling center', 'Other']],
      ['capacity', 'Capacity', 'number'], ['occupancy', 'Occupancy', 'number'], ['specialNeeds', 'Special needs occupants', 'number'], ['pets', 'Pets / animals', 'number'], ['staff', 'Staff', 'number'],
      ['power', 'Power', ['Grid', 'Generator', 'None']], ['meals', 'Meals served (this period)', 'number'],
      ['address', 'Address', 'text'], ['here', 'At my location', 'here'], ['poc', 'Point of contact', 'text'], ['phone', 'Phone', 'text'],
      ['capabilities', 'Capabilities / support needed', 'area'], ['comments', 'Comments', 'area']],
      list: ['shelter', 'occupancy', 'capacity', 'type'], updates: [['occupancy', 'Occupancy now', 'number'], ['meals', 'Meals served', 'number'], ['text', 'Comment', 'area']] },
    roads: { title: 'Road closures', kind: 'closure', status: ['Full closure', 'Restriction', 'Lane closure', 'Open'], fields: [
      ['road', 'Road name', 'text', true], ['municipality', 'Municipality', 'text'], ['jurisdiction', 'Road jurisdiction', ['Local', 'State', 'Private', 'Federal']],
      ['relation', 'Cross street', ['At', 'Before', 'After', 'Between']], ['cross1', 'Cross street', 'text'], ['cross2', 'Second cross street (Between)', 'text'],
      ['reason', 'Closure reason', ['Flooding', 'Debris', 'Downed lines', 'Collapse / washout', 'Accident', 'Fire', 'Hazmat', 'Law enforcement', 'Other']],
      ['severity', 'Severity', ['Impassable', 'Passable with caution', 'Emergency vehicles only']],
      ['closedAt', 'Date/time closed', 'dt'], ['openAt', 'Estimated date/time open', 'dt'], ['detour', 'Alternate route', 'text'], ['here', 'At my location', 'here'],
      ['details', 'Additional details', 'area'], ['contact', 'Contact information', 'text']],
      list: ['road', 'relation', 'cross1', 'reason'], updates: [['openAt', 'Estimated open', 'dt'], ['text', 'Comment', 'area']] },
    damage: { title: 'Damage assessment', kind: 'damage', fields: [
      ['address', 'Address / parcel', 'text', true], ['here', 'At my location', 'here'],
      ['category', 'Category', ['Individual assistance: residence', 'Individual assistance: business', 'Public assistance: roads and bridges', 'Public assistance: water control', 'Public assistance: buildings and equipment', 'Public assistance: utilities', 'Public assistance: parks and other', 'Public assistance: debris', 'Public assistance: emergency protective measures']],
      ['level', 'Damage level', ['Destroyed', 'Major', 'Minor', 'Affected', 'Inaccessible']],
      ['occupancy', 'Occupancy', ['Owner', 'Renter', 'Seasonal', 'Vacant', 'Public']], ['displaced', 'People displaced', 'number'],
      ['loss', 'Estimated loss ($)', 'number'], ['insured', 'Insured (%)', 'number'], ['water', 'Water depth (inches)', 'number'], ['notes', 'Notes', 'area']],
      list: ['address', 'level', 'category', 'loss'], updates: [['text', 'Comment', 'area']] },
    ics214: { title: 'Position log (ICS-214)', fields: [
      ['details', 'Details', 'area', true], ['type', 'Entry type', ['Sign-on', 'Status', 'Shift change', 'Activity', 'Decision', 'Internal event', 'Sign-off']],
      ['priority', 'Priority', ['Low', 'Medium', 'High']], ['poc', 'Point of contact (name / number)', 'text'], ['significant', 'Post to significant events', 'check']],
      list: ['type', 'details', 'name'] },
    press: { title: 'Press releases', status: ['Pending approval', 'Published', 'Not approved'], fields: [
      ['title', 'Title', 'text', true], ['lead', 'Lead', 'text'], ['text', 'Release', 'area', true], ['url', 'Link (when posted)', 'text']],
      list: ['title', 'name'], updates: [['text', 'Comment', 'area']] },
    signin: { title: 'Sign in / out', status: ['Signed in', 'Signed out'], fields: [
      ['person', 'Name', 'text', true], ['position', 'EOC position', SECTIONS.concat(['EOC Director', 'ESF representative', 'Agency representative'])],
      ['agency', 'Agency', 'text'], ['phone', 'Phone', 'text'], ['email', 'Email', 'text'], ['shift', 'Shift', ['Alpha', 'Bravo']]],
      list: ['person', 'position', 'agency'] },
  }
  const ORDER = ['incident', 'events', 'rr', 'sitrep', 'shelters', 'roads', 'damage', 'ics214', 'press', 'signin']
  RT.icsBoards = B

  RT.on('ics', (m) => { S.ics = m.rec || {}; S.icsAct = m.act || {}; redraw(); refreshSheet('ics') })
  const acts = (b, id) => Object.entries(S.icsAct[b] || {}).filter(([k]) => k.startsWith(id + '~')).map(([, a]) => a).sort((x, y) => (x.at || 0) - (y.at || 0))
  /** a record as it stands now: as filed, with every update's fields over it in order */
  const now = (b, id) => { const r = Object.assign({}, (S.ics[b] || {})[id] || {}); for (const a of acts(b, id)) for (const k in a) if (!['by', 'name', 'position', 'at', 'text'].includes(k) && a[k] !== '' && a[k] != null) r[k] = a[k]; return r }
  const when = (t) => t ? new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''
  const val = (f, v) => f[2] === 'check' ? (v ? 'yes' : '') : f[2] === 'dt' && v ? new Date(v).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : v
  const field = (f, v) => {
    const [k, label, t, req] = f, r = req ? ' required' : '', e = esc(v ?? '')
    if (Array.isArray(t)) return `<label>${esc(label)}<select name="${k}">${t.map(o => `<option${o === v ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select></label>`
    if (t === 'area') return `<label>${esc(label)}<textarea name="${k}" rows="3"${r}>${e}</textarea></label>`
    if (t === 'check') return `<label class="chk"><input type="checkbox" name="${k}"${v ? ' checked' : ''}> ${esc(label)}</label>`
    if (t === 'here') return `<label class="chk"><input type="checkbox" name="${k}"> ${esc(label)}</label>`
    return `<label>${esc(label)}<input name="${k}" type="${t === 'number' ? 'number' : t === 'dt' ? 'datetime-local' : 'text'}" value="${e}"${r}></label>`
  }
  const read = (form, fields) => {
    const bag = {}
    for (const f of fields) {
      const el = form[f[0]]; if (!el) continue
      if (f[2] === 'here') { if (el.checked) { const h = here(); bag.lat = h.lat; bag.lng = h.lng } continue }
      const v = f[2] === 'check' ? el.checked : f[2] === 'number' ? (el.value === '' ? '' : +el.value) : el.value.trim()
      if (v !== '' && v !== false) bag[f[0]] = v
    }
    return bag
  }
  const canWrite = (d) => !d.overviewWrites || RT.me().overview

  RT.screen('ics', () => {
    sheet('ics', 'Boards', ORDER.map(b => { const n = Object.keys(S.ics[b] || {}).length
      return `<button type="button" class="board" onclick="RT.open('ics-list','${b}')"><b>${esc(B[b].title)}</b> <span class="meta">${B[b].single ? (n ? 'set' : 'not set') : n + ' record' + (n === 1 ? '' : 's')}</span></button>` }).join('') +
      '<div class="meta">Every record is live for everyone on the network the moment it is filed. Records are never deleted; updates keep their history.</div>')
  })
  RT.screen('ics-list', (b) => {
    const d = B[b]; S.icsBoard = b
    if (d.single) { RT.open('ics-rec', b, b); return }
    const ids = Object.keys(S.ics[b] || {}).sort((x, y) => ((S.ics[b][y] || {}).at || 0) - ((S.ics[b][x] || {}).at || 0))
    const filt = S.icsFilter && S.icsFilter[b]
    const rows = ids.map(id => [id, now(b, id)]).filter(([, r]) => !filt || r.status === filt)
    const cols = d.list || []
    sheet('ics-list', d.title, `<button type="button" onclick="RT.open('ics')">◂ Boards</button> <button type="button" class="go" onclick="RT.open('ics-new','${b}')">New</button>
      ${d.status ? `<select class="icsf"><option value="">All</option>${d.status.map(s => `<option${s === filt ? ' selected' : ''}>${esc(s)}</option>`).join('')}</select>` : ''}
      <table class="ics"><tr>${cols.map(c => `<th>${esc((d.fields.find(f => f[0] === c) || [, c])[1])}</th>`).join('')}${d.status ? '<th>Status</th>' : ''}<th>Filed</th></tr>
      ${rows.map(([id, r]) => `<tr onclick="RT.open('ics-rec','${b}','${esc(id)}')">${cols.map(c => `<td>${esc(val(d.fields.find(f => f[0] === c) || [c, c, 'text'], r[c]) ?? '')}</td>`).join('')}${d.status ? `<td>${esc(r.status || d.status[0])}</td>` : ''}<td>${esc(when(r.at))}</td></tr>`).join('') || `<tr><td colspan="${cols.length + 2}" class="meta">none</td></tr>`}</table>`)
    const sel = document.querySelector('#sheet .icsf')
    if (sel) sel.onchange = () => { S.icsFilter = Object.assign({}, S.icsFilter, { [b]: sel.value }); RT.open('ics-list', b) }
  })
  RT.screen('ics-new', (b, prefill) => {
    const d = B[b], r = prefill || {}
    if (!canWrite(d)) { status('only the EOC sets the ' + d.title.toLowerCase()); return }
    sheet('ics-new', 'New: ' + d.title, `<button type="button" onclick="RT.open('ics-list','${b}')">◂ ${esc(d.title)}</button>
      <form class="icsform">${d.fields.map(f => field(Array.isArray(f[2]) && r[f[0]] === undefined ? [f[0], f[1], [''].concat(f[2]), f[3]] : f, r[f[0]])).join('')}${d.status ? field(['status', 'Status', d.status], r.status) : ''}
      <div class="meta">Filed as ${esc(S.cfg.name)} (${esc(RT.me().label || S.role)}${S.team ? ' ' + esc(S.team) : ''}), ${esc(new Date().toLocaleString())}.</div><button class="go">File it</button></form>`)
    document.querySelector('#sheet .icsform').onsubmit = (e) => {
      e.preventDefault(); const bag = read(e.target, d.fields.concat(d.status ? [['status', 'Status', d.status]] : []))
      if (b === 'rr' && !bag.due && bag.priority) {          // the priority's hours from now, in local time (as the form's own field)
        const t = new Date(Date.now() + RR_HOURS[bag.priority] * 3600000), z = (n) => String(n).padStart(2, '0')
        bag.due = t.getFullYear() + '-' + z(t.getMonth() + 1) + '-' + z(t.getDate()) + 'T' + z(t.getHours()) + ':' + z(t.getMinutes())
      }
      const id = d.single ? b : S.cfg.dev.replace(/[^A-Za-z0-9]/g, '').slice(-8) + '-' + Date.now()
      send({ op: 'icsWrite', board: b, id, bag }); RT.open('ics-list', b)
    }
  })
  RT.screen('ics-rec', (b, id) => {
    const d = B[b], r = now(b, id), first = (S.ics[b] || {})[id]
    if (!first) { if (d.single) { RT.open('ics-new', b); return } RT.open('ics-list', b); return }
    const hist = acts(b, id)
    sheet('ics-rec', d.title + (d.single ? '' : ': ' + (r[d.fields[0][0]] || id)), `<button type="button" onclick="RT.open('${d.single ? 'ics' : "ics-list','" + b}')">◂ ${d.single ? 'Boards' : esc(d.title)}</button>
      ${d.single && canWrite(d) ? ` <button type="button" onclick="RT.open('ics-new','${b}',RT.icsNow('${b}','${b}'))">Edit</button>` : ''}
      ${typeof r.lat === 'number' ? ` <button type="button" onclick="showOnMap(${r.lat},${r.lng})">Map</button>` : ''}
      <table class="ics rec">${d.fields.filter(f => f[2] !== 'here' && r[f[0]] !== undefined && r[f[0]] !== '').map(f => `<tr><th>${esc(f[1])}</th><td>${esc(val(f, r[f[0]]))}</td></tr>`).join('')}
      ${d.status ? `<tr><th>Status</th><td>${esc(r.status || d.status[0])}</td></tr>` : ''}
      ${b === 'rr' ? ['approval', 'filled', 'eta', 'cost', 'unitCost', 'po'].filter(k => r[k] !== undefined && r[k] !== '').map(k => `<tr><th>${esc(d.updates.find(u => u[0] === k)[1])}</th><td>${esc(k === 'eta' ? val([k, k, 'dt'], r[k]) : r[k])}</td></tr>`).join('') : ''}
      <tr><th>Filed by</th><td>${esc(first.name)} (${esc(first.position || '')}), ${esc(when(first.at))}</td></tr></table>
      <h3>History</h3>${hist.map(a => `<div class="meta">${esc(when(a.at))} · ${esc(a.name)} (${esc(a.position || '')}): ${esc(Object.entries(a).filter(([k, v]) => !['by', 'name', 'position', 'at'].includes(k) && v !== '').map(([k, v]) => (k === 'text' ? '' : k + ' ') + v).join(' · '))}</div>`).join('') || '<div class="meta">no updates yet</div>'}
      ${d.single ? '' : `<h3>Update</h3><form class="icsupd">${d.status ? field(['status', 'Status', [''].concat(d.status)], '') : ''}${(d.updates || [['text', 'Comment', 'area']]).map(f => field(f, '')).join('')}<button class="go">Add update</button></form>`}`)
    const u = document.querySelector('#sheet .icsupd')
    if (u) u.onsubmit = (e) => {
      e.preventDefault(); const bag = read(e.target, (d.status ? [['status', 'Status', d.status]] : []).concat(d.updates || [['text', 'Comment', 'area']]))
      if (!Object.keys(bag).length) return
      send({ op: 'icsAct', board: b, id, bag })
    }
  })
  RT.icsNow = (b, id) => now(b, id)
  RT.hook('refresh', (what) => {
    if (what !== 'ics') return
    if (S.sheet === 'ics') RT.open('ics')
    else if (S.sheet === 'ics-list') RT.open('ics-list', S.icsBoard)
    else if (S.sheet === 'ics-rec') { const t = document.querySelector('#sheet .icsupd textarea'); if (!t || !t.value) RT.open('ics-rec', S.icsRec[0], S.icsRec[1]) }
  })
  const _rec = RT.screens['ics-rec']; RT.screens['ics-rec'] = (b, id) => { S.icsRec = [b, id]; _rec(b, id) }

  // on the map: closures, damage, shelters filed here, and logged events with a location
  RT.hook('redraw', (want, place) => {
    for (const b of ['roads', 'damage', 'shelters', 'events']) {
      const d = B[b]
      for (const id in (S.ics[b] || {})) {
        const r = now(b, id)
        if (typeof r.lat !== 'number' || !S.on[d.kind] || (b === 'roads' && r.status === 'Open')) continue
        const k = 'ics:' + b + ':' + id; want[k] = 1
        place(k, { kind: d.kind, name: r[d.fields[0][0]] || d.title, lat: r.lat, lng: r.lng,
                   status: [r.status, (d.list || []).slice(1).map(c => r[c]).filter(Boolean).join(' · ')].filter(Boolean).join(' · ') }, null)
      }
    }
  })
  RT.button('icsBtn', 'Boards', () => RT.open('ics'), 'the EOC boards: incident, activity log, resource requests, sitreps, shelters, roads, damage, logs', '#layersBtn')
  RT.hook('connected', () => { $('#icsBtn').hidden = !!RT.me().only })
  // the incident's name and level on the status line
  RT.on('ics', () => { const i = now('incident', 'incident'); if (i.name) $('#mission').textContent = i.name + (i.level ? ' · ' + i.level.split(':')[0] : '') })
})
