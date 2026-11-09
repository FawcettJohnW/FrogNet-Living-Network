// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// shared/shell.js -- [UI_PREVIEW_V1] the frame every preview app runs in: the header (the incident, its operational
// period and activation level), the navigation, one dialog at a time, the status line, the form renderer, and the
// reviewer's notes (a note per screen, kept with the data and exported with it).
'use strict'
window.App = (() => {
  const mods = [], hooks = {}
  let cur = null, cfg = {}
  const $ = (s, el) => (el || document).querySelector(s)
  const $$ = (s, el) => [...(el || document).querySelectorAll(s)]
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const when = (iso) => { if (!iso) return ''; const d = new Date(iso); return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }
  // the preview's stand-in for a verifier: the password is never kept, only this (the real apps: the door's verifier)
  const verifier = (pw) => { let h = 2166136261; for (const c of 'openeoc-preview:' + pw) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0 } return 'pv1:' + h.toString(16) }
  // a datetime-local value: this device's local time, as the form inputs show it
  const local = (d) => { d = d || new Date(); const z = (n) => String(n).padStart(2, '0'); return d.getFullYear() + '-' + z(d.getMonth() + 1) + '-' + z(d.getDate()) + 'T' + z(d.getHours()) + ':' + z(d.getMinutes()) }
  function incident() { return Store.get('eoc-incident', 'current') }

  function module(id, label, render, opt) { mods.push(Object.assign({ id, label, render }, opt || {})) }
  function on(ev, fn) { (hooks[ev] = hooks[ev] || []).push(fn) }
  function emit(ev, x) { (hooks[ev] || []).forEach(fn => fn(x)) }

  function header() {
    const i = incident()
    const lv = i && FEMA.levels.find(l => l.id === Number(i.level))
    $('#hdr').innerHTML = `<span class="brand">${esc(cfg.title)}</span>
      <span class="inc">${i ? esc(i.name) + ' · ' + esc(i.type || '') + ' · Op period ' + esc((i.opPeriod || {}).n || 1) : 'No incident'}</span>
      ${lv ? `<span class="lvl l${lv.id}">${esc(lv.short)}</span>` : ''}
      <span class="who">${esc(cfg.who ? cfg.who() : '')}</span>
      <button type="button" id="noteBtn" title="your notes on this screen, for the UI review">✎ Note</button>`
    $('#noteBtn').onclick = note
  }
  function nav() {
    const vis = mods.filter(m => !m.when || m.when())
    $('#nav').innerHTML = vis.map(m => `<button type="button" data-mod="${m.id}" class="${cur === m.id ? 'on' : ''}">${m.icon ? '<i>' + m.icon + '</i>' : ''}<span>${esc(m.label)}</span></button>`).join('')
    $$('#nav button').forEach(b => b.onclick = () => show(b.dataset.mod))
  }
  function show(id) {
    const vis = mods.filter(m => !m.when || m.when())
    const m = vis.find(x => x.id === id) || vis[0]
    if (!m) return
    cur = m.id
    try { sessionStorage.setItem('openeoc-preview-mod-' + cfg.app, cur) } catch (e) {}
    close(); nav(); header()
    const el = $('#view'); el.innerHTML = ''; el.scrollTop = 0
    m.render(el)
  }
  // a change (here or in another tab) draws the screen again, but never under the reviewer's typing: a dialog stays open,
  // and a field being edited in the screen holds the redraw until it loses focus
  let pending = false
  function refresh() {
    if (!cur) return
    const a = document.activeElement
    if (a && $('#view').contains(a) && /INPUT|TEXTAREA|SELECT/.test(a.tagName)) { pending = true; return }
    pending = false
    const m = mods.find(x => x.id === cur); if (!m || (m.when && !m.when())) { show(); return }
    const el = $('#view'), keep = el.scrollTop
    nav(); header(); el.innerHTML = ''; m.render(el); el.scrollTop = keep
  }
  document.addEventListener('focusout', () => setTimeout(() => { if (pending) refresh() }, 0))

  // [ONE_DIALOG_V1] one dialog at a time: opening one closes the one that was open
  function dialog(title, body, buttons) {
    close()
    const d = document.createElement('div'); d.id = 'dlg'
    d.innerHTML = `<div class="box"><div class="dh"><b>${esc(title)}</b><button type="button" class="x" aria-label="close">✕</button></div>
      <div class="db">${body}</div><div class="df"></div></div>`
    document.body.appendChild(d)
    $('.x', d).onclick = close
    d.onclick = (e) => { if (e.target === d) close() }
    for (const [label, fn, cls] of buttons || []) {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = label; if (cls) b.className = cls
      b.onclick = () => { if (fn(d) !== false) close() }
      $('.df', d).appendChild(b)
    }
    const f = $('input,select,textarea', d); if (f) f.focus()
    return d
  }
  function close() { const d = $('#dlg'); if (d) d.remove() }
  let tmr
  function status(s, bad) { const el = $('#status'); el.textContent = s; el.className = bad ? 'bad' : ''; clearTimeout(tmr); tmr = setTimeout(() => { el.textContent = '' }, 6000) }

  // the form renderer: fields [{k, label, type: text|number|textarea|select|date|datetime|check, options, wide, hint}]
  function form(fields, v) {
    v = v || {}
    return '<div class="form">' + fields.map(f => {
      const val = v[f.k] == null ? (f.value == null ? '' : f.value) : v[f.k]
      const cls = 'fl' + (f.wide || f.type === 'textarea' ? ' wide' : '')
      let inp
      if (f.type === 'select') inp = `<select name="${f.k}">${(f.options || []).map(o => { const [ov, ol] = Array.isArray(o) ? o : [o, o]
        return `<option value="${esc(ov)}"${String(ov) === String(val) ? ' selected' : ''}>${esc(ol)}</option>` }).join('')}</select>`
      else if (f.type === 'textarea') inp = `<textarea name="${f.k}" rows="${f.rows || 4}">${esc(val)}</textarea>`
      else if (f.type === 'check') inp = `<input type="checkbox" name="${f.k}"${val ? ' checked' : ''}>`
      else if (f.type === 'heading') return `<h4 class="wide">${esc(f.label)}</h4>`
      else inp = `<input name="${f.k}" type="${f.type === 'datetime' ? 'datetime-local' : (f.type || 'text')}" value="${esc(val)}"${f.ro ? ' readonly' : ''}>`
      return `<label class="${cls}"><span>${esc(f.label)}</span>${inp}${f.hint ? `<em>${esc(f.hint)}</em>` : ''}</label>`
    }).join('') + '</div>'
  }
  function collect(el) {
    const o = {}
    $$('[name]', el).forEach(i => { o[i.name] = i.type === 'checkbox' ? i.checked : i.type === 'number' ? (i.value === '' ? '' : Number(i.value)) : i.value })
    return o
  }
  function table(cols, rows, empty) {
    if (!rows.length) return `<div class="empty">${esc(empty || 'nothing yet')}</div>`
    return `<div class="tw"><table><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
  }

  // the reviewer's notes: one per screen, in the data (family preview-note), so they go out in the export
  function note() {
    const id = cfg.app + '/' + cur
    const n = Store.get('preview-note', id) || {}
    dialog('Note on ' + cfg.title + ' › ' + (mods.find(m => m.id === cur) || {}).label,
      `<div class="meta">What works, what doesn't, what's missing on this screen. Notes are kept with the data; EOCAdmin › Offline package exports them.</div>
       <textarea id="noteText" rows="8">${esc(n.text || '')}</textarea>`,
      [['Save', () => { Store.write('preview-note', id, { app: cfg.app, screen: cur, text: $('#noteText').value }); status('note saved') }, 'pri']])
  }

  function start(c) {
    cfg = c
    document.title = c.title
    document.body.insertAdjacentHTML('afterbegin', `<header id="hdr"></header><div id="wrap"><nav id="nav"></nav><main id="view"></main></div><div id="status"></div>`)
    Store.on('*', (f) => { if (f !== 'preview-note') { emit('change', f); refresh() } })
    let first = null
    try { first = sessionStorage.getItem('openeoc-preview-mod-' + c.app) } catch (e) {}
    show(first || (mods[0] || {}).id)
  }
  return { $, $$, esc, when, local, verifier, incident, module, on, show, refresh, dialog, close, status, form, collect, table, start }
})()
