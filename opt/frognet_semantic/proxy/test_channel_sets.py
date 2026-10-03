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
test_channel_sets.py - UNIT tests for the driver-private socket-set allocator.

Covers (no real sockets, no daemon - pure logic + registry + thread lifecycle):
  - allocation: 3 protocols -> 3 independent sets; idempotent re-open
  - overlay: 4th+ protocol lands on the LEAST-sensitive set, never the
    high-sensitivity one; oversubscription never displaces an isolated set
  - lifecycle: release reaps only on empty; double-release is safe; default set 0
  - registry: _get_worker keys distinct _DaemonWorker per set_id; legacy
    (host, port) call still lands on set 0
  - threads: a set spawns its own writer + cleanup threads and BOTH exit on
    stop() (the reap path) - the leak this test exists to guard

Run:  python3 proxy/test_channel_sets.py        (exit 0 = pass)
Portable: uses real mysql.connector / lz4 if present (the box), stubs them only
if missing (the container).
"""
from __future__ import annotations
import os
import sys
import time
import types
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p and p not in sys.path:
        sys.path.insert(0, p)


def _ensure_deps():
    """Stub the heavy production deps ONLY if absent, so this runs in the
    container and on the box unchanged."""
    try:
        import mysql.connector  # noqa: F401
    except Exception:
        m = types.ModuleType("mysql"); mc = types.ModuleType("mysql.connector")
        mc.connect = lambda *a, **k: None
        mc.Error = Exception
        mc.errors = types.SimpleNamespace(Error=Exception)
        mc.pooling = types.SimpleNamespace(MySQLConnectionPool=object)
        m.connector = mc
        sys.modules.update({"mysql": m, "mysql.connector": mc,
                            "mysql.connector.errors": mc.errors,
                            "mysql.connector.pooling": mc.pooling})
    try:
        import lz4.frame  # noqa: F401
    except Exception:
        lz4 = types.ModuleType("lz4"); fr = types.ModuleType("lz4.frame")
        fr.compress = lambda d: zlib.compress(d, 1)
        fr.decompress = zlib.decompress
        lz4.frame = fr
        sys.modules.update({"lz4": lz4, "lz4.frame": fr})


_FAILS = []
def ck(name, cond, got=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  - {got}"))
    if not cond:
        _FAILS.append(name)


def test_allocator():
    import proxy.channel_sets as cs
    a = cs._PeerChannelSets("10.255.255.1", 9009)
    g = a.open("game", sensitivity=9)
    m = a.open("media", sensitivity=5)
    c = a.open("ctrl", sensitivity=1)
    ck("3 protocols -> 3 distinct sets",
       sorted({g.set_id, m.set_id, c.set_id}) == [0, 1, 2],
       sorted({g.set_id, m.set_id, c.set_id}))
    ck("re-open same protocol reuses its set", a.open("game").set_id == g.set_id)
    b = a.open("bulk", sensitivity=0)
    ck("overlay lands on least-sensitive set (ctrl's)", b.set_id == c.set_id, b.set_id)
    ck("overlay never displaces the high-sensitivity game set", b.set_id != g.set_id)
    lg = a.open("log", sensitivity=0)
    ck("further overlay still avoids the game set", lg.set_id != g.set_id, lg.set_id)
    reaped = []
    ck("releasing sole-tenant set reaps it",
       a.release(m, reap=lambda h, p, s: reaped.append(s)) and reaped == [m.set_id],
       reaped)
    ck("releasing a shared set does NOT reap",
       a.release(c, reap=lambda h, p, s: reaped.append("BAD")) is False and "BAD" not in reaped)
    ck("double release is harmless", a.release(m) is False)
    ck("default set id is 0", cs._DEFAULT_SET_ID == 0)


def test_registry_keying():
    import proxy.transport_semantic as tsx
    import proxy.channel_sets as cs
    H, P = "10.255.255.2", 9009
    toks = [cs.open_channel_set(H, P, pr, sensitivity=s)
            for pr, s in [("game", 9), ("media", 5), ("ctrl", 1)]]
    workers = [cs.worker_for_token(t) for t in toks]
    ck("3 sets -> 3 distinct workers", len({id(w) for w in workers}) == 3)
    keys = sorted(k for k in tsx._WORKERS if k[0] == H)
    ck("registry has 3 set-keyed entries", [k[2] for k in keys] == [0, 1, 2], keys)
    tsx._get_worker(H, P)  # legacy 2-arg call
    ck("legacy _get_worker(host,port) -> set 0", (H, P, 0) in tsx._WORKERS)
    for t in toks:
        cs.release_channel_set(t)
    time.sleep(2.5)
    ck("released sets reaped from registry",
       not [k for k in tsx._WORKERS if k[0] == H and k[2] != 0])


def test_thread_lifecycle():
    import proxy.transport_semantic as tsx
    import threading
    host = "10.255.255.3"
    w = tsx._DaemonWorker(host, 9009)   # spawns writer + cleanup
    time.sleep(0.3)
    spawned = [t.name for t in threading.enumerate() if host in t.name]
    ck("set spawned its own writer + cleanup threads", len(spawned) == 2, spawned)
    w.stop()                            # the reap path
    deadline = time.time() + 5
    while time.time() < deadline and any(host in t.name for t in threading.enumerate()):
        time.sleep(0.1)
    left = [t.name for t in threading.enumerate() if host in t.name]
    ck("both per-set threads exit on stop() - no leak", not left, left)
    ck("_stopped flag is set", w._stopped.is_set())


def main():
    _ensure_deps()
    print("=== channel_sets UNIT ===")
    print("[allocator]"); test_allocator()
    print("[registry keying]"); test_registry_keying()
    print("[thread lifecycle]"); test_thread_lifecycle()
    print("\nRESULT:", "ALL PASS" if not _FAILS else f"FAILURES {_FAILS}")
    return 1 if _FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
