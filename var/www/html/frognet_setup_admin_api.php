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
//============================================================
// frognet_setup_admin_api.php
// JSON API for the FrogNet setup admin page.
// All actions delegate to /usr/local/bin/frognet_setup_helper.bash via sudo.
//
// Actions (selected via ?action= or POST 'action'):
//   GET  state
//   GET  wifi_devices
//   GET  scan_wifi          ?iface=wlan0
//   GET  other_ifaces
//   GET  auto_ip
//   POST connect_wifi       JSON: {iface, ssid, password?}
//   POST disconnect         JSON: {iface}
//   POST apply_identity     JSON: {name, ip}
//
// Optional admin token: if /etc/frognet/admin_token exists and is non-empty,
// requests must supply X-Admin-Token header or ?token=… matching it.
//============================================================

header('Content-Type: application/json');
header('Cache-Control: no-store');

$HELPER     = '/usr/local/bin/frognet_setup_helper.bash';
$TOKEN_FILE = '/etc/frognet/admin_token';

if (file_exists($TOKEN_FILE)) {
    $expected = trim(@file_get_contents($TOKEN_FILE));
    if ($expected !== '') {
        $provided = $_SERVER['HTTP_X_ADMIN_TOKEN']
                  ?? $_REQUEST['token']
                  ?? '';
        if (!hash_equals($expected, (string)$provided)) {
            http_response_code(401);
            echo json_encode(['ok' => false, 'error' => 'unauthorized']);
            exit;
        }
    }
}

function helper(array $args): array {
    global $HELPER;
    $cmd = 'sudo -n ' . escapeshellcmd($HELPER);
    foreach ($args as $a) {
        $cmd .= ' ' . escapeshellarg((string)$a);
    }
    $out = shell_exec($cmd . ' 2>&1');
    if ($out === null) {
        return ['ok' => false, 'error' => 'helper invocation failed'];
    }
    $j = json_decode($out, true);
    if ($j === null) {
        return ['ok' => false, 'error' => 'helper produced non-JSON', 'raw' => $out];
    }
    return $j;
}

function body(): array {
    $raw = file_get_contents('php://input');
    if ($raw !== false && $raw !== '') {
        $j = json_decode($raw, true);
        if (is_array($j)) return $j;
    }
    return $_POST;
}

$action = $_REQUEST['action'] ?? '';

switch ($action) {

    case 'state':
        echo json_encode(helper(['state']));
        break;

    case 'wifi_devices':
        echo json_encode(helper(['wifi_devices']));
        break;

    case 'scan_wifi':
        $iface = $_REQUEST['iface'] ?? '';
        if ($iface === '') { echo json_encode(['ok'=>false,'error'=>'iface required']); break; }
        echo json_encode(helper(['scan_wifi', $iface]));
        break;

    case 'other_ifaces':
        echo json_encode(helper(['other_ifaces']));
        break;

    case 'auto_ip':
        echo json_encode(helper(['auto_ip']));
        break;

    case 'connect_wifi': {
        $b = body();
        $iface = $b['iface']    ?? '';
        $ssid  = $b['ssid']     ?? '';
        $pass  = $b['password'] ?? '';
        if ($iface === '' || $ssid === '') {
            echo json_encode(['ok'=>false,'error'=>'iface and ssid required']);
            break;
        }
        $args = ['connect_wifi', $iface, $ssid];
        if ($pass !== '') $args[] = $pass;
        echo json_encode(helper($args));
        break;
    }

    case 'disconnect': {
        $b = body();
        $iface = $b['iface'] ?? '';
        if ($iface === '') { echo json_encode(['ok'=>false,'error'=>'iface required']); break; }
        echo json_encode(helper(['disconnect', $iface]));
        break;
    }

    case 'apply_identity': {
        $b = body();
        $name = trim((string)($b['name'] ?? ''));
        $ip   = trim((string)($b['ip']   ?? ''));
        if ($name === '' || $ip === '') {
            echo json_encode(['ok'=>false,'error'=>'name and ip required']);
            break;
        }
        echo json_encode(helper(['apply_identity', $name, $ip]));
        break;
    }

    default:
        http_response_code(400);
        echo json_encode(['ok' => false, 'error' => 'unknown action: ' . $action]);
}
