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
// /var/www/html/getDefaultRoute.php
// Return default route + external nameservers (off-LAN) for FrogNet propagation.
// No authentication, no side effects. Designed for frequent polling.
//
// Contract:
// {
//   "ok": true,
//   "ts": 1700000000,
//   "ttl_sec": 45,
//   "host_ip": "10.x.y.1",
//   "default_via": "192.168.0.1",
//   "default_dev": "wlan1",
//   "exit_present": true,
//   "exit_gateway": "192.168.0.1",
//   "nameservers": ["205.171.3.25", ...],
//   "raw_default": "default via ..."
// }

header('Content-Type: application/json');

function now_ts() { return time(); }

function trim_line($s) {
  $s = str_replace(["\r","\n"], ["",""], $s);
  return trim($s);
}

function is_ipv4($s) {
  if (!is_string($s)) return false;
  $s = trim($s);
  if (!preg_match('/^\d{1,3}(\.\d{1,3}){3}$/', $s)) return false;
  $parts = explode('.', $s);
  if (count($parts) !== 4) return false;
  foreach ($parts as $p) {
    $n = intval($p);
    if ($n < 0 || $n > 255) return false;
  }
  return true;
}

function is_10x($ip) { return is_string($ip) && strpos($ip, "10.") === 0; }
function is_127($ip) { return is_string($ip) && strpos($ip, "127.") === 0; }

function local_host_ip_10() {
  // Best effort: first 10.* address on any interface
  $out = @shell_exec("ip -4 -o addr show 2>/dev/null");
  if (!is_string($out) || $out === "") return "";
  foreach (explode("\n", $out) as $line) {
    $line = trim_line($line);
    if ($line === "") continue;
    if (preg_match('/\s+inet\s+(\d+\.\d+\.\d+\.\d+)\//', $line, $m)) {
      $ip = $m[1];
      if (is_10x($ip)) return $ip;
    }
  }
  return "";
}

function get_default_route() {
  $raw = @shell_exec("ip route show default 2>/dev/null | head -n1");
  $raw = trim_line($raw);
  $via = "";
  $dev = "";
  if ($raw !== "") {
    $parts = preg_split('/\s+/', $raw);
    for ($i=0; $i<count($parts); $i++) {
      if ($parts[$i] === "via" && $i+1 < count($parts)) $via = $parts[$i+1];
      if ($parts[$i] === "dev" && $i+1 < count($parts)) $dev = $parts[$i+1];
    }
  }
  return [$raw, $via, $dev];
}

function get_external_nameservers() {
  // Collect from /etc/resolv.conf (and systemd resolved file if present),
  // keep only non-10.*, non-127.*, non-empty IPv4.
  $paths = ["/etc/resolv.conf", "/run/systemd/resolve/resolv.conf"];
  $seen = [];
  $out = [];

  foreach ($paths as $p) {
    if (!file_exists($p)) continue;
    $txt = @file_get_contents($p);
    if (!is_string($txt)) continue;
    foreach (explode("\n", $txt) as $line) {
      $line = trim_line($line);
      if ($line === "") continue;
      if (strpos($line, "nameserver") !== 0) continue;
      $parts = preg_split('/\s+/', $line);
      if (count($parts) < 2) continue;
      $ip = trim($parts[1]);
      if (!is_ipv4($ip)) continue;
      if (is_10x($ip) || is_127($ip)) continue;
      if (!isset($seen[$ip])) {
        $seen[$ip] = 1;
        $out[] = $ip;
      }
    }
  }
  return $out;
}

$ttl = 45;
$ts = now_ts();
$host_ip = local_host_ip_10();

list($raw_def, $via, $dev) = get_default_route();

$exit_present = false;
$exit_gateway = "";

if (is_ipv4($via) && !is_10x($via) && !is_127($via)) {
  $exit_present = true;
  $exit_gateway = $via;
}

// If there is no "via", treat as no exit (policy: only via indicates upstream gateway)
$nameservers = get_external_nameservers();

echo json_encode([
  "ok" => true,
  "ts" => $ts,
  "ttl_sec" => $ttl,
  "host_ip" => $host_ip,
  "default_via" => $via,
  "default_dev" => $dev,
  "exit_present" => $exit_present,
  "exit_gateway" => $exit_gateway,
  "nameservers" => $nameservers,
  "raw_default" => $raw_def,
], JSON_UNESCAPED_SLASHES);
