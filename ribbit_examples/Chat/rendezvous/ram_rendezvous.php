<?php
/*
 * Copyright (C) 2016-2026 Fawcett Innovations LLC
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * ram_rendezvous.php -- launch a RAM-only FrogNet server on demand, keyed by tag.
 *
 * A demo of FrogNet shared memory across the Internet needs two strangers to
 * share one memory. The broker has the public address; this hands each demo its
 * own C++ ram_server, keyed by a tag both sides agree on out of band ("give it
 * a word, tell your friend the word"). First request for a tag launches a
 * server and returns its port; every later request for a live tag returns the
 * same port. That is the whole contract.
 *
 *   GET  ram_rendezvous.php?pass=WORD&tag=WORD          -> {"ok":true,"tag":...,"host":...,"port":N,"new":bool,"expires_in":S}
 *   GET  ram_rendezvous.php?pass=WORD&tag=WORD&stop=1   -> stop that tag's server (idempotent)
 *   GET  ram_rendezvous.php?pass=WORD&list=1            -> the live tags
 *
 * Every request carries a demo password from the operator's list (RZ_PASSWORDS
 * in the config). A request with no password, or one not on the list, is
 * refused before anything is launched, listed, or stopped. The password gates
 * access to the demo; the tag is what two users share to meet in one memory.
 *
 * Then, from anywhere:   frogbench --peer HOST:PORT     (HOST is this broker)
 *
 * Two processes per tag: a RAM server (structured shared state, discovery,
 * coordination, barriers, results) and a REFLECTOR (the fast plane: one-to-many
 * send-or-drop for high-rate current-value traffic). Both are in RAM, cleared on
 * exit; neither needs Apache, MySQL, a daemon, or a tunnel. Each binds a distinct
 * port in the configured range; both exit on idle so an abandoned tag frees its
 * ports; the pool is capped so a script cannot spawn without bound. A request for
 * a live tag returns BOTH ports.
 *
 * NO FALLBACKS: a tag that cannot be launched is an error, not a guess.
 */
require_once __DIR__ . '/rendezvous_config.php';
header('Content-Type: application/json');

function out($o, $code = 200) { http_response_code($code); echo json_encode($o); exit; }
function bad($code, $msg)      { out(['ok' => false, 'error' => $msg], $code); }

$reg_dir = RZ_STATE_DIR;
if (!is_dir($reg_dir) && !@mkdir($reg_dir, 0700, true)) bad(500, 'state directory unavailable');
$reg_path = $reg_dir . '/registry.json';

// ---- the registry is one JSON file, guarded by a lock ----------------------
$lock = fopen($reg_dir . '/.lock', 'c');
if (!$lock || !flock($lock, LOCK_EX)) bad(500, 'cannot lock the registry');
$reg = is_file($reg_path) ? (json_decode(file_get_contents($reg_path), true) ?: []) : [];

function alive($e) {
    // a tag is live only if its process still runs AND its port still answers
    if (!isset($e['pid']) || !posix_kill($e['pid'], 0)) return false;
    $c = @fsockopen('127.0.0.1', $e['port'], $errno, $errstr, 0.5);
    if ($c) { fclose($c); return true; }
    return false;
}
// reap the dead so the pool count and the ports are honest
foreach ($reg as $t => $e) {
    if (!alive($e)) { if (isset($e['pid'])) @posix_kill($e['pid'], SIGTERM); if (isset($e['reflector_pid']) && $e['reflector_pid']) @posix_kill($e['reflector_pid'], SIGTERM); unset($reg[$t]); }
}

// ---- password gate: an operator-issued word, before anything is done -------
$pass = isset($_GET['pass']) ? (string) $_GET['pass'] : '';
$authed = false;
foreach (RZ_PASSWORDS as $known) { if (hash_equals($known, $pass)) { $authed = true; break; } }  // constant-time, no early exit
if (!$authed) {
    file_put_contents($reg_path, json_encode($reg)); flock($lock, LOCK_UN); fclose($lock);
    bad(401, 'a valid demo password is required (pass=...)');
}

$tag = isset($_GET['tag']) ? $_GET['tag'] : '';

if (isset($_GET['list'])) {
    $live = [];
    foreach ($reg as $t => $e) $live[] = ['tag' => $t, 'port' => $e['port'], 'reflector_port' => isset($e['reflector_port']) ? $e['reflector_port'] : null, 'age_s' => time() - $e['started']];
    file_put_contents($reg_path, json_encode($reg)); flock($lock, LOCK_UN); fclose($lock);
    out(['ok' => true, 'host' => RZ_PUBLIC_HOST, 'count' => count($live), 'max' => RZ_MAX_SERVERS, 'servers' => $live]);
}

// a tag is a demo word: short, lower-case letters, digits and dashes. Nothing else runs a shell near it.
if ($tag === '' || !preg_match('/^[a-z0-9][a-z0-9-]{0,30}$/', $tag)) {
    file_put_contents($reg_path, json_encode($reg)); flock($lock, LOCK_UN); fclose($lock);
    bad(400, 'tag must be 1-31 chars of [a-z0-9-] and start alphanumeric');
}

if (isset($_GET['stop'])) {
    if (isset($reg[$tag])) { @posix_kill($reg[$tag]['pid'], SIGTERM); if (isset($reg[$tag]['reflector_pid']) && $reg[$tag]['reflector_pid']) @posix_kill($reg[$tag]['reflector_pid'], SIGTERM); unset($reg[$tag]); }
    file_put_contents($reg_path, json_encode($reg)); flock($lock, LOCK_UN); fclose($lock);
    out(['ok' => true, 'tag' => $tag, 'stopped' => true]);
}

// already running -> return its port
if (isset($reg[$tag])) {
    $e = $reg[$tag];
    file_put_contents($reg_path, json_encode($reg)); flock($lock, LOCK_UN); fclose($lock);
    out(['ok' => true, 'tag' => $tag, 'host' => RZ_PUBLIC_HOST, 'port' => $e['port'], 'reflector_port' => isset($e['reflector_port']) ? $e['reflector_port'] : null, 'new' => false, 'expires_in' => RZ_IDLE_TIMEOUT_S]);
}

// new tag -> launch, if there is room and a free port
if (count($reg) >= RZ_MAX_SERVERS) {
    file_put_contents($reg_path, json_encode($reg)); flock($lock, LOCK_UN); fclose($lock);
    bad(503, 'the demo is at capacity (' . RZ_MAX_SERVERS . ' servers); try again shortly or stop an unused tag');
}
$used = [];
foreach ($reg as $e) $used[$e['port']] = true;
// A RANDOM free port in the range. Try random picks first (cheap while the pool is
// sparse), then fall back to a full scan so a nearly-full range still finds the last
// free port rather than failing by bad luck.
$span = RZ_PORT_HIGH - RZ_PORT_LOW + 1;
$port = 0;
$tries = min(200, 4 * $span);
for ($i = 0; $i < $tries; $i++) {
    $p = random_int(RZ_PORT_LOW, RZ_PORT_HIGH);
    if (isset($used[$p])) continue;
    $c = @fsockopen('127.0.0.1', $p, $n, $s, 0.2);            // and nobody else is on it
    if ($c) { fclose($c); continue; }
    $port = $p; break;
}
if (!$port) {
    $free = [];
    for ($p = RZ_PORT_LOW; $p <= RZ_PORT_HIGH; $p++) if (!isset($used[$p])) $free[] = $p;
    shuffle($free);
    foreach ($free as $p) {
        $c = @fsockopen('127.0.0.1', $p, $n, $s, 0.2);
        if ($c) { fclose($c); continue; }
        $port = $p; break;
    }
}
if (!$port) { file_put_contents($reg_path, json_encode($reg)); flock($lock, LOCK_UN); fclose($lock); bad(503, 'no free port in the configured range'); }

// The server exits itself after the idle timeout, so an abandoned tag frees its
// port with no cron. ram_server has no idle-exit of its own, so a tiny watchdog
// wraps it: it launches the server, then stops it when the port has had no
// established connection for RZ_IDLE_TIMEOUT_S. Both are backgrounded and fully
// detached; this request does not wait for them.
$log = $reg_dir . '/' . $tag . '.log';
$cmd = sprintf(
    'setsid %s %s %d %d %s < /dev/null >> %s 2>&1 & echo $!',
    escapeshellarg(RZ_WATCHDOG),
    escapeshellarg(RZ_RAM_SERVER),
    $port,
    RZ_IDLE_TIMEOUT_S,
    escapeshellarg(RZ_BIND_HOST),
    escapeshellarg($log)
);
$pid = (int) trim(shell_exec($cmd));
if ($pid <= 0) { file_put_contents($reg_path, json_encode($reg)); flock($lock, LOCK_UN); fclose($lock); bad(500, 'failed to launch a server'); }

// wait briefly for it to be listening, so the caller gets a usable port or an honest error
$ready = false;
for ($i = 0; $i < 40; $i++) {
    $c = @fsockopen('127.0.0.1', $port, $n, $s, 0.2);
    if ($c) { fclose($c); $ready = true; break; }
    usleep(50000);
}
if (!$ready) {
    @posix_kill($pid, SIGTERM);
    file_put_contents($reg_path, json_encode($reg)); flock($lock, LOCK_UN); fclose($lock);
    bad(500, 'the server did not come up on its port');
}
// the fast plane: a reflector on its own port, launched the same way
$rport = 0;
for ($p = RZ_PORT_LOW; $p <= RZ_PORT_HIGH; $p++) {
    if ($p == $port || isset($used[$p])) continue;
    $c = @fsockopen('127.0.0.1', $p, $n, $s, 0.2);
    if ($c) { fclose($c); continue; }
    $rport = $p; break;
}
$rpid = 0;
if ($rport && defined('RZ_REFLECTOR') && RZ_REFLECTOR) {
    $rlog = $reg_dir . '/' . $tag . '.reflector.log';
    $rcmd = sprintf('setsid %s %s %d %d %s < /dev/null >> %s 2>&1 & echo $!',
        escapeshellarg(RZ_WATCHDOG), escapeshellarg(RZ_REFLECTOR), $rport, RZ_IDLE_TIMEOUT_S, escapeshellarg(RZ_BIND_HOST), escapeshellarg($rlog));
    $rpid = (int) trim(shell_exec($rcmd));
    for ($i = 0; $i < 40; $i++) { $c = @fsockopen('127.0.0.1', $rport, $n, $s, 0.2); if ($c) { fclose($c); break; } usleep(50000); }
}

$reg[$tag] = ['pid' => $pid, 'port' => $port, 'reflector_pid' => $rpid, 'reflector_port' => $rport, 'started' => time()];
file_put_contents($reg_path, json_encode($reg)); flock($lock, LOCK_UN); fclose($lock);
out(['ok' => true, 'tag' => $tag, 'host' => RZ_PUBLIC_HOST, 'port' => $port, 'reflector_port' => $rport ?: null, 'new' => true, 'expires_in' => RZ_IDLE_TIMEOUT_S]);
