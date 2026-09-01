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
// /var/www/html/api_semantic_hosts.php
// One-shot read of /etc/frognet/semantic_hosts for dashboard edge coloring.
// No daemon.

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function out($arr, $code=200){
  http_response_code($code);
  echo json_encode($arr, JSON_UNESCAPED_SLASHES);
  exit;
}

$path = "/etc/frognet/semantic_hosts";
if (!file_exists($path)) out(["ok"=>true, "nets"=>[], "edges"=>new stdClass(), "raw"=>""], 200);

$raw = @file_get_contents($path);
if ($raw === false) out(["ok"=>false, "error"=>"cannot read semantic_hosts"], 500);

$nets = [];   // list of CIDRs (strings)
$edges = [];  // cidr -> list of via IPs

$lines = preg_split("/\\r?\\n/", $raw);
foreach ($lines as $line){
  $line = preg_replace('/#.*/', '', $line);
  $line = trim($line);
  if ($line === '') continue;

  // tokens: <cidr|ip> [via <ip>]
  $parts = preg_split('/\\s+/', $line);
  if (count($parts) < 1) continue;

  $tok0 = $parts[0];

  // Normalize single IP to /24
  if (preg_match('/^(\\d{1,3}\\.){3}\\d{1,3}$/', $tok0)){
    $p = explode('.', $tok0);
    $cidr = $p[0].'.'.$p[1].'.'.$p[2].'.0/24';
    $tok0 = $cidr;
  }

  // Accept only /24 CIDRs (matches your policy usage)
  if (!preg_match('/^(\\d{1,3}\\.){3}0\\/24$/', $tok0)) continue;

  $cidr = $tok0;
  if (!in_array($cidr, $nets, true)) $nets[] = $cidr;

  if (count($parts) >= 3 && strtolower($parts[1]) === "via"){
    $via = $parts[2];
    if (preg_match('/^(\\d{1,3}\\.){3}\\d{1,3}$/', $via)){
      if (!isset($edges[$cidr])) $edges[$cidr] = [];
      if (!in_array($via, $edges[$cidr], true)) $edges[$cidr][] = $via;
    }
  }
}

sort($nets);
ksort($edges);

out([
  "ok" => true,
  "nets" => $nets,
  "edges" => $edges,
  "raw" => $raw,
]);
