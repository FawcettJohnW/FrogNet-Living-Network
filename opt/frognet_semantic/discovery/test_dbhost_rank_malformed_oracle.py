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
test_dbhost_rank_malformed_oracle.py - [RANK_NUM_GUARD_V1]

Folds in John's 2026-06-19 box crash: select_database_host._rank read every
capability field with a bare int()/float(), so a malformed publish
(mem_available_kb arriving as a dict) raised TypeError and took the WHOLE merge
down (rc=1) AFTER discovery had already succeeded (verified=2). Same class as the
get_all capability-blob guard, different function.

The fix coerces each numeric field through _num: a bad field degrades that one
candidate's score, it never crashes the merge. These are already mysql-capable
.1 hosts, so a malformed perf field must NOT drop a DB-capable host - it ranks
with that field defaulted.

Drives the REAL select_database_host with _capability_index stubbed to two
eligible .1s, one carrying mem_available_kb as a dict:
  A  select_database_host does NOT raise (the crash is gone)
  B  a winner is still elected, and it is the clean, higher-capacity host
  C  _num coerces dict/None/str/bool/garbage without raising

REGRESSION: on the bare-int() code, A raises TypeError.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import discovery.hosts as H

CLEAN = "10.111.11.1"      # clean blob, higher bench
BAD = "10.102.60.1"        # mem_available_kb is a dict (the offending publish)

CAPS = {
    BAD: {"mysql_running": True, "cores": 4, "cpu_bench_total": 5000,
          "mem_available_kb": {"value": 7156240},      # <-- malformed: a dict
          "disk_write_mbps": 7.6, "disk_fsync_ms": 644.81, "disk_free_gb": 3.6},
    CLEAN: {"mysql_running": True, "cores": 4, "cpu_bench_total": 10190,
            "mem_available_kb": 1251364,
            "disk_write_mbps": 7.0, "disk_fsync_ms": 50.85, "disk_free_gb": 9.0},
}
ETC = [f"{CLEAN} BABox", f"{BAD} New-York-1", "10.250.250.1 Seattle5"]


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    H._capability_index = lambda gate, role="databasehost", dbhost=None: dict(CAPS)

    logs = []
    raised = None
    try:
        H.select_database_host(ETC, logger=logs.append)
    except Exception as e:
        raised = e

    check(raised is None,
          f"A  select_database_host does not raise on a dict-valued field "
          f"({type(raised).__name__ if raised else 'no error'})")

    winner = ""
    for l in logs:
        if "branch=mysql_eligible" in l and "winner=" in l:
            winner = l.split("winner=")[1].split()[0]
    check(winner == CLEAN,
          f"B  winner still elected, the clean higher-capacity host (got {winner or 'none'})")

    coerced = all([
        H._num({"x": 1}, 0.0) == 0.0,
        H._num(None, 0.0) == 0.0,
        H._num("12.5", 0.0) == 12.5,
        H._num("garbage", 0.0) == 0.0,
        H._num(True, 0.0) == 0.0,
        H._num([1, 2], 0.0) == 0.0,
        H._num(42, 0.0) == 42,
    ])
    check(coerced, "C  _num coerces dict/None/str/bool/list/int without raising")

    print()
    print("ALL DBHOST-RANK-MALFORMED ORACLE CHECKPOINTS PASS" if ok
          else "DBHOST-RANK-MALFORMED PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
