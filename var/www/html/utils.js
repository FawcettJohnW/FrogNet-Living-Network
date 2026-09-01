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
export function fmtDate(s){
  if (!s) return '';
  const d = new Date(String(s).replace(' ','T'));
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString();
}

export function minutesSince(ts){
  if (!ts) return Infinity;
  const d = new Date(String(ts).replace(' ','T'));
  if (isNaN(d.getTime())) return Infinity;
  return (Date.now() - d.getTime())/60000;
}

export function ipPrefix24(ip){
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(String(ip||''));
  return m ? `${m[1]}.${m[2]}.${m[3]}.` : null;
}

export function debounce(fn, ms){ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; }

export function spinner(){
  const span = document.createElement('span');
  span.innerHTML = '<span class="spinner"></span> <span class="muted">Loading…</span>';
  return span;
}

export function makeDetails(summaryEl, contentEl, onOpenOnce){
  const d = document.createElement('details');
  const s = document.createElement('summary');
  s.appendChild(summaryEl);
  d.appendChild(s);
  const content = document.createElement('div');
  content.style.marginTop = '8px';
  content.appendChild(contentEl);
  d.appendChild(content);
  let loaded = false;
  d.addEventListener('toggle', async () => {
    if (d.open && !loaded && onOpenOnce) {
      loaded = true;
      await onOpenOnce(content);
    }
  });
  return d;
}
