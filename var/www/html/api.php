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
require_once __DIR__ . '/config.php';
header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');
header("Access-Control-Allow-Methods: GET, POST, OPTIONS");
header("Access-Control-Allow-Headers: Content-Type");

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

$ENTITIES = [
  'users' => [
    'table'  => '`User`',
    'pk'     => ['CallSign'],
    'fields' => ['CallSign','RealName','Password']
  ],
  'teams' => [
    'table'  => 'Team',
    'pk'     => ['TeamName'],
    'fields' => ['TeamName','CreatedDate','TeamOwner']
  ],
  'team_members' => [
    'table'  => 'TeamMember',
    'pk'     => ['TeamName','TeamUser'],
    'fields' => ['TeamName','TeamUser']
  ],
  'messages' => [
    'table'  => 'Message',
    'pk'     => ['messageID'],
    'fields' => ['messageID','FromUser','ToUser','Message','sentDate','readDate']
  ],
  'known_frognets' => [
    'table'  => 'KnownFrogNet',
    'pk'     => ['NetworkName'],
    'fields' => ['NetworkName','IPAddress','FrogID','Tags','LastHeartbeat']
  ],
  'sensors' => [
    'table'  => 'Sensor',
    'pk'     => ['SensorID'],
    'fields' => ['SensorID','FrogID','SensorAddress','SensorNetwork','SensorName','SensorType','SensorLocation','Tags']
  ],
  'iahosts' => [
    'table'  => 'AIHost',
    'pk'     => ['AIHostID'],
    'fields' => ['AIHostID','FrogID','AIAddress','AINetwork','AIName','AIType','Tags']
  ],
  'actuators' => [
    'table'  => 'Actuator',
    'pk'     => ['ActuatorID'],
    'fields' => ['ActuatorID','FrogID','ActuatorAddress','ActuatorNetwork','ActuatorName','ActuatorType','Tags']
  ],
  'well_known_sites' => [
    'table'  => 'WellKnownSite',
    'pk'     => ['SiteID'],
    'fields' => ['SiteID','FrogID','SiteAddress','SiteName','Tags']
  ],
  'sensor_data' => [
    'table'  => 'SensorData',
    'pk'     => ['SensorID'],
    'fields' => ['SensorID','FrogID','jsonData']
  ],
  'history' => [
    'table'  => 'History',
    'pk'     => ['HistoryID'],
    'fields' => ['HistoryID','FrogID','SensorID','SensorName','jsonData','RecordedAt']
  ],
];

function bad($code, $msg, $detail=null) {
    http_response_code($code);
    echo json_encode(['error'=>$msg,'detail'=>$detail]); exit;
}
function read_json_body() {
    // Only attempt JSON parse when Content-Type indicates JSON
    $ctype = $_SERVER['CONTENT_TYPE'] ?? $_SERVER['HTTP_CONTENT_TYPE'] ?? '';
    $isJson = stripos($ctype, 'application/json') !== false
           || stripos($ctype, 'text/json') !== false
           || stripos($ctype, '+json') !== false;

    if (!$isJson) return []; // form-encoded or other → let callers read $_POST / $_GET

    $raw = file_get_contents('php://input');

    // [NO_EMPTY_BODY_FALLBACK_V1] An empty body is NOT an empty request.
    //
    // This returned [], which is indistinguishable from a caller that
    // legitimately sent {}. Every handler downstream then ran with no
    // SensorName, no SensorType, nothing -- and the first thing to notice was
    // "Failed to resolve SensorID" a few hundred lines later, naming the wrong
    // subsystem entirely.
    //
    // The cause worth naming: when PHP cannot write to its temp directory it
    // logs "POST data can't be buffered; all data discarded" at request startup
    // and hands the script an EMPTY body. Apache's error_log has it; the client
    // saw only a 500 about a SensorID. Say it here, where it is still true.
    if ($raw === false || $raw === '') {
        if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET') {
            $clen = $_SERVER['CONTENT_LENGTH'] ?? '(absent)';
            bad(400, 'Empty request body',
                "Content-Type declares JSON and Content-Length is $clen, but "
                . "php://input was empty. If Content-Length is non-zero the body "
                . "was discarded before this script ran: check the Apache "
                . "error_log for \"POST data can't be buffered\" and verify PHP "
                . "can write to " . sys_get_temp_dir() . " (systemd PrivateTmp "
                . "gives Apache a private one).");
        }
        return [];
    }
    $j = json_decode($raw, true);
    if ($j === null && json_last_error() !== JSON_ERROR_NONE) {
        bad(400, 'Invalid JSON body', json_last_error_msg());
    }
    return $j;
}

function whitelisted($keys, $allowList) {
    return array_values(array_intersect($keys, $allowList));
}
function str_ends_with_php($haystack, $needle) {
    $len = strlen($needle);
    if ($len === 0) return true;
    return (substr($haystack, -$len) === $needle);
}
function build_where_and_params($filters, $allowList) {
    $keys = array_keys($filters);
    $keys = whitelisted($keys, array_merge($allowList, array_map(fn($c)=>$c.'__like', $allowList)));
    $clauses = [];
    $params  = [];
    $types   = '';
    foreach ($keys as $k) {
        if (str_ends_with_php($k, '__like')) {
            $col = substr($k, 0, -6);
            if (!in_array($col, $allowList, true)) continue;
            $clauses[] = "$col LIKE ?";
            $params[]  = $filters[$k];
            $types    .= 's';
        } else {
            $clauses[] = "$k = ?";
            $params[]  = $filters[$k];
            $types    .= 's';
        }
    }
    $where = $clauses ? (' WHERE ' . implode(' AND ', $clauses)) : '';
    return [$where, $params, $types];
}
function exec_stmt($sql, $types='', $params=[]) {
    $mysqli = db();
    $stmt = $mysqli->prepare($sql);
    if (!$stmt) bad(500, 'DB prepare failed', $mysqli->error);
    if ($types !== '' && count($params) > 0) {
        $stmt->bind_param($types, ...$params);
    }
    try {
      if (!$stmt->execute()) {
            $err = $stmt->error; $stmt->close();
            bad(500, 'DB execute failed', $err);
        }
    }
    catch(Exception $ex) {
        $err = $stmt->error; $stmt->close();
        bad(500, 'DB execute failed', $err);
    }
    return $stmt;
}
function fetch_all($stmt) {
    $res = $stmt->get_result();
    $out = $res ? $res->fetch_all(MYSQLI_ASSOC) : [];
    $stmt->close();
    return $out;
}

$entity = $_GET['entity'] ?? null;
$action = $_GET['action'] ?? null;
if (!$entity || !$action) bad(400, 'Missing entity or action');
if (!isset($ENTITIES[$entity])) bad(404, 'Unknown entity');

$meta   = $ENTITIES[$entity];
$table  = $meta['table'];
$pk     = $meta['pk'];
$fields = $meta['fields'];

$method = $_SERVER['REQUEST_METHOD'];
$body   = read_json_body();

// jsonData may arrive as a nested object (from semantic diff transport)
// or as a pre-stringified JSON string.  The DB column is TEXT, so normalize
// to a JSON string before the generic create/update handlers bind it as 's'.
if (isset($body['jsonData']) && (is_array($body['jsonData']) || is_object($body['jsonData']))) {
    $body['jsonData'] = json_encode($body['jsonData'], JSON_UNESCAPED_SLASHES);
}

if ($action === 'create') {
    if ($method !== 'POST') bad(405, 'Use POST for create');
    if ($entity === 'messages' && empty($body['sentDate'])) $body['sentDate'] = date('Y-m-d H:i:s');
    if ($entity === 'teams' && empty($body['CreatedDate'])) $body['CreatedDate'] = date('Y-m-d H:i:s');

    $insertCols = whitelisted(array_keys($body), $fields);
    if (empty($insertCols)) bad(400, 'No valid fields for insert');

    $placeholders = implode(',', array_fill(0, count($insertCols), '?'));
    $colList      = implode(',', $insertCols);
    $types        = str_repeat('s', count($insertCols));
    $vals         = array_map(fn($k)=>$body[$k], $insertCols);

    // [IDEMPOTENT_CREATE_V1] Periodic emitters (peer telemetry, host
    // registration) hammer create on every cycle, racing against rows
    // that already exist.  The plain INSERT below 500s with a Duplicate
    // entry error every time.  For the two entities the logs flagged
    // (sensors, known_frognets) make create idempotent: on conflict,
    // return the existing row.  Other entities keep strict-create
    // semantics — POST means "expect a new row" and conflict is a real
    // error the caller should handle.
    if ($entity === 'sensors') {
        // Auto-increment SensorID PK + UNIQUE idx_sensor_name.  Use the
        // LAST_INSERT_ID(SensorID) trick from upsert_by_name so
        // $mysqli->insert_id returns the existing row's SensorID on
        // conflict — same shape as the success path.
        // [SENSOR_META_BACKFILL_V1] On conflict, also backfill the metadata the
        // caller supplied, so a row first created blank (or by the data-upsert
        // auto-create) gets SensorAddress/SensorNetwork/SensorLocation populated on
        // the next create cycle.  IF(VALUES(col)='', col, VALUES(col)) keeps the old
        // value when the caller sent blank and takes the caller's value otherwise —
        // no wipe, no churn.  Previously only SensorID was updated, so those columns
        // never landed once the row existed (daemon/proxy telemetry sensors).  Only
        // reference columns actually in this INSERT, or VALUES() would error.
        $dupSet = 'SensorID = LAST_INSERT_ID(SensorID)';
        foreach (['SensorAddress','SensorNetwork','SensorLocation','SensorType','Tags'] as $mc) {
            if (in_array($mc, $insertCols, true)) {
                $dupSet .= ", $mc = IF(VALUES($mc)='', $mc, VALUES($mc))";
            }
        }
        $sql = "INSERT INTO $table ($colList) VALUES ($placeholders)
                ON DUPLICATE KEY UPDATE $dupSet";
        $stmt = exec_stmt($sql, $types, $vals);
        $resolvedId = db()->insert_id;
        $stmt->close();
        if ($resolvedId) {
            $sql2  = "SELECT * FROM $table WHERE SensorID = ? LIMIT 1";
            $stmt2 = exec_stmt($sql2, 'i', [$resolvedId]);
            echo json_encode(['ok'=>true,'row'=>fetch_all($stmt2)[0] ?? null]); exit;
        }
        echo json_encode(['ok'=>true,'insert_id'=>$resolvedId]); exit;
    }

    if ($entity === 'known_frognets') {
        // Natural PK NetworkName, no auto-increment.  Conflict key IS
        // the value the caller supplied, so on conflict we just SELECT
        // by NetworkName.  Don't blindly overwrite mutable fields with
        // whatever the caller sent — only update columns the caller
        // actually supplied.
        $netName = $body['NetworkName'] ?? null;
        if ($netName === null || $netName === '') bad(400, 'Missing NetworkName for known_frognets create');

        $updCols = array_values(array_filter($insertCols, fn($c) => $c !== 'NetworkName'));
        if (!empty($updCols)) {
            $setSql = implode(',', array_map(fn($c) => "$c = VALUES($c)", $updCols));
            $sql = "INSERT INTO $table ($colList) VALUES ($placeholders)
                    ON DUPLICATE KEY UPDATE $setSql";
        } else {
            // No non-PK fields supplied → make conflict a true no-op
            $sql = "INSERT INTO $table ($colList) VALUES ($placeholders)
                    ON DUPLICATE KEY UPDATE NetworkName = NetworkName";
        }
        $stmt = exec_stmt($sql, $types, $vals);
        $stmt->close();

        $sql2  = "SELECT * FROM $table WHERE NetworkName = ? LIMIT 1";
        $stmt2 = exec_stmt($sql2, 's', [$netName]);
        echo json_encode(['ok'=>true,'row'=>fetch_all($stmt2)[0] ?? null]); exit;
    }

    if ($entity === 'sensor_data') {
        // [IDEMPOTENT_CREATE_V2] Peer emitters (notably Seattle4's
        // SemanticCache.Endpoints sensor) call create on every cycle for
        // a SensorID that already has a SensorData row, producing one
        // 'Duplicate entry NNNN for key PRIMARY' 500 every 30s per peer.
        // SensorData.SensorID is the PK; on conflict, update mutable
        // fields (FrogID, jsonData) so behavior matches what an emitter
        // intends — replace the row's content — without the 500.
        $sensorId = $body['SensorID'] ?? null;
        if ($sensorId === null || $sensorId === '') bad(400, 'Missing SensorID for sensor_data create');

        $updCols = array_values(array_filter($insertCols, fn($c) => $c !== 'SensorID'));
        if (!empty($updCols)) {
            $setSql = implode(',', array_map(fn($c) => "$c = VALUES($c)", $updCols));
            $sql = "INSERT INTO $table ($colList) VALUES ($placeholders)
                    ON DUPLICATE KEY UPDATE $setSql";
        } else {
            // No non-PK fields supplied → make conflict a true no-op
            $sql = "INSERT INTO $table ($colList) VALUES ($placeholders)
                    ON DUPLICATE KEY UPDATE SensorID = SensorID";
        }
        $stmt = exec_stmt($sql, $types, $vals);
        $stmt->close();

        $sql2  = "SELECT * FROM $table WHERE SensorID = ? LIMIT 1";
        $stmt2 = exec_stmt($sql2, 'i', [$sensorId]);
        echo json_encode(['ok'=>true,'row'=>fetch_all($stmt2)[0] ?? null]); exit;
    }

    // Original strict-create path for all other entities.
    $sql = "INSERT INTO $table ($colList) VALUES ($placeholders)";
    $stmt = exec_stmt($sql, $types, $vals);
    $lastId = db()->insert_id;
    $stmt->close();

    if (count($pk) === 1) {
        $k = $pk[0];
        $keyVal = $body[$k] ?? ($lastId ?: null);
        if ($keyVal !== null) {
            $sql = "SELECT * FROM $table WHERE $k = ?";
            $stmt = exec_stmt($sql, 's', [$keyVal]);
            echo json_encode(['ok'=>true,'row'=>fetch_all($stmt)[0] ?? null]); exit;
        }
    }
    echo json_encode(['ok'=>true,'insert_id'=>$lastId]); exit;
}

if ($action === 'list') {
    if ($method !== 'GET') bad(405, 'Use GET for list');
    $filters = $_GET;
    unset($filters['entity'],$filters['action']);

    [$where,$params,$types] = build_where_and_params($filters, $fields);
    $order = $_GET['order'] ?? null;
    $orderSql = '';
    if ($order && in_array($order, $fields, true)) $orderSql = " ORDER BY $order";

    // [API_LIMIT_HONOURED_V1] limit was commented out, so every caller asking for one
    // row got the ENTIRE matching set and threw the rest away. The monitor sends
    // &limit=1 five times per poll; on a Sensor table this size that is a full scan
    // and a full JSON encode per request, per poll, per node. The (int) cast is what
    // makes it injection-safe - the value never reaches SQL as text.
    $limitSql = '';
    if (isset($_GET['limit'])) {
        $limit = (int)$_GET['limit'];
        if ($limit > 0 && $limit <= 1000) $limitSql = " LIMIT $limit";
    }

    $sql = "SELECT * FROM $table{$where}{$orderSql}{$limitSql}";
    $stmt = exec_stmt($sql, $types, $params);
    echo json_encode(['ok'=>true,'rows'=>fetch_all($stmt)]); exit;
}

if ($action === 'get') {
    if ($method !== 'GET') bad(405, 'Use GET for get');
    $params=[]; $types=''; $clauses=[];
    foreach ($pk as $k) {
        if (!isset($_GET[$k])) bad(400, "Missing PK: $k");
        $clauses[] = "$k = ?";
        $params[]  = $_GET[$k];
        $types    .= 's';
    }
    $sql = "SELECT * FROM $table WHERE ".implode(' AND ', $clauses)." LIMIT 1";
    $stmt = exec_stmt($sql, $types, $params);
    $rows = fetch_all($stmt);
    echo json_encode(['ok'=>true,'row'=>$rows[0] ?? null]); exit;
}

if ($action === 'update') {
    if ($method !== 'PUT' && $method !== 'POST') bad(405, 'Use PUT/POST for update');

    $whereParams=[]; $whereTypes=''; $whereClauses=[];
    foreach ($pk as $k) {
        $val = $body[$k] ?? ($_GET[$k] ?? null);
        if ($val === null || $val === '') bad(400, "Missing PK: $k");
        $whereClauses[] = "$k = ?";
        $whereParams[]  = $val;
        $whereTypes    .= 's';
        unset($body[$k]);
    }
    $updCols = whitelisted(array_keys($body), $fields);
    if (empty($updCols)) bad(400, 'No updatable fields supplied');

    $set = [];
    $setParams=[]; $setTypes='';
    foreach ($updCols as $c) {
        $set[] = "$c = ?";
        $setParams[] = $body[$c];
        $setTypes   .= 's';
    }

    $sql = "UPDATE $table SET ".implode(',', $set)." WHERE ".implode(' AND ', $whereClauses);
    $stmt = exec_stmt($sql, $setTypes.$whereTypes, array_merge($setParams, $whereParams));
    $affected = $stmt->affected_rows;
    $stmt->close();

    echo json_encode(['ok'=>true,'affected'=>$affected]); exit;
}

if ($action === 'delete') {
    if ($method !== 'DELETE' && $method !== 'POST') bad(405, 'Use DELETE/POST for delete');

    $params=[]; $types=''; $clauses=[];
    foreach ($pk as $k) {
        $val = $_GET[$k] ?? ($body[$k] ?? null);
        if ($val === null) bad(400, "Missing PK: $k");
        $clauses[] = "$k = ?";
        $params[]  = $val;
        $types    .= 's';
    }
    $sql = "DELETE FROM $table WHERE ".implode(' AND ',$clauses)." LIMIT 1";
    $stmt = exec_stmt($sql, $types, $params);
    $affected = $stmt->affected_rows;
    $stmt->close();

    echo json_encode(['ok'=>true,'affected'=>$affected]); exit;
}

// ---- sensor_data / upsert (also handles upsert_by_name) --------------------
// ---- sensor_data / upsert ---------------------------------------------------
if ($entity === 'sensor_data' && ($action === 'upsert' || $action === 'upsert_by_name')) {
    if ($method !== 'POST' && $method !== 'PUT') bad(405, 'Use POST/PUT for upsert');

    // Prefer JSON body; fall back to form
    $getv = function($k, $default=null) use ($body) {
        if (array_key_exists($k, $body)) return $body[$k];
        if (isset($_POST[$k])) return $_POST[$k];
        if (isset($_GET[$k]))  return $_GET[$k];
        return $default;
    };

    $FrogID        = $getv('FrogID');
    $SensorID      = $getv('SensorID');        // optional
    $SensorName    = $getv('SensorName');      // for lookup/create when no SensorID
    $SensorType    = $getv('SensorType');      // optional, stored in Sensor
    $SensorAddress = $getv('SensorAddress');   // optional, stored in Sensor
    $SensorNetwork = $getv('SensorNetwork');   // optional, computed from address if missing
    $SensorLocation = $getv('SensorLocation'); // 3rd tuple dimension
    $Tags          = $getv('Tags');            // optional

    // Derive SensorNetwork from SensorAddress if not provided
    if (($SensorNetwork === null || $SensorNetwork === '') && $SensorAddress) {
        // Replace last octet with .1 for IPv4
        if (preg_match('/^\s*([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.[0-9]{1,3}\s*$/', $SensorAddress, $m)) {
            $a = array_map('intval', [$m[1], $m[2], $m[3]]);
            // Basic range clamp 0..255
            if ($a[0] >= 0 && $a[0] <= 255 && $a[1] >= 0 && $a[1] <= 255 && $a[2] >= 0 && $a[2] <= 255) {
                $SensorNetwork = sprintf('%d.%d.%d.0/24', $a[0], $a[1], $a[2]);
            }
        }
    }

    // jsonData may be object (preferred) or string; normalize to string for DB
    $jsonDataRaw = $getv('jsonData', $getv('data_str'));
    if (is_array($jsonDataRaw) || is_object($jsonDataRaw)) {
        $jsonData = json_encode($jsonDataRaw, JSON_UNESCAPED_SLASHES);
    } else if (is_string($jsonDataRaw) && strlen(trim($jsonDataRaw)) > 0) {
        $tmp = json_decode($jsonDataRaw, true);
        if ($tmp === null && json_last_error() !== JSON_ERROR_NONE) {
            bad(400, 'Invalid jsonData (not JSON)', json_last_error_msg());
        }
        $jsonData = $jsonDataRaw;
    } else {
        $jsonData = '{}';
    }

    if (!$SensorID) {
        if ($action === 'upsert_by_name') {
            // [ATOMIC_UPSERT_V1] upsert_by_name: atomic resolve-or-create.
            // Previous code did SELECT then INSERT which raced under
            // concurrent writes from multiple peers, producing
            // "Duplicate entry for key 'idx_sensor_name'" 500s.  The
            // INSERT ... ON DUPLICATE KEY UPDATE below is atomic: on
            // conflict LAST_INSERT_ID(SensorID) returns the existing
            // SensorID, so $mysqli->insert_id works either way.
            if (!$SensorName) bad(400, 'Missing SensorName for upsert_by_name');

            // Generate FrogID if caller didn't supply one.  If the row
            // already exists, our FrogID is discarded by the UPDATE
            // clause (we only set SensorID = LAST_INSERT_ID(SensorID)).
            if (!$FrogID) {
                $FrogID = sprintf('%04x%04x-%04x-%04x-%04x-%04x%04x%04x',
                    mt_rand(0,0xffff), mt_rand(0,0xffff),
                    mt_rand(0,0xffff),
                    mt_rand(0,0x0fff) | 0x4000,
                    mt_rand(0,0x3fff) | 0x8000,
                    mt_rand(0,0xffff), mt_rand(0,0xffff), mt_rand(0,0xffff));
            }

            // [TUPLE_ADDRESS_V1] The address is Name + Type + Address, and the
            // ON DUPLICATE KEY below can only fire if all three are in the INSERT --
            // that is the unique key. They are therefore ALWAYS supplied, empty
            // string where the caller gave nothing, never conditionally omitted: a
            // missing column means the key cannot match and every write becomes a
            // new row again, which is the bug this whole change exists to kill.
            $cols = ['FrogID','SensorName','SensorType','SensorAddress'];
            $vals = [$FrogID,$SensorName,
                     ($SensorType    === null ? '' : $SensorType),
                     ($SensorAddress === null ? '' : $SensorAddress)];
            $types= 'ssss';

            // SensorAddress is a KEY column and is supplied unconditionally above --
            // it must not also be appended here or the INSERT names it twice.
            if ($SensorNetwork  !== null && $SensorNetwork  !== '') { $cols[]='SensorNetwork';  $vals[]=$SensorNetwork;  $types.='s'; }
            if ($SensorLocation !== null && $SensorLocation !== '') { $cols[]='SensorLocation'; $vals[]=$SensorLocation; $types.='s'; }
            if ($Tags          !== null && $Tags          !== '') { $cols[]='Tags';          $vals[]=$Tags;          $types.='s'; }

            $colList = implode(',', $cols);
            $ph      = implode(',', array_fill(0, count($cols), '?'));
            // [SENSOR_META_BACKFILL_V1] Same rule as the create handler: on conflict,
            // backfill the metadata the caller supplied. Updating only SensorID meant a
            // Sensor row auto-created blank by an earlier data-upsert kept empty
            // SensorAddress/SensorNetwork forever - which is exactly why per-host
            // sensor lookups by address returned nothing. Only reference columns that
            // are actually in this INSERT, or VALUES() errors.
            $dupSet2 = 'SensorID = LAST_INSERT_ID(SensorID)';
            // The three coordinates (Name/Type/Address) are the KEY: never
            // rewritten on conflict, or the row would move to a different address.
            foreach (['SensorNetwork','SensorLocation','Tags'] as $mc) {
                if (in_array($mc, $cols, true)) {
                    $dupSet2 .= ", $mc = IF(VALUES($mc)='', $mc, VALUES($mc))";
                }
            }
            $sqlIns  = "INSERT INTO Sensor ($colList) VALUES ($ph)
                        ON DUPLICATE KEY UPDATE $dupSet2";
            $stmt2   = exec_stmt($sqlIns, $types, $vals);
            $SensorID  = db()->insert_id;
            $mysqlErr  = db()->error;
            $affected  = db()->affected_rows;
            $stmt2->close();

            // [SAY_WHAT_FAILED_V1] bad() takes a detail and this passed none, so
            // one message covered four different causes -- two of which are not
            // schema problems at all:
            //   * an empty SensorName, because the request body was discarded
            //     upstream (see [NO_EMPTY_BODY_FALLBACK_V1])
            //   * insert_id == 0 from ON DUPLICATE KEY UPDATE when the row
            //     already existed and nothing changed. That is a successful
            //     no-op, and reading it as failure is a bug: recover the id.
            if (!$SensorID && $mysqlErr === '' && $SensorName !== null
                && $SensorName !== '') {
                $q = exec_stmt("SELECT SensorID FROM Sensor WHERE SensorName=? LIMIT 1",
                               "s", [$SensorName]);
                $rr = $q->get_result()->fetch_assoc();
                $q->close();
                if ($rr && !empty($rr['SensorID'])) $SensorID = (int)$rr['SensorID'];
            }

            if (!$SensorID) {
                bad(500, 'Failed to resolve SensorID',
                    'SensorName='    . var_export($SensorName, true)
                    . ' SensorType='    . var_export($SensorType, true)
                    . ' SensorAddress=' . var_export($SensorAddress, true)
                    . ' insert_id=0 affected_rows=' . var_export($affected, true)
                    . ' mysql_error='   . var_export($mysqlErr, true)
                    . (($SensorName === null || $SensorName === '')
                        ? ' -- SensorName is EMPTY, so the request carried no '
                          . 'usable body. This is not a schema fault. See the '
                          . 'Apache error_log.'
                        : ''));
            }

            // Fetch the (possibly pre-existing) FrogID back so downstream
            // SensorData INSERT uses the right one.
            $sqlF = "SELECT FrogID FROM Sensor WHERE SensorID = ? LIMIT 1";
            $stmtF = exec_stmt($sqlF, 'i', [$SensorID]);
            $rowsF = fetch_all($stmtF);
            if (!empty($rowsF) && !empty($rowsF[0]['FrogID'])) {
                $FrogID = $rowsF[0]['FrogID'];
            }

            // Update mutable columns only if provided and changed.
            // (Skipped for brand-new rows: NEW.column == our value, so UPDATE affects 0.)
            if (true) {
                // Update address/network/type/tags if provided and changed
                if ($SensorAddress !== null && $SensorAddress !== '') {
                    $sqlU = "UPDATE Sensor SET SensorAddress = ? WHERE SensorID = ? AND (SensorAddress IS NULL OR SensorAddress = '' OR SensorAddress <> ?)";
                    $stmtU = exec_stmt($sqlU, 'sis', [$SensorAddress, $SensorID, $SensorAddress]); $stmtU->close();
                }
                if ($SensorNetwork !== null && $SensorNetwork !== '') {
                    $sqlU = "UPDATE Sensor SET SensorNetwork = ? WHERE SensorID = ? AND (SensorNetwork IS NULL OR SensorNetwork = '' OR SensorNetwork <> ?)";
                    $stmtU = exec_stmt($sqlU, 'sis', [$SensorNetwork, $SensorID, $SensorNetwork]); $stmtU->close();
                }
                if ($SensorType !== null && $SensorType !== '') {
                    $sqlU = "UPDATE Sensor SET SensorType = ? WHERE SensorID = ? AND (SensorType IS NULL OR SensorType = '' OR SensorType <> ?)";
                    $stmtU = exec_stmt($sqlU, 'sis', [$SensorType, $SensorID, $SensorType]); $stmtU->close();
                }
                if ($Tags !== null && $Tags !== '') {
                    $sqlU = "UPDATE Sensor SET Tags = ? WHERE SensorID = ? AND (Tags IS NULL OR Tags = '' OR Tags <> ?)";
                    $stmtU = exec_stmt($sqlU, 'sis', [$Tags, $SensorID, $Tags]); $stmtU->close();
                }
            }
        } else {
            // [ATOMIC_UPSERT_V1] Standard upsert: atomic resolve-or-create.
            // Same race as upsert_by_name, same fix.
            if (!$FrogID || !$SensorName) bad(400, 'Missing SensorID or (FrogID & SensorName)');

            // [TUPLE_ADDRESS_V1] The address is Name + Type + Address, and the
            // ON DUPLICATE KEY below can only fire if all three are in the INSERT --
            // that is the unique key. They are therefore ALWAYS supplied, empty
            // string where the caller gave nothing, never conditionally omitted: a
            // missing column means the key cannot match and every write becomes a
            // new row again, which is the bug this whole change exists to kill.
            $cols = ['FrogID','SensorName','SensorType','SensorAddress'];
            $vals = [$FrogID,$SensorName,
                     ($SensorType    === null ? '' : $SensorType),
                     ($SensorAddress === null ? '' : $SensorAddress)];
            $types= 'ssss';

            // SensorAddress is a KEY column and is supplied unconditionally above --
            // it must not also be appended here or the INSERT names it twice.
            if ($SensorNetwork  !== null && $SensorNetwork  !== '') { $cols[]='SensorNetwork';  $vals[]=$SensorNetwork;  $types.='s'; }
            if ($SensorLocation !== null && $SensorLocation !== '') { $cols[]='SensorLocation'; $vals[]=$SensorLocation; $types.='s'; }
            if ($Tags          !== null && $Tags          !== '') { $cols[]='Tags';          $vals[]=$Tags;          $types.='s'; }

            $colList = implode(',', $cols);
            $ph      = implode(',', array_fill(0, count($cols), '?'));
            // [SENSOR_META_BACKFILL_V1] Same rule as the create handler: on conflict,
            // backfill the metadata the caller supplied. Updating only SensorID meant a
            // Sensor row auto-created blank by an earlier data-upsert kept empty
            // SensorAddress/SensorNetwork forever - which is exactly why per-host
            // sensor lookups by address returned nothing. Only reference columns that
            // are actually in this INSERT, or VALUES() errors.
            $dupSet2 = 'SensorID = LAST_INSERT_ID(SensorID)';
            // The three coordinates (Name/Type/Address) are the KEY: never
            // rewritten on conflict, or the row would move to a different address.
            foreach (['SensorNetwork','SensorLocation','Tags'] as $mc) {
                if (in_array($mc, $cols, true)) {
                    $dupSet2 .= ", $mc = IF(VALUES($mc)='', $mc, VALUES($mc))";
                }
            }
            $sqlIns  = "INSERT INTO Sensor ($colList) VALUES ($ph)
                        ON DUPLICATE KEY UPDATE $dupSet2";
            $stmt2   = exec_stmt($sqlIns, $types, $vals);
            $SensorID  = db()->insert_id;
            $mysqlErr  = db()->error;
            $affected  = db()->affected_rows;
            $stmt2->close();

            // [SAY_WHAT_FAILED_V1] bad() takes a detail and this passed none, so
            // one message covered four different causes -- two of which are not
            // schema problems at all:
            //   * an empty SensorName, because the request body was discarded
            //     upstream (see [NO_EMPTY_BODY_FALLBACK_V1])
            //   * insert_id == 0 from ON DUPLICATE KEY UPDATE when the row
            //     already existed and nothing changed. That is a successful
            //     no-op, and reading it as failure is a bug: recover the id.
            if (!$SensorID && $mysqlErr === '' && $SensorName !== null
                && $SensorName !== '') {
                $q = exec_stmt("SELECT SensorID FROM Sensor WHERE SensorName=? LIMIT 1",
                               "s", [$SensorName]);
                $rr = $q->get_result()->fetch_assoc();
                $q->close();
                if ($rr && !empty($rr['SensorID'])) $SensorID = (int)$rr['SensorID'];
            }

            if (!$SensorID) {
                bad(500, 'Failed to resolve SensorID',
                    'SensorName='    . var_export($SensorName, true)
                    . ' SensorType='    . var_export($SensorType, true)
                    . ' SensorAddress=' . var_export($SensorAddress, true)
                    . ' insert_id=0 affected_rows=' . var_export($affected, true)
                    . ' mysql_error='   . var_export($mysqlErr, true)
                    . (($SensorName === null || $SensorName === '')
                        ? ' -- SensorName is EMPTY, so the request carried no '
                          . 'usable body. This is not a schema fault. See the '
                          . 'Apache error_log.'
                        : ''));
            }

            // Update mutable columns only if provided.
            if (true) {
                // Optional: update address/network/type if provided (and changed)
                if ($SensorAddress !== null && $SensorAddress !== '') {
                    $sqlU = "UPDATE Sensor SET SensorAddress = ? WHERE SensorID = ? AND (SensorAddress IS NULL OR SensorAddress = '' OR SensorAddress <> ?)";
                    $stmtU = exec_stmt($sqlU, 'sss', [$SensorAddress, $SensorID, $SensorAddress]); $stmtU->close();
                }
                if ($SensorNetwork !== null && $SensorNetwork !== '') {
                    $sqlU = "UPDATE Sensor SET SensorNetwork = ? WHERE SensorID = ? AND (SensorNetwork IS NULL OR SensorNetwork = '' OR SensorNetwork <> ?)";
                    $stmtU = exec_stmt($sqlU, 'sss', [$SensorNetwork, $SensorID, $SensorNetwork]); $stmtU->close();
                }
                if ($SensorType !== null && $SensorType !== '') {
                    $sqlU = "UPDATE Sensor SET SensorType = ? WHERE SensorID = ? AND (SensorType IS NULL OR SensorType = '' OR SensorType <> ?)";
                    $stmtU = exec_stmt($sqlU, 'sss', [$SensorType, $SensorID, $SensorType]); $stmtU->close();
                }
                if ($Tags !== null && $Tags !== '') {
                    $sqlU = "UPDATE Sensor SET Tags = ? WHERE SensorID = ? AND (Tags IS NULL OR Tags = '' OR Tags <> ?)";
                    $stmtU = exec_stmt($sqlU, 'sss', [$Tags, $SensorID, $Tags]); $stmtU->close();
                }
            }
        }
    }

    // Upsert into SensorData on SensorID
    $sqlUD = "INSERT INTO SensorData (SensorID, FrogID, jsonData)
              VALUES (?, ?, ?)
              ON DUPLICATE KEY UPDATE FrogID = VALUES(FrogID), jsonData = VALUES(jsonData), UpdatedAt = CURRENT_TIMESTAMP";
    $stmt = exec_stmt($sqlUD, 'iss', [$SensorID, $FrogID ?: '', $jsonData]);
    $stmt->close();

    echo json_encode(['ok'=>true, 'sensor_id'=>$SensorID]); exit;
}

// ---- sensors / values -------------------------------------------------------
if ($entity === 'sensors' && $action === 'values') {
    if ($method !== 'GET') bad(405, 'Use GET for sensors/values');

    // Allowed filter columns on Sensor
    $allow = ['SensorID','FrogID','SensorAddress','SensorNetwork','SensorName','SensorType','SensorLocation','Tags'];

    // Build WHERE from query params; supports __like suffix (e.g. SensorName__like=GPS%)
    $filters = $_GET;
    unset($filters['entity'],$filters['action'],$filters['order'],$filters['limit'],$filters['parse']);
    [$where,$params,$types] = build_where_and_params($filters, $allow);

    // ORDER BY & LIMIT (sanity caps)
    $order    = $_GET['order'] ?? null;
    $orderSql = ($order && in_array($order, $allow, true)) ? " ORDER BY $order" : "";
    // [API_LIMIT_HONOURED_V1] see the list handler above. Higher cap here because
    // values is the convergence read and legitimately pulls large sets.
    $limitSql = '';
    if (isset($_GET['limit'])) {
        $limit = (int)$_GET['limit'];
        if ($limit > 0 && $limit <= 5000) $limitSql = " LIMIT $limit";
    }

    // [ENVELOPE_TS_V1] Freshness is ENVELOPE information and it is decided HERE.
    //
    // UpdatedAt is the row's insert/update time, on the database's clock. A payload
    // may carry a ts of its own; it is not considered. The client used to filter on
    // that payload ts, which meant the READER's clock was compared against the
    // WRITER's stamp -- so a node whose clock was two minutes slow was invisible to
    // everyone while publishing perfectly, with no symptom but an empty roster.
    //
    // &fresh_s=N returns only rows written within the last N seconds. One clock, the
    // one that stamped the row, so no amount of skew between nodes can matter.
    $freshSql = '';
    if (isset($_GET['fresh_s'])) {
        $fs = (int)$_GET['fresh_s'];
        if ($fs > 0) {
            $freshSql = ($where === '' ? ' WHERE ' : ' AND ')
                      . 'd.UpdatedAt >= (NOW() - INTERVAL ? SECOND)';
            $types   .= 'i';
            $params[] = $fs;
        }
    }
    $sql = "SELECT s.SensorID, s.FrogID, s.SensorAddress, s.SensorNetwork, s.SensorName, s.SensorType, s.Tags,
                   d.jsonData, d.UpdatedAt, UNIX_TIMESTAMP(d.UpdatedAt) AS UpdatedAtEpoch
              FROM Sensor s
         LEFT JOIN SensorData d ON d.SensorID = s.SensorID
            {$where}{$freshSql}{$orderSql}{$limitSql}";
    $stmt = exec_stmt($sql, $types, $params);
    $rows = fetch_all($stmt);

    // parse=1 → include parsed JSON as 'data'
    $parse = isset($_GET['parse']) && ($_GET['parse']==='1' || $_GET['parse']==='true');
    if ($parse) {
        foreach ($rows as &$r) {
            if (isset($r['jsonData']) && is_string($r['jsonData']) && $r['jsonData'] !== '') {
                $j = json_decode($r['jsonData'], true);
                if (json_last_error() === JSON_ERROR_NONE) $r['data'] = $j;
            }
        }
    }

    echo json_encode(['ok'=>true,'rows'=>$rows]); exit;
}


// ---- sensor_data / upsert_batch --------------------------------------------
// [BATCH_UPSERT_V1] Coalesce many per-sensor upserts into ONE request so a node's
// whole metrics tick is a single round-trip to the database host instead of dozens
// (the per-metric fan-out that was saturating the elected DB host with 503s).
// Body: {"items":[{SensorName, SensorType?, SensorAddress?, jsonData}, ...]}
// Each item is an atomic resolve-or-create + data upsert, all inside one
// transaction. Fire-and-forget on the client side: returns a per-item summary but
// callers need not read it.
if ($entity === 'sensor_data' && $action === 'upsert_batch') {
    if ($method !== 'POST' && $method !== 'PUT') bad(405, 'Use POST/PUT for upsert_batch');
    $items = $body['items'] ?? null;
    if (!is_array($items)) bad(400, 'upsert_batch requires an "items" array');

    $mysqli = db();
    $mysqli->begin_transaction();
    $results = [];
    try {
        // atomic resolve-or-create of a Sensor row by name (mirrors upsert_by_name)
        // [SENSOR_META_BACKFILL_V1] batch path: store SensorNetwork too, derive it
        // from SensorAddress when the caller omits it, and backfill address/network
        // on conflict.  Was (FrogID,SensorName,SensorType,SensorAddress) with only
        // SensorID+SensorType updated on conflict, so daemon/proxy telemetry rows
        // (SemanticDaemon.*, SemanticCache.*) landed with empty address/network.
        $sql_sensor = "INSERT INTO Sensor (FrogID,SensorName,SensorType,SensorAddress,SensorNetwork)
                       VALUES (?,?,?,?,?)
                       ON DUPLICATE KEY UPDATE SensorID = LAST_INSERT_ID(SensorID),
                         SensorType    = IF(VALUES(SensorType)='',    SensorType,    VALUES(SensorType)),
                         SensorAddress = IF(VALUES(SensorAddress)='', SensorAddress, VALUES(SensorAddress)),
                         SensorNetwork = IF(VALUES(SensorNetwork)='', SensorNetwork, VALUES(SensorNetwork))";
        $st_sensor = $mysqli->prepare($sql_sensor);
        if (!$st_sensor) { throw new RuntimeException('prepare sensor: '.$mysqli->error); }

        $sql_data = "INSERT INTO SensorData (SensorID,FrogID,jsonData)
                     VALUES (?,?,?)
                     ON DUPLICATE KEY UPDATE jsonData = VALUES(jsonData)";
        $st_data = $mysqli->prepare($sql_data);
        if (!$st_data) { throw new RuntimeException('prepare data: '.$mysqli->error); }

        foreach ($items as $it) {
            $sn = $it['SensorName'] ?? '';
            if ($sn === '') { $results[] = ['ok'=>false,'error'=>'missing SensorName']; continue; }
            $stype = $it['SensorType'] ?? '';
            $saddr = $it['SensorAddress'] ?? '';
            $snet  = $it['SensorNetwork'] ?? '';
            // [BATCH_SENSORNETWORK_V1] SensorNetwork is NOT NULL with no default;
            // supply it here exactly as the single-upsert path does (derive
            // a.b.c.0/24 from SensorAddress when the item omits it), or the
            // INSERT fails with "Field 'SensorNetwork' doesn't have a default".
            // Octets are RANGE-CHECKED: \d{1,3} alone accepts 999.999.999.1 and
            // would store a network that matches nothing.
            if (($snet === null || $snet === '') && $saddr !== ''
                && preg_match('/^\s*([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.[0-9]{1,3}\s*$/', $saddr, $m)) {
                $a = array_map('intval', [$m[1], $m[2], $m[3]]);
                if ($a[0]>=0 && $a[0]<=255 && $a[1]>=0 && $a[1]<=255 && $a[2]>=0 && $a[2]<=255) {
                    $snet = sprintf('%d.%d.%d.0/24', $a[0], $a[1], $a[2]);
                }
            }
            $fid   = $it['FrogID'] ?? sprintf('%04x%04x-%04x-%04x-%04x-%04x%04x%04x',
                        mt_rand(0,0xffff), mt_rand(0,0xffff), mt_rand(0,0xffff),
                        mt_rand(0,0x0fff)|0x4000, mt_rand(0,0x3fff)|0x8000,
                        mt_rand(0,0xffff), mt_rand(0,0xffff), mt_rand(0,0xffff));
            $jd = $it['jsonData'] ?? '{}';
            if (is_array($jd) || is_object($jd)) $jd = json_encode($jd, JSON_UNESCAPED_SLASHES);

            $st_sensor->bind_param('sssss', $fid, $sn, $stype, $saddr, $snet);
            if (!$st_sensor->execute()) { throw new RuntimeException("sensor upsert ($sn): ".$st_sensor->error); }
            $sid = $mysqli->insert_id;

            $st_data->bind_param('iss', $sid, $fid, $jd);
            if (!$st_data->execute()) { throw new RuntimeException("data upsert ($sn): ".$st_data->error); }
            $results[] = ['ok'=>true,'SensorName'=>$sn,'SensorID'=>$sid];
        }
        $st_sensor->close(); $st_data->close();
        $mysqli->commit();
    } catch (Throwable $e) {
        $mysqli->rollback();
        bad(500, 'upsert_batch failed', $e->getMessage());
    }
    echo json_encode(['ok'=>true,'count'=>count($results),'results'=>$results]); exit;
}

// ---- history / append ------------------------------------------------------
// [HISTORY_APPEND_V1] Append one row to the history table without touching prior
// rows (time-series accumulation). Body: {FrogID?, SensorID?, SensorName?,
// jsonData, RecordedAt?}. RecordedAt defaults to now.
if ($entity === 'history' && $action === 'append') {
    if ($method !== 'POST' && $method !== 'PUT') bad(405, 'Use POST/PUT for append');
    $fid  = $body['FrogID'] ?? null;
    $sid  = $body['SensorID'] ?? null;
    $sn   = $body['SensorName'] ?? null;
    $jd   = $body['jsonData'] ?? '{}';
    if (is_array($jd) || is_object($jd)) $jd = json_encode($jd, JSON_UNESCAPED_SLASHES);
    $rat  = $body['RecordedAt'] ?? date('Y-m-d H:i:s');

    $sql = "INSERT INTO History (FrogID,SensorID,SensorName,jsonData,RecordedAt)
            VALUES (?,?,?,?,?)";
    $stmt = exec_stmt($sql, 'sisss', [$fid, $sid, $sn, $jd, $rat]);
    echo json_encode(['ok'=>true,'insert_id'=>db()->raw()->insert_id ?? null]); exit;
}

// ---- history / write_all ---------------------------------------------------
// [HISTORY_WRITE_ALL_V1] Replace the ENTIRE history table with a supplied set of
// rows as a SINGLE atomic unit — used when a BLDC-1 handler switches databases and
// must transplant its history wholesale. Body: {rows:[{FrogID?,SensorID?,
// SensorName?,jsonData,RecordedAt?}, ...], truncate?:bool(default true)}.
// truncate=true clears existing rows first (full replace); false = bulk append.
if ($entity === 'history' && $action === 'write_all') {
    if ($method !== 'POST' && $method !== 'PUT') bad(405, 'Use POST/PUT for write_all');
    $rows = $body['rows'] ?? null;
    if (!is_array($rows)) bad(400, 'write_all requires a "rows" array');
    $truncate = array_key_exists('truncate', $body) ? (bool)$body['truncate'] : true;

    $mysqli = db();
    $mysqli->begin_transaction();
    try {
        if ($truncate) {
            if (!$mysqli->query('DELETE FROM History')) {
                throw new RuntimeException('clear History: '.$mysqli->error);
            }
        }
        $sql = "INSERT INTO History (FrogID,SensorID,SensorName,jsonData,RecordedAt)
                VALUES (?,?,?,?,?)";
        $stmt = $mysqli->prepare($sql);
        if (!$stmt) { throw new RuntimeException('prepare: '.$mysqli->error); }
        $n = 0;
        foreach ($rows as $r) {
            $fid = $r['FrogID'] ?? null;
            $sid = $r['SensorID'] ?? null;
            $sn  = $r['SensorName'] ?? null;
            $jd  = $r['jsonData'] ?? '{}';
            if (is_array($jd) || is_object($jd)) $jd = json_encode($jd, JSON_UNESCAPED_SLASHES);
            $rat = $r['RecordedAt'] ?? date('Y-m-d H:i:s');
            $stmt->bind_param('sisss', $fid, $sid, $sn, $jd, $rat);
            if (!$stmt->execute()) { throw new RuntimeException("row $n: ".$stmt->error); }
            $n++;
        }
        $stmt->close();
        $mysqli->commit();
    } catch (Throwable $e) {
        $mysqli->rollback();
        bad(500, 'write_all failed', $e->getMessage());
    }
    echo json_encode(['ok'=>true,'written'=>count($rows),'truncated'=>$truncate]); exit;
}


bad(400, 'Unknown action');
