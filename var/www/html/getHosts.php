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
 * /var/www/html/getHosts.php
 *
 * Returns JSON array of known FrogNet hosts for recursive discovery
 * and dashboard consumption.
 *
 * Delegates to makeHostJson.bash, reshapes {ip, hostname, echo} into
 * {ip, name, echo}.
 *
 * FILTER RULES (primer-authoritative):
 *   - IP must match ^10\. (FrogNet space).
 *   - Exclude 10.253.x.x (WG tunnel transit /30 endpoints).
 *   - Exclude 10.254.x.x (chorus virtual subnets on frognet0 — never
 *     a discovery target).
 *   - Drop any IP ending in .2 (FrogNetAdmin probe-only phantom).
 *   - Drop any name or hostname beginning with "FrogNetAdmin."
 *     (defense in depth — even if /etc/hosts or makeHostJson leaks
 *     an admin alias through, the JSON served here is clean).
 *   - Strip "FrogNetHost." prefix from name on output.
 */

header('Content-Type: application/json');

// [HOSTJSON_FROM_THE_REGION_V1] On an FD&F region the host list is the region's: the tunnel daemon writes
// /var/lib/frognet/region_hosts.json on every region event; off a region it is makeHostJson.bash. The rows and the
// filter above are frognet_node.inc.php's fn_host_rows, the same rows getSnapshot.php serves as `members`
// [ONE_MONITOR_TRUTH_V1]. A source that cannot be read is an empty list (getSnapshot.php says why).
require_once __DIR__ . '/frognet_node.inc.php';
$errors = [];
$hosts = [];
foreach (fn_host_rows(fn_mode(), $errors) as $h) {
    unset($h['guid']);                                  // the shape every caller of this endpoint reads
    $hosts[] = $h;
}
echo json_encode($hosts);
