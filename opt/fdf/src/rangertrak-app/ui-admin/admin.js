// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// admin.js -- [ADMIN_APP_V1] the admin app's screen. Everything it shows comes from AdminCore's lines; a change is
// one "apply" line with the whole configuration and the new passwords (the core makes their verifiers).
'use strict'
const $ = (s) => document.querySelector(s)
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const send = (o) => window.rtNative.send(JSON.stringify(o))
const A = { cur: null, edit: null, pw: {}, dirty: false, sent: 0, state: null, open: undefined }
const DEF_CAT = 'Teams'   // [CATEGORIES_V1] a role from a configuration made before categories

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
  const f = $('#connect'); f.door.value = cfg.door || ''; f.app.value = cfg.app || 'rangertrak'; f.user.value = cfg.user || ''
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
  else if (m.ev === 'saves') saves(m)
  else if (m.ev === 'restored') restored(m)
  else if (m.ev === 'saved') $('#status').textContent = 'saved ' + m.path
}

// ------------------------------------------------------------ the configuration being edited
function fromCurrent(c) {
  const rams = new Set(c.rams || [])
  const roles = {}, cats = []
  for (const k of c.categories || []) if (typeof k === 'string' && k && !cats.includes(k)) cats.push(k)
  for (const [r, x] of Object.entries(c.roles || {})) {
    const cat = (x || {}).category || DEF_CAT; if (!cats.includes(cat)) cats.push(cat)
    roles[r] = { team: rams.has(r), def: !!(x || {}).default, cat }
  }
  if (!cats.length) cats.push(DEF_CAT)
  const users = {}
  for (const [u, x] of Object.entries(c.users || {})) users[u] = { role: (x || {}).role || '', lead: !!(x || {}).lead }
  const extra = [...rams].filter(r => r !== 'general' && !roles[r])
  const sel = {}; for (const [r, s] of Object.entries(c.select || {})) sel[r] = { need: ((s || {}).need || []).join(', '), rank: ((s || {}).rank || []).join(', ') }   // [APP_SELECTION_V1]
  return { roles, cats, users, login: !!c.login, partition: c.partition === 'fail' ? 'fail' : 'relocate', extra: extra.join(', '), sel }
}
function toConfig(e) {
  const rams = ['general', ...Object.entries(e.roles).filter(([, x]) => x.team).map(([r]) => r),
    ...e.extra.split(/[,\s]+/).map(s => s.trim().toLowerCase()).filter(Boolean)]
  const roles = {}; for (const [r, x] of Object.entries(e.roles)) roles[r] = { default: x.def, category: x.cat }
  const users = {}; for (const [u, x] of Object.entries(e.users)) users[u] = { role: x.role, lead: !!x.lead }
  const keys = (t) => t.split(/[,\s]+/).map(s => s.trim()).filter(Boolean)
  const select = {}; for (const [r, s] of Object.entries(e.sel || {})) select[r] = { need: keys(s.need), rank: keys(s.rank) }
  return { rams: [...new Set(rams)], partition: e.partition, login: e.login, roles, categories: [...e.cats], users, select }
}
const catOf = (e, role) => (e.roles[role] || {}).cat
function state(m) {
  A.state = m; A.cur = m.current
  if (!A.dirty) { A.edit = fromCurrent(A.cur); A.pw = {}; draw() }
  else dirtyLine()        // the edit against the new current: an edit the members have since adopted is no change any more
  members(m); groups(m); panels()
}
// [GROUPS_NOW_V1] each group (a role), its tab, where its lead placed it, where its RAM is served now, its lead(s)
function groups(m) {
  const c = m.current || {}, served = m.served || {}, places = c.places || {}, rams = new Set(c.rams || [])
  const leads = {}; for (const [u, x] of Object.entries(c.users || {})) if ((x || {}).lead) (leads[x.role] = leads[x.role] || []).push(u)
  const rows = Object.entries(c.roles || {}).sort((a, b) => ((a[1] || {}).category || DEF_CAT).localeCompare((b[1] || {}).category || DEF_CAT) || a[0].localeCompare(b[0]))
  $('#groups').innerHTML = rows.map(([r, x]) => {
    const p = places[r] || {}, own = rams.has(r)
    const lives = !own ? '<span class="mu">no RAM of its own (the general RAM)</span>'
      : p.at === 'here' ? `on its lead's device <span class="mu">${esc(p.guid || '')}, placed by ${esc(p.user || '')}</span>`
      : p.at === 'databasehost' ? `on databasehost <span class="mu">placed by ${esc(p.user || '')}</span>` : 'on databasehost <span class="mu">(the app\'s selection)</span>'
    const at = !own ? '' : served[r] ? `<span class="ok">${esc(served[r])}</span>` : (p.at === 'here' ? '<span class="mu">on the lead\'s device (not a member)</span>' : '<span class="bad">nobody</span>')
    return `<tr><td><b>${esc(r)}</b></td><td>${esc((x || {}).category || DEF_CAT)}</td><td>${lives}</td><td>${at}</td><td>${esc((leads[r] || []).join(', ')) || '<span class="mu">none</span>'}</td></tr>`
  }).join('') || '<tr><td colspan="5" class="mu">no groups</td></tr>'
}
// [CATEGORY_DIALOG_V1] the page: a card per category (its groups, its users); a category opens as a pop-up (drawCat)
const usersOf = (e, r) => Object.entries(e.users).filter(([, x]) => x.role === r)
const isNew = (u) => !(A.cur.users || {})[u]
function draw() {
  const e = A.edit
  $('#cats').innerHTML = e.cats.map(c => {
    const rs = Object.keys(e.roles).filter(r => e.roles[r].cat === c)
    const nu = rs.reduce((n, r) => n + usersOf(e, r).length, 0)
    return `<button type="button" class="catcard" data-open-cat="${esc(c)}"><b>${esc(c)}</b>
      <span class="mu">${rs.length} group${rs.length === 1 ? '' : 's'} · ${nu} user${nu === 1 ? '' : 's'}</span>
      <span class="gl">${rs.map(r => esc(r)).join(' · ') || '<span class="mu">no groups yet</span>'}</span></button>`
  }).join('')
  // a user whose group was removed: moved to a group, or removed
  const lost = Object.entries(e.users).filter(([, x]) => !e.roles[x.role])
  $('#lost').innerHTML = !lost.length ? '' : '<h3>Users with no group</h3>' + lost.map(([u, x]) => `<div class="urow"><b>${esc(u)}</b>
    <span class="mu">was ${esc(x.role || 'none')}</span><select data-move="${esc(u)}"><option value="">move to…</option>${groupOpts()}</select>
    <button type="button" class="x" data-del-user="${esc(u)}" title="remove">✕</button></div>`).join('')
  $('#login').checked = e.login; $('#pRel').checked = e.partition === 'relocate'; $('#pFail').checked = e.partition === 'fail'
  $('#sel').innerHTML = Object.entries(e.sel).map(([r, s]) => `<tr><td><b>${esc(r)}</b></td>
    <td><input type="text" data-sel="${esc(r)}" data-k="need" value="${esc(s.need)}" placeholder="e.g. mysql_running"></td>
    <td><input type="text" data-sel="${esc(r)}" data-k="rank" value="${esc(s.rank)}" placeholder="e.g. mem_total_kb, -disk_fsync_ms"></td>
    <td class="c"><button type="button" class="x" data-del-sel="${esc(r)}" title="remove">✕</button></td></tr>`).join('') || '<tr><td colspan="4" class="mu">the default: the most memory, then the most cores</td></tr>'
  if (A.open !== undefined && !$('#cat').hidden) drawCat()
  dirtyLine()
}
function groupOpts() { const e = A.edit; return e.cats.map(c => { const rs = Object.keys(e.roles).filter(r => e.roles[r].cat === c)
  return rs.length ? `<optgroup label="${esc(c)}">` + rs.map(r => `<option value="${esc(r)}">${esc(r)}</option>`).join('') + '</optgroup>' : '' }).join('') }
function openCat(c) { A.open = c; $('#cat').hidden = false; drawCat() }
function drawCat() {
  const e = A.edit, c = A.open
  if (!e.cats.includes(c)) { $('#cat').hidden = true; return }
  $('#catTitle').textContent = c
  const rs = Object.keys(e.roles).filter(r => e.roles[r].cat === c)
  $('#catDel').hidden = rs.length > 0 || e.cats.length < 2
  // keep what is being typed in a new-user row across a redraw
  const typed = {}; for (const i of document.querySelectorAll('#catGroups [data-nu]')) typed[i.dataset.nu + '|' + i.dataset.f] = i.type === 'checkbox' ? i.checked : i.value
  $('#catGroups').innerHTML = rs.map(r => { const x = e.roles[r], us = usersOf(e, r)
    return `<div class="grp"><div class="gh"><b>${esc(r)}</b>${!(A.cur.roles || {})[r] ? '<span class="pill">new</span>' : ''}
      <label class="mu"><input type="checkbox" data-r="${esc(r)}" data-k="team" ${x.team ? 'checked' : ''}> own RAM</label>
      <label class="mu"><input type="checkbox" data-r="${esc(r)}" data-k="def" ${x.def ? 'checked' : ''}> no login</label>
      <span class="sp"></span><button type="button" class="x" data-del-role="${esc(r)}" title="remove the group ${esc(r)}">Remove group</button></div>
      <table><thead><tr><th>User</th><th class="c">Lead</th><th>Password</th><th class="c">QR</th><th></th></tr></thead><tbody>
      ${us.map(([u, y]) => `<tr><td><b>${esc(u)}</b>${isNew(u) ? '<span class="pill">new</span>' : ''}</td>
        <td class="c"><input type="checkbox" data-lead="${esc(u)}" ${y.lead ? 'checked' : ''} ${x.team ? '' : 'disabled title="only a group with its own RAM has a lead"'}></td>
        <td><input type="password" data-pw="${esc(u)}" value="${esc(A.pw[u] || '')}" placeholder="${isNew(u) ? 'required' : 'keep'}" autocomplete="new-password"></td>
        <td class="c"><button type="button" data-qr="${esc(u)}" ${isNew(u) ? 'disabled title="apply first"' : ''}>QR</button></td>
        <td class="c"><button type="button" class="x" data-del-user="${esc(u)}" title="remove">✕</button></td></tr>`).join('') || '<tr><td colspan="5" class="mu">no users yet</td></tr>'}
      <tr class="nu"><td><input type="text" data-nu="${esc(r)}" data-f="name" placeholder="new user" value="${esc(typed[r + '|name'] || '')}"></td>
        <td class="c"><input type="checkbox" data-nu="${esc(r)}" data-f="lead" ${typed[r + '|lead'] ? 'checked' : ''} ${x.team ? '' : 'disabled'}></td>
        <td><input type="password" data-nu="${esc(r)}" data-f="pw" placeholder="password" autocomplete="new-password" value="${esc(typed[r + '|pw'] || '')}"></td>
        <td class="c" colspan="2"><button type="button" data-add-user="${esc(r)}">Add user</button></td></tr></tbody></table></div>` }).join('') ||
    '<div class="mu">no groups in ' + esc(c) + ' yet: add one below, with its lead</div>'
  const ch = changes(), miss = missing()
  $('#catChanges').textContent = !ch.length ? 'No changes.' : ch.length + ' change(s)' + (miss.length ? ' — a password is required for ' + miss.join(', ') : '')
  $('#catApply').disabled = !ch.length || miss.length > 0
}
function changes() {
  if (!A.cur) return []
  const a = toConfig(fromCurrent(A.cur)), b = toConfig(A.edit), out = []
  for (const c of b.categories) if (!a.categories.includes(c)) out.push('category ' + c)
  for (const c of a.categories) if (!b.categories.includes(c)) out.push('no category ' + c)
  if (a.categories.filter(c => b.categories.includes(c)).join() !== b.categories.filter(c => a.categories.includes(c)).join()) out.push('tabs reordered')
  for (const r of Object.keys(b.roles)) if (!a.roles[r]) out.push('add ' + r + ' (' + b.roles[r].category + ')')
  for (const r of Object.keys(b.roles)) if (a.roles[r] && a.roles[r].category !== b.roles[r].category) out.push(r + ' → ' + b.roles[r].category)
  for (const r of Object.keys(a.roles)) if (!b.roles[r]) out.push('remove ' + r)
  for (const r of Object.keys(b.roles)) if (a.roles[r] && a.roles[r].default !== b.roles[r].default) out.push(r + (b.roles[r].default ? ' with no login' : ' at login only'))
  const ar = new Set(a.rams), br = new Set(b.rams)
  for (const r of br) if (!ar.has(r)) out.push('RAM ' + r)
  for (const r of ar) if (!br.has(r)) out.push('no RAM ' + r)
  for (const u of Object.keys(b.users)) if (!a.users[u]) out.push('user ' + u); else if (a.users[u].role !== b.users[u].role) out.push(u + ' → ' + b.users[u].role)
  for (const u of Object.keys(b.users)) if (a.users[u] && !!a.users[u].lead !== !!b.users[u].lead) out.push(u + (b.users[u].lead ? ' leads ' : ' no longer leads ') + b.users[u].role)
  for (const u of Object.keys(a.users)) if (!b.users[u]) out.push('remove user ' + u)
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
  if (t.dataset.r) e.roles[t.dataset.r][t.dataset.k] = t.checked
  else if (t.dataset.move) { if (!t.value) return; e.users[t.dataset.move].role = t.value; e.users[t.dataset.move].lead = false; $('#status').textContent = t.dataset.move + ' is now in ' + t.value + ' (' + catOf(e, t.value) + ')' }
  else if (t.dataset.nu) return
  else if (t.dataset.lead) e.users[t.dataset.lead].lead = t.checked
  else if (t.id === 'login') e.login = t.checked
  else if (t.name === 'part') e.partition = t.value
  else if (t.dataset.sel) e.sel[t.dataset.sel][t.dataset.k] = t.value
  else return
  draw()
})
document.addEventListener('input', (ev) => { if (ev.target.dataset.pw !== undefined) { A.pw[ev.target.dataset.pw] = ev.target.value; dirtyLine(); if (!$('#cat').hidden) catLine() } })
function catLine() { const ch = changes(), miss = missing()
  $('#catChanges').textContent = !ch.length ? 'No changes.' : ch.length + ' change(s)' + (miss.length ? ' — a password is required for ' + miss.join(', ') : '')
  $('#catApply').disabled = !ch.length || miss.length > 0 }
document.addEventListener('click', (ev) => {
  const t = ev.target, e = A.edit; if (!e) return
  const open = t.closest && t.closest('[data-open-cat]')
  if (open) openCat(open.dataset.openCat)
  else if (t.dataset.delRole) { delete e.roles[t.dataset.delRole]; draw() }
  else if (t.dataset.addUser) addUser(t.dataset.addUser)
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
// [CATEGORY_DIALOG_V1] a group is added with its lead: the lead's user (new, with its password; or one there is, moved here)
$('#addRole').onclick = () => {
  const e = A.edit, r = $('#newRole').value.trim().toLowerCase(), team = $('#newTeam').checked
  const lead = $('#newLead').value.trim(), pw = $('#newLeadPw').value
  const say = (t) => { $('#catChanges').textContent = t }
  if (!/^[a-z0-9_-]{1,40}$/.test(r) || r === 'general') return say('a group is letters, digits, _ and - (not general)')
  if (e.roles[r]) return say(r + ' is already a group, in ' + e.roles[r].cat)
  if (lead && !okUser(lead)) return say('a user name is letters, digits and . _ @ -')
  if (lead && !e.users[lead] && !pw) return say(lead + ' is a new user: give its password')
  e.roles[r] = { team, def: $('#newDef').checked, cat: A.open }
  if (lead) { e.users[lead] = { role: r, lead: team }; if (pw) A.pw[lead] = pw }
  for (const i of ['#newRole', '#newLead', '#newLeadPw']) $(i).value = ''
  draw()
}
function addUser(r) {
  const e = A.edit, q = (f) => document.querySelector(`#catGroups [data-nu="${CSS.escape(r)}"][data-f="${f}"]`)
  const u = q('name').value.trim(), pw = q('pw').value, lead = q('lead').checked && e.roles[r].team
  if (!okUser(u)) { $('#catChanges').textContent = 'a user name is letters, digits and . _ @ -'; return }
  if (e.users[u]) { $('#catChanges').textContent = u + ' is a user already, in ' + e.users[u].role; return }
  if (!pw) { $('#catChanges').textContent = u + ': give its password'; return }
  e.users[u] = { role: r, lead }; A.pw[u] = pw
  q('name').value = ''; q('pw').value = ''; q('lead').checked = false
  draw()
}
$('#addCat').onclick = () => {
  const c = $('#newCat').value.trim()
  if (!/^[A-Za-z0-9 _-]{1,40}$/.test(c)) { $('#status').textContent = 'a category is letters, digits, space, _ and -, at most 40'; return }
  if (!A.edit.cats.includes(c)) A.edit.cats.push(c)
  $('#newCat').value = ''; draw(); openCat(c); $('#newRole').focus()
}
$('#catClose').onclick = () => { $('#cat').hidden = true; draw() }
$('#catDel').onclick = () => { const e = A.edit; e.cats = e.cats.filter(c => c !== A.open); $('#cat').hidden = true; draw() }
$('#catApply').onclick = () => $('#apply').click()
document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') { if (!$('#qr').hidden) $('#qr').hidden = true; else if (!$('#cat').hidden) $('#catClose').click() } })
$('#revert').onclick = () => { A.dirty = false; A.edit = fromCurrent(A.cur); A.pw = {}; draw() }
$('#reload').onclick = () => send({ op: 'refresh' })
$('#apply').onclick = () => {
  const pw = {}; for (const [u, p] of Object.entries(A.pw)) if (p) pw[u] = p
  send({ op: 'apply', config: toConfig(A.edit), passwords: pw })
  A.dirty = false; A.pw = {}; A.restoredUsers = {}; $('#apply').disabled = true; $('#changes').textContent = 'Sent: waiting for the members…'
  $('#catApply').disabled = true; $('#catChanges').textContent = 'Sent: waiting for the members…'
}

// ------------------------------------------------------------ the members
// two members' configurations are the same when what clients see is the same (each install line has its own seq)
function sig(c) { const u = {}; for (const [k, x] of Object.entries(c.users || {})) u[k] = [(x || {}).role, !!(x || {}).lead]  // (not the verifier: each install line salts its own)
  return JSON.stringify([[...(c.rams || [])].sort(), c.partition, !!c.login, Object.entries(c.roles || {}).sort().map(([r, x]) => [r, !!(x || {}).default, (x || {}).category || DEF_CAT]), Object.entries(u).sort(),
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
    $('#qrWho').textContent = u + ' · ' + ((A.cur.users || {})[u] || {}).role + (((A.cur.users || {})[u] || {}).lead ? ' (lead)' : '') + ' · ' + url
  }
  $('#qrDoor').oninput = draw; draw(); $('#qr').hidden = false
}
$('#qrClose').onclick = () => { $('#qr').hidden = true }
// [QR_SAVE_V1] the QR as a PNG, its user's name and group under it: drawn on a canvas, handed to the app (rtNative.saveFile:
// a save dialog on the desktop, Pictures/RangerTrak on the phone)
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
    window.rtNative.saveFile('RangerTrak-' + A.app + '-' + u.replace(/[^A-Za-z0-9._-]/g, '_') + '.png', b64)
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
