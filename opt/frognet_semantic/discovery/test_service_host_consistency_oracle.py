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
test_service_host_consistency_oracle.py - [NO_DBHOST_FLOOR_V1 / NO_REACHABILITY_CULL_V1]

Seattle3 elected itself databasehost (130) while peers elected 250 - a split. Root
cause was TWO things layered on the role determination: (1) a floor (select_database_host)
that was a second scoring path AND made the MERGE write databasehost.frognet, and (2) a
per-node reachability cull in service_host_lines that filtered the candidate pool by THIS
node's own routes - so a leaf keeps itself while peers drop it.

Doctrine: the merge writes ONLY databasehost_control.frognet. AFTER the merge, every
component's host is its UnREST callback's pure pick over the SHARED capability tuples -
a function of the converged memory, identical on every node. No floor, no fallback, no
per-node cull. Local by definition only when the callback names no winner. The
determination is a read; it never triggers a re-run.

Drives the REAL service_host_lines / DatabaseRoleHandler / GameRoleHandler against ONE
fixed persisted tuple set, simulating several nodes by varying ONLY the per-node inputs
that USED to change the outcome (reachable_subnets, self_ip):
  A  databasehost.frognet is IDENTICAL across nodes with DIFFERENT reachable sets
  B  boardgame.frognet (flat score, highest-IP tiebreak) is identical across nodes
  C  local-by-definition: empty pool + self_ip -> the line is self_ip (never absent host)
REGRESSION: on the old per-node-cull code, A/B diverge (a node that can't route to 130's
/24 drops it and elects a different host) -> the split this fix removes.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.frognet_tuples as FT
from core.frognet_service_hosts import service_host_lines

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

# ---- one fixed, persisted tuple memory (every host advertises every role) ----
def row(ip, role, **perf):
    import time as _t
    data = {"ts": int(_t.time()), "lan_ip": ip, "mysql_running": True}  # fresh: isolate the gate, not read-aging
    data.update(perf)
    # [ENVELOPE_TS_V1] carry the store's envelope; the payload ts is not consulted
    return {"data": data, "SensorName": f"SD:capability.host:{ip}:{role}",
            "SensorAddress": ip, "SensorID": f"{ip}:{role}",
            "UpdatedAtEpoch": int(data.get("ts", 0) or 0)}

PERF = {
    "10.130.130.1": dict(cores=4, mem_available_kb=15000000, cpu_bench_total=24000,
                         disk_write_mbps=2.1, disk_fsync_ms=68.0, disk_free_gb=12.0),  # most RAM
    "10.250.250.1": dict(cores=4, mem_available_kb=7000000, cpu_bench_total=26000,
                         disk_write_mbps=30.0, disk_fsync_ms=15.0, disk_free_gb=4.0),
    "10.120.120.1": dict(cores=4, mem_available_kb=8000000, cpu_bench_total=9000,
                         disk_write_mbps=20.0, disk_fsync_ms=15.0, disk_free_gb=20.0),
}
ROLES = ("databasehost", "boardgame", "mediahost")
PERSISTED = {r: [row(ip, r, **PERF[ip]) for ip in PERF] for r in ROLES}

def fake_values_raw(service, dbhost=None, timeout=4.0, name_like=None, fresh_s=0):
    return list(PERSISTED.get(service, []))          # [] for discovery/reach_plane -> all LAN
FT._values_raw = fake_values_raw

DBHOST = "databasehost_control.frognet"
def winner(role, reachable, self_ip=None, rows=PERSISTED):
    # swap the persisted set if a test wants a different pool (e.g. empty)
    global PERSISTED
    saved = PERSISTED; PERSISTED = rows
    try:
        kw = dict(dbhost=DBHOST, logger=lambda s: None, reachable_subnets=reachable)
        if self_ip is not None:
            kw["self_ip"] = self_ip
        lines = service_host_lines(**kw)
    finally:
        PERSISTED = saved
    for l in lines:
        if l.endswith(f"{role}.frognet"):
            return l.split()[0]
    return None

ALL   = {"10.130.130.0/24", "10.250.250.0/24", "10.120.120.0/24"}
NO130 = {"10.250.250.0/24", "10.120.120.0/24"}      # a node that can't route to 130's /24

# ---- A: databasehost identical across nodes with different reachable sets ----
a_db = winner("databasehost", ALL)                  # node that reaches everyone
b_db = winner("databasehost", NO130)                # leaf-blind node (can't route to 130)
print(f"  databasehost: reach-all={a_db}  reach-no130={b_db}")
check(a_db is not None and a_db == b_db,
      "A databasehost.frognet identical regardless of per-node reachable set "
      "(no per-node cull -> no split)")

# ---- B: boardgame identical (flat score, highest-IP tiebreak) ----------------
a_bg = winner("boardgame", ALL)
b_bg = winner("boardgame", NO130)
print(f"  boardgame: reach-all={a_bg}  reach-no130={b_bg}")
check(a_bg is not None and a_bg == b_bg,
      "B boardgame.frognet identical across nodes")

# ---- C: [NO_LOCAL_BY_DEFINITION_V1] empty pool -> NO LINE, and never self_ip --
#
# This check asserted the opposite until now: empty pool + self_ip must yield
# self_ip, "local by definition". That rule was RETIRED. service_host_lines'
# docstring states the reason -- a node that names itself when the pool is empty
# diverges from every other node doing the same, and hides a failed read behind
# a plausible answer. Every node running local-by-definition against an empty
# pool elects a DIFFERENT databasehost, which is precisely the split A and B
# above exist to prevent, so the old C contradicted them.
#
# self_ip is still ACCEPTED for caller-signature compatibility and ignored, so
# passing it is the sharp version of the test: the argument is there and must
# still not name a winner.
EMPTY = {r: [] for r in ROLES}
try:
    db_local = winner("databasehost", ALL, self_ip="10.130.130.1", rows=EMPTY)
    check(db_local is None,
          "C empty pool -> databasehost gets NO line; self_ip is accepted and "
          "must NOT name a winner [NO_LOCAL_BY_DEFINITION_V1]")
except TypeError as e:
    check(False, f"C self_ip no longer accepted at all (signature change: {e})")

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
