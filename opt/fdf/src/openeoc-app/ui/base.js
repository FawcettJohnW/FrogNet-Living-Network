// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// base.js -- [RT_APP_UI_V1] [RT_LAYERS_V1] the base of every OpenEOC app: the connect screen, the map, the chat,
// the mission, the radio log, sensors, Call, follow-me, the no-GPS address, Back -- and RT, the one interface the layers
// (ui/layers/*.js) build on. An app is this base and its layers (cfg.layers: the native side says which; every layer
// there is when it says none). A layer adds roles (views), screens, map kinds, header buttons, sections of another
// layer's screen, the core's events it shows, and hooks; it never edits the base or another layer.
// Everything it shows comes from the core's lines (rtDeliver); everything it does is a line to the core (rtNative.send).
// Nothing here talks to the network.
'use strict'
const $ = (s) => document.querySelector(s)
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const send = (o) => window.rtNative.send(JSON.stringify(o))
const cap = (t) => t ? t.toLowerCase().replace(/(^| )(.)/g, (x, a, c) => a + c.toUpperCase()) : t   // a team as people say it: RAM alpha, team Alpha; red cross, Red Cross

// kinds: how each is drawn on the map (a layer adds its own with RT.kind)
const KINDS = {
  team:      { label: 'Teams',      shape: '',    size: 18, color: '#888' },
  eoc:       { label: 'EOC',        shape: 'sq',  size: 20, color: '#6b4fd8', ch: 'E' },
  ambulance: { label: 'Ambulances', shape: 'sq',  size: 18, color: '#e23b3b', ch: '+' },
  boat:      { label: 'Boats',      shape: 'sq',  size: 18, color: '#1565c0', ch: 'B' },
  victim:    { label: 'Victims',    shape: '',    size: 12, color: '#f2c200', ch: '' },
  hospital:  { label: 'Hospitals',  shape: 'sq',  size: 20, color: '#ffffff', ch: 'H', fg: '#c62828' },
  fire:      { label: 'Fire',       shape: 'dia', size: 18, color: '#ff6d00', ch: 'F' },
  power:     { label: 'Power',      shape: 'dia', size: 18, color: '#7e57c2', ch: 'P' },
  water:     { label: 'Water',      shape: 'dia', size: 18, color: '#0288d1', ch: 'W' },
  shelter:   { label: 'Shelters',   shape: 'sq',  size: 18, color: '#6d4c41', ch: 'S' },
  phone:     { label: 'Telecom crews', shape: 'dia', size: 18, color: '#455a64', ch: '☎' },
  host:      { label: 'Hosts',      shape: 'sq',  size: 16, color: '#263238', ch: 'N' },   // the nodes' own GPS
}
const TEAM_COLOR = { alpha: '#d62728', bravo: '#1f77b4', charlie: '#2ca02c', delta: '#ff7f0e' }
// [RT_TEAMS_ARE_THE_NETWORKS_V1] the teams are the network's: the admin's groups with a RAM (the door's op=roles) and every
// team a position names -- not a list in the page (Alpha..Delta only: an EOC drew no other team, and had no tab for one).
// A team with no colour of its own gets one from its name.
const TEAM_MORE = ['#9467bd', '#8c564b', '#e377c2', '#17becf', '#bcbd22', '#7f7f7f', '#393b79', '#637939', '#8c6d31', '#843c39']
function teamColor(t) { t = String(t || '').toLowerCase(); if (TEAM_COLOR[t]) return TEAM_COLOR[t]; let h = 0; for (const c of t) h = (h * 31 + c.charCodeAt(0)) >>> 0; return TEAM_MORE[h % TEAM_MORE.length] }
const capT = (t) => t ? t[0].toUpperCase() + t.slice(1) : t
function knownTeams() { return [...S.teamNames].sort() }
// [MAP_KINDS_DISCOVERED_V1] (John 2026-11-06: "Admin apps should show connected children of all types. Maps too") a kind
// no layer of this app knows (a plug-in's, a child's) is drawn the moment it appears: a plain marker, its initial, a
// colour of its own; it is on, and it is in Layers and the legend like any other. Says true when it was new.
function addKind(k) {
  if (!k || KINDS[k]) return false
  KINDS[k] = { label: capT(String(k).replace(/[-_]/g, ' ')), shape: '', size: 16, color: teamColor('kind:' + k), ch: String(k)[0].toUpperCase(), found: true }
  S.on[k] = true; return true
}
function addTeam(t) {
  t = String(t || '').toLowerCase(); if (!t || S.teamNames.has(t)) return false
  S.teamNames.add(t); if (RT.me().teams === 'all' && !S.teamsOff.has(t)) S.teamsOn.add(t)
  return true
}
function teamsChanged() { if (!map) return; layersPanel(); legend(); tabs(); toList() }
const LKP_MIN = 10            // a last known position is drawn this many minutes (the app's own choice)

const S = { teamNames: new Set(), teamsOff: new Set(), teamsOn: new Set(), gps: {}, cfg: {}, role: '', team: '', who: {}, pos: {}, seen: {}, lkp: {}, markers: {}, links: {}, on: {}, msgs: [], tab: 'all', direct: {}, compliance: {}, sheet: '' }
let map

// ------------------------------------------------------------ [RT_LAYERS_V1] RT: what a layer builds on
// RT.layer(name, fn)       a layer: fn(RT) runs once, when the app loads it
// RT.role(name, def)       a view the device connects as. def: label, title (the app's name opened as it), field
//                          ('login': user and password; 'team': the team layer's team; 'unit': a unit name, unitLabel,
//                          unitHint, units (the names offered);
//                          'none'), who() (the header's name for this device), kinds ('all' or the kinds on at first),
//                          teams ('all' | 'own' | 'none': the teams drawn at first), only (the kinds this view may ever
//                          draw), chat, home, sensors, mission, log, legend (html), act / req ([label, screen]: the
//                          header's two role buttons), connected(m) (after the base has set the view up)
// RT.screen(name, fn)      a screen (a sheet): RT.open(name) shows it; the role's act / req buttons name one
// RT.section(screen, fn, at)  more on another layer's screen: fn() is html, put at 'top' (first) or 'bottom'
// RT.on(ev, fn)            the core's event ev (its line's "ev"), for this layer
// RT.kind(name, def)       a map kind
// RT.button(id, label, onclick, title, before)  a header button (before that one; else before Layers), hidden until the layer shows it
// RT.hook(name, fn)        world (S.pos changed), redraw (want, place: the layer's own map items), popup (id, b, lkp
//                          -> more html), refresh (what), back (-> true when it closed something), connected (m)
const RT = {
  roles: {}, screens: {}, sections: {}, evs: {}, hooks: {}, loaded: [], buttons: [],
  layer(name, fn) { RT.loaded.push(name); fn(RT) },
  role(name, def) { RT.roles[name] = Object.assign({ name, label: cap(name), title: 'OpenEOC ' + cap(name), field: 'none', kinds: [], teams: 'none',
                                                      chat: true, sensors: true, mission: true }, def) },
  screen(name, fn) { RT.screens[name] = fn },
  open(name, ...a) { if (RT.screens[name]) RT.screens[name](...a) },
  section(screen, fn, at) { (RT.sections[screen] = RT.sections[screen] || []).push({ fn, at: at || 'top' }) },
  on(ev, fn) { (RT.evs[ev] = RT.evs[ev] || []).push(fn) },
  kind(name, def) { KINDS[name] = def },
  button(id, label, onclick, title, before) {
    if ($('#' + id)) return $('#' + id)
    const b = document.createElement('button'); b.id = id; b.type = 'button'; b.hidden = true; b.textContent = label
    if (title) b.title = title
    b.onclick = onclick; $(before || '#layersBtn').before(b); RT.buttons.push(b); return b
  },
  hook(name, fn) { (RT.hooks[name] = RT.hooks[name] || []).push(fn) },
  fire(name, ...a) { return (RT.hooks[name] || []).map(f => f(...a)) },
  /** the sections other layers put on this screen, after the screen drew its sheet */
  sectioned(screen) {
    const body = document.querySelector('#sheet .sh-body')
    for (const s of (RT.sections[screen] || [])) {
      const h = s.fn(); if (!h) continue
      const box = document.createElement('div'); box.innerHTML = h
      if (s.at === 'bottom') body.appendChild(box); else body.prepend(box)
    }
  },
  me() { return RT.roles[S.role] || {} },
}
window.RT = RT
// [RT_HEADER_MENU_V1] a header too narrow for its buttons (a phone): the ones that do not fit go into ☰, last first; Chat
// and ☰ stay in the header. Measured, not guessed by width: a layer's buttons come and go, and so does what fits.
function fitHeader() {
  const h = document.querySelector('header'), mb = $('#menuBtn'), menu = $('#menu')
  for (const b of [...menu.children]) h.insertBefore(b, mb)                       // all back, in their order
  const movable = () => [...h.querySelectorAll('button')].filter(b => b !== mb && b.id !== 'chatBtn' && !b.hidden)
  mb.hidden = true
  if (h.scrollWidth <= h.clientWidth + 1) { menu.hidden = true; return }
  mb.hidden = false
  let m = movable()
  while (h.scrollWidth > h.clientWidth + 1 && m.length) { menu.insertBefore(m.pop(), menu.firstChild) }
  if (!menu.children.length) mb.hidden = true
}
let fitting = false
const refit = () => { if (fitting) return; fitting = true; requestAnimationFrame(() => { fitHeader(); fitting = false }) }
window.addEventListener('resize', refit)
new MutationObserver(refit).observe(document.querySelector('header'), { subtree: true, attributes: true, attributeFilter: ['hidden'], childList: true })
$('#menuBtn').onclick = (e) => { e.stopPropagation(); $('#menu').hidden = !$('#menu').hidden }
$('#menu').addEventListener('click', () => { $('#menu').hidden = true })
document.addEventListener('click', (e) => { if (!$('#menu').hidden && !e.target.closest('#menu')) $('#menu').hidden = true })
RT.hook('connected', refit)
// [RT_TEAMS_ARE_THE_NETWORKS_V1] the admin's teams: every role with its own RAM that is not another view's
// [RT_CATEGORY_IS_THE_VIEW_V1] the admin's categories are the views: a group in Teams is a team (the Team view's list),
// one in Ambulance an ambulance unit (the Ambulance view's list), Hospitals a hospital, Power, Water, Shelter, Phone ...
// (a category's name, any case, its plural s dropped, is the view's name or label). The connect screen offers a view
// the admin's groups of its own category only.
function viewOfCategory(c) {
  const k = String(c || 'Teams').toLowerCase().trim(), one = k.replace(/s$/, '')
  const v = Object.values(RT.roles).find(r => [r.name, String(r.label || '').toLowerCase()].some(n => n === k || n === one))
  return v ? v.name : ''
}
S.groupsByView = {}
RT.on('roles', (m) => {
  const by = {}
  for (const [r, x] of Object.entries(m.roles || {})) {
    const v = viewOfCategory((x || {}).category); if (!v || RT.roles[r.toLowerCase()]) continue   // a view's own role (eoc ...) is no group
    if (v === 'team' && !(x || {}).ram) continue                   // a team is a group with its own RAM
    ;(by[v] = by[v] || []).push(r)
  }
  S.groupsByView = by
  let more = false
  for (const r of by.team || []) if (addTeam(r)) more = true
  if (more) teamsChanged()
  roleFields()
})

// ------------------------------------------------------------ start: the native side calls rtInit once the page loads
// [RT_LAYERS_V1] the app's layers first (cfg.layers, in that order; every one there is when it names none), then the
// connect screen with the views they add
const ALL_LAYERS = ['help', 'field', 'eoc', 'team', 'medical', 'units', 'public', 'ics', 'fema', 'paper']   // [FEMA_EOC_V1] fema, paper: after ics (they add to its boards)
window.rtInit = function (cfg) {
  S.cfg = cfg || {}
  const names = (S.cfg.layers && S.cfg.layers.length ? S.cfg.layers : ALL_LAYERS).filter(n => /^[a-z0-9_-]+$/.test(n))
  const load = (n) => new Promise((ok) => {
    const s = document.createElement('script'); s.src = 'layers/' + n + '.js'
    s.onload = ok; s.onerror = () => { status('⚠ this app has no layer ' + n); ok() }; document.body.appendChild(s)
  })
  names.reduce((p, n) => p.then(() => load(n)), Promise.resolve()).then(start)
}
function start() {
  const f = $('#connect')
  f.door.value = S.cfg.door || ''; f.name.value = S.cfg.name || ''
  // the views: one radio each, in the order the layers added them
  $('#roleSet').innerHTML = '<legend>View</legend>' + Object.values(RT.roles).map(r => `<label><input type="radio" name="role" value="${esc(r.name)}"> ${esc(r.label)}</label>`).join('')
  document.querySelectorAll('[name=role]').forEach(e => e.onchange = roleFields)
  // [RT_TEAM_APP_V1] [RT_PUBLIC_APP_V1] an app built for one view (the Team app, the Public app ...) opens as that view only
  const lock = RT.roles[S.cfg.lock]
  if (lock) {
    S.cfg.role = lock.name; $('#roleSet').hidden = true
    document.querySelector('#connect h1').textContent = lock.title; document.title = lock.title
    // [RT_LOCKED_APP_SAYS_SO_V1] an app built for one view says so on its first screen, and which app asks instead
    document.querySelector('#connect h1').insertAdjacentHTML('afterend', `<div class="meta locked">This app opens as ${esc(lock.label)} only.${S.cfg.platform === 'android' ? ' The app that asks which one is <b>OpenEOC</b> (Android Studio: the <b>app</b> module).' : ' The app that asks which one is OpenEOC (OpenEOC.cmd).'}</div>`)
  }
  else pickerReady()
  // [APP_EXIT_V1] Exit, where the native side can (the phone): in the header (☰ on a narrow screen) and on the connect screen
  const canExit = !!(window.rtNative && window.rtNative.exit)
  $('#exitBtn').hidden = !canExit; $('#cexit').hidden = !canExit
  if (S.cfg.build) $('#cstatus').textContent = S.cfg.build      // [APP_SAYS_ITS_BUILD_V1] which build this is, on the first screen
  // [APP_IS_SELF_CONTAINED_V1] the phone is the FrogNet member itself: its invitation, pasted once, then kept (and only said)
  if (S.cfg.platform === 'android') {
    // [ON_THE_LAN_NO_TUNNEL_V1] on a FrogNet node's own LAN nothing is asked: the node routes the phone. Off it, the
    // invitation once; after that only what this phone is on FrogNet is said
    const said = S.cfg.lan || S.cfg.invited
    document.querySelector('.for-invite').hidden = false
    document.querySelector('.for-invite label').hidden = !!said
    document.querySelector('.inv-say').textContent = said ? 'FrogNet: ' + said : ''
  }
  const first = RT.roles[S.cfg.role] ? S.cfg.role : (RT.roles.team ? 'team' : Object.keys(RT.roles)[0])
  if (first) f.querySelector(`[name=role][value=${first}]`).checked = true
  RT.fire('init', S.cfg)
  roleFields(); askRoles()
  if (!$('#qrSec').hidden) $('#manual').open = false      // [CONNECT_QR_FIRST_V1] the QR code is the way in; Manual login a tap away
  // [JOINED_IS_DONE_V1] (John 2026-11-09: "once joined and authenticated the first time, they are done") a phone that has
  // joined opens on the map: no picker, no connect screen. It connects as what joining made it (its user, its team or
  // view, the node it joined through); the connect screen shows only if that connect fails, and says why.
  const joined = joinedView()
  S.ready = true; for (const m of early.splice(0)) window.rtDeliver(m)
  setTimeout(() => {
    if (joined) {          // the splash stays, saying who is connecting, until the map (or the connect screen, saying why)
      S.autoConnect = true
      const t = S.cfg.enrolled.user + ': connecting as ' + (RT.roles[joined] || {}).label + (S.cfg.enrolled.lead ? ', lead of ' + cap(S.cfg.enrolled.role) : ' in ' + cap(S.cfg.enrolled.role))
      $('#splash').insertAdjacentHTML('beforeend', `<div class="joining">${esc(t)}</div>`); $('#cstatus').textContent = t
      f.requestSubmit(); return
    }
    $('#splash').classList.add('gone')
    if (lock || Object.keys(RT.roles).length < 2) f.hidden = false; else pickApp()
  }, 1800)
}
// [JOINED_IS_DONE_V1] what joining made this phone: the view it connects as, with its fields filled ('' : not joined, or
// joined as a view this app does not have)
function joinedView() {
  const e = S.cfg.enrolled || {}; if (!e.user) return ''
  const f = $('#connect'), rl = String(e.role || '').toLowerCase()
  // an app built for one view (the Ambulance app ...) connects as that view; else the view by the role's name, else a team
  const view = S.cfg.lock && RT.roles[S.cfg.lock] ? S.cfg.lock : RT.roles[rl] && rl !== 'team' ? rl : RT.roles.team ? 'team' : ''
  if (!view) return ''
  if (e.door) f.door.value = e.door
  if (!S.cfg.nameGiven || !f.name.value) f.name.value = e.user
  f.user.value = e.user
  const rb = f.querySelector(`[name=role][value=${view}]`); if (rb) rb.checked = true
  RT.fire('enrolled', e, view); roleFields()
  return view
}
// [CONNECT_QR_FIRST_V1] a field Manual login holds, wanting a value at Connect: Manual login opens to show it
$('#connect').addEventListener('invalid', () => { $('#manual').open = true }, true)
// [RT_APP_PICKER_V1] an app with more than one view (the OpenEOC app, not the EOC, Team or Public app) asks first which
// app this is: a pop-up with one button per view; the connect screen is then that app's (its title, its fields), and
// "Other app" on it asks again. The view used last is marked, never taken without asking.
function pickerReady() {
  $('#roleSet').hidden = true
  document.body.insertAdjacentHTML('beforeend', '<div id="pick" hidden><div class="pick-card"><h2>Which app?</h2><div class="pick-list"></div></div></div>')
  $('#cstatus').insertAdjacentHTML('beforebegin', '<button type="button" id="otherApp" class="linkish">Other app</button>')
  $('#otherApp').onclick = pickApp
}
function pickApp() {
  const last = S.cfg.role
  $('#pick .pick-list').innerHTML = Object.values(RT.roles).map(r => `<button type="button" data-pick="${esc(r.name)}">${esc(r.title || 'OpenEOC ' + r.label)}${r.name === last ? '<span class="pick-last">last used</span>' : ''}</button>`).join('')
  $('#pick').querySelectorAll('[data-pick]').forEach(b => b.onclick = () => {
    const r = RT.roles[b.dataset.pick], f = $('#connect')
    f.querySelector(`[name=role][value=${r.name}]`).checked = true; roleFields()
    const t = r.title || 'OpenEOC ' + r.label
    f.querySelector('h1').textContent = t; document.title = t
    $('#pick').hidden = true; f.hidden = false
  })
  $('#connect').hidden = true; $('#pick').hidden = false
}
function roleFields() {
  const c = $('#connect').querySelector('[name=role]:checked'), r = RT.roles[c ? c.value : ''] || {}
  // [TEAM_HOST_V1] a team member's phone joins its lead's hotspot by scanning the hotspot's QR code
  // [ENROLL_V1] any view: the frog QR the admin (or the team's lead) shows, then the password
  // [CONNECT_QR_FIRST_V1] the QR code first, on a phone that can scan and has not joined yet; joined, it is done
  const joined = !!(S.cfg.enrolled && S.cfg.enrolled.user)
  $('#qrSec').hidden = joined || !(S.cfg.platform === 'android' && window.rtNative && window.rtNative.scanTeam)
  $('#joinEoc').hidden = joined || !(S.cfg.platform === 'android' && window.rtNative && window.rtNative.enroll)
  document.querySelectorAll('[data-field]').forEach(e => { e.hidden = e.dataset.field !== r.field })
  document.querySelector('.unit-l').textContent = r.unitLabel || 'Unit'
  // [RT_LAYERS_V1] the unit names this view offers and its hint: its own (a Telecom crew is not offered hospitals)
  $('#connect').unit.placeholder = r.unitHint || ''
  const mine = (S.groupsByView || {})[r.name]          // [RT_CATEGORY_IS_THE_VIEW_V1] the admin's groups of this view's category
  $('#units').innerHTML = (mine && mine.length ? mine.map(capT) : (r.units || [])).map(u => `<option>${esc(u)}</option>`).join('')
}
// [ADMIN_APP_V1] the app's roles are the admin's: asked of the node as soon as there is one (and when it changes)
function askRoles() { const d = $('#connect').door.value.trim(); if (d) send({ op: 'roles', door: d }) }
$('#connect').door.addEventListener('change', askRoles)
$('#connect').onsubmit = (e) => {
  e.preventDefault()
  const f = e.target, role = f.querySelector('[name=role]:checked').value
  $('#cstatus').textContent = 'connecting…'
  send({ op: 'connect', door: f.door.value.trim(), dev: S.cfg.dev, name: f.name.value.trim(), role, team: f.team ? f.team.value : '',
         user: f.user.value.trim(), password: f.password.value, unit: f.unit.value.trim(), invite: f.invite.value.trim(),
         teamRam: role === 'team' ? (S.teamRam || '') : '' })
}
$('#exitBtn').onclick = $('#cexit').onclick = () => window.rtNative.exit()
$('#scanTeam').onclick = () => { $('#cstatus').textContent = 'scan the frog QR code your admin or team lead shows you'; window.rtNative.scanTeam() }
// [CHILDREN_JOIN_V1] (John 2026-11-06: "Creating a team is a matter of installing the team app, firing up the admin
// console for it, and hitting the Join EOC button, with the team name. It needs the eoc teams password") the EOC's own
// line, at the node in the Node field: the same as scanning the EOC's QR -- the team's name, the teams password, Join
$('#joinEoc').onclick = () => {
  const d = $('#connect').door.value.trim()
  if (!d) { $('#cstatus').textContent = 'the Node first: the node the EOC is on (e.g. 10.170.170.1)'; $('#connect').door.focus(); return }
  const url = 'http://' + d.replace(/^https?:\/\//, '').replace(/\/.*/, '') + '/dc/ram.php'
  enrollScan({ line: FrogQR.enrollLine(url, 'openeoc', '*'), url, app: 'openeoc', user: '', manual: true })   // [EOC_LEADS_V1] the user typed here
}
// [ENROLL_V1] scanned: who this code is for and where; the password, then Join
function enrollScan(m) {
  S.enrollLine = m.line; S.enrollTeam = m.team ? m : null
  // [EOC_LEADS_V1] (John 2026-11-09: "When I scan the QR code it should take me to a filled in screen with my username filled
  // in, but the team name blank. I fill in the team name, add my password, and hit the button to create the Team") the
  // EOC's QR for a user it added (its door, a user, no key): the user filled in, the team's name blank, the password
  // [EOC_USER_QR_V1] an app that opens as a logged-in view (the EOC, EOCAdmin) takes the admin's QR for its user: the user
  // filled in, its password, Log in (enrolled as that user) -- no team's name
  const view = S.cfg.lock || (document.querySelector('[name=role]:checked') || {}).value || ''
  const loginView = !!(RT.roles[view] && RT.roles[view].field === 'login')
  const lead = /\/dc\/ram\.php/.test(m.url) && !m.key && !loginView && (m.manual || (m.user && m.user !== '*'))
  if (loginView && /\/dc\/ram\.php/.test(m.url) && m.user && m.user !== '*') {
    S.enrollLead = null; $('#enrollCallL').hidden = true
    $('#enrollUserL').hidden = false; $('#enrollUser').value = m.user; $('#enrollUser').readOnly = true; $('#enrollGo').textContent = 'Log in'
    $('#enrollWho').textContent = RT.roles[view].label + ' · ' + m.app + ' · ' + m.url.replace(/^https?:\/\//, '').replace(/\/.*/, '')
    $('#enrollBox').hidden = false; $('#enrollPw').value = ''; $('#enrollPw').focus()
    $('#cstatus').textContent = m.user + "'s password"
    return
  }
  S.enrollLead = lead ? m : null
  $('#enrollUserL').hidden = !lead; $('#enrollGo').textContent = lead ? 'Create the Team' : 'Join'
  if (lead) {
    $('#enrollUser').value = m.user || ''; $('#enrollUser').readOnly = !m.manual
    $('#enrollCallL').hidden = false; $('#enrollCall').value = ''; $('#enrollCallL').querySelector('.ecl').textContent = 'Team name'
    $('#enrollCall').setAttribute('autocapitalize', 'words')
    $('#enrollWho').textContent = 'Create your team · ' + m.app + ' · ' + m.url.replace(/^https?:\/\//, '').replace(/\/.*/, '')
    $('#enrollBox').hidden = false; $('#enrollPw').value = ''; (m.manual ? $('#enrollUser') : $('#enrollCall')).focus()
    $('#cstatus').textContent = m.manual ? 'the user the EOC gave you, your team\'s name and your password' : "your team's name and your password: you lead the team"
    return
  }
  // [TEAM_JOIN_V1] a team's QR: the call sign is yours, the password the team's. [CHILDREN_JOIN_V1] the EOC's QR (its
  // door): the name is your new team's (anything), the password the EOC's teams password -- you lead that team
  const eoc = m.team && /\/dc\/ram\.php/.test(m.url); S.enrollEoc = eoc
  // [CONNECT_QR_FIRST_V1] (John 2026-11-09: the team's QR "should not contain the password ... but that's all it should
  // ask for") a member's call sign is the name typed at Your name; asked here only when none was typed, or it is not one
  const nm = String($('#connect').name.value || '').trim().toUpperCase()
  const callFromName = m.team && !eoc && S.cfg.nameGiven && /^[A-Z0-9/._-]{1,30}$/.test(nm)
  $('#enrollCallL').hidden = !m.team || callFromName; $('#enrollCall').value = callFromName ? nm : ''
  $('#enrollCallL').querySelector('.ecl').textContent = eoc ? "Your team's name" : 'Your call sign'
  $('#enrollCall').setAttribute('autocapitalize', eoc ? 'words' : 'characters')
  // [TEAMS_AUTHORIZED_V1] a QR with a login is a Team's the admin authorized (or an EOC user's): its password claims it here
  $('#enrollWho').textContent = (eoc ? 'Join the EOC · ' : m.team ? 'Team · ' : m.key ? 'Join as ' + m.user + ' · ' : 'Claim ' + m.user + ' · ') + m.app + ' · ' + m.url.replace(/^https?:\/\//, '').replace(/\/.*/, '')
  $('#enrollBox').hidden = false; $('#enrollPw').value = ''; (m.team && !callFromName ? $('#enrollCall') : $('#enrollPw')).focus()
  $('#cstatus').textContent = eoc ? "your team's name and the EOC's teams password" : callFromName ? 'the team password (you join as ' + nm + ')' : m.team ? 'your call sign and the team password' : m.key ? m.user + "'s own password, from your team lead" : m.user + "'s password: this device claims it"
}
$('#enrollGo').onclick = () => {
  if (!S.enrollLine) return
  if (S.enrollLead) {                                    // [EOC_LEADS_V1] create the team: the user, the team's name, the password
    const u = $('#enrollUser').value.trim(), t = $('#enrollCall').value.trim()
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/.test(u)) { $('#cstatus').textContent = 'your user: the one the EOC gave you'; $('#enrollUser').focus(); return }
    if (!/^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$/.test(t)) { $('#cstatus').textContent = "the team's name: letters, digits, space . _ - (at most 40)"; $('#enrollCall').focus(); return }
    if (!$('#enrollPw').value) { $('#cstatus').textContent = 'your password'; $('#enrollPw').focus(); return }
    $('#cstatus').textContent = 'creating team ' + t + '…'; $('#enrollGo').disabled = true
    S.enrollPw = $('#enrollPw').value   // [PASSWORD_NOT_LOGGED_V1]
    // the kind: the view this app runs the team as (its own, or the one picked), one of the roles the EOC gave the lead
    const kind = S.cfg.lock || (document.querySelector('[name=role]:checked') || {}).value || 'team'
    window.rtNative.enroll(JSON.stringify({ line: FrogQR.enrollLine(S.enrollLead.url, S.enrollLead.app, u), password: S.enrollPw, team: t, kind }))
    return
  }
  let line = S.enrollLine
  if (S.enrollTeam) {
    const raw = $('#enrollCall').value.trim(), cs = S.enrollEoc ? raw : raw.toUpperCase()
    if (S.enrollEoc ? !/^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$/.test(cs) : !/^[A-Z0-9/._-]{1,30}$/.test(cs)) {
      $('#cstatus').textContent = S.enrollEoc ? "a team's name: letters, digits, space . _ - (at most 40)" : 'a call sign: letters, digits, / . _ -'; return }
    line = FrogQR.enrollLine(S.enrollTeam.url, S.enrollTeam.app, cs, S.enrollTeam.key)   // [SEALED_ENROLL_V1] the lead's key stays in the line
  }
  $('#cstatus').textContent = 'joining…'; $('#enrollGo').disabled = true
  S.enrollPw = $('#enrollPw').value   // [PASSWORD_NOT_LOGGED_V1] kept here for the connect: the native side never says it back (its lines are in Logcat)
  window.rtNative.enroll(JSON.stringify({ line, password: S.enrollPw }))
}
// [ENROLL_V1] enrolled: this phone is a member of the region now; the app connects as the user, in its role (the
// admin's role names are any case): a view this app has by that name, else a team (the team layer's)
function enrolled(m) {
  const f = $('#connect'); $('#enrollGo').disabled = false; $('#enrollBox').hidden = true; $('#enrollPw').value = ''
  S.cfg.enrolled = { user: m.user, role: m.role, lead: m.lead, door: m.door }
  if (m.door) f.door.value = m.door
  f.name.value = m.user; f.user.value = m.user; f.password.value = S.enrollPw || ''; S.enrollPw = ''
  const rl = String(m.role || '').toLowerCase(), kd = String(m.kind || '').toLowerCase()
  // [EOC_LEADS_V1] a team a lead created runs as its kind (team, hospital, ambulance ...): a unit's name is the team's
  const view = kd && RT.roles[kd] ? kd : RT.roles[rl] && rl !== 'team' ? rl : RT.roles.team ? 'team' : rl
  if (kd && RT.roles[kd] && RT.roles[kd].field === 'unit') f.unit.value = m.role
  const rb = f.querySelector(`[name=role][value=${view}]`); if (rb && !$('#roleSet').hidden) rb.checked = true
  RT.fire('enrolled', m, view)
  roleFields()
  $('#cstatus').textContent = m.user + (m.lead ? ', lead of ' + m.role : ' in ' + m.role) + ': joined FrogNet (' + m.invited + '); connecting'
  f.requestSubmit()
}

// ------------------------------------------------------------ the core's lines
// [RT_LAYERS_V1] a line the core says before the layers are loaded waits for them (theirs would be lost)
const early = []
window.rtDeliver = function (line) {
  const m = typeof line === 'string' ? JSON.parse(line) : line
  if (!S.ready) { early.push(m); return }
  if (m.ev === 'status') { status(m.text)
    // [JOINED_IS_DONE_V1] while a joined phone connects behind the splash, the splash says what it is doing
    if (S.autoConnect && document.querySelector('#splash .joining')) document.querySelector('#splash .say') ? document.querySelector('#splash .say').textContent = m.text
      : $('#splash').insertAdjacentHTML('beforeend', `<div class="say">${esc(m.text)}</div>`) }
  else if (m.ev === 'error') { if (/GPS sensors/.test(m.text)) { S.hostsSaid = true; addressFix() }
    // a sheet still waiting on an answer (Sensors: asking / finding the node) says the error, not "asking…" for ever
    if (/sensors/i.test(m.text) && S.sheet === 'sens' && !$('#sheet').hidden && /asking…|finding the node/.test($('#sheet .sh-body').textContent)) $('#sheet .sh-body').innerHTML = '<div class="bad">⚠ ' + esc(m.text) + '</div>'
    // [JOINED_IS_DONE_V1] a joined phone's own connect failed: the connect screen, saying why (not a blank screen)
    if (S.autoConnect && $('#main').hidden) { S.autoConnect = false; $('#splash').classList.add('gone'); $('#manual').open = true; $('#connect').hidden = false }
    status('⚠ ' + m.text); if (!$('#connect').hidden) $('#cstatus').textContent = m.text }
  else if (m.ev === 'diag') diagSheet(m)
  else if (m.ev === 'connected') connected(m)
  else if (m.ev === 'enrollScan') enrollScan(m)
  else if (m.ev === 'enrolled') enrolled(m)
  else if (m.ev === 'enrollFailed') { $('#enrollGo').disabled = false; $('#cstatus').textContent = '⚠ ' + m.text; status('⚠ ' + m.text) }
  else if (m.ev === 'mission') { $('#mission').textContent = m.name || m.id; RT.fire('mission', m)
    const changed = S.mission && S.mission.id !== m.id
    S.mission = m; if (S.sheet === 'mis' && !$('#sheet').hidden && !S.misEditing) missionPanel()
    if (changed) notify({ id: 'mis:' + m.id, from: '', name: 'Mission', to: 'all', text: 'the mission now is ' + (m.name || m.id), at: Date.now() / 1000 })
    addressFix() }
  else if (m.ev === 'world') { world(m.who || {}, m.pos || {}); diagAgain() }
  else if (m.ev === 'msg') { message(m); diagAgain() }
  else if (m.ev === 'sensorHosts') sensorHosts(m.hosts || {})
  else if (m.ev === 'sensors') sensorList(m.host, m.list || [])
  else if (m.ev === 'sensor') sensorShow(m)
  else if (m.ev === 'comms') commsStart(m.peer, m.door)
  else if (m.ev === 'incoming') ring(m)
  else if (m.ev === 'devices') devicePicker(m)
  else if (m.ev === 'myfix') { myFix = { lat: m.lat, lon: m.lng }; followMe() }
  else if (m.ev === 'radiolog') { S.rlog = m; if (S.sheet === 'rlog' && !$('#sheet').hidden) radioLog() }
  else if (m.ev === 'nogps') noGps(m)
  else if (m.ev === 'geocoded') geocoded(m)
  else if (m.ev === 'hosts') { S.hostsSaid = true; S.gps = {}; for (const h of m.list || []) S.gps['gps:' + h.name] = Object.assign({ kind: 'host' }, h); redraw(); addressFix() }
  for (const f of (RT.evs[m.ev] || [])) f(m)                     // [RT_LAYERS_V1] the layers' events
}
function status(t) { $('#status').textContent = t; $('#dot').classList.toggle('up', !/FAILING|cannot|⚠/.test(t)); if (/FAILING|cannot|⚠|not |page error/.test(t)) said(t) }
// ------------------------------------------------------------ [RT_DIAG_V1] Diagnostics: the core's account (op=diag) and the page's
// own (the map, this device's fixes, what went wrong lately), on one sheet, again whenever the world, a message or an error
// comes while it is open. Nothing to run anywhere: the app says it.
S.errs = []
function said(t) { S.errs.push(new Date().toLocaleTimeString() + ' ' + t); if (S.errs.length > 30) S.errs.shift(); diagAgain() }
let diagAsked = 0
function diagAgain() { if (S.sheet === 'diag' && !$('#sheet').hidden && Date.now() - diagAsked > 1000) { diagAsked = Date.now(); send({ op: 'diag' }) } }
$('#diagBtn').onclick = () => { diagAsked = Date.now(); send({ op: 'diag' }); sheet('diag', 'Diagnostics', '<div class="meta">asking the core…</div>') }
function diagSheet(d) {
  if (S.sheet !== 'diag' || $('#sheet').hidden) return
  const t = (ms) => ms ? new Date(ms).toLocaleTimeString() : '—', j = (o) => esc(JSON.stringify(o))
  const yes = (b) => b ? '<span class="ok">yes</span>' : '<span class="bad">NO</span>'
  let tun = null; try { tun = window.rtNative && window.rtNative.tunnelState ? JSON.parse(window.rtNative.tunnelState()) : null } catch (e) { tun = { notes: ['tunnel state not read: ' + e] } }
  let gps = null; try { gps = window.rtNative && window.rtNative.gpsState ? JSON.parse(window.rtNative.gpsState()) : null } catch (e) { gps = { error: String(e) } }
  const me = d.me || {}, lf = me.lastFix, ls = d.lastSay, h = d.hosts
  const fs = (d.followers || []).map(f => `<tr><td><b>${esc(f.name)}</b><div class="meta">${esc(f.service)}${f.variable ? ' / ' + esc(f.variable) : ''}</div></td>
    <td>${esc(f.at)}</td><td>${f.up ? '<span class="ok">up</span>' : '<span class="bad">DOWN</span>'}${f.failing ? '<div class="bad">' + esc(f.failing) + '</div>' : ''}</td>
    <td>${f.sessions}</td><td>${f.writes}</td><td>${Object.entries(f.holds || {}).map(([v, n]) => esc(v || '(none)') + ' ' + n).join('<br>') || '<span class="meta">nothing</span>'}</td></tr>`).join('')
  const body = `<div class="diag">
    <div><b>${esc(d.build)}</b> · view ${esc(d.role)} (${esc(d.view)})${d.team ? ' · team ' + esc(d.team) : ''} · mission ${esc(d.mission) || '<span class="bad">none</span>'} · ${esc(d.name)} · layers ${esc(d.layers)}</div>
    <div>Door <b>${esc(d.door) || '<span class="bad">none</span>'}</b>; the RAMs it says: ${j(d.doorRams)}; in use: ${j(d.rams)}</div>
    <h3>Where this device reads and writes</h3>
    <table class="dt"><tr><th>Follower</th><th>RAM at</th><th>State</th><th>Sessions</th><th>Writes</th><th>Holds</th></tr>${fs || '<tr><td colspan="6" class="bad">no followers: not connected</td></tr>'}</table>
    ${tun ? `<h3>FrogNet tunnel</h3>
    <div>This app's tunnel: ${tun.running ? '<span class="ok">up</span>' : '<span class="bad">not running</span>'}${tun.vpnUp ? ' · a VPN network is up' : ''}${tun.otherTunnel ? ' · another FrogNet app\'s tunnel is up' : ''} · invitation ${tun.invited ? 'yes (' + esc(tun.what) + ')' : '<span class="bad">NO</span>'}</div>
    <div>On a node's LAN: ${tun.nodeLan ? esc(tun.nodeLan) + ' (no tunnel needed: the node routes this phone)' : 'no'}${tun.lanShape && !tun.nodeLan ? ' · node-shaped LAN ' + esc(tun.lanShape) : ''} · gateways asked: ${esc(JSON.stringify(tun.gateways))}</div>
    <div>Team Wi-Fi (this phone's hotspot): ${tun.hotspot ? '<span class="ok">on</span> at ' + esc(tun.hotspot) : tun.hosting ? '<span class="bad">OFF while this phone hosts the team</span>' : 'off'}${tun.hosting ? ' · hosting the team' : ''} · other interfaces: ${esc((tun.ifaces || []).map(i => i.name + ' ' + i.addr + (i.hotspot ? ' (hotspot)' : '')).join(', ') || 'none')}</div>
    <div class="meta">${esc(tun.networks || '')}</div>
    <div class="meta">${(tun.notes || []).map(esc).join('<br>') || 'no tunnel decision yet'}</div>` : ''}
    <h3>This device</h3>
    <div>${gps ? `Phone GPS: permission ${yes(gps.permission)}, GPS on ${yes(gps.gpsOn)}, fixes ${gps.fixes}${gps.last ? `, last ${gps.last.lat.toFixed(5)},${gps.last.lng.toFixed(5)} ±${Math.round(gps.last.acc)} m, ${gps.last.ageS} s ago` : ' <span class="bad">(none yet)</span>'}` : `This device has no GPS of its own: its position is ${S.addr ? 'the address ' + esc(S.addr.label || '') : S.nodeFixed ? 'its node\'s, ' + esc((myNode() || {}).name || '') + ' (its GPS)' : '<span class="bad">none</span>: ' + nodeWhy()} <button type="button" onclick="addressSheet('Where is this device?')">Where am I?</button>`}</div>
    <div>Last position sent to the core: ${lf ? `${t(lf.at)} ${Number(lf.lat).toFixed(5)},${Number(lf.lng).toFixed(5)} (${esc(lf.src)}); written to ${esc(lf.to)}: ${yes(lf.written)}` : '<span class="bad">none</span>'}</div>
    <div>In the positions RAM: ${yes(me.inPositions)}${me.readBack ? ' ' + j({ lat: me.readBack.lat, lng: me.readBack.lng, kind: me.readBack.kind }) : ''} · present: ${yes(me.inPresence)}</div>
    <div>Last message: ${ls ? `${t(ls.at)} to ${esc(ls.to)}; written ${yes(ls.written)}; came back ${yes(ls.cameBack)}` : 'none sent'}</div>
    <h3>The map</h3>
    <div>${esc(mapSaid || '')}</div>
    <div>Layers on: ${esc(Object.entries(S.on).filter(([, v]) => v).map(([k]) => k).join(', ') || 'none')}</div>
    <div>Nodes' GPS: ${!h ? 'not asked (turn on the Hosts layer)' : h.error ? '<span class="bad">' + esc(h.error) + '</span> (' + esc(h.from) + ')' : (h.withFix || []).length + ' with a fix ' + j((h.withFix || []).map(x => x.name)) + ', no fix ' + j(h.noFix) + ' (' + esc(h.from) + ', ' + t(h.at) + ')'}</div>
    <h3>Lately</h3><div class="meta">${S.errs.slice().reverse().map(esc).join('<br>') || 'nothing went wrong'}</div>
    <div class="meta">as of ${t(d.at)}</div></div>`
  $('#sheet .sh-body').innerHTML = body
}
function refreshSheet(what) { if (!$('#sheet').hidden) RT.fire('refresh', what) }

function connected(m) {
  S.doorNode = m.node || ''                            // [DOOR_SAYS_ITS_NODE_V1] the node the door is (dc-door r16 on)
  if ($('#pick')) $('#pick').hidden = true     // [RT_APP_PICKER_V1] connected (an enrolled phone, a lead's card): the question is answered
  S.role = m.role; S.team = m.team || ''; S.connectedAt = Date.now(); S.autoConnect = false; $('#splash').classList.add('gone')
  // [WINDOW_SAYS_ITS_VIEW_V1] the title (the window's, on Windows) says the view this app is running as, and its team or unit
  { const rr = RT.roles[m.role] || {}; document.title = (rr.title || 'OpenEOC ' + (rr.label || m.role)) + (S.team ? ' · ' + cap(S.team) : '') }
  const r = RT.me()
  $('#connect').hidden = true; $('#main').hidden = false
  $('#who').textContent = S.cfg.name + ' · ' + (r.who ? r.who() : r.label || S.role)
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
  for (const k in KINDS) S.on[k] = !!KINDS[k].found || r.kinds === 'all' || (r.kinds || []).includes(k)   // [MAP_KINDS_DISCOVERED_V1] a found kind: on
  S.on.host = true                                      // [RT_HOSTS_ALWAYS_V1] the nodes are on every view's map, from the start
  S.on.lkp = !r.only
  // (John, 2026-11-01) the EOC starts with the map, teams and resources on it; the chat is a button away. A team member
  // starts with only their own team on the map ([TEAM_VIEW_IS_THE_TEAM_V1]); the others are added from Layers
  if (S.team) S.teamNames.add(S.team.toLowerCase())
  for (const b of Object.values(S.pos)) if (b && b.kind === 'team') S.teamNames.add(String(b.team || '').toLowerCase())
  S.teamNames.delete('')
  S.teamsOn = new Set(r.teams === 'own' ? [S.team.toLowerCase()] : r.teams === 'all' ? [...S.teamNames].filter(t => !S.teamsOff.has(t)) : [])
  layersPanel(); legend()
  if (S.on.host) send({ op: 'hosts' })                 // the nodes' own GPS sensors, read once now
  for (const b of RT.buttons) b.hidden = true          // the layers' buttons: each layer shows its own for this view
  $('#chatBtn').hidden = !r.chat
  $('#sensBtn').hidden = !r.sensors
  $('#misBtn').hidden = !r.mission
  $('#logBtn').hidden = !r.log
  for (const [id, x] of [['actBtn', r.act], ['reqBtn', r.req]]) {     // the role's two buttons, when this app has their screens
    const b = $('#' + id); b.hidden = !(x && RT.screens[x[1]])
    if (!b.hidden) { b.textContent = x[0]; b.onclick = () => RT.open(x[1]) }
  }
  tabs(); toList()
  if (r.connected) r.connected(m)
  // [LEAD_HOTSPOT_V1] a team's lead: its hotspot is the team's Wi-Fi; on every connect the phone makes sure it is on
  // [LEAD_HOSTS_ITS_TEAM_V1] (John 2026-11-09, found when team messages said "team Bigdogs's RAM is not served on this
  // network": "If the team is not currently active on the network, a device joining and starting the team will be the
  // host") the lead's phone hosts its team's RAM itself when no one serves it; one that is served is not replaced.
  // Hosting needs the hotspot on: Host team turns it on (asks Android) and hosts when it is.
  const myTeam = String(S.team || '').toLowerCase()
  const led = S.cfg.enrolled && S.cfg.enrolled.lead && String(S.cfg.enrolled.role || '').toLowerCase() === myTeam
  if (led && S.role === 'team' && myTeam && !Object.keys(m.rams || {}).some(k => k.toLowerCase() === myTeam) && window.rtNative && window.rtNative.teamHost) {
    status('team ' + cap(myTeam) + "'s RAM is served nowhere yet: this phone, its lead's, hosts it"); window.rtNative.teamHost()
  } else if (S.cfg.enrolled && S.cfg.enrolled.lead && window.rtNative && window.rtNative.ensureHotspot) window.rtNative.ensureHotspot()
  RT.fire('connected', m)
  addressFix()
  redraw()
}

// ------------------------------------------------------------ the map
function colorOf(b) { if (b.ooc) return '#d50000'; return b.kind === 'team' ? teamColor(b.team) : (KINDS[b.kind] || KINDS.team).color }
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
  const before = Object.keys(S.who).join(); S.who = who; S.pos = pos; RT.fire('world')
  let more = false; for (const id in pos) { const b = pos[id]; if (b && (b.kind === 'team' ? addTeam(b.team) : addKind(b.kind))) more = true }
  if (more) teamsChanged()
  const kinds = [...new Set(Object.values(pos).filter(Boolean).map(b => b.kind))].sort().join()
  if (kinds !== S.kindsSeen) { S.kindsSeen = kinds; if (map && !more) legend() }
  if (Object.keys(who).join() !== before && map) toList()
  redraw()
}
// [TEAM_VIEW_IS_THE_TEAM_V1] which teams are drawn: a team member starts with their own; the EOC with all
function shown(b) {
  if (b.kind === 'team') return !!S.on.team && S.teamsOn.has(String(b.team || '').toLowerCase())
  return S.on[b.kind] !== undefined ? S.on[b.kind] : S.on.team
}
// [RT_MARKER_INFO_V1] a marker, selected: what it is, where, since when, through which node -- and the Sensors of that
// platform (a node: its own; anyone else: the node it is on, by its address)
function popupHtml(id, b, lkp) {
  const bits = [b.status, b.beds !== undefined ? b.beds + ' beds available' : '', b.severity, b.customers_out ? b.customers_out + ' customers out' : '', b.to ? 'to ' + b.to : '']
  const kind = (KINDS[b.kind] || {}).label || b.kind || ''
  const at = b.at ? new Date(b.at * 1000).toLocaleTimeString() : b.fixAt ? new Date(b.fixAt).toLocaleTimeString() : ''
  const isHost = b.kind === 'host' || String(id).startsWith('gps:')
  const via = !isHost && b.via ? String(b.via) : ''
  const info = [kind, typeof b.lat === 'number' ? b.lat.toFixed(5) + ', ' + b.lng.toFixed(5) : '', b.acc ? '±' + Math.round(b.acc) + ' m' : '', b.src && b.src !== 'gps' ? b.src : '',
                at ? (isHost ? 'fix ' : 'updated ') + at : '', isHost && b.address ? b.address : '', via ? 'through ' + via : ''].filter(Boolean)
  const sens = isHost ? `<button onclick="sensorsOf('${esc(b.name)}')">Sensors</button>` : via ? `<button onclick="sensorsAt('${esc(via)}')">Sensors of its node</button>` : ''
  return `<b>${esc(b.name)}</b>${b.team ? ' · ' + esc(b.team) : ''}<br>${esc(bits.filter(Boolean).join(' · '))}` +
    `<div class="pinfo">${esc(info.join(' · '))}</div>` + (sens ? '<div class="pbtns">' + sens + '</div>' : '') +
    (lkp ? `<br><i>last known position, ${Math.round((Date.now() - lkp.t) / 60000)} min ago</i>` : '') +
    (talkable(id, b) && !lkp ? `<br><button onclick="openPop('${esc(id)}')">Chat</button>` : '') +
    RT.fire('popup', id, b, lkp).join('')
}
function talkable(id, b) { return !!RT.me().chat && id !== S.cfg.dev && (b.kind === 'team' || b.kind === 'eoc') }
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
  mk._b = b                                             // [LEGEND_IS_THE_MAP_V1] what this marker is, for the legend
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
  if (S.on.host) for (const id in S.gps) {               // real nodes, from their GPS sensors
    const h = S.gps[id], bad = S.compliance[h.name]
    want[id] = 1; place(id, bad ? Object.assign({}, h, { kind: 'host', ooc: true, status: 'OUT OF COMPLIANCE: ' + envText(bad) }) : h, null)
  }
  RT.fire('redraw', want, place)                          // [RT_LAYERS_V1] the layers' own items (field marks, requests)
  for (const id in S.markers) if (!want[id]) { map.removeLayer(S.markers[id]); delete S.markers[id] }
  legend()
  mapSays()
  // hosts: who is connected through which node
  for (const id in S.links) { map.removeLayer(S.links[id]); delete S.links[id] }
  if (S.on.host) for (const id in S.pos) {
    const b = S.pos[id], h = b && b.via && (S.pos[b.via] || Object.values(S.gps).find(g => g.address === b.via))
    if (h && want[id] && typeof h.lat === 'number') S.links[id] = L.polyline([[b.lat, b.lng], [h.lat, h.lng]], { color: '#90a4ae', weight: 1, opacity: .6, dashArray: '3 4' }).addTo(map)
  }
}
// [RT_MAP_SAYS_V1] what the map holds and why anything is not on it, said whenever that changes (Logcat tag OpenEOC; the
// Windows app's window): how many positions the world gave, by kind; how many are drawn; which kinds this view has off;
// which teams are not shown; how many have no coordinates; and this device's own state
let mapSaid = ''
function mapSays() {
  const all = Object.entries(S.pos).filter(([, b]) => b), by = {}
  for (const [, b] of all) by[b.kind || '?'] = (by[b.kind || '?'] || 0) + 1
  const off = Object.keys(by).filter(k => k !== 'team' && !(S.on[k] !== undefined ? S.on[k] : S.on.team))
  const teamsHidden = [...new Set(all.filter(([, b]) => b.kind === 'team' && !shown(b)).map(([, b]) => String(b.team || '?')))]
  const noPos = all.filter(([, b]) => typeof b.lat !== 'number').length
  const viewOnly = all.filter(([, b]) => !visibleInView(b)).length
  const me = S.pos[S.cfg.dev]
  const line = '[RT_MAP_SAYS_V1] ' + all.length + ' positions from the world (' + (Object.entries(by).map(([k, n]) => k + ' ' + n).join(', ') || 'none') + '); ' +
    Object.keys(S.markers).length + ' on the map' + (off.length ? '; kinds off in this view: ' + off.join(', ') : '') +
    (teamsHidden.length ? '; teams not shown: ' + teamsHidden.join(', ') : '') + (viewOnly ? '; ' + viewOnly + ' not for this view' : '') +
    (noPos ? '; ' + noPos + ' without coordinates' : '') + '; this device: ' +
    (me ? (typeof me.lat === 'number' ? 'at ' + me.lat.toFixed(5) + ',' + me.lng.toFixed(5) : 'present, no position yet') : (myFix ? 'has a fix, not in the world yet' : 'no position (no fix, no address)'))
  if (line === mapSaid) return
  mapSaid = line; console.log(line)
  if (window.rtNative && window.rtNative.log) try { window.rtNative.log(line) } catch (e) { }
}
function envText(c) { return c.sensors.map(x => Object.entries(x.readings).map(([k, v]) => k + ' ' + v).join(', ')).join('; ') }
function visibleInView(b) { const o = RT.me().only; return !o || o.includes(b.kind) }
function layersPanel() {
  const p = $('#layers'), r = RT.me(); p.innerHTML = ''
  const kinds = Object.keys(KINDS).filter(k => !r.only || r.only.includes(k) || k === 'host')
  if (!r.only) {                                  // the teams, one by one: a member adds others from here
    const h = document.createElement('div'); h.className = 'lh'; h.textContent = 'Teams'; p.appendChild(h)
    for (const t of knownTeams()) {
      const l = document.createElement('label')
      l.innerHTML = `<input type="checkbox" ${S.teamsOn.has(t) ? 'checked' : ''}> <i style="background:${teamColor(t)}"></i> ${esc(cap(t))}`
      l.querySelector('input').onchange = (e) => { if (e.target.checked) { S.teamsOn.add(t); S.teamsOff.delete(t) } else { S.teamsOn.delete(t); S.teamsOff.add(t) } redraw() }
      p.appendChild(l)
    }
    const h2 = document.createElement('div'); h2.className = 'lh'; h2.textContent = 'Show'; p.appendChild(h2)
  }
  for (const k of kinds.filter(k => k !== 'team').concat(!r.only ? ['lkp'] : [])) {
    const l = document.createElement('label')
    l.innerHTML = `<input type="checkbox" ${S.on[k] ? 'checked' : ''}> ${k === 'lkp' ? 'Last known positions' : KINDS[k].label}`
    l.querySelector('input').onchange = (e) => { S.on[k] = e.target.checked; if (k === 'host' && S.on.host) send({ op: 'hosts' }); redraw() }
    p.appendChild(l)
  }
}
$('#layersBtn').onclick = () => { if ($('#layers').hidden) only('#layers'); $('#layers').hidden = !$('#layers').hidden }
// [MAP_KINDS_DISCOVERED_V1] the legend is what is on this network: its teams, then each other kind present (not a fixed list)
// [LEGEND_IS_THE_MAP_V1] (John 2026-11-09: "The Teams legend at the lower left corner is not always properly updated") the
// legend is what the map draws now, made again on every redraw: the teams with a marker on it (a last known position
// too), then each other kind with one. Measured on r55, it was every team this page had ever heard of: a team gone, one
// switched off in Layers, and on a member's map the other teams it does not draw, all stayed in it.
function legend() {
  if (!map) return
  const r = RT.me(), teams = new Set(), kinds = new Set()
  for (const id in S.markers) { const b = S.markers[id]._b; if (!b || !b.kind) continue
    if (b.kind === 'team') teams.add(String(b.team || '').toLowerCase()); else if (b.kind !== 'host') kinds.add(b.kind) }
  const html = r.legend !== undefined ? r.legend : [...teams].filter(Boolean).sort().map(t => `<span><i style="background:${teamColor(t)}"></i>${esc(cap(t))}</span>`).join('') +
    [...kinds].sort().map(k => { const d = KINDS[k] || {}; return `<span><i style="background:${d.color || '#888'}${d.shape === 'sq' ? ';border-radius:2px' : ''}"></i>${esc(d.label || k)}</span>` }).join('')
  if ($('#legend').innerHTML !== html) $('#legend').innerHTML = html
}

// ------------------------------------------------------------ chat
function teamsHere() { return RT.me().teams === 'all' ? knownTeams().map(cap) : (S.team ? [S.team] : []) }
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
  const t = m.to.startsWith('team:') ? m.to.slice(5) : '', tc = t ? teamColor(t) : ''
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
  n.onclick = () => { S.tab = m.to.startsWith('dev:') ? 'dev' : m.to; only('#chat'); $('#chat').hidden = false; tabs(); readAll(); n.remove() }
  $('#notices').appendChild(n); setTimeout(() => n.remove(), 12000)
  if (window.rtNative && window.rtNative.notice) try { window.rtNative.notice(m.name, m.text) } catch (e) { }
}
function readAll() { unread = 0; $('#chatBtn').textContent = 'Chat'; $('#chatBtn').classList.remove('hot') }
$('#chatBtn').onclick = () => { if ($('#chat').hidden) only('#chat'); $('#chat').hidden = !$('#chat').hidden; if (!$('#chat').hidden) readAll() }
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
  only('#pop'); p.hidden = false; popLog(); map.closePopup()
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

// ------------------------------------------------------------ sheets, the device's own position, its status
let myFix = null
window.rtFix = (lat, lng) => { myFix = { lat, lon: lng } }          // the phone's GPS (the native side calls it)
// [ONE_DIALOG_V1] (John 2026-11-09: "If one dialog (Layers, Sensors, etc.) is on screen and another is chosen, the second
// replaces the first") Layers, the chat, a sheet (Sensors, Diag, Mission, the radio log, a layer's screen) and the person
// pop-up: opening one closes whichever other is open
function only(keep) {
  for (const sel of ['#layers', '#chat', '#sheet', '#pop']) if (sel !== keep && !$(sel).hidden) {
    $(sel).hidden = true
    if (sel === '#sheet') { S.sheet = ''; S.misEditing = false }
    if (sel === '#pop') popId = ''
  }
}
function sheet(name, title, html) {
  only('#sheet'); S.sheet = name; $('#sheet .sh-title').textContent = title; $('#sheet .sh-body').innerHTML = html; $('#sheet').hidden = false
}
$('#sheet .sh-close').onclick = () => { $('#sheet').hidden = true; S.sheet = '' }
window.showOnMap = (lat, lng) => { RT.fire('showOnMap'); map.setView([lat, lng], 15) }
function opts(list) { return list.map(x => `<option>${esc(x)}</option>`).join('') }
/** "here": this device's location; without one, the centre of the map */
function here() { if (myFix) return { lat: myFix.lat, lng: myFix.lon }; const c = map.getCenter(); return { lat: c.lat, lng: c.lng, center: true } }
/** this member's status (available, en route ...): part of its own presence; the screen that set it draws it again */
window.setStatus = (st) => { S.myStatus = st; send({ op: 'status', status: st }); refreshSheet('status') }

// ------------------------------------------------------------ [RT_SENSORS_V1] every node's sensors, as they file them
window.sensorsOf = (host) => { map.closePopup(); sheet('sens', 'Sensors: ' + host, '<div class="meta">asking…</div>'); send({ op: 'sensors', host }) }
window.sensorsAt = (addr) => { map.closePopup(); sheet('sens', 'Sensors', '<div class="meta">finding the node at ' + esc(addr) + '…</div>'); send({ op: 'sensorsAt', addr }) }
$('#sensBtn').onclick = () => { sheet('sens', 'Sensors', '<div class="meta">asking the nodes…</div>'); send({ op: 'sensorHosts' }) }
function sensorHosts(h) {
  const names = Object.keys(h).sort()
  sheet('sens', 'Sensors: the nodes', `<div class="meta">Every node that reports its System sensor. Pick one.</div><div class="sens">` +
    names.map(n => `<button type="button" onclick="send({op:'sensors',host:'${esc(n)}'})"><b>${esc(n)}</b> <span class="meta">${esc(h[n])}</span></button>`).join('') +
    `</div><button type="button" onclick="send({op:'sensorHosts'})">Refresh</button>`)
  RT.sectioned('sens')
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
  S.noGpsWhy = m.why || 'This device has no GPS.'
  S.addrAsk = true                                      // asked once connected (the sheet needs the main screen)
  addressFix()                                          // connected already (a phone whose GPS went off): now
}
// [RT_AT_ITS_NODE_V1] a device with no GPS and no address given is where its node is (the node it came in through, its
// GPS sensor's fix), "src":"node", until an address is given: on everyone's map from the start, not on nobody's
function myNode() {
  const via = String(($('#connect').door || {}).value || S.cfg.door || '').replace(/^https?:\/\//, '').replace(/[:/].*$/, '')
  const nm = String(S.doorNode || '').toLowerCase()
  return Object.values(S.gps).find(g => nm && String(g.name).toLowerCase() === nm) ||
         Object.values(S.gps).find(g => via && (g.address === via || String(g.name).toLowerCase() === via.toLowerCase()))
}
// why this device is not at its node: said, not left to guess
function nodeWhy() {
  const with_ = Object.values(S.gps).map(g => g.name).join(', ') || 'none'
  return !S.hostsSaid ? 'the nodes\' GPS not asked yet'
    : !S.doorNode ? `the node at ${esc(S.cfg.door || '')} does not say which node it is (its door is older than dc-door r16), so it is not matched to a node with a GPS fix (${esc(with_)})`
    : `${esc(S.doorNode)} has no GPS fix in the nodes' GPS sensors (with a fix: ${esc(with_)})`
}
function addressSheet(why) {
  const nd = myNode()
  sheet('addr', 'Where are you?', `<form class="af mf"><div>${esc(why)} ${nd ? `Until you say, you are on everyone's map at your node, <b>${esc(nd.name)}</b> (its GPS). ` : `You are not on the map: ${nodeWhy()}. `}Enter the street address where you are (or "latitude, longitude"); it is your position on everyone's map.</div>
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
function addressFix() {
  if (!S.noGps || !S.role) return
  if (S.addr) { send({ op: 'fix', lat: S.addr.lat, lng: S.addr.lng, acc: 0, src: 'address' }); return }
  const nd = myNode()
  if (nd && typeof nd.lat === 'number' && (!S.nodeFixed || S.nodeFixed !== nd.lat + ',' + nd.lng)) {
    S.nodeFixed = nd.lat + ',' + nd.lng
    send({ op: 'fix', lat: nd.lat, lng: nd.lng, acc: 0, src: 'node' })
    status('on the map at ' + nd.name + ', by its GPS (Diag, Where am I? sets another position)')
  }
  // [RT_AT_ITS_NODE_V1] a node with a GPS fix is this device's position: nothing to ask (Diag's Where am I? still sets an
  // address); asked only when its node has no fix, once the nodes are known
  if (S.addrAsk && (S.hostsSaid || !S.on.host)) { S.addrAsk = false; if (!S.nodeFixed) addressSheet(S.noGpsWhy) }
}

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
      ${RT.me().mission ? '<div class="row"><button type="button" onclick="missionPanel(true)">Update this mission</button> <button type="button" onclick="missionPanel(true,true)">Start a new mission</button></div>' : ''}
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


// ------------------------------------------------------------ [RT_RADIO_LOG_IN_THE_APP_V1] OpenEOC's radio log, here
// What the scribes and field phones log over the radio (the radio log app, /openeoc/log.html), for this mission and its
// operational period, newest first, as it is typed. Read only: the entries and their ICS-309 numbers are the posts'.
const RL_STATUS = { urgent: 'Urgent', 'need-rest-food': 'Needs rest/food', ok: 'OK', info: 'Info' }
function radioLog() {
  const m = S.rlog || {}, rows = Object.values(m.entries || {}).sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')))
  const no = (n) => n ? [n.station, n.seq].filter(Boolean).join('-') : ''
  const loc = (l) => !l ? '' : typeof l === 'string' ? l : l.text || (typeof l.lat === 'number' ? l.lat.toFixed(4) + ', ' + (l.lng ?? l.lon).toFixed(4) : '')
  sheet('rlog', 'Radio log — op period ' + esc(m.op || '1'), (rows.length ? `<table class="rl"><thead><tr><th>Time</th><th>No.</th><th>Call sign</th><th>Status</th><th>Location</th><th>Notes</th><th>Post</th></tr></thead><tbody>` +
    rows.map(r => `<tr${r.status === 'urgent' ? ' class="urg"' : ''}><td>${r.date ? new Date(r.date).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}</td><td>${esc(no(r.msgNo))}</td><td><b>${esc(r.callsign)}</b></td>
      <td>${esc(RL_STATUS[r.status] || r.status || '')}</td><td>${esc(loc(r.location))}</td><td>${esc(r.notes || '')}</td><td>${esc(r.by || '')}</td></tr>`).join('') + '</tbody></table>'
    : '<div class="meta">No radio log entries in this operational period yet. Scribes log them in the Radio log app (/openeoc/ on the node).</div>'))
}
$('#logBtn').onclick = radioLog


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
  return RT.fire('back').some(Boolean)            // a layer's own (the public's Home)
}
