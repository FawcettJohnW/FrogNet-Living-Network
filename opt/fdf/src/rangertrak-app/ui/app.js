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
  ambulance: { label: 'Ambulances', shape: 'sq',  size: 18, color: '#e23b3b', views: ['eoc', 'team'], ch: '+' },
  boat:      { label: 'Boats',      shape: 'sq',  size: 18, color: '#1565c0', views: ['eoc', 'team'], ch: 'B' },
  victim:    { label: 'Victims',    shape: '',    size: 12, color: '#f2c200', views: ['eoc', 'team'], ch: '' },
  hospital:  { label: 'Hospitals',  shape: 'sq',  size: 20, color: '#ffffff', views: ['eoc', 'team', 'public'], ch: 'H', fg: '#c62828' },
  fire:      { label: 'Fire',       shape: 'dia', size: 18, color: '#ff6d00', views: ['eoc', 'team', 'public'], ch: 'F' },
  power:     { label: 'Power',      shape: 'dia', size: 18, color: '#7e57c2', views: ['eoc', 'public'], ch: 'P' },
  host:      { label: 'Hosts',      shape: 'sq',  size: 16, color: '#263238', views: [], ch: 'N' },   // off by default: an option
}
const TEAM_COLOR = { alpha: '#d62728', bravo: '#1f77b4', charlie: '#2ca02c', delta: '#ff7f0e' }
const LKP_MIN = 10            // a last known position is drawn this many minutes (the app's own choice)

const S = { gps: {}, cfg: {}, role: '', team: '', who: {}, pos: {}, seen: {}, lkp: {}, markers: {}, links: {}, on: {}, msgs: [], tab: 'all', direct: {} }
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
  $('#chatBtn').hidden = S.role === 'public'
  if (S.role === 'eoc') $('#chat').hidden = false
  tabs(); toList(); redraw()
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
  S.who = who; S.pos = pos
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
  if (S.on.host) for (const id in S.gps) { want[id] = 1; place(id, S.gps[id], null) }      // real nodes, from their GPS sensors
  for (const id in S.markers) if (!want[id]) { map.removeLayer(S.markers[id]); delete S.markers[id] }
  // hosts: who is connected through which node
  for (const id in S.links) { map.removeLayer(S.links[id]); delete S.links[id] }
  if (S.on.host) for (const id in S.pos) {
    const b = S.pos[id], h = b && b.via && (S.pos[b.via] || Object.values(S.gps).find(g => g.address === b.via))
    if (h && want[id] && typeof h.lat === 'number') S.links[id] = L.polyline([[b.lat, b.lng], [h.lat, h.lng]], { color: '#90a4ae', weight: 1, opacity: .6, dashArray: '3 4' }).addTo(map)
  }
}
function visibleInView(b) { return S.role !== 'public' || ['hospital', 'fire', 'power'].includes(b.kind) }
function layersPanel() {
  const p = $('#layers'); p.innerHTML = ''
  const kinds = Object.keys(KINDS).filter(k => S.role !== 'public' || ['hospital', 'fire', 'power'].includes(k))
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
  const opts = [['all', 'Everyone']].concat(teamsHere().map(x => ['team:' + x, 'Team ' + x])).concat(Object.entries(S.direct).map(([id, n]) => ['dev:' + id, n]))
  for (const [v, n] of opts) { const o = document.createElement('option'); o.value = v; o.textContent = n; s.appendChild(o) }
  if (cur) s.value = cur
}
function inTab(m) { return S.tab === 'all' ? m.to === 'all' : S.tab === 'dev' ? m.to.startsWith('dev:') : m.to === S.tab }
function msgHtml(m) {
  const mine = m.from === S.cfg.dev, at = m.at ? new Date(m.at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''
  const where = m.to === 'all' ? '' : m.to.startsWith('team:') ? ' → ' + m.to.slice(5) : mine ? ' → ' + (S.direct[m.to.slice(4)] || 'direct') : ' → you'
  return `<div class="m${mine ? ' mine' : ''}"><div class="h">${esc(m.name)}${esc(where)} · ${at}</div>${esc(m.text)}</div>`
}
function showLog() { const l = $('#log'); l.innerHTML = S.msgs.filter(inTab).map(msgHtml).join(''); l.scrollTop = l.scrollHeight }
function message(m) {
  if (S.seen[m.id]) return; S.seen[m.id] = 1
  S.msgs.push(m); S.msgs.sort((a, b) => (a.at || 0) - (b.at || 0))
  if (m.to.startsWith('dev:') && m.from !== S.cfg.dev && !S.direct[m.from]) { S.direct[m.from] = m.name; toList() }
  showLog(); popLog()
}
$('#chatBtn').onclick = () => { $('#chat').hidden = !$('#chat').hidden }
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
