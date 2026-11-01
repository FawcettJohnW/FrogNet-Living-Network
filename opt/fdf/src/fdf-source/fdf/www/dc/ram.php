<?php
// ram.php -- [DC_APPS_V1] the DisasterComm pages' one door to the area's RAM, through the `ram` CLI as ../fdf/chat_*.php:
//   GET  ?op=read&s=SERVICE&v=VARIABLE[&i=INSTANCE][&after=ID&wait=S[&ram=WHERE]]   every cell (or the cells newer
//                                                                         than ID, held up to S seconds until one exists;
//                                                                         at once when WHERE is not where this node is now)
//   POST ?op=write   {"s":..,"v":..,"i":..,"bag":{..}}                  the caller's own cell
//   POST ?op=announce                                                     dc.apps.<this node> = this node's add-ins
//   GET  ?op=watch&w=[{"s","v","after","team"?,"ram"?},..]&wait=W            [ONE_HELD_READ_PER_PAGE_V1] held reads on
//        several families in one request: answered when any of them has newer cells (or one is on another RAM now),
//        {"got": [{"k": index, "rows": [..], "ram": ..}, ..]}, else after W s with none
//   GET  ?op=rams&app=APP                                                  {"rams": {ram: host:port}} served now
//   POST ?op=login&app=APP {"user"?,"password"?,"role"?}                    {"role"}: checked here, a read of the access tuples
//   POST ?op=device&app=APP {"dev","role","user"}                            the device's own access record, network RAM
//   GET  ?op=whoami                                                       {"host": the asking device's host name, or ''}
// Only the services an installed add-in declares (apps.json) are reachable. A failure is the answer, with why.
// [RAM_IS_SAID_V1] every read and write answer carries "ram": the RAM that holds the service right now. A page that sees
// it change reads everything again and writes its own truth there: memory is never moved, each owner writes its own again.
// [APP_DROPLET_V1] an app's services are in the app's own RAMs (APP-SETUP-AND-OPERATION.md): apps.json names, per service,
// the app and the RAM ("app", "rams": {service: ram}); where that RAM is served now is this node's app-where.json, which
// fdf-app serve writes on every event. A service of an app whose RAM is not served is refused: the client connects first.
// [TEAM_RAM_V1] a service whose RAM is "team" is in the team's own RAM, named by the request (read: &team=, write:
// "team" in the body): <app>.<team>, served where app-where.json says. ?op=rams lists the app's RAMs served now.
// The launcher's own service (dc) stays on the network RAM (fdf.env's RINA_RAM).
// [STATION_IS_HOSTNAME_V1] whoami: the asking device's hostname, as this node's facts have it (the lease this node gave
// that address, facts.addrs kind "given", value). It is the device's station ID (PLAN.md, "Message numbers"): unique on
// the network, typed by nobody. A device this node gave no lease has none here, and is told so.
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
function station_of($ip) {                                      // [STATION_IS_HOSTNAME_V1] the lease this node gave $ip
    global $fdf_var;
    $f = json_decode((string)@file_get_contents($fdf_var . '/facts.json'), true);
    foreach ((is_array($f) ? ($f['addrs'] ?? []) : []) as $a)
        if (($a['kind'] ?? '') === 'given' && ($a['ip'] ?? '') === $ip && ($a['value'] ?? '') !== '')
            return preg_match('/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/i', $a['value']) ? '' : $a['value'];   // a lease with no name: fdf-facts wrote the MAC
    return '';
}
function for_service($s, $team = null) {                        // [APP_DROPLET_V1] the RAM this service is in, now
    global $in_ram, $where, $env, $fdf_var;
    if (!isset($in_ram[$s])) return;
    $key = $in_ram[$s];
    if (substr($key, -5) === '.team') {                         // [TEAM_RAM_V1]
        if (!is_string($team) || !preg_match('/^[A-Za-z0-9_-]{1,40}$/', $team) || $team === 'general') fail(400, "service '$s' is in a team's RAM: name the team");
        $key = substr($key, 0, -4) . $team;
    }
    $map = json_decode((string)@file_get_contents($fdf_var . '/app-where.json'), true);
    $at = is_array($map) ? ($map[$key] ?? '') : '';
    if ($at === '' || !preg_match('/^[0-9.]+:[0-9]+$/', $at)) fail(503, "the app's RAM " . $key . ' is not served yet: nothing works until it is');
    $where = $at;
    $env = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && RINA_RAM=' . escapeshellarg($at) . ' exec "$FDF_BIN"/ram ';
}
function said($text, $where) { $j = json_decode($text, true); if (!is_array($j)) return $text; $j['ram'] = $where; return json_encode($j, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE); }

$op = $_GET['op'] ?? '';
if ($op === 'read') {
    $s = $_GET['s'] ?? ''; $v = $_GET['v'] ?? ''; $i = $_GET['i'] ?? '';
    if (!isset($allowed[$s])) fail(403, "service '$s' is not an installed add-in's");
    if (!name_ok($v)) fail(400, 'variable needed');
    for_service($s, $_GET['team'] ?? null);
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
    for_service($in['s'], $in['team'] ?? null);
    echo said(ram($env, ['write', $in['s'], $in['v'], $in['i'], json_encode($in['bag'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)]), $where);
} elseif ($op === 'announce' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $node = gethostname();
    $list = []; foreach ($apps['apps'] as $a) $list[] = ['id' => $a['id'], 'title' => $a['title'], 'version' => $a['version'], 'page' => $a['page']];
    echo ram($env, ['write', 'dc', 'apps', $node, json_encode(['node' => $node, 'apps' => $list, 'at' => time()], JSON_UNESCAPED_SLASHES)]);
} elseif ($op === 'watch') {
    // [ONE_HELD_READ_PER_PAGE_V1] one web server worker per page, not one per family: a page's held reads are this one
    // request, each family's `ram read ... after wait` a child, and the answer is whichever wake first
    $w = json_decode((string)($_GET['w'] ?? ''), true);
    if (!is_array($w) || count($w) < 1 || count($w) > 16) fail(400, 'w: 1 to 16 families');
    $wait = max(0, min(25, intval($_GET['wait'] ?? 8)));
    $where0 = $where; $env0 = $env; $kids = []; $got = [];
    foreach (array_values($w) as $k => $f) {
        $s = $f['s'] ?? ''; $v = $f['v'] ?? '';
        if (!isset($allowed[$s])) fail(403, "service '$s' is not an installed add-in's");
        if (!name_ok($v)) fail(400, 'variable needed');
        $where = $where0; $env = $env0; for_service($s, $f['team'] ?? null);
        if (isset($f['ram']) && $f['ram'] !== '' && $f['ram'] !== $where) { $got[] = ['k' => $k, 'rows' => [], 'ram' => $where]; continue; }
        $cmd = $env; foreach (['read', $s, $v, '', intval($f['after'] ?? 0), $wait] as $a) $cmd .= escapeshellarg((string)$a) . ' ';
        $p = proc_open($cmd, [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
        if (!is_resource($p)) fail(502, 'could not run ram');
        stream_set_blocking($pipes[1], false);
        $kids[$k] = ['p' => $p, 'out' => $pipes[1], 'err' => $pipes[2], 'text' => '', 'ram' => $where];
    }
    $done = []; $until = time() + $wait + 5;
    ignore_user_abort(true);
    while ($got === [] && count($done) < count($kids) && time() < $until) {
        $r = []; foreach ($kids as $k => $c) if (!isset($done[$k])) $r[] = $c['out'];
        $x = null; $e = null;
        $n = stream_select($r, $x, $e, 1);
        if ($n === false) break;
        if ($n === 0) {                                         // nothing yet: is the page still there? (a page that
            echo ' '; @ob_flush(); flush();                     // changed its set ended this request; free the worker)
            if (connection_aborted()) { foreach ($kids as $c) { proc_terminate($c['p']); fclose($c['out']); fclose($c['err']); proc_close($c['p']); } exit; }
            continue;
        }
        foreach ($kids as $k => &$c) {
            if (isset($done[$k]) || !in_array($c['out'], $r, true)) continue;
            $c['text'] .= (string)stream_get_contents($c['out']);
            if (feof($c['out'])) {
                $done[$k] = true;
                $j = json_decode($c['text'], true);
                if (!is_array($j)) { foreach ($kids as $q) proc_terminate($q['p']); fail(502, 'ram read failed', trim($c['text'] . stream_get_contents($c['err']))); }
                if (!empty($j['rows'])) $got[] = ['k' => $k, 'rows' => $j['rows'], 'ram' => $c['ram']];
            }
        }
        unset($c);
    }
    foreach ($kids as $k => $c) { if (!isset($done[$k])) proc_terminate($c['p']); fclose($c['out']); fclose($c['err']); proc_close($c['p']); }
    echo json_encode(['ok' => true, 'got' => $got], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
} elseif ($op === 'rams') {                                    // [TEAM_RAM_V1] the app's RAMs served now, and where
    $app = $_GET['app'] ?? '';
    if (!preg_match('/^[A-Za-z0-9_-]{1,40}$/', $app)) fail(400, 'app needed');
    $map = json_decode((string)@file_get_contents($fdf_var . '/app-where.json'), true);
    $out = []; foreach ((is_array($map) ? $map : []) as $k => $v) if (strpos($k, $app . '.') === 0) $out[substr($k, strlen($app) + 1)] = $v;
    echo json_encode(['ok' => true, 'rams' => $out]);
} elseif ($op === 'whoami') {
    // [STATION_IS_HOSTNAME_V1] the asking device's host name, from its lease here; '' when it gave none (the device's
    // own name is then its name: the page asks for it). Nobody sees a station ID: it is the host or device name.
    $ip = $_SERVER['REMOTE_ADDR'] ?? '';
    echo json_encode(['ok' => true, 'host' => station_of($ip), 'ip' => $ip]);
} elseif ($op === 'login' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    // [APP_ACCESS_V1] login is a read, here at the device's own edge: the app's access definition is in its configuration
    // tuples in the network RAM (each droplet writes it), and the password is checked against the verifier there. No
    // login server and nothing sent to another node. The answer is the role; the device writes its own access record.
    $in = json_decode(file_get_contents('php://input'), true);
    $app = $_GET['app'] ?? '';
    if (!is_array($in) || !preg_match('/^[A-Za-z0-9_-]{1,40}$/', $app)) fail(400, 'app and a JSON body needed');
    $cfg = null;
    foreach ((json_decode(ram($env, ['read', 'fdf-app', 'config']), true)['rows'] ?? []) as $c)
        if (strpos($c['instance'] ?? '', $app . '.') === 0) { $cfg = $c['bag']; break; }
    if (!is_array($cfg)) fail(503, "no configuration of $app in the network RAM yet: no droplet of it has written its own");
    $user = (string)($in['user'] ?? ''); $role = (string)($in['role'] ?? '');
    if ($user !== '') {
        $u = $cfg['users'][$user] ?? null; $v = is_array($u) ? ($u['verifier'] ?? null) : null;
        if (!is_array($v) || !hash_equals((string)$v['hash'], hash_pbkdf2('sha256', (string)($in['password'] ?? ''), hex2bin((string)$v['salt']), intval($v['n']))))
            fail(403, "that user and password are not this app's");
        $role = (string)$u['role'];
    } elseif ($role !== '') {
        if (empty($cfg['roles'][$role]['default'])) fail(403, "role $role is activated at its user's login");
    } elseif (!empty($cfg['login'])) fail(403, 'this app requires login');
    echo json_encode(['ok' => true, 'role' => $role, 'user' => $user, 'station' => station_of($_SERVER['REMOTE_ADDR'] ?? '')]);
} elseif ($op === 'device' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    // [APP_ACCESS_V1] the device's own access record (its role, its user), written by the device after its login and
    // again after every RAM change, in the network RAM beside the app's configuration: (fdf-app, device, <app>.<dev>)
    $in = json_decode(file_get_contents('php://input'), true);
    $app = $_GET['app'] ?? ''; $dev = (string)($in['dev'] ?? '');
    if (!preg_match('/^[A-Za-z0-9_-]{1,40}$/', $app) || !preg_match('/^[A-Za-z0-9_-]{1,40}$/', $dev)) fail(400, 'app and dev needed');
    $bag = ['dev' => $dev, 'station' => preg_replace('/[^a-z0-9-]/', '', strtolower((string)($in['station'] ?? ''))), 'role' => (string)($in['role'] ?? ''), 'user' => (string)($in['user'] ?? ''), 'at' => time()];
    echo ram($env, ['write', 'fdf-app', 'device', $app . '.' . $dev, json_encode($bag, JSON_UNESCAPED_SLASHES)]);
} else {
    fail(400, 'op is read, write, watch, announce, rams, whoami, login or device');
}
