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
 * wg-route.php — Dynamic WireGuard route manager for FrogNet transit droplet
 *
 * Called by discovery on FrogNet nodes when depth-2+ subnets are found
 * behind a peer.  Adds the subnet to the appropriate peer's AllowedIPs
 * and installs a kernel route so the droplet forwards traffic.
 *
 * POST /wg-route.php
 *   subnet=10.x.y.0/24       — the subnet to make routable
 *   via=10.x.y.1             — FrogNet IP of the relay node (parent)
 *
 * Security: only accepts requests from 10.253.253.x (WireGuard tunnel peers)
 *
 * Requires: /etc/sudoers.d/wg-route granting www-data access to wg and ip
 *   www-data ALL=(ALL) NOPASSWD: /usr/bin/wg show wg0 allowed-ips
 *   www-data ALL=(ALL) NOPASSWD: /usr/bin/wg set wg0 peer *
 *   www-data ALL=(ALL) NOPASSWD: /sbin/ip route *
 */

header('Content-Type: text/plain');

// ── Gate: only tunnel peers ──
$remote = $_SERVER['REMOTE_ADDR'] ?? '';
if (strpos($remote, '10.253.253.') !== 0) {
    http_response_code(403);
    exit("DENY remote=$remote\n");
}

// ── Validate inputs ──
$subnet = trim($_POST['subnet'] ?? '');
$via    = trim($_POST['via'] ?? '');

if (!preg_match('/^10\.\d{1,3}\.\d{1,3}\.0\/24$/', $subnet)) {
    http_response_code(400);
    exit("ERR invalid subnet=$subnet\n");
}
if (!preg_match('/^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/', $via)) {
    http_response_code(400);
    exit("ERR invalid via=$via\n");
}

// ── Derive the /24 that contains the via IP ──
$via_parts  = explode('.', $via);
$via_subnet = "{$via_parts[0]}.{$via_parts[1]}.{$via_parts[2]}.0/24";

// ── Find the peer whose AllowedIPs includes the via subnet ──
$raw = trim(shell_exec('sudo /usr/bin/wg show wg0 allowed-ips 2>&1'));
if (empty($raw)) {
    http_response_code(500);
    exit("ERR wg show failed\n");
}

$target_peer = null;
$current_ips = [];

foreach (explode("\n", $raw) as $line) {
    $parts = preg_split('/\t+/', trim($line), 2);
    if (count($parts) < 2) continue;

    $peer = $parts[0];
    $ips  = preg_split('/\s+/', trim($parts[1]));

    if (in_array($via_subnet, $ips, true)) {
        $target_peer = $peer;
        $current_ips = $ips;
        break;
    }
}

if ($target_peer === null) {
    http_response_code(404);
    exit("ERR no peer owns via_subnet=$via_subnet\n");
}

// ── Idempotent: already present? ──
if (in_array($subnet, $current_ips, true)) {
    exit("OK already_present subnet=$subnet peer=$target_peer\n");
}

// ── Append subnet to peer's AllowedIPs ──
$new_ips = implode(',', array_merge($current_ips, [$subnet]));
$wg_cmd  = sprintf(
    'sudo /usr/bin/wg set wg0 peer %s allowed-ips %s 2>&1',
    escapeshellarg($target_peer),
    escapeshellarg($new_ips)
);
$wg_out = trim(shell_exec($wg_cmd));
if (!empty($wg_out)) {
    http_response_code(500);
    exit("ERR wg set failed: $wg_out\n");
}

// ── Add kernel route so packets for this subnet go into wg0 ──
// Check if route already exists first
$route_check = trim(shell_exec("sudo /sbin/ip route show $subnet dev wg0 2>&1"));
if (empty($route_check)) {
    $route_cmd = sprintf('sudo /sbin/ip route add %s dev wg0 2>&1', escapeshellarg($subnet));
    $route_out = trim(shell_exec($route_cmd));
    if (!empty($route_out) && strpos($route_out, 'File exists') === false) {
        // Route add failed but AllowedIPs was already updated — log but don't fail
        error_log("wg-route: route add failed: $route_out");
    }
}

exit("OK added subnet=$subnet peer=$target_peer\n");
