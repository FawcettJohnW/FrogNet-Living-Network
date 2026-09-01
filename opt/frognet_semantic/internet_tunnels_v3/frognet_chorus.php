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
 * frognet_chorus.php — Chorus management (pond members)
 *
 * Reads broker URL from /etc/frognet/tunnel.conf.
 * Node pubkey is read from /var/lib/frognet-tunnel/node_public.key.
 *
 * GET  frognet_chorus.php              → list choruses visible to this node
 * POST frognet_chorus.php?action=create → create chorus
 * POST frognet_chorus.php?action=join   → join chorus
 * POST frognet_chorus.php?action=leave  → leave chorus
 */

header('Content-Type: application/json');

// ── Config ────────────────────────────────────────────────────────────────

function broker_url(): string {
    $conf = '/etc/frognet/tunnel.conf'  // [ONE_CONF_V1];
    if (!file_exists($conf)) {
        http_response_code(503);
        die(json_encode(['error' => 'Broker not configured']));
    }
    foreach (file($conf) as $line) {
        if (strpos($line, 'BROKER_URL=') === 0) {
            return trim(substr($line, 11));
        }
    }
    http_response_code(503);
    die(json_encode(['error' => 'BROKER_URL missing in broker.conf']));
}

function node_pubkey(): string {
    $kf = '/var/lib/frognet-tunnel/node_public.key';
    if (!file_exists($kf)) {
        http_response_code(503);
        die(json_encode([
            'error' => 'Node not registered — run frognet_tunnel_setup_v3.sh first'
        ]));
    }
    return trim(file_get_contents($kf));
}

function broker_request(string $method, string $path,
                         array $body = []): array {
    $url = rtrim(broker_url(), '/') . $path;

    $opts = [
        'http' => [
            'method'        => $method,
            'header'        => 'Content-Type: application/json',
            'ignore_errors' => true,
        ],
        'ssl' => ['verify_peer' => false],
    ];
    if ($body) {
        $opts['http']['content'] = json_encode($body);
    }

    $ctx      = stream_context_create($opts);
    $response = @file_get_contents($url, false, $ctx);
    if ($response === false) {
        http_response_code(502);
        die(json_encode(['error' => 'Could not reach broker']));
    }

    $status_line = $http_response_header[0] ?? 'HTTP/1.1 200 OK';
    preg_match('/\s(\d{3})\s/', $status_line, $m);
    http_response_code((int)($m[1] ?? 200));

    return json_decode($response, true) ?? ['raw' => $response];
}

// ── Router ────────────────────────────────────────────────────────────────

$method = $_SERVER['REQUEST_METHOD'];
$action = $_GET['action'] ?? '';
$pubkey = node_pubkey();

if ($method === 'GET') {
    // List choruses visible to this node
    $encoded = urlencode($pubkey);
    echo json_encode(
        broker_request('GET', "/api/v4/choruses?pubkey={$encoded}")
    );

} elseif ($method === 'POST' && $action === 'create') {
    $body     = json_decode(file_get_contents('php://input'), true) ?? [];
    $name     = trim($body['name']     ?? '');
    $visible  = (bool)($body['visible'] ?? true);
    $password = $body['password']      ?? '';

    if (empty($name)) {
        http_response_code(400);
        die(json_encode(['error' => 'name is required']));
    }

    echo json_encode(broker_request('POST', '/api/v4/choruses', [
        'pubkey'   => $pubkey,
        'name'     => $name,
        'visible'  => $visible,
        'password' => $password,
    ]));

} elseif ($method === 'POST' && $action === 'join') {
    $body     = json_decode(file_get_contents('php://input'), true) ?? [];
    $name     = trim($body['name']     ?? '');
    $password = $body['password']      ?? '';

    if (empty($name)) {
        http_response_code(400);
        die(json_encode(['error' => 'name is required']));
    }

    echo json_encode(broker_request('POST', '/api/v4/choruses/join', [
        'pubkey'   => $pubkey,
        'name'     => $name,
        'password' => $password,
    ]));

} elseif ($method === 'POST' && $action === 'leave') {
    $body = json_decode(file_get_contents('php://input'), true) ?? [];
    $name = trim($body['name'] ?? '');

    if (empty($name)) {
        http_response_code(400);
        die(json_encode(['error' => 'name is required']));
    }

    echo json_encode(broker_request('POST', '/api/v4/choruses/leave', [
        'pubkey' => $pubkey,
        'name'   => $name,
    ]));

} else {
    http_response_code(405);
    echo json_encode(['error' => 'Method not allowed. Use GET or POST?action=create|join|leave']);
}
