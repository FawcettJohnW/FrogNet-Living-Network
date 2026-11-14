<?php
/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// held_fire.php -- [HELD_READ_IN_API_V1 - John 2026-11-07] the holder's end of a held read.
//
// A role host's api.php fires a held read by POSTing here, through this node's port 80
// (proxy -> daemon -> Apache, the path every daemon-to-daemon call takes). The body is
// {hold, hid, build, SensorID, SensorType, SensorName, SensorAddress, UpdatedAtEpoch, data}.
// This hands it, unchanged, to the process that holds the read: a datagram to its socket
// /run/frognet/held/<hid>.sock. That process re-arms by holding again, and the hold's
// reply is the current value.
//   200  delivered
//   410  no such holder here (its socket is gone): the host has already dropped the hold
//   400  not a fire
const HELD_FIRE_BUILD = 'held_fire.php 20261107-held1 [HELD_READ_IN_API_V1]';
const HELD_DIR = '/run/frognet/held';
header('X-FrogNet-Held-Fire-Build: ' . HELD_FIRE_BUILD);
header('Content-Type: application/json');

function out($code, $why) {
    http_response_code($code);
    echo json_encode(['ok' => $code === 200, 'why' => $why, 'build' => HELD_FIRE_BUILD]);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') out(400, 'POST a fire');
$raw = file_get_contents('php://input');
$msg = json_decode($raw, true);
if (!is_array($msg) || !isset($msg['hid']) || !is_string($msg['hid'])
        || preg_match('/^[A-Za-z0-9._:-]{1,64}$/', $msg['hid']) !== 1) out(400, 'not a fire');

$sock = HELD_DIR . '/' . $msg['hid'] . '.sock';
if (!file_exists($sock)) out(410, 'no holder ' . $msg['hid'] . ' here');
$s = @stream_socket_client('udg://' . $sock, $errno, $errstr, 1);
if (!$s) out(410, 'holder ' . $msg['hid'] . ' not listening: ' . $errstr);
$n = @fwrite($s, $raw);
fclose($s);
if ($n !== strlen($raw)) out(410, 'holder ' . $msg['hid'] . ' did not take the fire');
out(200, 'delivered');
