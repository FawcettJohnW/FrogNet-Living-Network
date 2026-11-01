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
export const msgNoShort = (n) => n ? n.station + String(n.seq).padStart(3, '0') : ''
export const msgNoFull = (n) => n ? [n.mission, n.opPeriod ? 'OP ' + n.opPeriod : '', msgNoShort(n)].filter(Boolean).join(' / ') : ''

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
