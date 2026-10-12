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
 * /var/www/html/getPonds.php -- [PONDS_ARE_LISTED_V1] every network (pond) this machine takes part in, read LIVE from
 * each pond's own RAM manager on every request: which one is forward, each pond's published config
 * (pond, config, net), its members (name, role, alive, address in that pond), this machine's place in it, and what
 * this machine reaches there (/opt/fdf/var/reach/<area>.json, fdf-reach) or why that was refused. For the dashboard
 * and frognet_monitor. Nothing is cached and nothing is guessed: a pond that does not answer says so ("error").
 *
 * The ponds come from /opt/fdf/var/ponds/<area>.ram (the RAM manager's address, no key; written by fdf-client and
 * fdf-pond-add). A node joined the single-pond way (join.env, no ponds/) is one pond, forward.
 *
 * GET /getPonds.php -> {"forward":"<area>","me":"w:<guid>","ponds":[{area, ram, forward, error?, config?,
 *                       me?:{role,alive,addr}, members:[{name,role,alive,ip}], reach?:{...}, refused?}]}
 */
header('Content-Type: application/json');
header('Cache-Control: no-store');
const API = '/FawcettInnovations.RINA.ram_interface.php';
$V = '/opt/fdf/var';

function ram_call($ram, $method, $q, $body = null) {
    $ctx = stream_context_create(['http' => ['method' => $method, 'timeout' => 2, 'ignore_errors' => true,
        'header' => "Content-Type: application/json\r\n", 'content' => $body === null ? '' : json_encode($body)]]);
    $t = @file_get_contents('http://' . $ram . API . $q, false, $ctx);
    if ($t === false) return null;
    $j = json_decode($t, true);
    return is_array($j) ? $j : null;
}

// a member's address in its pond: a node's .1 (the /24 it serves), a leaf's or a reacher's given /32
function member_ip($f) {
    $role = $f['role'] ?? '';
    if ($role === 'leaf' || $role === 'participant') return explode('/', (string)($f['addr'] ?? ''))[0];
    foreach (($f['addrs'] ?? []) as $r) {
        $ip = (string)($r['ip'] ?? '');
        if (($r['kind'] ?? '') === 'own' && strpos($ip, '10.') === 0 && substr($ip, -2) === '.1') return $ip;
    }
    if (!empty($f['subnet'])) { $b = explode('/', $f['subnet'])[0]; return substr($b, 0, strrpos($b, '.')) . '.1'; }
    return '';
}

$forward = trim((string)@file_get_contents("$V/forward"));
$facts = json_decode((string)@file_get_contents("$V/facts.json"), true);
$me = 'w:' . (is_array($facts) ? ($facts['guid'] ?? '') : '');

$ponds = [];
foreach ((glob("$V/ponds/*.ram") ?: []) as $f) $ponds[basename($f, '.ram')] = trim((string)@file_get_contents($f));
if (!$ponds) {                                   // one pond, joined before ponds existed: join.env is its RAM manager
    $t = (string)@file_get_contents("$V/join.env");
    if (preg_match('/^(export )?RINA_RAM=(\S+)/m', $t, $m)) { $ponds[$forward !== '' ? $forward : 'this network'] = $m[2]; if ($forward === '') $forward = 'this network'; }
}

$out = [];
foreach ($ponds as $area => $ram) {
    $p = ['area' => $area, 'ram' => $ram, 'forward' => $area === $forward, 'members' => []];
    $w = ram_call($ram, 'POST', '?op=rina', ['op' => 'world']);
    if (!$w || empty($w['ok'])) { $p['error'] = "its RAM manager at $ram did not answer"; }
    else {
        $p['server'] = $w['server'] ?? '';
        foreach (($w['participants'] ?? []) as $q) {
            $f = is_array($q['facts'] ?? null) ? $q['facts'] : [];
            $role = (string)($f['role'] ?? 'node');
            if ($role === 'participant' && !empty($f['reach'])) $role = 'reacher';
            $m = ['name' => (string)($q['name'] ?? ''), 'role' => $role, 'alive' => (bool)($q['alive'] ?? false), 'ip' => member_ip($f)];
            if (($q['ns'] ?? '') === $me) { $m['me'] = true; $p['me'] = ['role' => $role, 'alive' => $m['alive'], 'addr' => $m['ip']]; }
            $p['members'][] = $m;
        }
        usort($p['members'], function ($a, $b) { return strcmp($a['name'], $b['name']); });
        $c = ram_call($ram, 'GET', '?op=read&service=pond&variable=config&instance=net');
        $cell = $c['cells'][0]['bag'] ?? ($c['rows'][0]['bag'] ?? null);
        $p['config'] = is_array($cell) ? ['reach' => $cell['reach'] ?? '', 'map' => $cell['map'] ?? ''] : null;   // null: publishes none
    }
    $r = json_decode((string)@file_get_contents("$V/reach/$area.json"), true);
    if (is_array($r)) $p['reach'] = ['as' => $r['addr'] ?? '', 'here' => $r['self'] ?? '', 'reach' => $r['reach'] ?? '',
                                     'routes' => $r['routes'] ?? [], 'alias' => $r['alias'] ?? (object)[]];
    $why = @file_get_contents("$V/reach/$area.refused");
    if ($why !== false) $p['refused'] = trim($why);
    $out[] = $p;
}
usort($out, function ($a, $b) { return ($b['forward'] <=> $a['forward']) ?: strcmp($a['area'], $b['area']); });
echo json_encode(['forward' => $forward, 'me' => $me, 'ponds' => $out]);
