<?php
// chat_wait.php?after=N -- [FDF_CHAT_V1] the room's messages newer than cell N, held in the RAM manager until one exists
// (Ribbit's own held read, up to its maximum wait). The page calls again with the newest id it was given.
header('Content-Type: application/json');
set_time_limit(0);
$bin = getenv('FDF_BIN') ?: '/opt/fdf/bin';
$after = isset($_GET['after']) ? (string)intval($_GET['after']) : '0';
$out = []; $rc = 0;
exec(escapeshellcmd($bin . '/ram') . ' read chat room "" ' . escapeshellarg($after) . ' 25', $out, $rc);   // stderr (diagnostics) goes to the server's terminal, never into the JSON
if ($rc !== 0) { http_response_code(502); echo json_encode(['error' => 'ram read failed', 'output' => implode("\n", $out)]); exit; }
echo implode("\n", $out);
