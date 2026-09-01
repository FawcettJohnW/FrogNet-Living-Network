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
"""[SEMCACHE_PROXY_LZ4_V1 / SEMCACHE_PROXY_LRU_V1] oracle.

Proves, against a mock MySQL connection (no live DB):
  1. upsert() stores ReplyBytes COMPRESSED (lz4) with Compressed=1, and the
     stored blob is smaller than the original for compressible payloads.
  2. get_by_sameid() round-trips the original bytes back (decompress on read).
  3. a read hit advances LastReadAt (LRU clock).
  4. evict_stale() exists and issues a LastReadAt-keyed DELETE.

OLD code: no Compressed column written (raw bytes stored), no evict_stale,
no LastReadAt touch  -> FAIL.
NEW code -> PASS.
"""
import sys, types

# ---- stub mysql.connector so the module imports with no driver/DB ----
_fake = types.ModuleType("mysql"); _conn = types.ModuleType("mysql.connector")
_conn.connect = lambda *a, **k: None
_fake.connector = _conn
sys.modules.setdefault("mysql", _fake)
sys.modules.setdefault("mysql.connector", _conn)
# core.codec only needs REQ_HASH_LEN
_core = types.ModuleType("core"); _codec = types.ModuleType("core.codec"); _codec.REQ_HASH_LEN = 32
_core.codec = _codec
sys.modules.setdefault("core", _core); sys.modules.setdefault("core.codec", _codec)

import importlib.util, os
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("semcache_db", os.path.join(HERE, "semcache_db.py"))
db = importlib.util.module_from_spec(spec); spec.loader.exec_module(db)

FAILS = []
def check(c, m): print(("  ok  " if c else "  FAIL") + "  " + m); (FAILS.append(m) if not c else None)


class FakeCursor:
    def __init__(self, log, rows): self.log = log; self._rows = rows; self.rowcount = 7
    def execute(self, sql, params=None): self.log.append((sql, params))
    def fetchone(self):
        return self._rows.pop(0) if self._rows else None
    def close(self): pass

class _FakeSock:
    def settimeout(self, *a, **k): pass
class _FakeSockWrap:
    sock = _FakeSock()

class FakeConn:
    _socket = _FakeSockWrap()
    def __init__(self, log, rows): self.log = log; self._rows = rows
    def cursor(self): return FakeCursor(self.log, self._rows)
    def is_connected(self): return True
    def close(self): pass

def install_conn(rows=None):
    log = []
    db._conn_local.conn = FakeConn(log, list(rows or []))
    return log


# the module must actually have lz4 to prove compression
check(db._lz4 is not None, "lz4 available for at-rest compression")

ORIG = b'{"value": 42, "name": "frognet", "blob": "' + b"A" * 4000 + b'"}'
sid = b"\x01" * 16; rh = b"\x02" * 32; rwh = b"\x03" * 32; sh = b"\x04" * 32

# 1) upsert compresses + sets Compressed=1
log = install_conn()
db.upsert(sid, rh, rwh, sh, ORIG, "application/json")
ins = [(s, p) for (s, p) in log if "INSERT INTO SemCacheProxy" in s]
check(bool(ins), "upsert issues an INSERT")
sql, params = ins[0]
check("Compressed" in sql, "INSERT writes the Compressed column")
stored = params[4]            # ReplyBytes position
comp_flag = params[-1]        # Compressed flag (last bound param)
check(comp_flag == 1, "Compressed flag set to 1 on write")
check(len(stored) < len(ORIG), f"stored blob is smaller than original ({len(stored)} < {len(ORIG)})")
check(stored != ORIG, "stored bytes are NOT the raw body (proves compression at rest)")

# 2) get_by_sameid round-trips original via decompress; 3) touches LastReadAt
comp_stored, flag = db._encode_reply(ORIG)
log = install_conn(rows=[(comp_stored, "application/json", flag)])
got = db.get_by_sameid(sid)
check(got is not None and got[0] == ORIG, "get_by_sameid round-trips the ORIGINAL bytes")
check(any("UPDATE SemCacheProxy SET LastReadAt" in s for (s, _) in log),
      "read hit advances LastReadAt (LRU clock)")

# old uncompressed rows still readable (Compressed=0 -> raw passthrough)
log = install_conn(rows=[(ORIG, "application/json", 0)])
check(db.get_by_sameid(sid)[0] == ORIG, "legacy uncompressed row (Compressed=0) still reads back raw")

# 4) eviction exists and is LastReadAt-keyed
check(hasattr(db, "evict_stale") and hasattr(db, "start_eviction"),
      "evict_stale + start_eviction exist (proxy now evicts)")
log = install_conn()
db.evict_stale(24)
dels = [s for (s, _) in log if "DELETE FROM SemCacheProxy" in s]
check(bool(dels) and "LastReadAt" in dels[0], "evict_stale DELETEs by LastReadAt (true LRU)")

if FAILS:
    print(f"\nORACLE RED: {len(FAILS)} failure(s)"); sys.exit(1)
print("\nORACLE GREEN: proxy cache compresses at rest and evicts LRU")
