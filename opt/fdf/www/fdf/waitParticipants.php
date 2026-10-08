<?php
// waitParticipants.php?gen=N -- [FDF_DASHBOARD_V1] long-poll: returns when the participant set changes after generation
// N (the transport's CHANGE, held in the RAM manager), with {"gen":M,"participants":[...]}. Never a timer: the page
// calls again with the generation it was given. The RAM get happens here, on the server (fdf-participants --wait).
header('Content-Type: application/json');
set_time_limit(0);
$bin = getenv('FDF_BIN') ?: '/opt/fdf/bin';
$gen = isset($_GET['gen']) ? (string)intval($_GET['gen']) : '0';
$out = []; $rc = 0;
exec(escapeshellcmd($bin . '/fdf-participants') . ' --wait ' . escapeshellarg($gen) , $out, $rc);   // stderr (diagnostics) goes to the server's terminal, never into the JSON
$text = implode("\n", $out);
if ($rc !== 0 || json_decode($text) === null) {
    http_response_code(502);
    echo json_encode(['error' => 'fdf-participants --wait failed', 'rc' => $rc, 'output' => $text]);
    exit;
}
echo $text;
