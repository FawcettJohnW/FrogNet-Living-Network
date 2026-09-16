<?php
require_once __DIR__ . '/util.php';

frognet_headers();

$entityKey = frognet_get_param('entity');
$action    = frognet_get_param('action');

if (!$entityKey) frognet_error(400, 'Missing entity param');
if (!$action) {
  $method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
  $action = match ($method) {
    'GET'    => 'list',
    'POST'   => 'create',
    'PUT'    => 'update',
    'DELETE' => 'delete',
    default  => 'list',
  };
}

$entity = frognet_entity($entityKey);
$pdo    = frognet_db();

try {
  switch ($action) {
    case 'list': {
      [$whereSql, $params, $orderBy, $limitSql] = frognet_build_query_parts($entity, $_GET);
      $sql = "SELECT " . implode(',', array_map('frognet_qcol', $entity['columns'])) .
             " FROM " . frognet_qcol($entity['table']) .
             $whereSql . $orderBy . $limitSql;
      $stmt = $pdo->prepare($sql);
      $stmt->execute($params);
      $rows = $stmt->fetchAll();
      frognet_ok(['success' => true, 'count' => count($rows), 'rows' => $rows]);
    }

    case 'get': {
      $pkVals = frognet_pk_from($_GET, $entity['pk']);
      $row = frognet_fetch_one($entity, $pkVals);
      if (!$row) frognet_error(404, 'Not found');
      frognet_ok(['success' => true, 'row' => $row]);
    }

    case 'create': {
      $body = frognet_json_body();
      frognet_require_fields($body, $entity['required_on_create']);
      $writable = frognet_extract_writable($body, $entity['columns'], $entity['auto']);
      if (!$writable) frognet_error(400, 'No fields to insert');

      $cols = array_keys($writable);
      $place = array_map(fn($c) => ':' . $c, $cols);
      $sql = "INSERT INTO " . frognet_qcol($entity['table']) .
             " (" . implode(',', array_map('frognet_qcol', $cols)) . ") VALUES (" . implode(',', $place) . ")";
      $stmt = $pdo->prepare($sql);
      $stmt->execute($writable);

      $insertId = null;
      if (!empty($entity['auto'])) {
        $insertId = $pdo->lastInsertId();
      }

      $row = null;
      if ($insertId && count($entity['pk']) === 1 && in_array($entity['pk'][0], $entity['auto'], true)) {
        $row = frognet_fetch_one($entity, [$entity['pk'][0] => $insertId]);
      } else {
        $providedPk = [];
        foreach ($entity['pk'] as $k) if (isset($writable[$k])) $providedPk[$k] = $writable[$k];
        if ($providedPk) $row = frognet_fetch_one($entity, $providedPk);
      }

      frognet_ok(['success' => true, 'insert_id' => $insertId, 'row' => $row]);
    }

    case 'update': {
      $body = frognet_json_body();
      $pkVals = frognet_pk_from($body, $entity['pk']);
      $writable = frognet_extract_writable($body, $entity['columns'], array_merge($entity['auto'], $entity['pk']));
      if (!$writable) frognet_error(400, 'No fields to update');
      if ($entity['table'] === 'TeamMember') frognet_error(400, 'Updates not supported for TeamMember; delete & recreate.');

      $setParts = [];
      foreach ($writable as $k => $_) $setParts[] = frognet_qcol($k) . " = :set_" . $k;
      $sql = "UPDATE " . frognet_qcol($entity['table']) . " SET " . implode(',', $setParts) . " WHERE ";
      $whereParts = [];
      foreach ($entity['pk'] as $k) $whereParts[] = frognet_qcol($k) . " = :pk_" . $k;
      $sql .= implode(' AND ', $whereParts);

      $params = [];
      foreach ($writable as $k => $v) $params["set_{$k}"] = $v;
      foreach ($pkVals as $k => $v) $params["pk_{$k}"] = $v;

      $stmt = $pdo->prepare($sql);
      $stmt->execute($params);

      $row = frognet_fetch_one($entity, $pkVals);
      frognet_ok(['success' => true, 'row' => $row, 'updated' => $stmt->rowCount()]);
    }

    case 'delete': {
      $pkVals = frognet_pk_from($_GET, $entity['pk']);
      $where = [];
      foreach ($entity['pk'] as $k) $where[] = frognet_qcol($k) . " = :{$k}";
      $sql = "DELETE FROM " . frognet_qcol($entity['table']) . " WHERE " . implode(' AND ', $where) . " LIMIT 1";
      $stmt = $pdo->prepare($sql);
      $stmt->execute($pkVals);
      frognet_ok(['success' => true, 'deleted' => $stmt->rowCount()]);
    }

    default:
      frognet_error(400, "Unknown action: {$action}");
  }
} catch (PDOException $e) {
  frognet_error(500, 'DB error', ['detail' => $e->getMessage()]);
}
