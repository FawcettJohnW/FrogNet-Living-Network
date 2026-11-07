// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/eoc.js -- [RT_LAYERS_V1] the EOC's view: logged in (its user's role is the admin's "eoc", any case), the whole
// map and every team, the reports to the EOC on Requests (and a notice for each new one), and [RT_COMPLIANCE_V1] the
// nodes whose environmental sensors are out of compliance (asked at connect; on the map, the status line, Sensors).
// Its acks and overview: the service requests it acknowledges (field), everything under way (other layers' sections).
'use strict'
RT.layer('eoc', (RT) => {
  RT.role('eoc', { label: 'EOC', title: 'RangerTrak EOC', field: 'login', who: () => 'EOC', kinds: 'all', teams: 'all',
                   log: true, acks: true, overview: true, act: ['Actions', 'field'], req: ['Requests', 'requests'],
                   connected() { send({ op: 'compliance' }) } })

  // the reports to the EOC, on its Requests; a notice when one comes in
  RT.section('requests', () => RT.me().overview ? `<h3>Reports to the EOC (${Object.keys(S.reports || {}).length})</h3>` +
    (Object.values(S.reports || {}).sort((a, b) => (b.at || 0) - (a.at || 0)).map(r => `<div class="card"><b>${esc(r.category)}</b> from ${esc(r.name)}
      <div class="meta">${r.at ? new Date(r.at * 1000).toLocaleTimeString() : ''}${typeof r.lat === 'number' ? ` · <a href="#" onclick="showOnMap(${r.lat},${r.lng});return false">map</a>` : ''}</div><div>${esc(r.text)}</div></div>`).join('') || '<div class="meta">none</div>') : '', 'bottom')
  let reportsSeen = -1
  RT.on('reports', () => {
    if (!RT.me().overview) return
    const n = Object.keys(S.reports).length
    if (reportsSeen >= 0 && n > reportsSeen) {
      const last = Object.values(S.reports).sort((a, b) => (b.at || 0) - (a.at || 0))[0]
      notify({ id: 'rep:' + n, from: last.from, name: last.name, to: 'all', text: 'Report: ' + last.category + ' — ' + last.text, at: last.at })
    }
    reportsSeen = n
  })

  // [RT_COMPLIANCE_V1] hosts with working environmental sensors
  RT.on('compliance', (m) => {
    const hosts = m.hosts || {}, asked = m.asked
    S.compliance = hosts; const names = Object.keys(hosts)
    status(names.length ? 'OUT OF COMPLIANCE: ' + names.map(n => n + ' (' + envText(hosts[n]) + ')').join(' · ') + ` — ${asked} node(s) asked`
                        : `Compliance: ${asked} node(s) asked; none has a working environmental sensor`)
    for (const n of names) notify({ id: 'ooc:' + n, from: n, name: n, to: 'all', text: 'Out of compliance: ' + envText(hosts[n]), at: Date.now() / 1000 })
    if (names.length && !Object.keys(S.gps).length) send({ op: 'hosts' })
    redraw()
  })
  RT.section('sens', () => {
    const bad = Object.keys(S.compliance)
    return bad.length ? '<div class="card ooc"><b>Out of compliance</b>' + bad.map(n => `<div>${esc(n)}: ${esc(envText(S.compliance[n]))}</div>`).join('') + '</div>' : ''
  })
})
