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
header('Content-Type: application/json');
header('Cache-Control: no-cache, no-store, must-revalidate');
$lines = [];
exec('/usr/local/bin/sysperf_outputter.bash 2>/dev/null', $lines);
// The JSON payload is the last non-empty line starting with {
foreach (array_reverse($lines) as $line) {
    $line = trim($line);
    if ($line !== '' && $line[0] === '{') {
        echo $line;
        exit;
    }
}
echo '{}';
?>
