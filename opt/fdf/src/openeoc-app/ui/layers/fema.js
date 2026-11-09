// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/fema.js -- [FEMA_EOC_V1] the FEMA EOC function on the EOC's boards (ics.js: one form and list engine, every
// record kept with its history, live for everyone): the community lifelines (the EOC's call on each, beside what the
// organizations on the network report), EOC staffing by the incident's NIMS structure, the incident objectives (ICS-202),
// and assignments (ICS-204) to the organizations that joined. The organizations are whoever is on the network, by the app
// they joined with (John 2026-11-09: "Categories come from the apps that connect"); a Team or unit sees its own
// assignments and reports progress on them.
'use strict'
RT.layer('fema', (RT) => {
  // the organizations, by the app they joined with (a device's presence: its kind and its team or unit)
  const CAT = { team: ['Teams', '👥', 'safety'], staff: ['Hospitals', '🏥', 'health'], ambulance: ['Ambulances', '🚑', 'health'],
                shelter: ['Shelters', '🏫', 'food'], power: ['Power', '⚡', 'energy'], water: ['Water', '💧', 'water'], phone: ['Communications', '📡', 'comms'] }
  const LL = [['safety', 'Safety and Security'], ['food', 'Food, Hydration, Shelter'], ['health', 'Health and Medical'], ['energy', 'Energy (Power and Fuel)'],
              ['comms', 'Communications'], ['transport', 'Transportation'], ['hazmat', 'Hazardous Materials'], ['water', 'Water Systems']]
  const LL_STATUS = ['Stable', 'Stabilizing', 'Unstable', 'Unknown']
  const COLOR = { Stable: '#2e9e5b', Stabilizing: '#f9a825', Unstable: '#d32f2f', Unknown: '#78909c' }
  // NIMS EOC structures: their sections and positions
  const STRUCT = {
    'ICS or ICS-like': { 'Command': ['EOC Director', 'Public Information Officer', 'Safety Officer', 'Liaison Officer'],
      'Operations': ['Operations Section Chief', 'Branch Director', 'Division/Group Supervisor'],
      'Planning': ['Planning Section Chief', 'Situation Unit Leader', 'Resources Unit Leader', 'Documentation Unit Leader', 'Demobilization Unit Leader'],
      'Logistics': ['Logistics Section Chief', 'Supply Unit Leader', 'Communications Unit Leader', 'Facilities Unit Leader'],
      'Finance/Admin': ['Finance/Admin Section Chief', 'Time Unit Leader', 'Cost Unit Leader', 'Procurement Unit Leader'] },
    'Incident Support Model': { 'EOC Director': ['EOC Director', 'Public Information Officer', 'Liaison Officer'],
      'Situational Awareness': ['Situational Awareness Section Chief', 'GIS Specialist', 'Intelligence Analyst'],
      'Planning Support': ['Planning Support Section Chief', 'Current Planning', 'Future Planning', 'Technical Specialist'],
      'Resources Support': ['Resources Support Section Chief', 'Resource Ordering', 'Resource Tracking', 'Donations Management'],
      'Center Support': ['Center Support Section Chief', 'IT/Communications', 'Facilities', 'Finance'] },
    'Departmental': { 'Emergency Management': ['Emergency Manager', 'Duty Officer'],
      'Departments': ['Fire', 'Law Enforcement', 'Public Works', 'Public Health', 'Transportation', 'Utilities', 'Human Services'] },
    'Emergency Support Functions': { 'EOC Management': ['EOC Director', 'ESF Coordinator'],
      'ESFs': ['ESF #1 Transportation', 'ESF #2 Communications', 'ESF #3 Public Works and Engineering', 'ESF #4 Firefighting', 'ESF #5 Information and Planning',
               'ESF #6 Mass Care, Emergency Assistance, Temporary Housing and Human Services', 'ESF #7 Logistics', 'ESF #8 Public Health and Medical Services',
               'ESF #9 Search and Rescue', 'ESF #10 Oil and Hazardous Materials Response', 'ESF #11 Agriculture and Natural Resources', 'ESF #12 Energy',
               'ESF #13 Public Safety and Security', 'ESF #14 Cross-Sector Business and Infrastructure', 'ESF #15 External Affairs'] },
  }
  const inc = () => { const r = RT.icsNow('incident', 'incident'); return Object.assign({}, r, { name: r.incName || '' }) }   // the incident's own name (ics.js: incName)
  const struct = () => STRUCT[inc().structure] || STRUCT['ICS or ICS-like']
  const positions = () => Object.values(struct()).flat()
  const sectionOf = (p) => (Object.entries(struct()).find(([, ps]) => ps.includes(p)) || [''])[0]
  const orgName = (w) => cap(w.unit || w.team || '')
  /** the organizations on the network now: {kind: {name: [devices]}} */
  function orgs() {
    const o = {}
    for (const [id, w0] of Object.entries(S.who || {})) {
      const w = Object.assign({}, S.pos[id] || {}, w0 || {}), n = orgName(w), k = w.kind
      if (!n || !k || k === 'eoc' || k === 'public') continue
      ((o[k] = o[k] || {})[n] = o[k][n] || []).push(Object.assign({ id }, w))
    }
    return o
  }
  const orgNames = () => { const s = new Set(); for (const by of Object.values(orgs())) for (const n of Object.keys(by)) s.add(n)
    for (const t of S.teamNames || []) s.add(cap(t)); for (const t of ((S.groupsByView || {}).team || [])) s.add(cap(t)); return [...s].sort() }
  const recs = (b) => Object.keys(S.ics[b] || {}).map(id => Object.assign({ id }, RT.icsNow(b, id))).sort((x, y) => (y.at || 0) - (x.at || 0))
  const opNow = () => +inc().opPeriod || 1

  const B = RT.icsBoards
  B.objectives = { title: 'Incident objectives (ICS-202)', status: ['Open', 'Met', 'Dropped'], fields: [
    ['objective', 'Objective', 'area', true], ['opPeriod', 'Operational period', 'number'], ['priority', 'Priority', ['High', 'Medium', 'Low']],
    ['lead', 'Lead section or organization', 'text']], list: ['objective', 'opPeriod', 'priority'], updates: [['text', 'Comment', 'area']] }
  B.assign = { title: 'Assignments (ICS-204)', status: ['Assigned', 'Acknowledged', 'Working', 'Complete', 'Canceled'], fields: [
    ['to', 'Assigned to (an organization on the network)', orgNames, true], ['work', 'Work assignment', 'area', true],
    ['where', 'Division / location', 'text'], ['here', 'At my location', 'here'], ['opPeriod', 'Operational period', 'number'],
    ['resources', 'Resources assigned', 'text'], ['comms', 'Communications (channel, frequency, phone)', 'text'], ['instructions', 'Special instructions', 'area']],
    list: ['to', 'work', 'opPeriod'], updates: [['text', 'Progress', 'area']] }
  B.staff = { title: 'EOC staffing', status: ['On duty', 'Off duty'], fields: [
    ['post', 'Position', positions, true], ['person', 'Name', 'text', true], ['agency', 'Agency', 'text'], ['contact', 'Contact', 'text'],
    ['shift', 'Shift', ['Day', 'Night', 'Alpha', 'Bravo']]], list: ['post', 'person', 'agency'], updates: [['text', 'Comment', 'area']] }   // post: the core's "position" is the filer's
  B.lifelines = { title: 'Community lifelines', overviewWrites: true, status: LL_STATUS, fields: [
    ['lifeline', 'Lifeline', LL.map(l => l[1]), true], ['components', 'Components affected', 'text'], ['why', 'What the call is based on', 'area'],
    ['eta', 'Estimated stabilization', 'dt']], list: ['lifeline'] }
  RT.icsOrder.push('objectives', 'assign', 'staff', 'lifelines')

  // ------------------------------------------------------------ the lifelines: the EOC's call, and the reports into each
  const worst = (xs) => xs.includes('Unstable') ? 'Unstable' : xs.includes('Stabilizing') ? 'Stabilizing' : xs.length ? 'Stable' : 'Unknown'
  /** what the organizations report into each lifeline: [[source, state, note]] */
  function reports() {
    const r = {}; for (const [k] of LL) r[k] = []
    for (const f of Object.values(S.fac || {})) {
      const bad = f.diversion === 'Closed to ambulances' || f.power === 'No power' || f.oxygen === 'Critical' || f.staff === 'Critical'
      const warn = (f.diversion && f.diversion !== 'Open') || f.power === 'Generator — fuel low' || f.oxygen === 'Limited' || f.staff === 'Short' || (f.water && f.water !== 'Normal')
      r.health.push([f.name, bad ? 'Unstable' : warn ? 'Stabilizing' : 'Stable', [f.diversion, f.power, f.needs].filter(Boolean).join(' · ')])
    }
    for (const s of recs('shelters')) r.food.push([s.shelter, s.status === 'Full' || (+s.capacity && +s.occupancy >= +s.capacity) ? 'Stabilizing' : s.status === 'Closed' ? 'Stabilizing' : 'Stable', (s.occupancy ?? '?') + ' / ' + (s.capacity ?? '?')])
    for (const x of recs('roads')) if (x.status !== 'Open') r.transport.push([x.road, x.severity === 'Impassable' || x.status === 'Full closure' ? 'Stabilizing' : 'Stable', [x.status, x.reason].filter(Boolean).join(' · ')])
    for (const [k, by] of Object.entries(orgs())) {
      const ll = (CAT[k] || [])[2]; if (!ll || k === 'staff') continue      // a hospital reports on its desk (above), not by its presence
      for (const [n, ds] of Object.entries(by)) { const st = ds.map(d => d.status).filter(Boolean)
        r[ll].push([n, st.some(s => /out of service/i.test(s)) ? 'Stabilizing' : 'Stable', st.join(', ') || 'on the network']) }
    }
    return r
  }
  const call = (name) => recs('lifelines').find(x => x.lifeline === name)
  // what EOCAdmin's forms are filled from (paper.js)
  RT.fema = { orgs, reports, call, worst, recs, inc, opNow, LL, CAT, struct }
  RT.screen('fema-ll', () => {
    const rp = reports()
    sheet('fema-ll', 'Community lifelines', `<button type="button" onclick="RT.open('fema')">◂ EOC</button>
      <div class="meta">The EOC's call on each lifeline, and what the organizations on the network report into it (hospitals, shelters, roads, units).</div>
      ${LL.map(([k, n]) => { const c = call(n), d = worst(rp[k].map(x => x[1])), st = c ? c.status || 'Unknown' : d
        return `<div class="card ll"><b><span class="lld" style="background:${COLOR[st]}"></span> ${esc(n)}</b> <span class="meta">${c ? 'EOC: ' + esc(st) : 'from the reports: ' + esc(d)}${c && c.why ? ' · ' + esc(c.why) : ''}</span>
          ${rp[k].map(([s, x, note]) => `<div class="meta"><span class="lld" style="background:${COLOR[x]}"></span> ${esc(s)}${note ? ': ' + esc(note) : ''}</div>`).join('')}
          ${RT.me().overview ? `<button type="button" onclick="S.icsBack='fema-ll';RT.open('ics-new','lifelines',{lifeline:'${esc(n)}',status:'${c ? esc(c.status || '') : esc(d)}'})">Make the EOC's call</button>` : ''}</div>` }).join('')}`)
  })

  // ------------------------------------------------------------ the organizations on the network, by their app
  RT.screen('fema-orgs', () => {
    const o = orgs(), as = recs('assign')
    sheet('fema-orgs', 'Organizations', `<button type="button" onclick="RT.open('fema')">◂ EOC</button>
      <div class="meta">Everyone on the network, by the app they joined with. A category is here because its app joined; the EOC allocates none.</div>
      ${Object.entries(o).sort().map(([k, by]) => { const c = CAT[k] || [cap(k), '•']
        return `<h3>${c[1]} ${esc(c[0])} <span class="meta">${Object.keys(by).length}</span></h3>` + Object.entries(by).sort().map(([n, ds]) => {
          const open = as.filter(a => a.to === n && !['Complete', 'Canceled'].includes(a.status)).length
          return `<div class="card"><b>${esc(n)}</b> <span class="meta">${ds.length} on now${ds.map(d => d.status).filter(Boolean).length ? ' · ' + esc([...new Set(ds.map(d => d.status).filter(Boolean))].join(', ')) : ''}${open ? ' · ' + open + ' open assignment(s)' : ''}</span>
            <div class="meta">${ds.map(d => esc(d.name || d.id)).join(', ')}</div>
            ${RT.me().overview ? `<button type="button" onclick="S.icsBack='fema-orgs';RT.open('ics-new','assign',{to:'${esc(n)}',opPeriod:${opNow()}})">Assign</button>` : ''}</div>` }).join('') }).join('')
      || '<div class="meta">no organization on the network yet</div>'}`)
  })

  // ------------------------------------------------------------ staffing by the incident's structure
  RT.screen('fema-staff', () => {
    const st = recs('staff'), who = (p) => st.find(x => x.post === p)
    sheet('fema-staff', 'EOC staffing', `<button type="button" onclick="RT.open('fema')">◂ EOC</button>
      <div class="meta">${esc(inc().structure || 'ICS or ICS-like')} (set on the Incident board). Each position: who holds it now.</div>
      ${Object.entries(struct()).map(([sec, ps]) => `<h3>${esc(sec)}</h3><table class="ics">${ps.map(p => { const x = who(p)
        return `<tr><th>${esc(p)}</th><td>${x ? esc(x.person) + (x.agency ? ' · ' + esc(x.agency) : '') + ' · ' + esc(x.status || 'On duty') : '<span class="meta">vacant</span>'}</td>
          <td>${x ? `<button type="button" onclick="RT.open('ics-rec','staff','${esc(x.id)}')">Open</button>` : ''} <button type="button" onclick="S.icsBack='fema-staff';RT.open('ics-new','staff',{post:'${esc(p)}',status:'On duty'})">${x ? 'Replace' : 'Fill'}</button></td></tr>` }).join('')}</table>`).join('')}`)
  })

  // ------------------------------------------------------------ the hub: the EOC function on one screen
  RT.screen('fema', () => {
    const i = inc(), obj = recs('objectives').filter(o => o.status !== 'Dropped' && (+o.opPeriod || opNow()) === opNow())
    const as = recs('assign').filter(a => !['Complete', 'Canceled'].includes(a.status)), rr = recs('rr').filter(r => !['Complete', 'Closed', 'Canceled', 'Rejected'].includes(r.status))
    const rp = reports()
    sheet('fema', 'EOC', `<div class="meta">${i.name ? esc(i.name) + ' · ' + esc(i.level || '') + ' · ' + esc(i.ictype || '') + ' · op period ' + opNow() : 'No incident yet: set it on the Incident board'}</div>
      <div class="llrow">${LL.map(([k, n]) => { const c = call(n), st = c ? c.status || 'Unknown' : worst(rp[k].map(x => x[1]))
        return `<span class="llp" title="${esc(n)}: ${esc(st)}"><span class="lld" style="background:${COLOR[st]}"></span>${esc(n.split(/[ (,]/)[0])}</span>` }).join('')}</div>
      <div class="sens">
        <button type="button" onclick="RT.open('fema-ll')"><b>Community lifelines</b> <span class="meta">the EOC's call and the reports</span></button>
        <button type="button" onclick="RT.open('fema-orgs')"><b>Organizations</b> <span class="meta">${Object.values(orgs()).reduce((a, by) => a + Object.keys(by).length, 0)} on the network</span></button>
        <button type="button" onclick="RT.open('fema-staff')"><b>Staffing</b> <span class="meta">${recs('staff').filter(s => s.status !== 'Off duty').length} on duty</span></button>
        <button type="button" onclick="RT.open('ics-list','objectives')"><b>Objectives (ICS-202)</b> <span class="meta">${obj.length} this period</span></button>
        <button type="button" onclick="RT.open('ics-list','assign')"><b>Assignments (ICS-204)</b> <span class="meta">${as.length} open</span></button>
        <button type="button" onclick="RT.open('ics-list','rr')"><b>Resource requests (ICS-213RR)</b> <span class="meta">${rr.length} open</span></button>
        <button type="button" onclick="RT.open('ics')"><b>All boards</b> <span class="meta">incident, activity log, sitreps, shelters, roads, damage, 214s, press, sign-in</span></button>
      </div>`)
    RT.sectioned('fema')
  })
  RT.button('femaBtn', 'EOC', () => RT.open('fema'), 'the EOC function: lifelines, organizations, staffing, objectives, assignments, requests', '#icsBtn')

  // ------------------------------------------------------------ a Team's or unit's own assignments
  RT.screen('fema-mine', () => {
    const me = cap(S.team), as = recs('assign').filter(a => a.to === me)
    sheet('fema-mine', 'Assignments: ' + me, `<div class="meta">What the EOC assigned ${esc(me)}. Open one to acknowledge it and report progress.</div>
      <table class="ics"><tr><th>Assignment</th><th>Where</th><th>Op</th><th>Status</th></tr>${as.map(a => `<tr onclick="RT.open('ics-rec','assign','${esc(a.id)}')"><td>${esc(a.work)}</td><td>${esc(a.where || '')}</td><td>${esc(a.opPeriod || '')}</td><td>${esc(a.status || 'Assigned')}</td></tr>`).join('')
      || '<tr><td colspan="4" class="meta">none yet</td></tr>'}</table>`)
  })
  RT.button('mineBtn', 'Assignments', () => RT.open('fema-mine'), "the EOC's assignments to this team or unit", '#layersBtn')
  RT.hook('connected', () => {
    $('#femaBtn').hidden = !RT.me().overview
    $('#mineBtn').hidden = RT.me().overview || !S.team || !!RT.me().only
  })
  RT.hook('refresh', (what) => {
    if (what !== 'ics') return
    if (['fema', 'fema-ll', 'fema-orgs', 'fema-staff', 'fema-mine'].includes(S.sheet)) RT.open(S.sheet)
  })
  RT.hook('world', () => { if (['fema', 'fema-orgs', 'fema-ll'].includes(S.sheet)) RT.open(S.sheet) })
  // a new assignment to this team or unit: a notice
  let seen = null
  RT.on('ics', () => {
    if (RT.me().overview || !S.team) return
    const mine = recs('assign').filter(a => a.to === cap(S.team)).map(a => a.id)
    if (seen) for (const id of mine) if (!seen.has(id)) status('the EOC assigned ' + cap(S.team) + ': ' + (RT.icsNow('assign', id).work || ''))
    seen = new Set(mine)
  })
})
