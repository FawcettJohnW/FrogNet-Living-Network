#!/usr/bin/env python3
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
proxy/cache/semcache_db.py

Proxy-side MySQL cache for SAME/DIFF protocol.

Stores responses by same_id for quick lookup when daemon returns RESP_SAME.
Supports both semantic and raw HTTP responses.

v4.4: Replaced MySQLConnectionPool with direct thread-local connections.
      PooledMySQLConnection wraps the real connection, hiding the socket
      and making it impossible to reliably set recv() timeouts.  Direct
      connections use conn._socket.sock - same path as daemon side.
"""

from __future__ import annotations

import os
import threading
from typing import Optional, Tuple

import mysql.connector

from core.codec import REQ_HASH_LEN

# [SEMCACHE_PROXY_LZ4_V1] Store ReplyBytes compressed at rest.  The proxy
# cache is the one place a compression engine was keeping full, fat bodies;
# lz4 is already a node dependency.  A per-row Compressed flag keeps old
# uncompressed rows readable and lets the codec be swapped later.
#
# [NO_FALLBACK_V1] The guard here was `except Exception: _lz4 = None;
# _HAVE_LZ4 = False`, with a comment on the same line reading "lz4 always
# present on nodes". Both cannot be true: either it is a dependency, in which
# case its absence is a broken install, or it is optional, in which case the
# code must work without it. It did not work without it - _decode_reply()
# called _lz4.decompress() unconditionally for any row with Compressed=1, so on
# a node where the guard fired, every read of an already-compressed row raised
# AttributeError on None. The "fallback" only ever covered writes, and the
# import comment was the correct statement. Keep the dependency, drop the flag.
import lz4.frame as _lz4


def _encode_reply(b):
    """Return (stored_bytes, compressed_flag).

    [NO_FALLBACK_V1] The inner `except Exception: return b, 0` is gone. It was
    documented as "store raw so a write never fails for compression's sake",
    but a compressor that raises on ordinary bytes is a broken compressor, and
    the row it wrote was silently marked Compressed=0 - so the corruption was
    persisted as a legitimate-looking uncompressed row and nothing anywhere
    recorded that lz4 had failed. Let it raise: the caller's write fails and
    names the reason.
    """
    if b:
        return _lz4.compress(b), 1
    return (b or b""), 0


def _decode_reply(b, compressed):
    """Inverse of _encode_reply.  compressed=0 (old rows / raw) returns as-is."""
    if compressed and b:
        return _lz4.decompress(bytes(b))
    return bytes(b) if b else b""


_conn_local = threading.local()

# [SEMCACHE_LOCAL_MYSQL_V1] MySQL listens on loopback only; the semantic cache
# uses the LOCAL MySQL. Shared/current-value data reaches the elected data host
# via api.php (HTTP), NOT a direct remote MySQL connection - do NOT point this at
# databasehost.frognet:3306 (closed off-box -> ECONNREFUSED, breaks the daemon).
_DB_HOST = os.environ.get("FROGNET_DB_HOST", "127.0.0.1")
_DB_PORT = int(os.environ.get("FROGNET_DB_PORT", "3306"))
_DB_USER = os.environ.get("FROGNET_DB_USER", "FrogUser")
# [SHIP_THE_CODE_TOKENIZE_THE_SECRET_V1] This is the installer's injection
# SITE (SECRET_INJECT_FILES, phase C1b), so the literal is rewritten on every
# install. It held a LIVE pond password, which meant the source shipped a
# working credential and an uninjected node connected anyway instead of
# failing. The placeholder is what C1b substitutes; if injection did not run,
# auth fails loudly rather than silently succeeding.
_DB_PASS = os.environ.get("FROGNET_DB_PASS", "__FROGNET_DB_PASS__")
_DB_NAME = os.environ.get("FROGNET_DB_NAME", "FrogNet")
_DB_SOCK_TIMEOUT = float(os.environ.get("FROGNET_DB_SOCK_TIMEOUT", "10"))


def _set_sock_timeout(conn):
    """Set socket-level timeout so recv() NEVER blocks forever.
    Returns True if set, False if socket not found.
    """
    try:
        sock = conn._socket.sock
        if sock is not None:
            sock.settimeout(_DB_SOCK_TIMEOUT)
            return True
    except (AttributeError, TypeError):
        pass
    return False


def _get_conn():
    """Get a per-thread MySQL connection. Reconnects if stale."""
    conn = getattr(_conn_local, "conn", None)

    if conn is not None:
        # Set timeout BEFORE is_connected() - is_connected() calls
        # cmd_ping which blocks forever if timeout was lost.
        if not _set_sock_timeout(conn):
            _kill_conn()
            conn = None

    if conn is None or not conn.is_connected():
        conn = mysql.connector.connect(
            host=_DB_HOST,
            port=_DB_PORT,
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
            raise RuntimeError("cannot set socket timeout on fresh MySQL connection")
        _conn_local.conn = conn
    return conn


def _kill_conn():
    """Destroy the thread-local connection unconditionally."""
    conn = getattr(_conn_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _conn_local.conn = None


def close_thread_conn():
    """Close this thread's MySQL connection.  Call on thread exit.

    Proxy handler threads from ThreadingMixIn are short-lived (one per
    HTTP request).  Without this, each handler thread leaves a sleeping
    MySQL connection that persists until wait_timeout expires.
    """
    _kill_conn()


def _with_retry(fn):
    """Execute fn(conn) with one retry on any DB error.

    First attempt uses the existing thread-local connection.
    On ANY exception: destroy connection, get fresh one, retry once.
    """
    conn = _get_conn()
    try:
        return fn(conn)
    except Exception:
        _kill_conn()
        conn = _get_conn()
        return fn(conn)


_TABLE_ENSURED = False


def ensure_table():
    """Ensure the proxy cache table exists. [ENSURE_TABLE_ONCE_V1] The DDL only
    needs to run once per process, but ensure_table() was called from
    handle_request on EVERY request - a MySQL connection acquire + 4 DDL
    round-trips per request that dominated burst CPU (18% in the py-spy
    flamegraph). proxy_main runs it once at startup; after the first successful
    ensure, per-request calls are a boolean no-op."""
    global _TABLE_ENSURED
    if _TABLE_ENSURED:
        return
    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS SemCacheProxy (
                    SameID BINARY(16) PRIMARY KEY,
                    ReqHash VARBINARY(32),
                    RawHash BINARY(32),
                    SemHash BINARY(32),
                    ReplyBytes MEDIUMBLOB,
                    ContentType VARCHAR(128),
                    IsRaw TINYINT DEFAULT 0,
                    HttpStatus SMALLINT,
                    HttpHeaders BLOB,
                    Compressed TINYINT DEFAULT 0,
                    CreatedAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UpdatedAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    LastReadAt DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_req_hash (ReqHash),
                    INDEX idx_last_read (LastReadAt)
                ) ENGINE=InnoDB
            """)
            # [SEMCACHE_PROXY_LZ4_V1 / SEMCACHE_PROXY_LRU_V1] Idempotent
            # migration for tables created by older revisions: add the
            # Compressed flag and the LastReadAt LRU column + its index.
            # ADD COLUMN/INDEX IF NOT EXISTS are MariaDB extensions, safe
            # to re-run; best-effort so they never block startup.
            for stmt in (
                "ALTER TABLE SemCacheProxy ADD COLUMN IF NOT EXISTS "
                "Compressed TINYINT DEFAULT 0",
                "ALTER TABLE SemCacheProxy ADD COLUMN IF NOT EXISTS "
                "LastReadAt DATETIME DEFAULT CURRENT_TIMESTAMP",
                "ALTER TABLE SemCacheProxy ADD INDEX IF NOT EXISTS "
                "idx_last_read (LastReadAt)",
            ):
                try:
                    cur.execute(stmt)
                except Exception as e:
                    print(f"[PROXY-CACHE] migration step skipped: {e}",
                          flush=True)
        finally:
            try:
                cur.close()
            except Exception:
                pass
    _with_retry(_do)
    _TABLE_ENSURED = True


def get_by_sameid(same_id: bytes) -> Optional[Tuple[bytes, str]]:
    """
    Get cached semantic response by same_id.
    
    Returns:
        Tuple of (response_body, content_type) if found, None otherwise.
    """
    if not same_id or len(same_id) != 16:
        return None
    
    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT ReplyBytes, ContentType, Compressed FROM SemCacheProxy WHERE SameID = %s AND IsRaw = 0 LIMIT 1",
                (same_id,)
            )
            row = cur.fetchone()
            if row:
                # [SEMCACHE_PROXY_LRU_V1] advance LRU clock on a real read hit
                try:
                    cur.execute("UPDATE SemCacheProxy SET LastReadAt = NOW() WHERE SameID = %s", (same_id,))
                except Exception:
                    pass
        finally:
            try:
                cur.close()
            except Exception:
                pass
        if not row:
            return None
        body, ctype, comp = row
        return (_decode_reply(body, comp), ctype or "application/json")
    
    return _with_retry(_do)


def get_raw_by_sameid(same_id: bytes) -> Optional[Tuple[bytes, int, bytes]]:
    if not same_id or len(same_id) != 16:
        return None
    
    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT ReplyBytes, HttpStatus, HttpHeaders, Compressed FROM SemCacheProxy WHERE SameID = %s AND IsRaw = 1 LIMIT 1",
                (same_id,)
            )
            row = cur.fetchone()
            if row:
                try:
                    cur.execute("UPDATE SemCacheProxy SET LastReadAt = NOW() WHERE SameID = %s", (same_id,))
                except Exception:
                    pass
        finally:
            try:
                cur.close()
            except Exception:
                pass
        if not row:
            return None
        body, status, headers, comp = row
        return (
            _decode_reply(body, comp),
            int(status) if status else 200,
            bytes(headers) if headers else b""
        )
    
    return _with_retry(_do)


def upsert(
    same_id: bytes,
    req_hash: bytes,
    raw_hash: bytes,
    sem_hash: bytes,
    response_body: bytes,
    content_type: str
) -> None:
    """Store semantic response."""
    if len(same_id) != 16:
        raise ValueError(f"same_id must be 16 bytes, got {len(same_id)}")
    
    def _do(conn):
        cur = conn.cursor()
        stored, comp = _encode_reply(response_body)
        try:
            cur.execute(
                """
                INSERT INTO SemCacheProxy (SameID, ReqHash, RawHash, SemHash, ReplyBytes, ContentType, IsRaw, Compressed)
                VALUES (%s, %s, %s, %s, %s, %s, 0, %s)
                ON DUPLICATE KEY UPDATE
                    ReqHash = VALUES(ReqHash),
                    RawHash = VALUES(RawHash),
                    SemHash = VALUES(SemHash),
                    ReplyBytes = VALUES(ReplyBytes),
                    ContentType = VALUES(ContentType),
                    IsRaw = 0,
                    Compressed = VALUES(Compressed)
                """,
                (same_id, req_hash, raw_hash, sem_hash, stored, content_type, comp)
            )
        finally:
            try:
                cur.close()
            except Exception:
                pass
    
    _with_retry(_do)


def upsert_raw(
    same_id: bytes,
    req_hash: bytes,
    raw_hash: bytes,
    body: bytes,
    status: int,
    headers: bytes,
    content_type: str
) -> None:
    """Store raw HTTP response."""
    if len(same_id) != 16:
        raise ValueError(f"same_id must be 16 bytes, got {len(same_id)}")
    
    def _do(conn):
        cur = conn.cursor()
        stored, comp = _encode_reply(body)
        try:
            cur.execute(
                """
                INSERT INTO SemCacheProxy (SameID, ReqHash, RawHash, SemHash, ReplyBytes, ContentType, IsRaw, HttpStatus, HttpHeaders, Compressed)
                VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    ReqHash = VALUES(ReqHash),
                    RawHash = VALUES(RawHash),
                    SemHash = VALUES(SemHash),
                    ReplyBytes = VALUES(ReplyBytes),
                    ContentType = VALUES(ContentType),
                    IsRaw = 1,
                    HttpStatus = VALUES(HttpStatus),
                    HttpHeaders = VALUES(HttpHeaders),
                    Compressed = VALUES(Compressed)
                """,
                (same_id, req_hash, raw_hash, raw_hash, stored, content_type, status, headers, comp)
            )
        finally:
            try:
                cur.close()
            except Exception:
                pass
    
    _with_retry(_do)


# Request seen tracking (for REQ_REPEAT optimization)
# [SEEN_CACHE_BOUNDED_V1] _seen_cache was unbounded and never cleared: it grew
# with every distinct (target_ip, req_hash) for the life of the process, unlike
# _hash_cache (4096) and _hash_opcode (8192) which both evict. Bounded here on
# the same pattern, and given an explicit drop for [SPACE_TS_V1]: when a tuple
# space's sentinel moves, the data behind these entries may have changed under
# us, so "we have sent this before" is no longer a safe basis for REQ_REPEAT and
# the entries must go. Data continuity is not guaranteed.
_seen_lock = threading.RLock()
_seen_cache: dict = {}  # (target_ip, req_hash) -> True
_SEEN_MAX = int(os.environ.get("FROGNET_SEEN_CACHE_MAX", "8192"))
_SEEN_EVICT = max(1, _SEEN_MAX // 4)


def is_req_seen(target_ip: str, req_hash: bytes) -> bool:
    """Check if we've sent this request to this target before."""
    key = (target_ip, req_hash)
    with _seen_lock:
        return key in _seen_cache


def mark_req_seen(target_ip: str, req_hash: bytes) -> None:
    """Mark that we've sent this request to this target."""
    key = (target_ip, req_hash)
    with _seen_lock:
        _seen_cache[key] = True
        if len(_seen_cache) > _SEEN_MAX:
            # oldest-first: dict preserves insertion order
            for k in list(_seen_cache.keys())[:_SEEN_EVICT]:
                _seen_cache.pop(k, None)


def clear_all_seen() -> int:
    """[SPACE_TS_V1] Drop every seen-marker. Called when a tuple space's change
    sentinel moves: the cached responses behind these markers may no longer
    match what the database holds, so the next request must go out full rather
    than as a REQ_REPEAT. Returns how many markers were dropped."""
    with _seen_lock:
        n = len(_seen_cache)
        _seen_cache.clear()
        return n


def clear_req_seen(target_ip: str, req_hash: bytes) -> None:
    """Clear seen flag (on REQ_MISS)."""
    key = (target_ip, req_hash)
    with _seen_lock:
        _seen_cache.pop(key, None)


# -- Persistent request/response reference cache ------------------

_REFS_TABLE_ENSURED = False
_REFS_TABLE_LOCK = threading.RLock()


def ensure_refs_table() -> None:
    """Create the SemCacheProxyRefs table for persistent reference storage."""
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
                    CREATE TABLE IF NOT EXISTS SemCacheProxyRefs (
                        TargetIP   VARCHAR(45)  NOT NULL,
                        OpCode     INT UNSIGNED NOT NULL,
                        RefType    VARCHAR(4)   NOT NULL,
                        RefJSON    MEDIUMTEXT,
                        UpdatedAt  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                                   ON UPDATE CURRENT_TIMESTAMP,
                        PRIMARY KEY (TargetIP, OpCode, RefType)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
            finally:
                try:
                    cur.close()
                except Exception:
                    pass

        _with_retry(_do)
        _REFS_TABLE_ENSURED = True


def upsert_ref(target_ip: str, opcode: int, ref_type: str, ref_dict: dict) -> None:
    """Write-through: store a request or response reference to DB.

    ref_type is 'req' or 'resp'.
    ref_dict is the dict of field->value pairs (JSON-serialized for storage).
    """
    import json as _json
    ensure_refs_table()
    ref_json = _json.dumps(ref_dict, sort_keys=True, default=str)

    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO SemCacheProxyRefs (TargetIP, OpCode, RefType, RefJSON)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    RefJSON   = VALUES(RefJSON),
                    UpdatedAt = CURRENT_TIMESTAMP
                """,
                (target_ip, opcode, ref_type, ref_json),
            )
        finally:
            try:
                cur.close()
            except Exception:
                pass

    try:
        _with_retry(_do)
    except Exception as e:
        import sys
        print(f"[PROXY-CACHE] upsert_ref failed: {e!r}", file=sys.stderr, flush=True)


def delete_ref(target_ip: str, opcode: int, ref_type: str) -> None:
    """Delete a reference from persistent storage."""
    ensure_refs_table()

    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                "DELETE FROM SemCacheProxyRefs WHERE TargetIP=%s AND OpCode=%s AND RefType=%s",
                (target_ip, opcode, ref_type),
            )
        finally:
            try:
                cur.close()
            except Exception:
                pass

    try:
        _with_retry(_do)
    except Exception as e:
        import sys
        print(f"[PROXY-CACHE] delete_ref failed: {e!r}", file=sys.stderr, flush=True)


def load_all_refs() -> dict:
    """Load all persistent references from DB.

    Returns:
        {
            'req':  {(target_ip, opcode): {field: value, ...}, ...},
            'resp': {(target_ip, opcode): {field: value, ...}, ...},
        }
    """
    import json as _json
    ensure_refs_table()

    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT TargetIP, OpCode, RefType, RefJSON FROM SemCacheProxyRefs")
            rows = cur.fetchall()
        finally:
            try:
                cur.close()
            except Exception:
                pass

        result = {'req': {}, 'resp': {}}
        for target_ip, opcode, ref_type, ref_json in rows:
            if ref_type not in ('req', 'resp'):
                continue
            try:
                ref_dict = _json.loads(ref_json) if ref_json else {}
            except Exception:
                continue
            result[ref_type][(target_ip, int(opcode))] = ref_dict
        return result

    try:
        return _with_retry(_do)
    except Exception as e:
        import sys
        print(f"[PROXY-CACHE] load_all_refs failed: {e!r}", file=sys.stderr, flush=True)
        return {'req': {}, 'resp': {}}


# -- Cache eviction ------------------------------------------------
# [SEMCACHE_PROXY_LRU_V1] The proxy cache previously had NO eviction
# (the daemon side grew one; the proxy never did), so SemCacheProxy
# grew without bound.  Eviction is keyed off LastReadAt, advanced only
# on a real read hit (get_by_sameid / get_raw_by_sameid).  Cold rows
# die after `hours` of no reads; hot rows stay forever - which is the
# point on slow links: we never evict something we're actively serving
# and would otherwise have to re-fetch over the wire.
import sys as _sys

_EVICT_HOURS = int(os.environ.get("FROGNET_CACHE_EVICT_HOURS", "24"))
_eviction_started = False
_eviction_lock = threading.RLock()


def evict_stale(hours: int = _EVICT_HOURS) -> int:
    """Delete SemCacheProxy rows not READ in `hours`.  Returns count deleted."""
    def _do(conn):
        cur = conn.cursor()
        try:
            cur.execute(
                "DELETE FROM SemCacheProxy WHERE LastReadAt < NOW() - INTERVAL %s HOUR",
                (hours,)
            )
            return cur.rowcount
        finally:
            try:
                cur.close()
            except Exception:
                pass
    try:
        return _with_retry(_do)
    except Exception:
        return 0


def _eviction_loop() -> None:
    import time
    while True:
        time.sleep(3600)
        try:
            n = evict_stale()
            if n > 0:
                print(f"[PROXY-CACHE] evicted {n} stale rows", file=_sys.stderr, flush=True)
        except Exception:
            pass


def start_eviction() -> None:
    """Start the background eviction thread (idempotent)."""
    global _eviction_started
    with _eviction_lock:
        if _eviction_started:
            return
        _eviction_started = True
        t = threading.Thread(target=_eviction_loop, daemon=True)
        t.start()
