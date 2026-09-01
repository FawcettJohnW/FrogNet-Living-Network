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

export function initTeams(){
  document.getElementById('reloadTeams').onclick = load;
  document.getElementById('teamFilter').addEventListener('input', debounce(load, 250));
  document.getElementById('showCreateTeam').onclick = toggleCreateForm;
  document.getElementById('ct_submit').onclick = createTeam;
  load();
}
function debounce(fn, ms){ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; }

function toggleCreateForm(){
  const box = document.getElementById('createTeamForm');
  box.style.display = (box.style.display === 'none' || !box.style.display) ? 'block' : 'none';
}

async function createTeam(){
  const name = document.getElementById('ct_name').value.trim();
  const owner = document.getElementById('ct_owner').value.trim();
  const stat = document.getElementById('ct_status');
  if (!name || !owner){ stat.textContent = 'TeamName and TeamOwner are required.'; return; }
  stat.textContent = 'Creating…';
  const createdDate = new Date().toISOString().slice(0,19).replace('T',' ');
  try {
    await api.create('teams', { TeamName: name, TeamOwner: owner, CreatedDate: createdDate });
    stat.textContent = 'Created!';
    await load();
  } catch(e){
    stat.textContent = `Failed: ${e.message}`;
  }
}

async function load(){
  const list = document.getElementById('teamList');
  list.innerHTML = '';
  const filter = document.getElementById('teamFilter').value.trim().toLowerCase();
  const teams = await api.list('teams', { order:'TeamName', limit:1000 });
  const filtered = filter ? teams.filter(t => {
    const pack = `${t.TeamName||''} ${t.TeamOwner||''}`.toLowerCase();
    return pack.includes(filter);
  }) : teams;

  if (!filtered.length) { list.innerHTML = '<div class="empty">No teams found.</div>'; return; }

  for (const t of filtered){
    const dd = document.createElement('details');
    const sm = document.createElement('summary');
    sm.innerHTML = `
      🧩 <strong>${t.TeamName}</strong>
      <span class="pill">Owner: ${t.TeamOwner}</span>
      ${t.CreatedDate ? `<span class="pill">${fmtDate(t.CreatedDate)}</span>`:''}
    `;
    dd.appendChild(sm);

    const body = document.createElement('div');
    body.style.marginTop = '8px';
    body.appendChild(spinner());
    dd.appendChild(body);

    dd.addEventListener('toggle', async () => {
      if (!dd.open) return;
      body.innerHTML = '';

      // Members list
      try {
        const members = await api.list('team_members', { TeamName: t.TeamName, order:'TeamUser', limit:1000 });
        const panel = document.createElement('div'); panel.className = 'panel';
        if (!members.length) {
          panel.innerHTML = '<div class="empty">No teammates in this team.</div>';
        } else {
          const ul = document.createElement('ul'); ul.style.margin = '0'; ul.style.paddingLeft = '16px';
          members.forEach(m => {
            const li = document.createElement('li');
            li.innerHTML = `<strong>${m.TeamUser}</strong>`;
            ul.appendChild(li);
          });
          panel.appendChild(ul);
        }
        body.appendChild(panel);
      } catch(err){
        body.innerHTML = `<div class="error">Failed to load teammates: ${err.message}</div>`;
      }

      // Add teammate form
      const addPanel = document.createElement('div'); addPanel.className = 'panel';
      addPanel.style.marginTop = '8px';
      addPanel.innerHTML = `
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px">
          <strong>Add Teammate</strong>
          <span class="muted small">(Team: ${t.TeamName})</span>
        </div>
        <div class="grid" style="gap:8px">
          <input id="tm_user_${t.TeamName}" placeholder="TeamUser (CallSign)" />
          <div>
            <button class="btn" id="tm_add_${t.TeamName}">Add</button>
            <span class="muted small" id="tm_stat_${t.TeamName}"></span>
          </div>
        </div>
      `;
      body.appendChild(addPanel);

      document.getElementById(`tm_add_${t.TeamName}`).onclick = async () => {
        const user = document.getElementById(`tm_user_${t.TeamName}`).value.trim();
        const stat = document.getElementById(`tm_stat_${t.TeamName}`);
        if (!user){ stat.textContent = 'TeamUser is required.'; return; }
        stat.textContent = 'Adding…';
        try {
          await api.create('team_members', { TeamName: t.TeamName, TeamUser: user });
          stat.textContent = 'Added!';
          await load(); // refresh entire list for simplicity
        } catch(e){
          stat.textContent = `Failed: ${e.message}`;
        }
      };
    });

    list.appendChild(dd);
  }
}
