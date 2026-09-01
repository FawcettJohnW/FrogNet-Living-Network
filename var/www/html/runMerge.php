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
// /var/www/html/runMerge.php
// Deferred merge trigger + propagation

function GUID() {
    if (function_exists('com_create_guid') === true) {
        return trim(com_create_guid(), '{}');
    }
    return sprintf(
        '%04X%04X-%04X-%04X-%04X-%04X%04X%04X',
        mt_rand(0,65535), mt_rand(0,65535),
        mt_rand(0,65535),
        mt_rand(16384,20479),
        mt_rand(32768,49151),
        mt_rand(0,65535), mt_rand(0,65535), mt_rand(0,65535)
    );
}

$SENT_DIR = "/etc/sentinels";
$SEEN_DIR = "$SENT_DIR/seen_sync";
@mkdir($SEEN_DIR, 0700, true);

$corrid = $_GET['corrid'] ?? GUID();
$seen_file = "$SEEN_DIR/$corrid";

if (file_exists($seen_file)) {
    // Already handled
    echo "OK (dedup)\n";
    exit;
}
touch($seen_file);

// 1) Enqueue local merge
$guid = GUID();
$cmdfile = __DIR__ . "/Commands/netCommand." . $guid;
file_put_contents($cmdfile, "/usr/local/bin/mergeHostsAndResolv.bash\n");

// 2) Forward trigger to neighbors
// Reuse your existing topology knowledge via makeHostJson
$hosts_json = @shell_exec("/usr/local/bin/makeHostJson.bash 2>/dev/null");
if ($hosts_json) {
    $hosts = json_decode($hosts_json, true);
    if (is_array($hosts)) {
        foreach ($hosts as $h) {
            $ip = $h['ip'] ?? null;
            if (!$ip || $ip === '127.0.0.1') continue;

            @file_get_contents(
                "http://$ip/runMerge.php?corrid=" . urlencode($corrid),
                false,
                stream_context_create(['http'=>['timeout'=>0.5]])
            );
        }
    }
}

echo "OK\n";

