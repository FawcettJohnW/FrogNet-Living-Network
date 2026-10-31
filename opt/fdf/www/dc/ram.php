<?php
// ram.php -- [DC_APPS_V1] the DisasterComm pages' one door to the area's RAM, through the `ram` CLI as ../fdf/chat_*.php:
//   GET  ?op=read&s=SERVICE&v=VARIABLE[&i=INSTANCE][&after=ID&wait=S]   every cell (or the cells newer than ID, held up
//                                                                         to S seconds until one exists)
//   POST ?op=write   {"s":..,"v":..,"i":..,"bag":{..}}                  the caller's own cell
//   POST ?op=announce                                                     dc.apps.<this node> = this node's add-ins
// Only the services an installed add-in declares (apps.json) are reachable. A failure is the answer, with why.
header('Content-Type: application/json');
set_time_limit(0);
$env = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && exec "$FDF_BIN"/ram ';
$apps = json_decode(file_get_contents(__DIR__ . '/apps.json'), true);
$allowed = ['dc' => true];
foreach ($apps['apps'] as $a) foreach ($a['services'] as $s) $allowed[$s] = true;

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

$op = $_GET['op'] ?? '';
if ($op === 'read') {
    $s = $_GET['s'] ?? ''; $v = $_GET['v'] ?? ''; $i = $_GET['i'] ?? '';
    if (!isset($allowed[$s])) fail(403, "service '$s' is not an installed add-in's");
    if (!name_ok($v)) fail(400, 'variable needed');
    if (isset($_GET['after'])) {
        $wait = max(0, min(25, intval($_GET['wait'] ?? 25)));
        echo ram($env, ['read', $s, $v, $i, intval($_GET['after']), $wait]);
    } else {
        echo ram($env, $i === '' ? ['read', $s, $v] : ['read', $s, $v, $i]);
    }
} elseif ($op === 'write' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $in = json_decode(file_get_contents('php://input'), true);
    if (!is_array($in)) fail(400, 'a JSON body is needed');
    if (!isset($allowed[$in['s'] ?? ''])) fail(403, "service '" . ($in['s'] ?? '') . "' is not an installed add-in's");
    if (!name_ok($in['v'] ?? null) || !name_ok($in['i'] ?? null)) fail(400, 'variable and instance needed');
    if (!is_array($in['bag'] ?? null)) fail(400, 'bag must be an object');
    echo ram($env, ['write', $in['s'], $in['v'], $in['i'], json_encode($in['bag'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)]);
} elseif ($op === 'announce' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $node = gethostname();
    $list = []; foreach ($apps['apps'] as $a) $list[] = ['id' => $a['id'], 'title' => $a['title'], 'version' => $a['version'], 'page' => $a['page']];
    echo ram($env, ['write', 'dc', 'apps', $node, json_encode(['node' => $node, 'apps' => $list, 'at' => time()], JSON_UNESCAPED_SLASHES)]);
} else {
    fail(400, 'op is read, write or announce');
}
