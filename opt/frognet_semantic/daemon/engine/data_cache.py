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
daemon/engine/data_cache.py   [DATA_CACHE_GEN_V2]

A RAM materialization of Sensor + SensorData that is provably current.

WHY THIS IS NOT THE OLD data_cache
----------------------------------
The previous version loaded both tables once at daemon start and never reloaded
on any read path. Nothing that wrote to MySQL by any other route -- another
node's api.php, the local Apache, the mysql client -- was ever seen. It also
filtered on whatever query keys it was handed, so a parameter it could not
express (fresh_s) matched no row and it returned {"ok":true,"rows":[]} as an
AUTHORITATIVE answer. Both faults have the same shape: RAM answered a question
it had no standing to answer.

THE RULE HERE
-------------
Serve from RAM only when it can be PROVEN, on this request, that RAM equals
disk. Proof is a generation counter that MySQL itself maintains:

    FrogNetTableGen(TableName='SensorTables', Gen)

bumped by AFTER INSERT / UPDATE / DELETE triggers on Sensor and SensorData.
Triggers fire for every writer -- PHP, another node, a human at the mysql
prompt -- so nothing can change either table without moving Gen.

Every read does one primary-key lookup of Gen. Unchanged means RAM is disk.
Changed means reload before answering. The snapshot is taken inside a single
consistent-read transaction with Gen read in that same transaction, so the
loaded rows and the Gen they are stamped with cannot disagree.

Cost per read: one indexed single-row SELECT on a local socket, in place of
Apache + PHP + a LEFT JOIN over the whole table + json_encode.

FAIL CLOSED
-----------
If FrogNetTableGen is missing, or fewer than the six expected triggers exist,
the cache DISABLES ITSELF for the life of the process and every request falls
through to Apache. An uncached node is slow. A node serving from a snapshot it
cannot prove is current is wrong, and wrong is not a performance tradeoff.

Cascaded deletes (SensorData via ON DELETE CASCADE) do not fire row triggers in
InnoDB. That is safe here because the parent DELETE on Sensor does fire, and any
Gen change reloads BOTH tables.

READS ONLY
----------
No write is intercepted. The old version acked upsert with {"ok":true} before
the row reached MySQL, which is a false answer about durability. Writes go to
api.php, the triggers move Gen, and the next read reloads. Client-side write
batching, if wanted, belongs in api.php's existing upsert_batch endpoint.

Disable entirely: FROGNET_DATA_CACHE_ENABLED=0
DDL:              var/www/html/data_cache_gen.sql
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

_ENABLED = os.environ.get("FROGNET_DATA_CACHE_ENABLED", "1").strip() == "1"

# [DATA_CACHE_GEN_V3] Per-table generations. V2 used one shared row,
# 'SensorTables', bumped by all six triggers -- so a metric write to SensorData
# invalidated the Sensor half too and the next read rebuilt BOTH tables whole.
#
# Measured Seattle5, one hour as elected databasehost: 1713 upserts produced 830
# distinct generations and 831 full two-table reloads. One complete rebuild of
# 258 + 258 rows every 4.3s, competing for the same MySQL as the writes that
# caused it. Sensor is node identity and changes near never; SensorData is all
# the churn. The write rate was normal, the invalidation granularity was not.
# [DB_SECRET_IS_INJECTABLE_V1] The installer rewrites the default below. An
# empty default is correct and intended: DB_CONFIG.json normally supplies the
# password, and a machine where neither is set must fail to connect loudly
# rather than try an empty one and look like a permissions problem.
_DB_PASS = os.environ.get("FROGNET_DB_PASS", "")

_GEN_TABLES = ("Sensor", "SensorData")
_GEN_KEY_LEGACY = "SensorTables"     # V2's shared row; read only by the preflight
_EXPECTED_TRIGGERS = 6

# api.php's filter allow-list for BOTH sensors handlers (api.php:614 for values;
# the generic handler uses the table's own column list, which for Sensor is the
# same set). A key outside this set is a filter we cannot express, and an
# inexpressible filter must reach MySQL -- never be silently applied to a column
# that does not exist.
_SENSOR_COLS = ("SensorID", "FrogID", "SensorAddress", "SensorNetwork",
                "SensorName", "SensorType", "SensorLocation", "Tags")
# Query keys that are not filters.
_CONTROL_KEYS = ("entity", "action", "order", "limit", "parse", "fresh_s")

_SQL_GEN = ("SELECT TableName, Gen, UNIX_TIMESTAMP() AS DbNow "
            "FROM FrogNetTableGen WHERE TableName IN (%s, %s)")
_SQL_TRIGGERS = ("SELECT COUNT(*) AS n FROM information_schema.TRIGGERS "
                 "WHERE TRIGGER_SCHEMA = DATABASE() "
                 "AND EVENT_OBJECT_TABLE IN ('Sensor','SensorData')")
_SQL_GEN_ONE = ("SELECT Gen FROM FrogNetTableGen WHERE TableName=%s")
_SQL_SENSORS = "SELECT * FROM Sensor"
_SQL_SENSORDATA = ("SELECT *, UNIX_TIMESTAMP(UpdatedAt) AS UpdatedAtEpoch "
                   "FROM SensorData")

# -- connection ---------------------------------------------------------------

_db_config: Optional[Dict[str, Any]] = None
_db_config_lock = threading.Lock()
_conn_local = threading.local()


def _load_db_config() -> Dict[str, Any]:
    global _db_config
    if _db_config is not None:
        return _db_config
    with _db_config_lock:
        if _db_config is None:
            with open("/opt/frognet_semantic/DB_CONFIG.json") as f:
                _db_config = json.load(f)
        return _db_config


def _get_conn():
    import mysql.connector
    conn = getattr(_conn_local, "conn", None)
    if conn is not None:
        try:
            sock = conn._socket.sock
            if sock:
                sock.settimeout(10.0)
            if conn.is_connected():
                return conn
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        _conn_local.conn = None

    cfg = _load_db_config()
    conn = mysql.connector.connect(
        host=cfg.get("host", "127.0.0.1"), user=cfg.get("user", "FrogUser"),
        # [DB_SECRET_IS_INJECTABLE_V1] DB_CONFIG.json is the source of truth and
        # stays that way. _DB_PASS is the installer's injection SITE, used only
        # when the config carries no password.
        #
        # frognet_install.sh lists this file in SECRET_INJECT_FILES and dies if a
        # listed file yields zero matches. It recognises four forms: a PHP
        # DB_PASS define, a JSON or dict password key, an environment-variable
        # default, and a build token.
        #
        # NOTE FOR ANYONE EDITING THIS COMMENT: do not write those forms out
        # literally here. The injector is a regex over the whole file and does
        # not know what a comment is -- spelling them out put THREE COPIES OF THE
        # LIVE PASSWORD into this comment block on the first attempt. Describe
        # them; do not quote them.
        #
        # The previous `cfg.get` call matched none of the four, so injection
        # reported NO-SECRET-FIELD and the install aborted:
        #
        #   NO-SECRET-FIELD /opt/frognet_semantic/daemon/engine/data_cache.py
        #   [FATAL] DB secret injection failed -- refusing to continue
        #
        # This is the same form daemon/cache/semcache_db.py already uses, so the
        # installer sees one site here exactly as it does there.
        password=cfg.get("password") or _DB_PASS,
        database=cfg.get("database", "FrogNet"),
        port=cfg.get("port", 3306),
        autocommit=True, use_pure=True, connection_timeout=10,
    )
    try:
        sock = conn._socket.sock
        if sock:
            sock.settimeout(10.0)
    except Exception:
        pass
    _conn_local.conn = conn
    return conn


# -- state --------------------------------------------------------------------

_sensors: List[Dict[str, Any]] = []
_sensor_data: Dict[Any, Dict[str, Any]] = {}
# [DATA_CACHE_GEN_V3] One generation PER TABLE. None until first load.
_gen: Dict[str, Optional[int]] = {t: None for t in _GEN_TABLES}
_data_lock = threading.RLock()

_wiring_ok: Optional[bool] = None      # None = not yet checked
_wiring_lock = threading.Lock()

_stats = {"reads_served": 0, "gen_checks": 0, "reloads": 0,
          "fell_through": 0, "disabled": 0,
          # [RELOAD_IS_SINGLE_FLIGHT_V1] reload_waits counts requests that rode
          # somebody else's rebuild instead of starting their own. On a busy
          # databasehost this should dwarf `reloads`; if the two are close, the
          # single-flight is not engaging and the storm is back.
          "reload_waits": 0, "reload_waits_timed_out": 0}

# [RELOAD_IS_SINGLE_FLIGHT_V1] How long a follower waits for the leader's
# rebuild before giving up and falling through to Apache. Must exceed a real
# two-table snapshot and stay under the daemon's own upstream timeout
# (FROGNET_DAEMON_UPSTREAM_TIMEOUT, default 5s) so a wait cannot itself become
# the thing that times the request out.
_RELOAD_WAIT_S = float(os.environ.get("FROGNET_DATA_CACHE_RELOAD_WAIT_S", "4.0"))


def _check_wiring(conn) -> bool:
    """[DATA_CACHE_GEN_V2] The counter and all six triggers must exist. Anything
    less and there is no way to know RAM is current, so there is no cache."""
    global _wiring_ok
    with _wiring_lock:
        if _wiring_ok is not None:
            return _wiring_ok
        ok = False
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(_SQL_GEN, _GEN_TABLES)
            rows = cur.fetchall() or []
            _present = {r.get("TableName") for r in rows}
            has_gen = all(t in _present for t in _GEN_TABLES)
            if not has_gen:
                # [DATA_CACHE_GEN_V3] Name the version mismatch. A V2 database
                # has only the shared 'SensorTables' row, and reporting that as
                # a generic "wiring incomplete" sends the reader looking for
                # missing triggers that are all present.
                cur.execute(_SQL_GEN_ONE, (_GEN_KEY_LEGACY,))
                if cur.fetchone() is not None:
                    print("[DATA-CACHE] DISABLED: this database is on "
                          "[DATA_CACHE_GEN_V2] -- one shared 'SensorTables' "
                          "counter, no per-table rows. Load V3's "
                          "var/www/html/data_cache_gen.sql. Missing: %s"
                          % ", ".join(t for t in _GEN_TABLES
                                      if t not in _present), flush=True)
            cur.execute(_SQL_TRIGGERS)
            trow = cur.fetchone()
            cur.close()
            n = int((trow or {}).get("n", 0))
            ok = bool(has_gen) and n >= _EXPECTED_TRIGGERS
            if not ok:
                print("[DATA-CACHE] DISABLED: generation wiring incomplete "
                      f"(FrogNetTableGen row={'yes' if has_gen else 'MISSING'}, "
                      f"triggers={n}/{_EXPECTED_TRIGGERS}). Every read will go to "
                      "Apache. Load var/www/html/data_cache_gen.sql to enable.",
                      flush=True)
                _stats["disabled"] += 1
        except Exception as e:
            print(f"[DATA-CACHE] DISABLED: cannot verify generation wiring: {e!r}",
                  flush=True)
            _stats["disabled"] += 1
            ok = False
        _wiring_ok = ok
        return ok


def _read_gen(conn):
    """Return ({table: gen}, db_now_epoch). Two primary-key rows, one query.
    Runs on every read.

    [DATA_CACHE_GEN_V3] Both rows or nothing: a missing row means this database
    has not been migrated, and a cache that cannot prove currency for a table
    has no standing to answer for it.
    """
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(_SQL_GEN, _GEN_TABLES)
        rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass
    gens = {r["TableName"]: int(r["Gen"]) for r in rows if r.get("TableName")}
    if not all(t in gens for t in _GEN_TABLES):
        return None, None
    _stats["gen_checks"] += 1
    return gens, int(rows[0]["DbNow"])


# [API_PHP_WIRE_TYPES_V1] Columns MySQL hands back as datetime/date/Decimal and
# api.php hands back as strings. mysql.connector with dictionary=True returns
# native Python objects; PHP's json_encode over a mysqli result returns the
# textual form. This module's rule is "must agree with api.php, not merely
# resemble it", so the conversion happens ONCE, HERE, on named columns.
#
# Why not json.dumps(default=str): that is a fallback. It stringifies ANY object
# json cannot serialise, so the next unexpected type becomes silently corrupt
# output instead of an error, and it hides the fact that a native object reached
# the wire at all. It was on the sensors/values path only, which is exactly why
# the SAME defect on sensor_data/get was invisible until a per-SensorID read hit
# it: 167 consecutive failures of
#     Daemon: exec error: TypeError('Object of type datetime is not JSON serializable')
# on /api.php?entity=sensor_data&action=get, measured Seattle6 -> 10.250.250.1,
# 2026-08-08. Deterministic. It never worked and never would have.
#
# MySQL DATETIME renders as 'YYYY-MM-DD HH:MM:SS', which is what PHP emits for
# the same column. Anything NOT in this table that is not JSON-serialisable now
# raises from json.dumps, loudly, naming the column - which is the point.
_WIRE_DATETIME_COLS = ("UpdatedAt", "CreatedAt")


def _to_api_wire(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one row's known temporal columns to api.php's textual form."""
    for col in _WIRE_DATETIME_COLS:
        v = row.get(col)
        if isinstance(v, _dt.datetime):
            row[col] = v.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(v, _dt.date):
            row[col] = v.strftime("%Y-%m-%d")
    return row


# [RELOAD_IS_SINGLE_FLIGHT_V1] One reload at a time, per generation.
#
# _ensure_current() runs on EVERY read. When Gen moves, every concurrent request
# in the db executor (_DB_POOL_SIZE = cpu_count * 4, so 16 on a 4-core node)
# independently called _reload() - each a consistent-snapshot transaction
# reading the WHOLE Sensor table and the WHOLE SensorData table. Sixteen full
# rebuilds for one generation bump, competing for the same local MySQL as the
# writes that bumped it.
#
# Measured on Seattle5, 2026-08-08, five minutes: 95 reloads across 71 distinct
# generations - 24 of them pure duplicate work, three threads rebuilding
# gen=455661 within the same second. Alongside 454 upstream TimeoutError('timed
# out') at the 5s FROGNET_DAEMON_UPSTREAM_TIMEOUT. Being the elected databasehost
# attracts every node's metric writes; the writes bump Gen; Gen bumps trigger
# reloads; the reloads starve the writes.
#
# Now: the first thread to see a new generation reloads. Every other thread waits
# on its result rather than duplicating it, then re-checks - if the winner
# already loaded a generation at least as new as the one they saw, they are done.
_reload_lock = threading.Lock()
_reload_in_flight: Dict[int, threading.Event] = {}


def _reload(conn, which=_GEN_TABLES) -> None:
    """Load the named tables and the Gens they belong to inside ONE consistent
    read, so the rows and the generation stamped on them cannot disagree.

    [DATA_CACHE_GEN_V3] `which` is the subset that actually moved. Sensor is node
    identity and changes when a node joins or leaves; SensorData is every metric
    write in the pond. Rebuilding the first because the second moved was 831
    two-table snapshots an hour on the elected databasehost.
    """
    # [NO_FALLBACK_V1] `except Exception: pass` around start_transaction meant a
    # failed snapshot dropped straight through to a NON-transactional read, and
    # the docstring's whole guarantee - that the rows and the Gen stamped on them
    # cannot disagree - silently stopped holding. A cache that cannot prove it is
    # current has no standing to answer, which is this module's founding rule.
    conn.start_transaction(consistent_snapshot=True)
    sensors = data = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(_SQL_GEN, _GEN_TABLES)
        grows = {r["TableName"]: int(r["Gen"]) for r in (cur.fetchall() or [])}
        if "Sensor" in which:
            cur.execute(_SQL_SENSORS)
            sensors = cur.fetchall()
        if "SensorData" in which:
            cur.execute(_SQL_SENSORDATA)
            data = cur.fetchall()
        cur.close()
    finally:
        conn.commit()

    missing = [t for t in which if t not in grows]
    if missing:
        raise RuntimeError(
            "FrogNetTableGen row(s) for %s vanished mid-reload; the snapshot "
            "cannot be stamped and will not be installed" % (", ".join(missing),))

    if sensors is not None:
        sensors = [_to_api_wire(dict(r)) for r in sensors]
    if data is not None:
        data = [_to_api_wire(dict(r)) for r in data]

    with _data_lock:
        if sensors is not None:
            _sensors[:] = sensors
            _gen["Sensor"] = grows["Sensor"]
        if data is not None:
            _sensor_data.clear()
            for r in data:
                _sensor_data[r.get("SensorID")] = r
            _gen["SensorData"] = grows["SensorData"]
    _stats["reloads"] += 1
    print("[DATA-CACHE] reloaded %s at gen=%s: %s sensors, %s sensor_data rows"
          % ("+".join(which),
             ",".join("%s=%s" % (t, _gen[t]) for t in which),
             len(sensors) if sensors is not None else "-",
             len(data) if data is not None else "-"), flush=True)


def _reload_once(conn, table: str, want_gen: int) -> None:
    """[RELOAD_IS_SINGLE_FLIGHT_V1] Reload `table` for want_gen, or wait for
    whoever is already doing it. Never two snapshots for one generation.

    [DATA_CACHE_GEN_V3] The in-flight key is (table, gen), not gen: the two
    tables move independently now, and a shared key would make a SensorData
    reload satisfy a waiter that needs Sensor.
    """
    key = (table, want_gen)
    with _reload_lock:
        with _data_lock:
            cur_gen = _gen.get(table)
            if cur_gen is not None and cur_gen >= want_gen:
                return                      # somebody already landed it
        ev = _reload_in_flight.get(key)
        leader = ev is None
        if leader:
            ev = threading.Event()
            _reload_in_flight[key] = ev

    if not leader:
        # Bounded: if the leader dies without setting the event we must not park
        # a request thread forever. On timeout we re-check and, if still stale,
        # fall through to Apache rather than start a second full rebuild.
        if not ev.wait(timeout=_RELOAD_WAIT_S):
            _stats["reload_waits_timed_out"] += 1
            print(f"[DATA-CACHE] waited {_RELOAD_WAIT_S:.0f}s for {table} "
                  f"gen={want_gen} reload that never completed - falling "
                  f"through to Apache", flush=True)
            raise TimeoutError("reload of %s for gen=%d did not complete in "
                               "%.0fs" % (table, want_gen, _RELOAD_WAIT_S))
        _stats["reload_waits"] += 1
        return

    try:
        _reload(conn, which=(table,))
    finally:
        with _reload_lock:
            _reload_in_flight.pop(key, None)
        ev.set()


def _ensure_current(conn):
    """Return db_now, having guaranteed RAM == disk, or None if unprovable."""
    gens, db_now = _read_gen(conn)
    if gens is None:
        return None
    with _data_lock:
        stale = [t for t in _GEN_TABLES if _gen.get(t) != gens[t]]
    if stale:
        # [RELOAD_IS_SINGLE_FLIGHT_V1] was a bare _reload(conn) here, on every
        # concurrent request that observed the same new generation.
        # [DATA_CACHE_GEN_V3] and it reloaded BOTH tables however small the
        # change. Only the table that moved is rebuilt now.
        for t in stale:
            _reload_once(conn, t, gens[t])
        with _data_lock:
            if any(_gen.get(t) != gens[t] for t in _GEN_TABLES):
                # The rebuild we rode landed a DIFFERENT generation than the one
                # we read. Gen only moves forward, so this is a newer snapshot -
                # but our db_now belongs to the older read and freshness is
                # decided on it. Unprovable for THIS request: fall through.
                return None
    return db_now


# -- matching (must agree with api.php, not merely resemble it) ----------------

def _eq_ci(a, b) -> bool:
    """utf8mb4_general_ci equality, which is what api.php's WHERE does."""
    if a is None:
        return False
    return str(a).casefold() == str(b).casefold()


def _matches(row: Dict[str, Any], filters: Dict[str, str]) -> bool:
    for k, v in filters.items():
        if not _eq_ci(row.get(k), v):
            return False
    return True


def _parse_api_path(path: str) -> Optional[Dict[str, str]]:
    if not path or "api.php" not in path:
        return None
    try:
        q = urlparse(path).query
        return {k: (v[0] if v else "")
                for k, v in parse_qs(q, keep_blank_values=True).items()}
    except Exception:
        return None


def _int_or_zero(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _apply_order_limit(rows: List[Dict[str, Any]], params: Dict[str, str],
                       cap: int) -> List[Dict[str, Any]]:
    """api.php orders THEN limits. The old code limited during the scan, which
    returned a different row set than the query it stood in for."""
    order = params.get("order")
    if order and order in _SENSOR_COLS:
        rows = sorted(rows, key=lambda r: (r.get(order) is None, str(r.get(order))))
    limit = _int_or_zero(params.get("limit"))
    if 0 < limit <= cap:
        rows = rows[:limit]
    return rows


# -- public -------------------------------------------------------------------

def try_intercept(path: str, body_text: str) -> Optional[str]:
    """Serve a READ from the proven-current materialization, or None to fall
    through to Apache. Never intercepts a write."""
    if not _ENABLED:
        return None

    params = _parse_api_path(path)
    if params is None:
        return None

    entity = params.get("entity", "")
    action = params.get("action", "")

    # Reads only. Writes are api.php's, always.
    if (entity, action) not in (("sensors", "values"), ("sensors", "list"),
                                ("sensors", "get"), ("sensor_data", "get")):
        return None

    # [DATACACHE_LIKE_FALLTHROUGH_V1] LIKE is not expressible in RAM.
    if any(k.endswith("__like") for k in params):
        _stats["fell_through"] += 1
        return None

    # Any filter key we do not implement goes to MySQL. This is an ALLOW-list on
    # purpose: the deny-list this replaces missed fresh_s and answered rows:[].
    unknown = [k for k in params
               if k not in _CONTROL_KEYS and k not in _SENSOR_COLS]
    if unknown:
        _stats["fell_through"] += 1
        return None

    try:
        conn = _get_conn()
        if not _check_wiring(conn):
            return None
        db_now = _ensure_current(conn)
        if db_now is None:
            return None
    except Exception as e:
        print(f"[DATA-CACHE] falling through to Apache: {e!r}", flush=True)
        _stats["fell_through"] += 1
        return None

    filters = {k: v for k, v in params.items() if k in _SENSOR_COLS}
    fresh_s = _int_or_zero(params.get("fresh_s"))

    if entity == "sensors" and action == "get":
        sid = params.get("SensorID")
        if sid is None:
            return None
        with _data_lock:
            rows = [dict(r) for r in _sensors if _eq_ci(r.get("SensorID"), sid)]
        _stats["reads_served"] += 1
        return json.dumps({"ok": True, "row": rows[0] if rows else None})

    if entity == "sensor_data" and action == "get":
        sid = params.get("SensorID")
        if sid is None:
            return None
        with _data_lock:
            hit = None
            for k, r in _sensor_data.items():
                if _eq_ci(k, sid):
                    hit = {c: v for c, v in r.items() if c != "UpdatedAtEpoch"}
                    break
        _stats["reads_served"] += 1
        return json.dumps({"ok": True, "row": hit})

    if entity == "sensors" and action == "list":
        # api.php: SELECT * FROM Sensor {WHERE} {ORDER} {LIMIT 1..1000}
        with _data_lock:
            rows = [dict(r) for r in _sensors if _matches(r, filters)]
        rows = _apply_order_limit(rows, params, 1000)
        _stats["reads_served"] += 1
        return json.dumps({"ok": True, "rows": rows})

    # sensors/values -- the convergence read.
    # api.php: SELECT s.<cols>, d.jsonData, d.UpdatedAt, UNIX_TIMESTAMP(d.UpdatedAt)
    #          FROM Sensor s LEFT JOIN SensorData d ON d.SensorID = s.SensorID
    #          {WHERE}{fresh}{ORDER}{LIMIT 1..5000}
    parse = params.get("parse") in ("1", "true")
    out: List[Dict[str, Any]] = []
    with _data_lock:
        for s in _sensors:
            if not _matches(s, filters):
                continue
            sd = _sensor_data.get(s.get("SensorID"))
            # [ENVELOPE_TS_V1] freshness is decided on the STORE's clock. db_now
            # came from UNIX_TIMESTAMP() in the same statement as Gen, so this is
            # the same clock and the same instant the query would have used.
            if fresh_s > 0:
                epoch = (sd or {}).get("UpdatedAtEpoch")
                # A LEFT JOIN row with no SensorData has NULL UpdatedAt, and
                # `NULL >= NOW() - INTERVAL n SECOND` is NULL, so MySQL drops it.
                if epoch is None or int(epoch) < (db_now - fresh_s):
                    continue
            row = {
                "SensorID":      s.get("SensorID"),
                "FrogID":        s.get("FrogID"),
                "SensorAddress": s.get("SensorAddress"),
                "SensorNetwork": s.get("SensorNetwork"),
                "SensorName":    s.get("SensorName"),
                "SensorType":    s.get("SensorType"),
                "Tags":          s.get("Tags"),
                "jsonData":      (sd or {}).get("jsonData"),
                "UpdatedAt":     (sd or {}).get("UpdatedAt"),
                "UpdatedAtEpoch": (sd or {}).get("UpdatedAtEpoch"),
            }
            if parse and isinstance(row["jsonData"], str) and row["jsonData"] != "":
                try:
                    row["data"] = json.loads(row["jsonData"])
                except (ValueError, TypeError):
                    pass
            out.append(row)

    out = _apply_order_limit(out, params, 5000)
    _stats["reads_served"] += 1
    # [API_PHP_WIRE_TYPES_V1] No default= here. It was `default=str`, which
    # silently stringified anything unserialisable and masked the datetime that
    # made sensor_data/get fail 100% of the time. Types are converted at load;
    # anything else non-serialisable now raises and names itself.
    return json.dumps({"ok": True, "rows": out})


def get_stats() -> Dict[str, Any]:
    with _data_lock:
        return {**_stats, "gen": dict(_gen), "sensors_cached": len(_sensors),
                "data_rows_cached": len(_sensor_data), "wiring_ok": _wiring_ok}
