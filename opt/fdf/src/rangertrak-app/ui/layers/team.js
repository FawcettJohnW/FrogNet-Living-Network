// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/team.js -- [RT_LAYERS_V1] a team's view: the team chosen at connect (the admin's teams: the roles with a RAM,
// door op=roles), its own team on the map and its own chat first ([TEAM_VIEW_IS_THE_TEAM_V1]); [TEAM_HOST_V1] the lead's
// phone hosts the team, or [GROUP_PLACE_V1] places it on databasehost; [ENROLL_V1] the lead's Team members; the lead's
// card, read on its hotspot.
'use strict'
RT.layer('team', (RT) => {
  RT.role('team', { label: 'Team', title: 'RangerTrak Team', field: 'team', who: () => 'Team ' + S.team, kinds: ['team'], teams: 'own',
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
  RT.button('placeBtn', 'Team on databasehost', () => { status("the team's RAM moves to databasehost: it stays there when you leave"); window.rtNative.teamPlace('databasehost') },
            "the team's RAM on databasehost: it stays when the lead leaves")

  // ------------------------------------------------------------ [ENROLL_V1] the lead's Team members
  // The lead names each member (a callsign) and a password; this phone keeps only verifiers and sponsors the team: a member
  // scans the member's frog QR (this phone's address, the name) and types the password, and gets its own invitation.
  RT.button('membersBtn', 'Team members', () => window.rtNative.teamMembers())
  RT.hook('connected', () => { $('#membersBtn').hidden = !(S.cfg.enrolled && S.cfg.enrolled.lead && window.rtNative && window.rtNative.teamMembers) })
  function membersPanel(m) {
    if (m) S.mem = m
    const x = S.mem || { members: [], addrs: [] }
    const at = S.memAt && x.addrs.some(a => a.addr === S.memAt) ? S.memAt : ((x.addrs[0] || {}).addr || '')
    S.memAt = at
    const rows = x.members.map(n => `<div class="mem-row"><b>${esc(n)}</b><button type="button" data-memqr="${esc(n)}">QR</button><button type="button" data-memdel="${esc(n)}">✕</button></div>`).join('') || '<div class="meta">no members yet</div>'
    const sel = x.addrs.length ? `<div class="mem-at">Members reach this phone at <select id="memAt">${x.addrs.map(a => `<option value="${esc(a.addr)}"${a.addr === at ? ' selected' : ''}>${esc(a.label)}: ${esc(a.addr)}</option>`).join('')}</select></div>`
      : '<div class="mem-at">⚠ this phone has no address a member can reach: turn on its hotspot, or join Wi-Fi</div>'
    sheet('members', 'Team ' + cap(x.team || '') + ' members', `${x.sponsoring ? '' : '<div class="meta">⚠ not sponsoring yet</div>'}
      <div class="mem-add"><input id="memName" placeholder="name or callsign" autocomplete="off"><input id="memPw" type="password" placeholder="password" autocomplete="new-password"><button type="button" id="memAdd">Add</button></div>
      ${rows}${sel}<div class="mem-qr" id="memQr"></div>`)
    if ($('#memAt')) $('#memAt').onchange = (e) => { S.memAt = e.target.value; if (S.memQr) memQr(S.memQr) }
    $('#memAdd').onclick = () => { const n = $('#memName').value.trim(), p = $('#memPw').value; if (!n || !p) return; status('adding ' + n + '…'); window.rtNative.addMember(n, p) }
    if (S.memQr && x.members.includes(S.memQr)) memQr(S.memQr)
  }
  function memQr(n) {
    S.memQr = n; const x = S.mem || {}
    if (!S.memAt) { $('#memQr').innerHTML = '<div class="meta">no address to put in the code</div>'; return }
    const url = 'http://' + S.memAt + ':' + x.port + '/enroll'
    $('#memQr').innerHTML = FrogQR.svg(FrogQR.enrollLine(url, x.app, n), { px: 340 }) + `<div class="who">${esc(n)} · ${esc(S.memAt)}</div>`
  }
  document.addEventListener('click', (e) => {
    const t = e.target
    if (t.dataset && t.dataset.memqr) memQr(t.dataset.memqr)
    else if (t.dataset && t.dataset.memdel) { if (S.memQr === t.dataset.memdel) S.memQr = ''; window.rtNative.removeMember(t.dataset.memdel) }
  })
  RT.on('members', membersPanel)
})
