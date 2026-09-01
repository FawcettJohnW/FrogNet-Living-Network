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
import { fmtDate, minutesSince, ipPrefix24, spinner, makeDetails } from './utils.js';

export function initFrogs(){
  document.getElementById('reloadFrogs').onclick = load;
  document.getElementById('frogFilter').addEventListener('input', debounce(load, 250));
  load();
}
function debounce(fn, ms){ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; }

async function load(){
  const list = document.getElementById('frogList');
  list.innerHTML = '';
  const filter = document.getElementById('frogFilter').value.trim().toLowerCase();
  const frogs = await api.list('known_frognets', { order:'NetworkName', limit:1000 });
  const filtered = filter ? frogs.filter(f => {
    const pack = `${f.NetworkName||''} ${f.FrogID||''} ${f.IPAddress||''}`.toLowerCase();
    return pack.includes(filter);
  }) : frogs;

  if (!filtered.length) { list.innerHTML = '<div class="empty">No KnownFrogNets found.</div>'; return; }

  for (const f of filtered){
    const summary = document.createElement('div');

    const mins = minutesSince(f.LastHeartbeat);
    let cls = 'status-ok';
    if (mins > 30) cls = 'status-danger';
    else if (mins >= 15) cls = 'status-warn';

    summary.innerHTML = `
      <strong>${f.NetworkName || '(unnamed)'}</strong>
      <span class="pill">IP: ${f.IPAddress || 'n/a'}</span>
      <span class="pill">FrogID: ${f.FrogID || 'n/a'}</span>
      ${f.LastHeartbeat ? `<span class="pill">Last: ${fmtDate(f.LastHeartbeat)}</span>` : '<span class="pill">Last: n/a</span>'}
    `;
    const content = document.createElement('div');
    content.appendChild(spinner());

    const det = makeDetails(summary, content, async (hostContent) => {
      hostContent.innerHTML = '';
      try {
        const prefix = ipPrefix24(f.IPAddress);
        if (!prefix) throw new Error('Invalid FrogNet IP');

        const [sensorCand, actCand] = await Promise.all([
          api.list('sensors',   {'SensorAddress__like': `${prefix}%`, order:'SensorName',   limit:1000 }),
          api.list('actuators', {'ActuatorAddress__like': `${prefix}%`, order:'ActuatorName', limit:1000 })
        ]);

        const sensors = sensorCand.filter(s => ipPrefix24(s.SensorAddress) === prefix);
        const acts    = actCand.filter(a => ipPrefix24(a.ActuatorAddress) === prefix);

        const blocks = [];
        const sWrap = document.createElement('div'); sWrap.className = 'list';
        const sHdr = document.createElement('div'); sHdr.className = 'muted'; sHdr.textContent = 'Sensors';
        blocks.push(sHdr);
        if (!sensors.length) { sWrap.innerHTML = '<div class="empty">No sensors for this host.</div>'; }
        else {
          for (const s of sensors){
            const sSum = document.createElement('div');
            sSum.innerHTML = `
              🛰️ <strong>${s.SensorName}</strong>
              <strong class="pill">${s.SensorType}</strong>
              <span class="pill">Addr: ${s.SensorAddress}</span>
              <span class="pill small">#${s.SensorID}</span>
            `;
            const sContent = document.createElement('div');
            sContent.appendChild(spinner());

            const sDet = makeDetails(sSum, sContent, async (sensorContent) => {
              sensorContent.innerHTML = '';
              try {
                const dataRows = await api.list('sensor_data', { SensorID: s.SensorID, order: 'SensorID', limit: 1 });
                if (!dataRows.length) {
                  sensorContent.innerHTML = '<div class="empty">No SensorData row (yet).</div>';
                  return;
                }
                const row = dataRows[0];
                const pre = document.createElement('pre'); pre.className = 'json';
                try {
                  const parsed = JSON.parse(row.jsonData || '{}');
                  pre.textContent = JSON.stringify(parsed, null, 2);
                } catch { pre.textContent = (row.jsonData || '').toString(); }
                sensorContent.appendChild(pre);
              } catch(err){
                sensorContent.innerHTML = `<div class="error">Failed to load SensorData: ${err.message}</div>`;
              }
            });
            sWrap.appendChild(sDet);
          }
        }
        blocks.push(sWrap);

        const aWrap = document.createElement('div'); aWrap.className = 'list';
        const aHdr = document.createElement('div'); aHdr.className = 'muted'; aHdr.style.marginTop = '8px'; aHdr.textContent = 'Actuators';
        blocks.push(aHdr);
        if (!acts.length) { aWrap.innerHTML = '<div class="empty">No actuators for this host.</div>'; }
        else {
          for (const a of acts){
            const aSum = document.createElement('div');
            aSum.innerHTML = `
              ⚙️ <strong>${a.ActuatorName}</strong>
              <strong class="pill">${a.ActuatorType}</strong>
              <span class="pill">Addr: ${a.ActuatorAddress}</span>
              <span class="pill small">#${a.ActuatorID}</span>
            `;
            const aContent = document.createElement('div');
            aContent.appendChild(spinner());
            const aDet = makeDetails(aSum, aContent, async (actContent) => {
              actContent.innerHTML = '<div class="muted small">No detailed actuator view implemented.</div>';
            });
            aWrap.appendChild(aDet);
          }
        }
        blocks.push(aWrap);

        blocks.forEach(b => hostContent.appendChild(b));
      } catch(err){
        hostContent.innerHTML = `<div class="error">Failed to load details: ${err.message}</div>`;
      }
    });

    det.querySelector('summary').classList.add(cls);
    document.getElementById('frogList').appendChild(det);
  }
}
