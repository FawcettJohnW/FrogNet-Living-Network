////////////////////////////////////////////////////////////////
//  Copyright (C) 2016-2026 Fawcett Innovations LLC           //
//  SPDX-License-Identifier: GPL-2.0-only                     //
////////////////////////////////////////////////////////////////
// rt.js -- [RT_PAGES_V1] the radio log app, served by the post's node: what every page shares.
//
// The cells (DESIGN.md, the truth table), through the node's ram.php (the DisasterComm add-in door, ../dc/ram.php):
//   (rt-mission, settings, <mission>)            latest   the scribe who started it: name, event, op period
//   (rt-log, <mission>.<op>, <dev>.<seq>)        distinct one cell per entry, written once by the device that logged it
//   (rt-handled, <mission>, <entry>.<dev>)       latest   [MARK_PER_HANDLER_V1] each handler's own mark on an entry; the
//                                                         mark shown is derived: the newest across handlers (markOf)
//   (rt-pos, <mission>, <dev>)                   latest   [DEVICE_POSITION_V1] the device's own current fix; the map is
//                                                         a query over them. Never derived from the log: a position in a
//                                                         log entry is the scribe's record of what was said
// <dev> is this browser's own id (kept in localStorage, made once). Its station ID is its hostname, from the node
// (connect(): ram.php whoami) [STATION_IS_HOSTNAME_V1]. The message number is rules.js's (nextMsgNo / msgNoShort /
// msgNoFull), stamped once when written.
import * as D from './rules.js'

export const RAM = (window.RT_RAM || '../dc/ram.php')
const q = (o) => Object.entries(o).map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&')

async function call(url, opts) {
  const r = await fetch(url, opts)
  const j = await r.json().catch(() => ({ ok: false, why: 'answer was not JSON (HTTP ' + r.status + ')' }))
  if (!r.ok || j.ok === false) throw new Error(j.why ? j.why + (j.output ? ': ' + j.output : '') : 'HTTP ' + r.status)
  return j
}
// [DIAG-WATCH] every read and write, with when it was asked and answered: the console of any page shows where a slow
// update waited (the request not started, the RAM not answering, or the answer not woken)
const diag = (...a) => console.debug('[DIAG-WATCH]', new Date().toISOString(), ...a)
let seq = 0
async function readJ(s, v, after, wait, ram) {
  const o = { op: 'read', s, v }; if (after !== undefined) { o.after = after; o.wait = wait ?? 25; if (ram) o.ram = ram }
  const n = ++seq, t0 = performance.now(); diag('read#' + n, 'ask', JSON.stringify(o))
  try { const j = await call(RAM + '?' + q(o)); diag('read#' + n, 'answer', Math.round(performance.now() - t0) + 'ms', 'rows=' + (j.rows || []).length,
                                                      'ids=' + (j.rows || []).map(r => r.id).join(','), 'ram=' + j.ram); return j }
  catch (e) { diag('read#' + n, 'failed', Math.round(performance.now() - t0) + 'ms', e.message); throw e }
}
export async function read(s, v, after, wait) { return (await readJ(s, v, after, wait)).rows || [] }

// [OWN_TRUTH_AGAIN_V1] what this device wrote, per (service, variable): written again to the RAM that holds it now, when
// a read finds it missing there (APP-SETUP-AND-OPERATION.md: memory is never moved; each owner writes its own).
// [OWN_TRUTH_KEPT_V1] kept on the device (browser storage) as well, so a reload during a partition still has it
const own = new Map()
function ownOf(s, v) {
  const k = s + '\u0000' + v
  if (!own.has(k)) own.set(k, new Map(Object.entries(kept('own.' + s + '.' + v) || {})))
  return own.get(k)
}
function keep(s, v, i, bag) { const m = ownOf(s, v); m.set(i, bag); kept('own.' + s + '.' + v, Object.fromEntries(m)) }
export async function write(s, v, i, bag) {
  const b = { ...bag, dev: device().id }, n = ++seq, t0 = performance.now(); diag('write#' + n, 'ask', s, v, i)
  const j = await call(RAM + '?op=write', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ s, v, i, bag: b }) })
  diag('write#' + n, 'answer', Math.round(performance.now() - t0) + 'ms', 'ram=' + j.ram, JSON.stringify(j).slice(0, 200))
  keep(s, v, i, b); return j
}
const note = (t) => (window.RT_NOTE || console.log)(t)                  // a page sets RT_NOTE to show it
async function again(s, v, cells) {
  const me = device().id, mine = ownOf(s, v)
  for (const r of cells.values()) if (r.bag && r.bag.dev === me && !mine.has(r.instance)) keep(s, v, r.instance, r.bag)
  const missing = [...ownOf(s, v)].filter(([i]) => !cells.has(i))
  for (const [i, bag] of missing) await call(RAM + '?op=write', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ s, v, i, bag }) })
  if (missing.length) note('this device\'s own ' + missing.length + ' written again (' + s + ', on ' + (cells.ram || 'the RAM it is on now') + ')')
}

// [OWN_TRUTH_AGAIN_V1] all of it: the first time any read says the RAM is another one, every family this device owns
// is read there and what is missing written again, watched on this page or not (its position, say)
let onRam = null
function ownFamilies() {
  const out = new Set([...own.keys()])
  try { for (let k = 0; k < localStorage.length; k++) { const n = localStorage.key(k)
          if (n.startsWith('rt.own.')) { const t = n.slice(7), d = t.indexOf('.'); if (d > 0) out.add(t.slice(0, d) + '\u0000' + t.slice(d + 1)) } } }
  catch (e) { }
  return [...out].map(k => k.split('\u0000'))
}
async function againAll(ram) {
  if (!ram || ram === onRam) return
  onRam = ram
  for (const [s, v] of ownFamilies()) {
    try { const j = await readJ(s, v), cells = new Map((j.rows || []).map(r => [r.instance, r])); cells.ram = j.ram; await again(s, v, cells) }
    catch (e) { note('this device\'s own ' + s + ' not written again yet: ' + e.message); onRam = null }
  }
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
                    cells.ram = ram; await again(s, v, cells); againAll(ram) }
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
export function setDevice(name) { const d = { ...device(), name }; kept('device', d); return d }
// the client connects first: its station ID from the node, before anything is shown or written
export async function connect() {
  const j = await call(RAM + '?op=whoami')
  const d = { ...device(), station: j.station }; kept('device', d); return d
}

// [MARK_PER_HANDLER_V1] the handled marks: each handler writes its own; the mark shown on an entry is the newest
export function markOf(cells) {
  const by = new Map()
  for (const r of cells.values()) { const b = r.bag || {}, o = by.get(b.entry)
    if (b.entry && (!o || b.atMs > o.atMs || (b.atMs === o.atMs && String(b.dev) > String(o.dev)))) by.set(b.entry, b) }
  return by                                                       // entry instance -> the newest mark's bag
}
export async function mark(m, entry, shown) {
  const d = device()
  return write('rt-handled', m.id, entry + '.' + d.id, { entry, mark: D.nextMark(shown), by: d.name || d.id, atMs: Date.now() })
}
export function currentMission() { return kept('mission') }
export function setMission(m) { kept('mission', m) }

// [DEVICE_POSITION_V1] this device's position, as it moves, into its own cell. After a RAM move its last fix is written
// again there with the rest of its own truth (againAll), even when the device has not moved since
export function sharePosition(m) {
  if (!navigator.geolocation) { note('this device gives no position'); return () => {} }
  const d = device()
  const id = navigator.geolocation.watchPosition(
    (p) => write('rt-pos', m.id, d.id, { name: d.name || d.id, lat: p.coords.latitude, lng: p.coords.longitude,
                                         acc: p.coords.accuracy, at: Math.round(p.timestamp / 1000) })
             .catch((e) => note('position not written: ' + e.message)),
    (e) => note('no position from this device (' + e.message + ')'), { enableHighAccuracy: true, maximumAge: 10000 })
  return () => navigator.geolocation.clearWatch(id)
}

export const esc = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
export const missionId = (name) => name.trim().replace(/[^A-Za-z0-9._-]+/g, '-').slice(0, 60)
export const logVariable = (m) => m.id + '.' + m.opPeriod


// write this device's next entry: its message number from its own entries' numbers and its high-water mark, the cell instance <dev>.<seq>, never overwritten
export async function logEntry(m, entries, form) {
  const d = device()
  if (!d.station) throw new Error('this device has no station ID: it is not connected')
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
