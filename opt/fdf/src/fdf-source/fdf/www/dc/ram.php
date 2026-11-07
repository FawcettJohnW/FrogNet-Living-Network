<?php
// ram.php -- [DC_APPS_V1] the DisasterComm pages' one door to the area's RAM, through the `ram` CLI as ../fdf/chat_*.php:
//   GET  ?op=read&s=SERVICE&v=VARIABLE[&i=INSTANCE][&after=ID&wait=S[&ram=WHERE]]   every cell (or the cells newer
//                                                                         than ID, held up to S seconds until one exists;
//                                                                         at once when WHERE is not where this node is now)
//   POST ?op=write   {"s":..,"v":..,"i":..,"bag":{..}}                  the caller's own cell
//   POST ?op=announce                                                     dc.apps.<this node> = this node's add-ins
//   GET  ?op=watch&w=[{"s","v","after","team"?,"ram"?},..]&wait=W            [ONE_HELD_READ_PER_PAGE_V1] held reads on
//        several families in one request: answered when any of them has newer cells (or one is on another RAM now),
//        {"got": [{"k": index, "rows": [..], "ram": .., "again"?: true}, ..], "down"?: [k..]}, else after W s with none;
//        "again": read that family again in full (its RAM started again, or is up again); "down": its RAM is not up yet
//   GET  ?op=rams&app=APP[&until=RAM|&not=JSON][&up=HOST:PORT,..]&wait_s=S  {"rams": {ram: host:port}} served now; held
//        until RAM is served, or the RAMs are not JSON, or one of the up RAMs says it is up ("up"): file events
//   POST ?op=login&app=APP {"user"?,"password"?,"role"?}                    {"role"}: checked here, a read of the access tuples
//   POST ?op=device&app=APP {"dev","role","user"}                            the device's own access record, network RAM
//   GET  ?op=roles&app=APP                                                 {"roles","rams","login","admin"}: what a client
//        offers (the teams the admin defined), from the app's configuration tuples; no users, no verifiers
//   GET  ?op=admin&app=APP[&after=ID&wait=S]                              [ADMIN_APP_V1] for the admin app: every member's
//        configuration tuple (verifiers, the admin's salt and check value, never a key), every member's answer to the
//        admin (applied), the RAMs served now; with after/wait, held until a member answers newer than ID
//   POST ?op=admin&app=APP {"config","seq","user","mac"}                   the admin's configuration, (fdf-app, admin, APP):
//        relayed, not judged -- each member checks the mac with its own admin key and adopts it or says why not
//   GET  ?op=whoami                                                       {"host": the asking device's host name, or ''}
//   POST ?op=enroll&app=APP {"user","password"}                            [ENROLL_V1] the common way a device joins a region
//        with an app: an enrollment QR (the app's admin made it: this door's URL and the login name) is scanned, the
//        password typed; checked here as op=login checks it, and the answer is the role, whether the user leads its
//        role's team, and the device's OWN invitation to the region this node is in (fdf-identity --leaf --again: the
//        region's hub makes it; a second enrollment of the same user gives a new key and the old one stops working).
//        After that the device is a member of the region: every other app's hub admits it by that, with no new login.
//   POST ?op=identity&app=APP {"user","password","name"}                   [ENROLL_V1] a sponsor (a user who leads a team)
//        asks the region for an invitation for one of its own members, NAME, made <app>-<team>-<NAME>: what the lead's
//        phone hands a member who enrolled with it
// Only the services an installed add-in declares (apps.json) are reachable. A failure is the answer, with why.
// [RAM_IS_SAID_V1] every read and write answer carries "ram": the RAM that holds the service right now. A page that sees
// it change reads everything again and writes its own truth there: memory is never moved, each owner writes its own again.
// [APP_SERVICES_V1] (the doc "FrogNet SystemRam, FrogNetHost Admin and App Services -- Design", sections 5, 7 and 9)
// an app's services are in its AppRams: apps.json names, per service, the app and the RAM ("app", "rams": {service: ram}).
// Each AppRam is a service whose AppHost the election writes into /etc/hosts -- <app>Host.frognet for the general one,
// <team>Host.<app> for a Team's -- and whose port is in the app's one configuration tuple. A service of an app whose
// AppRam has no AppHost now is refused: the client connects first. [TEAM_RAM_V1] a service whose RAM is "team" is in
// the team's own AppRam, named by the request (read: &team=, write: "team" in the body). ?op=rams lists them.
// The app's one configuration tuple, (fdf-app, config, <app>), and each Team's, (fdf-app, config, <app>.<team>), are in
// databasehost-control (databasehost_control.frognet's api.php); the installed machines' capability and answers,
// (fdf-app, can|applied, ...), in the SystemRam (databasehost.frognet's api.php); a device's own record in the general
// AppRam. [ROLES_ON_API_V1] both roles are api.php on port 80.
// The launcher's own service (dc) stays on the network RAM (fdf.env's RINA_RAM).
// [STATION_IS_HOSTNAME_V1] whoami: the asking device's hostname, as this node's facts have it (the lease this node gave
// that address, facts.addrs kind "given", value). It is the device's station ID (PLAN.md, "Message numbers"): unique on
// the network, typed by nobody. A device this node gave no lease has none here, and is told so.
header('Content-Type: application/json');
// [DOOR_SAYS_ITS_BUILD_V1] every answer says which door answered: the header, and "door" in the admin and roles answers
const DOOR_BUILD = 'dc-door-2026-11-07-roles1';   // [ROLES_ON_API_V1]
// [SENSORS_BY_THE_DOOR_V1] the sensor store (api.php on databasehost.frognet) by the address this node's /etc/hosts gives
// it: an app through the tunnel has no .frognet names (the phone's DNS is its carrier's), the address routes
function door_sensors() {
    $a = gethostbyname('databasehost.frognet');
    return $a !== 'databasehost.frognet' ? ['sensors' => 'http://' . $a . '/api.php'] : [];
}
header('X-FDF-Door: ' . DOOR_BUILD);
set_time_limit(0);
$env = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && exec "$FDF_BIN"/ram ';
$apps = json_decode(file_get_contents(__DIR__ . '/apps.json'), true);
$allowed = ['dc' => true];
$in_ram = [];                                                   // service => "app.ram"
foreach ($apps['apps'] as $a) foreach ($a['services'] as $s) {
    $allowed[$s] = true;
    if (isset($a['app'], $a['rams'][$s])) $in_ram[$s] = $a['app'] . '.' . $a['rams'][$s];
}

function fail($code, $why, $out = '') { http_response_code($code); echo json_encode(['ok' => false, 'why' => $why, 'output' => $out, 'door' => DOOR_BUILD]); exit; }
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
// [APP_SERVICES_V1] /etc/hosts (FDF_HOSTS), read at each request: the election rewrites it
$hostenv = explode("\n", (string)shell_exec('. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && printf "%s\n%s\n%s\n%s" "${FDF_HOSTS:-/etc/hosts}" "" "${RINA_CONTROL_RAM:-}" "${RINA_SYSTEM_RAM:-}"'));
function host_ip($name) {
    global $hostenv;
    foreach ((array)@file(trim($hostenv[0] ?? '/etc/hosts')) as $l) {
        $p = preg_split('/\s+/', trim(explode('#', $l, 2)[0]));
        if (count($p) >= 2 && in_array($name, array_slice($p, 1), true)) return $p[0];
    }
    return '';
}
// [ROLES_ON_API_V1 - John 2026-11-07] databasehost-control (the ControlRam) and the SystemRam are roles: one host each,
// named in /etc/hosts, served by that host's default api.php on port 80. A cell (service, variable, instance) is the
// api.php tuple SensorType=<service>, SensorName=<variable>.<instance>, SensorAddress=<the writer>, jsonData=<the bag>.
// There is no RAM host on a FrogNetHost and no RAM port (8841 and /etc/frognet/ram_port are gone).
function role_host($name, $over, $what) {                      // the role's address (RINA_CONTROL_RAM/RINA_SYSTEM_RAM: a test's)
    global $hostenv;
    $at = trim($hostenv[$over] ?? '');
    if ($at === '') $at = host_ip($name);
    if (!preg_match('/^[0-9.]+(:[0-9]+)?$/', $at)) fail(503, "this node names no $name yet (/etc/hosts): the election has not run here ($what)");
    return $at;
}
function ctl_host() { return role_host('databasehost_control.frognet', 2, 'databasehost-control'); }
function sys_host() { return role_host('databasehost.frognet', 3, 'the SystemRam'); }
function role_call($host, $method, $q, $body = null, $timeout = 10) {
    $url = 'http://' . $host . '/api.php?' . http_build_query($q);
    $ctx = ['http' => ['method' => $method, 'timeout' => $timeout, 'ignore_errors' => true]];
    if ($body !== null) { $ctx['http']['header'] = "Content-Type: application/json\r\n"; $ctx['http']['content'] = json_encode($body, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE); }
    $raw = @file_get_contents($url, false, stream_context_create($ctx));
    $j = $raw === false ? null : json_decode($raw, true);
    if (!is_array($j) || empty($j['ok'])) fail(502, "the role's api.php at $host did not answer " . ($q['action'] ?? ''), (string)$raw);
    return $j;
}
function role_filter($svc, $var, $inst) {
    $f = ['SensorType' => $svc];
    $esc = function ($x) { return str_replace(['%', '_'], ['\\%', '\\_'], $x); };
    if ($var !== '' && $inst !== '') $f['SensorName'] = $var . '.' . $inst;
    elseif ($var !== '') $f['SensorName__like'] = $esc($var) . '.%';
    elseif ($inst !== '') $f['SensorName__like'] = '%.' . $esc($inst);
    return $f;
}
function role_cells($rows) {                                   // api.php rows as the cells the pages know, the newest first
    $out = [];
    foreach ($rows as $r) {
        $nm = (string)($r['SensorName'] ?? ''); $dot = strpos($nm, '.');
        $out[] = ['id' => intval($r['SensorID'] ?? 0), 'service' => $r['SensorType'] ?? '', 'variable' => $dot === false ? $nm : substr($nm, 0, $dot),
                  'instance' => $dot === false ? '' : substr($nm, $dot + 1), 'bag' => $r['data'] ?? (object)[], 'writer' => $r['SensorAddress'] ?? '',
                  'updated' => intval($r['UpdatedAtEpoch'] ?? 0)];
    }
    usort($out, function ($a, $b) { return [$b['updated'], $b['id']] <=> [$a['updated'], $a['id']]; });
    return $out;
}
function role_sig($rows) {                                     // what the rows hold, as one number a page can hand back
    $k = [];
    foreach ($rows as $r) $k[] = [(string)($r['SensorID'] ?? ''), (string)($r['SensorAddress'] ?? ''), (string)($r['jsonData'] ?? '')];
    sort($k);
    return intval(sprintf('%u', crc32(json_encode($k))));
}
function role_rows($host, $svc, $var = '', $inst = '') {
    return role_call($host, 'GET', ['entity' => 'sensors', 'action' => 'values', 'parse' => '1'] + role_filter($svc, $var, $inst))['rows'] ?? [];
}
function role_read($host, $svc, $var = '', $inst = '') { return ['ok' => true, 'rows' => role_cells(role_rows($host, $svc, $var, $inst))]; }
function source_ip_toward($host) {
    $h = explode(':', $host)[0];
    $s = @stream_socket_client('udp://' . $h . ':80', $en, $es, 1);   // no packet is sent: this only picks the route
    $me = $s ? explode(':', (string)stream_socket_get_name($s, false))[0] : '';
    if ($s) fclose($s);
    return $me;
}
function role_write($host, $svc, $var, $inst, $bag) {
    $j = role_call($host, 'POST', ['entity' => 'sensor_data', 'action' => 'upsert_by_name'],
                   ['SensorType' => $svc, 'SensorName' => $var . '.' . $inst, 'SensorAddress' => source_ip_toward($host), 'jsonData' => $bag]);
    return json_encode(['ok' => true, 'id' => intval($j['sensor_id'] ?? 0)]);
}
// [HELD_READ_IN_API_V1] a held read from this door: hold at the role's api.php with this request's own socket in
// /run/frognet/held (held_fire.php hands the fire to it); return as soon as the rows are not the ones the page has
// ($seen, role_sig), or at $wait s. No polling: each wake is the fire.
function role_hold_wait($host, $svc, $var, $inst, $seen, $wait) {
    $hid = 'dcdoor.' . getmypid() . '.' . bin2hex(random_bytes(4));
    $path = '/run/frognet/held/' . $hid . '.sock';
    $sock = @stream_socket_server('udg://' . $path, $en, $es, STREAM_SERVER_BIND);
    if (!$sock) fail(503, "the door could not hold a read here ($path: $es)");
    @chmod($path, 0666);
    $end = microtime(true) + $wait;
    try {
        while (true) {
            $j = role_call($host, 'GET', ['entity' => 'held', 'action' => 'hold', 'holder' => source_ip_toward($host), 'hid' => $hid] + role_filter($svc, $var, $inst));
            $rows = $j['rows'] ?? [];
            if (role_sig($rows) !== $seen) return $rows;
            $left = $end - microtime(true);
            if ($left <= 0) return $rows;
            $r = [$sock]; $w = null; $e = null;
            if (!@stream_select($r, $w, $e, intval($left), intval(fmod($left, 1) * 1e6))) return $rows;
            @stream_socket_recvfrom($sock, 65536);                 // the fire: hold again; its reply is the current rows
        }
    } finally { fclose($sock); @unlink($path); }
}
function svc_host($app, $ram) { return $ram === 'general' ? $app . 'Host.frognet' : $ram . 'Host.' . $app; }   // a Team's scoped to its app
$fdf_var = trim((string)shell_exec('. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && printf %s "${FDF_VAR:-/opt/fdf/var}"'));
function station_of($ip) {                                      // [STATION_IS_HOSTNAME_V1] the lease this node gave $ip
    global $fdf_var;
    $f = json_decode((string)@file_get_contents($fdf_var . '/facts.json'), true);
    foreach ((is_array($f) ? ($f['addrs'] ?? []) : []) as $a)
        if (($a['kind'] ?? '') === 'given' && ($a['ip'] ?? '') === $ip && ($a['value'] ?? '') !== '')
            return preg_match('/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/i', $a['value']) ? '' : $a['value'];   // a lease with no name: fdf-facts wrote the MAC
    return '';
}
function for_service($s, $team = null, $soft = false) {         // [APP_DROPLET_V1] the RAM this service is in, now
    global $in_ram, $where, $env, $fdf_var;
    if (!isset($in_ram[$s])) return;
    $key = $in_ram[$s];
    if (substr($key, -5) === '.team') {                         // [TEAM_RAM_V1]
        if (!is_string($team) || !preg_match('/^[A-Za-z0-9_-]{1,40}$/', $team) || $team === 'general') fail(400, "service '$s' is in a team's RAM: name the team");
        $key = substr($key, 0, -4) . $team;
    }
    [$app, $ram] = explode('.', $key, 2);
    $at = served_at($app, $ram);
    if ($soft && ($at === '' || !preg_match('/^[0-9.]+:[0-9]+$/', $at))) { $where = '-'; return; }   // op=watch: held until placed
    if ($at === '' || !preg_match('/^[0-9.]+:[0-9]+$/', $at)) fail(503, "the app's RAM " . $key . ' is not served yet: nothing works until it is');
    $where = $at;
    $env = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && RINA_RAM=' . escapeshellarg($at) . ' exec "$FDF_BIN"/ram ';
}
function ram_try($env, $args) {                                // ram(), with its failure the caller's: [rc, stdout+stderr]
    $cmd = $env; foreach ($args as $a) $cmd .= escapeshellarg((string)$a) . ' ';
    $p = proc_open($cmd, [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    if (!is_resource($p)) fail(502, 'could not run ram');
    $text = stream_get_contents($pipes[1]); $err = stream_get_contents($pipes[2]); fclose($pipes[1]); fclose($pipes[2]);
    return [proc_close($p), $text . $err];
}
// [APP_SERVICES_V1] the app's one configuration tuple, (fdf-app, config, <app>), in databasehost-control: the Admin's
// text decoded, with its seq and admin (salt and check value, never the key); and each Team's, (fdf-app, config,
// <app>.<team>), as "places"; $soft: null when there is none
$cfg_cache = [];
function app_tuple($app) {
    global $cfg_cache;
    if (array_key_exists($app, $cfg_cache)) return $cfg_cache[$app];
    $bag = null; $places = [];
    foreach (array_reverse(role_read(ctl_host(), 'fdf-app', 'config')['rows']) as $c) {     // oldest first
        $i = (string)($c['instance'] ?? ''); $b = $c['bag'] ?? null;
        if (!is_array($b) || !$b) continue;
        if ($i === $app && ($bag === null || intval($b['seq'] ?? 0) > intval($bag['seq'] ?? 0))) $bag = $b;
        elseif (strpos($i, $app . '.') === 0) $places[substr($i, strlen($app) + 1)] = $b;
    }
    $cfg = null;
    if (is_array($bag)) {
        $cfg = json_decode((string)($bag['config'] ?? ''), true);
        if (is_array($cfg)) $cfg = $cfg + ['seq' => intval($bag['seq'] ?? 0), 'admin' => $bag['admin'] ?? (object)[], 'places' => $places, '_bag' => $bag];
        else $cfg = null;
    }
    return $cfg_cache[$app] = $cfg;
}
function app_cfg($app) {
    $cfg = app_tuple($app);
    if (!is_array($cfg)) fail(503, "no configuration of $app in databasehost-control yet: its first install writes it");
    return $cfg;
}
// [APP_SERVICES_V1] where APP's AppRam RAM is served now: its AppHost (/etc/hosts) and its port (the configuration)
function served_at($app, $ram) {
    $ip = host_ip(svc_host($app, $ram));
    if ($ip === '') return '';
    $cfg = app_tuple($app);
    $port = is_array($cfg) ? intval(($cfg['ports'] ?? [])[$ram] ?? 0) : 0;
    return $port ? $ip . ':' . $port : '';
}
function served_map($app) {
    $cfg = app_tuple($app); $o = [];
    foreach ((is_array($cfg) ? ($cfg['rams'] ?? []) : []) as $r) { $w = served_at($app, $r); if ($w !== '') $o[$r] = $w; }
    return $o;
}
// [ENROLL_V1] a user and password, checked against the verifier in the app's configuration: the user's record
// [CHILDREN_JOIN_V1] (John 2026-11-05: "One qr code for the whole team, and one password" ... "Same thing for the eoc. The
// teams aren't allocated beforehand. They are discovered" ... "a generic process applied across the board".) A parent has
// one QR and one password for its children: the app's (the EOC's) is the configuration's "join" verifier, set in the
// admin app (Teams password). A child gives a name of its own -- a team names itself anything it wants -- and the password,
// and is an entity: (fdf-app, child, <app>.<name>) {name, kind, at, from} in databasehost-control, its lead the one who
// joined it, with its own FrogNet invitation. Then the team is a parent the same way (its lead's QR and password).
function verified($v, $pw) { return is_array($v) && hash_equals((string)($v['hash'] ?? ''), hash_pbkdf2('sha256', (string)$pw, hex2bin((string)($v['salt'] ?? '')), intval($v['n'] ?? 1))); }
function children($app) {
    $o = [];
    foreach (array_reverse(role_read(ctl_host(), 'fdf-app', 'child')['rows']) as $c) {      // oldest first: the newest stands
        $i = (string)($c['instance'] ?? ''); $b = $c['bag'] ?? null;
        if (is_array($b) && $b && strpos($i, $app . '.') === 0) $o[substr($i, strlen($app) + 1)] = $b;
    }
    return $o;
}
function child_ok($name) { return is_string($name) && preg_match('/^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$/', $name) && $name !== 'general'; }
function check_user($app, $in) {
    $cfg = app_cfg($app);
    $user = (string)($in['user'] ?? '');
    // [CHILDREN_JOIN_V1] a child the app's join password made: its lead is whoever has that password and the child's name
    if ($user !== '' && !isset($cfg['users'][$user]) && isset(children($app)[$user]) && verified($cfg['join'] ?? null, $in['password'] ?? ''))
        return ['role' => $user, 'lead' => true, 'child' => true];
    $u = $cfg['users'][$user] ?? null; $v = is_array($u) ? ($u['verifier'] ?? null) : null;
    if ($user === '' || !is_array($v) || !hash_equals((string)$v['hash'], hash_pbkdf2('sha256', (string)($in['password'] ?? ''), hex2bin((string)$v['salt']), intval($v['n'])))) {
        // [REFUSAL_SAYS_WHAT_IT_GOT_V1] what the door was given and what it checked against -- never the password, only
        // its kind and length -- so a refusal can be told apart: the wrong password, a user the configuration does not
        // have, or a body that arrived changed on the way (keys missing, a password that is not a string)
        $pw = $in['password'] ?? null;
        error_log('dc ram.php: [REFUSAL_SAYS_WHAT_IT_GOT_V1] ' . $app . ' refused user ' . json_encode($user) . ' from ' . ($_SERVER['REMOTE_ADDR'] ?? '?')
            . ': body keys ' . json_encode(array_keys($in)) . ', password ' . gettype($pw) . (is_string($pw) ? ' of ' . strlen($pw) . ' chars' : '')
            . ', ' . (is_array($u) ? 'the user is in configuration ' . intval($cfg['seq'] ?? 0) . ' (role ' . ($u['role'] ?? '?') . ($v ? '' : ', NO verifier') . ')'
                                  : 'configuration ' . intval($cfg['seq'] ?? 0) . ' has no such user (it has ' . json_encode(array_keys($cfg['users'] ?? [])) . ')')
            . '; ' . ($_SERVER['HTTP_X_FROGNET_ORIGIN'] ?? '') . ' via ' . ($_SERVER['HTTP_VIA'] ?? 'no Via header'));
        fail(403, "that user and password are not this app's");
    }
    return $u;
}
// [ENROLL_V1] an identity's name in the region: the parts joined by '-', what fdf-identity takes (letters, digits, . _ -)
function ident_name($parts) {
    $n = strtolower(implode('-', array_map(function ($p) { return preg_replace('/[^A-Za-z0-9._-]/', '_', (string)$p); }, $parts)));
    return substr($n, 0, 63);
}
// [ENROLL_V1] the region this node is in makes NAME's identity: its hub admits a new key at an edge of its own (fdf-identity:
// the ask and the answer through the region's RAM; this node reaching the region is the trust). The fdfinv1 line.
// [IDENTITY_ASK_IS_SAID_V1] (found 2026-11-02 on Seattle7: an enrollment sat 60 s and nothing in any log said where the
// identity was asked or why it failed) the ask is logged when it starts and when it ends, with fdf-identity's own words
// (where it asked; the answer or why not) and how long it took. [IDENTITY_WAIT_IS_SAID_V1] it waits IDENTITY_WAIT_S,
// less than its callers wait (the phone 60 s, the node's proxy 60 s on Apache), so the reason reaches the phone.
const IDENTITY_WAIT_S = 40;
function region_identity($name) {
    $cmd = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && exec python3 "$FDF_BIN"/fdf-identity ' . escapeshellarg($name) . ' --leaf --again --line --wait=' . IDENTITY_WAIT_S;
    $t0 = microtime(true);
    error_log('dc ram.php: [IDENTITY_ASK_IS_SAID_V1] ' . DOOR_BUILD . ': asking the region for ' . $name . ' (waiting at most ' . IDENTITY_WAIT_S . ' s)');
    $p = proc_open($cmd, [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    if (!is_resource($p)) { error_log('dc ram.php: [IDENTITY_ASK_IS_SAID_V1] could not run fdf-identity'); fail(502, 'could not run fdf-identity'); }
    $out = trim(stream_get_contents($pipes[1])); $err = trim(stream_get_contents($pipes[2])); fclose($pipes[1]); fclose($pipes[2]);
    $rc = proc_close($p);
    $took = sprintf('%.1f s', microtime(true) - $t0);
    $said = str_replace("\n", ' | ', $err);
    if ($rc !== 0 || strpos($out, 'fdfinv1.') !== 0) {
        error_log('dc ram.php: [IDENTITY_ASK_IS_SAID_V1] ' . $name . ': NO identity after ' . $took . ' (exit ' . $rc . '): ' . ($said !== '' ? $said : $out));
        fail(502, 'the region did not give ' . $name . ' an identity in ' . $took . ': ' . ($err !== '' ? $err : $out));
    }
    error_log('dc ram.php: [IDENTITY_ASK_IS_SAID_V1] ' . $name . ': identity in ' . $took . ': ' . $said);
    return $out;
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
    // [ONE_HELD_READ_PER_PAGE_V1][ONE_FOLLOW_PER_FAMILY_V1] one web server worker per page and ONE child: `ram watch`
    // holds a follow on every family the page shows (one session per RAM) and answers with the first rows any is given.
    // No tick: the hold is the page's W seconds, and nothing is asked again inside it. A family whose RAM did not
    // answer is held until that RAM says it is up ("up": the page reads it again in full); ESTALE (another memory than
    // the page read: the RAM started again) is answered as a move, so the page reads it again in full there.
    $w = json_decode((string)($_GET['w'] ?? ''), true);
    if (!is_array($w) || count($w) < 1 || count($w) > 16) fail(400, 'w: 1 to 16 families');
    $wait = max(0, min(25, intval($_GET['wait'] ?? 8)));
    $where0 = $where; $env0 = $env; $got = []; $args = ['watch', $wait]; $at = [];
    foreach (array_values($w) as $k => $f) {
        $s = $f['s'] ?? ''; $v = $f['v'] ?? '';
        if (!isset($allowed[$s])) fail(403, "service '$s' is not an installed add-in's");
        if (!name_ok($v)) fail(400, 'variable needed');
        $where = $where0; $env = $env0; for_service($s, $f['team'] ?? null, true);   // '-': the app's RAM is not placed yet
        if (isset($f['ram']) && $f['ram'] !== '' && $where !== '-' && $f['ram'] !== $where) { $got[] = ['k' => $k, 'rows' => [], 'ram' => $where]; continue; }
        $at[count($at)] = ['k' => $k, 'ram' => $where];
        array_push($args, $where, $s, $v, intval($f['after'] ?? 0));
    }
    if ($got === [] && $at !== []) {
        $cmd = '. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && exec "$FDF_BIN"/ram ';
        foreach ($args as $a) $cmd .= escapeshellarg((string)$a) . ' ';
        $p = proc_open($cmd, [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
        if (!is_resource($p)) fail(502, 'could not run ram');
        $text = stream_get_contents($pipes[1]); $err = stream_get_contents($pipes[2]); fclose($pipes[1]); fclose($pipes[2]); proc_close($p);
        $j = json_decode($text, true);
        if (!is_array($j)) fail(502, 'ram watch failed', trim($err . "\n" . $text));
        if (empty($j['ok'])) {                                  // [NOT_THIS_MEMORY_V1] a follow that ended: read it again in full
            $f = $at[intval($j['k'] ?? 0)];
            if (strpos((string)($j['why'] ?? ''), 'ESTALE') === false) error_log('dc ram.php: ram watch: ' . ($j['why'] ?? '?'));
            $got[] = ['k' => $f['k'], 'rows' => [], 'ram' => $f['ram'], 'again' => true];
        }
        foreach ($j['got'] ?? [] as $g) { $f = $at[intval($g['k'])]; $o = ['k' => $f['k'], 'rows' => $g['rows'], 'ram' => $f['ram']]; if (!empty($g['up'])) $o['again'] = true; $got[] = $o; }
        $down = []; foreach ($j['down'] ?? [] as $k) $down[] = $at[intval($k)]['k'];
        echo json_encode(['ok' => true, 'got' => $got] + ($down ? ['down' => $down] : []), JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        exit;
    }
    echo json_encode(['ok' => true, 'got' => $got], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
} elseif ($op === 'rams') {                                    // [TEAM_RAM_V1] the app's AppRams served now, and where
    // [GROUP_PLACE_V1] &until=RAM&wait_s=S: held until RAM has an AppHost (the election writing /etc/hosts is the event),
    // at most S s (25). "places": where each Team's lead placed it (its own configuration tuple).
    $app = $_GET['app'] ?? '';
    if (!preg_match('/^[A-Za-z0-9_-]{1,40}$/', $app)) fail(400, 'app needed');
    // [EVENTS_NOT_TIMERS_V1] held (at most wait_s, 25) on the one event that changes the answer: /etc/hosts written
    // (an AppHost elected), or -- &up=HOST:PORT,.. -- one of those RAMs saying it is up (FDF_VAR/ram/<host>_<port>.up).
    // &until=RAM: until that RAM is served. &not=JSON: until the RAMs are not that map.
    $until = (string)($_GET['until'] ?? ''); $wait = max(0, min(25, intval($_GET['wait_s'] ?? 0)));
    $not = isset($_GET['not']) ? json_decode((string)$_GET['not'], true) : null;
    if ($until !== '' && !preg_match('/^[A-Za-z0-9_-]{1,40}$/', $until)) fail(400, 'until: a RAM name');
    $ups = []; foreach (array_filter(explode(',', (string)($_GET['up'] ?? ''))) as $u) {
        if (!preg_match('/^([0-9.]+):([0-9]+)$/', $u, $mm)) fail(400, 'up: HOST:PORT,..');
        $ups[$fdf_var . '/ram/' . $mm[1] . '_' . $mm[2] . '.up'] = $u; $ups[$fdf_var . '/ram/0.0.0.0_' . $mm[2] . '.up'] = $u;
    }
    $since = microtime(true); $out = served_map($app); $up = '';
    $done = function ($o) use ($until, $not) { return ($until === '' || isset($o[$until])) && (!is_array($not) || $o != $not); };
    if (($until !== '' || is_array($not)) && $wait > 0) {
        $end = $since + $wait;
        while (!$done($out) && ($left = $end - microtime(true)) > 0) {
            $args = ['written', sprintf('%.3f', $left), sprintf('%.6f', $since), trim($hostenv[0] ?? '/etc/hosts')]; foreach ($ups as $f => $u) $args[] = $f;
            $w = (string)(json_decode(ram($env, $args), true)['written'] ?? '');
            if ($w === '') break;                               // the wait's bound: the same answer
            if (isset($ups[$w])) { $up = $ups[$w]; break; }
            $since = microtime(true); $cfg_cache = []; $out = served_map($app);
        }
    }
    $cfg = app_tuple($app);
    // [DOOR_SAYS_ITS_NODE_V1] which node this door is (its hostname: the name its sensors are filed under, Seattle7.GPS.Position):
    // every node's LAN door is 10.170.170.1, so an app on a node's LAN cannot tell its node by the address
    echo json_encode(['ok' => true, 'rams' => (object)$out, 'places' => (object)(is_array($cfg) ? $cfg['places'] : []), 'door' => DOOR_BUILD, 'node' => gethostname()] + door_sensors() + ($up !== '' ? ['up' => $up] : []));
} elseif ($op === 'roles' || ($op === 'admin' && $_SERVER['REQUEST_METHOD'] === 'GET')) {
    $app = $_GET['app'] ?? '';
    if (!preg_match('/^[A-Za-z0-9_-]{1,40}$/', $app)) fail(400, 'app needed');
    $best = app_cfg($app);
    $served = served_map($app);
    if ($op === 'roles') {
        // [CATEGORIES_V1] each role (a group) says its category; `categories` is the admin's order of them (the tabs, the map's layers)
        $roles = []; $cats = array_values(array_filter($best['categories'] ?? [], 'is_string'));
        foreach (($best['roles'] ?? []) as $r => $x) {
            $c = (is_string($x['category'] ?? null) && $x['category'] !== '') ? $x['category'] : 'Teams';
            if (!in_array($c, $cats, true)) $cats[] = $c;
            $roles[$r] = ['default' => !empty($x['default']), 'ram' => in_array($r, $best['rams'] ?? [], true), 'category' => $c];
        }
        // [CHILDREN_JOIN_V1] the teams that joined: teams (their RAM is their lead's)
        foreach (children($app) as $n => $b) if (!isset($roles[$n])) $roles[$n] = ['default' => false, 'ram' => true, 'category' => 'Teams', 'child' => true, 'kind' => (string)($b['kind'] ?? 'team'), 'at' => intval($b['at'] ?? 0)];
        echo json_encode(['ok' => true, 'door' => DOOR_BUILD, 'node' => gethostname(), 'roles' => $roles, 'categories' => $cats, 'rams' => $best['rams'] ?? [], 'served' => (object)$served, 'login' => !empty($best['login']), 'seq' => intval($best['seq'] ?? 0)], JSON_UNESCAPED_SLASHES);
        exit;
    }
    // the admin's view: held until an installed machine answers, its capability comes or goes, or the configuration
    // changes (or W s). [HELD_READ_IN_API_V1] one held read on the service fdf-app at the SystemRam's api.php: `after`
    // is what the page has (the `last` this answer gives: a number for what the family holds, not a place in it)
    if (isset($_GET['after'])) {
        $wait = max(0, min(25, intval($_GET['wait'] ?? 20)));
        $rows = role_hold_wait(sys_host(), 'fdf-app', '', '', intval($_GET['after']), $wait);
        $cfg_cache = []; $best = app_cfg($app); $served = served_map($app);
    } else {
        $rows = role_rows(sys_host(), 'fdf-app');
    }
    $all = role_cells($rows);
    $last = role_sig($rows);
    // one row per installed machine (its capability tuples), each showing the one configuration; its answer in applied
    $pub = $best; unset($pub['_bag']); $machines = [];
    foreach ($all as $c) {
        if (($c['variable'] ?? '') !== 'can' || strpos($c['instance'] ?? '', $app . '.') !== 0 || !is_array($c['bag'] ?? null)) continue;
        $g = (string)($c['bag']['guid'] ?? ''); if ($g === '' || isset($machines[$g])) continue;
        $machines[$g] = $pub + ['guid' => $g, 'member' => (string)($c['bag']['member'] ?? $g), 'addr' => (string)($c['bag']['addr'] ?? '')];
    }
    $applied = [];
    foreach ($all as $c) if (($c['variable'] ?? '') === 'applied' && strpos($c['instance'] ?? '', $app . '.') === 0 && is_array($c['bag'] ?? null)) $applied[] = $c['bag'];
    $configs = array_values($machines) ?: [$pub + ['guid' => '', 'member' => 'databasehost-control']];
    echo json_encode(['ok' => true, 'door' => DOOR_BUILD, 'configs' => $configs, 'applied' => $applied, 'served' => (object)$served, 'last' => $last,
                      'children' => (object)children($app), 'join' => is_array($best['join'] ?? null)], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
} elseif ($op === 'admin' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    // [APP_SERVICES_V1] the Admin's configuration: the app's one tuple in databasehost-control, written by the Admin (the
    // admin app, through this door); the admin's salt and check value kept with it (never the key)
    $in = json_decode(file_get_contents('php://input'), true);
    $app = $_GET['app'] ?? '';
    if (!is_array($in) || !preg_match('/^[A-Za-z0-9_-]{1,40}$/', $app)) fail(400, 'app and a JSON body needed');
    if (!is_string($in['config'] ?? null) || strlen($in['config']) > 200000 || !is_int($in['seq'] ?? null) || !preg_match('/^[0-9a-f]{64}$/', (string)($in['mac'] ?? '')))
        fail(400, 'config (text), seq (integer) and mac (hex) needed');
    $cur = app_cfg($app);
    if ($in['seq'] <= intval($cur['seq'] ?? 0)) fail(409, 'seq ' . $in['seq'] . ' is not newer than the configuration in databasehost-control (' . intval($cur['seq'] ?? 0) . ')');
    $bag = ['config' => $in['config'], 'seq' => $in['seq'], 'user' => (string)($in['user'] ?? ''), 'mac' => $in['mac'], 'admin' => $cur['admin'] ?? (object)[], 'at' => time()];
    echo role_write(ctl_host(), 'fdf-app', 'config', $app, $bag);
} elseif ($op === 'whoami') {
    // [STATION_IS_HOSTNAME_V1] the asking device's host name, from its lease here; '' when it gave none (the device's
    // own name is then its name: the page asks for it). Nobody sees a station ID: it is the host or device name.
    $ip = $_SERVER['REMOTE_ADDR'] ?? '';
    echo json_encode(['ok' => true, 'host' => station_of($ip), 'ip' => $ip]);
} elseif (($op === 'enroll' || $op === 'identity') && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $in = json_decode(file_get_contents('php://input'), true);
    $app = $_GET['app'] ?? '';
    if (!is_array($in) || !preg_match('/^[A-Za-z0-9_-]{1,40}$/', $app)) fail(400, 'app and a JSON body needed');
    // [CHILDREN_JOIN_V1] a name the configuration does not have, with the app's join password: a new child (a team, named
    // by whoever joins it), or the one of that name again
    $cfg = app_cfg($app); $nm = (string)($in['user'] ?? '');
    if ($op === 'enroll' && $nm !== '' && !isset($cfg['users'][$nm]) && is_array($cfg['join'] ?? null)) {
        if (!child_ok($nm)) fail(400, "a team's name: letters, digits, space . _ -, at most 40 (not general)");
        if (!verified($cfg['join'], $in['password'] ?? '')) fail(403, "that is not the EOC's teams password");
        $kids = children($app);
        if (!isset($kids[$nm])) {
            $b = ['name' => $nm, 'kind' => (string)($in['kind'] ?? 'team') ?: 'team', 'at' => time(), 'from' => $_SERVER['REMOTE_ADDR'] ?? ''];
            role_write(ctl_host(), 'fdf-app', 'child', $app . '.' . $nm, $b);
            error_log('dc ram.php: [CHILDREN_JOIN_V1] ' . $app . ': ' . $b['kind'] . ' ' . $nm . ' joined (' . $b['from'] . ')');
        }
        echo json_encode(['ok' => true, 'user' => $nm, 'role' => $nm, 'lead' => true, 'child' => true, 'at' => '',
                          'invite' => region_identity(ident_name([$app, $nm])), 'door' => DOOR_BUILD], JSON_UNESCAPED_SLASHES);
        exit;
    }
    $u = check_user($app, $in);
    $role = (string)$u['role'];
    if ($op === 'identity') {
        if (empty($u['lead'])) fail(403, "user " . $in['user'] . " does not lead a team: only a lead asks for its members' invitations");
        $who = (string)($in['name'] ?? '');
        if (!preg_match('/^[A-Za-z0-9._-]{1,30}$/', $who)) fail(400, 'name: letters, digits, . _ -, at most 30');
        echo json_encode(['ok' => true, 'name' => $who, 'team' => $role, 'invite' => region_identity(ident_name([$app, $role, $who]))], JSON_UNESCAPED_SLASHES);
        exit;
    }
    echo json_encode(['ok' => true, 'user' => (string)$in['user'], 'role' => $role, 'lead' => !empty($u['lead']),
                      'invite' => region_identity(ident_name([$app, (string)$in['user']])), 'door' => DOOR_BUILD], JSON_UNESCAPED_SLASHES);
} elseif ($op === 'login' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    // [APP_ACCESS_V1] login is a read, here at the device's own edge: the app's access definition is in its configuration
    // tuple in databasehost-control (the Admin's one), and the password is checked against the verifier there. No
    // login server and nothing sent to another node. The answer is the role; the device writes its own access record.
    $in = json_decode(file_get_contents('php://input'), true);
    $app = $_GET['app'] ?? '';
    if (!is_array($in) || !preg_match('/^[A-Za-z0-9_-]{1,40}$/', $app)) fail(400, 'app and a JSON body needed');
    $cfg = app_cfg($app);
    $user = (string)($in['user'] ?? ''); $role = (string)($in['role'] ?? '');
    if ($user !== '' && !isset($cfg['users'][$user]) && isset(children($app)[$user])) {
        // [CHILDREN_JOIN_V1] a team that joined, its lead logging in with the EOC's teams password: the team is its role
        if (!verified($cfg['join'] ?? null, $in['password'] ?? '')) fail(403, "that user and password are not this app's");
        $role = $user;
    } elseif ($user !== '') {
        $u = $cfg['users'][$user] ?? null; $v = is_array($u) ? ($u['verifier'] ?? null) : null;
        if (!is_array($v) || !hash_equals((string)$v['hash'], hash_pbkdf2('sha256', (string)($in['password'] ?? ''), hex2bin((string)$v['salt']), intval($v['n']))))
            fail(403, "that user and password are not this app's");
        $role = (string)$u['role'];
    } elseif ($role !== '') {
        if (empty($cfg['roles'][$role]['default'])) fail(403, "role $role is activated at its user's login");
    } elseif (!empty($cfg['login'])) fail(403, 'this app requires login');
    echo json_encode(['ok' => true, 'role' => $role, 'user' => $user, 'station' => station_of($_SERVER['REMOTE_ADDR'] ?? '')]);
} elseif ($op === 'place' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    // [GROUP_PLACE_V1] (John 2026-11-03: "The Lead should be able to say that the region lives on databasehost.frognet")
    // the lead says where its group's RAM lives: "here", on the lead's own device (named by its guid: the group goes
    // where the lead goes, and members who cannot reach it fail), or "databasehost", wherever the app's selection puts
    // it (it stays when the lead leaves). Checked here against the verifier, and written as the Team's own configuration
    // tuple, (fdf-app, config, <app>.<team>), in databasehost-control: its owner is its lead. The election reads it.
    $in = json_decode(file_get_contents('php://input'), true);
    $app = $_GET['app'] ?? '';
    if (!is_array($in) || !preg_match('/^[A-Za-z0-9_-]{1,40}$/', $app)) fail(400, 'app and a JSON body needed');
    $u = check_user($app, $in);
    if (empty($u['lead'])) fail(403, 'user ' . $in['user'] . ' does not lead a group: only its lead says where it lives');
    $role = (string)$u['role']; $cfg = app_cfg($app);
    if (!empty($u['child']) && ($in['at'] ?? '') === 'databasehost') fail(400, "team $role joined the EOC: it lives on its lead's phone (Host team); the SystemRam serves the RAMs the admin configured");
    if (empty($u['child']) && !in_array($role, $cfg['rams'] ?? [], true)) fail(400, "group $role has no RAM of its own (the app's RAMs: " . implode(',', $cfg['rams'] ?? []) . ')');
    $at = (string)($in['at'] ?? ''); $guid = (string)($in['guid'] ?? '');
    if ($at !== 'here' && $at !== 'databasehost') fail(400, "at: here (the lead's device, named by its guid) or databasehost");
    if ($at === 'here' && !preg_match('/^[A-Za-z0-9._:-]{1,80}$/', $guid)) fail(400, "guid: the lead's device that serves the group");
    $bag = ['at' => $at, 'guid' => $at === 'here' ? $guid : '', 'user' => (string)$in['user'], 'role' => $role,
            'seq' => (int)(microtime(true) * 1000), 'from' => $_SERVER['REMOTE_ADDR'] ?? ''];
    role_write(ctl_host(), 'fdf-app', 'config', $app . '.' . $role, $bag);
    error_log('dc ram.php: [GROUP_PLACE_V1] ' . $app . ' group ' . $role . ' placed ' . $at . ($bag['guid'] !== '' ? ' (' . $bag['guid'] . ')' : '') . ' by its lead ' . $bag['user']);
    echo json_encode(['ok' => true, 'place' => $bag, 'door' => DOOR_BUILD], JSON_UNESCAPED_SLASHES);
} elseif ($op === 'device' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    // [APP_ACCESS_V1] the device's own access record (its role, its user), written by the device after its login and
    // again after every RAM change, in the app's general AppRam (a client's own record): (fdf-app, device, <app>.<dev>)
    $in = json_decode(file_get_contents('php://input'), true);
    $app = $_GET['app'] ?? ''; $dev = (string)($in['dev'] ?? '');
    if (!preg_match('/^[A-Za-z0-9_-]{1,40}$/', $app) || !preg_match('/^[A-Za-z0-9_-]{1,40}$/', $dev)) fail(400, 'app and dev needed');
    $bag = ['dev' => $dev, 'station' => preg_replace('/[^a-z0-9-]/', '', strtolower((string)($in['station'] ?? ''))), 'role' => (string)($in['role'] ?? ''), 'user' => (string)($in['user'] ?? ''), 'at' => time()];
    $at = served_at($app, 'general');
    if ($at === '') fail(503, "the app's general AppRam has no AppHost yet (" . svc_host($app, 'general') . "): nothing works until it has");
    echo ram('. ' . escapeshellarg(dirname(__DIR__, 2) . '/fdf.env') . ' && RINA_RAM=' . escapeshellarg($at) . ' exec "$FDF_BIN"/ram ', ['write', 'fdf-app', 'device', $app . '.' . $dev, json_encode($bag, JSON_UNESCAPED_SLASHES)]);
} else {
    fail(400, 'op is read, write, watch, announce, rams, roles, admin, whoami, login, device, enroll, identity or place');
}
