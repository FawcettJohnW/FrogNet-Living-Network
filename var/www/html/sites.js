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
import { spinner } from './utils.js';

export function initSites(){
  document.getElementById('reloadSites').onclick = load;
  document.getElementById('siteFilter').addEventListener('input', debounce(load, 250));
  document.getElementById('showCreateSite').onclick = toggleCreateForm;
  document.getElementById('cs_submit').onclick = createSite;
  load();
}
function debounce(fn, ms){ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; }

function toggleCreateForm(){
  const box = document.getElementById('createSiteForm');
  box.style.display = (box.style.display === 'none' || !box.style.display) ? 'block' : 'none';
}

async function createSite(){
  const name = document.getElementById('cs_name').value.trim();
  const addr = document.getElementById('cs_addr').value.trim();
  const frog = document.getElementById('cs_frogid').value.trim();
  const tags = document.getElementById('cs_tags').value.trim();
  const stat = document.getElementById('cs_status');
  if (!name || !addr || !frog){ stat.textContent = 'SiteName, SiteAddress, FrogID required.'; return; }
  stat.textContent = 'Adding…';
  try {
    await api.create('well_known_sites', { SiteName: name, SiteAddress: addr, FrogID: frog, Tags: tags });
    stat.textContent = 'Added!';
    await load();
  } catch(e){
    stat.textContent = `Failed: ${e.message}`;
  }
}

function anchorize(url){
  try {
    const u = new URL(url);
    return `<a href="${u.href}" target="_blank" rel="noopener">${u.href}</a>`;
  } catch {
    return url;
  }
}

async function load(){
  const list = document.getElementById('siteList');
  list.innerHTML = '';
  const filter = document.getElementById('siteFilter').value.trim().toLowerCase();
  const sites = await api.list('well_known_sites', { order:'SiteName', limit:1000 });
  const filtered = filter ? sites.filter(s => {
    const pack = `${s.SiteName||''} ${s.FrogID||''} ${s.SiteAddress||''}`.toLowerCase();
    return pack.includes(filter);
  }) : sites;

  if (!filtered.length) { list.innerHTML = '<div class="empty">No sites found.</div>'; return; }

  for (const s of filtered){
    const d = document.createElement('details');
    const sm = document.createElement('summary');
    const addr = s.SiteAddress ? anchorize(s.SiteAddress) : 'n/a';
    sm.innerHTML = `
      🌐 <strong>${s.SiteName || '(unnamed)'}</strong>
      <strong class="pill">WellKnownSite</strong>
      <span class="pill">Addr: ${addr}</span>
      <span class="pill">FrogID: ${s.FrogID || 'n/a'}</span>
      <span class="pill small">#${s.SiteID}</span>
    `;
    d.appendChild(sm);
    const body = document.createElement('div');
    body.style.marginTop = '8px';
    body.appendChild(spinner());
    d.addEventListener('toggle', () => {
      if (d.open) {
        body.innerHTML = '';
        const meta = document.createElement('div');
        meta.className = 'muted small';
        meta.innerHTML = s.Tags ? `Tags: ${s.Tags}` : 'No additional metadata.';
        body.appendChild(meta);
      }
    });
    d.appendChild(body);
    list.appendChild(d);
  }
}
