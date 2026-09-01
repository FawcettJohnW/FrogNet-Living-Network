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

$json = shell_exec('/usr/local/bin/makeHostJson.bash 2>/dev/null');

if ($json === null || trim($json) === '') {
    echo '[]';
    exit;
}

$raw = json_decode($json, true);
$hosts = [];
$seen  = [];

if (is_array($raw)) {
    foreach ($raw as $entry) {
        $ip = $entry['ip'] ?? '';
        if ($ip === '' || isset($seen[$ip])) continue;

        // IP must be in FrogNet space, but not transit or chorus.
        if (!preg_match('/^10\./', $ip))           continue;
        if (preg_match('/^10\.253\./', $ip))       continue;
        if (preg_match('/^10\.254\./', $ip))       continue;

        // Drop admin-alias phantoms (.2 is probe-only).
        if (preg_match('/\.2$/', $ip)) continue;

        $echo = $entry['echo'] ?? '';
        $name = '';
        if ($echo !== '') {
            $parts = explode(',', $echo);
            $name  = $parts[0];
        }
        if ($name === '' && !empty($entry['hostname'])) {
            $name = $entry['hostname'];
        }

        // Drop FrogNetAdmin-labeled entries from either field.
        if (strpos($name, 'FrogNetAdmin.') === 0) continue;
        if (strpos((string)($entry['hostname'] ?? ''), 'FrogNetAdmin.') === 0) continue;

        // Strip "FrogNetHost." prefix if present
        if (strpos($name, 'FrogNetHost.') === 0) {
            $name = substr($name, strlen('FrogNetHost.'));
        }
        if ($name === '') $name = 'unknown';

        $seen[$ip] = true;
        $hosts[] = [
            'ip'   => $ip,
            'name' => $name,
            'echo' => $echo,
        ];
    }
}

echo json_encode($hosts);
