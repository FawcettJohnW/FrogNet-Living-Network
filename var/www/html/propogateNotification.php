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
$event = $_GET['event'] ?? '';
if ($event === '') {
    http_response_code(400);
    echo "missing event\n";
    exit;
}

$inst = preg_replace('/[^A-Za-z0-9_.-]/', '_', $event);
$unit = "frognet-propogate-notification@{$inst}.service";

/*
 * Critical:
 *   - run as root via sudo -n
 *   - --no-block so systemctl returns immediately
 */
$cmd = "/usr/bin/sudo -n /bin/systemctl start --no-block " . escapeshellarg($unit) . " >/dev/null 2>&1";
echo "$cmd";
exec($cmd);

http_response_code(200);
echo "OK\n";
