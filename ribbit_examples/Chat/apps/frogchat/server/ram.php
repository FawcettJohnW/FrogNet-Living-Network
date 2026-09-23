<?php
/*
 * Copyright (C) 2016-2026 Fawcett Innovations LLC
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * ram.php -- an application's FrogNet RAM. The whole API.
 *
 * A cell is addressed by (service, variable, instance) and holds one JSON bag.
 * Writing replaces. Reading returns what is there now. A read may be held open
 * until enough cells exist. Nothing here knows what the application is.
 *
 *   POST   ram.php?op=write     {"service","variable","instance","bag":{...}}
 *   GET    ram.php?op=read&service=S[&variable=V][&instance=I]
 *                  [&fresh_s=N][&wait_s=F&min_rows=N][&wait_s=F&after=G]
 *   DELETE ram.php?op=remove&id=N
 *
 * Every answer is {"ok":true,...} or {"ok":false,"error":"..."} with a 4xx/5xx.
 * Every write, to any cell, takes the next `id` -- the memory's own write order,
 * taken from the single WriteOrder row inside the write's transaction, so ids are
 * in COMMIT order. A read given `after=N` returns only
 * cells written since N (id > N), and with wait_s it is held open until there
 * is one: "wake me when something is written here." Because the order is the
 * memory's and not any one cell's, that works on a partial index too:
 * service + variable with the instance left open waits on ALL of its instances.
 * (tensor_plane has the same read -- block until the generation advances.)
 *
 * The other blocking read is the tree's [ASK_ONCE_AND_WAIT_V1] (min_rows): capped, returns the
 * moment the condition is met, and on expiry returns what it has -- short of
 * what was asked, so "not yet" is distinct by construction.
 *
 * Listens on loopback behind the semantic daemon. It is not an Internet API.
 */
require_once __DIR__ . '/ram_config.php';
header('Content-Type: application/json');

function out($o, $code = 200) { http_response_code($code); echo json_encode($o); exit; }
function bad($code, $msg)      { out(['ok' => false, 'error' => $msg], $code); }

mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);
try {
    $db = new mysqli(RAM_DB_HOST, RAM_DB_USER, RAM_DB_PASS, RAM_DB_NAME);
    $db->set_charset('utf8mb4');
} catch (Throwable $e) { bad(500, 'memory unavailable: ' . $e->getMessage()); }

$op     = $_GET['op'] ?? '';
$method = $_SERVER['REQUEST_METHOD'];

try {
    if ($op === 'write') {
        if ($method !== 'POST') bad(405, 'write is POST');
        $in = json_decode(file_get_contents('php://input'));
        if (!is_object($in)) bad(400, 'body is not a JSON object');
        foreach (['service', 'variable', 'instance'] as $k)
            if (!isset($in->$k) || !is_string($in->$k) || $in->$k === '') bad(400, "missing coordinate: $k");
        if (!isset($in->bag) || !is_object($in->bag)) bad(400, 'bag must be a JSON object');
        $bag = json_encode($in->bag);   // no assoc decode: {"0":5} stays an object ([AN_OBJECT_IS_NOT_A_LIST_V1])
        // One transaction: take the next number from the ONE row every writer takes
        // (so ids are in commit order and writers queue rather than deadlock), then
        // put the value at its address under that number.
        $db->begin_transaction();
        $db->query('UPDATE WriteOrder SET n = LAST_INSERT_ID(n + 1) WHERE k = 1');
        if ($db->affected_rows !== 1) { $db->rollback(); bad(500, 'memory has no WriteOrder row'); }
        $id = (int)$db->insert_id;
        $st = $db->prepare('INSERT INTO Cell (id, service, variable, instance, bag) VALUES (?,?,?,?,?)
                            ON DUPLICATE KEY UPDATE id = VALUES(id), bag = VALUES(bag), updated = CURRENT_TIMESTAMP(3)');
        $st->bind_param('issss', $id, $in->service, $in->variable, $in->instance, $bag);
        $st->execute();
        $db->commit();
        out(['ok' => true, 'id' => $id]);
    }

    if ($op === 'read') {
        if ($method !== 'GET') bad(405, 'read is GET');
        $service = $_GET['service'] ?? '';
        if ($service === '') bad(400, 'missing coordinate: service');
        $where = 'service = ?'; $types = 's'; $args = [$service];
        foreach (['variable', 'instance'] as $k)
            if (isset($_GET[$k]) && $_GET[$k] !== '') { $where .= " AND $k = ?"; $types .= 's'; $args[] = $_GET[$k]; }
        $fresh = isset($_GET['fresh_s']) ? (int)$_GET['fresh_s'] : 0;
        if ($fresh > 0) { $where .= ' AND updated >= NOW(3) - INTERVAL ? SECOND'; $types .= 'i'; $args[] = $fresh; }
        $after = isset($_GET['after']) ? (int)$_GET['after'] : -1;
        if ($after >= 0) { $where .= ' AND id > ?'; $types .= 'i'; $args[] = $after; }
        $sql = "SELECT id, service, variable, instance, bag, UNIX_TIMESTAMP(updated) AS updated_epoch
                FROM Cell WHERE $where ORDER BY id";
        $read = function () use ($db, $sql, $types, $args) {
            $st = $db->prepare($sql); $st->bind_param($types, ...$args); $st->execute();
            return $st->get_result()->fetch_all(MYSQLI_ASSOC);
        };
        $wait = isset($_GET['wait_s'])   ? (float)$_GET['wait_s'] : 0.0;
        $min  = isset($_GET['min_rows']) ? (int)$_GET['min_rows'] : 0;
        if ($wait > 30.0) $wait = 30.0;
        if ($after >= 0 && $min <= 0) $min = 1;      // "something newer than what I hold"
        $rows = $read();
        if ($wait > 0 && $min > 0 && count($rows) < $min) {
            $deadline = microtime(true) + $wait;
            while (count($rows) < $min && microtime(true) < $deadline) { usleep(50000); $rows = $read(); }
        }
        foreach ($rows as &$r) {
            $r['id'] = (int)$r['id']; $r['updated_epoch'] = (float)$r['updated_epoch'];
            $r['bag'] = json_decode($r['bag']);
        }
        out(['ok' => true, 'rows' => $rows]);
    }

    if ($op === 'remove') {
        if ($method !== 'DELETE') bad(405, 'remove is DELETE');
        $id = isset($_GET['id']) ? (int)$_GET['id'] : 0;
        if ($id <= 0) bad(400, 'missing id');
        $st = $db->prepare('DELETE FROM Cell WHERE id = ?'); $st->bind_param('i', $id); $st->execute();
        out(['ok' => true, 'removed' => $st->affected_rows]);
    }

    bad(400, 'unknown op: ' . $op);
} catch (Throwable $e) { bad(500, 'memory error: ' . $e->getMessage()); }
