<?php
require_once __DIR__ . '/config.php';
header('Content-Type: application/json');

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
    'fields' => ['SensorID','FrogID','SensorAddress','SensorNetwork','SensorName','SensorType','Tags']
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
    if ($raw === false || $raw === '') return [];
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
    $Tags          = $getv('Tags');            // optional

    // Derive SensorNetwork from SensorAddress if not provided
    if (($SensorNetwork === null || $SensorNetwork === '') && $SensorAddress) {
        // Replace last octet with .1 for IPv4
        if (preg_match('/^\s*([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.[0-9]{1,3}\s*$/', $SensorAddress, $m)) {
            $a = array_map('intval', [$m[1], $m[2], $m[3]]);
            // Basic range clamp 0..255
            if ($a[0] >= 0 && $a[0] <= 255 && $a[1] >= 0 && $a[1] <= 255 && $a[2] >= 0 && $a[2] <= 255) {
                $SensorNetwork = sprintf('%d.%d.%d.1', $a[0], $a[1], $a[2]);
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
            // upsert_by_name: lookup by SensorName alone (globally unique)
            if (!$SensorName) bad(400, 'Missing SensorName for upsert_by_name');

            $sql = "SELECT SensorID, FrogID FROM Sensor WHERE SensorName = ? LIMIT 1";
            $stmt = exec_stmt($sql, 's', [$SensorName]);
            $rows = fetch_all($stmt);
            $SensorID = $rows[0]['SensorID'] ?? null;
            // If caller didn't supply FrogID, use the one already on the Sensor row
            if (!$FrogID && isset($rows[0]['FrogID'])) $FrogID = $rows[0]['FrogID'];

            if (!$SensorID) {
                // Create new Sensor — generate FrogID if not supplied
                if (!$FrogID) {
                    // UUID v4
                    $FrogID = sprintf('%04x%04x-%04x-%04x-%04x-%04x%04x%04x',
                        mt_rand(0,0xffff), mt_rand(0,0xffff),
                        mt_rand(0,0xffff),
                        mt_rand(0,0x0fff) | 0x4000,
                        mt_rand(0,0x3fff) | 0x8000,
                        mt_rand(0,0xffff), mt_rand(0,0xffff), mt_rand(0,0xffff));
                }

                $cols = ['FrogID','SensorName'];
                $vals = [$FrogID,$SensorName];
                $types= 'ss';

                if ($SensorType    !== null && $SensorType    !== '') { $cols[]='SensorType';    $vals[]=$SensorType;    $types.='s'; }
                if ($SensorAddress !== null && $SensorAddress !== '') { $cols[]='SensorAddress'; $vals[]=$SensorAddress; $types.='s'; }
                if ($SensorNetwork !== null && $SensorNetwork !== '') { $cols[]='SensorNetwork'; $vals[]=$SensorNetwork; $types.='s'; }
                if ($Tags          !== null && $Tags          !== '') { $cols[]='Tags';          $vals[]=$Tags;          $types.='s'; }

                $colList = implode(',', $cols);
                $ph      = implode(',', array_fill(0, count($cols), '?'));
                $sqlIns  = "INSERT INTO Sensor ($colList) VALUES ($ph)";
                $stmt2   = exec_stmt($sqlIns, $types, $vals);
                $SensorID = db()->insert_id;
                $stmt2->close();

                if (!$SensorID) bad(500, 'Failed to create Sensor');
            } else {
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
            // Standard upsert: require FrogID + SensorName to resolve or create Sensor
            if (!$FrogID || !$SensorName) bad(400, 'Missing SensorID or (FrogID & SensorName)');

            // Lookup existing sensor (FrogID + SensorName)
            $sql = "SELECT SensorID FROM Sensor WHERE FrogID = ? AND SensorName = ? LIMIT 1";
            $stmt = exec_stmt($sql, 'ss', [$FrogID, $SensorName]);
            $rows = fetch_all($stmt);
            $SensorID = $rows[0]['SensorID'] ?? null;

            if (!$SensorID) {
                // Create new Sensor
                $cols = ['FrogID','SensorName'];
                $vals = [$FrogID,$SensorName];
                $types= 'ss';

                if ($SensorType    !== null && $SensorType    !== '') { $cols[]='SensorType';    $vals[]=$SensorType;    $types.='s'; }
                if ($SensorAddress !== null && $SensorAddress !== '') { $cols[]='SensorAddress'; $vals[]=$SensorAddress; $types.='s'; }
                if ($SensorNetwork !== null && $SensorNetwork !== '') { $cols[]='SensorNetwork'; $vals[]=$SensorNetwork; $types.='s'; }
                if ($Tags          !== null && $Tags          !== '') { $cols[]='Tags';          $vals[]=$Tags;          $types.='s'; }

                $colList = implode(',', $cols);
                $ph      = implode(',', array_fill(0, count($cols), '?'));
                $sqlIns  = "INSERT INTO Sensor ($colList) VALUES ($ph)";
                $stmt2   = exec_stmt($sqlIns, $types, $vals);
                $SensorID = db()->insert_id;
                $stmt2->close();

                if (!$SensorID) bad(500, 'Failed to create Sensor');
            } else {
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
              ON DUPLICATE KEY UPDATE FrogID = VALUES(FrogID), jsonData = VALUES(jsonData)";
    $stmt = exec_stmt($sqlUD, 'iss', [$SensorID, $FrogID ?: '', $jsonData]);
    $stmt->close();

    echo json_encode(['ok'=>true, 'sensor_id'=>$SensorID]); exit;
}

// ---- sensors / values -------------------------------------------------------
if ($entity === 'sensors' && $action === 'values') {
    if ($method !== 'GET') bad(405, 'Use GET for sensors/values');

    // Allowed filter columns on Sensor
    $allow = ['SensorID','FrogID','SensorAddress','SensorNetwork','SensorName','SensorType','Tags'];

    // Build WHERE from query params; supports __like suffix (e.g. SensorName__like=GPS%)
    $filters = $_GET;
    unset($filters['entity'],$filters['action'],$filters['order'],$filters['limit'],$filters['parse']);
    [$where,$params,$types] = build_where_and_params($filters, $allow);

    // ORDER BY & LIMIT (sanity caps)
    $order    = $_GET['order'] ?? null;
    $orderSql = ($order && in_array($order, $allow, true)) ? " ORDER BY $order" : "";
    $limitSql = '';
    if (isset($_GET['limit'])) {
        $limit = (int)$_GET['limit'];
        if ($limit > 0 && $limit <= 5000) $limitSql = " LIMIT $limit";
    }

    // Join Sensor <- SensorData (last value per SensorID is the only row in your schema)
    $sql = "SELECT s.SensorID, s.FrogID, s.SensorAddress, s.SensorNetwork, s.SensorName, s.SensorType, s.Tags,
                   d.jsonData
              FROM Sensor s
         LEFT JOIN SensorData d ON d.SensorID = s.SensorID
            {$where}{$orderSql}{$limitSql}";
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


bad(400, 'Unknown action');
