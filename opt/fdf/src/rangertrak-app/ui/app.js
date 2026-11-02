// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// app.js -- [RT_APP_UI_V1] the screens of the RangerTrak app. Everything it shows comes from the core's lines
// (rtDeliver); everything it does is a line to the core (rtNative.send). Nothing here talks to the network.
'use strict'
const $ = (s) => document.querySelector(s)
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const send = (o) => window.rtNative.send(JSON.stringify(o))

// kinds: how each is drawn, and which views show it by default
const KINDS = {
  team:      { label: 'Teams',      shape: '',    size: 18, color: '#888', views: ['eoc', 'team'] },
  eoc:       { label: 'EOC',        shape: 'sq',  size: 20, color: '#6b4fd8', views: ['eoc', 'team'], ch: 'E' },
  ambulance: { label: 'Ambulances', shape: 'sq',  size: 18, color: '#e23b3b', views: ['eoc'], ch: '+' },
  boat:      { label: 'Boats',      shape: 'sq',  size: 18, color: '#1565c0', views: ['eoc'], ch: 'B' },
  victim:    { label: 'Victims',    shape: '',    size: 12, color: '#f2c200', views: ['eoc'], ch: '' },
  hospital:  { label: 'Hospitals',  shape: 'sq',  size: 20, color: '#ffffff', views: ['eoc', 'public'], ch: 'H', fg: '#c62828' },
  fire:      { label: 'Fire',       shape: 'dia', size: 18, color: '#ff6d00', views: ['eoc', 'public'], ch: 'F' },
  power:     { label: 'Power',      shape: 'dia', size: 18, color: '#7e57c2', views: ['eoc', 'public'], ch: 'P' },
  help:      { label: 'Help requests', shape: '', size: 16, color: '#ff1744', views: ['eoc', 'public'], ch: '!' },
  host:      { label: 'Hosts',      shape: 'sq',  size: 16, color: '#263238', views: ['eoc'], ch: 'N' },   // a layer: on for the EOC
}
const TEAM_COLOR = { alpha: '#d62728', bravo: '#1f77b4', charlie: '#2ca02c', delta: '#ff7f0e' }
const LKP_MIN = 10            // a last known position is drawn this many minutes (the app's own choice)

const S = { help: {}, actions: {}, reports: {}, sheet: '', gps: {}, cfg: {}, role: '', team: '', who: {}, pos: {}, seen: {}, lkp: {}, markers: {}, links: {}, on: {}, msgs: [], tab: 'all', direct: {} }
let map

// ------------------------------------------------------------ start: the native side calls rtInit once the page loads
window.rtInit = function (cfg) {
  S.cfg = cfg || {}
  const f = $('#connect')
  f.door.value = S.cfg.door || ''; f.name.value = S.cfg.name || ''
  if (S.cfg.role) f.querySelector(`[name=role][value=${S.cfg.role}]`).checked = true
  if (S.cfg.team) f.team.value = S.cfg.team
  roleFields()
  setTimeout(() => { $('#splash').classList.add('gone'); f.hidden = false }, 1800)
}
function roleFields() {
  const r = $('#connect').querySelector('[name=role]:checked').value
  document.querySelector('.for-team').hidden = r !== 'team'; document.querySelector('.for-eoc').hidden = r !== 'eoc'
}
document.querySelectorAll('[name=role]').forEach(e => e.onchange = roleFields)
$('#connect').onsubmit = (e) => {
  e.preventDefault()
  const f = e.target, role = f.querySelector('[name=role]:checked').value
  $('#cstatus').textContent = 'connecting…'
  send({ op: 'connect', door: f.door.value.trim(), dev: S.cfg.dev, name: f.name.value.trim(), role, team: f.team.value,
         user: f.user.value.trim(), password: f.password.value })
}

// ------------------------------------------------------------ the core's lines
window.rtDeliver = function (line) {
  const m = typeof line === 'string' ? JSON.parse(line) : line
  if (m.ev === 'status') status(m.text)
  else if (m.ev === 'error') { status('⚠ ' + m.text); if (!$('#connect').hidden) $('#cstatus').textContent = m.text }
  else if (m.ev === 'connected') connected(m)
  else if (m.ev === 'mission') $('#mission').textContent = m.name || m.id
  else if (m.ev === 'world') world(m.who || {}, m.pos || {})
  else if (m.ev === 'msg') message(m)
  else if (m.ev === 'help') { S.help = m.requests || {}; S.actions = m.actions || {}; redraw(); refreshSheet('help') }
  else if (m.ev === 'reports') { S.reports = m.list || {}; refreshSheet('reports'); if (S.role === 'eoc') reportNotice() }
  else if (m.ev === 'sensorHosts') sensorHosts(m.hosts || {})
  else if (m.ev === 'sensors') sensorList(m.host, m.list || [])
  else if (m.ev === 'sensor') sensorShow(m)
  else if (m.ev === 'myfix') myFix = { lat: m.lat, lon: m.lng }
  else if (m.ev === 'hosts') { S.gps = {}; for (const h of m.list || []) S.gps['gps:' + h.name] = Object.assign({ kind: 'host' }, h); redraw() }
}
function status(t) { $('#status').textContent = t; $('#dot').classList.toggle('up', !/FAILING|cannot|⚠/.test(t)) }

function connected(m) {
  S.role = m.role; S.team = m.team || ''
  $('#connect').hidden = true; $('#main').hidden = false
  $('#who').textContent = S.cfg.name + ' · ' + (S.role === 'eoc' ? 'EOC' : S.role === 'team' ? 'Team ' + S.team : 'Public')
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
  layersPanel(); legend()
  if (S.on.host) send({ op: 'hosts' })                 // the nodes' own GPS sensors, read once now
  $('#chatBtn').hidden = S.role === 'public'
  $('#homeBtn').hidden = S.role !== 'public'; $('#reqBtn').hidden = S.role === 'public'; $('#sensBtn').hidden = S.role === 'public'
  $('#home').hidden = S.role !== 'public'          // [RT_PUBLIC_HOME_V1] the public opens on what a person can do, not the map
  if (S.role === 'eoc') $('#chat').hidden = false
  // [TEAM_VIEW_IS_THE_TEAM_V1] (John, 2026-11-01) a team member opens on the teams' positions and the team's own chat;
  // everything else is a layer to turn on, and Everyone is a tab away
  if (S.role === 'team' && S.team) { S.tab = 'team:' + S.team }
  tabs(); toList(); if (S.role === 'team' && S.team) $('#to').value = 'team:' + S.team
  redraw()
}

// ------------------------------------------------------------ the map
function colorOf(b) { return b.kind === 'team' || (b.kind === 'eoc' && false) ? (TEAM_COLOR[String(b.team || '').toLowerCase()] || b.color || '#888') : (KINDS[b.kind] || KINDS.team).color }
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
  const before = Object.keys(S.who).join(); S.who = who; S.pos = pos
  if (Object.keys(who).join() !== before && map) toList()
  redraw()
}
function shown(b) { return S.on[b.kind] !== undefined ? S.on[b.kind] : S.on.team }
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
  if (S.on.host) for (const id in S.gps) { want[id] = 1; place(id, S.gps[id], null) }
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
function visibleInView(b) { return S.role !== 'public' || ['hospital', 'fire', 'power', 'help', 'shelter'].includes(b.kind) }
function layersPanel() {
  const p = $('#layers'); p.innerHTML = ''
  const kinds = Object.keys(KINDS).filter(k => S.role !== 'public' || ['hospital', 'fire', 'power', 'help'].includes(k))
  for (const k of kinds.concat(S.role !== 'public' ? ['lkp'] : [])) {
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
$('#sayForm').onsubmit = (e) => { e.preventDefault(); const t = $('#text').value.trim(); if (!t) return; send({ op: 'say', to: $('#to').value, text: t }); $('#text').value = '' }

// double-click a member: talk to that one, or to all of their team
let popId = ''
window.openPop = function (id) {
  const b = S.pos[id] || S.who[id]; if (!b) return
  popId = id; S.direct[id] = b.name; toList()
  const p = $('#pop'), sel = p.querySelector('.pto')
  p.querySelector('.pt').textContent = b.name + (b.team ? ' · Team ' + b.team : '')
  sel.innerHTML = `<option value="dev:${esc(id)}">to ${esc(b.name)}</option>` + (b.team ? `<option value="team:${esc(b.team)}">to all of ${esc(b.team)}</option>` : '')
  p.querySelector('.pvideo').textContent = 'Video: not in this build (it comes with the media library).'
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
document.querySelectorAll('#home .tile').forEach(t => t.onclick = () => go(t.dataset.go))
function go(what) {
  if (what === 'map') { $('#home').hidden = true; return }
  if (what === 'needhelp' || what === 'safe') return helpForm(what === 'safe' ? 'safe' : 'open')
  if (what === 'find') return findForm()
  if (what === 'report') return reportForm()
  if (what === 'places') return placesList()
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
