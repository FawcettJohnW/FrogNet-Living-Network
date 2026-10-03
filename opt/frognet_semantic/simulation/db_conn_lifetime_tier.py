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
"""db_conn_lifetime_tier.py -- a thread that dies must take its connection.

[A_THREAD_THAT_DIES_MUST_TAKE_ITS_CONNECTION_WITH_IT_V1 - John 2026-09-15]

Three modules park a MySQL connection in a threading.local(): the daemon's
semcache, the proxy's semcache, and the daemon's data_cache. One connection per
thread, held for the life of the thread. Two of them defined close_thread_conn()
documented "Call on thread exit"; nothing in the tree called it, and the third
did not even define one.

The daemon spawns a thread per served request, so every request that touched the
cache left a connection behind when its thread ended. Measured on the
databasehost: daemon_main.py holding ~270 of MariaDB's 300 connections against
253 live threads. More connections than threads is the leak stated as a number,
and it is the number this tier watches.

Downstream, api.php could not get a handle:

    {"error":"DB connect failed","detail":"Too many connections"}

which arrived at the benchmark as HTTP 500, as HTTP 502 "http exec error:
TimeoutError" when the origin timed out first, and as HTTP 503 from the proxy.
Three different transport messages for one exhausted resource, each chased
separately, across four campaigns.

WHAT THIS RUNS

The real _get_conn / _kill_conn / _ConnHolder from each module, with
mysql.connector.connect swapped for a counting fake. Threads are started, made
to use the cache, and joined; then the tier asserts the open count comes back
down. It does not model the thread-local -- a model of it would have agreed with
the old code, because the old code did exactly what it said it did.
"""
from __future__ import annotations

import gc
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEM = os.path.dirname(_HERE)
if _SEM not in sys.path:
    sys.path.insert(0, _SEM)

FAILS = []
PASSES = 0


def check(ok, what):
    global PASSES
    if ok:
        PASSES += 1
        print(f"  [PASS] {what}")
    else:
        FAILS.append(what)
        print(f"  [FAIL] {what}")


class _FakeSock:
    def settimeout(self, _t):
        return None


class _FakeSocketWrap:
    def __init__(self):
        self.sock = _FakeSock()


class _FakeConn:
    """Counts opens and closes. Close is idempotent, as mysql.connector's is."""

    def __init__(self, ledger):
        self._ledger = ledger
        self._open = True
        self._socket = _FakeSocketWrap()
        with ledger["lock"]:
            ledger["opened"] += 1
            ledger["live"] += 1

    def is_connected(self):
        return self._open

    def close(self):
        if not self._open:
            return
        self._open = False
        with self._ledger["lock"]:
            self._ledger["closed"] += 1
            self._ledger["live"] -= 1

    def cursor(self, *a, **k):
        raise AssertionError("this tier does not run queries")


def _ledger():
    return {"opened": 0, "closed": 0, "live": 0, "lock": threading.Lock()}


def _install_fake(mod, led):
    """Point the module's connect() at the counting fake.

    mysql.connector is imported INSIDE _get_conn in every one of these
    modules, so the patch goes on the mysql.connector module object itself.

    core.db_credentials is stubbed too: it reads DB_CONFIG.json and stops the
    process when it is absent, which is right on a node and wrong in a tier.
    This tier is about connection LIFETIME; the credential has its own.
    """
    import mysql.connector
    from core import db_credentials as cred
    saved = {"connect": mysql.connector.connect}
    for name, val in (("host", "127.0.0.1"), ("user", "tier"),
                      ("password", "tier"), ("database", "FrogNet")):
        if hasattr(cred, name):
            saved[name] = getattr(cred, name)
            setattr(cred, name, (lambda v: (lambda: v))(val))
    if hasattr(cred, "port"):
        saved["port"] = cred.port
        cred.port = lambda: 3306
    if hasattr(cred, "connect_kwargs"):
        saved["connect_kwargs"] = cred.connect_kwargs
        cred.connect_kwargs = lambda: {"host": "127.0.0.1", "port": 3306,
                                       "user": "tier", "password": "tier",
                                       "database": "FrogNet"}
    mysql.connector.connect = lambda *a, **k: _FakeConn(led)
    return saved


def _restore(saved):
    import mysql.connector
    from core import db_credentials as cred
    mysql.connector.connect = saved.pop("connect")
    for name, val in saved.items():
        setattr(cred, name, val)


def _settle():
    """Thread-local storage is cleared as the thread finishes; give the
    interpreter a moment and a collection before reading the ledger."""
    time.sleep(0.2)
    gc.collect()
    time.sleep(0.1)


def _exercise(mod, label, n_threads=25):
    led = _ledger()
    real = _install_fake(mod, led)
    try:
        errs = []

        def body():
            try:
                c = mod._get_conn()
                if c is None:
                    errs.append("_get_conn returned None")
                # touch it the way a served request would
                mod._get_conn()
            except Exception as e:  # noqa: BLE001
                errs.append(repr(e))

        threads = [threading.Thread(target=body, daemon=True)
                   for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10.0)

        check(not errs, f"{label}: every thread got a connection ({errs[:2]})")
        check(led["opened"] == n_threads,
              f"{label}: one connection per thread, not per call "
              f"({led['opened']} opens for {n_threads} threads)")

        _settle()

        # THE CHECK. Every thread is dead. Nothing may still hold a handle.
        check(led["live"] == 0,
              f"{label}: fail-on-old -- {led['live']} of {led['opened']} "
              f"connections are still open after every thread that opened "
              f"them has exited. That is the leak that took the databasehost "
              f"to 298 of 300 and returned 'Too many connections' to api.php.")
        check(led["closed"] == led["opened"],
              f"{label}: opens and closes balance "
              f"({led['opened']} / {led['closed']})")
    finally:
        _restore(real)


def _reuse(mod, label):
    """A thread that keeps running keeps ONE connection.

    The fix must not turn into a connection per call: that would trade a leak
    for a connect storm against the same limit.
    """
    led = _ledger()
    real = _install_fake(mod, led)
    try:
        seen = []

        def body():
            for _ in range(8):
                seen.append(id(mod._get_conn()))

        t = threading.Thread(target=body, daemon=True)
        t.start()
        t.join(10.0)
        check(len(set(seen)) == 1,
              f"{label}: eight calls on one live thread reuse one connection "
              f"({len(set(seen))} distinct)")
        check(led["opened"] == 1,
              f"{label}: and opened exactly one ({led['opened']})")
        _settle()
        check(led["live"] == 0, f"{label}: which is released when it exits")
    finally:
        _restore(real)


def _kill(mod, label):
    """_kill_conn must close, not just forget.

    It is called after any DB error, so a module that drops the reference
    without closing leaks one connection per error rather than per thread --
    slower, same ending.
    """
    led = _ledger()
    real = _install_fake(mod, led)
    try:
        done = threading.Event()

        def body():
            mod._get_conn()
            mod._kill_conn()
            done.set()
            # stay alive, so anything still open is _kill_conn's doing and
            # not the thread-local being torn down underneath it
            time.sleep(1.5)

        t = threading.Thread(target=body, daemon=True)
        t.start()
        done.wait(10.0)
        time.sleep(0.2)
        gc.collect()
        check(led["live"] == 0,
              f"{label}: _kill_conn closes the connection rather than "
              f"dropping the reference ({led['live']} still open while the "
              f"thread is still running)")
        t.join(5.0)
    finally:
        _restore(real)


def _structural():
    print("\n=== PLANE 4: no module parks a raw connection on a "
          "threading.local ===")
    import ast
    targets = [
        ("daemon/cache/semcache_db.py", "daemon semcache"),
        ("proxy/cache/semcache_db.py", "proxy semcache"),
        ("daemon/engine/data_cache.py", "daemon data_cache"),
    ]
    for rel, label in targets:
        src = open(os.path.join(_SEM, rel), encoding="utf-8").read()
        check("_conn_local.conn" not in src,
              f"{label}: fail-on-old -- the connection is not assigned "
              f"straight onto the thread-local, where nothing closes it")
        check("class _ConnHolder" in src,
              f"{label}: holds it in a _ConnHolder whose __del__ closes")
        tree = ast.parse(src)
        holder = [n for n in ast.walk(tree)
                  if isinstance(n, ast.ClassDef) and n.name == "_ConnHolder"]
        if holder:
            check(any(isinstance(f, ast.FunctionDef) and f.name == "__del__"
                      for f in holder[0].body),
                  f"{label}: and that __del__ actually exists")


def main():
    try:
        import mysql.connector  # noqa: F401
    except ImportError:
        print("SKIP: mysql.connector not installed; nothing to patch")
        return 0

    from daemon.cache import semcache_db as dsem
    from proxy.cache import semcache_db as psem
    from daemon.engine import data_cache as ddata

    print("=== PLANE 1: threads that exit release their connections ===")
    for mod, label in ((dsem, "daemon semcache"),
                       (psem, "proxy semcache"),
                       (ddata, "daemon data_cache")):
        _exercise(mod, label)

    print("\n=== PLANE 2: a live thread still reuses one connection ===")
    for mod, label in ((dsem, "daemon semcache"),
                       (psem, "proxy semcache"),
                       (ddata, "daemon data_cache")):
        _reuse(mod, label)

    print("\n=== PLANE 3: _kill_conn closes ===")
    for mod, label in ((dsem, "daemon semcache"), (psem, "proxy semcache")):
        if hasattr(mod, "_kill_conn"):
            _kill(mod, label)
        else:
            check(False, f"{label}: _kill_conn is missing")

    _structural()

    print()
    if FAILS:
        print(f"DB CONN LIFETIME TIER: {len(FAILS)} FAILED, {PASSES} passed")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print(f"ALL DB CONN LIFETIME CHECKPOINTS PASS ({PASSES})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
