<?php
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
/**
 * /var/www/html/getSnapshot.php -- [ONE_MONITOR_TRUTH_V1] the node's one monitor snapshot: build, mode (lan-only |
 * region), the ponds this node is in (forward marked), the members (name, guid, .1, role, alive, source), the hub
 * link (region edge iface, the kernel's handshake age), the elections (<role>.frognet from /etc/hosts), and what each
 * field was read from. Built by frognet_node.inc.php (its header says which file each field comes from in each mode);
 * getHosts.php, frognet_echo.php and index.php use the same functions. Read by frognet_monitor (Python, C++, C#),
 * fln_dashboard32.html and index.php's page.
 *
 * GET /getSnapshot.php -> {"snapshot":1, "at", "node":{name,dot1,guid,echo}, "build":{build,commit?,installed?},
 *                          "mode", "mode_why", "ponds":[{area,ram,forward,source,config,members?,me?,reach?,refused?,error?}],
 *                          "hub":[{iface,edge,handshake_age_s,source,error?}], "members":[{name,guid,ip,role,alive,source,
 *                          echo,self?}], "elections":{role: ip}, "sources":{...}, "errors":[...]}
 */
header('Content-Type: application/json');
header('Cache-Control: no-store');
require_once __DIR__ . '/frognet_node.inc.php';
echo json_encode(fn_snapshot(), JSON_UNESCAPED_SLASHES);
