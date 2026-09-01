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
// /var/www/html/api_semantic.php
// FrogNet telemetry API shim (NO direct DB access). Uses local api.php only.

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

$API_BASE = getenv('FROGNET_API_BASE');
if (!$API_BASE) {
  // default to local api.php on same host
  $scheme = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? "https" : "http";
  $host = $_SERVER['HTTP_HOST'] ?? 'localhost';
  $API_BASE = $scheme . "://" . $host . "/api.php";
}

$TIMEOUT = floatval(getenv('FROGNET_API_TIMEOUT') ?: "2.5");

function bad($msg, $detail=null, $code=500) {
  http_response_code($code);
  echo json_encode(["ok"=>false,"error"=>$msg,"detail"=>$detail], JSON_UNESCAPED_SLASHES);
  exit;
}

function http_get_json($url, $timeout) {
  $ctx = stream_context_create([
    "http" => [
      "method" => "GET",
      "timeout" => $timeout,
      "header" => "Accept: application/json\r\n",
    ]
  ]);
  $raw = @file_get_contents($url, false, $ctx);
  if ($raw === false) {
    $err = error_get_last();
    return [null, $err ? $err["message"] : "file_get_contents failed"];
  }
  $j = json_decode($raw, true);
  if ($j === null && json_last_error() !== JSON_ERROR_NONE) {
    return [null, "Invalid JSON from api.php (starts: " . substr(preg_replace('/\s+/', ' ', $raw), 0, 200) . ")"];
  }
  return [$j, null];
}

function api_values($api_base, $timeout, $sensorType, $nameLike, $limit=5000) {
  $q = [
    "entity" => "sensors",
    "action" => "values",
    "SensorType" => $sensorType,
    "SensorName__like" => $nameLike,
    "limit" => strval(intval($limit)),
    "parse" => "1",
  ];
  $url = $api_base . "?" . http_build_query($q);
  [$j, $err] = http_get_json($url, $timeout);
  if ($err) return [[], $url, $err];
  $rows = $j["rows"] ?? [];
  if (!is_array($rows)) $rows = [];
  return [$rows, $url, null];
}

function pick_latest_payload($rows) {
  // rows come from sensors/values parse=1, so parsed JSON should be in `data` (best), else try decode jsonData.
  // Choose payload with now_ts if present, else any with since_ts.
  $best = null;
  foreach ($rows as $r) {
    $p = null;
    if (isset($r["data"]) && is_array($r["data"])) $p = $r["data"];
    else if (isset($r["jsonData"]) && is_string($r["jsonData"])) {
      $tmp = json_decode($r["jsonData"], true);
      if (is_array($tmp)) $p = $tmp;
    }
    if (!$p) continue;
    if (isset($p["now_ts"])) return $p;
    if ($best === null) $best = $p;
  }
  return $best ?: new stdClass();
}

// --- router ---
$path = $_SERVER["PATH_INFO"] ?? "/";
if ($path === "/") $path = "/summary";

if ($path !== "/summary") {
  bad("not found", ["path"=>$path], 404);
}

$proxyLike  = "%\.SemanticProxy\.Bytes";
$daemonLike = "%\.SemanticDaemon\.Bytes";

[$proxyRows,  $proxyUrl,  $proxyErr]  = api_values($API_BASE, $TIMEOUT, "SemanticProxy",  $proxyLike);
[$daemonRows, $daemonUrl, $daemonErr] = api_values($API_BASE, $TIMEOUT, "SemanticDaemon", $daemonLike);

$out = [
  "ok" => true,
  "ts" => time(),
  "api_base" => $API_BASE,
  "sources" => [
    "proxy"  => ["url"=>$proxyUrl,  "error"=>$proxyErr],
    "daemon" => ["url"=>$daemonUrl, "error"=>$daemonErr],
  ],
  "proxy_rows" => $proxyRows,
  "daemon_rows" => $daemonRows,
  "proxy_latest" => pick_latest_payload($proxyRows),
  "daemon_latest" => pick_latest_payload($daemonRows),
];

echo json_encode($out, JSON_UNESCAPED_SLASHES);
