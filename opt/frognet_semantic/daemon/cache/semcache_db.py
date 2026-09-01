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
daemon/cache/semcache_db.py

Daemon-side SAME/DIFF cache database operations.
Stores both requests (for REQ_REPEAT re-execution) and responses.

v4.1: ReqHash uses VARBINARY(32) to support 16-byte truncated hashes.
      All cursor operations wrapped in try/finally to prevent
      'Commands out of sync' errors under concurrent thread access.

v4.2: Retry-with-reconnect on any DB error.  mysql-connector with
      use_pure=True has fragile internal state that corrupts under
      concurrent thread-pool use (ProgrammingError: "Cursor is not
      connected", "weakly-referenced object no longer exists",
      "bytearray index out of range").  is_connected() lies about
      corrupted connections.  Fix: on ANY exception, destroy the
      connection, get a fresh one, retry once.
      Also fixed evict_stale: wrong column name (UpdatedAt->UpdatedUTC)
      and conn.close() in finally was killing the thread-local conn.
"""

import os
import sys
import threading
import time
from collections import OrderedDict
from typing import Optional, Tuple

from core.codec import REQ_HASH_LEN

# [SEMCACHE_LOCAL_MYSQL_V1] Local MySQL only; shared data goes via api.php, not a
# remote MySQL connection to databasehost.frognet:3306 (closed off-box).
_DB_HOST = os.environ.get("FROGNET_DB_HOST", "127.0.0.1")
_DB_USER = os.environ.get("FROGNET_DB_USER", "FrogUser")
# [SHIP_THE_CODE_TOKENIZE_THE_SECRET_V1] This is the installer's injection
# SITE (SECRET_INJECT_FILES, phase C1b), so the literal is rewritten on every
# install. It held a LIVE pond password, which meant the source shipped a
# working credential and an uninjected node connected anyway instead of
# failing. The placeholder is what C1b substitutes; if injection did not run,
# auth fails loudly rather than silently succeeding.
_DB_PASS = os.environ.get("FROGNET_DB_PASS", "__FROGNET_DB_PASS__")
_DB_NAME = os.environ.get("FROGNET_DB_NAME", "FrogNet")

_conn_local = threading.local()


_DB_SOCK_TIMEOUT = float(os.environ.get("FROGNET_DB_SOCK_TIMEOUT", "10"))


def _set_sock_timeout(conn):
    """Prevent recv() from blocking forever on dead MySQL.

    v4.4: returns bool. False means timeout could not be set and the
    connection MUST NOT be used (recv will block forever).
    """
    try:
        sock = conn._socket.sock
        if sock:
            sock.settimeout(_DB_SOCK_TIMEOUT)
            return True
    except Exception:
        pass
    return False


def _get_conn():
    """Get a per-thread MySQL connection.  Reconnects if stale."""
    import mysql.connector
    conn = getattr(_conn_local, "conn", None)

    if conn is not None:
        # v4.4: set timeout BEFORE is_connected() check.
        # is_connected() calls cmd_ping which blocks forever
        # if the socket timeout was lost.
        if not _set_sock_timeout(conn):
            _kill_conn()
            conn = None

    # [NO_PING_PER_CALL_V1] is_connected() adds a cmd_ping round-trip to
    # MySQL on every cache lookup.  Since databasehost.frognet is remote
    # (over the wg overlay) that ping costs the full link RTT per call.
    # _with_retry catches any failure on the real query and reconnects
    # via _kill_conn -> _get_conn, so a dead socket is recovered on the
    # next call at the cost of one wasted query attempt rather than a
    # mandatory ping per call.  Net: half the round-trips on hot paths.
    if conn is None:
        conn = mysql.connector.connect(
            host=_DB_HOST,
            user=_DB_USER,
            password=_DB_PASS,
            database=_DB_NAME,
            autocommit=True,
            use_pure=True,
            connection_timeout=int(_DB_SOCK_TIMEOUT),
        )
        if not _set_sock_timeout(conn):
            try:
                conn.close()
            except Exception:
                pass
            raise RuntimeError("FATAL: cannot set socket timeout on fresh MySQL connection")
        _conn_local.conn = conn
    return conn


def _kill_conn():
    """Destroy the thread-local connection unconditionally.

    v4.2: Called after any DB error.  is_connected() lies about connections
    with corrupted internal state, so we nuke it and force a fresh connect
    on the next _get_conn() call.
    """
    conn = getattr(_conn_local, "conn", None)
    _conn_local.conn = None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def close_thread_conn():
    """Close this thread's MySQL connection.  Call on thread exit.

    Session threads are short-lived (one per inbound TCP connection).
    Without this, each session thread leaves a sleeping MySQL connection
    that persists until wait_timeout expires on the server side.
    ThreadPoolExecutor workers should NOT call this - their connections
    are intentionally permanent.
    """
    _kill_conn()


def _with_retry(fn):
    """Execute fn(conn) with one retry on failure.

    First attempt uses the existing thread-local connection.
    On ANY exception: destroy the connection, get a fresh one, retry once.
    If the retry also fails, propagate the exception.
    """
    try:
        return fn(_get_conn())
    except Exception as e1:
        _kill_conn()
        try:
            return fn(_get_conn())
        except Exception as e2:
            _kill_conn()
            print(
                f"[DAEMON-CACHE] DB retry failed: first={e1!r} second={e2!r}",
                file=sys.stderr, flush=True,
            )
            raise


_TABLE_ENSURED = False
_TABLE_LOCK = threading.RLock()


# -- [SEMCACHE_LRU_V1] In-memory LRU in front of MySQL --------------
# Every REQ_REPEAT hits lookup_for_repeat().  Without an LRU layer
# each hit costs one MySQL query over the wg overlay to whichever
# node currently holds databasehost.frognet - typically 100ms+ RTT.
# An in-memory LRU sized to the hot working set eliminates that hop
# for the high-rate, low-cardinality requests (sensor get/list, echo).
#
# Capacity is set high enough to hold the entire hot set on a typical
# node; overridable via env.  Values are the same shape lookup_for_repeat
# returns: (req_blob, raw_hash, same_id, is_raw).
#
# Mutations:
#   * upsert / upsert_raw:    invalidate the entry (next read re-loads)
#   * lookup_for_repeat hit:  move to MRU end + queue LastReadAt flush
#   * lookup_for_repeat miss: SELECT, populate LRU, queue LastReadAt
#
# Negative caching: a None value (row absent in DB) is NOT cached.
# Adding a row produces an upsert that would invalidate the negative
# cache anyway, but the cross-node write path doesn't go through this
# process, so negative caching would serve stale "no such row" for
# however long the TTL is.  Cheaper and safer to just re-query on miss.
_LRU_MAX = int(os.environ.get("FROGNET_DAEMON_CACHE_LRU_MAX", "8192"))
_lru: "OrderedDict[bytes, Tuple[bytes, bytes, bytes, bool]]" = OrderedDict()
_lru_lock = threading.RLock()
_lru_hits = 0
_lru_misses = 0


def _lru_get(req_hash: bytes):
    """Return cached lookup_for_repeat tuple or None."""
    global _lru_hits, _lru_misses
    with _lru_lock:
        val = _lru.get(req_hash)
        if val is not None:
            _lru.move_to_end(req_hash)
            _lru_hits += 1
        else:
            _lru_misses += 1
    return val


def _lru_put(req_hash: bytes, val) -> None:
    """Insert val under req_hash; evict oldest if over capacity."""
    with _lru_lock:
        _lru[req_hash] = val
        _lru.move_to_end(req_hash)
        while len(_lru) > _LRU_MAX:
            _lru.popitem(last=False)


def _lru_invalidate(req_hash: bytes) -> None:
    """Drop the cached entry for req_hash (called on every write)."""
    with _lru_lock:
        _lru.pop(req_hash, None)


# -- [SEMCACHE_LRU_V1] Batched LastReadAt flusher -------------------
# Updating LastReadAt on every read would re-introduce the per-call
# write that this whole change is trying to eliminate.  Instead we
# collect read req_hashes in a bounded in-memory set and flush them
# to MySQL every _LRU_FLUSH_SEC seconds as a single UPDATE.
# The eviction loop reads LastReadAt for its DELETE, so flushes have
# to happen often enough that a row that was just read isn't evicted
# in the next sweep.  Eviction runs hourly; 30s flush is plenty.
_LRU_FLUSH_SEC = float(os.environ.get("FROGNET_DAEMON_CACHE_FLUSH_SEC", "30"))
_LRU_FLUSH_BATCH_MAX = int(os.environ.get("FROGNET_DAEMON_CACHE_FLUSH_BATCH", "1000"))
_pending_reads: set = set()
_pending_reads_lock = threading.Lock()
_flusher_started = False
_flusher_lock = threading.RLock()


def _mark_read(req_hash: bytes) -> None:
    """Queue a req_hash for the next LastReadAt batch flush."""
    if not req_hash:
        return
    with _pending_reads_lock:
        _pending_reads.add(req_hash)


def _flush_pending_reads() -> int:
    """UPDATE LastReadAt for every queued req_hash.  Returns rows touched."""
    with _pending_reads_lock:
        if not _pending_reads:
            return 0
        batch = list(_pending_reads)[:_LRU_FLUSH_BATCH_MAX]
        for h in batch:
            _pending_reads.discard(h)
    if not batch:
        return 0
    placeholders = ",".join(["%s"] * len(batch))

    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                f"UPDATE SemCacheDaemon SET LastReadAt = NOW() "
                f"WHERE ReqHash IN ({placeholders})",
                tuple(batch),
            )
            return cur.rowcount
        finally:
            try:
                cur.close()
            except Exception:
                pass
    try:
        return _with_retry(_do)
    except Exception as e:
        # If the UPDATE fails, the queued reads are lost - that's
        # acceptable, worst case is premature eviction of warm rows
        # that will repopulate on their next read.
        print(f"[DAEMON-CACHE] flush_pending_reads failed: {e!r}",
              file=sys.stderr, flush=True)
        return 0


def _flusher_loop() -> None:
    """Background: every _LRU_FLUSH_SEC, flush queued LastReadAt updates."""
    while True:
        time.sleep(_LRU_FLUSH_SEC)
        try:
            n = _flush_pending_reads()
            if n:
                # Only log when something happened, to keep logs quiet.
                pass
        except Exception as e:
            print(f"[DAEMON-CACHE] flusher loop error: {e!r}",
                  file=sys.stderr, flush=True)


def start_flusher() -> None:
    """Start the LastReadAt batch flusher thread (idempotent)."""
    global _flusher_started
    with _flusher_lock:
        if _flusher_started:
            return
        _flusher_started = True
        t = threading.Thread(target=_flusher_loop, daemon=True,
                             name="SemCacheLastReadFlusher")
        t.start()


def lru_stats() -> dict:
    """Return LRU hit/miss counts + size for telemetry."""
    with _lru_lock:
        return {
            "size": len(_lru),
            "max": _LRU_MAX,
            "hits": _lru_hits,
            "misses": _lru_misses,
            "pending_reads": len(_pending_reads),
        }


def ensure_table() -> None:
    global _TABLE_ENSURED
    if _TABLE_ENSURED:
        return
    with _TABLE_LOCK:
        if _TABLE_ENSURED:
            return

        def _do(conn):
            cur = conn.cursor()
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS SemCacheDaemon (
                        CacheID      BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                        ReqHash      VARBINARY(32) NOT NULL UNIQUE,
                        ReqBlob      MEDIUMBLOB,
                        RawHash      BINARY(32),
                        SameID       BINARY(16),
                        SemHash      BINARY(32),
                        SemBlob      MEDIUMBLOB,
                        Opcode       INT UNSIGNED,
                        IsRaw        TINYINT UNSIGNED DEFAULT 0,
                        HttpStatus   INT UNSIGNED,
                        HttpHeaders  MEDIUMBLOB,
                        CreatedAt    DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UpdatedUTC   DATETIME DEFAULT CURRENT_TIMESTAMP,
                        LastReadAt   DATETIME DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_last_read (LastReadAt)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                # [SEMCACHE_LRU_V1] Idempotent migration for existing
                # tables created by older revisions of this file.
                # Adds LastReadAt + its index; drops the unused idx_same
                # (the daemon never SELECTs by SameID, only by ReqHash).
                # ADD COLUMN IF NOT EXISTS / DROP INDEX IF EXISTS are
                # MariaDB extensions; safe to re-run.
                for stmt in (
                    "ALTER TABLE SemCacheDaemon "
                    "ADD COLUMN IF NOT EXISTS LastReadAt DATETIME "
                    "DEFAULT CURRENT_TIMESTAMP",
                    "ALTER TABLE SemCacheDaemon "
                    "ADD COLUMN IF NOT EXISTS CreatedAt DATETIME "
                    "DEFAULT CURRENT_TIMESTAMP",
                    "ALTER TABLE SemCacheDaemon "
                    "ADD INDEX IF NOT EXISTS idx_last_read (LastReadAt)",
                    "ALTER TABLE SemCacheDaemon DROP INDEX IF EXISTS idx_same",
                    "ALTER TABLE SemCacheDaemon DROP INDEX IF EXISTS idx_updated",
                ):
                    try:
                        cur.execute(stmt)
                    except Exception as e:
                        # Migrations are best-effort; never block startup.
                        print(f"[DAEMON-CACHE] migration step skipped: "
                              f"{stmt[:60]}... err={e!r}",
                              file=sys.stderr, flush=True)
            finally:
                try:
                    cur.close()
                except Exception:
                    pass

        _with_retry(_do)
        _TABLE_ENSURED = True


def lookup_for_repeat(req_hash: bytes) -> Optional[Tuple[bytes, bytes, bytes, bool]]:
    """
    Look up cached request for REQ_REPEAT re-execution.
    Returns: (req_blob, raw_hash, same_id, is_raw) or None if not found.

    [SEMCACHE_LRU_V1] Hot path: in-memory LRU dict; no MySQL hop.
    Cold path: SELECT from MySQL, populate LRU, return.  Either way,
    queue the req_hash for batched LastReadAt update so eviction can
    distinguish hot rows from cold rows.
    """
    if not req_hash:
        return None

    # Fast path: in-memory hit, no MySQL round-trip.
    cached = _lru_get(req_hash)
    if cached is not None:
        _mark_read(req_hash)
        return cached

    ensure_table()
    # Lazy-start the flusher on first call so any process that imports
    # this module without using it doesn't spawn a thread.
    start_flusher()

    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT ReqBlob, RawHash, SameID, IsRaw
                FROM SemCacheDaemon
                WHERE ReqHash = %s AND ReqBlob IS NOT NULL
                """,
                (req_hash,),
            )
            row = cur.fetchone()
        finally:
            try:
                cur.close()
            except Exception:
                pass
        if not row:
            return None
        req_blob, raw_hash, same_id, is_raw = row
        if not req_blob:
            return None
        return (req_blob, raw_hash or b"", same_id or b"", bool(is_raw))

    result = _with_retry(_do)
    if result is not None:
        _lru_put(req_hash, result)
        _mark_read(req_hash)
    return result


def upsert(
    req_hash: bytes,
    raw_hash: bytes,
    same_id: bytes,
    sem_hash: bytes,
    sem_blob: bytes,
    opcode: Optional[int] = None,
    req_blob: Optional[bytes] = None,
) -> None:
    """Upsert semantic response cache entry."""
    ensure_table()
    # [SEMCACHE_LRU_V1] Drop any cached entry so the next lookup_for_repeat
    # re-reads the fresh row instead of serving a stale tuple.
    _lru_invalidate(req_hash)

    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO SemCacheDaemon
                    (ReqHash, ReqBlob, RawHash, SameID, SemHash, SemBlob, OpCode, IsRaw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 0)
                ON DUPLICATE KEY UPDATE
                    ReqBlob   = COALESCE(%s, ReqBlob),
                    RawHash   = %s,
                    SameID    = %s,
                    SemHash   = %s,
                    SemBlob   = %s,
                    OpCode    = %s,
                    IsRaw     = 0
                """,
                (
                    req_hash, req_blob, raw_hash, same_id, sem_hash, sem_blob, opcode,
                    req_blob, raw_hash, same_id, sem_hash, sem_blob, opcode,
                ),
            )
        finally:
            try:
                cur.close()
            except Exception:
                pass

    _with_retry(_do)


def upsert_raw(
    req_hash: bytes,
    raw_hash: bytes,
    same_id: bytes,
    body: bytes,
    status: int,
    headers: bytes,
    req_blob: Optional[bytes] = None,
) -> None:
    """Upsert raw HTTP response cache entry."""
    ensure_table()
    # [SEMCACHE_LRU_V1] Drop any cached entry so the next lookup_for_repeat
    # re-reads the fresh row.
    _lru_invalidate(req_hash)

    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO SemCacheDaemon
                    (ReqHash, ReqBlob, RawHash, SameID, SemBlob, IsRaw, HttpStatus, HttpHeaders)
                VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                ON DUPLICATE KEY UPDATE
                    ReqBlob     = COALESCE(%s, ReqBlob),
                    RawHash     = %s,
                    SameID      = %s,
                    SemBlob     = %s,
                    IsRaw       = 1,
                    HttpStatus  = %s,
                    HttpHeaders = %s
                """,
                (
                    req_hash, req_blob, raw_hash, same_id, body, status, headers,
                    req_blob, raw_hash, same_id, body, status, headers,
                ),
            )
        finally:
            try:
                cur.close()
            except Exception:
                pass

    _with_retry(_do)


# -- Cache eviction ------------------------------------------------
_EVICT_HOURS = int(os.environ.get("FROGNET_CACHE_EVICT_HOURS", "24"))
_eviction_started = False
_eviction_lock = threading.RLock()


def evict_stale(hours: int = _EVICT_HOURS) -> int:
    """Delete SemCacheDaemon rows that haven't been READ in `hours`.

    [SEMCACHE_LRU_V1] Eviction is keyed off LastReadAt, not UpdatedUTC.
    A row that gets re-upserted with identical content does NOT have
    its LastReadAt advanced; only an actual lookup_for_repeat() hit
    advances it (via the batched background flusher in _lru_loop).
    This gives a true LRU-by-access policy: cold rows die after
    `hours` of no reads, hot rows stay forever.

    Returns count deleted.
    """

    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                "DELETE FROM SemCacheDaemon "
                "WHERE LastReadAt < NOW() - INTERVAL %s HOUR",
                (hours,)
            )
            return cur.rowcount
        finally:
            try:
                cur.close()
            except Exception:
                pass
        # v4.2: removed conn.close() from finally - was killing the
        # thread-local connection for all future operations on this thread

    try:
        return _with_retry(_do)
    except Exception:
        return 0


def _eviction_loop() -> None:
    """Background thread: evict stale rows hourly."""
    import time
    while True:
        time.sleep(3600)
        try:
            n = evict_stale()
            if n > 0:
                print(f"[DAEMON-CACHE] evicted {n} stale rows", file=sys.stderr, flush=True)
        except Exception:
            pass


def start_eviction() -> None:
    """Start background eviction thread (idempotent)."""
    global _eviction_started
    with _eviction_lock:
        if _eviction_started:
            return
        _eviction_started = True
        t = threading.Thread(target=_eviction_loop, daemon=True)
        t.start()


# -- Persistent request/response reference cache ------------------

_REFS_TABLE_ENSURED = False
_REFS_TABLE_LOCK = threading.RLock()


def ensure_refs_table() -> None:
    """Create the SemCacheDaemonRefs table for persistent reference storage."""
    global _REFS_TABLE_ENSURED
    if _REFS_TABLE_ENSURED:
        return
    with _REFS_TABLE_LOCK:
        if _REFS_TABLE_ENSURED:
            return

        def _do(conn):
            cur = conn.cursor()
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS SemCacheDaemonRefs (
                        PeerIP     VARCHAR(45)  NOT NULL,
                        OpCode     INT UNSIGNED NOT NULL,
                        RefType    VARCHAR(4)   NOT NULL,
                        RefJSON    MEDIUMTEXT,
                        UpdatedAt  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                                   ON UPDATE CURRENT_TIMESTAMP,
                        PRIMARY KEY (PeerIP, OpCode, RefType)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
            finally:
                try:
                    cur.close()
                except Exception:
                    pass

        _with_retry(_do)
        _REFS_TABLE_ENSURED = True


def upsert_ref(peer_ip: str, opcode: int, ref_type: str, ref_dict: dict) -> None:
    """Write-through: store a request or response reference to DB."""
    import json as _json
    ensure_refs_table()
    ref_json = _json.dumps(ref_dict, sort_keys=True, default=str)

    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO SemCacheDaemonRefs (PeerIP, OpCode, RefType, RefJSON)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    RefJSON   = VALUES(RefJSON),
                    UpdatedAt = CURRENT_TIMESTAMP
                """,
                (peer_ip, opcode, ref_type, ref_json),
            )
        finally:
            try:
                cur.close()
            except Exception:
                pass

    try:
        _with_retry(_do)
    except Exception as e:
        print(
            f"[DAEMON-CACHE] upsert_ref failed: {e!r}",
            file=sys.stderr, flush=True,
        )


def delete_ref(peer_ip: str, opcode: int, ref_type: str) -> None:
    """Delete a reference from persistent storage."""
    ensure_refs_table()

    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                "DELETE FROM SemCacheDaemonRefs WHERE PeerIP=%s AND OpCode=%s AND RefType=%s",
                (peer_ip, opcode, ref_type),
            )
        finally:
            try:
                cur.close()
            except Exception:
                pass

    try:
        _with_retry(_do)
    except Exception as e:
        print(
            f"[DAEMON-CACHE] delete_ref failed: {e!r}",
            file=sys.stderr, flush=True,
        )


def load_all_refs() -> dict:
    """Load all persistent references from DB.

    Returns:
        {
            'req':  {(peer_ip, opcode): {field: value, ...}, ...},
            'resp': {(peer_ip, opcode): {field: value, ...}, ...},
        }
    """
    import json as _json
    ensure_refs_table()

    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT PeerIP, OpCode, RefType, RefJSON FROM SemCacheDaemonRefs")
            rows = cur.fetchall()
        finally:
            try:
                cur.close()
            except Exception:
                pass

        result = {'req': {}, 'resp': {}}
        for peer_ip, opcode, ref_type, ref_json in rows:
            if ref_type not in ('req', 'resp'):
                continue
            try:
                ref_dict = _json.loads(ref_json) if ref_json else {}
            except Exception:
                continue
            result[ref_type][(peer_ip, int(opcode))] = ref_dict
        return result

    try:
        return _with_retry(_do)
    except Exception as e:
        print(
            f"[DAEMON-CACHE] load_all_refs failed: {e!r}",
            file=sys.stderr, flush=True,
        )
        return {'req': {}, 'resp': {}}
