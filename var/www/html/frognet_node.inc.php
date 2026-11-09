<?php
/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
/**
 * /var/www/html/frognet_node.inc.php -- [ONE_MONITOR_TRUTH_V1] what this node says about itself, read once per
 * request, each field from ONE source. getSnapshot.php serves all of it; getHosts.php (its host rows), frognet_echo.php
 * (its own echo) and index.php (name, mode, build) call the same functions, so no page computes a field its own way.
 * Defines functions only; requesting this file prints nothing.
 *
 *   field        region mode (/opt/fdf/var/join.env names RINA_RAM)      LAN-only mode
 *   mode         join.env, the one switch [REGION_SWITCH_ONE_FILE_V1]      (the same file, absent or without RINA_RAM)
 *   name, .1     /opt/fdf/var/facts.json (what fdf-client announces)      getFrogNet.bash (this node's echo)
 *   guid         /etc/fnid                                                /etc/fnid
 *   build        /opt/frognet_semantic/BUILD (build=, commit=, installed=)  the same
 *   members      /var/lib/frognet/region_hosts.json (the daemon's applied  makeHostJson.bash (the merge's /etc/hosts:
 *                plan; alive = the member's region session this event)     alive = named by the last merge, HOSTS_GATE)
 *   ponds        /opt/fdf/var/ponds/<area>.ram + forward, or the one pond  none
 *                (each with `telem`: its devices' (pond, telem) tuples [TELEMETRY_IS_A_TUPLE_V1])
 *                join.env + facts.json `pond` name; a participant pond's members and
 *                any pond's config from that pond's RAM manager, live
 *   hub          facts.json `hub` rows (the region edge iface) + the       none
 *                kernel's latest handshake (`wg show all latest-handshakes`,
 *                sudoers: /etc/sudoers.d/frognet-snapshot)
 *   elections    /etc/hosts <role>.frognet lines (what every component     the same
 *                routes by; never the resolver)
 * A source that cannot be read is said in `errors` and its field is empty: nothing stands in for it.
 *
 * FROGNET_SNAPSHOT_ROOT (tests only) prefixes every path; FROGNET_SNAPSHOT_WG replaces the wg command.
 */

function fn_path($p) { return (string)getenv('FROGNET_SNAPSHOT_ROOT') . $p; }

function fn_env_value($file, $key) {
    $t = @file_get_contents(fn_path($file));
    if ($t === false) return null;
    return preg_match('/^(export )?' . preg_quote($key, '/') . '=(\S+)/m', $t, $m) ? $m[2] : null;
}

// mode: the region switch is /opt/fdf/var/join.env's RINA_RAM, nothing else [REGION_SWITCH_ONE_FILE_V1]
function fn_mode() {
    $f = '/opt/fdf/var/join.env';
    if (!is_file(fn_path($f))) return ['mode' => 'lan-only', 'why' => "no $f"];
    $ram = fn_env_value($f, 'RINA_RAM');
    if ($ram === null) return ['mode' => 'lan-only', 'why' => "$f names no RINA_RAM"];
    return ['mode' => 'region', 'why' => "$f RINA_RAM=$ram", 'ram' => $ram];
}

function fn_build(&$errors) {
    $f = '/opt/frognet_semantic/BUILD';
    $t = @file_get_contents(fn_path($f));
    if ($t === false) { $errors[] = "$f: cannot be read"; return ['build' => '']; }
    $b = ['build' => ''];
    foreach (preg_split('/\r?\n/', $t) as $ln) {
        if (preg_match('/^([a-z_]+)=(.*)$/', trim($ln), $m)) $b[$m[1]] = $m[2];
    }
    if ($b['build'] === '') $errors[] = "$f names no build=";
    return $b;
}

function fn_guid(&$errors) {
    $t = @file_get_contents(fn_path('/etc/fnid'));
    if ($t === false || trim($t) === '') { $errors[] = '/etc/fnid: absent or empty (frognet-node-guid.sh --ensure)'; return ''; }
    return trim($t);
}

function fn_facts() {
    $f = json_decode((string)@file_get_contents(fn_path('/opt/fdf/var/facts.json')), true);
    return is_array($f) ? $f : null;
}

// This node's echo line, "<name>,<.1>,<upstream1>,<upstream2>" -- what frognet_echo.php answers [ECHO_FROM_THE_REGION_V1]
function fn_self_echo($mode) {
    if ($mode['mode'] === 'region') {
        $facts = fn_facts();
        if ($facts === null) return '';                     // no facts yet: no echo (not a member yet)
        $dot1 = '';
        foreach (($facts['addrs'] ?? []) as $r) {
            if (($r['kind'] ?? '') === 'own' && substr((string)($r['ip'] ?? ''), -2) === '.1') { $dot1 = $r['ip']; break; }
        }
        // [ECHO_UPSTREAM_FROM_THE_ROWS_V1] fields 3 and 4: the facts' `uplink` rows in device order, 0.0.0.0 where none
        $ups = [];
        foreach (($facts['addrs'] ?? []) as $r) {
            if (($r['kind'] ?? '') === 'uplink' && ($r['ip'] ?? '') !== '') $ups[] = $r['ip'];
        }
        $ups[] = '0.0.0.0'; $ups[] = '0.0.0.0';
        return ($facts['name'] ?? '') . ',' . $dot1 . ',' . $ups[0] . ',' . $ups[1];
    }
    return trim((string)shell_exec(escapeshellarg(fn_path('/usr/local/bin/getFrogNet.bash')) . ' 2>/dev/null'));
}

// The host rows getHosts.php has always served: [{ip, name, echo, role, alive, guid}] (FILTER RULES in getHosts.php).
// Region: the daemon's region_hosts.json [HOSTJSON_FROM_THE_REGION_V1]; LAN-only: makeHostJson.bash (the merge's
// /etc/hosts). One source per mode, never both.
function fn_host_rows($mode, &$errors) {
    if ($mode['mode'] === 'region') {
        $src = '/var/lib/frognet/region_hosts.json';
        $json = @file_get_contents(fn_path($src));
        if ($json === false) { $errors[] = "$src: absent (the daemon writes it on its first region event)"; $json = ''; }
    } else {
        $src = '/usr/local/bin/makeHostJson.bash';
        $json = (string)shell_exec(escapeshellarg(fn_path($src)) . ' 2>/dev/null');
        if (trim($json) === '') $errors[] = "$src: no output";
    }
    $raw = trim($json) === '' ? [] : json_decode($json, true);
    if (!is_array($raw)) { $errors[] = "$src: not a JSON array"; $raw = []; }
    $hosts = []; $seen = [];
    foreach ($raw as $entry) {
        $ip = $entry['ip'] ?? '';
        if ($ip === '' || isset($seen[$ip])) continue;
        if (!preg_match('/^10\./', $ip))     continue;     // FrogNet space,
        if (preg_match('/^10\.253\./', $ip)) continue;     // not tunnel transit,
        if (preg_match('/^10\.254\./', $ip)) continue;     // not chorus virtual,
        if (preg_match('/\.2$/', $ip))       continue;     // not the probe-only .2 admin alias
        $echo = (string)($entry['echo'] ?? '');
        $name = $echo !== '' ? explode(',', $echo)[0] : '';
        if ($name === '' && !empty($entry['hostname'])) $name = $entry['hostname'];
        if (strpos($name, 'FrogNetAdmin.') === 0) continue;
        if (strpos((string)($entry['hostname'] ?? ''), 'FrogNetAdmin.') === 0) continue;
        if (strpos($name, 'FrogNetHost.') === 0) $name = substr($name, strlen('FrogNetHost.'));
        if ($name === '') $name = 'unknown';
        $seen[$ip] = true;
        // [DEVICE_ROW_V1] [ONE_ALIVE_V1] role and alive are decided HERE, once per mode, and getSnapshot.php's members
        // carry these same values: region = the member's role and whether its region session is up this event (the
        // daemon's row); LAN-only = a node, alive because the last merge named it (makeHostJson.bash lists nothing else).
        $region = $mode['mode'] === 'region';
        $hosts[] = [
            'ip'    => $ip,
            'name'  => $name,
            'echo'  => $echo,
            'role'  => $region ? ((string)($entry['role'] ?? '') !== '' ? (string)$entry['role'] : 'node') : 'node',
            'alive' => $region ? (($entry['alive'] ?? false) === true) : true,
            'guid'  => (string)($entry['guid'] ?? ''),
        ];
    }
    return $hosts;
}

// <role>.frognet -> ip, from /etc/hosts only (the file every component routes by) [DBHOST_IS_MARKED_V1]
function fn_elections(&$errors) {
    $t = @file_get_contents(fn_path('/etc/hosts'));
    if ($t === false) { $errors[] = '/etc/hosts: cannot be read'; return (object)[]; }
    $e = [];
    foreach (preg_split('/\r?\n/', $t) as $ln) {
        $f = preg_split('/\s+/', trim(explode('#', $ln, 2)[0]));
        if (count($f) < 2 || strpos($f[0], '10.') !== 0) continue;
        foreach (array_slice($f, 1) as $n) {
            $n = strtolower($n);
            if (substr($n, -8) === '.frognet' && !isset($e[substr($n, 0, -8)])) $e[substr($n, 0, -8)] = $f[0];
        }
    }
    ksort($e);
    return $e ? $e : (object)[];
}

const FN_RAM_API = '/FawcettInnovations.RINA.ram_interface.php';

function fn_ram_call($ram, $method, $q, $body = null) {
    $ctx = stream_context_create(['http' => ['method' => $method, 'timeout' => 2, 'ignore_errors' => true,
        'header' => "Content-Type: application/json\r\n", 'content' => $body === null ? '' : json_encode($body)]]);
    $t = @file_get_contents('http://' . $ram . FN_RAM_API . $q, false, $ctx);
    if ($t === false) return null;
    $j = json_decode($t, true);
    return is_array($j) ? $j : null;
}

// a member's address in its pond: a node's .1 (the /24 it serves), a leaf's or a reacher's given /32
function fn_member_ip($f) {
    $role = $f['role'] ?? '';
    if ($role === 'leaf' || $role === 'participant') return explode('/', (string)($f['addr'] ?? ''))[0];
    foreach (($f['addrs'] ?? []) as $r) {
        $ip = (string)($r['ip'] ?? '');
        if (($r['kind'] ?? '') === 'own' && strpos($ip, '10.') === 0 && substr($ip, -2) === '.1') return $ip;
    }
    if (!empty($f['subnet'])) { $b = explode('/', $f['subnet'])[0]; return substr($b, 0, strrpos($b, '.')) . '.1'; }
    return '';
}

// [PONDS_ARE_LISTED_V1] every pond this node is in. A node with ponds/ (fdf-pond-add) lists ponds/<area>.ram and
// marks /opt/fdf/var/forward; a node joined the single-pond way (fdf-region-join: join.env + site.env, no ponds/) is
// in exactly that one pond, forward. The FORWARD pond's members are the snapshot's `members` (the daemon's applied
// plan), so its entry carries none; a participant pond's members come from its own RAM manager, live (nothing else
// knows them). Each pond's published config comes from its RAM manager.
function fn_ponds($mode, $guid, &$errors) {
    if ($mode['mode'] !== 'region') return [];
    $V = '/opt/fdf/var';
    $ponds = []; $src = [];
    foreach ((glob(fn_path("$V/ponds/*.ram")) ?: []) as $f) {
        $a = basename($f, '.ram'); $ponds[$a] = trim((string)@file_get_contents($f)); $src[$a] = "$V/ponds/$a.ram";
    }
    if ($ponds) {
        $forward = trim((string)@file_get_contents(fn_path("$V/forward")));
        if ($forward === '') $errors[] = "$V/forward: absent or empty (no pond is marked forward)";
    } else {
        // the area: facts.json `pond`, the daemon's RINA_AREA (it reads site.env as root; site.env holds the site
        // key, mode 0600, so www-data cannot read it)
        $facts = fn_facts();
        $forward = (string)($facts['pond'] ?? '');
        if ($forward === '') $errors[] = "$V/facts.json names no pond (the daemon writes it from site.env RINA_AREA)";
        $ponds[$forward] = $mode['ram']; $src[$forward] = "$V/join.env + facts.json pond";
    }
    $me = 'w:' . $guid;
    $out = [];
    foreach ($ponds as $area => $ram) {
        $p = ['area' => (string)$area, 'ram' => $ram, 'forward' => (string)$area === $forward, 'source' => $src[$area]];
        if (!$p['forward']) {
            $p['members'] = [];
            $w = fn_ram_call($ram, 'POST', '?op=rina', ['op' => 'world']);
            if (!$w || empty($w['ok'])) { $p['error'] = "its RAM manager at $ram did not answer"; $errors[] = "pond $area: " . $p['error']; $out[] = $p; continue; }
            foreach (($w['participants'] ?? []) as $q) {
                $f = is_array($q['facts'] ?? null) ? $q['facts'] : [];
                $role = (string)($f['role'] ?? 'node');
                if ($role === 'participant' && !empty($f['reach'])) $role = 'reacher';
                $m = ['name' => (string)($q['name'] ?? ''), 'role' => $role, 'alive' => (bool)($q['alive'] ?? false),
                      'ip' => fn_member_ip($f), 'guid' => (string)($f['guid'] ?? ''), 'source' => "pond $area"];
                if (($q['ns'] ?? '') === $me) { $m['me'] = true; $p['me'] = ['role' => $role, 'alive' => $m['alive'], 'addr' => $m['ip']]; }
                $p['members'][] = $m;
            }
            usort($p['members'], function ($a, $b) { return strcmp($a['name'], $b['name']); });
        }
        // [TELEMETRY_IS_A_TUPLE_V1] what each device in this pond reports: (pond, telem, <its ns>), the member's own tuple
        // (gone with its session); the newest non-empty cell per ns, oldest id first so a later removal ({} ) wins
        $t = fn_ram_call($ram, 'GET', '?op=read&service=pond&variable=telem');
        $tel = [];
        $cells = is_array($t['cells'] ?? null) ? $t['cells'] : (is_array($t['rows'] ?? null) ? $t['rows'] : []);
        usort($cells, function ($a, $b) { return ($a['id'] ?? 0) <=> ($b['id'] ?? 0); });
        foreach ($cells as $cl) {
            $ns = (string)($cl['instance'] ?? ''); $bag = $cl['bag'] ?? null;
            if (is_array($bag) && $bag) $tel[$ns] = $bag; else unset($tel[$ns]);
        }
        $p['telem'] = [];
        foreach ($tel as $ns => $bag) $p['telem'][] = ['ns' => $ns, 'name' => (string)($bag['name'] ?? $ns), 'me' => $ns === $me] + $bag;
        usort($p['telem'], function ($a, $b) { return strcmp($a['name'], $b['name']); });
        $c = fn_ram_call($ram, 'GET', '?op=read&service=pond&variable=config&instance=net');
        if ($c === null) { $p['error'] = "its RAM manager at $ram did not answer"; $errors[] = "pond $area: " . $p['error']; }
        $cell = $c['cells'][0]['bag'] ?? ($c['rows'][0]['bag'] ?? null);
        $p['config'] = is_array($cell) ? ['reach' => $cell['reach'] ?? '', 'map' => $cell['map'] ?? ''] : null;   // null: publishes none
        $r = json_decode((string)@file_get_contents(fn_path("$V/reach/$area.json")), true);
        if (is_array($r)) $p['reach'] = ['as' => $r['addr'] ?? '', 'here' => $r['self'] ?? '', 'reach' => $r['reach'] ?? '',
                                         'routes' => $r['routes'] ?? [], 'alias' => $r['alias'] ?? (object)[]];
        $why = @file_get_contents(fn_path("$V/reach/$area.refused"));
        if ($why !== false) $p['refused'] = trim($why);
        $out[] = $p;
    }
    usort($out, function ($a, $b) { return ($b['forward'] <=> $a['forward']) ?: strcmp($a['area'], $b['area']); });
    return $out;
}

// The hub link: the region edge interface (facts.json `hub` rows, [HUB_ROW_IS_THE_REGION_EDGE_V1]) and the kernel's
// latest handshake on it. handshake_age_s null = the kernel holds no handshake on that interface.
function fn_hub($mode, &$errors) {
    if ($mode['mode'] !== 'region') return [];
    $facts = fn_facts();
    if ($facts === null) { $errors[] = '/opt/fdf/var/facts.json: absent (fdf-facts has not written it)'; return []; }
    $edges = [];
    foreach (($facts['addrs'] ?? []) as $r) {
        if (($r['kind'] ?? '') === 'hub' && !empty($r['iface'])) $edges[$r['iface']] = (string)($r['ip'] ?? '');
    }
    if (!$edges) return [];
    $cmd = getenv('FROGNET_SNAPSHOT_WG') ?: 'sudo -n /usr/bin/wg show all latest-handshakes';
    $o = []; $rc = 0;
    exec($cmd . ' 2>&1', $o, $rc);
    $latest = [];
    if ($rc !== 0) { $errors[] = "$cmd: rc=$rc " . substr(implode(' ', $o), 0, 200); }
    else foreach ($o as $ln) {
        $f = preg_split('/\s+/', trim($ln));
        if (count($f) === 3 && ctype_digit($f[2]) && (int)$f[2] > 0) $latest[$f[0]] = max($latest[$f[0]] ?? 0, (int)$f[2]);
    }
    $now = time(); $out = [];
    ksort($edges);
    foreach ($edges as $if => $ip) {
        $h = ['iface' => $if, 'edge' => $ip, 'handshake_age_s' => null, 'source' => 'facts.json hub row + wg latest-handshakes'];
        if ($rc !== 0) $h['error'] = 'the kernel was not asked (see errors)';
        elseif (isset($latest[$if])) $h['handshake_age_s'] = max(0, $now - $latest[$if]);
        $out[] = $h;
    }
    return $out;
}

// The whole snapshot [ONE_MONITOR_TRUTH_V1]. One read of each source.
function fn_snapshot() {
    $errors = [];
    $mode = fn_mode();
    $guid = fn_guid($errors);
    $build = fn_build($errors);
    $echo = fn_self_echo($mode);
    $ef = explode(',', $echo);
    if (trim($echo) === '' || count($ef) < 4 || $ef[0] === '')
        $errors[] = ($mode['mode'] === 'region' ? '/opt/fdf/var/facts.json' : 'getFrogNet.bash') . ': no echo (this node states no name)';
    $name = trim($echo) === '' ? '' : $ef[0];
    $dot1 = $ef[1] ?? '';
    $rows = fn_host_rows($mode, $errors);
    $members = [];
    foreach ($rows as $h) {
        // role and alive are fn_host_rows' (the same values getHosts.php serves) [ONE_ALIVE_V1]
        $m = ['name' => $h['name'], 'guid' => $h['guid'], 'ip' => $h['ip'], 'role' => $h['role'], 'alive' => $h['alive'],
              'source' => $mode['mode'] === 'region' ? 'region' : 'lan', 'echo' => $h['echo']];
        if ($dot1 !== '' && $h['ip'] === $dot1) { $m['self'] = true; if ($m['guid'] === '') $m['guid'] = $guid; }
        $members[] = $m;
    }
    return [
        'snapshot'  => 1,
        'at'        => time(),
        'node'      => ['name' => $name, 'dot1' => $dot1, 'guid' => $guid, 'echo' => $echo],
        'build'     => $build,
        'mode'      => $mode['mode'],
        'mode_why'  => $mode['why'],
        'ponds'     => fn_ponds($mode, $guid, $errors),
        'hub'       => fn_hub($mode, $errors),
        'members'   => $members,
        'elections' => fn_elections($errors),
        'sources'   => $mode['mode'] === 'region'
            ? ['members' => '/var/lib/frognet/region_hosts.json (the daemon\'s applied plan; alive = the member\'s region session this event)',
               'node' => '/opt/fdf/var/facts.json', 'ponds' => '/opt/fdf/var (ponds/, forward, join.env, facts.json pond) + each pond\'s RAM manager',
               'hub' => 'facts.json hub rows + the kernel (wg latest-handshakes)', 'elections' => '/etc/hosts']
            : ['members' => 'makeHostJson.bash (LAN discovery: the last merge\'s /etc/hosts; alive = named by that merge)',
               'node' => 'getFrogNet.bash', 'ponds' => 'none (LAN-only)', 'hub' => 'none (LAN-only)', 'elections' => '/etc/hosts'],
        'errors'    => $errors,
    ];
}
