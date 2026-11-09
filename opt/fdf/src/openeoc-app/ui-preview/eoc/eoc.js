// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// eoc/eoc.js -- [UI_PREVIEW_V1] the EOC: the incident's live picture as FEMA's EOC function runs it -- activation and
// structure, staffing, the community lifelines, the organizations that joined (their categories are their apps),
// assignments (ICS 204), resource requests (ICS 213RR), objectives (ICS 202), significant events, messages.
'use strict'
;(() => {
  const { $, $$, esc, when, verifier } = App
  const inc = () => App.incident()
  const ME = 'EOC'
  const appOf = (o) => FEMA.apps[o.app] || { name: o.app, icon: '•', status: [] }

  // ------------------------------------------------------------------ the incident
  App.module('incident', 'Incident', (el) => {
    const i = inc()
    if (!i) {
      el.innerHTML = `<h2>No incident</h2><p class="lead">Start one, or load the demo: a county windstorm with eight organizations authorized.</p>
        <div class="row"><button type="button" class="btn pri" id="new">Start an incident</button>
          <button type="button" class="btn" id="demo">Load the demo: authorized, no one joined</button>
          <button type="button" class="btn" id="demoJ">Load the demo: five organizations joined</button></div>`
      $('#new').onclick = () => edit({ name: '', type: 'Type 4', level: 2, structure: 'ics', opPeriod: { n: 1 } }, true)
      $('#demo').onclick = () => { Model.seed(false); App.status('demo loaded: now open Field in another tab and claim a login'); App.show('dash') }
      $('#demoJ').onclick = () => { Model.seed(true); App.status('demo loaded'); App.show('dash') }
      return
    }
    const lv = FEMA.levels.find(l => l.id === Number(i.level)) || {}
    el.innerHTML = `<h2>${esc(i.name)}</h2><p class="lead">${esc(i.type)} · ${esc(lv.name)} · ${esc((FEMA.structures[i.structure] || {}).name)} · ${esc(i.location || '')}</p>
      ${App.table(['Operational period', 'From', 'To'], [[esc(i.opPeriod.n), esc(when(i.opPeriod.start)), esc(when(i.opPeriod.end))]])}
      <div class="row"><button type="button" class="btn pri" id="ed">Change</button><button type="button" class="btn" id="nop">Next operational period</button>
        <button type="button" class="btn bad" id="end">Close the incident</button></div>
      <h3>Activation level</h3><div class="row">${FEMA.levels.map(l => `<button type="button" class="btn${l.id === Number(i.level) ? ' pri' : ''}" data-lv="${l.id}">${esc(l.short)}</button>`).join('')}</div>`
    $('#ed').onclick = () => edit(i)
    $('#nop').onclick = () => {
      const s = i.opPeriod.end || App.local(), e = App.local(new Date(new Date(s).getTime() + 12 * 3600e3))
      Store.write('eoc-incident', 'current', Object.assign({}, i, { opPeriod: { n: Number(i.opPeriod.n) + 1, start: s, end: e } }))
      Model.event('Operational period ' + (Number(i.opPeriod.n) + 1) + ' began', ME)
    }
    $('#end').onclick = () => App.dialog('Close the incident?', '<p>Everything here is erased from this preview (export it first from EOCAdmin › Offline package if you want it).</p>',
      [['Close and erase', () => { Store.reset(); location.reload() }, 'pri']])
    $$('[data-lv]', el).forEach(b => b.onclick = () => { Store.write('eoc-incident', 'current', Object.assign({}, i, { level: Number(b.dataset.lv) })); Model.event('Activation level ' + b.dataset.lv, ME) })
  }, { icon: '🚨' })
  function edit(i, isNew) {
    App.dialog(isNew ? 'Start an incident' : 'The incident', App.form([
      { k: 'name', label: 'Incident name', wide: true }, { k: 'type', label: 'Incident type', type: 'select', options: FEMA.incidentTypes },
      { k: 'level', label: 'Activation level', type: 'select', options: FEMA.levels.map(l => [l.id, l.name]) },
      { k: 'structure', label: 'EOC structure', type: 'select', options: Object.entries(FEMA.structures).map(([k, s]) => [k, s.name]) },
      { k: 'location', label: 'EOC location' }, { k: 'start', label: 'Op period from', type: 'datetime' }, { k: 'end', label: 'Op period to', type: 'datetime' }],
      Object.assign({}, i, (i.opPeriod || {}))), [['Save', (d) => {
        const v = App.collect(d); if (!v.name) { App.status('give the incident a name', true); return false }
        Store.write('eoc-incident', 'current', Object.assign({}, i, v, { level: Number(v.level), opPeriod: { n: (i.opPeriod || {}).n || 1, start: v.start, end: v.end },
          started: i.started || new Date().toISOString() }))
        if (isNew) Model.event('Incident started: ' + v.name, ME)
      }, 'pri']])
  }

  // ------------------------------------------------------------------ the situation
  App.module('dash', 'Situation', (el) => {
    const cats = Model.categories(), reqs = Model.requests().filter(r => !['Arrived', 'Demobilized', 'Denied'].includes(r.state))
    el.innerHTML = `<h2>Situation</h2>
      <h3>Community lifelines</h3><div class="cards">${FEMA.lifelines.map(l => { const s = Model.lifeline(l.id)
        return `<div class="card click" data-ll="${l.id}"><div class="t"><span class="dot ${s.status}"></span>${l.icon} ${esc(l.name)}</div>
          <div class="s">${s.own ? 'EOC: ' + esc((FEMA.lifelineStatus.find(x => x.id === s.status) || {}).name) : s.reports.length ? 'from ' + s.reports.length + ' report(s)' : 'no reports'}</div></div>` }).join('')}</div>
      <h3>On the network</h3>
      ${Object.keys(cats).length ? `<div class="cards">${Object.entries(cats).map(([k, os]) => { const a = FEMA.apps[k] || { name: k, icon: '•' }
        return `<div class="card"><div class="t">${a.icon} ${esc(a.plural || a.name)} <span class="pill">${os.length}</span></div>
          ${os.map(o => { const st = Model.status(o.id); return `<div class="s click" data-org="${esc(o.id)}" style="cursor:pointer"><span class="dot ${Model.sevOf(st)}"></span> ${esc(o.name)} · ${esc(st ? st.state : 'no report')}</div>` }).join('')}</div>` }).join('')}</div>`
        : '<div class="empty">no organization has joined yet: a lead claims their login in the Field app</div>'}
      <h3>Open resource requests <span class="pill">${reqs.length}</span></h3>
      ${App.table(['From', 'Resource', 'Priority', 'State'], reqs.slice(0, 6).map(r => [esc(Model.name(r.from)), esc(r.qty + ' × ' + r.resource), esc(r.priority), `<span class="pill">${esc(r.state)}</span>`]), 'none')}
      <h3>Latest events</h3>${App.table(['When', 'Event', 'By'], Model.events().slice(0, 6).map(e => [esc(when(e.at)), esc(e.text), esc(e.by)]), 'none')}`
    $$('[data-ll]', el).forEach(c => c.onclick = () => lifelineDlg(c.dataset.ll))
    $$('[data-org]', el).forEach(c => c.onclick = () => orgDlg(c.dataset.org))
  }, { icon: '📊', when: inc })

  function lifelineDlg(id) {
    const l = FEMA.lifelines.find(x => x.id === id), s = Model.lifeline(id)
    App.dialog(l.icon + ' ' + l.name, `
      <p class="meta">From the reports: <span class="dot ${s.derived}"></span> ${esc((FEMA.lifelineStatus.find(x => x.id === s.derived) || {}).name)}</p>
      ${App.table(['Organization', 'Reports'], s.reports.map(o => { const st = Model.status(o.id); return [esc(o.name), st ? `<span class="dot ${Model.sevOf(st)}"></span> ` + esc(st.state) + (st.note ? ' · ' + esc(st.note) : '') : 'no report'] }), 'no organization reports on this lifeline yet')}
      <h4>The EOC's call</h4>${App.form([{ k: 'status', label: 'Status', type: 'select', options: [['', '(from the reports)']].concat(FEMA.lifelineStatus.map(x => [x.id, x.name])) },
        { k: 'note', label: 'Why', type: 'textarea', rows: 2 }], s.own || {})}`,
      [['Save', (d) => { const v = App.collect(d)
        if (!v.status) Store.remove('eoc-lifeline', id)
        else { Store.write('eoc-lifeline', id, Object.assign(v, { by: ME })); Model.event(`${l.name}: ${v.status}${v.note ? ' (' + v.note + ')' : ''}`, ME) } }, 'pri']])
  }

  // ------------------------------------------------------------------ the organizations
  App.module('orgs', 'Organizations', (el) => {
    const os = Model.orgs()
    el.innerHTML = `<h2>Organizations</h2><p class="lead">Every organization that claimed its login, grouped by its app. Categories appear as their apps join; the EOC allocates none.</p>
      ${Object.keys(FEMA.apps).filter(k => os.some(o => o.app === k)).map(k => { const a = FEMA.apps[k]
        return `<h3>${a.icon} ${esc(a.plural)}</h3><div class="cards">${os.filter(o => o.app === k).map(o => { const st = Model.status(o.id)
          return `<div class="card click" data-org="${esc(o.id)}"><div class="t"><span class="dot ${o.active ? Model.sevOf(st) : 'grey'}"></span>${esc(o.name)}</div>
            <div class="s">${o.active ? esc(st ? st.state : 'no report yet') : 'signed off'} · lead ${esc(o.lead)} · ${(o.members || []).filter(m => m.joinedAt).length} ${esc(a.unit)}(s)</div></div>` }).join('')}</div>` }).join('')
      || '<div class="empty">none yet</div>'}`
    $$('[data-org]', el).forEach(c => c.onclick = () => orgDlg(c.dataset.org))
  }, { icon: '🏢', when: inc })

  function orgDlg(login) {
    const o = Model.org(login), a = appOf(o), st = Model.status(login), stt = Model.statusTuple(login)
    App.dialog(a.icon + ' ' + o.name, `
      <p class="meta">${esc(a.name)} · login ${esc(login)} · lead ${esc(o.lead)} · ${o.active ? 'on the network' : 'signed off'}</p>
      <h4>Status ${stt ? '· ' + esc(when(stt.at)) : ''}</h4>
      ${st ? App.table(['', ''], a.status.map(f => [esc(f.label), esc(st[f.k])])) : '<div class="empty">no report yet</div>'}
      <h4>Members</h4><div>${(o.members || []).filter(m => m.joinedAt).map(m => `<span class="pill">${esc(m.name)}</span>`).join(' ') || '<span class="meta">none joined</span>'}</div>
      <h4>Assignments</h4>${App.table(['Assignment', 'State'], Model.assigns().filter(x => x.to === login).map(x => [esc(x.what), esc(x.state)]), 'none')}`,
      [['Assign…', () => { assignDlg(login); return false }], ['Message…', () => { msgDlg(login); return false }]])
  }
  function msgDlg(to) {
    App.dialog('Message to ' + Model.name(to), '<textarea id="mt" rows="3"></textarea>', [['Send', () => { const t = $('#mt').value.trim(); if (t) Model.send('eoc', to, t) }, 'pri']])
  }

  // ------------------------------------------------------------------ assignments (ICS 204)
  App.module('assign', 'Assignments', (el) => {
    const as = Model.assigns()
    el.innerHTML = `<h2>Assignments</h2><p class="lead">ICS 204: what each organization is to do this operational period. Its lead acknowledges and reports progress.</p>
      <div class="row"><button type="button" class="btn pri" id="na">New assignment</button></div>
      ${App.table(['To', 'Assignment', 'Where', 'Op', 'State', ''], as.map(x => [esc(Model.name(x.to)), esc(x.what), esc(x.where), esc(x.opPeriod), `<span class="pill">${esc(x.state)}</span>`,
        `<button type="button" class="btn sm" data-del="${x.id}">Remove</button>`]), 'none')}`
    $('#na').onclick = () => assignDlg('')
    $$('[data-del]', el).forEach(b => b.onclick = () => Store.remove('eoc-assign', b.dataset.del))
  }, { icon: '📋', when: inc })
  function assignDlg(to) {
    const os = Model.activeOrgs()
    if (!os.length) return App.status('no organization is on the network to assign', true)
    App.dialog('New assignment (ICS 204)', App.form([
      { k: 'to', label: 'To', type: 'select', options: os.map(o => [o.id, appOf(o).icon + ' ' + o.name]) },
      { k: 'what', label: 'Assignment', wide: true }, { k: 'where', label: 'Where' }, { k: 'contact', label: 'Contact / frequency' },
      { k: 'notes', label: 'Special instructions', type: 'textarea', rows: 2 }], { to }),
      [['Assign', (d) => { const v = App.collect(d); if (!v.what) { App.status('what is the assignment?', true); return false }
        Store.write('eoc-assign', Store.id('a'), Object.assign(v, { opPeriod: (inc().opPeriod || {}).n, state: 'Assigned', by: ME }))
        Model.event(`Assigned ${Model.name(v.to)}: ${v.what}`, ME) }, 'pri']])
  }

  // ------------------------------------------------------------------ resource requests (ICS 213RR)
  App.module('reqs', 'Requests', (el) => {
    const rs = Model.requests()
    const next = { Requested: ['Approved', 'Denied'], Approved: ['Ordered'], Ordered: ['En route'], 'En route': ['Arrived'], Arrived: ['Demobilized'] }
    el.innerHTML = `<h2>Resource requests</h2><p class="lead">ICS 213RR, from request to demobilization. Each step is recorded with who took it.</p>
      <div class="row"><button type="button" class="btn" id="nr">The EOC requests…</button></div>
      ${App.table(['From', 'Resource', 'Priority', 'Needed by', 'State', 'Next'], rs.map(r => [esc(Model.name(r.from)), esc(r.qty + ' × ' + r.resource) + (r.why ? `<div class="meta">${esc(r.why)}</div>` : ''),
        esc(r.priority), esc(when(r.needBy)), `<span class="pill">${esc(r.state)}</span>`,
        (next[r.state] || []).map(s => `<button type="button" class="btn sm${s === 'Denied' ? ' bad' : ''}" data-r="${r.id}" data-s="${s}">${s}</button>`).join(' ') + ` <button type="button" class="btn sm" data-h="${r.id}">History</button>`]), 'none')}`
    $$('[data-r]', el).forEach(b => b.onclick = () => App.dialog(b.dataset.s + ': ' + (Store.get('eoc-request', b.dataset.r) || {}).resource,
      App.form([{ k: 'note', label: b.dataset.s === 'Ordered' ? 'Ordered from (supplier, mutual aid, state)' : 'Note', wide: true }]),
      [[b.dataset.s, (d) => { Model.request(b.dataset.r, { state: b.dataset.s, note: App.collect(d).note }, ME)
        Model.event(`Request ${(Store.get('eoc-request', b.dataset.r) || {}).resource}: ${b.dataset.s}`, ME) }, 'pri']]))
    $$('[data-h]', el).forEach(b => { const r = Store.get('eoc-request', b.dataset.h)
      b.onclick = () => App.dialog('History', App.table(['When', 'Step', 'By', 'Note'], (r.history || []).map(h => [esc(when(h.at)), esc(h.state), esc(h.by), esc(h.note)]))) })
    $('#nr').onclick = () => App.dialog('The EOC requests (to the state / mutual aid)', `<div id="rf">${App.form([
        { k: 'resource', label: 'Resource (NIMS type)', type: 'select', options: FEMA.rtlt }, { k: 'qty', label: 'Quantity', type: 'number', value: 1 },
        { k: 'priority', label: 'Priority', type: 'select', options: FEMA.priorities }, { k: 'needBy', label: 'Needed by', type: 'datetime' },
        { k: 'deliverTo', label: 'Deliver to', wide: true }, { k: 'why', label: 'What it is for', type: 'textarea', rows: 2 }])}</div>`,
      [['Request', (d) => { const v = App.collect(d); Store.write('eoc-request', Store.id('r'), Object.assign(v, { from: 'eoc', state: 'Requested', history: [{ state: 'Requested', by: ME, at: new Date().toISOString() }] })) }, 'pri']])
  }, { icon: '📦', when: inc })

  // ------------------------------------------------------------------ staffing
  App.module('staff', 'Staffing', (el) => {
    const i = inc(), s = FEMA.structures[i.structure] || FEMA.structures.ics
    el.innerHTML = `<h2>Staffing</h2><p class="lead">${esc(s.name)}: its sections and positions. Who holds each one, checked in or out (ICS 203 / 207 in EOCAdmin read this).</p>
      ${Object.entries(s.sections).map(([sec, ps]) => `<h3>${esc(sec)}</h3>${App.table(['Position', 'Who', 'In', ''], ps.map(p => { const x = Store.get('eoc-staff', p) || {}
        return [esc(p), esc(x.name || '—'), x.name ? (x.checkedIn ? '<span class="pill on">in</span>' : '<span class="pill">out</span>') : '',
          `<button type="button" class="btn sm" data-p="${esc(p)}" data-sec="${esc(sec)}">${x.name ? 'Change' : 'Fill'}</button>` + (x.name ? ` <button type="button" class="btn sm" data-io="${esc(p)}">${x.checkedIn ? 'Check out' : 'Check in'}</button>` : '')] }))}`).join('')}`
    $$('[data-p]', el).forEach(b => b.onclick = () => { const x = Store.get('eoc-staff', b.dataset.p) || {}
      App.dialog(b.dataset.p, App.form([{ k: 'name', label: 'Name' }, { k: 'agency', label: 'Agency' }, { k: 'contact', label: 'Contact' }], x),
        [['Save', (d) => { const v = App.collect(d); Store.write('eoc-staff', b.dataset.p, Object.assign({}, x, v, { position: b.dataset.p, section: b.dataset.sec, checkedIn: x.name ? x.checkedIn : true })) }, 'pri']]) })
    $$('[data-io]', el).forEach(b => b.onclick = () => { const x = Store.get('eoc-staff', b.dataset.io); Store.write('eoc-staff', b.dataset.io, Object.assign({}, x, { checkedIn: !x.checkedIn }))
      Model.event(`${x.name} checked ${x.checkedIn ? 'out of' : 'in as'} ${b.dataset.io}`, ME) })
  }, { icon: '🧑‍💼', when: inc })

  // ------------------------------------------------------------------ objectives (ICS 202)
  App.module('obj', 'Objectives', (el) => {
    const n = (inc().opPeriod || {}).n, os = Model.objectives()
    el.innerHTML = `<h2>Incident objectives</h2><p class="lead">ICS 202: the objectives for each operational period. The IAP in EOCAdmin is built from these.</p>
      <div class="row"><input id="ot" placeholder="a new objective for op period ${esc(n)}" style="max-width:520px"><button type="button" class="btn pri" id="oa">Add</button></div>
      ${App.table(['Op', 'Objective', 'State', ''], os.map(o => [esc(o.opPeriod), esc(o.text), `<span class="pill">${esc(o.state)}</span>`,
        ['Open', 'Met', 'Dropped'].filter(s => s !== o.state).map(s => `<button type="button" class="btn sm" data-o="${o.id}" data-s="${s}">${s}</button>`).join(' ')]), 'none')}`
    $('#oa').onclick = () => { const t = $('#ot').value.trim(); if (t) Store.write('eoc-objective', Store.id('o'), { opPeriod: n, text: t, state: 'Open' }) }
    $$('[data-o]', el).forEach(b => b.onclick = () => Store.write('eoc-objective', b.dataset.o, Object.assign({}, Store.get('eoc-objective', b.dataset.o), { state: b.dataset.s })))
  }, { icon: '🎯', when: inc })

  App.module('events', 'Events', (el) => {
    el.innerHTML = `<h2>Significant events</h2><div class="row"><input id="et" placeholder="what happened" style="max-width:520px"><button type="button" class="btn pri" id="ea">Log</button></div>
      ${App.table(['When', 'Event', 'By'], Model.events().map(e => [esc(when(e.at)), esc(e.text), esc(e.by)]), 'none')}`
    $('#ea').onclick = () => { const t = $('#et').value.trim(); if (t) Model.event(t, ME) }
  }, { icon: '🗒', when: inc })

  App.module('msgs', 'Messages', (el) => msgs(el, 'eoc', ME), { icon: '💬', when: inc })

  // ------------------------------------------------------------------ authorizations (EOCCommandAdmin's, here so the preview runs end to end)
  App.module('auth', 'Authorizations', (el) => {
    const as = Model.auths()
    el.innerHTML = `<h2>Authorizations</h2>
      <p class="lead">In OpenEOC this is EOCCommandAdmin's: the admin gives each organization a login and a password. The category is not set here:
        it is the app that claims the login. It is in this preview so the whole flow runs in one place.</p>
      <div class="row"><input id="al" placeholder="login (e.g. charlie, harbor-medical)" style="max-width:240px"><input id="ap" type="password" placeholder="password" style="max-width:180px">
        <input id="an" placeholder="note (who it is for)" style="max-width:260px"><button type="button" class="btn pri" id="aa">Authorize</button></div>
      ${App.table(['Login', 'For', 'On the network as', ''], as.map(a => { const o = Model.org(a.login)
        return [esc(a.login), esc(a.note), o ? `${appOf(o).icon} ${esc(o.name)} (${esc(appOf(o).name)})${o.active ? '' : ' · signed off'}` : '<span class="meta">not claimed</span>',
          `<button type="button" class="btn sm bad" data-x="${esc(a.login)}">Withdraw</button>`] }), 'none: authorize a login, then claim it in the Field app')}`
    $('#aa').onclick = () => {
      const l = $('#al').value.trim().toLowerCase(), p = $('#ap').value
      if (!/^[a-z0-9._-]{1,40}$/.test(l)) return App.status('a login: letters, digits, . _ -', true)
      if (!p) return App.status('a password', true)
      Store.write('eoc-auth', l, { login: l, verifier: verifier(p), note: $('#an').value.trim(), by: 'admin' }); App.status(l + ' authorized')
    }
    $$('[data-x]', el).forEach(b => b.onclick = () => { Store.remove('eoc-auth', b.dataset.x); const o = Model.org(b.dataset.x); if (o) Store.write('eoc-org', b.dataset.x, Object.assign({}, o, { active: false })) })
  }, { icon: '🔑', when: inc })

  App.start({ app: 'eoc', title: 'OpenEOC · EOC', who: () => { const d = Store.get('eoc-staff', 'EOC Director'); return d ? 'EOC Director: ' + d.name : '' } })
})()
