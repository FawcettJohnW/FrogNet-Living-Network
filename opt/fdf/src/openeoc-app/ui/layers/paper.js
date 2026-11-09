// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/paper.js -- [FEMA_EOC_V1] EOCAdmin: the EOC's paperwork. The ICS forms (201, 202, 203, 204, 205, 206, 207, 208,
// 209, 211, 213, 213RR, 214, 215, 221) and the situation report, each one filled in from what the boards and the
// organizations on the network have recorded (fema.js, ics.js, medical.js); kept as a draft, made final, printed. The
// Incident Action Plan for an operational period, from its forms. The after-action report and improvement plan (HSEEP).
// A form is a record on the forms board (its fields in it), so every EOCAdmin on the network sees it and its history.
// Needs the ics and fema layers.
'use strict'
RT.layer('paper', (RT) => {
  RT.role('eocadmin', { label: 'EOCAdmin', title: 'OpenEOC EOCAdmin', field: 'login', who: () => 'EOCAdmin', kinds: 'all', teams: 'all',
                        overview: true, log: true, act: ['Forms', 'paper'], req: ['IAP', 'paper-iap'], connected() { document.body.classList.add('paperwide'); RT.open('paper') } })
  const F = RT.fema, B = RT.icsBoards
  B.forms = { title: 'Forms (EOCAdmin)', status: ['Draft', 'Final'], fields: [['form', 'Form', 'text', true], ['title', 'Title', 'text'], ['opPeriod', 'Operational period', 'number'], ['values', 'Its fields', 'area']],
              list: ['form', 'title', 'opPeriod'], updates: [['text', 'Comment', 'area']] }
  B.aar = { title: 'After-action report', single: true, overviewWrites: true, fields: [['overview', 'Overview (dates, scope, organizations)', 'area'],
    ['objectives', 'Objectives and core capabilities assessed', 'area'], ['strengths', 'Strengths', 'area'], ['afi', 'Areas for improvement', 'area'], ['conclusion', 'Conclusion', 'area']] }
  B.ip = { title: 'Improvement plan', status: ['Open', 'In progress', 'Complete'], fields: [['issue', 'Issue / area for improvement', 'text', true], ['action', 'Corrective action', 'area', true],
    ['capability', 'Core capability', 'text'], ['owner', 'Responsible organization / POC', 'text'], ['start', 'Start', 'dt'], ['done', 'Completion', 'dt']],
    list: ['issue', 'owner', 'done'], updates: [['text', 'Comment', 'area']] }

  // ------------------------------------------------------------ what the forms are filled from
  const L = (xs) => xs.filter(x => x != null && x !== '').join('\n')
  const dt = (v) => v ? new Date(v).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''
  const at = (t) => t ? new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''
  const nowLocal = () => { const d = new Date(), z = (n) => String(n).padStart(2, '0'); return d.getFullYear() + '-' + z(d.getMonth() + 1) + '-' + z(d.getDate()) + 'T' + z(d.getHours()) + ':' + z(d.getMinutes()) }
  const inc = () => F.inc(), op = () => F.opNow()
  const head = () => [['incident', 'Incident name', 'text'], ['opFrom', 'Operational period from', 'dt'], ['opTo', 'Operational period to', 'dt']]
  const headV = () => ({ incident: inc().name, opFrom: inc().opFrom, opTo: inc().opTo })
  const prep = [['prepared', 'Prepared by (name, position)', 'text'], ['preparedAt', 'Date / time prepared', 'dt']]
  const prepV = () => ({ prepared: S.cfg.name ? S.cfg.name + ', EOCAdmin' : '', preparedAt: nowLocal() })
  const open = (b) => F.recs(b).filter(r => !['Complete', 'Closed', 'Canceled', 'Rejected', 'Dropped', 'Off duty', 'Demobilizing'].includes(r.status))
  const orgLines = () => { const o = F.orgs(), out = []
    for (const [k, by] of Object.entries(o)) for (const [n, ds] of Object.entries(by)) out.push(`${n} (${(F.CAT[k] || [k])[0]}): ${ds.length} on now${ds.map(d => d.status).filter(Boolean).length ? ', ' + [...new Set(ds.map(d => d.status).filter(Boolean))].join(', ') : ''}`)
    return out }
  const staffLines = () => Object.entries(F.struct()).flatMap(([sec, ps]) => ps.map(p => { const x = F.recs('staff').find(s => s.post === p && s.status !== 'Off duty'); return x ? `${sec} · ${p}: ${x.person}${x.agency ? ' (' + x.agency + ')' : ''}` : null }))
  const llLines = () => { const rp = F.reports(); return F.LL.map(([k, n]) => { const c = F.call(n), st = c ? c.status || 'Unknown' : F.worst(rp[k].map(x => x[1])); return `${n}: ${st}${c && c.why ? ' — ' + c.why : ''}` }) }
  const rrLines = () => open('rr').map(r => `${r.priority || ''}: ${r.quantity || ''} ${r.resource || r.title} for ${r.name || ''} [${r.status || 'New request'}]`)
  const sig = () => F.recs('events').filter(e => e.significant || e.status === 'Posted to significant events').reverse().map(e => `${at(e.at)} ${e.subject}${e.description ? ': ' + e.description : ''}`)
  const BEDS = [['ed', 'ED'], ['icu', 'ICU'], ['medsurg', 'Med/Surg'], ['peds', 'Peds'], ['burn', 'Burn'], ['or', 'OR']]

  // a form: fields as the boards' ([key, label, type]: text | area | number | dt | [choices] | function), and its fill
  const FORMS = [
    { id: '201', name: 'ICS 201 · Incident Briefing', purpose: 'The initial briefing: situation, objectives, actions so far, organization, resources.',
      fields: () => head().concat([['situation', 'Situation summary', 'area'], ['objectives', 'Current objectives', 'area'], ['actions', 'Actions taken so far', 'area'],
        ['org', 'Current organization', 'area'], ['resources', 'Resources on the network', 'area']], prep),
      fill: () => Object.assign(headV(), prepV(), { situation: L([inc().summary].concat(llLines())), objectives: L(open('objectives').map(o => '• ' + o.objective)),
        actions: L(sig()), org: L(staffLines()), resources: L(orgLines()) }) },
    { id: '202', name: 'ICS 202 · Incident Objectives', purpose: 'The objectives and command emphasis for the operational period.',
      fields: () => head().concat([['objectives', 'Objectives', 'area'], ['emphasis', 'Operational period command emphasis', 'area'], ['awareness', 'General situational awareness', 'area'],
        ['safety', 'Site safety plan required', ['No', 'Yes']]], prep),
      fill: () => Object.assign(headV(), prepV(), { objectives: L(F.recs('objectives').filter(o => o.status !== 'Dropped' && (+o.opPeriod || op()) === op()).map((o, i) => (i + 1) + '. ' + o.objective + (o.priority ? ' (' + o.priority + ')' : ''))),
        awareness: L(llLines()) }) },
    { id: '203', name: 'ICS 203 · Organization Assignment List', purpose: 'Who holds each EOC position.',
      fields: () => head().concat([['list', 'Positions and names', 'area'], ['orgs', 'Organizations on the network', 'area']], prep),
      fill: () => Object.assign(headV(), prepV(), { list: L(staffLines()), orgs: L(orgLines()) }) },
    { id: '204', name: 'ICS 204 · Assignment List', purpose: "One organization's assignments for the operational period.",
      fields: () => head().concat([['to', 'Organization', () => [...new Set(F.recs('assign').map(a => a.to))].sort()], ['work', 'Work assignments', 'area'],
        ['instructions', 'Special instructions', 'area'], ['comms', 'Communications', 'area']], prep),
      fill: (v) => { const to = (v && v.to) || (F.recs('assign')[0] || {}).to || '', as = F.recs('assign').filter(a => a.to === to && (+a.opPeriod || op()) === op())
        return Object.assign(headV(), prepV(), { to, work: L(as.map(a => `• ${a.work}${a.where ? ' — ' + a.where : ''}${a.resources ? ' · ' + a.resources : ''} [${a.status || 'Assigned'}]`)),
          instructions: L(as.map(a => a.instructions)), comms: L([...new Set(as.map(a => a.comms).filter(Boolean))]) }) } },
    { id: '205', name: 'ICS 205 · Incident Radio Communications Plan', purpose: 'Channels, frequencies and who uses them.',
      fields: () => head().concat([['plan', 'Zone / channel / function / frequency / assignment', 'area'], ['instructions', 'Special instructions', 'area']], prep),
      fill: () => Object.assign(headV(), prepV(), { plan: L(['FrogNet · OpenEOC messages and boards · every organization on the network'].concat([...new Set(F.recs('assign').map(a => a.comms).filter(Boolean))])) }) },
    { id: '206', name: 'ICS 206 · Medical Plan', purpose: 'Hospitals, ambulances and medical emergency procedures.',
      fields: () => head().concat([['hospitals', 'Hospitals (status, beds available)', 'area'], ['transport', 'Ambulances', 'area'], ['procedures', 'Medical emergency procedures', 'area']], prep),
      fill: () => Object.assign(headV(), prepV(), {
        hospitals: L(Object.values(S.fac || {}).map(f => `${f.name}: ${f.diversion || '?'} · ${BEDS.map(([k, n]) => (f.beds || {})[k] != null ? n + ' ' + f.beds[k] : null).filter(Boolean).join(', ')} · power ${f.power || '?'}${f.needs ? ' · needs: ' + f.needs : ''}`)),
        transport: L(Object.values((F.orgs().ambulance) || {}).map(ds => `${cap(ds[0].unit || ds[0].team || '')}: ${ds.map(d => d.status).filter(Boolean).join(', ') || 'on the network'}`)) }) },
    { id: '207', name: 'ICS 207 · Incident Organization Chart', purpose: 'The organization, by section.',
      fields: () => head().concat([['chart', 'The organization, by section', 'area']], prep),
      fill: () => Object.assign(headV(), prepV(), { chart: Object.entries(F.struct()).map(([sec, ps]) => sec + '\n' + ps.map(p => { const x = F.recs('staff').find(s => s.post === p && s.status !== 'Off duty'); return '    ' + p + ': ' + (x ? x.person : 'vacant') }).join('\n')).join('\n') }) },
    { id: '208', name: 'ICS 208 · Safety Message / Plan', purpose: 'The safety message for the operational period.',
      fields: () => head().concat([['message', 'Safety message', 'area'], ['plan', 'Site safety plan located at', 'text']], prep), fill: () => Object.assign(headV(), prepV()) },
    { id: '209', name: 'ICS 209 · Incident Status Summary', purpose: 'The status summary sent up: situation, lifelines, organizations, needs.',
      fields: () => head().concat([['ictype', 'Incident complexity', 'text'], ['level', 'EOC activation level', 'text'], ['lifelines', 'Community lifelines', 'area'],
        ['orgs', 'Organizations reporting', 'area'], ['needs', 'Critical resource needs', 'area'], ['events', 'Significant events', 'area'], ['outlook', 'Projected outlook', 'area']], prep),
      fill: () => Object.assign(headV(), prepV(), { ictype: inc().ictype, level: inc().level, lifelines: L(llLines()), orgs: L(orgLines()), needs: L(rrLines()), events: L(sig()) }) },
    { id: '211', name: 'ICS 211 · Check-In List', purpose: 'Everyone who checked in: Command Center staff and the organizations.',
      fields: () => head().concat([['list', 'Checked in (name, agency, position or organization, time)', 'area']], prep),
      fill: () => Object.assign(headV(), prepV(), { list: L(F.recs('staff').filter(s => s.status !== 'Off duty').map(s => `${s.person} · ${s.agency || ''} · ${s.post} · ${at(s.at)}`)
        .concat(F.recs('signin').filter(s => s.status !== 'Signed out').map(s => `${s.person} · ${s.agency || ''} · ${s.post || ''} · ${at(s.at)}`)).concat(orgLines())) }) },
    { id: '213', name: 'ICS 213 · General Message', purpose: 'A message on paper: to, from, subject, message, reply.',
      fields: () => [['incident', 'Incident name', 'text'], ['to', 'To (name, position)', 'text'], ['from', 'From (name, position)', 'text'], ['subject', 'Subject', 'text'],
        ['at', 'Date / time', 'dt'], ['message', 'Message', 'area'], ['approved', 'Approved by', 'text'], ['reply', 'Reply', 'area']],
      fill: () => ({ incident: inc().name, at: nowLocal(), from: S.cfg.name || '' }) },
    { id: '213RR', name: 'ICS 213RR · Resource Request Message', purpose: 'One request: the order, its approvals and ordering, its source.',
      fields: () => [['incident', 'Incident name', 'text'], ['request', 'Request', () => F.recs('rr').map(r => r.id)], ['order', 'Order (quantity, kind, type, priority, needed by, deliver to)', 'area'],
        ['steps', 'Approvals and ordering (when, who, what)', 'area'], ['source', 'Supplier / source', 'text']],
      fill: (v) => { const r = F.recs('rr').find(x => x.id === (v && v.request)) || F.recs('rr')[0] || {}
        return { incident: inc().name, request: r.id || '', order: r.id ? L([`${r.title}: ${r.quantity || ''} ${r.unit || ''} ${r.resource || ''}`, `${r.priority || ''} · needed by ${dt(r.due)}`, r.deliverTo ? 'deliver to ' + r.deliverTo : '', r.justification]) : '',
          steps: r.id ? L(Object.entries(S.icsAct.rr || {}).filter(([k]) => k.startsWith(r.id + '~')).map(([, a]) => a).sort((x, y) => x.at - y.at).map(a => `${at(a.at)} ${a.name}: ${Object.entries(a).filter(([k, x]) => !['by', 'name', 'position', 'at'].includes(k) && x !== '').map(([k, x]) => k + ' ' + x).join(' · ')}`)) : '' } } },
    { id: '214', name: 'ICS 214 · Activity Log', purpose: "A position's log for the operational period, from its position-log entries.",
      fields: () => head().concat([['person', 'Name', 'text'], ['position', 'Position', () => [...new Set(F.recs('ics214').map(e => e.position).filter(Boolean))]], ['log', 'Activity log (time, notable activity)', 'area']], prep),
      fill: (v) => { const p = (v && v.position) || (F.recs('ics214')[0] || {}).position || '', es = F.recs('ics214').filter(e => e.position === p).reverse()
        return Object.assign(headV(), prepV(), { position: p, person: (es[0] || {}).name || '', log: L(es.map(e => `${at(e.at)}  ${e.type ? e.type + ': ' : ''}${e.details}`)) }) } },
    { id: '215', name: 'ICS 215 · Operational Planning Worksheet', purpose: 'Assignments and the resources they need, for the next period.',
      fields: () => head().concat([['rows', 'Organization · assignment · resources required · have · need', 'area']], prep),
      fill: () => Object.assign(headV(), prepV(), { rows: L(open('assign').map(a => `${a.to} · ${a.work} · ${a.resources || ''} · · `)) }) },
    { id: '221', name: 'ICS 221 · Demobilization Check-Out', purpose: 'A resource or person released from the incident.',
      fields: () => [['incident', 'Incident name', 'text'], ['resource', 'Resource or person released', 'text'], ['at', 'Release date / time', 'dt'],
        ['checks', 'Checked out with (supply, communications, documentation, finance)', 'area'], ['remarks', 'Remarks', 'area']], fill: () => ({ incident: inc().name, at: nowLocal() }) },
    { id: 'sitrep', name: 'Situation Report', purpose: "The Command Center's periodic report to the jurisdiction and the state.",
      fields: () => head().concat([['summary', 'Summary', 'area'], ['lifelines', 'Community lifelines', 'area'], ['orgs', 'Organizations', 'area'], ['requests', 'Resource requests outstanding', 'area'],
        ['events', 'Significant events', 'area'], ['next', 'Next report', 'dt']], prep),
      fill: () => Object.assign(headV(), prepV(), { summary: L([`${inc().name || ''}, ${inc().ictype || ''}, ${inc().level || ''}, operational period ${op()}.`, inc().summary]),
        lifelines: L(llLines()), orgs: L(orgLines()), requests: L(rrLines()), events: L(sig()) }) },
  ]
  const IAP = ['202', '203', '204', '205', '206', '208']
  const form = (id) => FORMS.find(f => f.id === id)
  RT.paperForms = FORMS

  // ------------------------------------------------------------ the forms
  const kept = () => F.recs('forms').map(r => { let v = {}; try { v = JSON.parse(r.values || '{}') } catch (e) { } return Object.assign({}, r, { v }) })
  RT.screen('paper', () => {
    const ks = kept()
    sheet('paper', 'EOCAdmin · Forms', `<div class="meta">Each form opens filled in from the boards and the organizations on the network. Change what you need; keep it as a draft or make it final; print it.</div>
      <div class="sens">${FORMS.map(f => `<button type="button" onclick="RT.open('paper-form','${f.id}')"><b>${esc(f.name)}</b> <span class="meta">${esc(f.purpose)}</span></button>`).join('')}</div>
      <h3>Kept</h3><table class="ics"><tr><th>Form</th><th>Title</th><th>Op</th><th>Status</th><th>Saved</th></tr>
      ${ks.map(k => `<tr onclick="RT.open('paper-form','${esc(k.form)}','${esc(k.id)}')"><td>${esc((form(k.form) || {}).name || k.form)}</td><td>${esc(k.title || '')}</td><td>${esc(k.opPeriod || '')}</td><td>${esc(k.status || 'Draft')}</td><td>${esc(at(k.at))}</td></tr>`).join('') || '<tr><td colspan="5" class="meta">none yet</td></tr>'}</table>
      <div class="sens"><button type="button" onclick="RT.open('paper-iap')"><b>Incident Action Plan</b> <span class="meta">ICS ${IAP.join(', ')} for the period</span></button>
        <button type="button" onclick="RT.open('paper-aar')"><b>After-action report and improvement plan</b> <span class="meta">HSEEP</span></button></div>`)
  })
  const ed = { }    // the form open: its fields as typed (a redraw keeps them)
  const fieldHtml = (f, v) => {
    const [k, label, t0] = f, t = typeof t0 === 'function' ? t0() : t0, e = esc(v ?? '')
    if (Array.isArray(t)) return `<label>${esc(label)}<select name="${k}">${[''].concat(t).map(o => `<option${String(o) === String(v ?? '') ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select></label>`
    if (t === 'area') return `<label class="wide">${esc(label)}<textarea name="${k}" rows="${Math.min(14, Math.max(3, String(v || '').split('\n').length + 1))}">${e}</textarea></label>`
    return `<label>${esc(label)}<input name="${k}" type="${t === 'number' ? 'number' : t === 'dt' ? 'datetime-local' : 'text'}" value="${e}"></label>`
  }
  const typed = () => { const o = {}; document.querySelectorAll('#sheet .paperform [name]').forEach(x => { o[x.name] = x.value }); return o }
  RT.screen('paper-form', (fid, rid) => {
    const f = form(fid); if (!f) return
    const k = rid ? kept().find(x => x.id === rid) : null
    if (ed.key !== fid + '/' + (rid || '')) { ed.key = fid + '/' + (rid || ''); ed.v = k ? k.v : f.fill(); ed.rid = rid || '' }
    const fin = k && k.status === 'Final'
    sheet('paper-form', f.name, `<div class="noprint"><button type="button" onclick="RT.open('paper')">◂ Forms</button>
        ${fin ? '<b>Final</b>: read only' : `<button type="button" id="pfFill">Fill again from the records</button> <button type="button" id="pfDraft">Keep as draft</button> <button type="button" class="go" id="pfFinal">Make final</button>`}
        <button type="button" id="pfPrint">Print</button></div>
      <div class="paper"><h2>${esc(f.name)}</h2><div class="meta">${esc(f.purpose)}${k ? ' · ' + esc(k.status || 'Draft') + ', ' + esc(at(k.at)) + ' by ' + esc(k.name || '') : ''}</div>
      <form class="paperform">${f.fields().map(x => fieldHtml(x, ed.v[x[0]])).join('')}</form></div>`)
    const pf = document.querySelector('#sheet .paperform')
    if (fin) pf.querySelectorAll('[name]').forEach(x => { x.disabled = true })
    pf.oninput = pf.onchange = () => { ed.v = typed() }
    // a pick that names another record (204's organization, 213RR's request, 214's position) fills from that one
    pf.querySelectorAll('select[name=to], select[name=request], select[name=position]').forEach(s => { s.onchange = () => { ed.v = f.fill(typed()); RT.open('paper-form', fid, rid) } })
    const save = (st) => {
      const v = typed(), bag = { form: fid, title: v.subject || v.to || v.position || f.name, opPeriod: op(), values: JSON.stringify(v), status: st }
      if (!ed.rid) { const id = S.cfg.dev.replace(/[^A-Za-z0-9]/g, '').slice(-8) + '-' + Date.now(); send({ op: 'icsWrite', board: 'forms', id, bag }); ed.rid = id; ed.key = fid + '/' + id }
      else send({ op: 'icsAct', board: 'forms', id: ed.rid, bag })
      status(f.name + (st === 'Final' ? ': made final' : ': kept as a draft'))
    }
    if (!fin) {
      $('#pfFill').onclick = () => { ed.v = f.fill(typed()); RT.open('paper-form', fid, rid) }
      $('#pfDraft').onclick = () => save('Draft')
      $('#pfFinal').onclick = () => { if (confirmFinal()) save('Final') }
    }
    $('#pfPrint').onclick = () => printSheet(f.name)
  })
  // made final: read only from then on (a change is a new form); asked on the sheet, no browser dialog
  function confirmFinal() {
    const b = $('#pfFinal')
    if (b.dataset.sure) return true
    b.dataset.sure = '1'; b.textContent = 'Final: read only from now. Make it final'; return false
  }
  /** [FEMA_EOC_V1] printing is the native side's: the Android print service, the desktop's printer dialog */
  function printSheet(title) {
    if (!(window.rtNative && window.rtNative.print)) { status('⚠ this app cannot print (no native print)'); return }
    document.body.classList.add('printing'); window.rtNative.print('OpenEOC · ' + title)
    setTimeout(() => document.body.classList.remove('printing'), 1500)
  }

  // ------------------------------------------------------------ the IAP
  RT.screen('paper-iap', () => {
    const ks = kept().filter(k => (+k.opPeriod || op()) === op())
    sheet('paper-iap', 'Incident Action Plan · op period ' + op(), `<div class="noprint"><button type="button" onclick="RT.open('paper')">◂ Forms</button> <button type="button" id="iapPrint">Print the IAP</button>
      <div class="meta">ICS ${IAP.join(', ')} for this period: each one the final kept for it, else its newest draft, else filled from the records now.</div></div>
      ${IAP.map(id => { const f = form(id), k = ks.filter(x => x.form === id).sort((a, b) => (b.status === 'Final') - (a.status === 'Final') || b.at - a.at)[0], v = k ? k.v : f.fill()
        return `<div class="paper"><h2>${esc(f.name)} <span class="meta">${k ? esc(k.status || 'Draft') : 'from the records'}</span></h2><table class="ics rec">${f.fields().map(x => `<tr><th>${esc(x[1])}</th><td>${esc(v[x[0]] ?? '').replace(/\n/g, '<br>')}</td></tr>`).join('')}</table></div>` }).join('')}`)
    $('#iapPrint').onclick = () => printSheet('Incident Action Plan, op period ' + op())
  })

  // ------------------------------------------------------------ the after-action report and improvement plan
  RT.screen('paper-aar', () => {
    const a = RT.icsNow('aar', 'aar'), ip = F.recs('ip')
    sheet('paper-aar', 'After-action report', `<div class="noprint"><button type="button" onclick="RT.open('paper')">◂ Forms</button>
        <button type="button" onclick="S.icsBack='paper-aar';RT.open('ics-new','aar',Object.assign(${esc(JSON.stringify({ overview: `${inc().name || ''}. Organizations: ${orgLines().map(l => l.split(' (')[0]).join(', ')}.`,
          objectives: F.recs('objectives').map(o => '• ' + o.objective + ' [' + (o.status || 'Open') + ']').join('\n') }))},RT.icsNow('aar','aar')))">${a.at ? 'Edit' : 'Write'} the report</button>
        <button type="button" onclick="S.icsBack='paper-aar';RT.open('ics-new','ip')">Add to the improvement plan</button> <button type="button" id="aarPrint">Print</button></div>
      <div class="paper"><h2>After-action report</h2>${a.at ? `<table class="ics rec">${B.aar.fields.map(f => `<tr><th>${esc(f[1])}</th><td>${esc(a[f[0]] ?? '').replace(/\n/g, '<br>')}</td></tr>`).join('')}</table>` : '<div class="meta">not written yet</div>'}
      <h2>Improvement plan</h2><table class="ics"><tr><th>Issue</th><th>Corrective action</th><th>Capability</th><th>Owner</th><th>Start</th><th>Complete</th><th>Status</th></tr>
      ${ip.map(x => `<tr onclick="RT.open('ics-rec','ip','${esc(x.id)}')"><td>${esc(x.issue)}</td><td>${esc(x.action)}</td><td>${esc(x.capability || '')}</td><td>${esc(x.owner || '')}</td><td>${esc(dt(x.start))}</td><td>${esc(dt(x.done))}</td><td>${esc(x.status || 'Open')}</td></tr>`).join('') || '<tr><td colspan="7" class="meta">none yet</td></tr>'}</table></div>`)
    $('#aarPrint').onclick = () => printSheet('After-action report')
  })
  RT.hook('refresh', (what) => {
    if (what !== 'ics') return
    if (S.sheet === 'paper' || S.sheet === 'paper-iap' || S.sheet === 'paper-aar') RT.open(S.sheet)
  })
})
