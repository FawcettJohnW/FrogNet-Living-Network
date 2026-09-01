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
"""test_service_handlers_static_oracle — [DBHOST_STATIC_RANK_V1]/[MEDIAHOST_STATIC_RANK_V1].

The SERVICE role handlers (the elections that actually write databasehost.frognet /
mediahost.frognet) must score on STATIC capability so a node's own tuple ranks identically
write-to-write and every box agrees. Before: DatabaseRoleHandler scored on mem_available
(free RAM); SotFMediaHandler multiplied by load headroom and instantaneous temperature.
Both made the elected host thrash on runtime state. Drives the REAL handler .score().
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.database_handler import DatabaseRoleHandler
from core.sotf_handler import SotFMediaHandler

FAILS = []
def check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

def main():
    print("=== SERVICE HANDLERS STATIC RANK ORACLE ===")
    db = DatabaseRoleHandler()
    def dbcand(avail_kb):
        return dict(mysql_running=1, mem_total_kb=8*1024*1024, mem_available_kb=avail_kb,
                    cores=4, cpu_bench_total=3000, disk_write_mbps=40, disk_fsync_ms=5, disk_free_gb=50)
    db_scores = {db.score(dbcand(a)) for a in (2*1024*1024, 8*1024*1024, 14*1024*1024)}
    check(f"databasehost score invariant to free-RAM drift ({sorted(db_scores)})", len(db_scores) == 1)

    me = SotFMediaHandler()
    def mecand(load1, temp):
        return dict(ffmpeg=1, libvpx=1, cores=4, cpu_bench_total=3000, mem_total_kb=8*1024*1024,
                    _perf={"loadavg": {"1": load1}, "temps_c": [{"temp_c": temp}]})
    me_scores = {round(me.score(mecand(l, t)), 6) for l, t in [(0.1, 40), (3.5, 40), (0.1, 85), (3.5, 85)]}
    check(f"mediahost score invariant to load+temp drift ({sorted(me_scores)})", len(me_scores) == 1)

    # sanity: static merit still discriminates (a faster CPU -> higher score)
    #
    # NOT cpu_bench_total. [DBHOST_STATIC_RANK_V1] was extended to CPU and disk
    # after this check was written: database_handler.score() now reads
    # cores * cpu_mhz, because "a benchmark on a busy box measures the busy, not
    # the box" -- the same feedback loop that fsync caused (Seattle5 2026-08-08,
    # seven role changes in one hour). cpu_bench_total, disk_write_mbps and
    # disk_fsync_ms are all deliberately ignored now, so varying one of them
    # correctly changes nothing and this check was asserting the OLD inputs.
    # SotFMediaHandler still scores cpu_bench_total, which is why the mediahost
    # half of this oracle kept passing.
    check("databasehost still ranks a stronger box higher",
          db.score(dbcand(4*1024*1024)) < db.score(dict(dbcand(4*1024*1024), cpu_mhz=3000)))
    check("mediahost still ranks a stronger box higher",
          me.score(mecand(0.1, 40)) < me.score(dict(mecand(0.1, 40), cpu_bench_total=9000)))

    print("\n" + ("ALL SERVICE-HANDLER-STATIC CHECKS PASS" if not FAILS
                  else f"SERVICE-HANDLER-STATIC ORACLE FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
