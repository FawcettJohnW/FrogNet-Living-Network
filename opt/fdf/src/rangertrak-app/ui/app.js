// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// app.js -- [RT_APP_UI_V1] the screens of the RangerTrak app. Everything it shows comes from the core's lines
// (rtDeliver); everything it does is a line to the core (rtNative.send). Nothing here talks to the network.
'use strict'
const $ = (s) => document.querySelector(s)
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const send = (o) => window.rtNative.send(JSON.stringify(o))
const cap = (t) => t ? t[0].toUpperCase() + t.slice(1).toLowerCase() : t   // a team as people say it: RAM alpha, team Alpha

// kinds: how each is drawn, and which views show it by default
const KINDS = {
  team:      { label: 'Teams',      shape: '',    size: 18, color: '#888', views: ['eoc', 'team'] },
  eoc:       { label: 'EOC',        shape: 'sq',  size: 20, color: '#6b4fd8', views: ['eoc'], ch: 'E' },
  ambulance: { label: 'Ambulances', shape: 'sq',  size: 18, color: '#e23b3b', views: ['eoc'], ch: '+' },
  boat:      { label: 'Boats',      shape: 'sq',  size: 18, color: '#1565c0', views: ['eoc'], ch: 'B' },
  victim:    { label: 'Victims',    shape: '',    size: 12, color: '#f2c200', views: ['eoc'], ch: '' },
  hospital:  { label: 'Hospitals',  shape: 'sq',  size: 20, color: '#ffffff', views: ['eoc', 'public'], ch: 'H', fg: '#c62828' },
  fire:      { label: 'Fire',       shape: 'dia', size: 18, color: '#ff6d00', views: ['eoc', 'public'], ch: 'F' },
  power:     { label: 'Power',      shape: 'dia', size: 18, color: '#7e57c2', views: ['eoc', 'public'], ch: 'P' },
  help:      { label: 'Help requests', shape: '', size: 16, color: '#ff1744', views: ['eoc', 'public'], ch: '!' },
  platform:  { label: 'Sensor platforms', shape: 'dia', size: 16, color: '#00897b', views: ['eoc'], ch: 'S' },
  service:   { label: 'Service requests', shape: 'sq', size: 16, color: '#f9a825', views: ['eoc'], ch: 'R', fg: '#222' },
  hazard:    { label: 'Hazards',    shape: 'dia', size: 18, color: '#d50000', views: ['eoc', 'public'], ch: '⚠' },
  search:    { label: 'Searched areas', shape: '', size: 14, color: '#546e7a', views: ['eoc'], ch: '✓' },
  host:      { label: 'Hosts',      shape: 'sq',  size: 16, color: '#263238', views: ['eoc'], ch: 'N' },   // a layer: on for the EOC
}
const TEAM_COLOR = { alpha: '#d62728', bravo: '#1f77b4', charlie: '#2ca02c', delta: '#ff7f0e' }
const LKP_MIN = 10            // a last known position is drawn this many minutes (the app's own choice)

const S = { fac: {}, tr: {}, trActs: {}, teamsOn: new Set(), field: {}, fieldActs: {}, compliance: {}, help: {}, actions: {}, reports: {}, sheet: '', gps: {}, cfg: {}, role: '', team: '', who: {}, pos: {}, seen: {}, lkp: {}, markers: {}, links: {}, on: {}, msgs: [], tab: 'all', direct: {} }
let map

// ------------------------------------------------------------ start: the native side calls rtInit once the page loads
window.rtInit = function (cfg) {
  S.cfg = cfg || {}
  const f = $('#connect')
  f.door.value = S.cfg.door || ''; f.name.value = S.cfg.name || ''
  // [RT_TEAM_APP_V1] [RT_PUBLIC_APP_V1] an app built for one view (the Team app, the Public app) opens as that view only
  const LOCKED = { team: 'RangerTrak Team', public: 'RangerTrak Public' }
  if (LOCKED[S.cfg.lock]) {
    S.cfg.role = S.cfg.lock; $('#roleSet').hidden = true
    document.querySelector('#connect h1').textContent = LOCKED[S.cfg.lock]; document.title = LOCKED[S.cfg.lock]
  }
  if (S.cfg.build) $('#cstatus').textContent = S.cfg.build      // [APP_SAYS_ITS_BUILD_V1] which build this is, on the first screen
  // [RT_IS_A_MEMBER_V1] the phone is the FrogNet member itself: its invitation, pasted once, then kept (and only said)
  if (S.cfg.platform === 'android') {
    // [ON_THE_LAN_NO_TUNNEL_V1] on a FrogNet node's own LAN nothing is asked: the node routes the phone. Off it, the
    // invitation once; after that only what this phone is on FrogNet is said
    const said = S.cfg.lan || S.cfg.invited
    document.querySelector('.for-invite').hidden = false
    document.querySelector('.for-invite label').hidden = !!said
    document.querySelector('.inv-say').textContent = said ? 'FrogNet: ' + said : ''
  }
  if (S.cfg.role) f.querySelector(`[name=role][value=${S.cfg.role}]`).checked = true
  if (S.cfg.team) f.team.value = S.cfg.team
  roleFields(); askRoles()
  setTimeout(() => { $('#splash').classList.add('gone'); f.hidden = false }, 1800)
}
function roleFields() {
  const r = $('#connect').querySelector('[name=role]:checked').value
  // [TEAM_HOST_V1] a team member's phone joins its lead's hotspot by scanning the hotspot's QR code
  // [ENROLL_V1] any view: the frog QR the admin (or the team's lead) shows, then the password
  $('#scanTeam').hidden = !(S.cfg.platform === 'android' && window.rtNative && window.rtNative.scanTeam)
  document.querySelector('.for-team').hidden = r !== 'team'; document.querySelector('.for-eoc').hidden = r !== 'eoc'
  document.querySelector('.for-unit').hidden = r !== 'hospital' && r !== 'ambulance'
  document.querySelector('.unit-l').textContent = r === 'hospital' ? 'Hospital' : 'Unit (e.g. Medic 12)'
}
document.querySelectorAll('[name=role]').forEach(e => e.onchange = roleFields)
// [ADMIN_APP_V1] the teams are the admin's: asked of the node as soon as there is one (and when it changes)
function askRoles() { const d = $('#connect').door.value.trim(); if (d) send({ op: 'roles', door: d }) }
$('#connect').door.addEventListener('change', askRoles)
function teamsFrom(roles) {
  const sel = $('#connect').team, was = sel.value
  const teams = Object.entries(roles).filter(([r, x]) => x.ram && !['eoc', 'hospital', 'hospitals', 'ambulance', 'public', 'command'].includes(r)).map(([r]) => r)
  if (!teams.length) return
  sel.innerHTML = teams.map(t => `<option value="${esc(cap(t))}">${esc(cap(t))}</option>`).join('')
  if (teams.some(t => cap(t) === was)) sel.value = was
}
$('#connect').onsubmit = (e) => {
  e.preventDefault()
  const f = e.target, role = f.querySelector('[name=role]:checked').value
  $('#cstatus').textContent = 'connecting…'
  send({ op: 'connect', door: f.door.value.trim(), dev: S.cfg.dev, name: f.name.value.trim(), role, team: f.team.value,
         user: f.user.value.trim(), password: f.password.value, unit: f.unit.value.trim(), invite: f.invite.value.trim(),
         teamRam: role === 'team' ? (S.teamRam || '') : '' })
}
$('#scanTeam').onclick = () => { $('#cstatus').textContent = 'scan the frog QR code your admin or team lead shows you'; window.rtNative.scanTeam() }
// [ENROLL_V1] scanned: who this code is for and where; the password, then Join
function enrollScan(m) {
  S.enrollLine = m.line
  $('#enrollWho').textContent = m.user + ' · ' + m.app + ' · ' + m.url.replace(/^https?:\/\//, '').replace(/\/.*/, '')
  $('#enrollBox').hidden = false; $('#enrollPw').value = ''; $('#enrollPw').focus()
  $('#cstatus').textContent = 'type ' + m.user + "'s password"
}
$('#enrollGo').onclick = () => {
  if (!S.enrollLine) return
  $('#cstatus').textContent = 'joining…'; $('#enrollGo').disabled = true
  S.enrollPw = $('#enrollPw').value   // [PASSWORD_NOT_LOGGED_V1] kept here for the connect: the native side never says it back (its lines are in Logcat)
  window.rtNative.enroll(JSON.stringify({ line: S.enrollLine, password: S.enrollPw }))
}
// [ENROLL_V1] enrolled: this phone is a member of the region now; the app connects as the user, in its role
function enrolled(m) {
  const f = $('#connect'); $('#enrollGo').disabled = false; $('#enrollBox').hidden = true; $('#enrollPw').value = ''
  S.cfg.enrolled = { user: m.user, role: m.role, lead: m.lead, door: m.door }
  if (m.door) f.door.value = m.door
  f.name.value = m.user; f.user.value = m.user; f.password.value = S.enrollPw || ''; S.enrollPw = ''
  const view = ['eoc', 'hospital', 'ambulance', 'public'].includes(m.role) ? m.role : 'team'
  const rb = f.querySelector(`[name=role][value=${view}]`); if (rb && !$('#roleSet').hidden) rb.checked = true
  if (view === 'team') { const t = cap(m.role); if (![...f.team.options].some(o => o.value === t)) f.team.insertAdjacentHTML('beforeend', `<option>${esc(t)}</option>`); f.team.value = t }
  roleFields()
  $('#cstatus').textContent = m.user + (m.lead ? ', lead of ' + m.role : ' in ' + m.role) + ': joined FrogNet (' + m.invited + '); connecting'
  f.requestSubmit()
}

// ------------------------------------------------------------ [ENROLL_V1] the lead's Team members
// The lead names each member (a callsign) and a password; this phone keeps only verifiers and sponsors the team: a member
// scans the member's frog QR (this phone's address, the name) and types the password, and gets its own invitation.
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
$('#membersBtn').onclick = () => window.rtNative.teamMembers()
// [TEAM_HOST_V1] the lead's card, read on its hotspot: the team, the EOC's node, its join line, the team's RAM there
function teamCard(m) {
  const f = $('#connect')
  S.teamRam = m.teamRam || ''
  if (m.door) f.door.value = m.door
  if (m.team) { if (![...f.team.options].some(o => o.value === m.team)) f.team.insertAdjacentHTML('beforeend', `<option>${esc(m.team)}</option>`); f.team.value = m.team }
  if (m.join) f.invite.value = m.join
  $('#cstatus').textContent = 'Team ' + (m.team || '') + (m.lead ? ', ' + m.lead + "'s hotspot" : '') + ': connecting'
  f.requestSubmit()
}

// ------------------------------------------------------------ the core's lines
window.rtDeliver = function (line) {
  const m = typeof line === 'string' ? JSON.parse(line) : line
  if (m.ev === 'status') status(m.text)
  else if (m.ev === 'error') { status('⚠ ' + m.text); if (!$('#connect').hidden) $('#cstatus').textContent = m.text }
  else if (m.ev === 'connected') connected(m)
  else if (m.ev === 'teamCard') teamCard(m)
  else if (m.ev === 'enrollScan') enrollScan(m)
  else if (m.ev === 'enrolled') enrolled(m)
  else if (m.ev === 'enrollFailed') { $('#enrollGo').disabled = false; $('#cstatus').textContent = '⚠ ' + m.text; status('⚠ ' + m.text) }
  else if (m.ev === 'members') membersPanel(m)
  else if (m.ev === 'roles') teamsFrom(m.roles || {})
  else if (m.ev === 'mission') { $('#mission').textContent = m.name || m.id; $('#heroMission').textContent = m.name || m.id
    const changed = S.mission && S.mission.id !== m.id
    S.mission = m; if (S.sheet === 'mis' && !$('#sheet').hidden && !S.misEditing) missionPanel()
    if (changed) notify({ id: 'mis:' + m.id, from: '', name: 'Mission', to: 'all', text: 'the mission now is ' + (m.name || m.id), at: Date.now() / 1000 })
    addressFix() }
  else if (m.ev === 'world') world(m.who || {}, m.pos || {})
  else if (m.ev === 'msg') message(m)
  else if (m.ev === 'help') { S.help = m.requests || {}; S.actions = m.actions || {}; redraw(); refreshSheet('help') }
  else if (m.ev === 'reports') { S.reports = m.list || {}; refreshSheet('reports'); if (S.role === 'eoc') reportNotice() }
  else if (m.ev === 'sensorHosts') sensorHosts(m.hosts || {})
  else if (m.ev === 'sensors') sensorList(m.host, m.list || [])
  else if (m.ev === 'sensor') sensorShow(m)
  else if (m.ev === 'field') { S.field = m.items || {}; S.fieldActs = m.acts || {}; redraw(); refreshSheet('field') }
  else if (m.ev === 'compliance') compliance(m.hosts || {}, m.asked)
  else if (m.ev === 'facilities') { S.fac = m.list || {}; redraw(); refreshSheet('fac'); heroSummary() }
  else if (m.ev === 'transports') { S.tr = m.items || {}; S.trActs = m.acts || {}; refreshSheet('tr'); trNotice() }
  else if (m.ev === 'comms') commsStart(m.peer, m.door)
  else if (m.ev === 'incoming') ring(m)
  else if (m.ev === 'devices') devicePicker(m)
  else if (m.ev === 'myfix') { myFix = { lat: m.lat, lon: m.lng }; followMe() }
  else if (m.ev === 'radiolog') { S.rlog = m; if (S.sheet === 'rlog' && !$('#sheet').hidden) radioLog() }
  else if (m.ev === 'nogps') noGps(m)
  else if (m.ev === 'geocoded') geocoded(m)
  else if (m.ev === 'hosts') { S.gps = {}; for (const h of m.list || []) S.gps['gps:' + h.name] = Object.assign({ kind: 'host' }, h); redraw() }
}
function status(t) { $('#status').textContent = t; $('#dot').classList.toggle('up', !/FAILING|cannot|⚠/.test(t)) }

function connected(m) {
  S.role = m.role; S.team = m.team || ''; S.connectedAt = Date.now()
  $('#connect').hidden = true; $('#main').hidden = false
  $('#who').textContent = S.cfg.name + ' · ' + (S.role === 'eoc' ? 'EOC' : S.role === 'team' ? 'Team ' + S.team : S.role === 'hospital' ? S.team : S.role === 'ambulance' ? S.team : 'Public')
  $('#dot').classList.add('up'); status('connected to the node at ' + (S.cfg.door || '') + ' (' + (m.build || '') + ')')
  if (!map) {
    map = L.map('map', { zoomControl: true, doubleClickZoom: false }).setView([47.55, -122.33], 10)
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors' }).addTo(map)
    // [MAP_FILLS_ITS_PANE_V1] the map is made the moment its pane is shown, before the window has laid it out (seen in
    // the Windows app: the map in a corner, tiles of other sizes around it). Leaflet is told its real size once laid
    // out, and again whenever the pane changes size (window resized, moved to another screen's scaling).
    const fit = () => map.invalidateSize({ pan: false })
    requestAnimationFrame(() => { fit(); setTimeout(fit, 250); setTimeout(fit, 1000) })
    if (window.ResizeObserver) new ResizeObserver(fit).observe(document.getElementById('map'))
    window.addEventListener('resize', fit)
  }
  for (const k in KINDS) S.on[k] = KINDS[k].views.includes(S.role)
  S.on.lkp = S.role !== 'public'
  S.teamsOn = new Set(S.role === 'team' ? [S.team.toLowerCase()] : Object.keys(TEAM_COLOR))
  layersPanel(); legend()
  if (S.on.host) send({ op: 'hosts' })                 // the nodes' own GPS sensors, read once now
  $('#chatBtn').hidden = S.role === 'public'
  $('#homeBtn').hidden = S.role !== 'public'; $('#reqBtn').hidden = S.role === 'public'; $('#sensBtn').hidden = S.role === 'public'
  $('#hostBtn').hidden = !(S.role === 'team' && window.rtNative && window.rtNative.teamHost)
  $('#membersBtn').hidden = !(S.cfg.enrolled && S.cfg.enrolled.lead && window.rtNative && window.rtNative.teamMembers)
  $('#home').hidden = S.role !== 'public'          // [RT_PUBLIC_HOME_V1] the public opens on what a person can do, not the map
  // (John, 2026-11-01) the EOC starts with the map, teams and resources on it; the chat is a button away.
  // A team member starts with only their own team on the map; the others are added from Layers
  S.teamsOn = new Set(S.role === 'team' ? [S.team.toLowerCase()] : Object.keys(TEAM_COLOR))
  $('#actBtn').hidden = S.role === 'public'
  if (S.role === 'eoc') send({ op: 'compliance' })
  // [TEAM_VIEW_IS_THE_TEAM_V1] (John, 2026-11-01) a team member opens on the teams' positions and the team's own chat;
  // everything else is a layer to turn on, and Everyone is a tab away
  if (S.role === 'team' && S.team) { S.tab = 'team:' + S.team }
  tabs(); toList(); if (S.role === 'team' && S.team) $('#to').value = 'team:' + S.team
  redraw()
}

// ------------------------------------------------------------ the map
function colorOf(b) { if (b.ooc) return '#d50000'; return b.kind === 'team' || (b.kind === 'eoc' && false) ? (TEAM_COLOR[String(b.team || '').toLowerCase()] || b.color || '#888') : (KINDS[b.kind] || KINDS.team).color }
function icon(b, id, lkp) {
  const k = KINDS[b.kind] || KINDS.team, c = colorOf(b), s = k.size
  const ch = b.kind === 'team' ? String(b.team || '?')[0] : (k.ch ?? '')
  const me = id === S.cfg.dev ? ' me' : ''
  const html = `<div class="mk ${k.shape}${lkp ? ' lkp' : ''}${me}" style="width:${s}px;height:${s}px;background:${c};color:${k.fg || '#fff'};border-color:${lkp ? c : '#fff'}"><b>${esc(ch)}</b></div>`
  return L.divIcon({ html, className: '', iconSize: [s, s], iconAnchor: [s / 2, s / 2] })
}
function world(who, pos) {
  const now = Date.now()
  for (const id in S.pos) if (!pos[id] && S.pos[id]) S.lkp[id] = { b: S.pos[id], t: now }   // it left: its last known position
  for (const id in pos) delete S.lkp[id]
  const before = Object.keys(S.who).join(); S.who = who; S.pos = pos; heroSummary()
  if (Object.keys(who).join() !== before && map) toList()
  redraw()
}
// [TEAM_VIEW_IS_THE_TEAM_V1] which teams are drawn: a team member starts with their own; the EOC with all
function shown(b) {
  if (b.kind === 'team') return !!S.on.team && S.teamsOn.has(String(b.team || '').toLowerCase())
  return S.on[b.kind] !== undefined ? S.on[b.kind] : S.on.team
}
function popupHtml(id, b, lkp) {
  const bits = [b.status, b.beds !== undefined ? b.beds + ' beds available' : '', b.severity, b.customers_out ? b.customers_out + ' customers out' : '', b.to ? 'to ' + b.to : '']
  return `<b>${esc(b.name)}</b>${b.team ? ' · ' + esc(b.team) : ''}<br>${esc(bits.filter(Boolean).join(' · '))}` +
    (lkp ? `<br><i>last known position, ${Math.round((Date.now() - lkp.t) / 60000)} min ago</i>` : '') +
    (talkable(id, b) && !lkp ? `<br><button onclick="openPop('${esc(id)}')">Chat</button>` : '')
}
function talkable(id, b) { return S.role !== 'public' && id !== S.cfg.dev && (b.kind === 'team' || b.kind === 'eoc') }
function place(id, b, lkp) {
  if (typeof b.lat !== 'number') return
  let mk = S.markers[id]
  const key = (lkp ? 'L' : '') + b.kind + colorOf(b) + (id === S.cfg.dev)
  if (!mk) {
    mk = S.markers[id] = L.marker([b.lat, b.lng], { icon: icon(b, id, lkp), keyboard: false }).addTo(map)
    mk.on('dblclick', () => { if (talkable(id, S.pos[id] || b)) openPop(id) })
    mk._k = key
  } else {
    mk.setLatLng([b.lat, b.lng]); if (mk._k !== key) { mk.setIcon(icon(b, id, lkp)); mk._k = key }
  }
  mk.bindPopup(popupHtml(id, b, lkp))
}
function redraw() {
  if (!map) return
  const want = {}, now = Date.now()
  for (const id in S.pos) { const b = S.pos[id]; if (b && b.kind && shown(b) && visibleInView(b)) { want[id] = 1; place(id, b, null) } }
  if (S.on.lkp) for (const id in S.lkp) {
    const l = S.lkp[id]
    if (now - l.t > LKP_MIN * 60000) { delete S.lkp[id]; continue }
    if (shown(l.b) && visibleInView(l.b)) { want[id] = 1; place(id, l.b, l) }
  }
  if (S.on.host) for (const id in S.gps) {
    const h = S.gps[id], bad = S.compliance[h.name]
    want[id] = 1; place(id, bad ? Object.assign({}, h, { kind: 'host', ooc: true, status: 'OUT OF COMPLIANCE: ' + envText(bad) }) : h, null)
  }
  for (const fid in S.field) {                       // [RT_FIELD_V1] what the teams did in the field
    const b = S.field[fid], k = b.what
    if (!KINDS[k] || !S.on[k] || typeof b.lat !== 'number' || !visibleInView({ kind: k })) continue
    if (k === 'service' && fieldState(fid) === 'done') continue
    const id = 'field:' + fid; want[id] = 1
    place(id, { kind: k, name: (b.type || k) + (b.team ? ' · ' + b.team : ''), lat: b.lat, lng: b.lng,
                status: [b.priority, fieldState(fid), b.note, 'by ' + (b.name || '')].filter(Boolean).join(' · ') }, null)
  }
  if (S.on.help) for (const rid in S.help) {                // [RT_HELP_V1] requests needing help, where they said they are
    const b = S.help[rid], l = b.location || {}
    if (b.status !== 'open' || typeof l.lat !== 'number') continue
    const id = 'help:' + rid; want[id] = 1
    place(id, { kind: 'help', name: b.name + ' (' + (b.people || 1) + ')', lat: l.lat, lng: l.lon, status: (b.needs || []).join(', ') + (b.note ? ' — ' + b.note : '') }, null)
  }      // real nodes, from their GPS sensors
  for (const id in S.markers) if (!want[id]) { map.removeLayer(S.markers[id]); delete S.markers[id] }
  // hosts: who is connected through which node
  for (const id in S.links) { map.removeLayer(S.links[id]); delete S.links[id] }
  if (S.on.host) for (const id in S.pos) {
    const b = S.pos[id], h = b && b.via && (S.pos[b.via] || Object.values(S.gps).find(g => g.address === b.via))
    if (h && want[id] && typeof h.lat === 'number') S.links[id] = L.polyline([[b.lat, b.lng], [h.lat, h.lng]], { color: '#90a4ae', weight: 1, opacity: .6, dashArray: '3 4' }).addTo(map)
  }
}
function visibleInView(b) { return S.role !== 'public' || ['hospital', 'fire', 'power', 'help', 'hazard'].includes(b.kind) }
function layersPanel() {
  const p = $('#layers'); p.innerHTML = ''
  const kinds = Object.keys(KINDS).filter(k => S.role !== 'public' || ['hospital', 'fire', 'power', 'help', 'hazard'].includes(k))
  if (S.role !== 'public') {                     // the teams, one by one: a member adds others from here
    const h = document.createElement('div'); h.className = 'lh'; h.textContent = 'Teams'; p.appendChild(h)
    for (const t of Object.keys(TEAM_COLOR)) {
      const l = document.createElement('label')
      l.innerHTML = `<input type="checkbox" ${S.teamsOn.has(t) ? 'checked' : ''}> <i style="background:${TEAM_COLOR[t]}"></i> ${t[0].toUpperCase() + t.slice(1)}`
      l.querySelector('input').onchange = (e) => { e.target.checked ? S.teamsOn.add(t) : S.teamsOn.delete(t); redraw() }
      p.appendChild(l)
    }
    const h2 = document.createElement('div'); h2.className = 'lh'; h2.textContent = 'Show'; p.appendChild(h2)
  }
  for (const k of kinds.filter(k => k !== 'team').concat(S.role !== 'public' ? ['lkp'] : [])) {
    const l = document.createElement('label')
    l.innerHTML = `<input type="checkbox" ${S.on[k] ? 'checked' : ''}> ${k === 'lkp' ? 'Last known positions' : KINDS[k].label}`
    l.querySelector('input').onchange = (e) => { S.on[k] = e.target.checked; if (k === 'host' && S.on.host) send({ op: 'hosts' }); redraw() }
    p.appendChild(l)
  }
}
$('#layersBtn').onclick = () => { $('#layers').hidden = !$('#layers').hidden }
function legend() {
  const g = $('#legend')
  if (S.role === 'public') { g.innerHTML = '<span><i style="background:#fff"></i>Hospital</span><span><i style="background:#ff6d00"></i>Fire</span><span><i style="background:#7e57c2"></i>Power outage</span>'; return }
  g.innerHTML = Object.entries(TEAM_COLOR).map(([t, c]) => `<span><i style="background:${c}"></i>${t[0].toUpperCase() + t.slice(1)}</span>`).join('') +
    '<span><i style="background:#e23b3b;border-radius:2px"></i>Ambulance</span><span><i style="background:#f2c200"></i>Victim</span>'
}

// ------------------------------------------------------------ chat
function teamsHere() { return S.role === 'eoc' ? Object.keys(TEAM_COLOR).map(t => t[0].toUpperCase() + t.slice(1)) : (S.team ? [S.team] : []) }
function tabs() {
  const t = $('#tabs'); t.innerHTML = ''
  for (const [k, n] of [['all', 'Everyone']].concat(teamsHere().map(x => ['team:' + x, x])).concat([['dev', 'Direct']])) {
    const b = document.createElement('button'); b.textContent = n; b.className = S.tab === k ? 'on' : ''
    b.onclick = () => { S.tab = k; tabs(); showLog() }; t.appendChild(b)
  }
  showLog()
}
function toList() {
  const s = $('#to'); const cur = s.value; s.innerHTML = ''
  // [DM_TO_ANYONE_PRESENT_V1] direct: every person present now (their own presence cell), real devices first, then
  // anyone talked to already; the simulated ones are reached from the map
  const people = Object.entries(S.who).filter(([id, b]) => b && talkable(id, b) && !b.sim).map(([id, b]) => ['dev:' + id, b.name + (b.team ? ' (' + b.team + ')' : b.kind === 'eoc' ? ' (EOC)' : '')])
  const known = new Set(people.map(p => p[0]))
  const opts = [['all', 'Everyone']].concat(teamsHere().map(x => ['team:' + x, 'Team ' + x])).concat(people)
    .concat(Object.entries(S.direct).filter(([id]) => !known.has('dev:' + id)).map(([id, n]) => ['dev:' + id, n]))
  for (const [v, n] of opts) { const o = document.createElement('option'); o.value = v; o.textContent = n; s.appendChild(o) }
  if (cur) s.value = cur
}
// [EVERYONE_TAB_HAS_ALL_V1] the Everyone tab is every message this device has: team and direct ones too, marked
function inTab(m) { return S.tab === 'all' ? true : S.tab === 'dev' ? m.to.startsWith('dev:') : m.to === S.tab }
function msgHtml(m) {
  const mine = m.from === S.cfg.dev, at = m.at ? new Date(m.at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''
  const where = m.to === 'all' ? '' : m.to.startsWith('team:') ? ' → ' + m.to.slice(5) : mine ? ' → ' + (S.direct[m.to.slice(4)] || 'direct') : ' → you'
  const t = m.to.startsWith('team:') ? m.to.slice(5) : '', tc = t ? (TEAM_COLOR[t.toLowerCase()] || '#888') : ''
  const kind = t ? ' team' : m.to.startsWith('dev:') ? ' direct' : ''
  const style = t ? ` style="border-left:5px solid ${tc}"` : ''
  const tag = t ? `<span class="tag" style="background:${tc}">${esc(t)}</span> ` : m.to.startsWith('dev:') ? '<span class="tag dm">direct</span> ' : ''
  return `<div class="m${mine ? ' mine' : ''}${kind}"${style}><div class="h">${tag}${esc(m.name)}${esc(where)} · ${at}</div>${esc(m.text)}</div>`
}
function showLog() { const l = $('#log'); l.innerHTML = S.msgs.filter(inTab).map(msgHtml).join(''); l.scrollTop = l.scrollHeight }
function message(m) {
  if (S.seen[m.id]) return; S.seen[m.id] = 1
  S.msgs.push(m); S.msgs.sort((a, b) => (a.at || 0) - (b.at || 0))
  if (m.to.startsWith('dev:') && m.from !== S.cfg.dev && !S.direct[m.from]) { S.direct[m.from] = m.name; toList() }
  showLog(); popLog(); notify(m)
}
// [CHAT_NOTICE_V1] a message the user cannot see right now (the chat closed, or on another tab, and not in the open
// member box): a notice on the map, which opens it, and a count on the Chat button until the chat is opened
let unread = 0
function seenNow(m) {
  if (!$('#pop').hidden && popId && (m.to === 'dev:' + popId || m.from === popId)) return true
  return !$('#chat').hidden && inTab(m)
}
function notify(m) {
  if ((m.at || 0) * 1000 < (S.connectedAt || 0) - 2000) return  // written before this device connected: history, not news
  if (m.from === S.cfg.dev || seenNow(m) || (Date.now() / 1000 - (m.at || 0)) > 120) return
  unread++; $('#chatBtn').textContent = 'Chat (' + unread + ')'; $('#chatBtn').classList.add('hot')
  const n = document.createElement('div'); n.className = 'notice'
  const where = m.to === 'all' ? 'to everyone' : m.to.startsWith('team:') ? 'to ' + m.to.slice(5) : 'to you'
  n.innerHTML = `<b>${esc(m.name)}</b> <span>${esc(where)}</span><div>${esc(m.text)}</div>`
  n.onclick = () => { S.tab = m.to.startsWith('dev:') ? 'dev' : m.to; $('#chat').hidden = false; tabs(); readAll(); n.remove() }
  $('#notices').appendChild(n); setTimeout(() => n.remove(), 12000)
  if (window.rtNative && window.rtNative.notice) try { window.rtNative.notice(m.name, m.text) } catch (e) { }
}
function readAll() { unread = 0; $('#chatBtn').textContent = 'Chat'; $('#chatBtn').classList.remove('hot') }
$('#chatBtn').onclick = () => { $('#chat').hidden = !$('#chat').hidden; if (!$('#chat').hidden) readAll() }
// [RT_CALL_V1] the chat's Call: anyone can call anyone the To list has (a person)
$('#to').addEventListener('change', () => { $('#callTo').hidden = !$('#to').value.startsWith('dev:') })
$('#callTo').onclick = () => { const o = $('#to').selectedOptions[0]; if (o && o.value.startsWith('dev:')) send({ op: 'call', to: o.value.slice(4), peerName: o.textContent.replace(/ \(.*\)$/, '') }) }
$('#sayForm').onsubmit = (e) => { e.preventDefault(); const t = $('#text').value.trim(); if (!t) return; send({ op: 'say', to: $('#to').value, text: t }); $('#text').value = '' }

// double-click a member: talk to that one, or to all of their team
let popId = ''
window.openPop = function (id) {
  const b = S.pos[id] || S.who[id]; if (!b) return
  popId = id; S.direct[id] = b.name; toList()
  const p = $('#pop'), sel = p.querySelector('.pto')
  p.querySelector('.pt').textContent = b.name + (b.team ? ' · Team ' + b.team : '')
  sel.innerHTML = `<option value="dev:${esc(id)}">to ${esc(b.name)}</option>` + (b.team ? `<option value="team:${esc(b.team)}">to all of ${esc(b.team)}</option>` : '')
  p.querySelector('.pvideo').textContent = ''
  p.querySelector('.pcall').onclick = () => send({ op: 'call', to: id, peerName: b.name })   // [RT_CALL_V1] the Communicator
  p.hidden = false; popLog(); map.closePopup()
}
function popLog() {
  if (!popId || $('#pop').hidden) return
  const b = S.pos[popId] || S.who[popId] || {}
  const l = $('#pop .pl')
  l.innerHTML = S.msgs.filter(m => (m.to === 'dev:' + popId) || (m.from === popId && m.to.startsWith('dev:')) || (b.team && m.to === 'team:' + b.team)).slice(-30).map(msgHtml).join('')
  l.scrollTop = l.scrollHeight
}
$('#pop form').onsubmit = (e) => { e.preventDefault(); const i = $('#pop .ptext'), t = i.value.trim(); if (!t) return; send({ op: 'say', to: $('#pop .pto').value, text: t }); i.value = '' }
$('#pop .pclose').onclick = () => { $('#pop').hidden = true; popId = '' }
setInterval(redraw, 30000)    // the last known positions age on the page's own clock

// ------------------------------------------------------------ [RT_PUBLIC_HOME_V1] what a person can do (John, 2026-11-01:
// "find a friend, log a request for help, file a report with the EOC ... plus the big map")
const NEEDS = ['medical', 'trapped', 'evacuation', 'water', 'food', 'shelter', 'medication', 'power', 'other']   // DisasterComm's help page's
const mine = (() => { try { return JSON.parse(localStorage.getItem('rt.mine') || '{}') } catch (e) { return {} } })()
const keep = () => { try { localStorage.setItem('rt.mine', JSON.stringify(mine)) } catch (e) { } }
const rid0 = () => 'r-' + S.cfg.dev + '-' + Math.random().toString(36).slice(2, 7)
let myFix = null
window.rtFix = (lat, lng) => { myFix = { lat, lon: lng } }          // the phone's GPS (the native side calls it)
function sheet(name, title, html) {
  S.sheet = name; $('#sheet .sh-title').textContent = title; $('#sheet .sh-body').innerHTML = html; $('#sheet').hidden = false
}
$('#sheet .sh-close').onclick = () => { $('#sheet').hidden = true; S.sheet = '' }
$('#homeBtn').onclick = () => { $('#home').hidden = false; $('#sheet').hidden = true }
document.querySelectorAll('#home [data-go]').forEach(t => t.onclick = () => go(t.dataset.go))
function go(what) {
  if (what === 'map') { $('#home').hidden = true; return }
  if (what === 'needhelp' || what === 'safe') return helpForm(what === 'safe' ? 'safe' : 'open')
  if (what === 'find') return findForm()
  if (what === 'report') return reportForm()
  if (what === 'places') return placesList()
  if (what === 'library') return library()
  if (what === 'fema') return fema()
}
function whereHtml() {
  return `<label>Where (address, landmark)<input name="place"></label>
    <div class="row2"><button type="button" class="loc">Use my location</button> <span class="locsay"></span></div>`
}
function wireWhere(f) {
  const say = f.querySelector('.locsay'), b = f.querySelector('.loc'); let got = myFix
  if (got) say.textContent = 'your location: ' + got.lat.toFixed(4) + ', ' + got.lon.toFixed(4)
  b.onclick = () => {
    if (myFix) { got = myFix; say.textContent = 'your location: ' + got.lat.toFixed(4) + ', ' + got.lon.toFixed(4); return }
    if (!navigator.geolocation) { say.textContent = 'this device gives no location: say where above'; return }
    navigator.geolocation.getCurrentPosition(p => { got = { lat: p.coords.latitude, lon: p.coords.longitude }; say.textContent = 'your location: ' + got.lat.toFixed(4) + ', ' + got.lon.toFixed(4) },
      e => { say.textContent = 'no location (' + e.message + '): say where above' })
  }
  return () => { const l = { text: f.place.value.trim() }; if (got) { l.lat = got.lat; l.lon = got.lon } return l }
}
function helpForm(status) {
  const safe = status === 'safe'
  sheet('form', safe ? "I'm safe" : 'I need help', `<form class="hf">
    <label>Name (or who this is for)<input name="name" required value="${esc(S.cfg.name || '')}"></label>
    <label>How many people<input name="people" type="number" min="1" value="1"></label>
    ${safe ? '' : `<div>What is needed</div><div class="needs">${NEEDS.map(n => `<label><input type="checkbox" value="${n}"> ${n}</label>`).join('')}</div>`}
    ${whereHtml()}
    <label>How to reach you (phone, radio)<input name="contact"></label>
    <label>Anything else<textarea name="note" rows="3"></textarea></label>
    <button class="go">${safe ? 'Tell everyone I am safe' : 'Ask for help'}</button></form>
    <div class="meta">${safe ? 'Your family can find you by name under Find someone.' : 'Responders and the EOC see this at once, on their maps.'}</div>`)
  const f = document.querySelector('.hf'), loc = wireWhere(f)
  f.onsubmit = (e) => {
    e.preventDefault()
    const rid = rid0()
    const bag = { name: f.name.value.trim(), people: +f.people.value || 1, needs: [...f.querySelectorAll('.needs input:checked')].map(c => c.value),
                  status, location: loc(), contact: f.contact.value.trim(), note: f.note.value.trim() }
    send({ op: 'help', rid, bag }); mine[rid] = bag; keep()
    myRequests()
  }
}
function myRequests() {
  const rows = Object.keys(mine).map(rid => {
    const b = S.help[rid] || mine[rid], acts = actionsFor(rid)
    return `<div class="card"><b>${esc(b.name)}</b> <span class="s-${esc(b.status)}">${b.status === 'open' ? 'needs help' : esc(b.status)}</span>
      <div class="meta">${S.help[rid] ? 'published' : 'sending…'} · ${esc(where(b.location))}</div>
      ${acts.map(a => `<div class="meta">↳ ${esc(a.action)} by ${esc(a.unit)}${a.note ? ' — ' + esc(a.note) : ''}</div>`).join('')}
      ${b.status === 'open' ? `<button type="button" onclick="resolveMine('${esc(rid)}')">I don't need help any more</button>` : ''}</div>`
  }).join('')
  sheet('help', 'Your requests', rows || '<div class="meta">None yet.</div>')
}
window.resolveMine = (rid) => { const b = Object.assign({}, S.help[rid] || mine[rid], { status: 'resolved' }); mine[rid] = b; keep(); send({ op: 'help', rid, bag: b }) }
function where(l) { if (!l) return ''; return l.text || (typeof l.lat === 'number' ? l.lat.toFixed(4) + ', ' + l.lon.toFixed(4) : '') }
function actionsFor(rid) { return Object.entries(S.actions).filter(([k]) => k.startsWith(rid + '~')).map(([, a]) => a).sort((a, b) => (b.at || 0) - (a.at || 0)) }
function findForm() {
  sheet('find', 'Find someone', `<label>Name<input class="q" placeholder="type a name"></label><div class="found"></div>`)
  const q = document.querySelector('#sheet .q'); q.oninput = () => findDraw(q.value); q.focus(); findDraw('')
}
function findDraw(text) {
  const t = text.trim().toLowerCase(), box = document.querySelector('#sheet .found'); if (!box) return
  if (t.length < 2) { box.innerHTML = '<div class="meta">Type at least two letters of the name. Everyone who said they are safe, or asked for help, is here.</div>'; return }
  const hits = Object.entries(S.help).filter(([, b]) => (b.name || '').toLowerCase().includes(t)).sort((a, b) => (b[1].at || 0) - (a[1].at || 0))
  box.innerHTML = hits.length ? hits.map(([rid, b]) => `<div class="card"><b>${esc(b.name)}</b> <span class="s-${esc(b.status)}">${b.status === 'open' ? 'asked for help' : b.status === 'safe' ? 'is safe' : 'help no longer needed'}</span>
      <div class="meta">${esc(where(b.location))}${b.contact ? ' · ' + esc(b.contact) : ''} · ${b.at ? new Date(b.at * 1000).toLocaleString() : ''}</div>
      ${b.note ? `<div>${esc(b.note)}</div>` : ''}</div>`).join('') : '<div class="meta">No one by that name has checked in yet.</div>'
}
function reportForm() {
  sheet('form', 'Report to the EOC', `<form class="rf">
    <label>What is it<select name="category"><option>Damage</option><option>Hazard</option><option>Fire</option><option>Flooding</option><option>Power line down</option><option>Road blocked</option><option>Missing person</option><option>Other</option></select></label>
    <label>What do you see<textarea name="text" rows="4" required></textarea></label>
    ${whereHtml()}<button class="go">Send to the EOC</button></form>`)
  const f = document.querySelector('.rf'), loc = wireWhere(f)
  f.onsubmit = (e) => {
    e.preventDefault(); const l = loc()
    send({ op: 'report', category: f.category.value, text: f.text.value.trim() + (l.text ? ' (at ' + l.text + ')' : ''), lat: l.lat, lng: l.lon })
    sheet('form', 'Report to the EOC', '<div class="card">Sent. The EOC has it now.</div>')
  }
}
function placesList() {
  const by = (k) => Object.values(S.pos).filter(b => b && b.kind === k)
  const h = by('hospital').sort((a, b) => (b.beds || 0) - (a.beds || 0)).map(b => `<div class="card"><b>${esc(b.name)}</b><div class="meta">${b.beds ?? '?'} beds available</div></div>`).join('')
  const f = by('fire').map(b => `<div class="card"><b>${esc(b.name)}</b><div class="meta">${esc(b.severity || '')} — keep clear</div></div>`).join('')
  const p = by('power').map(b => `<div class="card"><b>${esc(b.name)}</b><div class="meta">${b.customers_out || '?'} customers without power</div></div>`).join('')
  sheet('places', 'Hospitals & services', `<h3>Hospitals</h3>${h || '<div class="meta">none known</div>'}<h3>Fires</h3>${f || '<div class="meta">none</div>'}<h3>Power outages</h3>${p || '<div class="meta">none</div>'}`)
}

// ------------------------------------------------------------ responders and the EOC: the requests and the reports
$('#reqBtn').onclick = () => requestsPanel()
function requestsPanel() {
  const open = Object.entries(S.help).filter(([, b]) => b.status === 'open').sort((a, b) => (a[1].at || 0) - (b[1].at || 0))
  const reqs = open.map(([rid, b]) => {
    const acts = actionsFor(rid)
    return `<div class="card"><b>${esc(b.name)}</b> (${b.people || 1}) ${(b.needs || []).map(n => `<span class="pill">${esc(n)}</span>`).join('')}
      <div class="meta">${esc(where(b.location))}${b.contact ? ' · ' + esc(b.contact) : ''} · ${b.at ? new Date(b.at * 1000).toLocaleTimeString() : ''}</div>
      ${b.note ? `<div>${esc(b.note)}</div>` : ''}
      ${acts.map(a => `<div class="meta">↳ ${esc(a.action)} by ${esc(a.unit)}${a.note ? ' — ' + esc(a.note) : ''}</div>`).join('')}
      <div><button type="button" onclick="act('${esc(rid)}','on the way')">On the way</button> <button type="button" onclick="act('${esc(rid)}','resolved')">Resolved</button>
      ${b.location && typeof b.location.lat === 'number' ? `<button type="button" onclick="showOnMap(${b.location.lat},${b.location.lon})">Map</button>` : ''}</div></div>`
  }).join('')
  const reps = S.role === 'eoc' ? Object.values(S.reports).sort((a, b) => (b.at || 0) - (a.at || 0)).map(r => `<div class="card"><b>${esc(r.category)}</b> from ${esc(r.name)}
      <div class="meta">${r.at ? new Date(r.at * 1000).toLocaleTimeString() : ''}${typeof r.lat === 'number' ? ` · <a href="#" onclick="showOnMap(${r.lat},${r.lng});return false">map</a>` : ''}</div><div>${esc(r.text)}</div></div>`).join('') : ''
  const safe = Object.values(S.help).filter(b => b.status === 'safe').length
  sheet('reqs', 'Requests', `<h3>Needing help (${open.length})</h3>${reqs || '<div class="meta">none open</div>'}<div class="meta">${safe} people have said they are safe (Find someone, on the public screen).</div>` +
    (S.role === 'eoc' ? `<h3>Reports to the EOC (${Object.keys(S.reports).length})</h3>${reps || '<div class="meta">none</div>'}` : ''))
}
window.act = (rid, action) => send({ op: 'helpAction', rid, action })
window.showOnMap = (lat, lng) => { $('#home').hidden = true; map.setView([lat, lng], 15) }
function refreshSheet(what) {
  if ($('#sheet').hidden) return
  if (S.sheet === 'reqs' && (what === 'help' || what === 'reports')) requestsPanel()
  else if (S.sheet === 'help' && what === 'help') myRequests()
  else if (S.sheet === 'find' && what === 'help') findDraw(document.querySelector('#sheet .q').value)
}
let reportsSeen = -1
function reportNotice() {
  const n = Object.keys(S.reports).length
  if (reportsSeen >= 0 && n > reportsSeen) {
    const last = Object.values(S.reports).sort((a, b) => (b.at || 0) - (a.at || 0))[0]
    notify({ id: 'rep:' + n, from: last.from, name: last.name, to: 'all', text: 'Report: ' + last.category + ' — ' + last.text, at: last.at })
  }
  reportsSeen = n
}

// ------------------------------------------------------------ [RT_SENSORS_V1] every node's sensors, as they file them
// [TEAM_HOST_V1] the team lead's phone hosts the team: its hotspot and the team's RAM on this phone
$('#hostBtn').onclick = () => { status("hosting the team: turn on this phone's hotspot if it is not on, then come back"); window.rtNative.teamHost() }
$('#sensBtn').onclick = () => { sheet('sens', 'Sensors', '<div class="meta">asking the nodes…</div>'); send({ op: 'sensorHosts' }) }
function sensorHosts(h) {
  const names = Object.keys(h).sort()
  sheet('sens', 'Sensors: the nodes', `<div class="meta">Every node that reports its System sensor. Pick one.</div><div class="sens">` +
    names.map(n => `<button type="button" onclick="send({op:'sensors',host:'${esc(n)}'})"><b>${esc(n)}</b> <span class="meta">${esc(h[n])}</span></button>`).join('') +
    `</div><button type="button" onclick="send({op:'sensorHosts'})">Refresh</button>`)
}
function sensorList(host, list) {
  list.sort((a, b) => a.name.localeCompare(b.name))
  sheet('sens', 'Sensors: ' + host, `<button type="button" onclick="send({op:'sensorHosts'})">◂ All nodes</button><div class="sens">` +
    list.map(x => `<button type="button" onclick="send({op:'sensor',id:'${esc(x.id)}',name:'${esc(x.name)}'})"><b>${esc(x.name.slice(host.length + 1))}</b> <span class="meta">${esc(x.type)}</span></button>`).join('') + '</div>')
  S.sensHost = host
}
function sensorShow(m) {
  const fx = m.fix ? `<div class="card">Position: ${m.fix.lat.toFixed(5)}, ${m.fix.lon.toFixed(5)} <button type="button" onclick="showOnMap(${m.fix.lat},${m.fix.lon})">Map</button></div>`
           : (/\.GPS\.Position$/.test(m.name) ? '<div class="card">No fix in this reading: no position.</div>' : '')
  sheet('sens', m.name, `<button type="button" onclick="send({op:'sensors',host:'${esc(S.sensHost || '')}'})">◂ ${esc(S.sensHost || 'back')}</button>
    <button type="button" onclick="send({op:'sensor',id:'${esc(m.id)}',name:'${esc(m.name)}'})">Refresh</button>
    <div class="meta">${m.updated ? 'updated ' + esc(m.updated) : ''}</div>${fx}<pre class="json">${esc(JSON.stringify(m.data, null, 2))}</pre>`)
}

// ------------------------------------------------------------ [RT_FIELD_V1] team actions (John, 2026-11-01: "Deploy Sensor
// Platform, Request Services, etc. Be creative, but realistic.")
const STATUSES = ['Available', 'En route', 'On scene', 'Need assistance', 'Out of service']
const PLATFORMS = ['Air quality (PM2.5 / gas)', 'Weather station', 'Water level / flood gauge', 'Gas detector (CH4 / CO / H2S)', 'Radiation monitor', 'Seismic / structural', 'Camera (fixed)', 'Mesh relay node']
const SERVICES = ['Medical evacuation', 'Ambulance', 'Water / food resupply', 'Fuel', 'Generator / power', 'Heavy equipment', 'Shelter space', 'Comms / radio relief', 'Relief crew', 'Hazmat team', 'Search dog team', 'Utility shut-off']
const HAZARDS = ['Gas leak', 'Downed power line', 'Flooding', 'Structural collapse', 'Fire', 'Road blocked', 'Hazardous materials', 'Unstable slope']
$('#actBtn').onclick = () => actionsPanel()
function fieldActs(fid) { return Object.entries(S.fieldActs).filter(([k]) => k.startsWith(fid + '~')).map(([, a]) => a).sort((a, b) => (b.at || 0) - (a.at || 0)) }
function fieldState(fid) { const a = fieldActs(fid)[0]; return a ? a.action : '' }
function here() { if (myFix) return { lat: myFix.lat, lng: myFix.lon }; const c = map.getCenter(); return { lat: c.lat, lng: c.lng, center: true } }
function opts(list) { return list.map(x => `<option>${esc(x)}</option>`).join('') }
function actionsPanel() {
  const mineIds = Object.keys(S.field).filter(f => S.field[f].by === S.cfg.dev).sort().reverse()
  sheet('field', 'Team actions', `
    <h3>My status</h3><div class="row">${STATUSES.map(x => `<button type="button" onclick="setStatus('${x}')">${x}</button>`).join(' ')}</div>
    <div class="meta">Now: ${esc(S.myStatus || 'Available')}</div>
    <h3>Deploy a sensor platform</h3><form class="ff" data-what="platform"><label>Platform<select name="type">${opts(PLATFORMS)}</select></label>
      <label>Note<input name="note" placeholder="mounted on the pole at the corner"></label><button class="go">Deploy here</button></form>
    <h3>Request services</h3><form class="ff" data-what="service"><label>Service<select name="type">${opts(SERVICES)}</select></label>
      <label>Priority<select name="priority"><option>Routine</option><option>Priority</option><option>Urgent</option></select></label>
      <label>Details<input name="note" placeholder="how many, how much, for whom"></label><button class="go">Send to the EOC</button></form>
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
}
window.setStatus = (st) => { S.myStatus = st; send({ op: 'status', status: st }); if (S.sheet === 'field') actionsPanel() }
function fieldCard(fid) {
  const b = S.field[fid], acts = fieldActs(fid)
  return `<div class="card"><b>${esc(b.type)}</b> <span class="pill">${esc(b.what)}</span>${b.priority ? ` <span class="pill">${esc(b.priority)}</span>` : ''}
    <div class="meta">${esc(b.name)}${b.team ? ' · ' + esc(b.team) : ''} · ${b.at ? new Date(b.at * 1000).toLocaleTimeString() : ''}</div>
    ${b.note ? `<div>${esc(b.note)}</div>` : ''}${acts.map(a => `<div class="meta">↳ ${esc(a.action)} by ${esc(a.unit)}${a.note ? ' — ' + esc(a.note) : ''}</div>`).join('')}
    ${S.role === 'eoc' && b.what === 'service' ? `<div><button type="button" onclick="fact('${esc(fid)}','acknowledged')">Acknowledge</button> <button type="button" onclick="fact('${esc(fid)}','dispatched')">Dispatched</button> <button type="button" onclick="fact('${esc(fid)}','done')">Done</button></div>` : ''}
    ${typeof b.lat === 'number' ? `<button type="button" onclick="showOnMap(${b.lat},${b.lng})">Map</button>` : ''}</div>`
}
window.fact = (id, action) => send({ op: 'fieldAction', id, action })
const _requestsPanel = requestsPanel
requestsPanel = function () {
  _requestsPanel()
  const svc = Object.keys(S.field).filter(f => S.field[f].what === 'service' && fieldState(f) !== 'done')
    .sort((a, b) => ({ Urgent: 0, Priority: 1, Routine: 2 }[S.field[a].priority] ?? 3) - ({ Urgent: 0, Priority: 1, Routine: 2 }[S.field[b].priority] ?? 3))
  const box = document.createElement('div')
  box.innerHTML = `<h3>Service requests from the teams (${svc.length})</h3>` + (svc.map(fieldCard).join('') || '<div class="meta">none open</div>')
  document.querySelector('#sheet .sh-body').prepend(box)
}
const _refresh = refreshSheet
refreshSheet = function (what) { _refresh(what); if (!$('#sheet').hidden && S.sheet === 'field' && what === 'field') actionsPanel(); if (!$('#sheet').hidden && S.sheet === 'reqs' && what === 'field') requestsPanel() }

// ------------------------------------------------------------ [RT_COMPLIANCE_V1] hosts with working environmental sensors
function envText(c) { return c.sensors.map(x => Object.entries(x.readings).map(([k, v]) => k + ' ' + v).join(', ')).join('; ') }
function compliance(hosts, asked) {
  S.compliance = hosts; const names = Object.keys(hosts)
  status(names.length ? 'OUT OF COMPLIANCE: ' + names.map(n => n + ' (' + envText(hosts[n]) + ')').join(' · ') + ` — ${asked} node(s) asked`
                      : `Compliance: ${asked} node(s) asked; none has a working environmental sensor`)
  for (const n of names) notify({ id: 'ooc:' + n, from: n, name: n, to: 'all', text: 'Out of compliance: ' + envText(hosts[n]), at: Date.now() / 1000 })
  if (names.length && !Object.keys(S.gps).length) send({ op: 'hosts' })
  redraw()
}
const _sensorHosts = sensorHosts
sensorHosts = function (h) {
  _sensorHosts(h)
  const bad = Object.keys(S.compliance)
  if (bad.length) { const d = document.createElement('div'); d.className = 'card ooc'
    d.innerHTML = '<b>Out of compliance</b>' + bad.map(n => `<div>${esc(n)}: ${esc(envText(S.compliance[n]))}</div>`).join(''); document.querySelector('#sheet .sh-body').prepend(d) }
}

function heroSummary() {
  if (!$('#heroSummary')) return
  const v = Object.values(S.pos), haz = Object.values(S.field).filter(b => b.what === 'hazard').length
  const beds = v.filter(b => b && b.kind === 'hospital' && (b.beds || 0) > 0).length
  const safe = Object.values(S.help).filter(b => b.status === 'safe').length
  $('#heroSummary').innerHTML = `${beds} hospitals with beds · ${haz} hazards marked<br>${safe} people checked in safe`
}

// ------------------------------------------------------------ the survival library: in the app, so it works with no network
const LIBRARY = [
  ['Emergencies: call first', ['Call 911 for life-threatening emergencies if any phone works. Text 911 where it is supported.',
    'If you cannot call, use "I need help" here: responders and the EOC see it at once, with where you are.',
    'Three of anything (whistle blasts, shouts, flashes, rock piles) is a call for help.']],
  ['Bleeding and first aid', ['Press firmly on the wound with a clean cloth and keep pressing. Do not lift it to look.',
    'For bleeding from an arm or leg that pressure will not stop: a tourniquet 2-3 inches above the wound, tight until the bleeding stops; note the time.',
    'Shock (pale, cold, clammy, confused): lay the person down, keep them warm, nothing to eat or drink, get help.',
    'Do not move someone who may have a neck or back injury unless they are in danger where they are.']],
  ['Drinking water', ['Plan on one gallon per person per day, for drinking and hygiene.',
    'Boil clear water for 1 minute (3 minutes above 6,500 feet), then let it cool.',
    'If you cannot boil: 8 drops (1/8 teaspoon) of plain unscented household bleach (6%) per gallon of clear water; stir and wait 30 minutes. Double it for cloudy water.',
    'Water from the water heater and ice cubes is usually safe. Water from flood areas is not.']],
  ['Carbon monoxide', ['Never run a generator, grill, camp stove or charcoal indoors, in a garage, or near a window or door.',
    'Keep a generator at least 20 feet from the house, exhaust pointed away.',
    'Headache, dizziness or nausea in several people at once: get everyone into fresh air now and call for help.']],
  ['Earthquake', ['During: Drop, Cover, Hold On. Stay away from windows. Do not run outside.',
    'After: expect aftershocks. If you smell gas, leave, and do not use flames, switches or phones inside.',
    'Check for injuries, then hazards: gas, water, power lines, broken glass. Wear shoes.']],
  ['Flooding', ["Turn around, don't drown: six inches of moving water can knock you down; a foot can float a car.",
    'Stay off bridges over fast water. Do not touch electrical equipment if you are wet or in water.',
    'Move to higher ground. Flood water carries sewage and chemicals: wash anything it touches.']],
  ['Power outage and food', ['Keep the fridge and freezer closed: food stays cold about 4 hours in a closed fridge, 48 hours in a full freezer (24 half full).',
    'Throw away perishable food that has been above 40 °F for 2 hours or more. When in doubt, throw it out.',
    'Unplug appliances to protect them from the surge when power comes back.']],
  ['Fire and smoke', ['Get out, stay out. Crawl low under smoke. Feel doors before opening; a hot door means use another way.',
    'If your clothes catch fire: stop, drop, and roll.',
    'For wildfire smoke, stay indoors with windows closed; an N95 mask helps outdoors.']],
  ['Shelter and warmth', ['Stay dry: wet clothing loses most of its warmth. Layer, cover your head, insulate yourself from the ground.',
    'Shivering, confusion, slurred speech: hypothermia. Get them warm and dry, warm drinks if they are awake, and get help.',
    'Shelters are under Hospitals & services as the EOC opens them.']],
]
function library() {
  sheet('library', 'Survival library', `<div class="lib"><div class="meta">Kept in this app: it works with no network at all.</div>` +
    LIBRARY.map(([t, items], i) => `<details${i === 0 ? ' open' : ''}><summary>${esc(t)}</summary><ul>${items.map(x => `<li>${esc(x)}</li>`).join('')}</ul></details>`).join('') +
    `<div class="src">From standard public guidance (Ready.gov, the American Red Cross, CDC). In an emergency, follow what responders tell you.</div></div>`)
}
function fema() {
  sheet('fema', 'Contact FEMA', `<div class="lib">
    <div class="card"><b>Apply for disaster assistance</b><ul>
      <li>Online: <b>DisasterAssistance.gov</b></li>
      <li>By phone: <b>1-800-621-3362</b></li>
      <li>On a phone: the free <b>FEMA app</b>, from your app store</li>
      <li>In person: a <b>Disaster Recovery Center</b> (DisasterAssistance.gov lists them once they open)</li></ul></div>
    <div class="card"><b>Have ready</b><ul><li>Your address at the time of the disaster, and where you can be reached now</li>
      <li>Insurance information</li><li>Bank account details, for direct deposit</li></ul></div>
    <div class="card"><b>No Internet here?</b> Use "Report to the EOC": the EOC can pass a FEMA request on for you.</div>
    <div class="src">From USA.gov, "How to apply for disaster assistance" (checked 2026-11-01).</div></div>`)
}


// ------------------------------------------------------------ [RT_CALL_V1] Call: the Communicator, on this device, joined to the
// person called; the called device rings and, answered, opens its Communicator joined back. Camera, microphone and
// speakers are chosen before it opens (the desktop asks the Communicator what this machine has).
function commsStart(peer, door, answer) {
  if (!window.rtNative || !window.rtNative.comms) { status('this device has no Communicator to open'); return }
  window.rtNative.comms(JSON.stringify({ peer, name: S.cfg.name, door: door || S.cfg.door, answer: !!answer }))
}
function ring(m) {
  const r = document.createElement('div'); r.className = 'ring'
  r.innerHTML = `<div><b>${esc(m.name)}</b> is calling</div><button class="yes">Answer</button><button class="no">Decline</button>`
  r.querySelector('.yes').onclick = () => { r.remove(); commsStart(m.name, m.door, true) }
  r.querySelector('.no').onclick = () => r.remove()
  document.body.appendChild(r); setTimeout(() => r.remove(), 60000)
  if (window.rtNative && window.rtNative.notice) try { window.rtNative.notice(m.name, 'is calling you') } catch (e) { }
}
function devicePicker(m) {
  const opt = (list, v, sel) => list.map(x => `<option value="${esc(x[v])}"${String(x[v]) === String(sel) ? ' selected' : ''}>${esc(x.label)}</option>`).join('')
  const sv = m.saved || {}
  sheet('call', (m.answer ? 'Answer ' : 'Call ') + m.peer, `<form class="cf">
    <label>Camera<select name="camera">${opt(m.cameras || [], 'value', sv.camera !== undefined && sv.camera !== null ? sv.camera : ((m.cameras || [])[0] || {}).value)}<option value=""${sv.camera === '' ? ' selected' : ''}>No video (voice only)</option></select></label>
    <label>Microphone<select name="mic">${opt(m.mics || [], 'n', sv.mic)}</select></label>
    <label>Speakers<select name="speaker">${opt(m.speakers || [], 'n', sv.speaker)}</select></label>
    <details><summary>Communicator servers</summary><label>Its memory<input name="ram" value="${esc(m.ram)}"></label><label>Its media server<input name="media" value="${esc(m.media)}"></label></details>
    <button class="go">${m.answer ? 'Answer' : 'Start the call'}</button></form><div class="meta">${esc(m.note || '')}</div>`)
  document.querySelector('.cf').onsubmit = (e) => {
    e.preventDefault(); const f = e.target
    window.rtNative.startComms(JSON.stringify({ peer: m.peer, answer: !!m.answer, name: m.name, camera: f.camera.value, mic: f.mic.value, speaker: f.speaker.value, ram: f.ram.value.trim(), media: f.media.value.trim() }))
    $('#sheet').hidden = true; S.sheet = ''
  }
}

// ------------------------------------------------------------ [RT_FACILITY_V1] the hospital's desk; [RT_TRANSPORT_V1] the ambulance
const BEDS = [['ed', 'Emergency'], ['icu', 'ICU'], ['medsurg', 'Med / surg'], ['peds', 'Pediatric'], ['burn', 'Burn'], ['or', 'Operating rooms']]
const DIVERSION = ['Open', 'ED diversion', 'Trauma diversion', 'Closed to ambulances']
const TRIAGE = [['Red', '#d32f2f', 'immediate'], ['Yellow', '#f9a825', 'delayed'], ['Green', '#2e9e5b', 'minor'], ['Black', '#333', 'expectant']]
const UNIT_STATUS = ['Available', 'Dispatched', 'En route to scene', 'On scene', 'Transporting', 'At hospital', 'Out of service']
function facId(n) { return String(n || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') }
function hospitals() {
  const names = new Set(Object.values(S.pos).filter(b => b && b.kind === 'hospital').map(b => b.name))
  for (const f of Object.values(S.fac)) names.add(f.name)
  return [...names].sort()
}
function facOf(name) { return S.fac[facId(name)] }
function bedsTotal(f) { return f ? BEDS.reduce((a, [k]) => a + (+((f.beds || {})[k]) || 0), 0) : null }
function trActs(id) { return Object.entries(S.trActs).filter(([k]) => k.startsWith(id + '~')).map(([, a]) => a).sort((a, b) => (b.at || 0) - (a.at || 0)) }
function trState(id) { const a = trActs(id)[0]; return a ? a.action : 'en route' }
function triPill(t) { const x = TRIAGE.find(z => z[0] === t); return x ? `<span class="tri" style="background:${x[1]}"></span>${esc(t)}` : esc(t || '') }

function facilityDesk() {
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
}
function inbound() {
  const mine = S.role === 'hospital' ? (t) => facId(t.to) === facId(S.team) : () => true
  const ids = Object.keys(S.tr).filter(id => mine(S.tr[id]) && trState(id) !== 'arrived').sort((a, b) => (S.tr[a].at + (S.tr[a].eta || 0) * 60) - (S.tr[b].at + (S.tr[b].eta || 0) * 60))
  const card = (id) => { const t = S.tr[id], acts = trActs(id), due = t.at + (t.eta || 0) * 60
    return `<div class="card"><b>${esc(t.unit)}</b> → ${esc(t.to)} · ${t.patients || 1} patient(s) · ${triPill(t.triage)}
      <div>${esc(t.complaint || '')}${t.age ? ' · ' + esc(t.age) : ''}</div>
      <div class="meta">ETA ${new Date(due * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · ${esc(trState(id))}</div>
      ${acts.map(a => `<div class="meta">↳ ${esc(a.action)} by ${esc(a.unit)}</div>`).join('')}
      ${S.role === 'hospital' ? `<button type="button" onclick="tact('${esc(id)}','acknowledged')">Acknowledge</button> <button type="button" onclick="tact('${esc(id)}','arrived')">Arrived</button>` : ''}</div>` }
  sheet('tr', S.role === 'hospital' ? 'Inbound to ' + S.team : 'Patient transports', ids.map(card).join('') || '<div class="meta">nothing inbound</div>')
}
window.tact = (id, action) => send({ op: 'transportAction', id, action })
let trSeen = null
function trNotice() {
  if (S.role !== 'hospital') return
  const ids = Object.keys(S.tr).filter(id => facId(S.tr[id].to) === facId(S.team))
  if (trSeen) for (const id of ids) if (!trSeen.has(id)) { const t = S.tr[id]
    notify({ id: 'tr:' + id, from: t.by, name: t.unit, to: 'all', text: `inbound: ${t.patients || 1} patient(s), ${t.triage || ''}, ETA ${t.eta || '?'} min — ${t.complaint || ''}`, at: t.at }) }
  trSeen = new Set(ids)
}
function unitPanel() {
  const hs = hospitals().map(n => { const f = facOf(n), tot = bedsTotal(f)
    return `<option value="${esc(n)}">${esc(n)}${f ? ' — ' + (f.diversion && f.diversion !== 'Open' ? f.diversion.toUpperCase() : tot + ' beds') : ''}</option>` }).join('')
  const jobs = Object.keys(S.field).filter(fid => { const b = S.field[fid]; return b.what === 'service' && /ambulance|medical evac/i.test(b.type) && fieldState(fid) !== 'done' })
  const meds = Object.entries(S.help).filter(([, b]) => b.status === 'open' && (b.needs || []).includes('medical')).slice(0, 12)
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
    <h3>Assignments</h3>${jobs.map(fieldCard).join('') || '<div class="meta">no ambulance requests open</div>'}
    ${meds.map(([rid, b]) => `<div class="card"><b>${esc(b.name)}</b> needs medical help · ${esc(where(b.location))}
      <div><button type="button" onclick="act('${esc(rid)}','${esc(S.team)} responding')">Take it</button>
      ${b.location && typeof b.location.lat === 'number' ? `<button type="button" onclick="showOnMap(${b.location.lat},${b.location.lon})">Map</button>` : ''}</div></div>`).join('')}`)
  document.querySelector('.tf').onsubmit = (e) => {
    e.preventDefault(); const x = e.target
    send({ op: 'transport', bag: { to: x.to.value, patients: +x.patients.value || 1, triage: x.triage.value, eta: +x.eta.value || 10, complaint: x.complaint.value.trim(), age: x.age.value.trim() } })
    window.setStatus('Transporting')
  }
}
function hospitalsPanel() {
  sheet('fac', 'Hospitals', hospitals().map(n => { const f = facOf(n)
    if (!f) return `<div class="card"><b>${esc(n)}</b><div class="meta">no status published</div></div>`
    return `<div class="card"><b>${esc(n)}</b> ${f.diversion && f.diversion !== 'Open' ? `<span class="divert">${esc(f.diversion)}</span>` : ''}
      <div class="meta">${BEDS.filter(([k]) => (f.beds || {})[k] != null).map(([k, l]) => l + ' ' + f.beds[k]).join(' · ')}</div>
      <div class="meta">power ${esc(f.power || '?')} · water ${esc(f.water || '?')} · oxygen ${esc(f.oxygen || '?')} · staff ${esc(f.staff || '?')}</div>
      ${f.needs ? `<div>Needs: ${esc(f.needs)}</div>` : ''}</div>` }).join(''))
}
// the header's two role buttons say what they are for these roles
const _connected = connected
connected = function (m) {
  _connected(m)
  if (S.role === 'hospital') {
    $('#actBtn').textContent = 'Status'; $('#actBtn').onclick = facilityDesk; $('#reqBtn').textContent = 'Inbound'; $('#reqBtn').onclick = inbound
    S.teamsOn = new Set(); for (const k in KINDS) S.on[k] = ['hospital', 'ambulance', 'hazard'].includes(k); layersPanel(); redraw(); facilityDesk()
  } else if (S.role === 'ambulance') {
    $('#actBtn').textContent = 'Unit'; $('#actBtn').onclick = unitPanel; $('#reqBtn').textContent = 'Hospitals'; $('#reqBtn').onclick = hospitalsPanel
    S.teamsOn = new Set(); for (const k in KINDS) S.on[k] = ['hospital', 'ambulance', 'help', 'hazard', 'service'].includes(k); layersPanel(); redraw()
  } else {
    $('#actBtn').textContent = 'Actions'; $('#actBtn').onclick = () => actionsPanel(); $('#reqBtn').textContent = 'Requests'; $('#reqBtn').onclick = () => requestsPanel()
  }
}
const _refresh2 = refreshSheet
refreshSheet = function (what) {
  _refresh2(what)
  if ($('#sheet').hidden) return
  if (what === 'tr' && S.sheet === 'tr') inbound()
  if (what === 'fac' && S.sheet === 'fac' && S.role !== 'hospital') hospitalsPanel()
}
// the hospital's own status in its map popup and in the public's list
const _popupHtml = popupHtml
popupHtml = function (id, b, lkp) {
  let h = _popupHtml(id, b, lkp)
  if (b.kind === 'hospital') { const f = facOf(b.name)
    if (f) h += `<br>${f.diversion && f.diversion !== 'Open' ? `<b class="divert">${esc(f.diversion)}</b><br>` : ''}${BEDS.filter(([k]) => (f.beds || {})[k] != null).map(([k, l]) => l + ' ' + f.beds[k]).join(' · ')}` }
  return h
}

// [RT_TRANSPORT_V1] the EOC's Requests: patient transports under way and the hospitals not open to ambulances
const _requestsPanel2 = requestsPanel
requestsPanel = function () {
  _requestsPanel2()
  if (S.role !== 'eoc') return
  const ids = Object.keys(S.tr).filter(id => trState(id) !== 'arrived')
  const div = hospitals().map(n => facOf(n)).filter(f => f && f.diversion && f.diversion !== 'Open')
  const box = document.createElement('div')
  box.innerHTML = `<h3>Patient transports under way (${ids.length})</h3>` + (ids.map(id => { const t = S.tr[id]
      return `<div class="card"><b>${esc(t.unit)}</b> → ${esc(t.to)} · ${t.patients || 1} · ${triPill(t.triage)} · ${esc(t.complaint || '')}<div class="meta">ETA ${t.eta || '?'} min from ${new Date(t.at * 1000).toLocaleTimeString()} · ${esc(trState(id))}</div></div>` }).join('') || '<div class="meta">none</div>')
    + (div.length ? `<h3>Hospitals not open to ambulances</h3>` + div.map(f => `<div class="card"><b>${esc(f.name)}</b> <span class="divert">${esc(f.diversion)}</span>${f.needs ? `<div>Needs: ${esc(f.needs)}</div>` : ''}</div>`).join('') : '')
  document.querySelector('#sheet .sh-body').prepend(box)
}
const _refresh3 = refreshSheet
refreshSheet = function (what) { _refresh3(what); if (!$('#sheet').hidden && S.sheet === 'reqs' && (what === 'tr' || what === 'fac')) requestsPanel() }
// an ambulance's status buttons redraw its own panel
const _setStatus = window.setStatus
window.setStatus = (st) => { _setStatus(st); if (S.sheet === 'unit') unitPanel() }


// ------------------------------------------------------------ [RT_FOLLOW_ME_V1] the map centred on you, north up
// The map follows this device's own position (its GPS, or the address typed on a device without one); everyone else is
// where they are around you. Moving the map yourself stops following; the ⦿ button centres on you and follows again.
S.follow = true
let meBtn = null
function followMe() {
  if (!map || !myFix) return
  if (!meBtn) {
    meBtn = document.createElement('button'); meBtn.id = 'meBtn'; meBtn.type = 'button'; meBtn.title = 'Centre on me'; meBtn.textContent = '⦿'
    meBtn.onclick = () => { S.follow = true; meBtn.classList.add('on'); map.setView([myFix.lat, myFix.lon], Math.max(map.getZoom(), 14)) }
    document.getElementById('main').appendChild(meBtn)
    map.on('dragstart', () => { S.follow = false; meBtn.classList.remove('on') })
    meBtn.classList.add('on')
    map.setView([myFix.lat, myFix.lon], Math.max(map.getZoom(), 14)); return
  }
  if (S.follow) map.panTo([myFix.lat, myFix.lon], { animate: true })
}

// ------------------------------------------------------------ [RT_NO_GPS_V1] a device with no GPS: its street address
// The native side says so (ev nogps); the user types the address once, the device looks it up (Android's Geocoder) and
// that is this device's position, kept on the device and published like a fix ("src":"address"). A "lat, lon" typed is
// taken as it is.
function noGps(m) {
  S.noGps = true
  if (m.saved && typeof m.saved.lat === 'number') { S.addr = m.saved; addressFix(); return }
  addressSheet(m.why || 'This device has no GPS.')
}
function addressSheet(why) {
  sheet('addr', 'Where are you?', `<form class="af mf"><div>${esc(why)} Enter the street address where you are; it is your position on everyone's map.</div>
    <label>Street address<input name="a" placeholder="17631 Vashon Hwy SW, Vashon, WA" value="${esc((S.addr || {}).label || '')}" required></label>
    <button class="go">Use this address</button><div class="meta as"></div></form>`)
  document.querySelector('.af').onsubmit = (e) => {
    e.preventDefault(); const a = e.target.a.value.trim(), ll = a.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/)
    if (ll) { geocoded({ lat: +ll[1], lng: +ll[2], label: a }); return }
    document.querySelector('.af .as').textContent = 'looking it up…'
    if (window.rtNative && window.rtNative.geocode) window.rtNative.geocode(a)
    else document.querySelector('.af .as').textContent = 'this device cannot look up addresses: type "latitude, longitude"'
  }
}
function geocoded(m) {
  if (m.error) { const x = document.querySelector('.af .as'); if (x) x.textContent = m.error; return }
  S.addr = { lat: m.lat, lng: m.lng, label: m.label }
  if (window.rtNative && window.rtNative.saveAddress) try { window.rtNative.saveAddress(JSON.stringify(S.addr)) } catch (e) { }
  $('#sheet').hidden = true; S.sheet = ''
  addressFix()
}
function addressFix() { if (S.noGps && S.addr && S.role) send({ op: 'fix', lat: S.addr.lat, lng: S.addr.lng, acc: 0, src: 'address' }) }

// ------------------------------------------------------------ [RT_MISSION_V1] see, update and start the mission
const MIS_FIELDS = [['name', 'Name'], ['event', 'Event / incident'], ['opPeriod', 'Operational period'], ['start', 'Start'], ['end', 'End'],
  ['commander', 'Incident commander'], ['objectives', 'Objectives'], ['area', 'Area'], ['notes', 'Notes']]
function missionPanel(edit, fresh) {
  const m = S.mission || {}, b = fresh ? {} : (m.bag || {})
  S.misEditing = !!edit
  if (!edit) {
    const all = Object.entries(m.all || {}).sort((x, y) => (+(y[1].at || 0)) - (+(x[1].at || 0)))
    sheet('mis', 'Mission', `<div class="mis"><dl>${MIS_FIELDS.filter(([k]) => b[k] !== undefined && b[k] !== '').map(([k, l]) => `<dt>${l}</dt><dd>${esc(b[k])}</dd>`).join('') || '<dt>Mission</dt><dd>' + esc(m.id || 'none yet') + '</dd>'}
      ${b.updated ? `<dt>Updated</dt><dd>${new Date(b.updated * 1000).toLocaleString()} by ${esc(b.by || '')}</dd>` : ''}</dl>
      ${S.role !== 'public' ? '<div class="row"><button type="button" onclick="missionPanel(true)">Update this mission</button> <button type="button" onclick="missionPanel(true,true)">Start a new mission</button></div>' : ''}
      ${all.length > 1 ? '<h3>Earlier missions</h3>' + all.filter(([id]) => id !== m.id).map(([id, x]) => `<div class="meta">${esc(x.name || id)}${x.at ? ' · started ' + new Date(x.at * 1000).toLocaleString() : ''}</div>`).join('') : ''}</div>`)
    return
  }
  sheet('mis', fresh ? 'Start a new mission' : 'Update the mission', `<form class="mf">${MIS_FIELDS.map(([k, l]) => `<label>${l}${k === 'objectives' || k === 'notes'
      ? `<textarea name="${k}" rows="3">${esc(b[k] || '')}</textarea>` : `<input name="${k}" value="${esc(b[k] ?? '')}"${k === 'name' ? ' required' : ''}>`}</label>`).join('')}
    ${fresh ? '<div class="meta">A new mission is the mission on every device from now on: positions, chat and requests start again in it.</div>' : ''}
    <div class="row"><button class="go">${fresh ? 'Start it' : 'Save'}</button> <button type="button" onclick="missionPanel()">Cancel</button></div></form>`)
  document.querySelector('.mf').onsubmit = (e) => {
    e.preventDefault(); const bag = {}
    for (const [k] of MIS_FIELDS) { const v = e.target[k].value.trim(); if (v !== '') bag[k] = v }
    for (const k of ['lat', 'lng', 'sim']) if (!fresh && b[k] !== undefined) bag[k] = b[k]
    send({ op: 'missionSet', id: fresh ? '' : (m.id || ''), bag }); S.misEditing = false; missionPanel()
  }
}
window.missionPanel = missionPanel
$('#misBtn').onclick = () => missionPanel()
const _connected4 = connected
connected = function (m) { _connected4(m); $('#misBtn').hidden = S.role === 'public'; addressFix() }


// ------------------------------------------------------------ [RT_RADIO_LOG_IN_THE_APP_V1] RangerTrak's radio log, here
// What the scribes and field phones log over the radio (the radio log app, /rt/log.html), for this mission and its
// operational period, newest first, as it is typed. Read only: the entries and their ICS-309 numbers are the posts'.
const RL_STATUS = { urgent: 'Urgent', 'need-rest-food': 'Needs rest/food', ok: 'OK', info: 'Info' }
function radioLog() {
  const m = S.rlog || {}, rows = Object.values(m.entries || {}).sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')))
  const no = (n) => n ? [n.station, n.seq].filter(Boolean).join('-') : ''
  const loc = (l) => !l ? '' : typeof l === 'string' ? l : l.text || (typeof l.lat === 'number' ? l.lat.toFixed(4) + ', ' + (l.lng ?? l.lon).toFixed(4) : '')
  sheet('rlog', 'Radio log — op period ' + esc(m.op || '1'), (rows.length ? `<table class="rl"><thead><tr><th>Time</th><th>No.</th><th>Call sign</th><th>Status</th><th>Location</th><th>Notes</th><th>Post</th></tr></thead><tbody>` +
    rows.map(r => `<tr${r.status === 'urgent' ? ' class="urg"' : ''}><td>${r.date ? new Date(r.date).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}</td><td>${esc(no(r.msgNo))}</td><td><b>${esc(r.callsign)}</b></td>
      <td>${esc(RL_STATUS[r.status] || r.status || '')}</td><td>${esc(loc(r.location))}</td><td>${esc(r.notes || '')}</td><td>${esc(r.by || '')}</td></tr>`).join('') + '</tbody></table>'
    : '<div class="meta">No radio log entries in this operational period yet. Scribes log them in the Radio log app (/rt/ on the node).</div>'))
}
$('#logBtn').onclick = radioLog
const _connected5 = connected
connected = function (m) { _connected5(m); $('#logBtn').hidden = S.role !== 'eoc' && S.role !== 'team' }


// ------------------------------------------------------------ [PAGE_ERRORS_ARE_SAID_V1] a script error on the page is said
// on the status line (and the connect screen), not lost in a phone's web view console
window.addEventListener('error', (e) => { try { const t = 'page error: ' + e.message + ' (' + String(e.filename || '').split('/').pop() + ':' + e.lineno + ')'
  status(t); if (!$('#connect').hidden) $('#cstatus').textContent = t } catch (x) { } })
window.addEventListener('unhandledrejection', (e) => { try { status('page error: ' + (e.reason && e.reason.message || e.reason)) } catch (x) { } })

// ------------------------------------------------------------ [BACK_CLOSES_FIRST_V1] the phone's Back: closes what is open
// (a sheet, the person pop-up, the chat, Layers), then goes Home on the public view; answers false when there is
// nothing to close, and the app goes to the background
window.rtBack = function () {
  const open = (sel) => { const e = $(sel); return e && !e.hidden }
  if (document.querySelector('.ring')) { document.querySelector('.ring').remove(); return true }
  if (open('#sheet')) { $('#sheet').hidden = true; S.sheet = ''; S.misEditing = false; return true }
  if (open('#pop')) { $('#pop').hidden = true; return true }
  if (open('#layers')) { $('#layers').hidden = true; return true }
  if (open('#chat')) { $('#chat').hidden = true; return true }
  if (S.role === 'public' && !open('#home') && !$('#main').hidden) { $('#home').hidden = false; return true }
  return false
}
