// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// [ROLE_ADMIN_V1] (John 2026-11-10: "Create -Admin roles for everything that has a non-admin version. For now, it's just
// the RAM host, but add "Reserved for future customization" below that") one Admin per role that has instances:
// OpenEOC -- TeamAdmin, -- HospitalAdmin, -- AmbulanceAdmin, -- ShelterAdmin, -- PowerAdmin, -- WaterAdmin, -- TelecomAdmin.
// A Command Center user logs in; the Admin writes the role's one configuration (the door's op=roleconf), and every
// instance of the role holds a read on it and brings its own tuples to match (base.js: roleConfHere).
'use strict'
RT.layer('roleadmin', (RT) => {
  const ROLES = [['team', 'Team'], ['hospital', 'Hospital'], ['ambulance', 'Ambulance'], ['shelter', 'Shelter'], ['power', 'Power'], ['water', 'Water'], ['telecom', 'Telecom']]
  // the lead's own choices ([LEAD_HOST_CHOICES_V1]), for every instance of the role
  const HOSTS = [['device', "The hotspot and the role on each lead's phone.", 'WARNING: Eats battery life'],
                 ['systemram', "The hotspot on each lead's phone, but the network for the role.", ''],
                 ['other', 'The network and the role on another device', '(Not Implemented)'],
                 ['none', 'Not set', 'each lead chooses when it connects']]
  const conf = {}
  for (const [r, L] of ROLES) {
    RT.role(r + 'admin', { label: L + 'Admin', title: 'OpenEOC -- ' + L + 'Admin', field: 'login', who: () => L + 'Admin', kinds: 'all', teams: 'all',
                           sensors: false, mission: false, act: ['RAM host', 'roleadmin'],
                           connected() { RT.open('roleadmin'); send({ op: 'roleConf', role: r }) } })
  }
  RT.on('roleConf', (m) => { conf[m.role] = m.conf || {}; if (S.sheet === 'roleadmin' && !$('#sheet').hidden) RT.open('roleadmin', m.set ? 'saved' : '') })
  RT.screen('roleadmin', (said) => {
    const r = String(S.role || '').replace(/admin$/, ''), L = (ROLES.find(x => x[0] === r) || [r, cap(r)])[1], c = conf[r]
    const cur = c && c.ramhost ? c.ramhost : ''
    sheet('roleadmin', 'OpenEOC -- ' + L + 'Admin', `<div class="meta">The configuration every ${esc(L)} instance on the network follows: each one holds a read on it and changes its own to match.</div>
      <h3>RAM host</h3>
      <div class="meta">${c === undefined ? 'reading…' : cur ? 'Now: <b>' + esc((HOSTS.find(h => h[0] === cur) || [cur, cur])[1]) + '</b>' + (c.by ? ' (set by ' + esc(c.by) + ')' : '') : 'Now: not set (each lead decides)'}</div>
      <div class="sens">${HOSTS.map(([v, t, d]) => `<label class="rhost"><input type="radio" name="rhost" value="${v}"${v === cur ? ' checked' : ''}${v === 'other' ? ' disabled' : ''}> <b>${esc(t)}</b> <span class="meta">${esc(d)}</span></label>`).join('')}</div>
      <button type="button" id="rhSave">Save</button> <span class="meta" id="rhSaid">${said === 'saved' ? 'saved: every ' + esc(L) + ' instance follows it' : ''}</span>
      <h3>Reserved for future customization</h3>`)
    $('#rhSave').onclick = () => { const v = (document.querySelector('[name=rhost]:checked') || {}).value; if (!v) { $('#rhSaid').textContent = 'pick the RAM host first'; return }
      $('#rhSaid').textContent = 'saving…'; send({ op: 'roleConf', role: r, ramhost: v }) }
  })
})
