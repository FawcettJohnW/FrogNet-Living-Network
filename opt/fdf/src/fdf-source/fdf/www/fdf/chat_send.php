<?php
// chat_send.php (POST from, text) -- [FDF_CHAT_V1][ROOM_IS_THE_FAMILY_V1] a message is its sender's own cell,
// (chat, msg, <from>.<ms>) = {from, text, at}, written HERE, on the page's own server, over its FNWP connection (ram
// write). The room is that family in its write order: nothing relays it.
header('Content-Type: application/json');
// [FDF_WEB_UNDER_APACHE_V1 - T5b] the command runs under the fdf.env of the install this page belongs to (www/fdf ->
// ../../fdf.env: /opt/fdf/fdf.env on a node; it sources var/join.env and var/area.env), read on every request, so the
// node's Apache (www-data) and a `php -S` see the same settings, and a join or leave is seen at the next request.
$env = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && exec ';
$from = isset($_POST['from']) ? trim($_POST['from']) : ''; $text = isset($_POST['text']) ? (string)$_POST['text'] : '';
if (!preg_match('/^[A-Za-z0-9_.-]{1,40}$/', $from) || $text === '') { http_response_code(400); echo json_encode(['error' => 'from (letters, digits, _ . -) and text are required']); exit; }
$at = microtime(true); $bag = json_encode(['from' => $from, 'text' => $text, 'at' => $at]);
$out = []; $rc = 0;
exec($env . '"$FDF_BIN"/ram write chat msg ' . escapeshellarg($from . '.' . intval($at * 1000)) . ' ' . escapeshellarg($bag), $out, $rc);   // stderr (diagnostics) goes to the server's error log (Apache) or terminal (php -S), never into the JSON
if ($rc !== 0) { http_response_code(502); echo json_encode(['error' => 'ram write failed', 'output' => implode("\n", $out)]); exit; }
echo implode("\n", $out);
