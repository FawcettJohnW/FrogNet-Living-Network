// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/medical.js -- [RT_LAYERS_V1] [RT_FACILITY_V1] the hospital's desk (beds by type, diversion, power, water, oxygen,
// staff, needs) and [RT_TRANSPORT_V1] the ambulance (status, a patient transport to a hospital, its assignments): the
// Hospital and Ambulance views; each hospital's status in its map popup; for a view with the overview (the EOC), the
// transports under way and the hospitals not open to ambulances on Requests.
'use strict'
RT.layer('medical', (RT) => {
  S.fac = {}; S.tr = {}; S.trActs = {}
  RT.role('hospital', { label: 'Hospital', field: 'unit', unitLabel: 'Hospital', unitHint: 'Harborview',
                        units: ['Harborview', 'Swedish First Hill', 'Virginia Mason', 'UW Medical Center', 'UW Northwest', 'Swedish Ballard', "Seattle Children's",
                                'Highline (Burien)', 'Valley Medical (Renton)', 'Overlake (Bellevue)', 'EvergreenHealth (Kirkland)', 'Vashon Health Center'], who: () => S.team, kinds: ['hospital', 'ambulance', 'hazard'],
                        act: ['Status', 'facility'], req: ['Inbound', 'inbound'], connected() { RT.open('facility') } })
  RT.role('ambulance', { label: 'Ambulance', field: 'unit', unitLabel: 'Unit', unitHint: 'Medic 12', units: ['Medic 1', 'Medic 12', 'Medic 21', 'Aid 31'], who: () => S.team, kinds: ['hospital', 'ambulance', 'help', 'hazard', 'service'],
                         act: ['Unit', 'unit'], req: ['Hospitals', 'hospitals'] })
  const BEDS = [['ed', 'Emergency'], ['icu', 'ICU'], ['medsurg', 'Med / surg'], ['peds', 'Pediatric'], ['burn', 'Burn'], ['or', 'Operating rooms']]
  const DIVERSION = ['Open', 'ED diversion', 'Trauma diversion', 'Closed to ambulances']
  const TRIAGE = [['Red', '#d32f2f', 'immediate'], ['Yellow', '#f9a825', 'delayed'], ['Green', '#2e9e5b', 'minor'], ['Black', '#333', 'expectant']]
  const UNIT_STATUS = ['Available', 'Dispatched', 'En route to scene', 'On scene', 'Transporting', 'At hospital', 'Out of service']
  const facId = (n) => String(n || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  const hospitals = () => {
    const names = new Set(Object.values(S.pos).filter(b => b && b.kind === 'hospital').map(b => b.name))
    for (const f of Object.values(S.fac)) names.add(f.name)
    return [...names].sort()
  }
  const facOf = (name) => S.fac[facId(name)]
  const bedsTotal = (f) => f ? BEDS.reduce((a, [k]) => a + (+((f.beds || {})[k]) || 0), 0) : null
  const trActs = (id) => Object.entries(S.trActs).filter(([k]) => k.startsWith(id + '~')).map(([, a]) => a).sort((a, b) => (b.at || 0) - (a.at || 0))
  const trState = (id) => { const a = trActs(id)[0]; return a ? a.action : 'en route' }
  const triPill = (t) => { const x = TRIAGE.find(z => z[0] === t); return x ? `<span class="tri" style="background:${x[1]}"></span>${esc(t)}` : esc(t || '') }

  RT.on('facilities', (m) => { S.fac = m.list || {}; redraw(); refreshSheet('fac') })
  RT.on('transports', (m) => { S.tr = m.items || {}; S.trActs = m.acts || {}; refreshSheet('tr'); trNotice() })

  RT.screen('facility', () => {
    const name = S.team, f = facOf(name) || {}, b = f.beds || {}
    sheet('fac', name + ' — status', `<form class="fac">
      <div class="meta">${f.at ? 'last published ' + new Date(f.at * 1000).toLocaleTimeString() + ' by ' + esc(f.by) : 'not published yet'}</div>
      <h3>Beds available now</h3><div class="beds">${BEDS.map(([k, l]) => `<label>${l}<input name="b_${k}" type="number" min="0" value="${b[k] ?? ''}"></label>`).join('')}</div>
      <label>Ambulance traffic<select name="diversion">${DIVERSION.map(d => `<option${f.diversion === d ? ' selected' : ''}>${d}</option>`).join('')}</select></label>
      <h3>Resources</h3><div class="beds">
        <label>Power<select name="power">${['Grid', 'Generator', 'Generator — fuel low', 'No power'].map(x => `<option${f.power === x ? ' selected' : ''}>${x}</option>`).join('')}</select></label>
        <label>Water<select name="water">${['Normal', 'Boil notice', 'Limited', 'None'].map(x => `<option${f.water === x ? ' selected' : ''}>${x}</option>`).join('')}</select></label>
        <label>Oxygen<select name="oxygen">${['Normal', 'Limited', 'Critical'].map(x => `<option${f.oxygen === x ? ' selected' : ''}>${x}</option>`).join('')}</select></label>
        <label>Staffing<select name="staff">${['Normal', 'Short', 'Critical'].map(x => `<option${f.staff === x ? ' selected' : ''}>${x}</option>`).join('')}</select></label></div>
      <label>Needs (what would help most)<textarea name="needs" rows="2">${esc(f.needs || '')}</textarea></label>
      <button class="go">Publish ${esc(name)}'s status</button></form>`)
    document.querySelector('.fac').onsubmit = (e) => {
      e.preventDefault(); const x = e.target, beds = {}
      for (const [k] of BEDS) if (x['b_' + k].value !== '') beds[k] = +x['b_' + k].value
      send({ op: 'facility', id: facId(name), bag: { name, beds, diversion: x.diversion.value, power: x.power.value, water: x.water.value, oxygen: x.oxygen.value, staff: x.staff.value, needs: x.needs.value.trim() } })
    }
  })
  RT.screen('inbound', () => {
    const hosp = S.role === 'hospital'
    const mine = hosp ? (t) => facId(t.to) === facId(S.team) : () => true
    const ids = Object.keys(S.tr).filter(id => mine(S.tr[id]) && trState(id) !== 'arrived').sort((a, b) => (S.tr[a].at + (S.tr[a].eta || 0) * 60) - (S.tr[b].at + (S.tr[b].eta || 0) * 60))
    const card = (id) => { const t = S.tr[id], acts = trActs(id), due = t.at + (t.eta || 0) * 60
      return `<div class="card"><b>${esc(t.unit)}</b> → ${esc(t.to)} · ${t.patients || 1} patient(s) · ${triPill(t.triage)}
        <div>${esc(t.complaint || '')}${t.age ? ' · ' + esc(t.age) : ''}</div>
        <div class="meta">ETA ${new Date(due * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · ${esc(trState(id))}</div>
        ${acts.map(a => `<div class="meta">↳ ${esc(a.action)} by ${esc(a.unit)}</div>`).join('')}
        ${hosp ? `<button type="button" onclick="tact('${esc(id)}','acknowledged')">Acknowledge</button> <button type="button" onclick="tact('${esc(id)}','arrived')">Arrived</button>` : ''}</div>` }
    sheet('tr', hosp ? 'Inbound to ' + S.team : 'Patient transports', ids.map(card).join('') || '<div class="meta">nothing inbound</div>')
  })
  window.tact = (id, action) => send({ op: 'transportAction', id, action })
  let trSeen = null
  function trNotice() {
    if (S.role !== 'hospital') return
    const ids = Object.keys(S.tr).filter(id => facId(S.tr[id].to) === facId(S.team))
    if (trSeen) for (const id of ids) if (!trSeen.has(id)) { const t = S.tr[id]
      notify({ id: 'tr:' + id, from: t.by, name: t.unit, to: 'all', text: `inbound: ${t.patients || 1} patient(s), ${t.triage || ''}, ETA ${t.eta || '?'} min — ${t.complaint || ''}`, at: t.at }) }
    trSeen = new Set(ids)
  }
  RT.screen('unit', () => {
    const hs = hospitals().map(n => { const f = facOf(n), tot = bedsTotal(f)
      return `<option value="${esc(n)}">${esc(n)}${f ? ' — ' + (f.diversion && f.diversion !== 'Open' ? f.diversion.toUpperCase() : tot + ' beds') : ''}</option>` }).join('')
    const jobs = Object.keys(S.field || {}).filter(fid => { const b = S.field[fid]; return b.what === 'service' && /ambulance|medical evac/i.test(b.type) && RT.fieldState(fid) !== 'done' })
    const meds = Object.entries(S.help || {}).filter(([, b]) => b.status === 'open' && (b.needs || []).includes('medical')).slice(0, 12)
    sheet('unit', S.team + ' — unit', `
      <h3>Status</h3><div class="row">${UNIT_STATUS.map(x => `<button type="button"${(S.myStatus || 'Available') === x ? ' class="on"' : ''} onclick="setStatus('${x}')">${x}</button>`).join(' ')}</div>
      <div class="meta">Now: ${esc(S.myStatus || 'Available')}</div>
      <h3>Transport a patient</h3><form class="tf">
        <label>To<select name="to">${hs}</select></label>
        <div class="beds"><label>Patients<input name="patients" type="number" min="1" value="1"></label>
          <label>Triage<select name="triage">${TRIAGE.map(t => `<option>${t[0]}</option>`).join('')}</select></label>
          <label>ETA (min)<input name="eta" type="number" min="1" value="12"></label></div>
        <label>Chief complaint<input name="complaint" placeholder="crush injury, left leg"></label>
        <label>Age / sex<input name="age" placeholder="50s M"></label>
        <button class="go">Send to the hospital</button></form>
      <h3>Assignments</h3>${jobs.map(RT.fieldCard).join('') || '<div class="meta">no ambulance requests open</div>'}
      ${meds.map(([rid, b]) => `<div class="card"><b>${esc(b.name)}</b> needs medical help · ${esc(RT.where(b.location))}
        <div><button type="button" onclick="act('${esc(rid)}','${esc(S.team)} responding')">Take it</button>
        ${b.location && typeof b.location.lat === 'number' ? `<button type="button" onclick="showOnMap(${b.location.lat},${b.location.lon})">Map</button>` : ''}</div></div>`).join('')}`)
    document.querySelector('.tf').onsubmit = (e) => {
      e.preventDefault(); const x = e.target
      send({ op: 'transport', bag: { to: x.to.value, patients: +x.patients.value || 1, triage: x.triage.value, eta: +x.eta.value || 10, complaint: x.complaint.value.trim(), age: x.age.value.trim() } })
      window.setStatus('Transporting')
    }
  })
  RT.screen('hospitals', () => {
    sheet('fac', 'Hospitals', hospitals().map(n => { const f = facOf(n)
      if (!f) return `<div class="card"><b>${esc(n)}</b><div class="meta">no status published</div></div>`
      return `<div class="card"><b>${esc(n)}</b> ${f.diversion && f.diversion !== 'Open' ? `<span class="divert">${esc(f.diversion)}</span>` : ''}
        <div class="meta">${BEDS.filter(([k]) => (f.beds || {})[k] != null).map(([k, l]) => l + ' ' + f.beds[k]).join(' · ')}</div>
        <div class="meta">power ${esc(f.power || '?')} · water ${esc(f.water || '?')} · oxygen ${esc(f.oxygen || '?')} · staff ${esc(f.staff || '?')}</div>
        ${f.needs ? `<div>Needs: ${esc(f.needs)}</div>` : ''}</div>` }).join(''))
  })
  RT.hook('refresh', (what) => {
    if (what === 'tr' && S.sheet === 'tr') RT.open('inbound')
    if (what === 'fac' && S.sheet === 'fac' && S.role !== 'hospital') RT.open('hospitals')
    if (S.sheet === 'reqs' && (what === 'tr' || what === 'fac')) RT.open('requests')
    if (what === 'status' && S.sheet === 'unit') RT.open('unit')     // an ambulance's status buttons redraw its own panel
  })
  // the hospital's own status in its map popup
  RT.hook('popup', (id, b) => {
    if (b.kind !== 'hospital') return ''
    const f = facOf(b.name); if (!f) return ''
    return `<br>${f.diversion && f.diversion !== 'Open' ? `<b class="divert">${esc(f.diversion)}</b><br>` : ''}${BEDS.filter(([k]) => (f.beds || {})[k] != null).map(([k, l]) => l + ' ' + f.beds[k]).join(' · ')}`
  })
  // the overview's Requests (the EOC): patient transports under way and the hospitals not open to ambulances
  RT.section('requests', () => {
    if (!RT.me().overview) return ''
    const ids = Object.keys(S.tr).filter(id => trState(id) !== 'arrived')
    const div = hospitals().map(n => facOf(n)).filter(f => f && f.diversion && f.diversion !== 'Open')
    return `<h3>Patient transports under way (${ids.length})</h3>` + (ids.map(id => { const t = S.tr[id]
        return `<div class="card"><b>${esc(t.unit)}</b> → ${esc(t.to)} · ${t.patients || 1} · ${triPill(t.triage)} · ${esc(t.complaint || '')}<div class="meta">ETA ${t.eta || '?'} min from ${new Date(t.at * 1000).toLocaleTimeString()} · ${esc(trState(id))}</div></div>` }).join('') || '<div class="meta">none</div>')
      + (div.length ? `<h3>Hospitals not open to ambulances</h3>` + div.map(f => `<div class="card"><b>${esc(f.name)}</b> <span class="divert">${esc(f.diversion)}</span>${f.needs ? `<div>Needs: ${esc(f.needs)}</div>` : ''}</div>`).join('') : '')
  })
})
