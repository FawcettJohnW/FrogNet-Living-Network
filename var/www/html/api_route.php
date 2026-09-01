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
// /var/www/html/api_route.php
// One-shot route probe for FrogNet dashboard: returns `ip route get <ip>` parsed.
// No daemon. Uses existing Apache/PHP.

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function out($arr, $code=200){
  http_response_code($code);
  echo json_encode($arr, JSON_UNESCAPED_SLASHES);
  exit;
}

$ip = isset($_GET['ip']) ? trim($_GET['ip']) : '';
if ($ip === '') out(["ok"=>false, "error"=>"missing ip"], 400);

// Strict IPv4 validation
if (!preg_match('/^(\d{1,3}\.){3}\d{1,3}$/', $ip)) out(["ok"=>false, "error"=>"invalid ip"], 400);
$parts = explode('.', $ip);
foreach ($parts as $p){
  $n = intval($p);
  if ($n < 0 || $n > 255) out(["ok"=>false, "error"=>"invalid ip"], 400);
}

// Safety: limit to private spaces you actually use.
// (Adjust if needed, but keep it strict.)
$is10 = (strpos($ip, "10.") === 0);
$is192 = (strpos($ip, "192.168.") === 0);
$is127 = (strpos($ip, "127.") === 0);
if (!$is10 && !$is192 && !$is127) out(["ok"=>false, "error"=>"ip not permitted"], 403);

// Run ip route get
$cmd = "ip route get " . escapeshellarg($ip) . " 2>/dev/null";
$raw = trim(shell_exec($cmd) ?? '');
if ($raw === '') out(["ok"=>false, "error"=>"no route or ip command failed"], 404);

// Parse minimal fields: dev, via, src
$dev = "";
$via = "";
$src = "";

$tokens = preg_split('/\s+/', $raw);
for ($i=0; $i<count($tokens); $i++){
  if ($tokens[$i] === 'dev' && $i+1 < count($tokens)) $dev = $tokens[$i+1];
  if ($tokens[$i] === 'via' && $i+1 < count($tokens)) $via = $tokens[$i+1];
  if ($tokens[$i] === 'src' && $i+1 < count($tokens)) $src = $tokens[$i+1];
}

out([
  "ok" => true,
  "ip" => $ip,
  "dev" => $dev,
  "via" => $via,   // empty => directly connected OR local
  "src" => $src,
  "raw" => $raw,
]);
