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
        /*
         * [MYSQLI_THROWS_SINCE_PHP81_V1 - 2026-09-12]
         *
         * The connect_errno check below is PHP 7 error handling and it stopped
         * running years ago. Since PHP 8.1 the default mysqli error mode is
         * MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT, so `new mysqli(...)` with
         * a bad credential THROWS mysqli_sql_exception. Nothing here caught it,
         * so the "DB connect failed" JSON was never emitted -- PHP died on an
         * uncaught exception instead, and with display_errors off that is:
         *
         *     HTTP 500, Content-Type: application/json, Content-Length: 0
         *
         * api.php sets the JSON header at line 20, immediately after requiring
         * this file, so the header is already out and the body is empty. That is
         * the exact signature every store read on BAMacBook was getting, while
         * MariaDB logged "Access denied for user 'FrogUser'@'localhost'" for each
         * one. A credential failure was presenting as a blank server error and
         * sent the whole investigation somewhere else.
         *
         * Report the mode explicitly rather than relying on a version default,
         * and catch, so the failure says what it is.
         */
        mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);
        try {
            $m = new mysqli(DB_HOST, DB_USER, DB_PASS, DB_NAME);
        } catch (\mysqli_sql_exception $e) {
            http_response_code(500);
            header('Content-Type: application/json');
            echo json_encode([
                'error'  => 'DB connect failed',
                'detail' => $e->getMessage(),
                'user'   => DB_USER,
                'host'   => DB_HOST,
                'db'     => DB_NAME,
                'hint'   => 'config.php DB_PASS must match the FrogUser password '
                          . 'MySQL holds, and must agree with '
                          . '/opt/frognet_semantic/DB_CONFIG.json',
            ]);
            error_log('FrogNet api: DB connect failed for ' . DB_USER . '@' . DB_HOST
                      . ': ' . $e->getMessage());
            exit;
        }
        /* Retained: harmless on 8.1+, and correct if mysqli_report is ever set
         * back to MYSQLI_REPORT_OFF by a future php.ini. */
        if ($m->connect_errno) {
            http_response_code(500);
            header('Content-Type: application/json');
            echo json_encode(['error' => 'DB connect failed',
                              'detail' => $m->connect_error]);
            exit;
        }
        $m->set_charset('utf8mb4');
        $wrapper = new FrogNetDb($m);
    }
    return $wrapper;
}
