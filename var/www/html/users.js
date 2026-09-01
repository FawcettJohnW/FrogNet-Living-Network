/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
import * as api from './api.js';
import { fmtDate, spinner, makeDetails } from './utils.js';

export function initUsers(){
  document.getElementById('reloadUsers').onclick = load;
  document.getElementById('userFilter').addEventListener('input', debounce(load, 250));
  load();
}
function debounce(fn, ms){ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; }

async function load(){
  const list = document.getElementById('userList');
  list.innerHTML = '';
  const filter = document.getElementById('userFilter').value.trim().toLowerCase();
  const users = await api.list('users', { order:'CallSign', limit:1000 });
  const filtered = filter ? users.filter(u => {
    const pack = `${u.CallSign||''} ${u.RealName||''}`.toLowerCase();
    return pack.includes(filter);
  }) : users;

  if (!filtered.length) { list.innerHTML = '<div class="empty">No users found.</div>'; return; }

  for (const u of filtered){
    const summary = document.createElement('div');
    summary.innerHTML = `
      👤 <strong>${u.CallSign}</strong>
      ${u.RealName ? `<span class="pill">${u.RealName}</span>` : ''}
    `;
    const content = document.createElement('div');
    content.appendChild(spinner());

    const det = makeDetails(summary, content, async (userContent) => {
      userContent.innerHTML = '';

      const grid = document.createElement('div'); grid.className = 'grid cols-2';

      const inboxPanel = document.createElement('div'); inboxPanel.className = 'panel';
      inboxPanel.innerHTML = `<div><strong>Inbox</strong> <span class="muted small">(To: ${u.CallSign})</span></div>`;
      const inboxList = document.createElement('div'); inboxList.className = 'list'; inboxPanel.appendChild(inboxList);

      const outPanel = document.createElement('div'); outPanel.className = 'panel';
      outPanel.innerHTML = `<div><strong>Outbox</strong> <span class="muted small">(From: ${u.CallSign})</span></div>`;
      const outList = document.createElement('div'); outList.className = 'list'; outPanel.appendChild(outList);

      grid.appendChild(inboxPanel); grid.appendChild(outPanel);
      userContent.appendChild(grid);

      try {
        const [inbox, outbox] = await Promise.all([
          api.list('messages', { ToUser: u.CallSign, order: 'sentDate DESC', limit: 200 }),
          api.list('messages', { FromUser: u.CallSign, order: 'sentDate DESC', limit: 200 }),
        ]);

        function renderMsg(m){
          const dd = document.createElement('details');
          const s = document.createElement('summary');
          s.innerHTML = `
            ✉️ <strong>#${m.messageID}</strong>
            <span class="pill">From: ${m.FromUser}</span>
            <span class="pill">To: ${m.ToUser}</span>
            ${m.sentDate ? `<span class="pill">${fmtDate(m.sentDate)}</span>`:''}
          `;
        dd.appendChild(s);
          const body = document.createElement('div');
          body.innerHTML = `<div class="panel" style="margin-top:8px">${(m.Message || '').replace(/</g,'&lt;')}</div>`;
          dd.appendChild(body);
          return dd;
        }

        if (!inbox.length) inboxList.innerHTML = '<div class="empty">No messages.</div>';
        else inbox.forEach(m => inboxList.appendChild(renderMsg(m)));

        if (!outbox.length) outList.innerHTML = '<div class="empty">No messages.</div>';
        else outbox.forEach(m => outList.appendChild(renderMsg(m)));

      } catch(err){
        userContent.innerHTML = `<div class="error">Failed to load messages: ${err.message}</div>`;
      }

      const sendPanel = document.createElement('div'); sendPanel.className = 'panel';
      sendPanel.style.marginTop = '10px';
      sendPanel.innerHTML = `
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px">
          <strong>Send a Message</strong>
          <span class="muted small">(to: ${u.CallSign})</span>
        </div>
        <div class="grid" style="gap:8px">
          <input id="from_${u.CallSign}" placeholder="FromUser (your CallSign)" />
          <textarea id="msg_${u.CallSign}" rows="3" placeholder="Type your message…"></textarea>
          <div>
            <button class="btn" id="send_${u.CallSign}">Send Message</button>
            <span class="muted small" id="stat_${u.CallSign}"></span>
          </div>
        </div>
      `;
      userContent.appendChild(sendPanel);

      document.getElementById(`send_${u.CallSign}`).onclick = async () => {
        const from = document.getElementById(`from_${u.CallSign}`).value.trim();
        const msg  = document.getElementById(`msg_${u.CallSign}`).value.trim();
        const stat = document.getElementById(`stat_${u.CallSign}`);
        if (!from || !msg){ stat.textContent = 'FromUser and Message are required.'; return; }
        stat.textContent = 'Sending…';
        const sentDate = new Date().toISOString().slice(0,19).replace('T',' ');
        try {
          await api.create('messages', { FromUser: from, ToUser: u.CallSign, Message: msg, sentDate });
          stat.textContent = 'Sent!';
          document.getElementById(`msg_${u.CallSign}`).value = '';
        } catch(e){
          stat.textContent = `Failed: ${e.message}`;
        }
      };
    });

    document.getElementById('userList').appendChild(det);
  }
}
