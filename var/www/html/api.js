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
// Simple API client with .ok contract
export const storageKey = 'frognet_api';

export const getBaseUrl = () =>
  (localStorage.getItem(storageKey) || document.getElementById('apiUrl')?.value || '').trim();

export const setBaseUrl = (v) => localStorage.setItem(storageKey, v);

export const qs = (o={}) =>
  Object.entries(o)
    .filter(([,v]) => v !== undefined && v !== null && v !== '')
    .map(([k,v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');

async function handle(res){
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const j = await res.json();
  if (!j.ok) throw new Error(j.error || 'API error');
  return j;
}

export async function list(entity, params={}){
  const url = `${getBaseUrl()}?entity=${encodeURIComponent(entity)}&action=list${Object.keys(params).length?'&'+qs(params):''}`;
  return (await handle(await fetch(url))).rows || [];
}

export async function create(entity, payload){
  const url = `${getBaseUrl()}?entity=${encodeURIComponent(entity)}&action=create`;
  return handle(await fetch(url, {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
  }));
}

export async function update(entity, payload){
  const url = `${getBaseUrl()}?entity=${encodeURIComponent(entity)}&action=update`;
  return handle(await fetch(url, {
    method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
  }));
}
