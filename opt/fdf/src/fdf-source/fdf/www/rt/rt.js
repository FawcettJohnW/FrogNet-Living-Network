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
//   (rt-who, <mission>, <dev>)                   latest   the device: name, station (its hostname), team
//   (rt-msg, <mission>, <dev>.<ms>)              distinct an announcement, to everyone
//   (rt-dm, <mission>.<to station>, <dev>.<ms>)  distinct a direct message (the addressee reads its own variable)
//   (rt-team@<team>, <mission>, <dev>.<ms>)      distinct a team message, in the team's own RAM [TEAM_RAM_V1]
//   (rt-notes, <mission>, <dev>.<ms>)            distinct an ICS-214 activity line or an after-action note [RT_214_V1]
//   (rt-pos, <mission>, <dev>)                   latest   [DEVICE_POSITION_V1] the device's own current fix; the map is
//                                                         a query over them. Never derived from the log: a position in a
//                                                         log entry is the scribe's record of what was said
// <dev> is this browser's own id (kept in localStorage, made once). [STATION_IS_HOSTNAME_V1] Its station ID is its host
// name, from the lease the node gave it (connect(): ram.php whoami), or, when the device sent no host name, the name
// typed for the device. Nobody sees a station ID as such: what users see is the host or device name. The message number is rules.js's (nextMsgNo / msgNoShort /
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
// [TEAM_RAM_V1] a family in a team's RAM is named "<service>@<team>" here: ram.php is told the team, and this device's own
// truth there is kept and written again apart from the general RAM's
const fam = (s) => { const k = s.indexOf('@'); return k < 0 ? { svc: s, team: '' } : { svc: s.slice(0, k), team: s.slice(k + 1) } }
export const inTeam = (svc, team) => svc + '@' + team
async function post(s, v, i, bag) {
  const { svc, team } = fam(s), body = { s: svc, v, i, bag }; if (team) body.team = team
  return call(RAM + '?op=write', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
}
async function readJ(s, v, after, wait, ram) {
  const { svc, team } = fam(s), o = { op: 'read', s: svc, v }; if (team) o.team = team
  if (after !== undefined) { o.after = after; o.wait = wait ?? 25; if (ram) o.ram = ram }
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
  const j = await post(s, v, i, b)
  diag('write#' + n, 'answer', Math.round(performance.now() - t0) + 'ms', 'ram=' + j.ram, JSON.stringify(j).slice(0, 200))
  keep(s, v, i, b); return j
}
const note = (t) => (window.RT_NOTE || console.log)(t)                  // a page sets RT_NOTE to show it
async function again(s, v, cells) {
  const me = device().id, mine = ownOf(s, v)
  for (const r of cells.values()) if (r.bag && r.bag.dev === me && !mine.has(r.instance)) keep(s, v, r.instance, r.bag)
  const missing = [...ownOf(s, v)].filter(([i]) => !cells.has(i))
  for (const [i, bag] of missing) await post(s, v, i, bag)
  if (missing.length) diag('own truth written again', missing.length, s, v, 'on', cells.ram)   // the console's, not the user's
}

// [OWN_TRUTH_AGAIN_V1] all of it: the first time any read says the RAM is another one, every family this device owns
// is read there and what is missing written again, watched on this page or not (its position, say). Per RAM: the general
// RAM's families when the general RAM moves, a team's when that team's does
const onRam = new Map()                                           // 'general' or the team -> the RAM it was last on
function ownFamilies() {
  const out = new Set([...own.keys()])
  try { for (let k = 0; k < localStorage.length; k++) { const n = localStorage.key(k)
          if (n.startsWith('rt.own.')) { const t = n.slice(7), d = t.indexOf('.'); if (d > 0) out.add(t.slice(0, d) + '\u0000' + t.slice(d + 1)) } } }
  catch (e) { }
  return [...out].map(k => k.split('\u0000'))
}
async function againAll(key, ram) {
  if (!ram || onRam.get(key) === ram) return
  const moved = onRam.has(key); onRam.set(key, ram)
  if (moved) writeDevice().catch((e) => note('this device\'s access record not written again yet: ' + e.message))
  for (const [s, v] of ownFamilies()) {
    if ((fam(s).team || 'general') !== key) continue
    try { const j = await readJ(s, v), cells = new Map((j.rows || []).map(r => [r.instance, r])); cells.ram = j.ram; await again(s, v, cells) }
    catch (e) { note('this device\'s own ' + s + ' not written again yet: ' + e.message); onRam.delete(key) }
  }
}

// every cell of s.v now, then each newer one as it is written: held reads, no polling. onChange(Map instance -> row).
// [RAM_IS_SAID_V1] the node says which RAM manager each answer came from; when that changes (or a read failed: it may be
// moving) everything is read again there, and this device's own cells that are missing are written again.
// a held read lasts at most HOLD_S: the longest a page stays on a RAM manager its node has left (a held read on it cannot
// know); 8 s keeps "every screen again within seconds" of a post's uplink coming back
const HOLD_S = 8
// [ONE_HELD_READ_PER_PAGE_V1] every family a page watches is held in ONE request (ram.php op=watch): one web server
// worker and one browser connection per page, however many families it shows. A family being added or stopped
// ends the held request and the next one carries the new set.
const fams = new Map()
let famSeq = 0, holding = null, looping = false
function take(f, rows) { for (const r of rows) { f.after = Math.max(f.after, r.id); const o = f.cells.get(r.instance); if (!o || o.id < r.id) f.cells.set(r.instance, r) } }
async function full(f) {
  try {
    const j = await readJ(f.s, f.v)
    f.cells = new Map(); f.after = 0; f.ram = j.ram || f.ram; take(f, j.rows || []); f.full = false
    if (f.failed) { f.failed = false; note('') }                  // it answers again: the page's "did not answer" goes
    f.onChange(f.cells)
    f.cells.ram = f.ram; await again(f.s, f.v, f.cells); againAll(fam(f.s).team || 'general', f.ram)
  } catch (e) { f.failed = true; (f.onError || console.error)(e); f.retry = Date.now() + 2000 }
}
async function loop() {
  if (looping) return
  looping = true
  try {
    while (fams.size) {
      let pending                                                    // every family read in full before holding: a family
      while ((pending = [...fams.values()].filter(f => f.full && !(f.retry > Date.now()))).length)   // added meanwhile too
        for (const f of pending) await full(f)
      const held = [...fams.values()].filter(f => !f.full)
      if (!held.length) { await new Promise(r => setTimeout(r, 500)); continue }
      const w = held.map(f => { const { svc, team } = fam(f.s), o = { s: svc, v: f.v, after: f.after, ram: f.ram || '' }; if (team) o.team = team; return o })
      const ac = new AbortController(); holding = ac
      const n = ++seq, t0 = performance.now(); diag('watch#' + n, 'ask', held.map(f => f.s + ' ' + f.v + ' after ' + f.after).join('; '))
      try {
        const j = await call(RAM + '?' + q({ op: 'watch', w: JSON.stringify(w), wait: HOLD_S }), { signal: ac.signal })
        diag('watch#' + n, 'answer', Math.round(performance.now() - t0) + 'ms', (j.got || []).map(g => held[g.k].s + ' rows=' + g.rows.length + ' ids=' + g.rows.map(r => r.id).join(',') + ' ram=' + g.ram).join('; ') || 'none')
        for (const g of j.got || []) { const f = held[g.k]; if (!fams.has(f.id)) continue
          if (g.ram && f.ram && g.ram !== f.ram) { f.full = true; continue }          // on another RAM now: read everything there
          if (g.rows.length) { take(f, g.rows); f.onChange(f.cells) } }
      } catch (e) {
        if (ac.signal.aborted) { diag('watch#' + n, 'ended: the set of families changed'); continue }
        diag('watch#' + n, 'failed', Math.round(performance.now() - t0) + 'ms', e.message)
        for (const f of held) { f.failed = true; (f.onError || console.error)(e); f.full = true }
        await new Promise(r => setTimeout(r, 2000))
      } finally { if (holding === ac) holding = null }
    }
  } finally { looping = false }
}
export function watch(s, v, onChange, onError) {
  const f = { id: ++famSeq, s, v, onChange, onError, cells: new Map(), after: 0, ram: null, full: true }
  fams.set(f.id, f); if (holding) holding.abort(); loop()
  return () => { fams.delete(f.id); if (holding) holding.abort() }
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
const stationOf = (d) => d.host || String(d.name || '').trim().toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 30)
export const shownName = (d) => d.host || d.name || ''          // what users see for a device
export function setDevice(name) { const d = { ...device(), name }; d.station = stationOf(d); kept('device', d); return d }
// [TEAM_RAM_V1] the app's RAMs served now: { general: host:port, <team>: host:port, ... }
export async function rams() { return (await call(RAM + '?op=rams&app=rangertrak')).rams || {} }
// this device in the mission's directory: who it is, its station, its team (its own cell; DMs are addressed by station)
export async function present(m) { const d = device(); return write('rt-who', m.id, d.id, { name: d.name || shownName(d), host: d.host || '', station: d.station, team: d.team || '' }) }
// what this device sent, from its own store: [variable, instance, bag] of every family s it owns whose variable starts so
export function sent(s, prefix) {
  const out = []
  for (const [fs, v] of ownFamilies()) if (fs === s && v.startsWith(prefix)) for (const [i, b] of ownOf(s, v)) out.push([v, i, b])
  return out
}
// the client connects first: its station ID from the node, then its role, before anything is shown or written.
// [APP_ACCESS_V1] login is a read at this device's own edge (ram.php checks the password against the access tuples).
// A device that has not logged in takes its default role, or none when the app allows it; when the app requires its
// user's login, connect() says so (e.needsLogin) and the page asks for it. The role is the device's own truth: kept here,
// and written as its access record, again after every RAM change
export const APP = 'rangertrak'
export async function connect(defaultRole) {
  const j = await call(RAM + '?op=whoami')
  let d = { ...device(), host: j.host || '' }; d.station = stationOf(d); kept('device', d)
  if (!kept('access')) {
    try { await login({ role: defaultRole || '' }) }
    catch (e) { const x = new Error('log in: ' + e.message); x.needsLogin = true; throw x }
    d = device()
  }
  await writeDevice()
  return d
}
export async function login({ user, password, role }) {
  const j = await call(RAM + '?op=login&app=' + APP, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                       body: JSON.stringify({ user: user || '', password: password || '', role: role || '' }) })
  kept('access', { role: j.role || '', user: j.user || '' }); kept('device', { ...device(), team: j.role || '', user: j.user || '' })
  return j
}
export function logout() { kept('access', null); kept('device', { ...device(), team: '', user: '' }) }
// the device's own access record, in the network RAM beside the app's configuration: written at connect and again
// whenever a RAM it uses is another one (everybody reasserts its own)
async function writeDevice() {
  const d = device()
  return call(RAM + '?op=device&app=' + APP, { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ dev: d.id, station: d.station || '', role: d.team || '', user: d.user || '' }) })
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
                                         acc: p.coords.accuracy, alt: p.coords.altitude, at: Math.round(p.timestamp / 1000) })   // Meshtastic's Position has the same
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
  if (!d.station) throw new Error('name this device first (it sent the post no host name)')
  const mine = [...entries.values()].filter(r => r.instance.startsWith(d.id + '.')).map(r => r.bag)
  const hw = kept('hw.' + m.id) || {}
  const msgNo = D.nextMsgNo(mine, hw, { mission: m.name, opPeriod: m.opPeriod, station: d.station })
  kept('hw.' + m.id, D.bumpHighWater(hw, msgNo))
  const bag = { uid: d.id + '.' + msgNo.seq, callsign: form.callsign.trim(), status: form.status, location: D.parseLocation(form.location || ''),
                notes: form.notes.trim(), date: new Date().toISOString(), msgNo, dev: d.id, by: d.name || d.id,
                ...(form.f213 ? { generates213: true, ...form.f213 } : {}) }   // [RT_213_V1] opt-in per entry
  await write('rt-log', logVariable(m), d.id + '.' + msgNo.seq, bag)
  return bag
}
export { D }
