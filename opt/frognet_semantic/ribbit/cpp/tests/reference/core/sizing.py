#!/opt/frognet_semantic/venv/bin/python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
core/sizing.py - shared sizing for DB connection pool and DB executor.

Single source of truth so the executor and the pool can never drift
apart.  When they do, half the executor workers race for connections
that don't exist and the pool churns connect()/close() under load.

initial_db_concurrency() runs at startup.  It returns a number that
respects three ceilings:
  * cpu_count() * 4 - diminishing returns past this on most hardware
  * max_connections / 4 from MariaDB - leave 75% for PHP and headroom
  * RLIMIT_NOFILE / 8 - fds aren't free

Override via FROGNET_DB_CONCURRENCY env var for testing.

The pool may resize itself at runtime via heuristics; this is only the
starting point.
"""

from __future__ import annotations

import json
import os
import resource
import sys

import mysql.connector


_DB_CONFIG_PATH = "/opt/frognet_semantic/DB_CONFIG.json"


def _probe_max_connections() -> int | None:
    """Open one short-lived connection, ask MariaDB its max_connections.
    Returns None on any failure - caller falls back to CPU heuristic."""
    try:
        with open(_DB_CONFIG_PATH) as f:
            cfg = json.load(f)
        conn = mysql.connector.connect(
            host=cfg["host"],
            user=cfg["user"],
            password=cfg["password"],
            database=cfg["database"],
            port=cfg.get("port", 3306),
            connection_timeout=5,
            use_pure=True,
        )
        try:
            cur = conn.cursor()
            cur.execute("SHOW VARIABLES LIKE 'max_connections'")
            row = cur.fetchone()
            cur.close()
            if row and len(row) >= 2:
                return int(row[1])
        finally:
            conn.close()
    except Exception as e:
        print(f"[sizing] max_connections probe failed: {e!r}", file=sys.stderr)
    return None


def initial_db_concurrency() -> int:
    """Compute the starting DB concurrency for this process.

    Used by both core.store._MySQLPool (as pool size) and
    daemon.engine.session (as db_executor size).  Same number for both
    so the executor never has more workers than connections.
    """
    override = os.environ.get("FROGNET_DB_CONCURRENCY")
    if override:
        try:
            n = int(override)
            if n >= 2:
                return n
        except ValueError:
            pass

    # [NO_FALLBACK_V1] If the hardware cannot be measured, say so - do not
    # invent 4. session.py::_default_db_pool raises for exactly this reason;
    # the two paths size the same quantity and must fail the same way, or a
    # node that cannot read its own CPU count gets a resizer working from an
    # invented number while the daemon refuses to start on the real one.
    cpu = os.cpu_count()
    if not cpu:
        raise RuntimeError(
            "cannot determine CPU count; DB concurrency cannot be sized. "
            "Set FROGNET_DB_CONCURRENCY explicitly.")
    cpu_based = cpu * 4

    max_conn = _probe_max_connections()
    if max_conn is not None:
        db_share = max_conn // 4
    else:
        db_share = cpu_based  # neutral fallback

    fd_soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    fd_share = fd_soft // 8

    n = max(2, min(cpu_based, db_share, fd_share))
    print(
        f"[sizing] initial_db_concurrency: cpu={cpu} cpu_based={cpu_based} "
        f"max_conn={max_conn} db_share={db_share} fd_share={fd_share} -> {n}",
        file=sys.stderr,
    )
    return n


# Hard ceilings the runtime resizer must not exceed.
def upper_bound() -> int:
    """The largest value the resizer is ever allowed to grow to."""
    # [NO_FALLBACK_V1] If the hardware cannot be measured, say so - do not
    # invent 4. session.py::_default_db_pool raises for exactly this reason;
    # the two paths size the same quantity and must fail the same way, or a
    # node that cannot read its own CPU count gets a resizer working from an
    # invented number while the daemon refuses to start on the real one.
    cpu = os.cpu_count()
    if not cpu:
        raise RuntimeError(
            "cannot determine CPU count; DB concurrency cannot be sized. "
            "Set FROGNET_DB_CONCURRENCY explicitly.")
    cpu_based = cpu * 4

    max_conn = _probe_max_connections()
    db_share = (max_conn // 4) if max_conn is not None else cpu_based

    fd_soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    fd_share = fd_soft // 8

    return max(2, min(cpu_based, db_share, fd_share))


def lower_bound() -> int:
    """Smallest value the resizer is allowed to shrink to."""
    cpu = os.cpu_count() or 4
    return max(2, cpu)
