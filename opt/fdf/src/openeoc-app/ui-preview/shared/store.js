// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// shared/store.js -- [UI_PREVIEW_V1] the preview's stand-in for the RAM: tuples (family, instance, bag), as api.php
// keeps them, held in this browser. The screens use only write / read / remove / on, so binding the real apps is
// replacing this file: on() is the held read (a family's change fires its watchers), not a poll.
'use strict'
window.Store = (() => {
  const KEY = 'openeoc-ui-preview-v1'
  let T = {}                      // "family\u0000instance" -> {family, instance, bag, seq, at}
  let seq = 0
  const watch = {}                // family -> [fn]
  const k = (f, i) => f + '\u0000' + i
  function save() { try { localStorage.setItem(KEY, JSON.stringify({ T, seq })) } catch (e) { /* preview: memory only */ } }
  function load() {
    try { const s = JSON.parse(localStorage.getItem(KEY) || 'null'); if (s && s.T) { T = s.T; seq = s.seq || 0; return true } } catch (e) {}
    return false
  }
  function fire(f) { (watch[f] || []).concat(watch['*'] || []).forEach(fn => { try { fn(f) } catch (e) { console.error(e) } }) }
  const api = {
    write(family, instance, bag) {
      const t = { family, instance: String(instance), bag: Object.assign({}, bag), seq: ++seq, at: new Date().toISOString() }
      T[k(family, t.instance)] = t; save(); fire(family); return t
    },
    get(family, instance) { const t = T[k(family, String(instance))]; return t ? t.bag : null },
    read(family) { return Object.values(T).filter(t => t.family === family).sort((a, b) => b.seq - a.seq) },
    remove(family, instance) { delete T[k(family, String(instance))]; save(); fire(family) },
    on(family, fn) { (watch[family] = watch[family] || []).push(fn) },
    id(prefix) { return prefix + '-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6) },
    // EOCAdmin's offline package: everything, as one JSON file, and merged back newest-wins per tuple
    exportAll() { return JSON.stringify({ kind: 'openeoc-ui-preview', at: new Date().toISOString(), tuples: Object.values(T) }, null, 1) },
    importAll(text) {
      const p = JSON.parse(text); let n = 0
      for (const t of p.tuples || []) {
        const o = T[k(t.family, t.instance)]
        if (!o || o.at < t.at) { T[k(t.family, t.instance)] = Object.assign({}, t, { seq: ++seq }); n++ }
      }
      save(); Object.keys(watch).forEach(fire); return n
    },
    reset() { T = {}; seq = 0; try { localStorage.removeItem(KEY) } catch (e) {} },
    loaded: false,
  }
  api.loaded = load()
  // another tab (the EOC, a Team, a Hospital ...) wrote: the families that changed fire here, as a held read would
  window.addEventListener('storage', (e) => {
    if (e.key !== KEY) return
    const old = T; load()
    const fams = new Set()
    for (const x of Object.keys(old).concat(Object.keys(T))) { const a = old[x], b = T[x]; if (!a || !b || a.seq !== b.seq) fams.add((a || b).family) }
    fams.forEach(fire)
  })
  return api
})()
