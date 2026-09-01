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
 * frognet_setup_v4_api.php — JSON gateway for setup_frognet.html
 *
 * Deployed on the FrogNetHost.  No page password — the page is served
 * over the LAN that the host itself controls (eth0, 10.x.x.1).  Anyone
 * already on that LAN is implicitly trusted.
 *
 * Routes calls to two bash helpers:
 *   /usr/local/bin/frognet_setup_helper.bash      — state, wifi, identity
 *   /usr/local/bin/frognet_setup_v4_helper.bash   — broker, qr, reboot
 *
 * Both helpers run via sudo (no password required for www-data on these
 * specific paths — see the sudoers fragment in the install notes).
 *
 * URL shape: ?action=<verb>
 */

header('Content-Type: application/json');
header('Cache-Control: no-store');

const HELPER_V1 = '/usr/local/bin/frognet_setup_helper.bash';
const HELPER_V4 = '/usr/local/bin/frognet_setup_v4_helper.bash';

function fail(string $msg, int $code = 400): void {
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $msg]);
    exit;
}

/** Run a helper subcommand; pass through its JSON. */
function helper(string $script, string $sub, array $args = []): void {
    $cmd = 'sudo ' . escapeshellarg($script) . ' ' . escapeshellarg($sub);
    foreach ($args as $a) {
        $cmd .= ' ' . escapeshellarg((string)$a);
    }
    $out = shell_exec($cmd . ' 2>&1');
    if ($out === null) fail('helper invocation failed', 500);
    // The helpers always emit valid JSON; pass through verbatim.
    echo $out;
    exit;
}

function read_input(): array {
    $raw = file_get_contents('php://input');
    if ($raw === '' || $raw === false) return [];
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

$action = $_GET['action'] ?? '';
$method = $_SERVER['REQUEST_METHOD'];
$input  = read_input();

switch ($action) {

    // -------- State / inventory (existing helper) -------------------------

    case 'state':
        helper(HELPER_V1, 'state');

    case 'scan-wifi':
        $iface = trim($_GET['iface'] ?? '');
        if ($iface === '') fail('iface required');
        helper(HELPER_V1, 'scan_wifi', [$iface]);

    case 'auto-ip':
        helper(HELPER_V1, 'auto_ip');

    // -------- Identity (existing helper) ----------------------------------

    case 'apply-identity':
        if ($method !== 'POST') fail('POST required', 405);
        $name = trim($input['name'] ?? '');
        $ip   = trim($input['ip']   ?? '');
        if ($name === '' || $ip === '') fail('name and ip required');
        helper(HELPER_V1, 'apply_identity', [$name, $ip]);

    // -------- Upstream WiFi (existing helper) -----------------------------

    case 'connect-wifi':
        if ($method !== 'POST') fail('POST required', 405);
        $iface = trim($input['iface'] ?? '');
        $ssid  = trim($input['ssid']  ?? '');
        $pw    = (string)($input['password'] ?? '');
        if ($iface === '' || $ssid === '') fail('iface and ssid required');
        helper(HELPER_V1, 'connect_wifi', [$iface, $ssid, $pw]);

    case 'disconnect':
        if ($method !== 'POST') fail('POST required', 405);
        $iface = trim($input['iface'] ?? '');
        if ($iface === '') fail('iface required');
        helper(HELPER_V1, 'disconnect', [$iface]);

    // -------- Broker (v4 helper) ------------------------------------------

    case 'broker-state':
        helper(HELPER_V4, 'broker_state');

    case 'apply-broker':
        if ($method !== 'POST') fail('POST required', 405);
        $host = trim($input['host'] ?? '');
        $port = trim((string)($input['port'] ?? '18257'));
        $pond = trim($input['pond'] ?? '');
        $pw   = (string)($input['pond_password'] ?? '');
        $cs   = is_array($input['choruses'] ?? null)
                ? implode(',', $input['choruses'])
                : (string)($input['choruses'] ?? '');
        if ($host === '' || $pond === '') fail('host and pond are required');
        helper(HELPER_V4, 'apply_broker', [$host, $port, $pond, $pw, $cs]);

    case 'clear-broker':
        if ($method !== 'POST') fail('POST required', 405);
        helper(HELPER_V4, 'clear_broker');

    // -------- Reboot + QR -------------------------------------------------

    case 'reboot':
        if ($method !== 'POST') fail('POST required', 405);
        helper(HELPER_V4, 'reboot');

    case 'qr':
        $text = trim($_GET['text'] ?? '');
        if ($text === '') fail('text required');
        helper(HELPER_V4, 'qr', [$text]);

    // =====================================================================
    // SSID_PROJECTION — paste into /var/www/html/frognet_setup_v4_api.php
    // Insert these two cases BEFORE the `default:` case at line 127.
    // Update the default-case error message to mention the new actions.
    // =====================================================================
    
    case 'ssid-projection-state':
        helper(HELPER_V4, 'ssid_projection_get');

    case 'apply-ssid-projection':
        if ($method !== 'POST') fail('POST required', 405);
        $enabled = $input['enabled'] ?? null;
        if (!is_bool($enabled)) fail("'enabled' must be true or false");
        $target = $enabled ? 'on' : 'off';
        helper(HELPER_V4, 'ssid_projection_set', [$target]);

    // =====================================================================
    // Update default case to add the new actions:
    //   "..., reboot, qr, ssid-projection-state, apply-ssid-projection"
    // =====================================================================
    
    default:
        fail("unknown action: " . json_encode($action) . ". Available: " .
             "state, scan-wifi, auto-ip, apply-identity, connect-wifi, " .
             "disconnect, broker-state, apply-broker, clear-broker, " .
             "reboot, qr");
}
