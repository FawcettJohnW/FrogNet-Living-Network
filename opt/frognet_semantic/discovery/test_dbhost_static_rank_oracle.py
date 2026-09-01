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
"""test_dbhost_static_rank_oracle - [DBHOST_STATIC_RANK_V1].

Each node writes its OWN capability tuple, and that tuple must be STATIC between writes so
the election is deterministic. The rank scored on mem_available_kb (free RAM) and so a
node's tuple changed write-to-write: a cold election picked different winners as free RAM
drifted, and two boxes reading at different instants disagreed. Rank must use installed RAM
(mem_total_kb) - fixed capability. Drives the REAL select_database_host (capability index
stubbed) with two hosts of IDENTICAL installed hardware, one whose FREE ram drifts.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import discovery.hosts as H

FAILS = []
def check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

def _setc(c):
    H._capability_index = lambda gate, role="databasehost", dbhost="x": {ip: x for ip, x in c.items() if gate(x)}

def _caps(b_avail_kb):
    return {
      "10.10.10.1": dict(lan_ip="10.10.10.1", mysql_running=1, mem_total_kb=8*1024*1024,
                         mem_available_kb=4*1024*1024, cores=4, cpu_bench_total=5000,
                         disk_write_mbps=40, disk_fsync_ms=5, disk_free_gb=50),
      "10.20.20.1": dict(lan_ip="10.20.20.1", mysql_running=1, mem_total_kb=8*1024*1024,
                         mem_available_kb=b_avail_kb, cores=4, cpu_bench_total=3000,
                         disk_write_mbps=40, disk_fsync_ms=5, disk_free_gb=50),
    }

def _db(out):
    for l in out:
        if "databasehost.frognet" in l and "control" not in l:
            return l.split()[0]
    return None

BASE = ["10.10.10.1 A", "10.20.20.1 B"]

def main():
    print("=== DBHOST STATIC RANK ORACLE ===")
    # A wins on STATIC merit (higher cpu_bench, identical installed RAM). Only B's free
    # RAM drifts. COLD election each pass (no incumbent) so the score alone decides.
    seq = []
    for b_avail in [2*1024*1024, 14*1024*1024, 2*1024*1024, 14*1024*1024]:
        _setc(_caps(b_avail))
        seq.append(_db(H.select_database_host(list(BASE))))
    flaps = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i-1])
    check(f"cold winner stable through free-RAM drift (seq={seq})", flaps == 0)
    check("static winner is the higher-capability host (A)", all(w == "10.10.10.1" for w in seq))

    print("\n" + ("ALL DBHOST-STATIC-RANK CHECKS PASS" if not FAILS
                  else f"DBHOST-STATIC-RANK ORACLE FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
