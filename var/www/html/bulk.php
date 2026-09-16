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
 **************************************************************/

/*
 * bulk.php - the REST arm of the AIConnect ramp.
 *
 * The socket receiver in the AIConnect service is the DATA PLANE arm: bytes
 * cross a socket the receiver opened. This is the same sink reached over HTTP,
 * so the identical ramp can be run three ways and compared:
 *
 *     --transport bulk         socket, no proxy         (data plane)
 *     --transport rest8080     HTTP direct to Apache    (semantic bypassed)
 *     --transport semantic80   HTTP through the proxy   (semantic path)
 *
 * Only the transport differs. Same objects, same sizes, same thread ramp, same
 * resource thresholds -- which is the only way the three numbers mean anything
 * next to each other.
 *
 * IT DISCARDS. The bytes are received, counted and dropped. A bulk sink that
 * wrote to MySQL would be measuring MySQL: the question is what the transport
 * can carry, and storage is a different question with a different answer.
 *
 *   POST /bulk.php        body = the object      -> {"ok":true,"bytes":N}
 *   GET  /bulk.php        -> {"ok":true,"transfers":N,"bytes":N}
 *
 * The counters live in a file because Apache serves each request in a separate
 * process; there is nothing in memory for them to share.
 *
 * INSTALL: cp bulk.php /var/www/html/
 */

header('Content-Type: application/json');

$counter = '/run/frognet/bulk_php_counters.json';
@mkdir('/run/frognet', 0777, true);

if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    $c = @json_decode(@file_get_contents($counter), true);
    if (!is_array($c)) { $c = ['transfers' => 0, 'bytes' => 0]; }
    echo json_encode(['ok' => true] + $c);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST' && $_SERVER['REQUEST_METHOD'] !== 'PUT') {
    http_response_code(405);
    echo json_encode(['ok' => false, 'error' => 'POST the object']);
    exit;
}

// Read and discard in chunks. Never buffer the whole object: an arm of this
// test is meant to move objects larger than the receiver's memory, and a sink
// that allocates the object size would fail for a reason that has nothing to
// do with the transport under test.
$n = 0;
$in = fopen('php://input', 'rb');
if ($in === false) {
    http_response_code(500);
    echo json_encode(['ok' => false, 'error' => 'cannot open input']);
    exit;
}
while (!feof($in)) {
    $chunk = fread($in, 1 << 16);
    if ($chunk === false) { break; }
    $n += strlen($chunk);
}
fclose($in);

// Last-write-wins on the counter file. Exact accounting is the producer's job;
// this is a sink, and a lock here would measure the lock.
$c = @json_decode(@file_get_contents($counter), true);
if (!is_array($c)) { $c = ['transfers' => 0, 'bytes' => 0]; }
$c['transfers'] += 1;
$c['bytes'] += $n;
@file_put_contents($counter, json_encode($c));

echo json_encode(['ok' => true, 'bytes' => $n]);
