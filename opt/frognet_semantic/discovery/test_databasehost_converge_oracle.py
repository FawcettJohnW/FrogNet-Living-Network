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
"""[DBHOST_FLAT_ELIGIBILITY_V1] - replays the sea3/sea5 2026-06-21 databasehost split.

Real logs, same minute, two nodes reading the SAME control DB (10.250.250.1):
  sea5: SERVICE_CAND databasehost ip=10.130.130.1 score=6.32   -> winner=10.250.250.1
  sea3: SERVICE_CAND databasehost ip=10.130.130.1 score=33.13  -> winner=10.130.130.1
Same host (10.130.130.1), two different scores, two different winners = split brain.

Two compounding causes, both reproduced here:
  1. score() multiplied by live-load headroom, so the SAME host scores differently on
     every async read (33 under low load, 6 under high load).
  2. the candidate pools differ (sea5 saw {250,130}; sea3 saw {250,130,120,102,phantom
     10.179.179.1}), so a perf-top candidate present in one pool and absent from the
     other lets the two nodes pick different winners.

A floating role every node must resolve identically CANNOT be perf-elected across
independent async reads. The fix: flat eligibility (mysql gate) + highest-IP decides,
so the global highest-IP mysql host - the control host present in EVERY pool - wins
identically everywhere, immune to load jitter and pool differences.

Asserts, driving the REAL DatabaseRoleHandler.evaluate:
  - sea3's pool and sea5's pool elect the SAME databasehost.
FAILS on the upload (load + pool divergence -> different winners). PASSES on the fix.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.database_handler import DatabaseRoleHandler

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

def cand(ip, load1, *, mem_kb=4_000_000, cores=2, wmbps=80.0, fsync=8.0,
         free=30.0, bench=2500.0, innodb=1_000_000_000, mysql=True):
    return {"lan_ip": ip, "mysql_running": mysql, "cores": cores,
            "mem_available_kb": mem_kb, "mem_total_kb": mem_kb,
            "mysql_innodb_pool_bytes": innodb, "disk_write_mbps": wmbps,
            "disk_fsync_ms": fsync, "disk_free_gb": free, "cpu_bench_total": bench,
            "_perf": {"loadavg": {"1": load1}}}

# Seattle3 (10.130.130.1): strong box. Read on sea3 it is idle -> high perf score (~33);
# read on sea5 it is loaded -> low perf score (~6). Same static capability, different load.
s3_idle  = cand("10.130.130.1", 0.1, mem_kb=8_000_000, cores=4, wmbps=200.0,
                fsync=2.0, free=50.0, bench=5000.0, innodb=2_000_000_000)
s3_busy  = cand("10.130.130.1", 8.0, mem_kb=8_000_000, cores=4, wmbps=200.0,
                fsync=2.0, free=50.0, bench=5000.0, innodb=2_000_000_000)
# Seattle5 / control (10.250.250.1): steady mid box, the highest IP in the pond.
s5       = cand("10.250.250.1", 0.5)
# extras only sea3 saw (incl. the unreachable phantom 10.179.179.1 no-age won't drop)
s2       = cand("10.120.120.1", 1.0)
ny       = cand("10.102.60.1", 0.3, mem_kb=1_500_000, wmbps=20.0, fsync=20.0, bench=800.0)
phantom  = cand("10.179.179.1", 0.1, mem_kb=6_000_000, cores=4, wmbps=150.0,
                fsync=3.0, free=40.0, bench=4000.0)

sea5_pool = [s5, s3_busy]                       # what sea5 read:  {250, 130}
sea3_pool = [s5, s3_idle, s2, ny, phantom]      # what sea3 read:  {250,130,120,102,179.179}

h = DatabaseRoleHandler()
w5 = (h.evaluate(sea5_pool, []) or {}).get("lan_ip")
w3 = (h.evaluate(sea3_pool, []) or {}).get("lan_ip")
print(f"  sea5 elects {w5} ; sea3 elects {w3}")

check(w5 == w3, f"both nodes elect the SAME databasehost (sea5={w5} sea3={w3})")
check(w5 == "10.130.130.1",
      "converged winner is the most-capable mysql box (present in both pools), not "
      "load-jittered away on the busy node")

# Load-invariance in isolation: re-scoring the identical pool under a load spike must
# not move the winner.
spike = [dict(c, _perf={"loadavg": {"1": 9.9}}) for c in sea3_pool]
ws = (h.evaluate(spike, []) or {}).get("lan_ip")
check(ws == w3, "winner is invariant to a load spike on every candidate")

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
