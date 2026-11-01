<?php
// ram.php -- [DC_APPS_V1] the DisasterComm pages' one door to the area's RAM, through the `ram` CLI as ../fdf/chat_*.php:
//   GET  ?op=read&s=SERVICE&v=VARIABLE[&i=INSTANCE][&after=ID&wait=S[&ram=WHERE]]   every cell (or the cells newer
//                                                                         than ID, held up to S seconds until one exists;
//                                                                         at once when WHERE is not where this node is now)
//   POST ?op=write   {"s":..,"v":..,"i":..,"bag":{..}}                  the caller's own cell
//   POST ?op=announce                                                     dc.apps.<this node> = this node's add-ins
// Only the services an installed add-in declares (apps.json) are reachable. A failure is the answer, with why.
// [RAM_IS_SAID_V1] every read and write answer carries "ram": the RAM that holds the service right now. A page that sees
// it change reads everything again and writes its own truth there: memory is never moved, each owner writes its own again.
// [APP_DROPLET_V1] an app's services are in the app's own RAMs (APP-SETUP-AND-OPERATION.md): apps.json names, per service,
// the app and the RAM ("app", "rams": {service: ram}); where that RAM is served now is this node's app-where.json, which
// fdf-app serve writes on every event. A service of an app whose RAM is not served is refused: the client connects first.
// The launcher's own service (dc) stays on the network RAM (fdf.env's RINA_RAM).
header('Content-Type: application/json');
set_time_limit(0);
$env = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && exec "$FDF_BIN"/ram ';
$apps = json_decode(file_get_contents(__DIR__ . '/apps.json'), true);
$allowed = ['dc' => true];
$in_ram = [];                                                   // service => "app.ram"
foreach ($apps['apps'] as $a) foreach ($a['services'] as $s) {
    $allowed[$s] = true;
    if (isset($a['app'], $a['rams'][$s])) $in_ram[$s] = $a['app'] . '.' . $a['rams'][$s];
}

function fail($code, $why, $out = '') { http_response_code($code); echo json_encode(['ok' => false, 'why' => $why, 'output' => $out]); exit; }
function name_ok($x) { return is_string($x) && $x !== '' && strlen($x) <= 120 && !preg_match('/[\x00-\x1f]/', $x); }
function ram($env, $args) {
    $cmd = $env; foreach ($args as $a) $cmd .= escapeshellarg((string)$a) . ' ';
    // the answer is stdout; stderr (the CLI's diagnostics) goes to the server's log, and into the reply only on failure
    $p = proc_open($cmd, [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    if (!is_resource($p)) fail(502, 'could not run ram');
    $text = stream_get_contents($pipes[1]); $err = stream_get_contents($pipes[2]); fclose($pipes[1]); fclose($pipes[2]);
    $rc = proc_close($p);
    if ($err !== '') error_log('dc ram.php: ram ' . $args[0] . ': ' . trim($err));
    if ($rc !== 0) fail(502, 'ram ' . $args[0] . ' failed', trim($err . "\n" . $text));
    return $text;
}

$where = trim((string)shell_exec('. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && printf %s "$RINA_RAM"'));
$fdf_var = trim((string)shell_exec('. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && printf %s "${FDF_VAR:-/opt/fdf/var}"'));
function for_service($s) {                                      // [APP_DROPLET_V1] the RAM this service is in, now
    global $in_ram, $where, $env, $fdf_var;
    if (!isset($in_ram[$s])) return;
    $map = json_decode((string)@file_get_contents($fdf_var . '/app-where.json'), true);
    $at = is_array($map) ? ($map[$in_ram[$s]] ?? '') : '';
    if ($at === '' || !preg_match('/^[0-9.]+:[0-9]+$/', $at)) fail(503, "the app's RAM " . $in_ram[$s] . ' is not served yet: nothing works until it is');
    $where = $at;
    $env = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && RINA_RAM=' . escapeshellarg($at) . ' exec "$FDF_BIN"/ram ';
}
function said($text, $where) { $j = json_decode($text, true); if (!is_array($j)) return $text; $j['ram'] = $where; return json_encode($j, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE); }

$op = $_GET['op'] ?? '';
if ($op === 'read') {
    $s = $_GET['s'] ?? ''; $v = $_GET['v'] ?? ''; $i = $_GET['i'] ?? '';
    if (!isset($allowed[$s])) fail(403, "service '$s' is not an installed add-in's");
    if (!name_ok($v)) fail(400, 'variable needed');
    for_service($s);
    if (isset($_GET['after'])) {
        // [RAM_IS_SAID_V1] a held read for a page that was on another RAM manager: answered at once, with where it is now
        if (isset($_GET['ram']) && $_GET['ram'] !== '' && $_GET['ram'] !== $where) { echo json_encode(['ok' => true, 'rows' => [], 'ram' => $where]); exit; }
        $wait = max(0, min(25, intval($_GET['wait'] ?? 25)));
        echo said(ram($env, ['read', $s, $v, $i, intval($_GET['after']), $wait]), $where);
    } else {
        echo said(ram($env, $i === '' ? ['read', $s, $v] : ['read', $s, $v, $i]), $where);
    }
} elseif ($op === 'write' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $in = json_decode(file_get_contents('php://input'), true);
    if (!is_array($in)) fail(400, 'a JSON body is needed');
    if (!isset($allowed[$in['s'] ?? ''])) fail(403, "service '" . ($in['s'] ?? '') . "' is not an installed add-in's");
    if (!name_ok($in['v'] ?? null) || !name_ok($in['i'] ?? null)) fail(400, 'variable and instance needed');
    if (!is_array($in['bag'] ?? null)) fail(400, 'bag must be an object');
    for_service($in['s']);
    echo said(ram($env, ['write', $in['s'], $in['v'], $in['i'], json_encode($in['bag'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)]), $where);
} elseif ($op === 'announce' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $node = gethostname();
    $list = []; foreach ($apps['apps'] as $a) $list[] = ['id' => $a['id'], 'title' => $a['title'], 'version' => $a['version'], 'page' => $a['page']];
    echo ram($env, ['write', 'dc', 'apps', $node, json_encode(['node' => $node, 'apps' => $list, 'at' => time()], JSON_UNESCAPED_SLASHES)]);
} else {
    fail(400, 'op is read, write or announce');
}
