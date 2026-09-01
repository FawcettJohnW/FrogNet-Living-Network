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
// DB connection info
define('DB_HOST', '127.0.0.1'); // adjust if MySQL is on another host
define('DB_USER', 'FrogUser');
define('DB_PASS', '__FROGNET_DB_PASS__');
define('DB_NAME', 'FrogNet');

// CORS (adjust or tighten for production)
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { exit; }

/**
 * [CLEAN_SHUTDOWN_V2] Destructor-based clean close.
 *
 * Previous version (V1) used register_shutdown_function.  In production
 * mariadb continued logging "Aborted connection ... Got an error
 * reading communication packets" warnings at the same rate the V1 fix
 * was supposed to eliminate, suggesting either:
 *   (a) the shutdown handler wasn't firing reliably under mod_php /
 *       php-fpm, or
 *   (b) it fired but mysqli's internal state (open prepared statements,
 *       unbuffered result sets) prevented close() from sending COM_QUIT
 *       cleanly.
 *
 * Wrapping the singleton in an object with a __destruct method gives
 * PHP a deterministic, scope-driven hook that runs *before* the request
 * is torn down, while statics are still alive.  Inside __destruct we
 * drain any pending result sets first, then close().  Caller code is
 * unchanged: db() still acts like a real mysqli via __call/__get magic,
 * so api.php's $mysqli->prepare(), $stmt->execute(), etc. all work the
 * same.
 *
 * The wrapper is held by the static in db(); on script end PHP frees
 * statics, the wrapper goes out of scope, __destruct runs, mysqli
 * closes cleanly, mariadb sees COM_QUIT, no warning.
 */
final class FrogNetDb
{
    private ?mysqli $m;

    public function __construct(mysqli $m)
    {
        $this->m = $m;
    }

    /** Return the raw mysqli for code that does `db()->prepare(...)`. */
    public function raw(): ?mysqli
    {
        return $this->m;
    }

    /* Forward method calls and property access so existing call sites
     * — `db()->prepare(...)`, `db()->insert_id`, `db()->error`,
     * `db()->set_charset(...)` — continue to work without edits. */

    public function __call(string $name, array $args)
    {
        if ($this->m === null) {
            throw new RuntimeException("mysqli wrapper used after close: $name");
        }
        return $this->m->$name(...$args);
    }

    public function __get(string $name)
    {
        if ($this->m === null) return null;
        return $this->m->$name;
    }

    public function __set(string $name, $value): void
    {
        if ($this->m !== null) {
            $this->m->$name = $value;
        }
    }

    public function __isset(string $name): bool
    {
        return $this->m !== null && isset($this->m->$name);
    }

    public function __destruct()
    {
        if ($this->m === null) return;
        try {
            // Drain any unread result sets so close() doesn't itself
            // tear down a socket the server hasn't finished writing to.
            while (@$this->m->more_results() && @$this->m->next_result()) {
                if ($r = @$this->m->store_result()) { $r->free(); }
            }
        } catch (\Throwable $e) { /* ignore */ }
        @$this->m->close();
        $this->m = null;
    }
}

function db(): FrogNetDb
{
    static $wrapper = null;
    if ($wrapper === null) {
        $m = new mysqli(DB_HOST, DB_USER, DB_PASS, DB_NAME);
        if ($m->connect_errno) {
            http_response_code(500);
            echo json_encode(['error' => 'DB connect failed', 'detail' => $m->connect_error]);
            exit;
        }
        $m->set_charset('utf8mb4');
        $wrapper = new FrogNetDb($m);
    }
    return $wrapper;
}
