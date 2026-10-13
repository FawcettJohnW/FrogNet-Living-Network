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
    // [ECHO_FROM_THE_REGION_V1] On an FD&F region this node's echo is its own facts (the file fdf-client announces to
    // the region), in the form every region reader uses (RegionEcho, region_hosts.json): "<name>,<.1>,<upstream1>,<upstream2>".
    // No getFrogNet.bash (four helper scripts, a lock and a cache file). Off a region, unchanged.
    $region = false;
    foreach (['/opt/fdf/var/join.env'] as $f) {          // the one switch [REGION_SWITCH_ONE_FILE_V1]
        $t = @file_get_contents($f);
        if ($t !== false && preg_match('/^(export )?RINA_RAM=\S+/m', $t)) { $region = true; break; }
    }
    if ($region) {
        $facts = json_decode((string)@file_get_contents('/opt/fdf/var/facts.json'), true);
        if (!is_array($facts)) { echo ""; return; }      // no facts yet: no echo (not a member yet)
        $dot1 = '';
        foreach (($facts['addrs'] ?? []) as $r) {
            if (($r['kind'] ?? '') === 'own' && substr((string)($r['ip'] ?? ''), -2) === '.1') { $dot1 = $r['ip']; break; }
        }
        // [ECHO_UPSTREAM_FROM_THE_ROWS_V1] fields 3 and 4 are what getFrogNet.bash gave (getWlan0IP, getWlan1IP): this
        // node's upstream addresses, the facts' `uplink` rows in device order, 0.0.0.0 where there is none. (They were
        // read from facts.echo.upstream, which nothing writes: always empty.)
        $ups = [];
        foreach (($facts['addrs'] ?? []) as $r) {
            if (($r['kind'] ?? '') === 'uplink' && ($r['ip'] ?? '') !== '') $ups[] = $r['ip'];
        }
        $ups[] = '0.0.0.0'; $ups[] = '0.0.0.0';
        echo ($facts['name'] ?? '') . ',' . $dot1 . ',' . $ups[0] . ',' . $ups[1];
    } else {
        echo `/usr/local/bin/getFrogNet.bash`;
    }
?>

