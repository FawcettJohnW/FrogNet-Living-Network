<?php
/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// roles_ram.php -- [ROLE_ADMIN_PAGES_V1] the door the <Role>Admin pages reach a Role's memory through.
//
// A Role's memory is a role host's api.php, found by its name (exerciseHost.frognet; <role>Host.exercise for the others). A page in a browser cannot hold a
// read there itself (a fire is handed to a socket on the holder's machine), so this door, on the node the page is
// served from, holds it for the page:
//   ?op=read&role=R&like=SD:var.%            the memory's tuples that match, now
//   ?op=watch&role=R&like=...&seen=S&wait=25 the same, answered when they are not what the page last saw (S, the
//                                            signature the last answer carried), or after `wait` seconds. One held
//                                            read: nothing is asked again while it waits.
//   POST ?op=write&role=R&var=V&scope=S      body: the tuple's JSON. Written from this node, as this node's own.
// The door names a memory and nothing else about how it is reached: no protocol, no key.
const ROLES_RAM_BUILD = 'roles_ram.php 20261121a [ROLE_ADMIN_PAGES_V1]';
const HELD_DIR = '/run/frognet/held';
header('Content-Type: application/json');
header('X-Roles-Ram-Build: ' . ROLES_RAM_BUILD);

function out($code, $v) { http_response_code($code); echo json_encode($v + ['build' => ROLES_RAM_BUILD]); exit; }
function role() {
    $r = $_GET['role'] ?? '';
    if (preg_match('/^[a-z]{1,32}$/', $r) !== 1) out(400, ['ok' => false, 'why' => 'role=<a Role, or channel>']);
    return $r;
}
function memory($role) { return $role === 'channel' ? 'databasehost_control.frognet' : ($role === 'exercise' ? 'exerciseHost.frognet' : $role . 'Host.exercise'); }
function my_ip() {
    foreach (explode("\n", (string)shell_exec('ip -4 -o addr show 2>/dev/null')) as $l) {
        if (preg_match('/inet (10\.(\d+)\.\d+\.1)\//', $l, $m) && $m[2] !== '253' && $m[2] !== '254') return $m[1];
    }
    out(503, ['ok' => false, 'why' => 'this machine has no FrogNet 10/8 address: a held read has nowhere to be fired at']);
}
function get($url, $post = null) {
    $ch = curl_init($url);
    $o = [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 120, CURLOPT_CONNECTTIMEOUT => 20];
    if ($post !== null) $o += [CURLOPT_POST => true, CURLOPT_POSTFIELDS => $post, CURLOPT_HTTPHEADER => ['Content-Type: application/json']];
    curl_setopt_array($ch, $o);
    $body = curl_exec($ch); $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE); $err = curl_error($ch); curl_close($ch);
    $j = json_decode((string)$body, true);
    if ($code !== 200 || !is_array($j)) out(502, ['ok' => false, 'why' => 'the memory did not answer: ' . ($code ? "HTTP $code" : $err)]);
    return $j;
}
function tuples($rows) {
    $t = [];
    foreach ($rows as $r) {
        $t[] = ['name' => $r['SensorName'], 'from' => $r['SensorAddress'], 'id' => (int)$r['SensorID'],
                'value' => $r['data'] ?? json_decode((string)($r['jsonData'] ?? 'null'), true)];
    }
    usort($t, fn($a, $b) => [$a['name'], $a['id']] <=> [$b['name'], $b['id']]);
    return $t;
}
function hold($role, $like, $me, $hid) {
    $q = 'http://' . memory($role) . '/api.php?entity=held&action=hold&SensorType=' . rawurlencode($role)
       . (strpos($like, '%') === false ? '&SensorName=' : '&SensorName__like=') . rawurlencode($like)
       . '&holder=' . $me . '&hid=' . $hid;
    return tuples(get($q)['rows'] ?? []);
}

$op = $_GET['op'] ?? '';
if ($op === 'write') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') out(405, ['ok' => false, 'why' => 'POST a write']);
    $role = role(); $var = $_GET['var'] ?? ''; $scope = $_GET['scope'] ?? '';
    $value = json_decode(file_get_contents('php://input'), true);
    if (preg_match('/^[A-Za-z0-9_]{1,40}$/', $var) !== 1 || preg_match('/^[A-Za-z0-9_.-]{1,80}$/', $scope) !== 1 || !is_array($value))
        out(400, ['ok' => false, 'why' => 'var, scope and a JSON object']);
    $value['ts'] = time();
    $j = get('http://' . memory($role) . '/api.php?entity=sensor_data&action=upsert_by_name',
             json_encode(['SensorName' => "SD:$var.$scope", 'SensorType' => $role, 'SensorAddress' => my_ip(), 'jsonData' => $value]));
    out(200, ['ok' => (bool)($j['ok'] ?? false)]);
}
if ($op === 'read' || $op === 'watch') {
    $role = role(); $like = $_GET['like'] ?? 'SD:%';
    if (preg_match('/^SD:[A-Za-z0-9_.%-]{1,120}$/', $like) !== 1) out(400, ['ok' => false, 'why' => 'like=SD:<var>.<scope>, % for any']);
    $me = my_ip();
    $hid = 'page.' . getmypid() . '.' . bin2hex(random_bytes(4));
    $path = HELD_DIR . '/' . $hid . '.sock';
    $sock = null;
    if ($op === 'watch') {
        $sock = @stream_socket_server('udg://' . $path, $errno, $errstr, STREAM_SERVER_BIND);
        if (!$sock) out(503, ['ok' => false, 'why' => 'no socket for the fire (' . $errstr . ')']);
    }
    $t = hold($role, $like, $me, $hid);
    $sig = md5(json_encode($t));
    if ($op === 'watch' && $sig === ($_GET['seen'] ?? '')) {
        $r = [$sock]; $w = $e = null;
        if (stream_select($r, $w, $e, max(1, min(55, (int)($_GET['wait'] ?? 25)))) > 0) {      // the fire, or the time is up
            $t = hold($role, $like, $me, $hid);
            $sig = md5(json_encode($t));
        }
    }
    if ($sock) { fclose($sock); @unlink($path); }
    out(200, ['ok' => true, 'role' => $role, 'seen' => $sig, 'tuples' => $t]);
}
out(400, ['ok' => false, 'why' => 'op=read, watch or write']);
