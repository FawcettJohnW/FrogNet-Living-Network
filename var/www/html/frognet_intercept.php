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
 * FrogNet Apache/PHP Slow-Link Interceptor
 *
 * Policy:
 *  - If link is FAST  (latency <= 500ms) → ALWAYS normal HTTP
 *  - If link is SLOW  (latency > 500ms):
 *      - If template exists → semantic-compressed socket path
 *      - If NO template     → reject + log bandwidth exception
 */

$GLOBALS['FROGNET_CONNECTION_MAP'] = [];   // host => [ip, latency, slow_link, socket]

define('FROGNET_TEMPLATE_API_URL', 'https://databasehost.frognet/frognet_templates.php');

/**
 * Compute templateId = base64url( SHA256(path|method|sortedNames) )
 * path = URL path only (e.g. "/frognet_echo.php")
 */
function frognet_compute_template_id(string $path, string $method, array $params): string {
    ksort($params);
    $names = array_keys($params);
    $sig = $path . '|' . strtoupper($method) . '|' . implode(',', $names);

    $hash = hash('sha256', $sig, true);
    $b64  = base64_encode($hash);
    return rtrim(strtr($b64, '+/', '-_'), '=');
}

function frognet_template_exists_via_api(string $templateId): bool {
    $url = FROGNET_TEMPLATE_API_URL . '?action=exists&templateId=' . urlencode($templateId);

    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 5,
    ]);
    $body = curl_exec($ch);
    if ($body === false) {
        error_log('[FrogNet] template_exists_via_api curl error: ' . curl_error($ch));
        curl_close($ch);
        return false;
    }
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($code !== 200) {
        error_log("[FrogNet] template_exists_via_api HTTP $code body=$body");
        return false;
    }

    $json = json_decode($body, true);
    if (!is_array($json)) {
        error_log("[FrogNet] template_exists_via_api invalid JSON: $body");
        return false;
    }

    return isset($json['status'], $json['exists']) &&
           $json['status'] === 'ok' &&
           (bool)$json['exists'] === true;
}

/**
 * Get a template for a given request (by path).
 */
function frognet_get_template_for_request(string $url, string $method, array $params) {
    $parsed = parse_url($url);
    $path   = $parsed['path'] ?? '/';

    $templateId = frognet_compute_template_id($path, $method, $params);

    if (!frognet_template_exists_via_api($templateId)) {
        return null;
    }

    // Derive opcode from templateId (same as Python)
    $h = hash('sha256', $templateId, true);
    $opcode = (ord($h[0]) << 8) | ord($h[1]);

    $param_order = array_keys($params);
    sort($param_order);

    return [
        'templateId'  => $templateId,
        'opcode'      => $opcode,
        'param_order' => $param_order,
        'path'        => $path,
    ];
}

/**
 * Pack parameters according to template.
 * Format:
 *   [opcode:uint16]
 *   For each param (in param_order):
 *     [len:uint16][UTF-8 bytes]
 * Then '\n' terminator.
 */
function frognet_pack_semantic_request(array $template, array $params): string {
    $opcode = (int)$template['opcode'];
    $order  = $template['param_order'] ?? [];

    $buffer = pack('n', $opcode);

    foreach ($order as $name) {
        $value = '';
        if (array_key_exists($name, $params)) {
            $value = (string)$params[$name];
        }
        $len = strlen($value);
        $buffer .= pack('n', $len) . $value;
    }

    return $buffer . "\n";
}

/**
 * MAIN INTERCEPTOR
 */
function frognet_intercept_request($url, $params = [], $method = 'GET') {

    $parsed = parse_url($url);
    if (!isset($parsed['host'])) {
        error_log("[FrogNet] Invalid URL: $url");
        return false;
    }

    $host = $parsed['host'];
    error_log("[FrogNet] Intercepting URL=$url host=$host");

    $ip = gethostbyname($host);
    if ($ip === $host && !filter_var($host, FILTER_VALIDATE_IP)) {
        error_log("[FrogNet] DNS resolution failed for $host");
        return false;
    }

    error_log("[FrogNet] Resolved host=$host to ip=$ip");

    if (!isset($GLOBALS['FROGNET_CONNECTION_MAP'][$host])) {

        error_log("[FrogNet] Host $host not in map, measuring latency");
        $latency_ms = frognet_ping_latency($ip);

        $slow = ($latency_ms > 500.0);

        $sock = null;
        if ($slow) {
            $sock = frognet_open_socket($ip, 9009);
            if ($sock === false) {
                error_log("[FrogNet] Could not open socket to $ip:9009 for slow host; semantic path disabled");
                $sock = null;
            }
        }

        $GLOBALS['FROGNET_CONNECTION_MAP'][$host] = [
            'ip'        => $ip,
            'latency'   => $latency_ms,
            'slow_link' => $slow,
            'socket'    => $sock
        ];

        error_log("[FrogNet] Classified host=$host ip=$ip latency_ms=$latency_ms slow_link=" . ($slow ? "true" : "false"));
    } else {
        error_log("[FrogNet] Host $host found in map; reusing cached entry");
    }

    $entry     = $GLOBALS['FROGNET_CONNECTION_MAP'][$host];
    $slow_link = $entry['slow_link'];
    $socket    = $entry['socket'];

    error_log("[FrogNet] Cached entry: slow_link=" . ($slow_link ? "true" : "false") .
              " latency_ms={$entry['latency']} socket=" . (is_resource($socket) ? "resource" : "null"));

    // Fast link → normal HTTP
    if (!$slow_link) {
        error_log("[FrogNet] Link to $host is FAST; using NORMAL HTTP path");
        return frognet_normal_http($url, $params, $method);
    }

    // Slow link, but no socket → fallback to normal HTTP for now
    if ($socket === null) {
        error_log("[FrogNet] Link to $host is SLOW but no socket; using NORMAL HTTP path");
        return frognet_normal_http($url, $params, $method);
    }

    // Slow link + socket → must have template
    $template = frognet_get_template_for_request($url, $method, $params);
    if ($template === null) {
        error_log("[FrogNet] BANDWIDTH EXCEPTION: slow link to $host but no template; rejecting request");
        return false;
    }

    $buffer = frognet_pack_semantic_request($template, $params);

    error_log("[FrogNet] Using SLOW-LINK SOCKET path for host=$host, opcode={$template['opcode']} sending " . strlen($buffer) . " bytes");

    fwrite($socket, $buffer);
    fflush($socket);

    $ack = fgets($socket);

    error_log("[FrogNet] Received ACK from daemon: " . var_export($ack, true));

    return $ack;
}

/**
 * Ping helper
 */
function frognet_ping_latency($ip) {
    $cmd = "ping -c 1 -W 1 " . escapeshellarg($ip) . " 2>/dev/null";
    $output = [];
    exec($cmd, $output);
    foreach ($output as $line) {
        if (strpos($line, 'time=') !== false) {
            preg_match('/time=([\d\.]+)/', $line, $m);
            $lat = floatval($m[1]);
            error_log("[FrogNet] Ping $ip latency_ms=$lat");
            return $lat;
        }
    }
    error_log("[FrogNet] Ping $ip failed, treating as high latency");
    return 9999.0;
}

/**
 * Socket helper
 */
function frognet_open_socket($ip, $port) {
    $sock = @fsockopen("tcp://$ip", $port, $errno, $errstr, 1.0);
    if (!$sock) {
        error_log("[FrogNet] fsockopen to $ip:$port failed: $errno $errstr");
        return false;
    }
    stream_set_timeout($sock, 5);
    return $sock;
}

/**
 * Normal HTTP path
 */
function frognet_normal_http($url, $params, $method = 'GET') {

    $ch = curl_init();

    if (strtoupper($method) === 'POST') {
        curl_setopt($ch, CURLOPT_POST, 1);
        curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query($params));
    } else {
        if (!empty($params)) {
            $url .= (strpos($url, '?') === false ? '?' : '&') . http_build_query($params);
        }
    }

    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);

    $out = curl_exec($ch);

    if ($out === false) {
        $err = curl_error($ch);
        error_log("[FrogNet] cURL error for $url: $err");
    }

    curl_close($ch);

    return $out;
}
