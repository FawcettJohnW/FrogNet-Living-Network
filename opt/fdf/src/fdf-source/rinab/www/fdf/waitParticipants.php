<?php
// waitParticipants.php?gen=N -- [FDF_DASHBOARD_V1] long-poll: returns when the participant set changes after generation
// N (the transport's CHANGE, held in the RAM manager), with {"gen":M,"participants":[...]}. Never a timer: the page
// calls again with the generation it was given. The RAM get happens here, on the server (fdf-participants --wait).
header('Content-Type: application/json');
set_time_limit(0);
// [FDF_WEB_UNDER_APACHE_V1 - T5b] the command runs under the fdf.env of the install this page belongs to (www/fdf ->
// ../../fdf.env: /opt/fdf/fdf.env on a node; it sources var/join.env and var/area.env), read on every request, so the
// node's Apache (www-data) and a `php -S` see the same settings, and a join or leave is seen at the next request.
$env = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && exec ';
$gen = isset($_GET['gen']) ? (string)intval($_GET['gen']) : '0';
$out = []; $rc = 0;
exec($env . '"$FDF_BIN"/fdf-participants --wait ' . escapeshellarg($gen), $out, $rc);   // stderr (diagnostics) goes to the server's error log (Apache) or terminal (php -S), never into the JSON
$text = implode("\n", $out);
if ($rc !== 0 || json_decode($text) === null) {
    http_response_code(502);
    echo json_encode(['error' => 'fdf-participants --wait failed', 'rc' => $rc, 'output' => $text]);
    exit;
}
echo $text;
