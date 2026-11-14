// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/team.js -- [RT_LAYERS_V1] a team's view: the team chosen at connect (the admin's teams: the roles with a RAM,
// door op=roles), its own team on the map and its own chat first ([TEAM_VIEW_IS_THE_TEAM_V1]); [TEAM_HOST_V1] the lead's
// phone hosts the team, or [GROUP_PLACE_V1] places it on databasehost; [ENROLL_V1] the lead's Team members; the lead's
// card, read on its hotspot.
'use strict'
RT.layer('team', (RT) => {
  RT.role('team', { label: 'Team', title: 'OpenEOC -- Team', field: 'team', who: () => 'Team ' + S.team, kinds: ['team'], teams: 'own',
                    log: true, act: ['Actions', 'field'], req: ['Requests', 'requests'],
                    connected() {
                      $('#hostBtn').hidden = !(window.rtNative && window.rtNative.teamHost)
                      // [GROUP_PLACE_V1] the lead's choice of where its team lives: Host team (this phone) or databasehost
                      $('#placeBtn').hidden = !(S.cfg.enrolled && S.cfg.enrolled.lead && window.rtNative && window.rtNative.teamPlace)
                      // a team member opens on the teams' positions and the team's own chat; Everyone is a tab away
                      if (S.team) { S.tab = 'team:' + S.team; tabs(); toList(); $('#to').value = 'team:' + S.team }
                    } })
  // the connect screen's team: the admin's, asked of the node (op=roles)
  $('[data-field=login]').insertAdjacentHTML('beforebegin', '<div class="for-team" data-field="team" hidden><label>Team <select name="team"><option>Alpha</option><option>Bravo</option><option>Charlie</option><option>Delta</option></select></label></div>')
  RT.hook('init', (cfg) => { if (cfg.team) $('#connect').team.value = cfg.team })
  RT.on('roles', (m) => {
    const sel = $('#connect').team, was = sel.value
    // [RT_CATEGORY_IS_THE_VIEW_V1] the groups in the admin's Teams category (with a RAM): not its Ambulance, Power ... groups
    const teams = (S.groupsByView || {}).team || []
    if (!teams.length) return
    sel.innerHTML = teams.map(t => `<option value="${esc(cap(t))}">${esc(cap(t))}</option>`).join('')
    if (teams.some(t => cap(t) === was)) sel.value = was
  })
  const pick = (t) => { const f = $('#connect'); if (![...f.team.options].some(o => o.value === t)) f.team.insertAdjacentHTML('beforeend', `<option>${esc(t)}</option>`); f.team.value = t }
  RT.hook('enrolled', (m, view) => { if (view === 'team') pick(cap(m.role)) })

  // [TEAM_HOST_V1] the lead's card, read on its hotspot: the team, the EOC's node, its join line, the team's RAM there
  RT.on('teamCard', (m) => {
    const f = $('#connect')
    S.teamRam = m.teamRam || ''
    if (m.door) f.door.value = m.door
    if (m.team) pick(m.team)
    if (m.join) f.invite.value = m.join
    $('#cstatus').textContent = 'Team ' + (m.team || '') + (m.lead ? ', ' + m.lead + "'s hotspot" : '') + ': connecting'
    f.requestSubmit()
  })
  RT.button('hostBtn', 'Host team', () => { status("hosting the team: turn on this phone's hotspot if it is not on, then come back"); window.rtNative.teamHost() },
            "the team's RAM on this phone: it goes where the lead goes")
  // [TEAM_ON_SYSTEMRAM_V1] (John 2026-11-09: "Use SystemRam ... the role does not have its own RAM and runs everything
  // through databasehost.frognet, using the default api.php") in place of a RAM of the team's own on databasehost
  RT.button('placeBtn', 'Use SystemRam', () => { status("team " + cap(S.team) + ": no RAM of its own; everything through the SystemRam (databasehost.frognet's api.php)"); window.rtNative.teamPlace('systemram') },
            "the team has no RAM of its own: everything goes through the SystemRam (databasehost.frognet's api.php); it stays when the lead leaves")

  // ------------------------------------------------------------ [TEAM_JOIN_V1] the lead's team: one QR, one password
  // (John 2026-11-05: "One qr code for the whole team, and one password"; members give their own call signs.) The lead sets
  // the team's password (this phone keeps its verifier, never the password) and shows the team's QR ("fdfenroll1 URL APP *":
  // this phone's sponsor, at an address a member can reach). A member joins the lead's hotspot (Android's own hotspot QR),
  // scans the team's QR, gives a call sign and the team's password, and is in -- its own FrogNet invitation (its WireGuard
  // key: its tunnel through the hotspot) given with it. Who joined is listed; the lead can remove one.
  RT.button('membersBtn', 'Team members', () => window.rtNative.teamMembers())
  RT.hook('connected', () => { $('#membersBtn').hidden = !(S.cfg.enrolled && S.cfg.enrolled.lead && window.rtNative && window.rtNative.teamMembers) })
  function membersPanel(m) {
    if (m) S.mem = m
    const x = S.mem || { members: [], addrs: [] }
    const at = S.memAt && x.addrs.some(a => a.addr === S.memAt) ? S.memAt : ((x.addrs[0] || {}).addr || '')
    S.memAt = at
    // [MEMBER_PASSWORDS_OPTIONAL_V1] (John 2026-11-09: "default to one password for the team, with the option of individual
    // passwords if the lead says so") the team's password and QR first; a member with a password of their own is added under
    // Individual passwords, and has a QR of their own (the line names them: "fdfenroll1 URL APP <name> <key>")
    const own = x.own || []
    const ownRows = own.map(n => `<div class="mem-row"><b>${esc(n)}</b><button type="button" data-memqr="${esc(n)}">QR</button></div>`).join('')
    const rows = x.members.map(n => `<div class="mem-row"><b>${esc(n)}</b><button type="button" data-memdel="${esc(n)}" title="remove ${esc(n)}">✕</button></div>`).join('') || '<div class="meta">no one has joined yet</div>'
    const sel = x.addrs.length ? `<div class="mem-at">Members reach this phone at <select id="memAt">${x.addrs.map(a => `<option value="${esc(a.addr)}"${a.addr === at ? ' selected' : ''}>${esc(a.label)}: ${esc(a.addr)}</option>`).join('')}</select></div>`
      : '<div class="mem-at">⚠ this phone has no address a member can reach: turn on its hotspot (the team\'s Wi-Fi)</div>'
    sheet('members', 'Team ' + cap(x.team || ''), `${x.sponsoring ? '' : '<div class="meta">⚠ not sponsoring yet</div>'}
      <div class="mem-add"><input id="teamPw" type="password" placeholder="${x.teamPassword ? 'team password: set (type to change)' : 'the team password'}" autocomplete="new-password"><button type="button" id="teamPwSet">${x.teamPassword ? 'Change' : 'Set'}</button></div>
      ${sel}<div class="mem-qr" id="memQr"></div>
      <details id="memOwn"${S.memOwnOpen ? ' open' : ''}><summary>Individual passwords</summary>
        <div class="meta">Only if you want them: a member you add here joins with a QR code and a password of their own, not the team's.</div>
        <div class="mem-add"><input id="memName" placeholder="member's call sign" autocomplete="off" autocapitalize="characters">
          <input id="memPw" type="password" placeholder="their password" autocomplete="new-password"><button type="button" id="memAdd">Add</button></div>
        ${ownRows}<div class="mem-qr" id="memOwnQr"></div></details>
      <h3>Joined</h3>${rows}<h3>On now</h3><div id="memNow"></div>`)
    memNow()
    if ($('#memAt')) $('#memAt').onchange = (e) => { S.memAt = e.target.value; teamQr() }
    $('#teamPwSet').onclick = () => { const p = $('#teamPw').value; if (!p) return; status('setting the team password…'); window.rtNative.setTeamPassword(p) }
    $('#memOwn').ontoggle = () => { S.memOwnOpen = $('#memOwn').open }
    $('#memAdd').onclick = () => {
      const n = $('#memName').value.trim().toUpperCase(), p = $('#memPw').value
      if (!/^[A-Z0-9._-]{1,30}$/.test(n)) { status("a member's call sign: letters, digits, . _ - (at most 30)"); return }
      if (!p) { status(n + ': give their password'); return }
      if ((x.members || []).includes(n)) { status(n + ' is a member already'); return }
      S.memOwnOpen = true; S.memOwnQr = n; status('adding ' + n + '…'); window.rtNative.addMember(n, p)
    }
    if (S.memOwnQr && own.includes(S.memOwnQr)) ownQr(S.memOwnQr)
    teamQr()
  }
  // [ADMIN_TREE_V1] the team's admin likewise: everyone on now in this team, of whatever kind it says (a member, a unit,
  // a plug-in's device), from the world, as it changes
  function memNow() {
    const el = $('#memNow'); if (!el) return
    const t = String((S.mem || {}).team || S.team || '').toLowerCase()
    const on = Object.entries(S.who || {}).map(([id, w]) => [id, Object.assign({}, S.pos[id] || {}, w || {})])
      .filter(([, w]) => [w.team, w.unit].some(x => String(x || '').toLowerCase() === t))
    el.innerHTML = on.map(([id, w]) => `<div class="mem-row"><b>${esc(w.name || id)}</b> <span class="meta">${esc(w.kind || '?')}${w.via ? ' via ' + esc(w.via) : ''}</span></div>`).join('') || '<div class="meta">no one on now</div>'
  }
  RT.hook('world', memNow)
  function teamQr() {
    const x = S.mem || {}
    if (!x.teamPassword) { $('#memQr').innerHTML = '<div class="meta">set the team password: then the team\'s QR is here</div>'; return }
    if (!S.memAt) { $('#memQr').innerHTML = '<div class="meta">no address to put in the code</div>'; return }
    if (!x.key) { $('#memQr').innerHTML = '<div class="meta">the team\'s sponsor has no key yet: its QR is here when it has</div>'; return }   // [SEALED_ENROLL_V1]
    const url = 'http://' + S.memAt + ':' + x.port + '/enroll'
    $('#memQr').innerHTML = FrogQR.svg(FrogQR.enrollLine(url, x.app, '*', x.key), { px: 340 }) +   // [SEALED_ENROLL_V1] the sponsor key's fingerprint
      `<div class="who">Team ${esc(cap(x.team || ''))} · ${esc(S.memAt)}</div><div class="meta">A member joins this phone's hotspot (its QR is on the Hotspot screen), scans this, and gives a call sign and the team password.</div>`
  }
  // [MEMBER_PASSWORDS_OPTIONAL_V1] a member's own QR: their name in it, and the sponsor key's fingerprint as the team's has
  function ownQr(n) {
    const x = S.mem || {}, el = $('#memOwnQr'); if (!el) return
    if (!S.memAt || !x.key) { el.innerHTML = '<div class="meta">no address or no sponsor key yet: the QR is here when there are</div>'; return }
    const url = 'http://' + S.memAt + ':' + x.port + '/enroll'
    el.innerHTML = FrogQR.svg(FrogQR.enrollLine(url, x.app, n, x.key), { px: 300 }) +
      `<div class="who">${esc(n)} · Team ${esc(cap(x.team || ''))}</div><div class="meta">${esc(n)} joins this phone's hotspot, scans this, and gives the password you set for them.</div>`
  }
  document.addEventListener('click', (e) => {
    const t = e.target
    if (t.dataset && t.dataset.memqr) { S.memOwnQr = t.dataset.memqr; ownQr(t.dataset.memqr) }
    if (t.dataset && t.dataset.memdel) window.rtNative.removeMember(t.dataset.memdel)
  })
  RT.on('members', membersPanel)
})
