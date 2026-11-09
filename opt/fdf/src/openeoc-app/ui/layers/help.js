// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/help.js -- [RT_LAYERS_V1] [RT_HELP_V1] requests for help, as DisasterComm's help page keeps them, and the
// reports to the EOC: on the map where they said they are, and the responders' Requests screen (On the way, Resolved).
// Other layers add sections to Requests (RT.section('requests', ...)). The public's own forms are the public layer's.
'use strict'
RT.layer('help', (RT) => {
  S.help = {}; S.actions = {}; S.reports = {}
  RT.kind('help', { label: 'Help requests', shape: '', size: 16, color: '#ff1744', ch: '!' })
  RT.where = (l) => { if (!l) return ''; return l.text || (typeof l.lat === 'number' ? l.lat.toFixed(4) + ', ' + l.lon.toFixed(4) : '') }
  RT.actionsFor = (rid) => Object.entries(S.actions).filter(([k]) => k.startsWith(rid + '~')).map(([, a]) => a).sort((a, b) => (b.at || 0) - (a.at || 0))
  const where = RT.where, actionsFor = RT.actionsFor

  RT.on('help', (m) => { S.help = m.requests || {}; S.actions = m.actions || {}; redraw(); refreshSheet('help') })
  RT.on('reports', (m) => { S.reports = m.list || {}; refreshSheet('reports') })

  RT.hook('redraw', (want, place) => {                  // requests needing help, where they said they are
    if (!S.on.help) return
    for (const rid in S.help) {
      const b = S.help[rid], l = b.location || {}
      if (b.status !== 'open' || typeof l.lat !== 'number') continue
      const id = 'help:' + rid; want[id] = 1
      place(id, { kind: 'help', name: b.name + ' (' + (b.people || 1) + ')', lat: l.lat, lng: l.lon, status: (b.needs || []).join(', ') + (b.note ? ' — ' + b.note : '') }, null)
    }
  })

  RT.screen('requests', () => {
    const open = Object.entries(S.help).filter(([, b]) => b.status === 'open').sort((a, b) => (a[1].at || 0) - (b[1].at || 0))
    const reqs = open.map(([rid, b]) => {
      const acts = actionsFor(rid)
      return `<div class="card"><b>${esc(b.name)}</b> (${b.people || 1}) ${(b.needs || []).map(n => `<span class="pill">${esc(n)}</span>`).join('')}
        <div class="meta">${esc(where(b.location))}${b.contact ? ' · ' + esc(b.contact) : ''} · ${b.at ? new Date(b.at * 1000).toLocaleTimeString() : ''}</div>
        ${b.note ? `<div>${esc(b.note)}</div>` : ''}
        ${acts.map(a => `<div class="meta">↳ ${esc(a.action)} by ${esc(a.unit)}${a.note ? ' — ' + esc(a.note) : ''}</div>`).join('')}
        <div><button type="button" onclick="act('${esc(rid)}','on the way')">On the way</button> <button type="button" onclick="act('${esc(rid)}','resolved')">Resolved</button>
        ${b.location && typeof b.location.lat === 'number' ? `<button type="button" onclick="showOnMap(${b.location.lat},${b.location.lon})">Map</button>` : ''}</div></div>`
    }).join('')
    const safe = Object.values(S.help).filter(b => b.status === 'safe').length
    sheet('reqs', 'Requests', `<h3>Needing help (${open.length})</h3>${reqs || '<div class="meta">none open</div>'}<div class="meta">${safe} people have said they are safe (Find someone, on the public screen).</div>`)
    RT.sectioned('requests')
  })
  window.act = (rid, action) => send({ op: 'helpAction', rid, action })
  RT.hook('refresh', (what) => { if (S.sheet === 'reqs' && (what === 'help' || what === 'reports')) RT.open('requests') })
})
