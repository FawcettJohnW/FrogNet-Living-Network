////////////////////////////////////////////////////////////////
//  Copyright (C) 2016-2026 Fawcett Innovations LLC           //
//  SPDX-License-Identifier: GPL-2.0-only                     //
////////////////////////////////////////////////////////////////
// rt.js -- [RT_PAGES_V1] the radio log app, served by the post's node: what every page shares.
//
// The cells (DESIGN.md, the truth table), through the node's ram.php (the DisasterComm add-in door, ../dc/ram.php):
//   (rt-mission, settings, <mission>)            latest   the scribe who started it: name, event, op period
//   (rt-log, <mission>.<op>, <dev>.<seq>)        distinct one cell per entry, written once by the device that logged it
//   (rt-handled, <mission>, <entry instance>)    latest   whoever marked it
// <dev> is this browser's own id (kept in localStorage, made once); its station letter is its own fact. The message
// number is rules.js's (nextMsgNo / msgNoShort / msgNoFull), stamped once when written.
import * as D from './rules.js'

export const RAM = (window.RT_RAM || '../dc/ram.php')
const q = (o) => Object.entries(o).map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&')

async function call(url, opts) {
  const r = await fetch(url, opts)
  const j = await r.json().catch(() => ({ ok: false, why: 'answer was not JSON (HTTP ' + r.status + ')' }))
  if (!r.ok || j.ok === false) throw new Error(j.why ? j.why + (j.output ? ': ' + j.output : '') : 'HTTP ' + r.status)
  return j
}
async function readJ(s, v, after, wait, ram) {
  const o = { op: 'read', s, v }; if (after !== undefined) { o.after = after; o.wait = wait ?? 25; if (ram) o.ram = ram }
  return call(RAM + '?' + q(o))
}
export async function read(s, v, after, wait) { return (await readJ(s, v, after, wait)).rows || [] }

// [OWN_TRUTH_AGAIN_V1] what this device wrote, per (service, variable): written again wherever the node's RAM manager is
// now, when a read finds it missing there (ORGANIZATIONS.md section 6: memory is never moved; each owner writes its own)
const own = new Map()
function keep(s, v, i, bag) { const k = s + '\u0000' + v; if (!own.has(k)) own.set(k, new Map()); own.get(k).set(i, bag) }
export async function write(s, v, i, bag) {
  const b = { ...bag, dev: device().id }
  const j = await call(RAM + '?op=write', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ s, v, i, bag: b }) })
  keep(s, v, i, b); return j
}
const note = (t) => (window.RT_NOTE || console.log)(t)                  // a page sets RT_NOTE to show it
async function again(s, v, cells) {
  const me = device().id, mine = own.get(s + '\u0000' + v) || new Map()
  for (const r of cells.values()) if (r.bag && r.bag.dev === me && !mine.has(r.instance)) keep(s, v, r.instance, r.bag)
  const missing = [...(own.get(s + '\u0000' + v) || new Map())].filter(([i]) => !cells.has(i))
  for (const [i, bag] of missing) await call(RAM + '?op=write', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ s, v, i, bag }) })
  if (missing.length) note('this device\'s own ' + missing.length + ' written again (' + s + ', on ' + (cells.ram || 'the RAM it is on now') + ')')
}

// every cell of s.v now, then each newer one as it is written: held reads, no polling. onChange(Map instance -> row).
// [RAM_IS_SAID_V1] the node says which RAM manager each answer came from; when that changes (or a read failed: it may be
// moving) everything is read again there, and this device's own cells that are missing are written again.
// a held read lasts at most HOLD_S: the longest a page stays on a RAM manager its node has left (a held read on it cannot
// know); 8 s keeps "every screen again within seconds" of a post's uplink coming back
const HOLD_S = 8
export function watch(s, v, onChange, onError) {
  let cells = new Map(), after = 0, stopped = false, ram = null, full = true
  const take = (rows) => { for (const r of rows) { after = Math.max(after, r.id); const o = cells.get(r.instance); if (!o || o.id < r.id) cells.set(r.instance, r) } }
  ;(async () => {
    while (!stopped) {
      try {
        const j = full ? await readJ(s, v) : await readJ(s, v, after, HOLD_S, ram)
        if (!full && j.ram && ram && j.ram !== ram) { full = true; continue }      // the node moved: read everything there
        if (full) { cells = new Map(); after = 0; ram = j.ram || ram; take(j.rows || []); onChange(cells); full = false
                    cells.ram = ram; await again(s, v, cells) }
        else if ((j.rows || []).length) { take(j.rows); onChange(cells) }
      } catch (e) { (onError || console.error)(e); full = true; await new Promise(r => setTimeout(r, 2000)) }
    }
  })()
  return () => { stopped = true }
}

function kept(key, val) {
  try { if (val === undefined) return JSON.parse(localStorage.getItem('rt.' + key) || 'null'); localStorage.setItem('rt.' + key, JSON.stringify(val)) }
  catch (e) { return null }
}
export function device() {
  let d = kept('device')
  if (!d) { d = { id: 'd' + Math.random().toString(36).slice(2, 10), name: '', station: '' }; kept('device', d) }
  return d
}
export function setDevice(name, station) { const d = { ...device(), name, station: station.toUpperCase().slice(0, 1) }; kept('device', d); return d }
export function currentMission() { return kept('mission') }
export function setMission(m) { kept('mission', m) }

export const esc = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
export const missionId = (name) => name.trim().replace(/[^A-Za-z0-9._-]+/g, '-').slice(0, 60)
export const logVariable = (m) => m.id + '.' + m.opPeriod


// write this device's next entry: its message number from its own entries' numbers and its high-water mark, the cell instance <dev>.<seq>, never overwritten
export async function logEntry(m, entries, form) {
  const d = device()
  if (!d.station) throw new Error('this device has no station letter yet (set it above)')
  const mine = [...entries.values()].filter(r => r.instance.startsWith(d.id + '.')).map(r => r.bag)
  const hw = kept('hw.' + m.id) || {}
  const msgNo = D.nextMsgNo(mine, hw, { mission: m.name, opPeriod: m.opPeriod, station: d.station })
  kept('hw.' + m.id, D.bumpHighWater(hw, msgNo))
  const bag = { uid: d.id + '.' + msgNo.seq, callsign: form.callsign.trim(), status: form.status, location: D.parseLocation(form.location || ''),
                notes: form.notes.trim(), date: new Date().toISOString(), msgNo, dev: d.id, by: d.name || d.id }
  await write('rt-log', logVariable(m), d.id + '.' + msgNo.seq, bag)
  return bag
}
export { D }
