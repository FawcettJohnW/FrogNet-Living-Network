// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// test_rules.mjs -- [RT_SUMMARY_V1][RT_213_V1] RangerTrak's own cases (b6745f49: domain/mission-summary.spec.ts and
// shared/export/ics213-pdf.spec.ts's field mapping) against rules.js, translated only where this app's cells differ:
// an entry is a log row {id, bag}, statuses are the app's own (urgent is flagged), a ranger is its roster bag.
// Left out: the cases on fields the cells do not carry (rangerUid, tactical calls, source, loggedAt/late, revisions,
// per-mission status lists and flagOnMap overrides).     usage: node test_rules.mjs
import * as D from './rules.js'
let fails = 0, n = 0
const eq = (a, b, what) => { n++; const ok = JSON.stringify(a) === JSON.stringify(b); console.log((ok ? 'PASS  ' : 'FAIL  ') + what + (ok ? '' : ': got ' + JSON.stringify(a) + ', want ' + JSON.stringify(b))); if (!ok) fails++ }
const at = (d, h, min = 0) => new Date(2026, 9, d, h, min)
let nextId = 1
const entry = (when, over = {}) => ({ id: nextId++, bag: { callsign: 'K7AAA', date: when.toISOString(), status: 'normal', notes: '', ...over } })
const note = (createdAt, over = {}) => ({ createdAt: createdAt.toISOString(), text: 'x', about: 'incident', kind: 'aar', ...over })
const NOW = at(20, 12)
const run = (over = {}) => D.summarizeMission({ entries: [], rangers: [], notes: [], window: null, now: NOW, ...over })

{ const s = run()
  eq([s.totals.entries, s.totals.stations, s.totals.first, s.totals.last, s.buckets.items.length, s.buckets.items[0].count, s.byTeam, s.attention, s.window.wholeLog, s.window.soFar, +s.generatedAt],
     [0, 0, null, null, 1, 0, [], [], true, false, +NOW], 'handles an empty log') }
{ const s = run({ entries: [entry(at(15, 10)), entry(at(15, 8, 30)), entry(at(15, 14))] })
  eq([+s.window.from, +s.window.to, s.totals.entries, +s.totals.first, +s.totals.last], [+at(15, 8, 30), +at(15, 14), 3, +at(15, 8, 30), +at(15, 14)], 'whole log: the window is the first and last entry times') }
{ const s = run({ entries: [entry(at(15, 7, 59)), entry(at(15, 8)), entry(at(15, 12)), entry(at(15, 12, 1))], window: { from: at(15, 8), to: at(15, 12) } })
  eq([s.totals.entries, s.window.wholeLog], [2, false], 'clips to the window: entries before and after are excluded, the edges are included') }
{ const s = run({ entries: [entry(at(20, 9)), entry(at(20, 13))], window: { from: at(20, 8), to: at(20, 18) } })
  eq([s.window.soFar, +s.window.to, s.totals.entries, run({ window: { from: at(19, 8), to: at(19, 18) } }).window.soFar], [true, +NOW, 1, false], 'an op period whose end is still in the future is summarised "so far"') }
const sizeFor = (hours) => { const s = run({ entries: [entry(at(1, 0))], window: { from: at(1, 0), to: new Date(2026, 9, 1, hours) } }); return { hours: s.buckets.hours, bars: s.buckets.items.length } }
eq(sizeFor(6), { hours: 1, bars: 6 }, '6 h window: hourly bars')
eq(sizeFor(47), { hours: 1, bars: 47 }, '47 h window: still hourly')
eq(sizeFor(48), { hours: 1, bars: 48 }, '48 h window: still hourly, exactly 48 bars')
eq(sizeFor(49), { hours: 2, bars: 25 }, '49 h window: 2-hour bars')
eq(sizeFor(9 * 24), { hours: 6, bars: 36 }, '9 days: 6-hour bars')
eq(sizeFor(40 * 24).bars <= 48, true, 'never more than 48 bars while a 24 h bucket can manage it')
{ const s = run({ entries: [entry(at(14, 22, 45)), entry(at(15, 1, 10))], window: { from: at(14, 22, 30), to: at(15, 2, 15) } })
  eq([s.buckets.hours, s.buckets.items.map(b => +b.start), s.buckets.items.map(b => b.count)], [1, [at(14, 22), at(14, 23), at(15, 0), at(15, 1), at(15, 2)].map(Number), [1, 0, 0, 1, 0]],
     'starts at the window start floored to the bucket, and crosses midnight without gaps') }
eq(run({ entries: [entry(at(15, 8)), entry(at(15, 12, 30))] }).buckets.items.map(b => b.count), [1, 0, 0, 0, 1], 'zero-fills the quiet hours')
{ const s = run({ entries: [entry(at(15, 8)), entry(at(15, 8, 5), { status: 'urgent' }), entry(at(15, 9))] })
  eq([s.buckets.items[0].count, s.buckets.items[0].attentionCount, s.buckets.items[1].attentionCount], [2, 1, 0], 'counts attention entries within each bar') }
{ const s = run({ entries: [entry(at(14, 7)), entry(at(16, 20))] })
  eq([s.buckets.hours, +s.buckets.items[0].start], [2, +at(14, 6)], 'floors multi-hour bars to a multiple of the bar size') }
{ const s = run({ entries: [entry(at(15, 8), { callsign: 'W7ABC' }), entry(at(15, 9), { callsign: 'K7AAA' }), entry(at(15, 10), { callsign: 'w7abc' })], rangers: [{ callsign: 'W7ABC', team: 'Alpha' }, { callsign: 'K7AAA', team: '  ' }] })
  eq(s.byTeam, [{ label: 'Alpha', count: 2 }, { label: D.NO_TEAM_LABEL, count: 1 }], 'no ranger, or a blank team, is "No team / not on roster", sorted by count') }
{ const s = run({ entries: [entry(at(15, 8), { status: 'urgent' }), entry(at(15, 9)), entry(at(15, 10), { status: 'urgent' })] })
  eq([s.attention.map(e => e.date), s.totals.attention], [[at(15, 10).toISOString(), at(15, 8).toISOString()], 2], 'lists flagged statuses newest first') }
{ const s = run({ entries: [entry(at(15, 8))], window: { from: at(15, 0), to: at(15, 23) },
                  notes: [note(at(15, 12), { text: 'c', area: 'communications ' }), note(at(15, 9), { text: 'a', area: 'Communications' }), note(at(15, 10), { text: 'b' }),
                          note(at(15, 11), { text: 'd', area: '   ' }), note(at(15, 13), { text: 'e', area: 'Logistics' })] })
  eq([s.aar.groups.map(g => [g.area, g.notes.map(n => n.text)]), s.aar.incidentCount], [[['Communications', ['a', 'c']], [D.NO_AREA_LABEL, ['b', 'd']], ['Logistics', ['e']]], 5],
     'groups incident notes by area across case and blanks, oldest first, first spelling wins') }
{ const s = run({ notes: [note(at(15, 9), { about: 'app' }), note(at(15, 10), { about: 'app' }), note(at(15, 11))] })
  eq([s.aar.appCount, s.aar.groups.flatMap(g => g.notes).length], [2, 1], 'counts RangerTrak notes without listing them') }
{ const s = run({ notes: [note(at(15, 9), { kind: 'activity' }), note(at(15, 10))] })
  eq([s.aar.incidentCount, s.aar.appCount, s.aar.groups.flatMap(g => g.notes).length], [1, 0, 1], 'never counts or groups an ICS-214 activity line') }
{ const notes = [note(at(14, 9)), note(at(15, 9)), note(at(16, 9))]
  eq([run({ notes, window: { from: at(15, 0), to: at(15, 23) } }).aar.incidentCount, run({ notes, entries: [entry(at(15, 8))] }).aar.incidentCount], [1, 3], 'keeps only notes made inside a chosen window, but all of them for the whole log') }
{ const s = run({ notes: [note(at(15, 9))], window: { from: at(15, 0), to: at(15, 23) } })
  eq([s.totals.entries, s.aar.incidentCount], [0, 1], 'still shows the notes when the window has no entries') }
{ const f = D.ics213Fields({ date: new Date(2026, 2, 4, 7, 5).toISOString(), callsign: 'W7ABC', subject213: 'Water', message213: 'need 20 L', recipients213: ['IC', 'EOC'], by: 'scribe-a' }, { name: '26-0142' })
  eq([f['2 To Name and Position'], f['3 From Name and Position'], f['4 Subject'], f['5 Date'], f['6 Time'], f['7 Message'], f['8 Approved by Name'], f['1 Incident Name Optional']],
     ['IC, EOC', 'W7ABC', 'Water', '03/04/2026', '07:05', 'need 20 L', 'scribe-a', '26-0142'], 'the 213: mm/dd/yyyy, 24-hour time, recipients joined, subject and message, approved by the scribe') }
console.log('RESULT ' + (fails ? 'FAIL: ' + fails + ' of ' + n : 'PASS ' + n + ' of ' + n)); process.exit(fails ? 1 : 0)
