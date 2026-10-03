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
 * frognet_broker_admin_v4.php — Admin API for FrogNet Tunnel Broker v4
 *
 * Deployed on the BROKER host (the droplet).  The companion HTML UI is
 * pond_admin.html.  This file is the JSON gateway that pond_admin.html
 * calls via fetch().
 *
 * Two layers of auth:
 *   1. Page password (PAGE_PASSWORD constant below).  POST ?action=login
 *      with {password: "..."}; sets a PHP session cookie.  All other
 *      actions require an active session.
 *   2. The admin Bearer token loaded from /etc/frognet/admin_token_v4.conf,
 *      attached server-side to every backend call.  The browser never
 *      sees this token.
 *
 * Backend: http://127.0.0.1:18428/api/v1/...  (the v4 uvicorn process)
 *
 * URL shape: ?action=<verb>
 * Methods:   GET (read), POST (mutate).  PATCH and DELETE are tunnelled
 *            via POST + ?action verb so a stock browser fetch works
 *            without preflight surprises.
 */

session_set_cookie_params([
    'lifetime' => 0,
    'path'     => '/',
    'httponly' => true,
    'samesite' => 'Strict',
]);
session_start();

header('Content-Type: application/json');
header('Cache-Control: no-store');

// =============================================================================
// Configuration
// =============================================================================

const PAGE_PASSWORD     = 'Fr0gN3t!';
const ADMIN_TOKEN_FILE  = '/etc/frognet/admin_token_v4.conf';
const BROKER_URL        = 'http://127.0.0.1:18428';

// =============================================================================
// Helpers
// =============================================================================

function fail(string $msg, int $code = 400): void {
    http_response_code($code);
    echo json_encode(['error' => $msg]) . "\n";
    exit;
}

function require_session(): void {
    if (empty($_SESSION['admin_ok'])) {
        fail('Not authenticated', 401);
    }
}

function admin_token(): string {
    static $cached = null;
    if ($cached !== null) return $cached;
    $t = @file_get_contents(ADMIN_TOKEN_FILE);
    if ($t === false) {
        fail('Admin token file unreadable: ' . ADMIN_TOKEN_FILE, 500);
    }
    $cached = trim($t);
    if ($cached === '') {
        fail('Admin token file is empty', 500);
    }
    return $cached;
}

/**
 * Call the v4 broker.  Returns ['code'=>int,'body'=>string,'json'=>mixed].
 * On curl-level failure raises 502.
 */
function broker(string $method, string $path, $body = null): array {
    $url = BROKER_URL . $path;
    $ch  = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 15);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        'Content-Type: application/json',
        'Authorization: Bearer ' . admin_token(),
    ]);
    if ($body !== null) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
    }

    $response = curl_exec($ch);
    $code     = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err      = curl_error($ch);
    curl_close($ch);

    if ($err) fail("Backend error: $err", 502);

    return [
        'code' => $code,
        'body' => $response,
        'json' => json_decode($response, true),
    ];
}

/** Forward broker response back to the browser as-is (status + body). */
function passthrough(array $r): void {
    http_response_code($r['code']);
    echo $r['body'];
    exit;
}

function read_input(): array {
    $raw = file_get_contents('php://input');
    if ($raw === '' || $raw === false) return [];
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

// =============================================================================
// Dispatch
// =============================================================================

$action = $_GET['action'] ?? '';
$method = $_SERVER['REQUEST_METHOD'];
$input  = read_input();

switch ($action) {

    // -------- Auth --------------------------------------------------------

    case 'login':
        if ($method !== 'POST') fail('POST required', 405);
        $pw = $input['password'] ?? '';
        // hash_equals to keep timing sane even though the password is local.
        if (!hash_equals(PAGE_PASSWORD, (string)$pw)) {
            sleep(1);
            fail('Invalid password', 403);
        }
        $_SESSION['admin_ok'] = true;
        echo json_encode(['ok' => true]) . "\n";
        break;

    case 'logout':
        $_SESSION = [];
        if (ini_get('session.use_cookies')) {
            $p = session_get_cookie_params();
            setcookie(session_name(), '', time() - 42000,
                $p['path'], $p['domain'], $p['secure'], $p['httponly']);
        }
        session_destroy();
        echo json_encode(['ok' => true]) . "\n";
        break;

    case 'session':
        echo json_encode(['authenticated' => !empty($_SESSION['admin_ok'])]) . "\n";
        break;

    // -------- Ponds -------------------------------------------------------

    case 'ponds':
        require_session();
        passthrough(broker('GET', '/api/v1/ponds'));

    case 'create-pond':
        require_session();
        if ($method !== 'POST') fail('POST required', 405);
        $name = trim($input['name'] ?? '');
        if ($name === '') fail('name is required');

        $body = [
            'name'           => $name,
            'max_users'      => (int)($input['max_users'] ?? 50),
            'node_base'      => $input['node_base'] ?? '10.101.100.1',
            'node_increment' => (int)($input['node_increment'] ?? 10),
            'password'       => $input['password'] ?? '',
        ];
        $r = broker('POST', '/api/v1/ponds', $body);

        // If pond was created and admin checked allow_user_choruses, patch it.
        if ($r['code'] === 201 && !empty($input['allow_user_choruses'])) {
            broker('PATCH', '/api/v1/admin/ponds/' . rawurlencode($name),
                ['allow_user_choruses' => true]);
        }
        passthrough($r);

    case 'patch-pond':
        require_session();
        if ($method !== 'POST') fail('POST required', 405);
        $name = trim($input['name'] ?? '');
        if ($name === '') fail('name is required');

        $payload = [];
        if (array_key_exists('allow_user_choruses', $input)) {
            $payload['allow_user_choruses'] = (bool)$input['allow_user_choruses'];
        }
        if (array_key_exists('max_users', $input)) {
            $payload['max_users'] = (int)$input['max_users'];
        }
        if (array_key_exists('password', $input)) {
            $payload['password'] = (string)$input['password'];
        }
        if (!$payload) fail('no patchable fields provided');

        passthrough(broker('PATCH',
            '/api/v1/admin/ponds/' . rawurlencode($name), $payload));

    case 'pond-status':
        require_session();
        $name = trim($_GET['pond'] ?? '');
        if ($name === '') fail('pond parameter required');
        passthrough(broker('GET',
            '/api/v1/ponds/' . rawurlencode($name)));

    // -------- Choruses (admin path) ---------------------------------------

    case 'create-chorus':
        require_session();
        if ($method !== 'POST') fail('POST required', 405);
        $pond = trim($input['pond'] ?? '');
        $name = trim($input['name'] ?? '');
        if ($pond === '' || $name === '') {
            fail('pond and name are required');
        }
        $body = [
            'pond'     => $pond,
            'name'     => $name,
            'visible'  => isset($input['visible']) ? (bool)$input['visible'] : true,
            'password' => $input['password'] ?? '',
        ];
        passthrough(broker('POST', '/api/v1/admin/choruses', $body));

    case 'remove-chorus-member':
        require_session();
        if ($method !== 'POST') fail('POST required', 405);
        $body = [
            'pond'      => trim($input['pond']      ?? ''),
            'chorus'    => trim($input['chorus']    ?? ''),
            'node_name' => trim($input['node_name'] ?? ''),
        ];
        if ($body['pond'] === '' || $body['chorus'] === '' || $body['node_name'] === '') {
            fail('pond, chorus, and node_name are required');
        }
        passthrough(broker('DELETE', '/api/v1/chorus-member', $body));

    // -------- Nodes -------------------------------------------------------

    case 'nodes':
        require_session();
        $pond = $_GET['pond'] ?? '';
        $path = '/api/v1/admin/nodes';
        if ($pond !== '') $path .= '?pond=' . rawurlencode($pond);
        passthrough(broker('GET', $path));

    case 'force-deregister':
        require_session();
        if ($method !== 'POST') fail('POST required', 405);
        $pubkey = trim($input['pubkey'] ?? '');
        if ($pubkey === '') fail('pubkey is required');
        passthrough(broker('DELETE',
            '/api/v1/admin/nodes/' . rawurlencode($pubkey)));

    // -------- Blocklist ---------------------------------------------------

    case 'blocklist':
        require_session();
        passthrough(broker('GET', '/api/v1/admin/blocklist'));

    case 'block-node':
        require_session();
        if ($method !== 'POST') fail('POST required', 405);
        $body = [
            'pubkey'    => trim($input['pubkey']    ?? ''),
            'node_name' => trim($input['node_name'] ?? ''),
            'pond_name' => trim($input['pond_name'] ?? ''),
            'reason'    => trim($input['reason']    ?? ''),
        ];
        if ($body['pubkey'] === '') fail('pubkey is required');
        passthrough(broker('POST', '/api/v1/admin/blocklist', $body));

    case 'unblock-node':
        require_session();
        if ($method !== 'POST') fail('POST required', 405);
        $pubkey = trim($input['pubkey'] ?? '');
        if ($pubkey === '') fail('pubkey is required');
        passthrough(broker('DELETE',
            '/api/v1/admin/blocklist/' . rawurlencode($pubkey)));

    // -------- Default -----------------------------------------------------

    default:
        fail('Unknown action: ' . json_encode($action) . '. Available: '
            . 'login, logout, session, ponds, create-pond, patch-pond, '
            . 'pond-status, create-chorus, remove-chorus-member, nodes, '
            . 'force-deregister, blocklist, block-node, unblock-node');
}
