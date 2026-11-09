// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// admin.js -- [ADMIN_APP_V1] the admin app's screen. Everything it shows comes from AdminCore's lines; a change is
// one "apply" line with the whole configuration and the new passwords (the core makes their verifiers).
'use strict'
const $ = (s) => document.querySelector(s)
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const send = (o) => window.rtNative.send(JSON.stringify(o))
const A = { cur: null, edit: null, pw: {}, dirty: false, sent: 0, state: null }

// [RT_LAYERS_V1] a layer's own panel in the admin app: RTA.panel(title, fn) -- fn(state) is its section's html, drawn
// again with every state the core says. An admin app is this page and the admin parts of its layers (cfg.layers;
// ui-admin/layers/<name>.js, for a layer that has one).
const RTA = { panels: [], panel(title, fn) { RTA.panels.push({ title, fn }) } }
window.RTA = RTA
function panels() {
  for (const [i, p] of RTA.panels.entries()) {
    let s = document.getElementById('rta-' + i)
    if (!s) { s = document.createElement('section'); s.id = 'rta-' + i; document.querySelector('#main main').appendChild(s) }
    s.innerHTML = '<h2>' + esc(p.title) + '</h2>' + (p.fn(A.state) || '')
  }
}
window.rtInit = function (cfg) {
  cfg = cfg || {}
  for (const n of (cfg.layers || []).filter(n => /^[a-z0-9_-]+$/.test(n))) {
    const sc = document.createElement('script'); sc.src = 'layers/' + n + '.js'; sc.onerror = () => sc.remove()   // a layer with no admin part
    document.body.appendChild(sc)
  }
  const f = $('#connect'); f.door.value = cfg.door || ''; f.app.value = cfg.app || 'openeoc'; f.user.value = cfg.user || ''
  setTimeout(() => { $('#splash').classList.add('gone'); f.hidden = false; (f.user.value ? f.password : f.door).focus() }, 1200)
}
$('#connect').onsubmit = (e) => {
  e.preventDefault(); const f = e.target
  $('#cstatus').textContent = 'connecting… (checking the admin password takes a moment)'
  send({ op: 'connect', door: f.door.value.trim(), app: f.app.value.trim(), user: f.user.value.trim(), password: f.password.value })
}
window.rtDeliver = function (line) {
  const m = typeof line === 'string' ? JSON.parse(line) : line
  if (m.ev === 'status') { $('#status').textContent = m.text; if (!$('#connect').hidden) $('#cstatus').textContent = m.text }
  else if (m.ev === 'error') { $('#status').textContent = '⚠ ' + m.text; if (!$('#connect').hidden) $('#cstatus').textContent = m.text }
  else if (m.ev === 'connected') {
    $('#connect').hidden = true; $('#main').hidden = false; $('#dot').classList.add('up')
    $('#who').textContent = m.app + ' · admin ' + m.user + ' · through ' + m.door
    $('#status').textContent = 'connected (' + m.build + ')'
    A.door = m.door; A.app = m.app; send({ op: 'saves' })
  } else if (m.ev === 'state') state(m)
  else if (m.ev === 'onNow') { A.now = m; if (A.last) groups(A.last) }
  else if (m.ev === 'saves') saves(m)
  else if (m.ev === 'restored') restored(m)
  else if (m.ev === 'saved') $('#status').textContent = 'saved ' + m.path
}

// ------------------------------------------------------------ the configuration being edited
// [TEAMS_AUTHORIZED_V1] (John 2026-11-09: "we are authorizing Teams, and that's the trust boundary. EOC allocates Team login
// and PW, lead connects to the EOC LAN and claims the Team on their Pi or phone"; "The EOC does not allocate" categories:
// "Categories are added by virtue of their apps being connected to the EOC") The configuration keeps its shape -- roles,
// users with verifiers -- and this screen is two things in it:
//   a Team: a role with its own RAM, and its login: a user of the Team's name, its lead, the role the Team. Its category
//     is the claiming app's (the door's, at the claim); one a configuration already had is shown, and kept.
//   an EOC user: a user of the EOC's role (the role in category EOC; made, as "eoc", with the first EOC user).
// Gone from here: categories, the groups in them and their user lists, and the EOC's one teams password ("join").
const EOC_CAT = 'EOC'
// [COMMAND_CENTER_V1] the category's name in the configuration stays EOC; on the screen it is the Command Center
const catName = (c) => c === EOC_CAT ? 'Command Center' : c
function fromCurrent(c) {
  const rams = new Set(c.rams || [])
  const roles = {}
  for (const [r, x] of Object.entries(c.roles || {})) roles[r] = { team: rams.has(r), def: !!(x || {}).default, cat: (x || {}).category || '' }
  const users = {}
  for (const [u, x] of Object.entries(c.users || {})) users[u] = { role: (x || {}).role || '', lead: !!(x || {}).lead }
  const extra = [...rams].filter(r => r !== 'general' && !roles[r])
  const sel = {}; for (const [r, s] of Object.entries(c.select || {})) sel[r] = { need: ((s || {}).need || []).join(', '), rank: ((s || {}).rank || []).join(', ') }   // [APP_SELECTION_V1]
  return { roles, users, login: !!c.login, partition: c.partition === 'fail' ? 'fail' : 'relocate', extra: extra.join(', '), sel }
}
function toConfig(e) {
  const rams = ['general', ...Object.entries(e.roles).filter(([, x]) => x.team).map(([r]) => r),
    ...e.extra.split(/[,\s]+/).map(s => s.trim().toLowerCase()).filter(Boolean)]
  const roles = {}; for (const [r, x] of Object.entries(e.roles)) roles[r] = { default: x.def, category: x.cat }
  const users = {}; for (const [u, x] of Object.entries(e.users)) users[u] = { role: x.role, lead: !!x.lead }
  const keys = (t) => t.split(/[,\s]+/).map(s => s.trim()).filter(Boolean)
  const select = {}; for (const [r, s] of Object.entries(e.sel || {})) select[r] = { need: keys(s.need), rank: keys(s.rank) }
  const categories = [...new Set(Object.values(e.roles).map(x => x.cat).filter(Boolean))]   // the ones the roles have, nothing kept apart
  return { rams: [...new Set(rams)], partition: e.partition, login: e.login, roles, categories, users, select }
}
const eocRoles = (e) => Object.keys(e.roles).filter(r => e.roles[r].cat === EOC_CAT)
const teamsOf = (e) => Object.keys(e.roles).filter(r => e.roles[r].cat !== EOC_CAT).sort()
const loginOf = (e, r) => e.users[r] && e.users[r].role === r ? r : (Object.entries(e.users).find(([, x]) => x.role === r && x.lead) || [''])[0]
function state(m) {
  A.state = m; A.cur = m.current
  if (!A.dirty) { A.edit = fromCurrent(A.cur); A.pw = {}; draw() }
  else dirtyLine()        // the edit against the new current: an edit the members have since adopted is no change any more
  members(m); groups(m); panels()
}
// [GROUPS_NOW_V1] each group (a role), its tab, where its lead placed it, where its RAM is served now, its lead(s)
function groups(m) {
  A.last = m
  const c = m.current || {}, served = m.served || {}, places = c.places || {}, rams = new Set(c.rams || [])
  // [ADMIN_ON_NOW_V1] each device on now, in the group it says (a team view's team, a unit's unit), any case
  // [CHILDREN_JOIN_V1] the teams that joined the EOC by its old teams password (named by their joiners) are groups too
  const kids = m.children || {}, kidOf = {}
  for (const [n, k] of Object.entries(kids)) kidOf[n.toLowerCase()] = Object.assign({ name: n }, k || {})
  const groupOf = (x) => (c.roles || {})[x] !== undefined || kidOf[x] ? x : ''
  const now = (A.now || {}).who || {}, inGroup = {}, others = [], devsIn = {}, devsOut = []
  for (const [d, w] of Object.entries(now)) {
    const g = [w.team, w.unit].map(x => String(x || '').toLowerCase()).find(x => x && groupOf(x))
    if (g) (devsIn[g] = devsIn[g] || []).push([d, w]); else devsOut.push([d, w])
    const tag = `${esc(w.name || d)} <span class="mu">${esc(w.kind || '')}${w.via ? ' via ' + esc(w.via) : ''}</span>`
    if (g) (inGroup[g] = inGroup[g] || []).push(tag); else others.push(tag + (w.team || w.unit ? ` <span class="mu">(says ${esc(w.team || w.unit)}: no such group)</span>` : ''))
  }
  const leads = {}; for (const [u, x] of Object.entries(c.users || {})) if ((x || {}).lead) (leads[x.role] = leads[x.role] || []).push(u)
  const catSay = (x) => catName((x || {}).category) || 'its app\'s, at its claim'
  const rows = Object.entries(c.roles || {}).sort((a, b) => catSay(a[1]).localeCompare(catSay(b[1])) || a[0].localeCompare(b[0]))
  $('#groups').innerHTML = rows.map(([r, x]) => {
    const p = places[r] || {}, own = rams.has(r)
    const lives = !own ? '<span class="mu">no RAM of its own (the general RAM)</span>'
      : p.at === 'here' ? `on its lead's device <span class="mu">${esc(p.guid || '')}, placed by ${esc(p.user || '')}</span>`
      : p.at === 'databasehost' ? `on the SystemRam <span class="mu">placed by ${esc(p.user || '')}</span>` : 'on the SystemRam <span class="mu">(the app\'s selection)</span>'
    const at = !own ? '' : served[r] ? `<span class="ok">${esc(served[r])}</span>` : (p.at === 'here' ? '<span class="mu">on the lead\'s device (not a member)</span>' : '<span class="bad">nobody</span>')
    return `<tr><td><b>${esc(r)}</b></td><td>${esc(catSay(x))}</td><td>${lives}</td><td>${at}</td><td>${esc((leads[r] || []).join(', ')) || '<span class="mu">none</span>'}</td>
      <td>${(inGroup[r] || []).join('<br>') || '<span class="mu">nobody</span>'}</td></tr>`
  }).join('') + Object.entries(kidOf).filter(([k]) => (c.roles || {})[k] === undefined).sort().map(([k, x]) =>
    `<tr><td><b>${esc(x.name)}</b> <span class="mu">joined</span></td><td>Teams</td><td>on its lead's phone <span class="mu">joined the Command Center ${x.at ? esc(new Date(x.at * 1000).toLocaleString()) : ''}${x.from ? ' from ' + esc(x.from) : ''}</span></td>
      <td><span class="mu">its lead's phone</span></td><td>${esc(x.name)} <span class="mu">(by the old teams password)</span></td><td>${(inGroup[k] || []).join('<br>') || '<span class="mu">nobody</span>'}</td></tr>`).join('')
    || '<tr><td colspan="6" class="mu">no groups</td></tr>'
  tree(c, kidOf, leads, devsIn, devsOut)
  const n = A.now
  $('#onNow').innerHTML = !n ? 'Who is on now: asking…' : !n.mission ? 'Who is on now: no mission on this network yet (the first app to connect starts Standing operations)'
    : `Mission <b>${esc(n.mission)}</b> · ${Object.keys(now).length} on now` + (others.length ? ' · in no group: ' + others.join(' · ') : '')
}
// [ADMIN_TREE_V1] (John 2026-11-06: "Admin apps should show connected children of all types") who is connected, as a
// tree: the mission; each category; each group in it (the Teams and the teams that joined), its kind, its lead; each
// device on now in it, of whatever kind it says it is (a team member, an EOC desk, a unit, a plug-in's); then anyone on
// now in no group. Kinds are what the devices say, not a list kept here.
function tree(c, kidOf, leads, devsIn, devsOut) {
  const n = A.now || {}, dev = ([d, w]) => `<li><b>${esc(w.name || d)}</b> <span class="mu">${esc(w.kind || '?')}${w.via ? ' via ' + esc(w.via) : ''}${w.status ? ' · ' + esc(w.status) : ''}</span></li>`
  const cats = {}, catOf = (x) => (x || {}).category || 'Not claimed yet'
  for (const [r, x] of Object.entries(c.roles || {})) (cats[catOf(x)] = cats[catOf(x)] || []).push([r, r, 'group', (leads[r] || []).join(', ')])
  for (const [k, x] of Object.entries(kidOf)) if ((c.roles || {})[k] === undefined) (cats.Teams = cats.Teams || []).push([k, x.name, (x.kind || 'team') + ', joined', x.name])
  const body = Object.keys(cats).sort().map(cat => `<li><b>${esc(catName(cat))}</b><ul>` + cats[cat].sort((a, b) => a[1].localeCompare(b[1])).map(([k, name, kind, lead]) => {
    const ds = devsIn[k] || []
    return `<li><b>${esc(name)}</b> <span class="mu">${esc(kind)} · lead ${esc(lead || 'none')} · ${ds.length} on now</span>${ds.length ? '<ul>' + ds.map(dev).join('') + '</ul>' : ''}</li>`
  }).join('') + '</ul></li>').join('')
  const out = devsOut.length ? `<li><b>In no group</b><ul>${devsOut.map(dev).join('')}</ul></li>` : ''
  $('#onTree').innerHTML = `<ul class="tree"><li><b>${esc(n.mission ? 'Mission ' + n.mission : 'No mission yet')}</b> <span class="mu">${Object.keys(n.who || {}).length} on now</span><ul>${body}${out}</ul></li></ul>`
}
// [TEAMS_AUTHORIZED_V1] the page: the Teams, the EOC users
const isNew = (u) => !(A.cur.users || {})[u]
function draw() {
  const e = A.edit
  $('#teams').innerHTML = teamsOf(e).map(r => {
    const x = e.roles[r], login = loginOf(e, r)
    const also = Object.entries(e.users).filter(([u, y]) => y.role === r && u !== login).map(([u]) => u)   // a configuration made before Teams: its other users
    const isN = !(A.cur.roles || {})[r]
    return `<tr><td><b>${esc(r)}</b>${isN ? '<span class="pill">new</span>' : ''}${login && login !== r ? ` <span class="mu">login ${esc(login)}</span>` : ''}
        ${also.length ? `<div class="mu also">also signed in by ${also.map(u => `${esc(u)} <button type="button" class="x" data-del-user="${esc(u)}" title="remove ${esc(u)}">✕</button>`).join(' ')}</div>` : ''}</td>
      <td>${x.cat ? esc(catName(x.cat)) : '<span class="mu">its app\'s, when it is claimed</span>'}</td>
      <td>${login ? `<input type="password" data-pw="${esc(login)}" value="${esc(A.pw[login] || '')}" placeholder="${isNew(login) ? 'required' : 'keep'}" autocomplete="new-password">`
                  : `<input type="password" data-tpw="${esc(r)}" placeholder="give it a password: it has no login" autocomplete="new-password">`}</td>
      <td class="c"><button type="button" data-qr="${esc(login)}" ${!login || isNew(login) ? 'disabled title="apply first"' : ''}>QR</button></td>
      <td class="c"><button type="button" class="x" data-del-team="${esc(r)}" title="remove Team ${esc(r)}">✕</button></td></tr>`
  }).join('') || '<tr><td colspan="5" class="mu">no Teams yet: add one below</td></tr>'
  const eu = Object.entries(e.users).filter(([, x]) => eocRoles(e).includes(x.role)).map(([u]) => u).sort()
  $('#eocUsers').innerHTML = eu.map(u => `<tr><td><b>${esc(u)}</b>${isNew(u) ? '<span class="pill">new</span>' : ''}</td>
      <td><input type="password" data-pw="${esc(u)}" value="${esc(A.pw[u] || '')}" placeholder="${isNew(u) ? 'required' : 'keep'}" autocomplete="new-password"></td>
      <td class="c"><button type="button" data-qr="${esc(u)}" ${isNew(u) ? 'disabled title="apply first"' : ''}>QR</button></td>
      <td class="c"><button type="button" class="x" data-del-user="${esc(u)}" title="remove ${esc(u)}">✕</button></td></tr>`).join('') || '<tr><td colspan="4" class="mu">no Command Center users yet</td></tr>'
  $('#login').checked = e.login; $('#pRel').checked = e.partition === 'relocate'; $('#pFail').checked = e.partition === 'fail'
  $('#sel').innerHTML = Object.entries(e.sel).map(([r, s]) => `<tr><td><b>${esc(r)}</b></td>
    <td><input type="text" data-sel="${esc(r)}" data-k="need" value="${esc(s.need)}" placeholder="e.g. mysql_running"></td>
    <td><input type="text" data-sel="${esc(r)}" data-k="rank" value="${esc(s.rank)}" placeholder="e.g. mem_total_kb, -disk_fsync_ms"></td>
    <td class="c"><button type="button" class="x" data-del-sel="${esc(r)}" title="remove">✕</button></td></tr>`).join('') || '<tr><td colspan="4" class="mu">the default: the most memory, then the most cores</td></tr>'
  dirtyLine()
}
function changes() {
  if (!A.cur) return []
  const a = toConfig(fromCurrent(A.cur)), b = toConfig(A.edit), out = []
  const eocA = new Set(Object.keys(a.roles).filter(r => a.roles[r].category === EOC_CAT)), eocB = new Set(Object.keys(b.roles).filter(r => b.roles[r].category === EOC_CAT))
  for (const r of Object.keys(b.roles)) if (!a.roles[r] && !eocB.has(r)) out.push('Team ' + r)
  for (const r of Object.keys(a.roles)) if (!b.roles[r]) out.push((eocA.has(r) ? 'no Command Center role ' : 'no Team ') + r)
  for (const r of Object.keys(b.roles)) if (a.roles[r] && a.roles[r].default !== b.roles[r].default) out.push(r + (b.roles[r].default ? ' with no login' : ' at login only'))
  if ((A.cur || {}).join) out.push("no Command Center teams password (each Team has its own)")   // [TEAMS_AUTHORIZED_V1] the old shared one goes at the next Apply
  const ar = new Set(a.rams), br = new Set(b.rams)
  for (const r of br) if (!ar.has(r) && !(b.roles[r] && !a.roles[r])) out.push('RAM ' + r)
  for (const r of ar) if (!br.has(r) && !(a.roles[r] && !b.roles[r])) out.push('no RAM ' + r)
  for (const u of Object.keys(b.users)) if (!a.users[u]) { if (!(b.users[u].role === u && !a.roles[u])) out.push((eocB.has(b.users[u].role) ? 'Command Center user ' : 'user ') + u) }
    else if (a.users[u].role !== b.users[u].role) out.push(u + ' → ' + b.users[u].role)
  for (const u of Object.keys(b.users)) if (a.users[u] && !!a.users[u].lead !== !!b.users[u].lead) out.push(u + (b.users[u].lead ? ' leads ' : ' no longer leads ') + b.users[u].role)
  for (const u of Object.keys(a.users)) if (!b.users[u] && b.roles[a.users[u].role]) out.push('remove ' + (eocA.has(a.users[u].role) ? 'Command Center user ' : 'user ') + u)
  for (const u of Object.keys(A.pw)) if (A.pw[u] && a.users[u]) out.push('new password for ' + u)
  if (a.login !== b.login) out.push(b.login ? 'login required' : 'login not required')
  if (a.partition !== b.partition) out.push('partition: ' + b.partition)
  for (const r of new Set([...Object.keys(a.select), ...Object.keys(b.select)]))   // [APP_SELECTION_V1]
    if (JSON.stringify(a.select[r] || null) !== JSON.stringify(b.select[r] || null)) out.push(b.select[r] ? 'selection of ' + r + ': need ' + (b.select[r].need.join(',') || 'none') + ', rank ' + (b.select[r].rank.join(',') || 'the default') : 'no selection of ' + r)
  return out
}
function missing() { return Object.keys(A.edit.users).filter(u => !(A.cur.users || {})[u] && !(A.restoredUsers || {})[u] && !A.pw[u]) }   // a restore brings its users' verifiers
function dirtyLine() {
  const c = changes(), miss = missing()
  A.dirty = c.length > 0
  $('#changes').className = A.dirty ? 'dirty' : ''
  $('#changes').textContent = !A.dirty ? 'No changes.' : c.length + ' change(s): ' + c.join(' · ') + (miss.length ? ' — a password is required for ' + miss.join(', ') : '')
  $('#apply').disabled = !A.dirty || miss.length > 0
}
document.addEventListener('change', (ev) => {
  const t = ev.target, e = A.edit; if (!e) return
  if (t.id === 'login') e.login = t.checked
  else if (t.name === 'part') e.partition = t.value
  else if (t.dataset.sel) e.sel[t.dataset.sel][t.dataset.k] = t.value
  else if (t.dataset.tpw !== undefined) {                 // a Team with no login (a configuration made before Teams): its login now
    const r = t.dataset.tpw; if (!t.value) return
    if (e.users[r]) { $('#teamSays').textContent = r + ' is a user already, of ' + e.users[r].role; return }
    e.users[r] = { role: r, lead: true }; A.pw[r] = t.value
  } else return
  draw()
})
document.addEventListener('input', (ev) => { if (ev.target.dataset.pw !== undefined) { A.pw[ev.target.dataset.pw] = ev.target.value; dirtyLine() } })
document.addEventListener('click', (ev) => {
  const t = ev.target, e = A.edit; if (!e) return
  if (t.dataset.delTeam) { const r = t.dataset.delTeam; delete e.roles[r]; for (const [u, x] of Object.entries(e.users)) if (x.role === r) { delete e.users[u]; delete A.pw[u] } draw() }
  else if (t.dataset.delUser) { delete e.users[t.dataset.delUser]; delete A.pw[t.dataset.delUser]; draw() }
  else if (t.dataset.qr) showQr(t.dataset.qr)
  else if (t.dataset.delSel) { delete e.sel[t.dataset.delSel]; draw() }
})
$('#addSel').onclick = () => {
  const r = $('#newSel').value.trim().toLowerCase()
  if (r !== '*' && !/^[a-z0-9_-]{1,40}$/.test(r)) { $('#status').textContent = 'a RAM name (letters, digits, _ and -), or *'; return }
  if (!A.edit.sel[r]) A.edit.sel[r] = { need: '', rank: '' }; $('#newSel').value = ''; draw()
}
const okUser = (u) => /^[A-Za-z0-9._@-]{1,64}$/.test(u)
// [TEAMS_AUTHORIZED_V1] a Team: its login (the Team's name: its role, its own RAM, its lead's user) and its password
$('#addTeam').onclick = () => {
  const e = A.edit, r = $('#newTeam').value.trim().toLowerCase(), pw = $('#newTeamPw').value, say = (t) => { $('#teamSays').textContent = t }
  if (!/^[a-z0-9_-]{1,40}$/.test(r) || r === 'general') return say("a Team's login is letters, digits, _ and - (not general)")
  if (e.roles[r]) return say(r + ' is a Team already')
  if (e.users[r]) return say(r + ' is a user already, of ' + e.users[r].role)
  if (!pw) return say(r + ': give its password')
  e.roles[r] = { team: true, def: false, cat: '' }; e.users[r] = { role: r, lead: true }; A.pw[r] = pw
  $('#newTeam').value = ''; $('#newTeamPw').value = ''; say(''); draw()
}
// [TEAMS_AUTHORIZED_V1] an EOC user: a user of the EOC's role (made, as eoc, with the first one)
$('#addEoc').onclick = () => {
  const e = A.edit, u = $('#newEoc').value.trim(), pw = $('#newEocPw').value, say = (t) => { $('#eocSays').textContent = t }
  if (!okUser(u)) return say('a user name is letters, digits and . _ @ -')
  if (e.users[u]) return say(u + ' is a user already, of ' + e.users[u].role)
  if (e.roles[u.toLowerCase()] && !eocRoles(e).includes(u.toLowerCase())) return say(u + ' is a Team: give the user another name')
  if (!pw) return say(u + ': give its password')
  let er = eocRoles(e)[0]
  if (!er) { er = 'eoc'; if (e.roles[er]) return say('there is a Team named eoc: the Command Center role cannot be made'); e.roles[er] = { team: false, def: false, cat: EOC_CAT } }
  e.users[u] = { role: er, lead: false }; A.pw[u] = pw
  $('#newEoc').value = ''; $('#newEocPw').value = ''; say(''); draw()
}
document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && !$('#qr').hidden) $('#qr').hidden = true })
$('#revert').onclick = () => { A.dirty = false; A.edit = fromCurrent(A.cur); A.pw = {}; draw() }
$('#reload').onclick = () => send({ op: 'refresh' })
$('#apply').onclick = () => {
  const pw = {}; for (const [u, p] of Object.entries(A.pw)) if (p) pw[u] = p
  send({ op: 'apply', config: toConfig(A.edit), passwords: pw })
  A.dirty = false; A.pw = {}; A.restoredUsers = {}; $('#apply').disabled = true; $('#changes').textContent = 'Sent: waiting for the members…'
}

// ------------------------------------------------------------ the members
// two members' configurations are the same when what clients see is the same (each install line has its own seq)
function sig(c) { const u = {}; for (const [k, x] of Object.entries(c.users || {})) u[k] = [(x || {}).role, !!(x || {}).lead]  // (not the verifier: each install line salts its own)
  return JSON.stringify([[...(c.rams || [])].sort(), c.partition, !!c.login, Object.entries(c.roles || {}).sort().map(([r, x]) => [r, !!(x || {}).default, (x || {}).category || '']), Object.entries(u).sort(),
    Object.entries(c.select || {}).sort().map(([r, s]) => [r, (s || {}).need || [], (s || {}).rank || []])]) }
function members(m) {
  const ap = {}; for (const a of m.applied || []) ap[a.guid] = a
  const sent = m.sent || 0, top = (m.current || {}).seq || 0
  $('#members').innerHTML = (m.configs || []).sort((a, b) => String(a.member).localeCompare(String(b.member))).map(c => {
    const a = ap[c.guid]
    let ans = '<span class="mu">—</span>'
    if (a && (!sent || a.seq >= sent)) ans = a.ok ? '<span class="ok">adopted</span>' : `<span class="bad">refused</span> <span class="mu">${esc(a.why)}</span>`
    else if (sent) ans = '<span class="wait">waiting…</span>'
    const cur = sig(c) === sig(m.current || {}) ? '<span class="ok">current</span>' : `<span class="wait">differs</span>`
    return `<tr><td><b>${esc(c.member || c.guid)}</b><div class="mu" style="font-size:12px">${esc(c.guid)}</div></td><td class="hide-s">${esc(c.addr || '')}</td>
      <td>${cur} <span class="mu">${(c.rams || []).length} RAMs · ${Object.keys(c.roles || {}).length} roles · ${Object.keys(c.users || {}).length} users${c.admin && c.admin.check ? '' : ' · <span class="bad">no admin key</span>'}</span></td><td>${ans}</td></tr>`
  }).join('') || '<tr><td colspan="4" class="mu">no member has written the configuration</td></tr>'
  const sv = Object.entries(m.served || {})
  $('#served').textContent = sv.length ? 'Served now (as this node sees it): ' + sv.map(([r, w]) => r + ' at ' + w).join(' · ') : 'No RAM of the app is served now, as this node sees it.'
  if (sent && (m.applied || []).filter(a => a.seq >= sent).length && !A.dirty) {
    const ok = (m.applied || []).filter(a => a.seq >= sent && a.ok).length, n = (m.configs || []).length
    $('#changes').textContent = `Configuration ${sent}: adopted by ${ok} of ${n} member(s).`
  }
}

// ------------------------------------------------------------ [ENROLL_V1] a user's enrollment QR (frogqr.js)
// The door this admin is connected through is where the phone enrolls, unless the admin says another address reaches it.
function showQr(u) {
  A.qrUser = u
  if (!$('#qrDoor').value) $('#qrDoor').value = A.door || ''
  const draw = () => {
    const d = $('#qrDoor').value.trim(), url = 'http://' + d + '/dc/ram.php'
    const line = FrogQR.enrollLine(url, A.app, u)   // [ENROLL_V1] the same line for a phone's camera and a computer's setup
    $('#qrImg').innerHTML = FrogQR.svg(line, { px: 420 }); $('#qrLine').textContent = line
    const x = (A.cur.users || {})[u] || {}      // [TEAMS_AUTHORIZED_V1] a Team's: its lead's device scans it and gives the Team's password
    // [EOC_USER_QR_V1] an EOC user's: the EOC app (or EOCAdmin) scans it, the user filled in, and gives the user's password
    $('#qrWho').textContent = (x.lead ? 'Team ' + x.role + ' · its lead scans this and gives the Team\'s password'
      : eocRoles(A.edit).includes(x.role) ? u + ' · Command Center user · the Command Center app scans this and gives ' + u + "'s password" : u + ' · ' + x.role) + ' · ' + url
  }
  $('#qrDoor').oninput = draw; draw(); $('#qr').hidden = false
}
$('#qrClose').onclick = () => { $('#qr').hidden = true }
// [QR_SAVE_V1] the QR as a PNG, its user's name and group under it: drawn on a canvas, handed to the app (rtNative.saveFile:
// a save dialog on the desktop, Pictures/OpenEOC on the phone)
$('#qrSave').onclick = () => {
  const u = A.qrUser, who = $('#qrWho').textContent, svg = $('#qrImg').innerHTML
  const img = new Image()
  img.onload = () => {
    const W = 840, H = 940, cv = document.createElement('canvas'); cv.width = W; cv.height = H
    const g = cv.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, W, H); g.drawImage(img, 0, 0, W, W)
    g.fillStyle = '#000'; g.textAlign = 'center'; g.font = 'bold 40px sans-serif'; g.fillText(u, W / 2, W + 44)
    g.font = '22px sans-serif'; g.fillText(who.replace(/^[^·]*·\s*/, ''), W / 2, W + 82)
    let b64
    try { b64 = cv.toDataURL('image/png').split(',')[1] } catch (err) { $('#status').textContent = '⚠ the QR image could not be made: ' + err.message; return }
    if (!window.rtNative.saveFile) { $('#status').textContent = '⚠ this admin app cannot save files (update it)'; return }
    window.rtNative.saveFile('OpenEOC-' + A.app + '-' + u.replace(/[^A-Za-z0-9._-]/g, '_') + '.png', b64)
  }
  img.onerror = () => { $('#status').textContent = '⚠ the QR image could not be drawn' }
  img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)))
}

// ------------------------------------------------------------ [ASSIGNMENTS_SAVE_V1] save and restore, by hand only
$('#save').onclick = () => send({ op: 'save' })
$('#restore').onclick = () => { const f = $('#saves').value; if (f) send({ op: 'restore', file: f }) }
function saves(m) {
  $('#saves').innerHTML = (m.files || []).map(f => `<option>${esc(f)}</option>`).join('') || '<option value="">no saves yet</option>'
  $('#savesDir').textContent = 'Saves are kept in ' + m.dir
}
function restored(m) {
  A.restoredUsers = m.config.users || {}; A.edit = fromCurrent(m.config); A.pw = {}; A.dirty = true; draw()
  $('#status').textContent = 'loaded ' + m.file + ': review the changes below, then Apply'
}
