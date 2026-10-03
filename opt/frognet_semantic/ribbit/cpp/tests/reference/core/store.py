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
# core/store.py - TemplateStore v3 (pooled, reconnect-safe) + store_templates restored
#
# [PER_CALL_CONN_V1] Removed self._conn instance attribute and the
# _acquire / _release / _discard methods that wrote to it.  Every
# DB-touching method now takes its conn as a local from _with_conn().
#
# Why: TemplateLoader holds a single shared TemplateStore instance
# (see daemon/templates/loader.py:21).  All 16 ThreadPoolExecutor
# workers calling lookup_by_opcode through that one loader were
# clobbering each other's self._conn slot.  Symptom captured in
# strace: all 16 workers blocked in recvfrom() on the same fd while
# the rest of the 8-slot pool sat idle, MariaDB showing every conn
# as Sleep, no MDL waits, no in-flight query.  Socket timeouts also
# never fired - under a 16-way race on one fd, only one thread's
# recv unblocks per cycle and the rest just queue back into the
# next acquire->same-conn path.

import json
import os
import time
import zlib
import queue
import threading
from typing import Any, Dict, Optional, Tuple, List

import mysql.connector
from mysql.connector import errors as mysql_errors

from .template import RequestTemplate, ReplyTemplate


def _loads_json_maybe(x: Any, default: Any):
    if x is None:
        return default
    if isinstance(x, (dict, list)):
        return x
    if isinstance(x, (bytes, bytearray)):
        try:
            x = x.decode("utf-8", "replace")
        except Exception:
            return default
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return default
        try:
            return json.loads(s)
        except Exception:
            return default
    return default


_POOL_SIZE = int(os.environ.get("FROGNET_DB_POOL_SIZE", "8"))


class _MySQLPool:
    def __init__(self, cfg: Dict[str, Any], size: int):
        self.cfg = cfg
        self.q: queue.Queue = queue.Queue(maxsize=size)

    _SOCK_TIMEOUT = float(os.environ.get("FROGNET_DB_SOCK_TIMEOUT", "10"))

    def _new_conn(self):
        conn = mysql.connector.connect(
            host=self.cfg["host"],
            user=self.cfg["user"],
            password=self.cfg["password"],
            database=self.cfg["database"],
            port=self.cfg.get("port", 3306),
            use_pure=True,
            connection_timeout=int(self._SOCK_TIMEOUT),
        )
        conn.autocommit = True
        if not self._set_sock_timeout(conn):
            try:
                conn.close()
            except Exception:
                pass
            raise RuntimeError(
                "FATAL: cannot set socket timeout on fresh MySQL connection"
            )
        # [NO_PROACTIVE_PING_V1]
        conn.is_connected = lambda: True

        # [NO_SQLMODE_LOOKUP_V1]
        conn._sql_mode = ""
        try:
            _cur = conn.cursor()
            _cur.execute("SELECT @@SESSION.sql_mode")
            _row = _cur.fetchone()
            _cur.close()
            if _row and _row[0]:
                conn._sql_mode = _row[0]
        except Exception:
            pass

        return conn

    @staticmethod
    def _set_sock_timeout(conn, timeout=None):
        if timeout is None:
            timeout = _MySQLPool._SOCK_TIMEOUT
        try:
            sock = conn._socket.sock
            if sock:
                sock.settimeout(timeout)
                return True
        except Exception:
            pass
        return False

    def acquire(self):
        try:
            conn = self.q.get_nowait()
        except queue.Empty:
            return self._new_conn()

        if not self._set_sock_timeout(conn):
            try:
                conn.close()
            except Exception:
                pass
            return self._new_conn()

        return conn

    def release(self, conn):
        if not conn:
            return
        try:
            if self.q.full():
                conn.close()
            else:
                self.q.put_nowait(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass


class TemplateStore:
    _pool: Optional[_MySQLPool] = None
    _pool_lock = threading.RLock()
    # [TEMPLATE_STORE_ENSURE_V1] set once ensure_tables() has succeeded in this
    # process; see the method for why the DDL lives here and not in a .sql.
    _tables_ensured: bool = False
    _TPL_CACHE_TTL = float(os.environ.get("FROGNET_TPL_CACHE_TTL", "60"))
    # [NEG_CACHE_TTL_V1] Negative results (template not in DB yet) need
    # a far shorter TTL than positive ones.  When a path bootstraps,
    # the proxy writes the template to the central DB.  Without this,
    # the daemon's lookup_by_opcode cached the (None, None) miss for
    # the full 60s - so every subsequent request hit the negative
    # cache, returned "no templates for opcode" to the proxy, and the
    # proxy fell back to BOOTSTRAP again.  Result: every request to a
    # newly-learned path went through the raw HTTP path for ~60s,
    # 850KB-payload concurrent tests saturated the per-target RPC cap
    # at 32, and 111/300 burst requests failed.  1.0s is short enough
    # that template propagation completes inside one user-visible
    # round trip; long enough to absorb a thundering-herd of misses
    # against an actually-missing template.
    _TPL_NEG_CACHE_TTL = float(os.environ.get("FROGNET_TPL_NEG_CACHE_TTL", "1.0"))
    _TPL_CACHE_MAX = int(os.environ.get("FROGNET_TPL_CACHE_MAX", "1024"))
    _tpl_cache: Dict[str, Tuple[float, Any]] = {}
    _tpl_cache_lock = threading.RLock()

    def __init__(self):
        with self._pool_lock:
            if self.__class__._pool is None:
                cfg = json.load(open("/opt/frognet_semantic/DB_CONFIG.json"))
                self.__class__._pool = _MySQLPool(cfg, _POOL_SIZE)

    # -------------------------
    # Connection lifecycle
    # -------------------------
    # [PER_CALL_CONN_V1] No instance _conn slot.  Every DB method gets
    # its own conn from the pool, holds it as a local, releases it
    # when done.  See file header for the full rationale.
    # -------------------------
    # Schema
    # -------------------------
    def ensure_tables(self):
        """[TEMPLATE_STORE_ENSURE_V1] Create the template tables if absent.

        These two tables had NO creator anywhere in the shipped tree. store.py
        SELECTs and INSERTs them and clear_tables.sh TRUNCATEs them, but the
        only CREATE lived in usr/local/bin/semantic_cache_schema.sql, which is
        explicitly EXCLUDED from the release tarball by both make_tar.bash and
        frognet_build_release.sh and is loaded by nothing. A node built from a
        release therefore took

            1146 (42S02): Table 'FrogNet.frognet_request_templates' doesn't exist

        on the first request through :80 -- from lookup_by_path, before any
        semantic work could start -- and could never learn a template.

        Self-healing rather than a .sql the installer loads, for two reasons.
        DB_CONFIG.json points at 127.0.0.1, so the template store is LOCAL to
        every node; install_databasehost.sh runs only on a databasehost and
        would never reach a plain proxy node. And it only loads schema when the
        database does not already exist, so an upgrade would skip it. This
        mirrors what proxy/cache/semcache_db.ensure_table() already does for
        SemCacheProxy, which is why that table is not affected.

        Column types match `describe` on a running box exactly, including
        baseline_blobs and description -- both are read by the template layer
        but never written by store.py, so a schema inferred from these INSERT
        statements alone would silently omit them.

        Idempotent. Runs once per process; after the first success it is a
        boolean no-op, following [ENSURE_TABLE_ONCE_V1].
        """
        if self.__class__._tables_ensured:
            return

        def _do(conn):
            cur = conn.cursor()
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS frognet_request_templates (
                        templateId      varchar(64)      NOT NULL,
                        opcode          int(10) unsigned NOT NULL,
                        method          varchar(8)       NOT NULL,
                        actionUrl       varchar(512)     NOT NULL,
                        params          longtext         DEFAULT NULL,
                        url_query_keys  longtext         DEFAULT NULL,
                        PRIMARY KEY (templateId),
                        KEY opcode (opcode),
                        KEY method (method)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS frognet_response_templates (
                        templateId      varchar(64)      NOT NULL,
                        opcode          int(10) unsigned NOT NULL,
                        responseMode    varchar(16)      NOT NULL,
                        templateJSON    longtext         DEFAULT NULL,
                        baseline_blobs  longtext         DEFAULT NULL,
                        description     varchar(255)     DEFAULT NULL,
                        PRIMARY KEY (templateId),
                        KEY opcode (opcode)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                # Idempotent migration for tables created by older revisions
                # that predate url_query_keys / baseline_blobs / description.
                # ADD COLUMN IF NOT EXISTS is a MariaDB extension and safe to
                # re-run; best-effort so it never blocks startup.
                for stmt in (
                    "ALTER TABLE frognet_request_templates ADD COLUMN "
                    "IF NOT EXISTS url_query_keys longtext DEFAULT NULL",
                    "ALTER TABLE frognet_response_templates ADD COLUMN "
                    "IF NOT EXISTS baseline_blobs longtext DEFAULT NULL",
                    "ALTER TABLE frognet_response_templates ADD COLUMN "
                    "IF NOT EXISTS description varchar(255) DEFAULT NULL",
                ):
                    try:
                        cur.execute(stmt)
                    except Exception as e:
                        print(f"[TEMPLATE-STORE] migration step skipped: {e}",
                              flush=True)
                try:
                    conn.commit()
                except Exception:
                    pass
            finally:
                try:
                    cur.close()
                except Exception:
                    pass

        self._with_conn(_do)
        self.__class__._tables_ensured = True

    def _with_conn(self, fn):
        """Run fn(conn) with one retry on a fresh conn if it raises.
        Always releases the conn last in hand back to the pool."""
        conn = self._pool.acquire()
        try:
            try:
                return fn(conn)
            except Exception:
                # Conn presumed bad - close, don't requeue, retry once.
                try:
                    conn.close()
                except Exception:
                    pass
                conn = self._pool.acquire()
                return fn(conn)
        finally:
            try:
                self._pool.release(conn)
            except Exception:
                pass

    # -------------------------
    # Deterministic IDs
    # -------------------------
    def _template_id_from_key(self, method: str, semantic_path: str) -> str:
        key = f"{method.upper()} {semantic_path}".encode("utf-8", "replace")
        crc = zlib.crc32(key) & 0xFFFFFFFF
        return f"tpl_{crc:08x}"

    def _opcode_from_semantic_key(self, semantic_key: str) -> int:
        crc32 = zlib.crc32(semantic_key.encode("utf-8")) & 0xFFFFFFFF
        if crc32 == 0:
            return 0xFFFFFFFE
        if crc32 == 0xFFFFFFFF:
            return 0xFFFFFFFE
        return crc32

    # -------------------------
    # Lookups
    # -------------------------
    def lookup_by_path(self, method: str, semantic_path: str):
        cache_key = f"{method}|{semantic_path}"
        now = time.time()

        with self._tpl_cache_lock:
            cached = self._tpl_cache.get(cache_key)
            if cached is not None:
                expires_at, result = cached
                if now < expires_at:
                    return result
                else:
                    del self._tpl_cache[cache_key]

        def _do(conn):
            cur = conn.cursor(dictionary=True, buffered=True)
            try:
                cur.execute(
                    """
                    SELECT * FROM frognet_request_templates
                    WHERE method=%s AND actionUrl=%s
                    """,
                    (method, semantic_path),
                )
                req = cur.fetchone()
                resp = None
                if req:
                    cur.execute(
                        """
                        SELECT * FROM frognet_response_templates
                        WHERE templateId=%s
                        """,
                        (req["templateId"],),
                    )
                    resp = cur.fetchone()
                return (req, resp)
            finally:
                try:
                    cur.close()
                except Exception:
                    pass

        result = self._with_conn(_do)
        self._cache_put(cache_key, result)
        return result

    def _cache_put(self, key: str, result: Any) -> None:
        # [NEG_CACHE_TTL_V1] Detect negative result: lookup_by_path and
        # lookup_by_opcode both return (req_row, resp_row).  A miss is
        # (None, None) - but treat any falsy req_row as negative since
        # resp_row is meaningless without it.
        is_negative = (
            isinstance(result, tuple)
            and len(result) >= 1
            and not result[0]
        )
        ttl = self._TPL_NEG_CACHE_TTL if is_negative else self._TPL_CACHE_TTL
        with self._tpl_cache_lock:
            self._tpl_cache[key] = (time.time() + ttl, result)
            if len(self._tpl_cache) > self._TPL_CACHE_MAX:
                now = time.time()
                expired = [k for k, (exp, _) in self._tpl_cache.items() if now >= exp]
                for k in expired:
                    del self._tpl_cache[k]
                while len(self._tpl_cache) > self._TPL_CACHE_MAX:
                    try:
                        del self._tpl_cache[next(iter(self._tpl_cache))]
                    except (StopIteration, KeyError):
                        break

    def _cache_invalidate(self, method: str, semantic_path: str) -> None:
        key = f"{method}|{semantic_path}"
        with self._tpl_cache_lock:
            self._tpl_cache.pop(key, None)

    def lookup_by_opcode(self, opcode: int):
        cache_key = f"op|{opcode}"
        now = time.time()

        with self._tpl_cache_lock:
            cached = self._tpl_cache.get(cache_key)
            if cached is not None:
                expires_at, result = cached
                if now < expires_at:
                    return result
                else:
                    del self._tpl_cache[cache_key]

        def _do(conn):
            cur = conn.cursor(dictionary=True, buffered=True)
            try:
                cur.execute(
                    "SELECT * FROM frognet_request_templates WHERE opcode=%s",
                    (opcode,),
                )
                req = cur.fetchone()
                resp = None
                if req:
                    cur.execute(
                        "SELECT * FROM frognet_response_templates WHERE templateId=%s",
                        (req["templateId"],),
                    )
                    resp = cur.fetchone()
                return (req, resp)
            finally:
                try:
                    cur.close()
                except Exception:
                    pass

        result = self._with_conn(_do)
        self._cache_put(cache_key, result)
        return result

    # Backward-compatible names
    def lookup_templates(self, method: str, semantic_path: str):
        return self.lookup_by_path(method, semantic_path)

    def lookup_templates_by_opcode(self, opcode: int):
        return self.lookup_by_opcode(opcode)

    # -------------------------
    # Builders
    # -------------------------
    def build_request_template(self, row: Optional[Dict[str, Any]]) -> Optional[RequestTemplate]:
        if not row:
            return None

        frag = _loads_json_maybe(row.get("params"), {})
        if not isinstance(frag, dict):
            frag = {}

        tokens = frag.get("tokens")
        if not isinstance(tokens, dict):
            tokens = {"ip": {}, "host": {}, "str": {}, "enum": {}}

        uqk_raw = row.get("url_query_keys")
        uqk = _loads_json_maybe(uqk_raw, [])
        if not isinstance(uqk, list):
            uqk = []

        return RequestTemplate(
            template_id=row["templateId"],
            opcode=int(row["opcode"]),
            method=row["method"],
            url_static=row["actionUrl"],
            url_query_keys=uqk,
            template_fragment=frag,
            tokens=tokens,
        )

    def build_reply_template(self, row: Optional[Dict[str, Any]]) -> ReplyTemplate:
        if not row:
            return ReplyTemplate(
                template_id="none",
                opcode=0,
                template_fragment={"mode": "raw"},
                tokens={"ip": {}, "host": {}, "str": {}, "enum": {}},
                response_mode="raw",
            )

        frag = _loads_json_maybe(row.get("templateJSON"), {})
        if not isinstance(frag, dict):
            raise RuntimeError("templateJSON is not dict")

        tokens = frag.get("tokens", {})
        if not isinstance(tokens, dict):
            tokens = {"ip": {}, "host": {}, "str": {}, "enum": {}}

        return ReplyTemplate(
            template_id=row.get("templateId", "none"),
            opcode=int(row.get("opcode") or 0),
            template_fragment=frag,
            tokens=tokens,
            response_mode=row.get("responseMode"),
        )

    # -------------------------
    # STORE PHASE-I TEMPLATES
    # -------------------------
    def store_templates(self, method: str, semantic_path: str, Treq: dict, Tresp: Optional[dict]):
        method = (method or "").strip()
        semantic_path = (semantic_path or "").strip()
        if not method or not semantic_path:
            return

        self._cache_invalidate(method, semantic_path)

        if not isinstance(Treq, dict):
            Treq = {}
        if Tresp is not None and not isinstance(Tresp, dict):
            raise RuntimeError(f"Tresp must be dict or None, got {type(Tresp)}")

        tpl_id = self._template_id_from_key(method, semantic_path)
        opcode = self._opcode_from_semantic_key(f"{method.upper()} {semantic_path}")

        req_json = json.dumps(Treq, separators=(",", ":"), ensure_ascii=False)

        url_query_keys: List[str] = []
        if isinstance(Treq.get("url_query_keys"), list):
            url_query_keys = Treq["url_query_keys"]
        uqk_json = json.dumps(url_query_keys, separators=(",", ":"), ensure_ascii=False)

        resp_json = ""
        resp_mode = "raw"
        if Tresp:
            resp_mode = str(Tresp.get("mode") or "raw")
            resp_json = json.dumps(Tresp, separators=(",", ":"), ensure_ascii=False)
            max_bytes = int(os.environ.get("FROGNET_MAX_TEMPLATEJSON_BYTES", str(16 * 1024 * 1024)))
            if len(resp_json.encode("utf-8", "replace")) > max_bytes:
                raise RuntimeError("Refusing to store oversized templateJSON; blobization missing")

        def _do(conn):
            cur = conn.cursor(buffered=True)
            try:
                try:
                    cur.execute(
                        """
                        INSERT INTO frognet_request_templates (templateId, opcode, method, actionUrl, params, url_query_keys)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                          opcode=VALUES(opcode),
                          params=VALUES(params),
                          url_query_keys=VALUES(url_query_keys)
                        """,
                        (tpl_id, opcode, method, semantic_path, req_json, uqk_json),
                    )
                except Exception:
                    cur.execute(
                        """
                        INSERT INTO frognet_request_templates (templateId, opcode, method, actionUrl, params)
                        VALUES (%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                          opcode=VALUES(opcode),
                          params=VALUES(params)
                        """,
                        (tpl_id, opcode, method, semantic_path, req_json),
                    )

                if resp_json:
                    cur.execute(
                        """
                        INSERT INTO frognet_response_templates (templateId, opcode, responseMode, templateJSON)
                        VALUES (%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                          opcode=VALUES(opcode),
                          responseMode=VALUES(responseMode),
                          templateJSON=VALUES(templateJSON)
                        """,
                        (tpl_id, opcode, resp_mode, resp_json),
                    )

                try:
                    conn.commit()
                except Exception:
                    pass
            finally:
                try:
                    cur.close()
                except Exception:
                    pass

        self._with_conn(_do)

    def save_templates(self, method: str, semantic_path: str, req_tpl: dict, resp_tpl: Optional[dict]):
        return self.store_templates(method, semantic_path, req_tpl, resp_tpl)
