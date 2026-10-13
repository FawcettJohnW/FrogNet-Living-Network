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
 * /var/www/html/index.php -- [NODE_LANDING_V1 - T5] this node's landing page: its name, mode and build (from
 * frognet_node.inc.php, the same functions getSnapshot.php uses) and the links an operator wants: the setup page, the
 * dashboard, the monitor endpoint, and on a region FD&F's region members page. It replaces the MAMP default page and
 * "Welcome to the Admin BlackBox Portal foo".
 */
require_once __DIR__ . '/frognet_node.inc.php';
header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-store');
$errors = [];
$mode  = fn_mode();
$build = fn_build($errors);
$echo  = fn_self_echo($mode);
$f     = explode(',', $echo);
$name  = (trim($echo) !== '' && $f[0] !== '') ? $f[0] : '';
$dot1  = $f[1] ?? '';
if ($name === '') $errors[] = ($mode['mode'] === 'region' ? '/opt/fdf/var/facts.json' : 'getFrogNet.bash') . ': no echo (this node states no name)';
$h = function ($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); };
$host = $h(preg_replace('/:\d+$/', '', $_SERVER['HTTP_HOST'] ?? ''));
?><!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= $name !== '' ? $h($name) . ' - ' : '' ?>FrogNet node</title>
<style>
  :root { --bg:#f6f7f9; --fg:#1c2330; --muted:#5d6878; --card:#fff; --line:#dde2ea; --bad:#b3261e; --accent:#1b7f3b; }
  @media (prefers-color-scheme: dark) { :root { --bg:#12161c; --fg:#e6e9ee; --muted:#98a2b3; --card:#1a2029; --line:#2a323e; --bad:#ff6b5e; --accent:#4cc27a; } }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width:720px; margin:0 auto; padding:24px 16px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .facts { font-family:ui-monospace, Menlo, Consolas, monospace; font-size:13px; color:var(--muted); }
  .facts b { color:var(--fg); font-weight:600; }
  ul { list-style:none; padding:0; margin:20px 0; border:1px solid var(--line); border-radius:8px; background:var(--card); }
  li { padding:12px 16px; border-bottom:1px solid var(--line); }
  li:last-child { border-bottom:0; }
  a { color:var(--accent); font-weight:600; text-decoration:none; }
  a:hover { text-decoration:underline; }
  li span { display:block; color:var(--muted); font-size:13px; }
  .bad { color:var(--bad); font-size:13px; }
  code { font-size:13px; }
</style>
</head>
<body>
<main>
  <h1><?= $name !== '' ? $h($name) : 'FrogNet node' ?></h1>
  <div class="facts">
    <b><?= $h($dot1 !== '' ? $dot1 : 'no .1') ?></b> · mode <b><?= $h($mode['mode']) ?></b> · build <b><?= $h($build['build'] !== '' ? $build['build'] : '?') ?></b><?= isset($build['commit']) ? ' ' . $h($build['commit']) : '' ?>
    <div><?= $h($mode['why']) ?></div>
  </div>
<?php foreach ($errors as $e): ?>  <div class="bad">! <?= $h($e) ?></div>
<?php endforeach; ?>
  <ul>
    <li><a href="/setup_frognet.html">Setup</a><span>name, WiFi and the access point of this node</span></li>
    <li><a href="/fln_dashboard32.html">Dashboard</a><span>the map: members, ponds, hub links, link health</span></li>
    <li><a href="/getSnapshot.php">Monitor endpoint (getSnapshot.php)</a><span>this node's snapshot as JSON: mode, ponds, hub link, members, elections; <code>frognet_monitor</code> (and <code>--once</code>) draws it in a terminal</span></li>
<?php if ($mode['mode'] === 'region'): ?>
    <li><a href="http://<?= $host ?>:8090/fdf/fdf_dashboard.html">Region members (FD&amp;F)</a><span>the region as its RAM manager holds it; served by <code>php -S 0.0.0.0:8090 -t /opt/fdf/www</code></span></li>
<?php endif; ?>
  </ul>
</main>
</body>
</html>
