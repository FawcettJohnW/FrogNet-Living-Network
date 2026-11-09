// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// shared/views.js -- [UI_PREVIEW_V1] views more than one app shows
'use strict'
;(() => {
  const { $, esc, when } = App
  // the message view, the EOC's too
  window.msgs = function msgs(el, mine, by) {
    const ms = Model.msgs().filter(m => m.from === mine || m.to === mine || m.to === 'all' || (mine === 'eoc' && m.to === 'eoc'))
    const tos = mine === 'eoc' ? [['all', 'Everyone']].concat(Model.activeOrgs().map(o => [o.id, o.name])) : [['eoc', 'EOC'], ['all', 'Everyone']]
    el.innerHTML = `<h2>Messages</h2><div class="msgs" id="ml">${ms.map(m => `<div class="m"><div class="h">${esc(Model.name(m.from))} → ${esc(Model.name(m.to))} · ${esc(when(m.at))}</div>${esc(m.text)}</div>`).join('') || '<div class="empty">no messages</div>'}</div>
      <form class="say" id="say"><select id="to">${tos.map(([v, n]) => `<option value="${esc(v)}">${esc(n)}</option>`).join('')}</select><input id="txt" placeholder="message" autocomplete="off"><button class="btn pri">Send</button></form>`
    const l = $('#ml'); l.scrollTop = l.scrollHeight
    $('#say').onsubmit = (e) => { e.preventDefault(); const t = $('#txt').value.trim(); if (!t) return; $('#txt').value = ''; $('#txt').blur(); Model.send(mine, $('#to').value, t) }
  }

})()
