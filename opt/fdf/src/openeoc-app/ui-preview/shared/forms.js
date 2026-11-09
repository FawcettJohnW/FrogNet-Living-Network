// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// shared/forms.js -- [UI_PREVIEW_V1] the forms EOCAdmin fills: each one is data (its fields) and a prefill from the
// incident's records, so a new form is an entry here. The ICS form numbers and titles are FEMA's; the fields are the
// form's blocks, not its exact layout.
'use strict'
window.FORMS = (() => {
  const i = () => App.incident() || {}
  const op = () => (i().opPeriod || {})
  const head = () => [
    { k: 'incident', label: '1. Incident name', wide: true },
    { k: 'opFrom', label: 'Operational period from', type: 'datetime' }, { k: 'opTo', label: 'Operational period to', type: 'datetime' }]
  const headV = () => ({ incident: i().name, opFrom: op().start, opTo: op().end })
  const prep = [{ k: 'prepared', label: 'Prepared by (name, position)' }, { k: 'preparedAt', label: 'Date / time', type: 'datetime' }]
  const now = () => App.local()
  const lines = (xs) => xs.join('\n')
  const staffLines = () => Model.staff().filter(s => s.name).map(s => `${s.section} · ${s.position}: ${s.name}${s.agency ? ' (' + s.agency + ')' : ''}${s.checkedIn ? '' : ' [out]'}`)
  const orgLine = (o) => { const st = Model.status(o.id) || {}; return `${o.name} (${(FEMA.apps[o.app] || {}).name}): ${st.state || 'no report'}${st.note ? ' · ' + st.note : ''}` }
  const F = [
    { id: '201', name: 'ICS 201 · Incident Briefing', purpose: 'The initial briefing: situation, objectives, actions so far, organization, resources.',
      fields: () => head().concat([{ k: 'situation', label: 'Situation summary', type: 'textarea', rows: 5 }, { k: 'objectives', label: 'Current objectives', type: 'textarea' },
        { k: 'actions', label: 'Actions taken so far', type: 'textarea' }, { k: 'org', label: 'Current organization', type: 'textarea' },
        { k: 'resources', label: 'Resources on hand', type: 'textarea' }], prep),
      prefill: () => Object.assign(headV(), { situation: lines(Model.activeOrgs().map(orgLine)), objectives: lines(Model.objectives().filter(o => o.state === 'Open').map(o => '• ' + o.text)),
        actions: lines(Model.events().slice(0, 12).reverse().map(e => App.when(e.at) + ' ' + e.text)), org: lines(staffLines()),
        resources: lines(Model.activeOrgs().map(o => o.name + ' · ' + (FEMA.apps[o.app] || {}).name)) }) },
    { id: '202', name: 'ICS 202 · Incident Objectives', purpose: 'The objectives and command emphasis for the operational period.',
      fields: () => head().concat([{ k: 'objectives', label: 'Objectives', type: 'textarea', rows: 6 }, { k: 'emphasis', label: 'Operational period command emphasis', type: 'textarea' },
        { k: 'awareness', label: 'General situational awareness', type: 'textarea' }, { k: 'safety', label: 'Site safety plan required?', type: 'select', options: ['No', 'Yes'] }], prep),
      prefill: () => Object.assign(headV(), { objectives: lines(Model.objectives().filter(o => Number(o.opPeriod) === Number(op().n)).map((o, n) => (n + 1) + '. ' + o.text)) }) },
    { id: '203', name: 'ICS 203 · Organization Assignment List', purpose: 'Who holds each position.',
      fields: () => head().concat([{ k: 'list', label: 'Positions and names', type: 'textarea', rows: 10 }], prep), prefill: () => Object.assign(headV(), { list: lines(staffLines()) }) },
    { id: '204', name: 'ICS 204 · Assignment List', purpose: "One organization's assignment for the operational period.",
      fields: () => head().concat([{ k: 'to', label: 'Organization', type: 'select', options: Model.activeOrgs().map(o => [o.id, o.name]) },
        { k: 'work', label: 'Work assignments', type: 'textarea' }, { k: 'instructions', label: 'Special instructions', type: 'textarea' },
        { k: 'comms', label: 'Communications (name, frequency, phone)', type: 'textarea', rows: 2 }], prep),
      prefill: (v) => { const to = (v && v.to) || (Model.activeOrgs()[0] || {}).id
        return Object.assign(headV(), { to, work: lines(Model.assigns().filter(a => a.to === to).map(a => `• ${a.what}${a.where ? ' — ' + a.where : ''} [${a.state}]`)),
          instructions: lines(Model.assigns().filter(a => a.to === to && a.notes).map(a => a.notes)) }) } },
    { id: '205', name: 'ICS 205 · Incident Radio Communications Plan', purpose: 'Channels, frequencies and assignments.',
      fields: () => head().concat([{ k: 'plan', label: 'Zone / channel / function / frequency / assignment', type: 'textarea', rows: 8 },
        { k: 'instructions', label: 'Special instructions', type: 'textarea' }], prep),
      prefill: () => Object.assign(headV(), { plan: 'Command · ARES net · 147.000+ · EOC ↔ shelters\nTactical 1 · simplex 146.520 · teams in the field\nFrogNet · OpenEOC messages · all organizations' }) },
    { id: '206', name: 'ICS 206 · Medical Plan', purpose: 'Medical aid stations, transport, hospitals, and procedures.',
      fields: () => head().concat([{ k: 'hospitals', label: 'Hospitals (status, beds)', type: 'textarea' }, { k: 'transport', label: 'Ambulance services', type: 'textarea' },
        { k: 'procedures', label: 'Medical emergency procedures', type: 'textarea' }], prep),
      prefill: () => Object.assign(headV(), {
        hospitals: lines(Model.activeOrgs().filter(o => o.app === 'hospital').map(o => { const s = Model.status(o.id) || {}; return `${o.name}: ${s.state || '?'} · ED ${s.edBeds ?? '?'} · ICU ${s.icuBeds ?? '?'} · Med/Surg ${s.medsurg ?? '?'} · power ${s.power || '?'}` })),
        transport: lines(Model.activeOrgs().filter(o => o.app === 'ambulance').map(o => { const s = Model.status(o.id) || {}; return `${o.name}: ${s.level || ''} ${s.state || '?'}${s.dest ? ' → ' + s.dest : ''}` })) }) },
    { id: '207', name: 'ICS 207 · Incident Organization Chart', purpose: 'The organization, by section.',
      fields: () => head().concat([{ k: 'chart', label: 'The organization, by section', type: 'textarea', rows: 10 }], prep),
      prefill: () => { const by = {}; for (const s of Model.staff().filter(x => x.name)) (by[s.section] = by[s.section] || []).push('   ' + s.position + ': ' + s.name)
        return Object.assign(headV(), { chart: Object.entries(by).map(([k, v]) => k + '\n' + v.join('\n')).join('\n') }) } },
    { id: '208', name: 'ICS 208 · Safety Message / Plan', purpose: 'The safety message for the operational period.',
      fields: () => head().concat([{ k: 'message', label: 'Safety message', type: 'textarea', rows: 6 }, { k: 'plan', label: 'Site safety plan located at' }], prep), prefill: headV },
    { id: '209', name: 'ICS 209 · Incident Status Summary', purpose: 'The status summary sent up: situation, lifelines, resources, needs.',
      fields: () => head().concat([{ k: 'type', label: 'Incident type' }, { k: 'level', label: 'EOC activation level' },
        { k: 'lifelines', label: 'Community lifelines', type: 'textarea', rows: 8 }, { k: 'orgs', label: 'Organizations reporting', type: 'textarea', rows: 6 },
        { k: 'needs', label: 'Critical resource needs', type: 'textarea' }, { k: 'outlook', label: 'Projected outlook', type: 'textarea' }], prep),
      prefill: () => Object.assign(headV(), { type: i().type, level: (FEMA.levels.find(l => l.id === Number(i().level)) || {}).name,
        lifelines: lines(FEMA.lifelines.map(l => { const s = Model.lifeline(l.id); return `${l.name}: ${(FEMA.lifelineStatus.find(x => x.id === s.status) || {}).name}${s.own && s.own.note ? ' — ' + s.own.note : ''}` })),
        orgs: lines(Model.activeOrgs().map(orgLine)),
        needs: lines(Model.requests().filter(r => !['Arrived', 'Demobilized', 'Denied'].includes(r.state)).map(r => `${r.priority}: ${r.qty} × ${r.resource} for ${Model.name(r.from)} [${r.state}]`)) }) },
    { id: '211', name: 'ICS 211 · Incident Check-In List', purpose: 'Everyone and everything that checked in.',
      fields: () => head().concat([{ k: 'list', label: 'Checked in (name, agency, position or category, time)', type: 'textarea', rows: 10 }], prep),
      prefill: () => Object.assign(headV(), { list: lines(Model.staff().filter(s => s.name && s.checkedIn).map(s => `${s.name} · ${s.agency || ''} · ${s.position} · ${App.when(s.at)}`)
        .concat(Model.activeOrgs().map(o => `${o.name} · ${(FEMA.apps[o.app] || {}).name} · lead ${o.lead} · ${App.when(o.claimedAt)}`))) }) },
    { id: '213', name: 'ICS 213 · General Message', purpose: 'A message on paper: to, from, subject, message, reply.',
      fields: () => [{ k: 'incident', label: 'Incident name', wide: true }, { k: 'to', label: 'To (name, position)' }, { k: 'from', label: 'From (name, position)' },
        { k: 'subject', label: 'Subject', wide: true }, { k: 'at', label: 'Date / time', type: 'datetime' }, { k: 'message', label: 'Message', type: 'textarea', rows: 6 },
        { k: 'approved', label: 'Approved by' }, { k: 'reply', label: 'Reply', type: 'textarea', rows: 3 }],
      prefill: () => ({ incident: i().name, at: now() }) },
    { id: '213RR', name: 'ICS 213RR · Resource Request Message', purpose: 'A request, its approvals, ordering and its source.',
      fields: () => [{ k: 'incident', label: 'Incident name', wide: true }, { k: 'request', label: 'Request', type: 'select', options: Model.requests().map(r => [r.id, `${Model.name(r.from)}: ${r.qty} × ${r.resource}`]) },
        { k: 'detail', label: 'Order (qty, kind, type, priority, needed by, deliver to)', type: 'textarea', rows: 4 },
        { k: 'steps', label: 'Approvals and ordering (who, when, note)', type: 'textarea', rows: 5 }, { k: 'source', label: 'Supplier / source' }],
      prefill: (v) => { const r = Model.requests().find(x => x.id === (v && v.request)) || Model.requests()[0] || {}
        return { incident: i().name, request: r.id, detail: r.id ? `${r.qty} × ${r.resource} · ${r.priority} · needed by ${App.when(r.needBy)} · to ${r.deliverTo || ''}\n${r.why || ''}` : '',
          steps: lines((r.history || []).map(h => `${App.when(h.at)} ${h.state} · ${h.by}${h.note ? ' · ' + h.note : ''}`)) } } },
    { id: '214', name: 'ICS 214 · Activity Log', purpose: "A position's log for the operational period (EOCAdmin › Activity logs keeps them).",
      fields: () => head().concat([{ k: 'name', label: 'Name' }, { k: 'position', label: 'ICS position' }, { k: 'log', label: 'Activity log (time, notable activity)', type: 'textarea', rows: 10 }], prep),
      prefill: (v) => { const p = (v && v.position) || ''
        return Object.assign(headV(), { position: p, log: lines(Store.read('adm-log').map(t => t.bag).filter(e => !p || e.position === p).reverse().map(e => App.when(e.at) + '  ' + e.text)) }) } },
    { id: '215', name: 'ICS 215 · Operational Planning Worksheet', purpose: 'Work assignments and the resources they need, for planning the next period.',
      fields: () => head().concat([{ k: 'rows', label: 'Organization · assignment · resources required · have · need', type: 'textarea', rows: 10 }], prep),
      prefill: () => Object.assign(headV(), { rows: lines(Model.assigns().map(a => `${Model.name(a.to)} · ${a.what} · · ·`)) }) },
    { id: '221', name: 'ICS 221 · Demobilization Check-Out', purpose: 'A resource released from the incident.',
      fields: () => [{ k: 'incident', label: 'Incident name', wide: true }, { k: 'resource', label: 'Resource or person released' }, { k: 'at', label: 'Release date / time', type: 'datetime' },
        { k: 'checks', label: 'Units checked out with (supply, comms, documentation, finance)', type: 'textarea', rows: 4 }, { k: 'remarks', label: 'Remarks', type: 'textarea' }], prefill: () => ({ incident: i().name, at: now() }) },
    { id: 'sitrep', name: 'Situation Report (SitRep)', purpose: "The EOC's periodic report to the jurisdiction and the state.",
      fields: () => head().concat([{ k: 'summary', label: 'Summary', type: 'textarea', rows: 4 }, { k: 'lifelines', label: 'Lifelines', type: 'textarea', rows: 8 },
        { k: 'orgs', label: 'Organizations', type: 'textarea', rows: 6 }, { k: 'requests', label: 'Requests outstanding', type: 'textarea' },
        { k: 'events', label: 'Significant events', type: 'textarea', rows: 6 }, { k: 'next', label: 'Next report', type: 'datetime' }], prep),
      prefill: () => Object.assign(FORMS.get('209').prefill(), { summary: `${i().name}, ${i().type}, ${(FEMA.levels.find(l => l.id === Number(i().level)) || {}).name}. Op period ${op().n}.`,
        requests: lines(Model.requests().filter(r => !['Arrived', 'Demobilized', 'Denied'].includes(r.state)).map(r => `${r.qty} × ${r.resource} for ${Model.name(r.from)} [${r.state}]`)),
        events: lines(Model.events().slice(0, 15).reverse().map(e => App.when(e.at) + ' ' + e.text)) }) },
  ]
  return { all: F, get: (id) => F.find(f => f.id === id), iap: ['202', '203', '204', '205', '206', '208'] }
})()
