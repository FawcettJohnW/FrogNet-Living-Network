////////////////////////////////////////////////////////////////
//  Copyright (C) 2016-2026 Fawcett Innovations LLC           //
//  SPDX-License-Identifier: GPL-2.0-only                     //
////////////////////////////////////////////////////////////////
// rules.js -- [RT_PAGES_V1] the radio log's rules, pure (no page, no storage): message numbers, statuses, coordinates.
// The forms people already use: a message number is {mission, opPeriod, station, seq}, printed "A007" and
// "26-0142 / OP 2 / A007", stamped once by the device that logs the entry and never changed. Each device counts its own:
// the cell instance is <device>.<seq>, so two devices can never collide.

export function nextMsgNo(mine, highWater, ctx) {
  // 1 + the larger of this device's high-water mark and its highest seq in this op period at this station
  let max = highWater?.[ctx.opPeriod + '\u0000' + ctx.station] ?? 0
  for (const e of mine) { const n = e.msgNo; if (n && n.opPeriod === ctx.opPeriod && n.station === ctx.station && n.seq > max) max = n.seq }
  return { mission: ctx.mission, opPeriod: ctx.opPeriod, station: ctx.station, seq: max + 1 }
}
export function bumpHighWater(highWater, n) {
  const k = n.opPeriod + '\u0000' + n.station
  return { ...highWater, [k]: Math.max(highWater?.[k] ?? 0, n.seq) }
}
// [STATION_IS_HOSTNAME_V1] the station ID is the device's hostname: "A007" for a one-letter one, "phone-f-007" otherwise
export const msgNoShort = (n) => n ? n.station + (String(n.station).length > 1 ? '-' : '') + String(n.seq).padStart(3, '0') : ''
export const msgNoFull = (n) => n ? [n.mission, n.opPeriod ? 'OP ' + n.opPeriod : '', msgNoShort(n)].filter(Boolean).join(' / ') : ''

// the handled mark on an entry: blank, then ✓ handled, → passed on, ! needs action, and back to blank
export const MARKS = ['handled', 'passed', 'action']
export const MARK_GLYPH = { handled: '✓', passed: '→', action: '!' }
export const nextMark = (m) => !m ? MARKS[0] : MARKS[MARKS.indexOf(m) + 1] || ''

export const STATUSES = [
  ['normal', '#5b7fa6'], ['location-report', '#3a8f5c'], ['evidence-report', '#8a5cb8'], ['need-rest-food', '#c98a14'],
  ['incident-check-in', '#2f8f8f'], ['incident-check-out', '#6b6b6b'], ['urgent', '#c0392b'],
]
export const statusColor = (s) => (STATUSES.find(x => x[0] === s) || [s, '#888'])[1]

// how long since an entry, in bands of the check-in interval: 0 within it, then 1..6, 6 at three intervals or more
export function overdueBand(minutesSince, intervalMin = 30) {
  if (!(minutesSince >= 0) || !(intervalMin > 0)) return 0
  return Math.min(6, Math.floor((minutesSince / intervalMin) * 2))
}

// ---- coordinates: decimal degrees, degrees decimal minutes, degrees minutes seconds, Maidenhead
const hemi = (v, lng) => (v < 0 ? (lng ? 'W' : 'S') : (lng ? 'E' : 'N'))
export function ddm(v, lng) { const a = Math.abs(v), d = Math.floor(a), m = (a - d) * 60; return d + '°' + m.toFixed(3) + "'" + hemi(v, lng) }
export function dms(v, lng) { const a = Math.abs(v), d = Math.floor(a), mm = (a - d) * 60, m = Math.floor(mm), s = (mm - m) * 60; return d + '°' + m + "'" + s.toFixed(1) + '"' + hemi(v, lng) }
export function maidenhead(lat, lng) {
  const L = lng + 180, A = lat + 90, up = (i) => String.fromCharCode(65 + i), lo = (i) => String.fromCharCode(97 + i)
  return up(Math.floor(L / 20)) + up(Math.floor(A / 10)) + Math.floor((L % 20) / 2) + Math.floor(A % 10) +
         lo(Math.floor(((L % 2) / 2) * 24)) + lo(Math.floor((A % 1) * 24))
}
export function isMaidenhead(t) { return /^[A-R]{2}[0-9]{2}([a-x]{2})?$/i.test(t.trim()) }
export function fromMaidenhead(t) {
  const s = t.trim(); if (!isMaidenhead(s)) return null
  const c = (i) => s.toUpperCase().charCodeAt(i) - 65
  let lng = c(0) * 20 - 180 + (+s[2]) * 2, lat = c(1) * 10 - 90 + (+s[3])
  if (s.length === 6) { lng += c(4) * (2 / 24) + 1 / 24; lat += c(5) * (1 / 24) + 1 / 48 } else { lng += 1; lat += 0.5 }
  return { lat, lng }
}
export function formatLatLng(lat, lng, f = 'DD') {
  if (f === 'DDM') return ddm(lat, false) + ' ' + ddm(lng, true)
  if (f === 'DMS') return dms(lat, false) + ' ' + dms(lng, true)
  if (f === 'Maidenhead') return maidenhead(lat, lng)
  return lat.toFixed(5) + ', ' + lng.toFixed(5)
}
// a location as typed: "47.6, -122.3", a Maidenhead locator, or a place
export function parseLocation(text) {
  const t = (text || '').trim(), ll = t.match(/^(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)$/)
  if (ll && Math.abs(+ll[1]) <= 90 && Math.abs(+ll[2]) <= 180) return { lat: +ll[1], lng: +ll[2], text: t }
  const m = fromMaidenhead(t); if (m) return { ...m, text: t }
  return { lat: null, lng: null, text: t }
}

// ---- the mission summary [RT_SUMMARY_V1]: OpenEOC's domain/mission-summary.ts (b6745f49) on this app's cells.
// What the cells do not carry is left out, not faked: revisions, late entries (no logged-at apart from the date),
// the source, tactical calls. "Needs attention" is the statuses OpenEOC flags by default.
export const FLAGGED = new Set(['urgent', 'need-rest-food', 'evidence-report'])
export const BUCKET_HOURS_CHOICES = [1, 2, 3, 6, 12, 24]
export const MAX_BUCKETS = 48
export const NO_TEAM_LABEL = 'No team / not on roster'
export const NOT_RECORDED_LABEL = '(not recorded)'
export const NO_STATION_LABEL = '(no call sign)'
export const NO_AREA_LABEL = 'No area'
const floorToBucket = (d, h) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), Math.floor(d.getHours() / h) * h)
function bucketStarts(from, to, hours, limit) {
  const first = floorToBucket(from, hours), starts = [first]
  while (starts.length <= limit) {
    const n = starts.length, next = new Date(first.getFullYear(), first.getMonth(), first.getDate(), first.getHours() + n * hours)
    if (next.getTime() >= to.getTime()) break
    starts.push(next)
  }
  return starts
}
function chooseBuckets(from, to) {
  for (const hours of BUCKET_HOURS_CHOICES) { const starts = bucketStarts(from, to, hours, MAX_BUCKETS); if (starts.length <= MAX_BUCKETS) return { hours, starts } }
  return { hours: 24, starts: bucketStarts(from, to, 24, Number.MAX_SAFE_INTEGER) }
}
function tally(labels) {
  const c = new Map(); for (const l of labels) c.set(l, (c.get(l) ?? 0) + 1)
  return [...c].map(([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
}
// entries: [{ id, bag }] (the log's rows); rangers: roster bags; notes: openeoc-notes bags; window: {from, to} or null
export function summarizeMission({ entries: rows, rangers, notes, window, now }) {
  const dated = rows.map(r => ({ e: r.bag, id: r.id, t: new Date(r.bag.date).getTime() })).filter(x => Number.isFinite(x.t)).sort((a, b) => a.t - b.t || a.id - b.id)
  const wholeLog = window === null
  let from, to, soFar = false
  if (window) { from = window.from; to = window.to; if (to.getTime() > now.getTime()) { to = now; soFar = true } }
  else if (dated.length) { from = new Date(dated[0].t); to = new Date(dated[dated.length - 1].t) }
  else { from = now; to = now }
  const inWindow = wholeLog ? dated : dated.filter(x => x.t >= from.getTime() && x.t <= to.getTime())
  const entries = inWindow.map(x => x.e), isAttention = (e) => FLAGGED.has(e.status)
  const stationLabel = (e) => (e.callsign || '').trim() || NO_STATION_LABEL
  const bucketEnd = wholeLog && inWindow.length ? new Date(to.getTime() + 1) : to
  const { hours, starts } = chooseBuckets(from, bucketEnd)
  const items = starts.map(start => ({ start, count: 0, attentionCount: 0 }))
  for (const { e, t } of inWindow) {
    let i = Math.min(items.length - 1, Math.max(0, Math.floor((t - starts[0].getTime()) / (hours * 3600000))))
    while (i < items.length - 1 && t >= starts[i + 1].getTime()) i++
    while (i > 0 && t < starts[i].getTime()) i--
    items[i].count++; if (isAttention(e)) items[i].attentionCount++
  }
  const sc = new Map(); for (const e of entries) sc.set(e.status, (sc.get(e.status) ?? 0) + 1)
  const byStatus = []
  for (const [s, color] of STATUSES) if (sc.get(s)) byStatus.push({ status: s, color, count: sc.get(s), inSettings: true })
  for (const [s, count] of sc) if (!byStatus.some(r => r.status === s)) byStatus.push({ status: s, color: '#A3A3A3', count, inSettings: false })
  const byCall = new Map(); for (const r of rangers) if (r.callsign && !byCall.has(r.callsign.toUpperCase())) byCall.set(r.callsign.toUpperCase(), r)
  const teamOf = (e) => byCall.get((e.callsign || '').toUpperCase())?.team?.trim() || NO_TEAM_LABEL
  const ts = new Map()
  for (const e of entries) { const t = teamOf(e), m = ts.get(t) ?? new Map(); m.set(e.status, (m.get(e.status) ?? 0) + 1); ts.set(t, m) }
  const byTeamStatus = {}; for (const [t, m] of ts) byTeamStatus[t] = byStatus.filter(r => m.has(r.status)).map(r => ({ status: r.status, count: m.get(r.status) }))
  const st = new Map()
  for (const { e, t } of inWindow) { const s = stationLabel(e), r = st.get(s); if (r) { r.count++; if (t > r.lastHeard.getTime()) r.lastHeard = new Date(t) } else st.set(s, { station: s, count: 1, lastHeard: new Date(t) }) }
  const byStation = [...st.values()].sort((a, b) => b.count - a.count || a.station.localeCompare(b.station))
  const attention = inWindow.filter(x => isAttention(x.e)).sort((a, b) => b.t - a.t || b.id - a.id).map(x => x.e)
  const inNote = (n) => wholeLog || (new Date(n.createdAt).getTime() >= from.getTime() && new Date(n.createdAt).getTime() <= to.getTime())
  const wn = notes.filter(n => n.kind !== 'activity').filter(inNote)
  const incident = wn.filter(n => n.about !== 'app').sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt))
  const gm = new Map()
  for (const n of incident) { const area = (n.area || '').trim(), k = area.toLowerCase(), g = gm.get(k); if (g) g.notes.push(n); else gm.set(k, { area: area || NO_AREA_LABEL, notes: [n] }) }
  return { generatedAt: now, window: { from, to, soFar, wholeLog },
           totals: { entries: entries.length, stations: st.size, first: inWindow.length ? new Date(inWindow[0].t) : null,
                     last: inWindow.length ? new Date(inWindow[inWindow.length - 1].t) : null, attention: attention.length },
           buckets: { hours, items }, byStatus, byTeam: tally(entries.map(teamOf)), byTeamStatus, byStation,
           byOperator: tally(entries.map(e => (e.by || '').trim() || NOT_RECORDED_LABEL)), attention,
           aar: { groups: [...gm.values()], incidentCount: incident.length, appCount: wn.length - incident.length } }
}

// ---- the ICS-213 [RT_213_V1]: OpenEOC's ics213FieldsFromReport (b6745f49), the form's own field names
const p2 = (n) => String(n).padStart(2, '0')
export const formDate = (d) => p2(d.getMonth() + 1) + '/' + p2(d.getDate()) + '/' + d.getFullYear()     // mm/dd/yyyy, never the locale's
export const formTime = (d) => p2(d.getHours()) + ':' + p2(d.getMinutes())                              // 24-hour
export function ics213Fields(e, mission) {
  const d = new Date(e.date)
  return { '1 Incident Name Optional': mission?.event || mission?.name || '', '2 To Name and Position': (e.recipients213 || []).join(', '),
           '3 From Name and Position': e.callsign || '', '4 Subject': e.subject213 || '', '5 Date': formDate(d), '6 Time': formTime(d),
           '7 Message': e.message213 || '', '8 Approved by Name': e.by || '' }
}
