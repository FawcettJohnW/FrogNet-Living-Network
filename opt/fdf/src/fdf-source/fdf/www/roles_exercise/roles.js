// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// roles.js -- [ROLE_ADMIN_PAGES_V1] what the <Role>Admin pages share: a Role's memory through the door (roles_ram.php).
// A page watches the tuples it shows by HELD reads (watch): the door answers when they change. Nothing here polls. A page
// names memories and tuples; it names no protocol and no key.
export const DOOR = window.ROLES_RAM || 'roles_ram.php'
export const $ = (s) => document.querySelector(s)
export const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
export const say = (t) => { const e = $('#say'); if (e) e.textContent = t || '' }

// {"<var>.<scope>": value} of a door's answer. Two writers of one name are two tuples; the one for the later step is kept.
export function byName(tuples) {
  const out = new Map(), rank = new Map()
  for (const t of tuples || []) {
    const name = String(t.name || '').replace(/^SD:/, ''), k = [Number.isInteger(t.value?.step) ? t.value.step : -1, t.id]
    const was = rank.get(name)
    if (!was || k[0] > was[0] || (k[0] === was[0] && k[1] > was[1])) { out.set(name, t.value); rank.set(name, k) }
  }
  return out
}
export function family(cells, variable) {            // [[scope, value]] of one variable, in order of scope
  const pre = variable + '.'
  return [...cells].filter(([n]) => n.startsWith(pre)).map(([n, v]) => [n.slice(pre.length), v])
    .sort((a, b) => (isNaN(a[0]) || isNaN(b[0])) ? a[0].localeCompare(b[0]) : a[0] - b[0])
}
// every tuple of role that matches like, now and at each change: onChange(Map). Returns a function that stops it.
export function watch(role, like, onChange) {
  let on = true, seen = ''
  ;(async () => {
    while (on) {
      try {
        const r = await fetch(DOOR + '?op=watch&role=' + role + '&like=' + encodeURIComponent(like) + '&seen=' + seen + '&wait=25')
        const j = await r.json()
        if (!j.ok) throw new Error(j.why || ('HTTP ' + r.status))
        if (j.seen !== seen) { seen = j.seen; say(''); onChange(byName(j.tuples)) }
      } catch (e) {
        say('the memory of ' + role + ' did not answer (' + e.message + '); held again when this page is next shown')
        await new Promise((go) => document.addEventListener('visibilitychange', go, { once: true }))   // an event, not a timer
      }
    }
  })()
  return () => { on = false }
}
export async function write(role, variable, scope, value) {
  const r = await fetch(DOOR + '?op=write&role=' + role + '&var=' + variable + '&scope=' + encodeURIComponent(scope),
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
  const j = await r.json()
  if (!j.ok) say('the write into ' + role + ' was not taken (' + (j.why || r.status) + ')')
  return !!j.ok
}
// the 16 by 16 grid: marks is [{x, y, cls, text, title}], later ones over earlier ones. y 15 is the top row.
export function grid(el, marks, size = 16) {
  const cell = new Map()
  for (const m of marks) if (m.x >= 0 && m.x < size && m.y >= 0 && m.y < size) cell.set(m.x + ',' + m.y, m)
  let h = ''
  for (let y = size - 1; y >= 0; y--) for (let x = 0; x < size; x++) {
    const m = cell.get(x + ',' + y)
    h += '<div class="' + (m ? m.cls : '') + '" title="' + esc((m && m.title) || (x + ', ' + y)) + '">' + esc(m ? m.text : '') + '</div>'
  }
  el.innerHTML = h
}
export const far = (ax, ay, bx, by) => Math.max(Math.abs(ax - bx), Math.abs(ay - by))
export function table(el, head, rows) {
  el.innerHTML = '<thead><tr>' + head.map((h) => '<th>' + esc(h) + '</th>').join('') + '</tr></thead><tbody>' +
    rows.map((r) => '<tr>' + r.map((c) => '<td>' + (c && c.html !== undefined ? c.html : esc(c)) + '</td>').join('') + '</tr>').join('') + '</tbody>'
}
export const latest = (cells, variable) => { const f = family(cells, variable).filter(([s]) => !isNaN(s)); return f.length ? f[f.length - 1][1] : null }
