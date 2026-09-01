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
 * echo.php — PRIMER 1 mode-2 origin.
 *
 * The upstream the remote test daemon forwards to. It echoes the request back
 * so a round trip proves end-to-end content fidelity through the whole chain:
 *
 *   proxy -> FNW1 over a true interface -> remote_test_daemon -> THIS -> back
 *
 * Run it standalone:
 *     php -S 0.0.0.0:8080 echo.php
 *
 * Then point the daemon at it:
 *     python3 remote_test_daemon.py --listen 0.0.0.0:19009 \
 *         --origin http://127.0.0.1:8080/echo.php
 *
 * The daemon POSTs the proxied request bytes here; this returns them verbatim
 * (with a little metadata) so the driver can assert the body survived the trip.
 */

$body = file_get_contents('php://input');

// Reflect method, a few headers, and the raw body. Keep it byte-faithful:
// the daemon compares the returned body to what the proxy sent.
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$ua     = $_SERVER['HTTP_USER_AGENT'] ?? '';
$ctype  = $_SERVER['CONTENT_TYPE'] ?? 'application/octet-stream';

header('Content-Type: ' . $ctype);
header('X-Echo-Method: ' . $method);
header('X-Echo-Length: ' . strlen($body));

// Echo the body exactly. (For a JSON-wrapped variant, json_encode an array
// of method/headers/body instead — but exact-body echo is the cleanest
// fidelity check.)
echo $body;
