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
"""test_dbhost_ballot_freshness_oracle - [DBHOST_BALLOT_FRESHNESS_V1].

The floor election (select_database_host -> _capability_index) MUST apply the same
ballot admissibility the service election (frognet_role_elect.gather_candidates) does:
a capability tuple with ts==0 or older than FROGNET_BALLOT_MAX_AGE_S is a record of a
host that stopped speaking and is INADMISSIBLE. A real two-merge split was traced to the
floor counting a stale tuple (ts_age ~3011s) for a quiet host and electing it, while the
service election refused it - databasehost.frognet flapped between the two answers.
Drives the REAL select_database_host with stubbed tuples of varying freshness.
"""
import sys, os, time, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []
def check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

def _install(caps):
    rows = {("databasehost", "capability"): [
        # [ENVELOPE_TS_V1] the age is the STORE's, on the row. The payload keeps its
        # own ts, deliberately unread, so a case that sets them differently proves it.
        {"addr": ip, "var": "capability", "ts_env": ts,
         "age_s": (None if ts in (0, None) else int(time.time()) - ts),
         "value": {"capability": c, "ts": ts}}
        for ip, (ts, c) in caps.items()]}
    T = types.ModuleType("frognet_tuples")
    T.get = lambda service, var, dbhost=None, fresh_s=0, timeout=4.0: rows.get((service, var), [])
    T.put = lambda *a, **k: True
    sys.modules["frognet_tuples"] = T
    sys.modules["core.frognet_tuples"] = T
    import core
    core.frognet_tuples = T

def _cap(ip, mem_kb, wmbps=40, free=50, bench=3000):
    return dict(lan_ip=ip, mysql_running=1, mem_available_kb=mem_kb, cores=4,
                cpu_bench_total=bench, disk_write_mbps=wmbps, disk_fsync_ms=5, disk_free_gb=free)

def _db(out):
    for l in out:
        if "databasehost.frognet" in l and "control" not in l:
            return l.split()[0]
    return None

A, B = "10.130.130.1", "10.179.178.1"
BASE = [f"{A} FrogNetHost.Seattle3", f"{B} FrogNetHost.Other"]

def main():
    print("=== DBHOST BALLOT FRESHNESS ORACLE ===")
    import discovery.hosts as H
    now = int(time.time())

    # (A) B is stale (3011s) and scores HIGHER; A is fresh -> A must win (B refused)
    _install({A: (now - 42, _cap(A, 15553728, wmbps=1.8, free=9)),
              B: (now - 3011, _cap(B, 15477136, wmbps=632, free=399, bench=27391))})
    check("stale high-score host refused; fresh host wins",
          _db(H.select_database_host(list(BASE))) == A)

    # (B) ts==0 (never speaks) is inadmissible even if fresh-looking otherwise
    _install({A: (now - 42, _cap(A, 8 * 1024 * 1024)),
              B: (0, _cap(B, 64 * 1024 * 1024))})
    check("ts==0 host refused regardless of score",
          _db(H.select_database_host(list(BASE))) == A)

    # (C) both fresh -> normal best-score election is unaffected (no false exclusion)
    _install({A: (now - 10, _cap(A, 8 * 1024 * 1024)),
              B: (now - 10, _cap(B, 16 * 1024 * 1024))})
    check("both fresh -> higher-score host still wins (no over-refusal)",
          _db(H.select_database_host(list(BASE))) == B)

    print("\n" + ("ALL DBHOST-FRESHNESS CHECKS PASS" if not FAILS
                  else f"DBHOST-FRESHNESS ORACLE FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
