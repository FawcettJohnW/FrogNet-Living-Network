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
 * /var/www/html/getHosts.php
 *
 * Returns JSON array of {ip, name} — same output contract as the
 * previous version.
 *
 * Cache layer
 * -----------
 * Keyed by the mtime of /etc/sentinels/old_host_json (the
 * authoritative output of makeHostJson.bash). Steady state is a
 * single stat() plus a readfile() — sub-millisecond, no fork, no
 * json_decode, no reshape.
 *
 * shell_exec(makeHostJson.bash) fires ONLY when /etc/hosts is
 * newer than the builder's last output (i.e. a rebuild is genuinely
 * due). When runMerge does the right thing and rebuilds after each
 * /etc/hosts write, this path never executes.
 *
 * Cache files:
 *   /etc/sentinels/getHosts.cache.json   (the reshaped array)
 *   /etc/sentinels/getHosts.cache.meta   ("VERSION:source_mtime")
 *
 * Bump GETHOSTS_CACHE_VERSION whenever reshape rules change so stale
 * cache entries from the previous logic get thrown away automatically.
 */

const GETHOSTS_CACHE_VERSION = 1;

header('Content-Type: application/json');

$SOURCE_JSON = '/etc/sentinels/old_host_json';
$CACHE_JSON  = '/etc/sentinels/getHosts.cache.json';
$CACHE_META  = '/etc/sentinels/getHosts.cache.meta';
$ETC_HOSTS   = '/etc/hosts';

// ----------------------------------------------------------------------
// helpers
// ----------------------------------------------------------------------
function make_cache_key($sourceMtime) {
    return GETHOSTS_CACHE_VERSION . ':' . (int)$sourceMtime;
}

/**
 * If the on-disk cache matches $expectedKey, stream it to the client
 * and return true. Caller should exit after a true return.
 */
function try_serve_cache($cacheJson, $cacheMeta, $expectedKey) {
    if (!is_readable($cacheJson) || !is_readable($cacheMeta)) return false;
    $stored = @file_get_contents($cacheMeta);
    if ($stored === false) return false;
    if (trim($stored) !== $expectedKey) return false;
    readfile($cacheJson);
    return true;
}

/**
 * Reshape one row from makeHostJson's {ip, hostname, echo} shape
 * into the {ip, name} shape consumers expect. Returns null if the
 * row should be filtered out.
 *
 * Filtering rules (preserved from the previous version):
 *   - IP must start with 10.
 *   - IP must NOT be in 10.253.253.x (transit overlay)
 */
function reshape_row($entry) {
    $ip = $entry['ip'] ?? '';
    if ($ip === '') return null;
    if (!preg_match('/^10\./', $ip)) return null;
    if (preg_match('/^10\.253\.253\./', $ip)) return null;

    $name = '';
    if (!empty($entry['echo'])) {
        $parts = explode(',', $entry['echo']);
        $name  = $parts[0];
    }
    if ($name === '' && !empty($entry['hostname'])) {
        $name = $entry['hostname'];
    }
    if (strpos($name, 'FrogNetHost.') === 0) {
        $name = substr($name, strlen('FrogNetHost.'));
    }
    if ($name === '') $name = 'unknown';

    return ['ip' => $ip, 'name' => $name];
}

/**
 * Build the reshaped array from makeHostJson's raw output.
 */
function reshape($sourceJson) {
    $raw = json_decode($sourceJson, true);
    $hosts = [];
    $seen  = [];
    if (is_array($raw)) {
        foreach ($raw as $entry) {
            $row = reshape_row($entry);
            if ($row === null) continue;
            $ip = $row['ip'];
            if (isset($seen[$ip])) continue;
            $seen[$ip] = true;
            $hosts[] = $row;
        }
    }
    return $hosts;
}

/**
 * Atomically persist the reshape output. Best-effort: any failure
 * here must NOT fail the response.
 */
function persist_cache($cacheJson, $cacheMeta, $cacheKey, $output) {
    $tmp = $cacheJson . '.tmp.' . getmypid();
    if (@file_put_contents($tmp, $output) === false) return;
    if (!@rename($tmp, $cacheJson)) {
        @unlink($tmp);
        return;
    }
    @file_put_contents($cacheMeta, $cacheKey);
}

// ----------------------------------------------------------------------
// fast path: on-disk cache matches current source mtime
// ----------------------------------------------------------------------
$sourceMtime = @filemtime($SOURCE_JSON);
if ($sourceMtime !== false) {
    if (try_serve_cache($CACHE_JSON, $CACHE_META, make_cache_key($sourceMtime))) {
        exit;
    }
}

// ----------------------------------------------------------------------
// /etc/hosts newer than builder output (or builder output absent)?
// Fire makeHostJson.bash once to catch up. It has its own cache +
// flock, so this is cheap and safely serialised across workers.
// ----------------------------------------------------------------------
$hostsMtime = @filemtime($ETC_HOSTS);
$needBuilder = ($sourceMtime === false)
            || ($hostsMtime !== false && $hostsMtime > $sourceMtime);

if ($needBuilder) {
    @shell_exec('/usr/local/bin/makeHostJson.bash 2>/dev/null');
    $sourceMtime = @filemtime($SOURCE_JSON);

    // A concurrent worker may have populated the cache while we were
    // waiting on shell_exec. Re-check before doing the reshape work.
    if ($sourceMtime !== false) {
        if (try_serve_cache($CACHE_JSON, $CACHE_META, make_cache_key($sourceMtime))) {
            exit;
        }
    }
}

// ----------------------------------------------------------------------
// slow path: read source, reshape, cache, serve
// ----------------------------------------------------------------------
if ($sourceMtime === false) {
    echo '[]';
    exit;
}

$sourceJson = @file_get_contents($SOURCE_JSON);
if ($sourceJson === false || trim($sourceJson) === '') {
    echo '[]';
    exit;
}

$hosts    = reshape($sourceJson);
$output   = json_encode($hosts);
$cacheKey = make_cache_key($sourceMtime);

persist_cache($CACHE_JSON, $CACHE_META, $cacheKey, $output);

echo $output;
