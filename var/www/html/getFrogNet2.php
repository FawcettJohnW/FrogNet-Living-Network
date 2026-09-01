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
// /var/www/html/getFrogNet.php
// Return the local FrogNet identity string from /usr/local/bin/getFrogNet.bash

header("Content-Type: text/plain; charset=utf-8");

$out = shell_exec('/usr/local/bin/getFrogNet2.bash 2>/dev/null');
if ($out === null) {
    http_response_code(500);
    echo "getFrogNet failed\n";
    exit;
}

echo $out;
?>
