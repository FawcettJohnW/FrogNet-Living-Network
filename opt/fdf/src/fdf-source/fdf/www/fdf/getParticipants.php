<?php
// getParticipants.php -- [FDF_DASHBOARD_V1] (plan item 7) FD&F's getHosts.php: the participants, as the shared space holds
// them. The RAM get happens HERE, on the server, over this machine's FNWP connection to the RAM manager (fdf-participants);
// the browser only reads this page. Same keys as getHosts.php (ip, hostname), plus ips, alive and the capability fields.
// Configured: FDF_BIN (where fdf-participants is) and the machine's own area settings (RINA_RAM, or RINA_AREA +
// RINA_MAP_RESOLVER) in the server's environment.
header('Content-Type: application/json');
$bin = getenv('FDF_BIN') ?: '/opt/fdf/bin';
$out = []; $rc = 0;
exec(escapeshellcmd($bin . '/fdf-participants') , $out, $rc);   // stderr (diagnostics) goes to the server's terminal, never into the JSON
$text = implode("\n", $out);
if ($rc !== 0 || json_decode($text) === null) {
    http_response_code(502);
    echo json_encode(['error' => 'fdf-participants failed', 'rc' => $rc, 'output' => $text]);
    exit;
}
echo $text;
