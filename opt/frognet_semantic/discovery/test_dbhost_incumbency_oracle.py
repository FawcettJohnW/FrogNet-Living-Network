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
"""test_dbhost_incumbency_oracle - [DBHOST_INCUMBENCY_HOLD_V1].

The floating databasehost must obey the same law as routes (ROUTE_INCUMBENCY_HOLD_V1):
a healthy incumbent is NEVER displaced by a challenger, at any score margin. Before the
hold, select_database_host ran max(_rank) every merge over fluctuating mem_available, so
the winner oscillated between two mysql-capable hosts merge-to-merge (the observed
databasehost flap between two boxes). This drives the REAL select_database_host with a
stubbed capability index and asserts: (A) a healthy incumbent is held against a
higher-scoring challenger; (B) no flap across merges as available memory drifts; (C) an
incumbent that LEAVES the eligible set (dies) triggers a clean re-election.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import discovery.hosts as H

FAILS = []
def check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

def _caps(members):
    def idx(gate, role="databasehost", dbhost="x"):
        return {ip: c for ip, c in members.items() if gate(c)}
    H._capability_index = idx

def _cap(ip, mem_kb, mysql=1):
    return dict(lan_ip=ip, mysql_running=mysql, mem_available_kb=mem_kb, cores=4,
                cpu_bench_total=3000, disk_write_mbps=40, disk_fsync_ms=5, disk_free_gb=50)

def _dbof(out):
    for l in out:
        if "databasehost.frognet" in l and "control" not in l:
            return l.split()[0]
    return None

BASE = ["10.130.130.1 FrogNetHost.Seattle3", "10.179.178.1 FrogNetHost.Other"]
A, B = "10.130.130.1", "10.179.178.1"

def main():
    print("=== DBHOST INCUMBENCY HOLD ORACLE ===")

    # (A) incumbent A healthy; challenger B scores higher -> A held
    _caps({A: _cap(A, 8 * 1024 * 1024), B: _cap(B, 16 * 1024 * 1024)})
    got = _dbof(H.select_database_host(BASE + [f"{A} databasehost.frognet"]))
    check("healthy incumbent held against higher-scoring challenger", got == A)

    # (B) no flap across 4 merges as available memory drifts (winner would swap w/o hold)
    drifts = [(9, 8), (8, 9), (9, 8), (8, 9)]
    db, seq = None, []
    for m130, m179 in drifts:
        _caps({A: _cap(A, m130 * 1024 * 1024), B: _cap(B, m179 * 1024 * 1024)})
        hosts = list(BASE) + ([f"{db} databasehost.frognet"] if db else [])
        db = _dbof(H.select_database_host(hosts)); seq.append(db)
    flaps = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])
    check(f"no flap across drifting merges (seq={seq})", flaps == 0)

    # (C) incumbent dies (drops from eligible) -> clean re-election to the survivor
    _caps({B: _cap(B, 8 * 1024 * 1024)})            # A no longer mysql-capable/present
    got = _dbof(H.select_database_host(BASE + [f"{A} databasehost.frognet"]))
    check("dead incumbent re-elects to the surviving capable host", got == B)

    print("\n" + ("ALL DBHOST-INCUMBENCY CHECKS PASS" if not FAILS
                  else f"DBHOST-INCUMBENCY ORACLE FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
