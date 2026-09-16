<?php
require_once __DIR__ . '/db.php';
require_once __DIR__ . '/entities.php';

function frognet_headers(): void {
  header('Content-Type: application/json; charset=utf-8');
  header('Cache-Control: no-store');
}

function frognet_error(int $code, string $message, array $extra = []): never {
  http_response_code($code);
  $payload = array_merge(['success' => false, 'error' => $message], $extra);
  echo json_encode($payload, JSON_UNESCAPED_SLASHES);
  exit;
}

function frognet_ok(array $payload): void {
  echo json_encode($payload, JSON_UNESCAPED_SLASHES);
  exit;
}

function frognet_get_param(string $key, ?string $default = null): ?string {
  return $_GET[$key] ?? $default;
}

function frognet_json_body(): array {
  $raw = file_get_contents('php://input');
  if ($raw === false || $raw === '') return [];
  $data = json_decode($raw, true);
  if (!is_array($data)) frognet_error(400, 'Invalid JSON body');
  return $data;
}

function frognet_entity(string $entityKey): array {
  $map = frognet_entities();
  if (!isset($map[$entityKey])) frognet_error(400, "Unknown entity: {$entityKey}");
  return $map[$entityKey];
}

function frognet_qcol(string $col): string {
  return '`' . str_replace('`','``',$col) . '`';
}

function ends_with($s, $suffix) {
  return substr($s, -strlen($suffix)) === $suffix;
}

function frognet_build_query_parts(array $entity, array $filters): array {
  $where = [];
  $params = [];
  $columns = $entity['columns'];
  $order = null;
  $limit = null;
  $offset = null;

  $cfg = frognet_config();

  foreach ($filters as $k => $v) {
    if ($v === '' || $v === null) continue;
    if ($k === 'order') { $order = $v; continue; }
    if ($k === 'limit') { $limit = (int)$v; continue; }
    if ($k === 'offset') { $offset = (int)$v; continue; }

    if (ends_with($k, '__like')) {
      $col = substr($k, 0, -6);
      if (!in_array($col, $columns, true)) continue;
      $where[] = frognet_qcol($col) . " LIKE :{$col}_like";
      $params["{$col}_like"] = $v;
    } else {
      $col = $k;
      if (!in_array($col, $columns, true)) continue;
      $where[] = frognet_qcol($col) . " = :{$col}";
      $params[$col] = $v;
    }
  }

  $orderBy = '';
  if ($order) {
    $parts = preg_split('/\s+/', trim($order));
    $col = $parts[0] ?? '';
    $dir = strtoupper($parts[1] ?? 'ASC');
    if (in_array($col, $columns, true)) {
      if ($dir !== 'ASC' && $dir !== 'DESC') $dir = 'ASC';
      $orderBy = ' ORDER BY ' . frognet_qcol($col) . ' ' . $dir . ' ';
    }
  }

  $max = (int)($cfg['max_limit'] ?? 1000);
  $def = (int)($cfg['default_limit'] ?? 200);
  if ($limit === null || $limit <= 0 || $limit > $max) $limit = $def;
  $limitSql = ' LIMIT ' . (int)$limit;
  if ($offset !== null && $offset >= 0) $limitSql .= ' OFFSET ' . (int)$offset;

  $whereSql = $where ? (' WHERE ' . implode(' AND ', $where)) : '';

  return [$whereSql, $params, $orderBy, $limitSql];
}

function frognet_require_fields(array $body, array $required): void {
  foreach ($required as $r) {
    if (!array_key_exists($r, $body) || $body[$r] === '' || $body[$r] === null) {
      frognet_error(400, "Missing required field: {$r}");
    }
  }
}

function frognet_extract_writable(array $body, array $columns, array $auto): array {
  $out = [];
  foreach ($columns as $c) {
    if (in_array($c, $auto, true)) continue;
    if (array_key_exists($c, $body)) $out[$c] = $body[$c];
  }
  return $out;
}

function frognet_pk_from(array $src, array $pk): array {
  $vals = [];
  foreach ($pk as $k) {
    if (!isset($src[$k])) frognet_error(400, "Missing primary key: {$k}");
    $vals[$k] = $src[$k];
  }
  return $vals;
}

function frognet_fetch_one(array $entity, array $pkVals): ?array {
  $pdo = frognet_db();
  $where = [];
  foreach ($entity['pk'] as $k) $where[] = frognet_qcol($k) . " = :{$k}";
  $sql = "SELECT " . implode(',', array_map('frognet_qcol', $entity['columns'])) .
         " FROM " . frognet_qcol($entity['table']) .
         " WHERE " . implode(' AND ', $where) . " LIMIT 1";
  $stmt = $pdo->prepare($sql);
  $stmt->execute($pkVals);
  $row = $stmt->fetch();
  return $row ?: null;
}
