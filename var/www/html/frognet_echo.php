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
    // [ECHO_FROM_THE_REGION_V1] On an FD&F region this node's echo is its own facts (the file fdf-client announces to
    // the region), in the form every region reader uses (RegionEcho, region_hosts.json): "<name>,<.1>,<upstream1>,<upstream2>".
    // Off a region, getFrogNet.bash. frognet_node.inc.php's fn_self_echo, the same line getSnapshot.php serves as
    // node.echo [ONE_MONITOR_TRUTH_V1].
    require_once __DIR__ . '/frognet_node.inc.php';
    echo fn_self_echo(fn_mode());
?>
