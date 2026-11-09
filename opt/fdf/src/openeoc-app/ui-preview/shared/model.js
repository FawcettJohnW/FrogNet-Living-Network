// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// shared/model.js -- [UI_PREVIEW_V1] the incident's records, as tuples every app reads and writes:
//   eoc-incident current   the incident: name, type, activation level, EOC structure, operational period
//   eoc-auth     <login>   an authorization the admin gave (login, verifier, note); the category is not in it
//   eoc-org      <login>   an organization that claimed its login: its app (= its category), name, lead, device, members
//   eoc-status   <login>   its latest status report (the fields its app's FEMA.apps[...].status lists)
//   eoc-assign   <id>      an assignment (ICS 204) to an organization
//   eoc-request  <id>      a resource request (ICS 213RR) and its history
//   eoc-msg      <id>      a message: from, to (eoc | all | <login>), text
//   eoc-staff    <position> who holds an EOC position, checked in or out
//   eoc-lifeline <id>      the EOC's own call on a lifeline (else it is worked out from the reports)
//   eoc-objective <id>     an incident objective for an operational period
//   eoc-event    <id>      a significant event
//   adm-form     <id>      a form in EOCAdmin (draft or final)
//   adm-log      <id>      an activity log entry (ICS 214)
//   adm-aar      current   the after-action report; adm-ip <id> its improvement-plan items
'use strict'
window.Model = (() => {
  const by = (f) => Store.read(f).map(t => Object.assign({ id: t.instance, at: t.at }, t.bag))
  const M = {
    orgs: () => by('eoc-org'),
    org: (login) => Store.get('eoc-org', login),
    activeOrgs: () => by('eoc-org').filter(o => o.active),
    status: (login) => Store.get('eoc-status', login),
    statusTuple: (login) => Store.read('eoc-status').find(t => t.instance === login),
    auths: () => by('eoc-auth'),
    assigns: () => by('eoc-assign'),
    requests: () => by('eoc-request'),
    msgs: () => by('eoc-msg').reverse(),
    staff: () => by('eoc-staff'),
    objectives: () => by('eoc-objective').reverse(),
    events: () => by('eoc-event'),
    categories() {     // the categories now on the network: the apps of the organizations that joined (none allocated)
      const c = {}
      for (const o of M.activeOrgs()) (c[o.app] = c[o.app] || []).push(o)
      return c
    },
    sevOf(st) { return !st ? 'grey' : (FEMA.stateSev[st.state] || 'green') },
    lifeline(id) {     // the EOC's call when it made one, else the worst of the reports into it
      const own = Store.get('eoc-lifeline', id)
      const rep = M.activeOrgs().filter(o => (FEMA.apps[o.app] || {}).lifeline === id)
      const order = ['red', 'yellow', 'green', 'grey']
      const sevs = rep.map(o => M.sevOf(M.status(o.id)))
      const derived = sevs.length ? sevs.sort((a, b) => order.indexOf(a) - order.indexOf(b))[0] : 'grey'
      return { status: own ? own.status : derived, derived, own, reports: rep }
    },
    send(from, to, text) { Store.write('eoc-msg', Store.id('m'), { from, to, text }) },
    event(text, by) { Store.write('eoc-event', Store.id('e'), { text, by }) },
    request(id, change, who) {
      const r = Store.get('eoc-request', id); if (!r) return
      const h = (r.history || []).concat([{ state: change.state || r.state, by: who, at: new Date().toISOString(), note: change.note || '' }])
      Store.write('eoc-request', id, Object.assign({}, r, change, { history: h }))
    },
    name(login) { if (login === 'eoc') return 'EOC'; if (login === 'all') return 'Everyone'; const o = M.org(login); return o ? o.name : login },
  }

  // the demo: a fictional county windstorm, with organizations already authorized (and, for the "joined" demo, joined)
  M.seed = function (joined) {
    Store.reset()
    const now = new Date(), iso = (h) => App.local(new Date(now.getTime() + h * 3600e3))
    Store.write('eoc-incident', 'current', { name: 'Cascade County Windstorm', type: 'Type 3', level: 2, structure: 'ics',
      opPeriod: { n: 1, start: iso(0), end: iso(12) }, started: now.toISOString(), location: 'Cascade County EOC' })
    const A = [['alpha', 'alpha1', 'CERT team, north district'], ['bravo', 'bravo1', 'CERT team, south district'],
               ['cascade-general', 'cgh1', 'Cascade General Hospital'], ['medic-12', 'medic1', 'County EMS unit'],
               ['medic-14', 'medic1', 'County EMS unit'], ['island-shelter', 'shelter1', 'Red Cross shelter, Island HS'],
               ['cascade-pud', 'pud1', 'Public utility district'], ['ares', 'ares1', 'County ARES/RACES']]
    for (const [l, p, n] of A) Store.write('eoc-auth', l, { login: l, verifier: App.verifier(p), note: n, by: 'admin' })
    for (const [pos, sec, nm] of [['EOC Director', 'Command', 'Dana Ruiz'], ['Planning Section Chief', 'Planning', 'Lee Park'],
                                  ['Logistics Section Chief', 'Logistics', 'Sam Ortiz']])
      Store.write('eoc-staff', pos, { position: pos, section: sec, name: nm, checkedIn: true, at: now.toISOString() })
    Store.write('eoc-objective', 'o1', { opPeriod: 1, text: 'Account for all residents in the north district by 18:00', state: 'Open' })
    Store.write('eoc-objective', 'o2', { opPeriod: 1, text: 'Restore power to Cascade General Hospital', state: 'Open' })
    M.event('EOC activated at Level 2 (partial)', 'Dana Ruiz')
    if (!joined) return
    const J = [['alpha', 'team', 'Team Alpha', 'K7ABC', { state: 'Available', people: 6 }],
               ['cascade-general', 'hospital', 'Cascade General Hospital', 'Nurse supervisor', { state: 'Open', edBeds: 4, icuBeds: 1, medsurg: 12, power: 'Generator' }],
               ['medic-12', 'ambulance', 'Medic 12', 'Medic 12', { state: 'Available', level: 'ALS' }],
               ['island-shelter', 'shelter', 'Island HS Shelter', 'Shelter manager', { state: 'Open', capacity: 150, occupancy: 41, pets: 'Yes' }],
               ['cascade-pud', 'power', 'Cascade PUD', 'Duty supervisor', { state: 'Major outage', customersOut: 18400, restore: '48–72 h' }]]
    for (const [l, app, name, lead, st] of J) {
      Store.write('eoc-org', l, { login: l, app, name, lead, device: 'demo-' + l, active: true, claimedAt: now.toISOString(),
        teamVerifier: App.verifier('team'), members: app === 'team' ? [{ name: 'K7DEF' }, { name: 'K7GHI' }] : [] })
      Store.write('eoc-status', l, Object.assign({ by: lead }, st))
    }
    Store.write('eoc-request', 'r1', { from: 'cascade-general', resource: 'Generator, 100 kW', qty: 1, priority: 'Urgent',
      needBy: iso(4), deliverTo: 'Cascade General Hospital, loading dock', why: 'Backup generator has 10 h of fuel', state: 'Requested',
      history: [{ state: 'Requested', by: 'Cascade General Hospital', at: now.toISOString() }] })
    Store.write('eoc-assign', 'a1', { to: 'alpha', what: 'Welfare checks, Maple St to Elm St', where: 'North district, grid C4', opPeriod: 1, state: 'Assigned', by: 'Operations' })
    M.send('cascade-pud', 'eoc', 'Feeder 7 down; crews staged at the north yard.')
  }
  return M
})()
