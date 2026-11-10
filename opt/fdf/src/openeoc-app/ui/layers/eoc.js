// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/eoc.js -- [RT_LAYERS_V1] the EOC's view: logged in (its user's role is the admin's "eoc", any case), the whole
// map and every team, the reports to the EOC on Requests (and a notice for each new one), and [RT_COMPLIANCE_V1] the
// nodes whose environmental sensors are out of compliance (asked at connect; on the map, the status line, Sensors).
// Its acks and overview: the service requests it acknowledges (field), everything under way (other layers' sections).
'use strict'
RT.layer('eoc', (RT) => {
  RT.role('eoc', { label: 'Command Center', title: 'OpenEOC Command Center', field: 'login', who: () => 'Command Center', kinds: 'all', teams: 'all',
                   log: true, acks: true, overview: true, act: ['Actions', 'field'], req: ['Requests', 'requests'],
                   connected() { send({ op: 'compliance' }) } })

  // the reports to the EOC, on its Requests; a notice when one comes in
  RT.section('requests', () => RT.me().overview ? `<h3>Reports to the Command Center (${Object.keys(S.reports || {}).length})</h3>` +
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
  // ------------------------------------------------------------ [EOC_LEADS_V1] Leads
  // (John 2026-11-09: "the EOC needs to have a menu named Leads. [Click] that and you get the Lead creation screen, which has
  // the name, password, and list of Roles the name can handle. The list of roles may be * for all roles, or a hand-entered
  // list of only those that user can run") A lead's QR is the EOC's door and the lead's name, never the password: the lead
  // scans it, sees its name filled in, names its team, gives its password, and creates the team (as one of its roles).
  const VIEWS = ['team', 'hospital', 'ambulance', 'shelter', 'power', 'water', 'phone']
  S.leads = null
  RT.on('leads', (m) => { S.leads = m.leads || {}; S.leadTeams = m.teams || {}; S.leadsDoor = m.door || ''; refreshSheet('leads') })
  RT.hook('refresh', (what) => { if (what === 'leads' && S.sheet === 'leads') RT.open('leads') })
  const doorUrl = () => 'http://' + String(S.leadsDoor || $('#connect').door.value || '').replace(/^https?:\/\//, '').replace(/\/.*/, '') + '/dc/ram.php'
  RT.screen('leads', (qr) => {
    const ls = S.leads
    // what is typed in the form stays when the list comes in (a redraw is not a reset)
    const was = S.sheet === 'leads' && document.querySelector('#sheet .leadform'), keep = {}
    if (was) for (const k of ['lead', 'password', 'roles']) keep[k] = was[k].value
    sheet('leads', 'Leads', `<form class="leadform">
        <div class="meta">A lead creates and runs a team. Give the lead a name, a password, and the roles they can run their team as.</div>
        <label>Name <input name="lead" autocomplete="off" autocapitalize="none"></label>
        <label>Password <input name="password" type="password" autocomplete="new-password"></label>
        <label>Roles <input name="roles" value="*" autocomplete="off" autocapitalize="none"></label>
        <div class="meta">* for all roles, or a list: ${VIEWS.join(', ')}</div>
        <button class="go">Create the lead</button><div class="leadsay${S.leadSay && !S.leadSay.ok ? ' bad' : ''}">${esc((S.leadSay || {}).text || '')}</div></form>
      <h3>Leads</h3>${ls === null ? '<div class="meta">asking the node…</div>' : `<table class="ics"><tr><th>Lead</th><th>Roles</th><th>Team</th><th></th></tr>
      ${Object.entries(ls).sort().map(([n, l]) => `<tr><td><b>${esc(n)}</b></td><td>${esc((l.roles || ['*']).join(', '))}</td><td>${l.team ? esc(l.team) + (l.kind ? ' <span class="meta">(' + esc(l.kind) + ')</span>' : '') : '<span class="meta">not created yet</span>'}</td>
        <td><button type="button" onclick="RT.open('leads','${esc(n)}')">QR</button> <button type="button" onclick="send({op:'leadRemove',lead:'${esc(n)}'})">Remove</button></td></tr>`).join('') || '<tr><td colspan="4" class="meta">none yet</td></tr>'}</table>`}
      <h3>Teams created on the network</h3>${ls === null ? '' : `<table class="ics"><tr><th>Team</th><th>Kind</th><th>Lead</th><th>Lives</th><th></th></tr>
      ${Object.entries(S.leadTeams || {}).sort().map(([t, x]) => `<tr><td><b>${esc(t)}</b></td><td>${esc(x.kind || 'team')}</td><td>${esc(x.lead || '')}</td>
        <td>${x.place === 'systemram' ? 'on the SystemRam' : x.place === 'here' ? "on its lead's phone" : '<span class="meta">its lead hosts it</span>'}</td>
        <td><button type="button" class="tdel" data-t="${esc(t)}">${S.teamDelSure === t ? 'Delete ' + esc(t) + ': sure?' : 'Delete'}</button></td></tr>`).join('') || '<tr><td colspan="5" class="meta">none yet</td></tr>'}</table>
      <div class="meta">Teams the admin configured are removed in the admin tool.</div>`}
      ${qr ? `<div class="card leadqr"><b>${esc(qr)}</b> scans this on the Command Center's Wi-Fi, names the team, gives their password, and creates it.<div class="qrbox">${FrogQR.svg(FrogQR.enrollLine(doorUrl(), 'openeoc', qr), { px: 300 })}</div>
        <div class="meta">${esc(doorUrl())} · the password is not in the code</div></div>` : ''}`)
    if (was) { const f = document.querySelector('#sheet .leadform'); for (const k in keep) f[k].value = keep[k] }
    // [TEAM_DELETE_V1] Delete, then Delete X: sure? (asked on the screen: no browser dialog)
    document.querySelectorAll('#sheet .tdel').forEach(b => b.onclick = () => {
      const t = b.dataset.t
      if (S.teamDelSure !== t) { S.teamDelSure = t; b.textContent = 'Delete ' + t + ': sure?'; return }
      S.teamDelSure = ''; S.leadSay = { ok: true, text: 'deleting team ' + t + '…' }; status('deleting team ' + t + '…'); send({ op: 'teamDelete', team: t })
    })
    document.querySelector('#sheet .leadform').onsubmit = (e) => {
      e.preventDefault(); const x = e.target
      // [LEAD_SAYS_ITS_ANSWER_V1] every refusal said on this screen (a form's own "required" says nothing in a desktop web view)
      const say = (t, ok) => { S.leadSay = { ok: !!ok, text: t }; const d = document.querySelector('#sheet .leadsay'); if (d) { d.textContent = t; d.classList.toggle('bad', !ok) } status(t) }
      const roles = x.roles.value.split(/[\s,]+/).map(r => r.trim().toLowerCase()).filter(Boolean)
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/.test(x.lead.value.trim())) return say("the lead's name: letters, digits, . _ - (at most 40)")
      if (x.password.value.length < 4) return say('a password of at least 4 characters')
      if (!roles.length) return say('the roles: * for all, or a list')
      const bad = roles.filter(r => r !== '*' && !VIEWS.includes(r))
      if (bad.length) return say('not a role: ' + bad.join(', ') + ' (the roles: * or ' + VIEWS.join(', ') + ')')
      say('creating lead ' + x.lead.value.trim() + '…', true)
      send({ op: 'leadAdd', lead: x.lead.value.trim(), password: x.password.value, roles })
      S.leadQr = x.lead.value.trim(); x.lead.value = ''; x.password.value = ''; x.roles.value = '*'
    }
  })
  RT.on('leadSaid', (m) => { S.leadSay = { ok: !!m.ok, text: m.text }; const d = document.querySelector('#sheet .leadsay'); if (d) { d.textContent = m.text; d.classList.toggle('bad', !m.ok) } if (!m.ok) S.leadQr = '' })
  // a lead just added: its QR, when the node's list has it
  RT.on('leads', () => { if (S.leadQr && S.leads && S.leads[S.leadQr] && S.sheet === 'leads') { const n = S.leadQr; S.leadQr = ''; RT.open('leads', n) } })
  RT.button('leadsBtn', 'Leads', () => { RT.open('leads'); send({ op: 'leads' }) }, 'the leads: who can create and run a team, and as which roles', '#layersBtn')
  RT.hook('connected', () => { $('#leadsBtn').hidden = S.role !== 'eoc' })

  RT.section('sens', () => {
    const bad = Object.keys(S.compliance)
    return bad.length ? '<div class="card ooc"><b>Out of compliance</b>' + bad.map(n => `<div>${esc(n)}: ${esc(envText(S.compliance[n]))}</div>`).join('') + '</div>' : ''
  })
})
