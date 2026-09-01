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
/*
 * opendb.php — legacy include that other scripts use to get a $mysqli
 * connection ready to issue queries.
 *
 * Rewritten to delegate to config.php's db() wrapper so the connection
 * goes through FrogNetDb's [CLEAN_SHUTDOWN_V2] __destruct path on
 * request end (drains pending results, then close()).  Caller scripts
 * see $mysqli as before — db()->raw() returns the underlying mysqli.
 *
 * The wrapper is request-scoped (static singleton in db()), so every
 * request that include()s this file ends with a clean COM_QUIT to
 * mariadb, eliminating the "Aborted connection ... Got an error
 * reading communication packets" log spam from this caller chain.
 */
require_once __DIR__ . '/config.php';
$mysqli = db()->raw();
if ($mysqli === null) {
    http_response_code(500);
    echo '{"Status":[{"Code":8888}]}';
    exit;
}
