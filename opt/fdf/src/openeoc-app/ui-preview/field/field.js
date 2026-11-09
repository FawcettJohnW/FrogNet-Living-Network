// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// field/field.js -- [UI_PREVIEW_V1] the app an organization runs: a Team's, a Hospital's, an Ambulance's, a Shelter's ...
// Its category is the app (John 2026-11-09: "Categories come from the apps that connect"). Each browser tab is one
// device. The lead claims the login the admin gave; if the organization is not on the network the claiming device
// hosts it, and if it is, there is no replacement. Members join the lead's device with the team password (one for all),
// or a password of their own if the lead gave them one.
'use strict'
;(() => {
  const { $, $$, esc, when, verifier } = App
  const ss = { get(k) { try { return JSON.parse(sessionStorage.getItem('openeoc-preview-' + k) || 'null') } catch (e) { return null } },
               set(k, v) { try { sessionStorage.setItem('openeoc-preview-' + k, JSON.stringify(v)) } catch (e) {} } }
  let device = ss.get('device'); if (!device) { device = 'dev-' + Math.random().toString(36).slice(2, 8); ss.set('device', device) }
  let me = ss.get('me')        // {role: lead|member, login, call, app}
  let pickApp = ss.get('app') || 'team'
  const myOrg = () => me && Model.org(me.login)
  const lead = () => me && me.role === 'lead'
  const app = () => FEMA.apps[(myOrg() || {}).app || (me || {}).app || pickApp] || FEMA.apps.team
  const joined = () => {
    if (!me) return false
    const o = myOrg(); if (!o || !o.active) return me.role === 'lead' ? false : false
    if (me.role === 'lead') return o.device === device
    return (o.members || []).some(m => m.name === me.call)
  }
  function setMe(x) { me = x; ss.set('me', x) }

  // ------------------------------------------------------------------ joining
  App.module('join', 'Join', (el) => {
    const auths = Model.auths()
    const live = Model.activeOrgs()
    el.innerHTML = `<h2>Join the incident</h2>
      <p class="lead">Each browser tab is one device. Pick the app this device runs (that is its category at the EOC), then claim the
        login the EOC's admin gave your organization, or join an organization that is already on as a member.</p>
      <h3>This device runs</h3>
      <div class="row" id="apps">${Object.entries(FEMA.apps).map(([k, a]) => `<button type="button" class="btn${k === pickApp ? ' pri' : ''}" data-app="${k}">${a.icon} ${esc(a.name)}</button>`).join('')}</div>
      <div class="cards">
        <div class="card"><div class="t">① Lead: claim your login</div>
          <div class="s">The EOC's QR code carries the login; you give the password the admin gave you. Not in the code: the password.</div>
          <div class="form" style="margin-top:8px">
            <label class="fl"><span>Scan the EOC's QR (the preview: pick the login it carries)</span>
              <select id="cl">${auths.map(a => `<option value="${esc(a.login)}">${esc(a.login)}</option>`).join('') || '<option value="">no logins authorized yet</option>'}</select></label>
            <label class="fl"><span>Name shown at the EOC</span><input id="cname" placeholder="e.g. Team Alpha, Medic 12"></label>
            <label class="fl"><span>Your name or call sign</span><input id="clead" placeholder="e.g. K7ABC"></label>
            <label class="fl"><span>Password</span><input id="cpw" type="password"></label>
          </div>
          <div class="row"><button type="button" class="btn pri" id="claim">Claim</button></div></div>
        <div class="card"><div class="t">② Member: join your lead</div>
          <div class="s">Join the lead's hotspot, scan the lead's QR, give your call sign and the team password (or your own, if your lead gave you one).</div>
          <div class="form" style="margin-top:8px">
            <label class="fl"><span>Scan the lead's QR (the preview: pick the organization)</span>
              <select id="jl">${live.map(o => `<option value="${esc(o.id)}">${esc((FEMA.apps[o.app] || {}).icon || '')} ${esc(o.name)}</option>`).join('') || '<option value="">no organization is on yet</option>'}</select></label>
            <label class="fl"><span>Your call sign</span><input id="jcall" placeholder="e.g. K7XYZ"></label>
            <label class="fl"><span>Password</span><input id="jpw" type="password"></label>
          </div>
          <div class="row"><button type="button" class="btn pri" id="mjoin">Join</button></div></div>
      </div>
      <h3>Logins on the network now</h3>
      ${App.table(['Login', 'Organization', 'Category', 'Served by'], live.map(o => [esc(o.id), esc(o.name), esc((FEMA.apps[o.app] || {}).name || o.app), o.device === device ? 'this device' : 'another device']), 'none yet')}`
    $$('#apps button').forEach(b => b.onclick = () => { pickApp = b.dataset.app; ss.set('app', pickApp); App.refresh() })
    $('#claim').onclick = () => claim($('#cl').value, $('#cname').value.trim(), $('#clead').value.trim(), $('#cpw').value)
    $('#mjoin').onclick = () => memberJoin($('#jl').value, $('#jcall').value.trim().toUpperCase(), $('#jpw').value)
  }, { icon: '🔑', when: () => !joined() })

  function claim(login, name, leadName, pw) {
    const a = Store.get('eoc-auth', login)
    if (!a) return App.status('that login is not one the EOC authorized', true)
    if (!pw || verifier(pw) !== a.verifier) return App.status("that password is not this login's", true)
    const o = Model.org(login)
    if (o && o.active && o.device !== device) return App.status(`${o.name} is on the network already, served by its lead's device: there is no replacement`, true)
    if (o && o.app !== pickApp) return App.status(`${login} joined as ${FEMA.apps[o.app].name}: run that app to claim it`, true)
    if (!leadName) return App.status('give your name or call sign', true)
    Store.write('eoc-org', login, Object.assign({ members: [] }, o || {}, { login, app: pickApp, name: name || (o && o.name) || login,
      lead: leadName, device, active: true, claimedAt: new Date().toISOString() }))
    Model.event(`${name || login} (${FEMA.apps[pickApp].name}) joined; ${leadName}'s device hosts it`, leadName)
    setMe({ role: 'lead', login, call: leadName, app: pickApp })
    App.status('claimed: this device hosts ' + (name || login)); App.show('home')
  }
  function memberJoin(login, call, pw) {
    const o = Model.org(login)
    if (!o || !o.active) return App.status('that organization is not on the network', true)
    if (!/^[A-Z0-9._ -]{1,30}$/.test(call)) return App.status('your call sign: letters, digits, . _ - (at most 30)', true)
    const m = (o.members || []).find(x => x.name === call)
    const ok = m && m.verifier ? verifier(pw) === m.verifier : (o.teamVerifier && verifier(pw) === o.teamVerifier)
    if (!o.teamVerifier && !(m && m.verifier)) return App.status(`${o.name}'s lead has not set a team password yet`, true)
    if (!ok) return App.status(m && m.verifier ? 'that is not the password your lead set for you' : 'that is not the team password', true)
    const members = (o.members || []).filter(x => x.name !== call).concat([Object.assign({}, m || {}, { name: call, device, joinedAt: new Date().toISOString() })])
    Store.write('eoc-org', login, Object.assign({}, o, { members }))
    setMe({ role: 'member', login, call, app: o.app })
    App.status('joined ' + o.name); App.show('home')
  }

  // ------------------------------------------------------------------ the organization's home
  App.module('home', 'Overview', (el) => {
    const o = myOrg(), a = app(), st = Model.status(me.login) || {}, stt = Model.statusTuple(me.login)
    const asg = Model.assigns().filter(x => x.to === me.login)
    el.innerHTML = `<h2>${a.icon} ${esc(o.name)}</h2>
      <p class="lead">${esc(a.name)} · ${lead() ? 'you lead it; this device hosts it' : 'member ' + esc(me.call) + ' · lead ' + esc(o.lead)}</p>
      <h3>Status report ${stt ? `<span class="meta">last sent ${esc(when(stt.at))}</span>` : ''}</h3>
      <div id="stf">${App.form(a.status.map(f => Object.assign({ ro: !lead() }, f)), st)}</div>
      ${lead() ? '<div class="row"><button type="button" class="btn pri" id="stSend">Send to the EOC</button></div>' : ''}
      <h3>Assignments</h3>
      ${App.table(['Assignment', 'Where', 'Op', 'State', ''], asg.map(x => [esc(x.what), esc(x.where), esc(x.opPeriod), `<span class="pill">${esc(x.state)}</span>`,
        lead() && x.state !== 'Complete' ? ['Acknowledged', 'Working', 'Complete'].filter(s => s !== x.state).map(s => `<button type="button" class="btn sm" data-as="${x.id}" data-st="${s}">${s}</button>`).join(' ') : '']), 'none from the EOC yet')}`
    if (lead()) {
      $('#stSend').onclick = () => { Store.write('eoc-status', me.login, Object.assign(App.collect($('#stf')), { by: me.call })); App.status('status sent to the EOC') }
      $$('[data-as]', el).forEach(b => b.onclick = () => {
        const x = Store.get('eoc-assign', b.dataset.as); Store.write('eoc-assign', b.dataset.as, Object.assign({}, x, { state: b.dataset.st }))
        if (b.dataset.st === 'Complete') Model.event(`${o.name}: "${x.what}" complete`, me.call)
      })
    }
  }, { icon: '🏠', when: joined })

  // ------------------------------------------------------------------ the lead's members
  App.module('members', 'Members', (el) => {
    const o = myOrg(), a = app()
    const own = (o.members || []).filter(m => m.verifier)
    el.innerHTML = `<h2>Members</h2>
      <p class="lead">One password for the whole ${esc(a.name.toLowerCase())}: your members scan your QR and give it. Individual passwords only if you want them.</p>
      <div class="row"><input id="tpw" type="password" style="max-width:260px" placeholder="${o.teamVerifier ? 'team password: set (type to change)' : 'the team password'}">
        <button type="button" class="btn pri" id="tpwSet">${o.teamVerifier ? 'Change' : 'Set'}</button></div>
      <div class="card" style="max-width:360px"><div class="t">Your QR</div><div class="s">${o.teamVerifier ? `Members scan this on your hotspot. It carries ${esc(o.name)} and this device's address, not the password.` : 'Set the team password: then the QR is here.'}</div>
        ${o.teamVerifier ? '<div class="qr" aria-label="QR placeholder">▦ ▦ ▦<br>▦ QR ▦<br>▦ ▦ ▦</div>' : ''}</div>
      <details ${own.length ? 'open' : ''} style="margin-top:12px"><summary>Individual passwords</summary>
        <div class="row"><input id="mn" placeholder="call sign" style="max-width:180px"><input id="mp" type="password" placeholder="their password" style="max-width:200px">
          <button type="button" class="btn" id="madd">Add</button></div>
        ${App.table(['Member', ''], own.map(m => [esc(m.name), '<span class="meta">their own QR and password</span>']), 'none: everyone uses the team password')}</details>
      <h3>Joined</h3>
      ${App.table(['Member', 'Joined', ''], (o.members || []).filter(m => m.joinedAt).map(m => [esc(m.name), esc(when(m.joinedAt)), `<button type="button" class="btn sm bad" data-rm="${esc(m.name)}">Remove</button>`]), 'no one has joined yet')}`
    $('#tpwSet').onclick = () => { const p = $('#tpw').value; if (!p) return; Store.write('eoc-org', me.login, Object.assign({}, myOrg(), { teamVerifier: verifier(p) })); App.status('team password set (this device keeps its verifier, not the password)') }
    $('#madd').onclick = () => {
      const n = $('#mn').value.trim().toUpperCase(), p = $('#mp').value
      if (!n || !p) return App.status('a call sign and their password', true)
      const ms = (myOrg().members || []).filter(m => m.name !== n).concat([{ name: n, verifier: verifier(p) }])
      Store.write('eoc-org', me.login, Object.assign({}, myOrg(), { members: ms })); App.status(n + ' added with a password of their own')
    }
    $$('[data-rm]', el).forEach(b => b.onclick = () => {
      Store.write('eoc-org', me.login, Object.assign({}, myOrg(), { members: (myOrg().members || []).filter(m => m.name !== b.dataset.rm) })); App.status(b.dataset.rm + ' removed')
    })
  }, { icon: '👥', when: () => joined() && lead() })

  // ------------------------------------------------------------------ resource requests (ICS 213RR)
  App.module('requests', 'Requests', (el) => {
    const mine = Model.requests().filter(r => r.from === me.login)
    el.innerHTML = `<h2>Resource requests</h2><p class="lead">What you need from the EOC (ICS 213RR). The EOC approves, orders and tracks it; you see each step.</p>
      ${lead() ? `<div class="card"><div id="rf">${App.form([
        { k: 'resource', label: 'Resource (NIMS type)', type: 'select', options: FEMA.rtlt }, { k: 'qty', label: 'Quantity', type: 'number', value: 1 },
        { k: 'priority', label: 'Priority', type: 'select', options: FEMA.priorities }, { k: 'needBy', label: 'Needed by', type: 'datetime' },
        { k: 'deliverTo', label: 'Deliver to', wide: true }, { k: 'why', label: 'What it is for', type: 'textarea', rows: 2 }])}</div>
        <div class="row"><button type="button" class="btn pri" id="rsend">Send the request</button></div></div>` : ''}
      <h3>Yours</h3>
      ${App.table(['Resource', 'Qty', 'Priority', 'State', 'Last step'], mine.map(r => { const h = (r.history || []).slice(-1)[0] || {}
        return [esc(r.resource), esc(r.qty), esc(r.priority), `<span class="pill">${esc(r.state)}</span>`, esc(when(h.at)) + (h.note ? ' · ' + esc(h.note) : '')] }), 'none')}`
    if (lead()) $('#rsend').onclick = () => {
      const v = App.collect($('#rf')); if (!v.qty) return App.status('a quantity', true)
      const o = myOrg()
      Store.write('eoc-request', Store.id('r'), Object.assign(v, { from: me.login, state: 'Requested', history: [{ state: 'Requested', by: o.name, at: new Date().toISOString() }] }))
      App.status('request sent to the EOC')
    }
  }, { icon: '📦', when: joined })

  App.module('msgs', 'Messages', (el) => msgs(el, me.login, me.call), { icon: '💬', when: joined })

  App.module('leave', 'Leave', (el) => {
    el.innerHTML = `<h2>Leave</h2><p class="lead">${lead() ? 'Signing off takes your organization off the network: another of its devices can then claim it with the login.' : 'Signing off leaves your organization; you can join again with your password.'}</p>
      <div class="row"><button type="button" class="btn bad" id="off">Sign off</button></div>`
    $('#off').onclick = () => {
      const o = myOrg()
      if (lead()) { Store.write('eoc-org', me.login, Object.assign({}, o, { active: false })); Model.event(`${o.name} left the network`, me.call) }
      else Store.write('eoc-org', me.login, Object.assign({}, o, { members: (o.members || []).map(m => m.name === me.call ? Object.assign({}, m, { joinedAt: null, device: null }) : m) }))
      setMe(null); App.status('signed off'); App.show('join')
    }
  }, { icon: '⏏', when: joined })

  App.start({ app: 'field', title: 'OpenEOC Field', who: () => me && joined() ? `${(myOrg() || {}).name} · ${me.call} · ${me.role}` : 'device ' + device + ' · not joined' })
})()
