<?php
// chat_wait.php?after=N -- [FDF_CHAT_V1] the room's messages newer than cell N, held in the RAM manager until one exists
// (Ribbit's own held read, up to its maximum wait). The page calls again with the newest id it was given.
header('Content-Type: application/json');
set_time_limit(0);
// [FDF_WEB_UNDER_APACHE_V1 - T5b] the command runs under the fdf.env of the install this page belongs to (www/fdf ->
// ../../fdf.env: /opt/fdf/fdf.env on a node; it sources var/join.env and var/area.env), read on every request, so the
// node's Apache (www-data) and a `php -S` see the same settings, and a join or leave is seen at the next request.
$env = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && exec ';
$after = isset($_GET['after']) ? (string)intval($_GET['after']) : '0';
$out = []; $rc = 0;
exec($env . '"$FDF_BIN"/ram read chat room "" ' . escapeshellarg($after) . ' 25', $out, $rc);   // stderr (diagnostics) goes to the server's error log (Apache) or terminal (php -S), never into the JSON
if ($rc !== 0) { http_response_code(502); echo json_encode(['error' => 'ram read failed', 'output' => implode("\n", $out)]); exit; }
echo implode("\n", $out);
