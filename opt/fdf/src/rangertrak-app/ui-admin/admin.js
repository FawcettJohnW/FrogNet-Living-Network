// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// admin.js -- [ADMIN_APP_V1] the admin app's screen. Everything it shows comes from AdminCore's lines; a change is
// one "apply" line with the whole configuration and the new passwords (the core makes their verifiers).
'use strict'
const $ = (s) => document.querySelector(s)
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const send = (o) => window.rtNative.send(JSON.stringify(o))
const A = { cur: null, edit: null, pw: {}, dirty: false, sent: 0, state: null }

window.rtInit = function (cfg) {
  cfg = cfg || {}
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
  } else if (m.ev === 'state') state(m)
}

// ------------------------------------------------------------ the configuration being edited
function fromCurrent(c) {
  const rams = new Set(c.rams || [])
  const roles = {}
  for (const [r, x] of Object.entries(c.roles || {})) roles[r] = { team: rams.has(r), def: !!(x || {}).default }
  const users = {}
  for (const [u, x] of Object.entries(c.users || {})) users[u] = { role: (x || {}).role || '' }
  const extra = [...rams].filter(r => r !== 'general' && !roles[r])
  return { roles, users, login: !!c.login, partition: c.partition === 'fail' ? 'fail' : 'relocate', extra: extra.join(', ') }
}
function toConfig(e) {
  const rams = ['general', ...Object.entries(e.roles).filter(([, x]) => x.team).map(([r]) => r),
    ...e.extra.split(/[,\s]+/).map(s => s.trim().toLowerCase()).filter(Boolean)]
  const roles = {}; for (const [r, x] of Object.entries(e.roles)) roles[r] = { default: x.def }
  const users = {}; for (const [u, x] of Object.entries(e.users)) users[u] = { role: x.role }
  return { rams: [...new Set(rams)], partition: e.partition, login: e.login, roles, users }
}
function state(m) {
  A.state = m; A.cur = m.current
  if (!A.dirty) { A.edit = fromCurrent(A.cur); A.pw = {}; draw() }
  members(m)
}
function draw() {
  const e = A.edit
  $('#roles').innerHTML = Object.entries(e.roles).map(([r, x]) => `<tr><td><b>${esc(r)}</b>${x.team ? '<span class="pill team">team</span>' : ''}</td>
    <td class="c"><input type="checkbox" data-r="${esc(r)}" data-k="team" ${x.team ? 'checked' : ''}></td>
    <td class="c"><input type="checkbox" data-r="${esc(r)}" data-k="def" ${x.def ? 'checked' : ''}></td>
    <td class="c"><button type="button" class="x" data-del-role="${esc(r)}" title="remove">✕</button></td></tr>`).join('') || '<tr><td colspan="4" class="mu">no roles</td></tr>'
  const opts = (sel) => Object.keys(e.roles).map(r => `<option${r === sel ? ' selected' : ''}>${esc(r)}</option>`).join('')
  $('#users').innerHTML = Object.entries(e.users).map(([u, x]) => `<tr><td><b>${esc(u)}</b>${A.cur.users && A.cur.users[u] ? '' : '<span class="pill">new</span>'}</td>
    <td><select data-u="${esc(u)}">${x.role && !e.roles[x.role] ? `<option selected>${esc(x.role)} (gone)</option>` : ''}${opts(x.role)}</select></td>
    <td><input type="password" data-pw="${esc(u)}" value="${esc(A.pw[u] || '')}" placeholder="${A.cur.users && A.cur.users[u] ? 'keep' : 'required'}" autocomplete="new-password"></td>
    <td class="c"><button type="button" class="x" data-del-user="${esc(u)}" title="remove">✕</button></td></tr>`).join('') || '<tr><td colspan="4" class="mu">no users</td></tr>'
  $('#login').checked = e.login; $('#pRel').checked = e.partition === 'relocate'; $('#pFail').checked = e.partition === 'fail'
  $('#extraRams').value = e.extra
  dirtyLine()
}
function changes() {
  if (!A.cur) return []
  const a = toConfig(fromCurrent(A.cur)), b = toConfig(A.edit), out = []
  for (const r of Object.keys(b.roles)) if (!a.roles[r]) out.push('add ' + r)
  for (const r of Object.keys(a.roles)) if (!b.roles[r]) out.push('remove ' + r)
  for (const r of Object.keys(b.roles)) if (a.roles[r] && a.roles[r].default !== b.roles[r].default) out.push(r + (b.roles[r].default ? ' with no login' : ' at login only'))
  const ar = new Set(a.rams), br = new Set(b.rams)
  for (const r of br) if (!ar.has(r)) out.push('RAM ' + r)
  for (const r of ar) if (!br.has(r)) out.push('no RAM ' + r)
  for (const u of Object.keys(b.users)) if (!a.users[u]) out.push('user ' + u); else if (a.users[u].role !== b.users[u].role) out.push(u + ' → ' + b.users[u].role)
  for (const u of Object.keys(a.users)) if (!b.users[u]) out.push('remove user ' + u)
  for (const u of Object.keys(A.pw)) if (A.pw[u] && a.users[u]) out.push('new password for ' + u)
  if (a.login !== b.login) out.push(b.login ? 'login required' : 'login not required')
  if (a.partition !== b.partition) out.push('partition: ' + b.partition)
  return out
}
function missing() { return Object.keys(A.edit.users).filter(u => !(A.cur.users || {})[u] && !A.pw[u]) }
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
  else if (t.dataset.u) e.users[t.dataset.u].role = t.value
  else if (t.id === 'login') e.login = t.checked
  else if (t.name === 'part') e.partition = t.value
  else if (t.id === 'extraRams') e.extra = t.value
  else return
  draw()
})
document.addEventListener('input', (ev) => { if (ev.target.dataset.pw !== undefined) { A.pw[ev.target.dataset.pw] = ev.target.value; dirtyLine() } })
document.addEventListener('click', (ev) => {
  const t = ev.target, e = A.edit; if (!e) return
  if (t.dataset.delRole) { delete e.roles[t.dataset.delRole]; draw() }
  else if (t.dataset.delUser) { delete e.users[t.dataset.delUser]; delete A.pw[t.dataset.delUser]; draw() }
})
$('#addRole').onclick = () => {
  const r = $('#newRole').value.trim().toLowerCase()
  if (!/^[a-z0-9_-]{1,40}$/.test(r) || r === 'general') { $('#status').textContent = 'a role is letters, digits, _ and - (not general)'; return }
  A.edit.roles[r] = { team: $('#newTeam').checked, def: $('#newDef').checked }; $('#newRole').value = ''; draw()
}
$('#addUser').onclick = () => {
  const u = $('#newUser').value.trim()
  if (!u || A.edit.users[u]) return
  A.edit.users[u] = { role: Object.keys(A.edit.roles)[0] || '' }; $('#newUser').value = ''; draw()
  const p = document.querySelector(`[data-pw="${CSS.escape(u)}"]`); if (p) p.focus()
}
$('#revert').onclick = () => { A.dirty = false; A.edit = fromCurrent(A.cur); A.pw = {}; draw() }
$('#reload').onclick = () => send({ op: 'refresh' })
$('#apply').onclick = () => {
  const pw = {}; for (const [u, p] of Object.entries(A.pw)) if (p) pw[u] = p
  send({ op: 'apply', config: toConfig(A.edit), passwords: pw })
  A.dirty = false; A.pw = {}; $('#apply').disabled = true; $('#changes').textContent = 'Sent: waiting for the members…'
}

// ------------------------------------------------------------ the members
// two members' configurations are the same when what clients see is the same (each install line has its own seq)
function sig(c) { const u = {}; for (const [k, x] of Object.entries(c.users || {})) u[k] = (x || {}).role   // (not the verifier: each install line salts its own)
  return JSON.stringify([[...(c.rams || [])].sort(), c.partition, !!c.login, Object.entries(c.roles || {}).sort().map(([r, x]) => [r, !!(x || {}).default]), Object.entries(u).sort()]) }
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
