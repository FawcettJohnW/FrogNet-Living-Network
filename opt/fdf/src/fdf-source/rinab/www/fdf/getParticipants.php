<?php
// getParticipants.php -- [FDF_DASHBOARD_V1] (plan item 7) FD&F's getHosts.php: the participants, as the shared space holds
// them. The RAM get happens HERE, on the server, over this machine's FNWP connection to the RAM manager (fdf-participants);
// the browser only reads this page. Same keys as getHosts.php (ip, hostname), plus ips, alive and the capability fields.
// Configured: FDF_BIN (where fdf-participants is) and the machine's own area settings (RINA_RAM, or RINA_AREA +
// RINA_MAP_RESOLVER), all from fdf.env (below).
header('Content-Type: application/json');
// [FDF_WEB_UNDER_APACHE_V1 - T5b] the command runs under the fdf.env of the install this page belongs to (www/fdf ->
// ../../fdf.env: /opt/fdf/fdf.env on a node; it sources var/join.env and var/area.env), read on every request, so the
// node's Apache (www-data) and a `php -S` see the same settings, and a join or leave is seen at the next request.
$env = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && exec ';
$out = []; $rc = 0;
exec($env . '"$FDF_BIN"/fdf-participants', $out, $rc);   // stderr (diagnostics) goes to the server's error log (Apache) or terminal (php -S), never into the JSON
$text = implode("\n", $out);
if ($rc !== 0 || json_decode($text) === null) {
    http_response_code(502);
    echo json_encode(['error' => 'fdf-participants failed', 'rc' => $rc, 'output' => $text]);
    exit;
}
echo $text;
