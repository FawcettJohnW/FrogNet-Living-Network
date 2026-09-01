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
require __DIR__ . '/frognet_intercept.php';

// Target URL – keep this EXACTLY as 127.0.0.1 for this test
$targetUrl = 'http://127.0.0.1/dummy_endpoint.php';

// Derive host/ip from the URL so the key is guaranteed to match
$parsed = parse_url($targetUrl);
$host   = $parsed['host'];              // should be "127.0.0.1"
$ip     = gethostbyname($host);

error_log("[FrogNet-Test] targetUrl=$targetUrl host=$host ip=$ip");

// Open a socket to the local daemon
$socket = frognet_open_socket($ip, 9009);

if ($socket === false) {
    echo "Failed to open socket to daemon\n";
    error_log("[FrogNet-Test] Failed to open socket to daemon at $ip:9009");
    exit;
}

// Seed the global connection map so interceptor thinks it's a known slow host
$GLOBALS['FROGNET_CONNECTION_MAP'][$host] = [
    'ip'        => $ip,
    'latency'   => 1500, // fake latency > 500ms
    'slow_link' => true,
    'socket'    => $socket
];

error_log("[FrogNet-Test] Seeded map for host=$host as slow_link=true");

// Now call the interceptor — it should detect slow_link=true and use the socket path.
$params = [
    'foo' => 'bar',
    'x'   => 123,
];

$result = frognet_intercept_request($targetUrl, $params, 'GET');

echo "<pre>Interceptor result:\n";
var_dump($result);
echo "</pre>\n";
