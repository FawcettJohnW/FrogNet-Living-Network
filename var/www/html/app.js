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
import { setBaseUrl, storageKey } from './api.js';
import { } from './utils.js';
import { initFrogs } from './frogs.js';
import { initUsers } from './users.js';
import { initTeams } from './teams.js';
import { initSites } from './sites.js';

document.getElementById('yr').textContent = new Date().getFullYear();

if (!localStorage.getItem(storageKey)) {
  localStorage.setItem(storageKey, "http://databasehost.frognet/api.php");
}

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const id = tab.dataset.tab;
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
  });
});

(function init(){
  try {
    initFrogs();
    initUsers();
    initTeams();
    initSites();
  } catch (e){
    console.error(e);
    alert('Failed to load initial data: ' + e.message + '\nCheck API URL and CORS.');
  }
})();
