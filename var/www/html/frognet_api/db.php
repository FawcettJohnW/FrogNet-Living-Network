<?php
function frognet_config(): array {
  static $cfg;
  if (!$cfg) {
    $cfg = require __DIR__ . '/config.php';
  }
  return $cfg;
}

function frognet_db(): PDO {
  static $pdo;
  if ($pdo instanceof PDO) return $pdo;
  $cfg = frognet_config();
  $db  = $cfg['db'];
  $dsn = sprintf('mysql:host=%s;dbname=%s;charset=%s', $db['host'], $db['name'], $db['charset']);
  $pdo = new PDO($dsn, $db['user'], $db['pass'], [
    PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES   => false,
  ]);
  return $pdo;
}
