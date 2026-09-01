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
test_capability_no_age_oracle.py - [CAPABILITY_READ_NO_AGE_V1]

Seattle5/Seattle3 2026-06-21: a quiescent net, yet the two nodes elect DIFFERENT
databasehosts (250 vs 120). Root cause: the election READ ages the tuple out itself.
gather_candidates() passed fresh_s=CAPABILITY_FRESH_S into get_all(), which does
`if fresh_s and (now-ts) > fresh_s: continue` - dropping rows older than 180s INSIDE
the read. Two nodes reading the same persisted DB at different wall-clock instants
therefore see different subsets and elect different winners. That is the network
layer aging a tuple; aging is the CONSUMER's call, not the read path's.

Doctrine: the producer writes; the tuple persists (overwrite-on-new-value + the
30-min reaper is GC, not election aging); the CONSUMER decides liveness. The election
consumer already does, deterministically: [SERVICE_ELECT_REACHABLE_V1] drops any
candidate whose /24 did not win a route THIS merge. ts liveness is a vestigial proxy
that only injects the race.

Fix: read capability with fresh_s=0 - the read never ages anything. Every node pulls
the identical persisted pool; reachability (measured, per-node) decides who is live.

Drives the REAL gather_candidates / service_host_lines / DatabaseRoleHandler against
ONE fixed persisted tuple set, with the clock advanced between reads:
  A  two reads at different clock offsets yield the IDENTICAL candidate pool
  B  ...and therefore the identical elected winner
  C  an OLD-ts pool still elects (the read did not wipe it) - ts is not liveness
  D  an unreachable host is dropped by the REACHABILITY gate, not by ts
REGRESSION: on the fresh_s=CAPABILITY_FRESH_S read, A/B fail (the later read ages the
whole pool to empty -> divergent / no winner).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.frognet_tuples as FT
from core.frognet_role_elect import gather_candidates
from core.frognet_service_hosts import service_host_lines
from core.role_registry import DATABASE_HANDLER

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

# ---- one fixed, persisted tuple set (written once, ts never updated) --------
T_WRITE = 1000
def row(ip, **perf):
    data = {"ts": T_WRITE, "lan_ip": ip, "mysql_running": True}
    data.update(perf)
    # [ENVELOPE_TS_V1] the store stamps the row; the payload ts is not consulted.
    # The double carries an envelope so it can stand in for api.php, which is now
    # what decides freshness.
    return {"data": data, "SensorName": f"SD:capability.host:{ip}:databasehost",
            "SensorAddress": ip, "SensorID": ip, "UpdatedAtEpoch": T_WRITE}

PERSISTED = [
    row("10.120.120.1", cores=4, mem_available_kb=8000000, cpu_bench_total=9000,
        disk_write_mbps=30.0, disk_fsync_ms=15.0, disk_free_gb=20.0),   # strongest
    row("10.130.130.1", cores=4, mem_available_kb=2000000, cpu_bench_total=5000,
        disk_write_mbps=2.0, disk_fsync_ms=68.0, disk_free_gb=12.0),
    row("10.250.250.1", cores=4, mem_available_kb=4000000, cpu_bench_total=7000,
        disk_write_mbps=20.0, disk_fsync_ms=15.0, disk_free_gb=4.0),
]

# Fake the DB read: capability rows for databasehost, nothing else (reach_plane empty
# -> WAN plane empty -> all candidates are LAN). The set NEVER changes - it's persisted.
def fake_values_raw(service, dbhost=None, timeout=4.0, name_like=None, fresh_s=0):
    """Stands in for api.php, INCLUDING its freshness filter: &fresh_s=N returns only
    rows whose envelope is within N seconds of the STORE's clock."""
    if service != "databasehost":
        return []
    rows = list(PERSISTED)
    if fresh_s:
        now = FT.time.time()
        rows = [r for r in rows if (now - r.get("UpdatedAtEpoch", 0)) <= fresh_s]
    return rows
FT._values_raw = fake_values_raw

# Controllable clock for the read-side age test (only get_all reads time.time()).
class _Clock:
    t = 0
    def time(self): return self.t
FT.time = _Clock()
import core.frognet_role_elect as _REmod
_REmod._clock = lambda: FT.time.time()   # admissibility ages on the injected clock

DBHOST = "databasehost_control.frognet"
def pool_at(now):
    FT.time.t = now
    hosts, _lan = gather_candidates(DATABASE_HANDLER, dbhost=DBHOST)
    return sorted(c["lan_ip"] for c in hosts)
def winner_at(now):
    FT.time.t = now
    hosts, lan = gather_candidates(DATABASE_HANDLER, dbhost=DBHOST)
    w = DATABASE_HANDLER.evaluate(hosts, lan)
    return w.get("lan_ip") if w else None

# [SUPERSESSION 2026-07-06 - BALLOT_ADMISSIBILITY_V1] The chain, in full:
#   2026-06-12: fresh_s=180 read gate - caused the June-21 chronic split-brain
#     (180s gate vs 300s heartbeat: every row perpetually AT the boundary, so
#     nodes reading seconds apart elected different winners, forever).
#   2026-06-23: no-age read - cured the race by removing aging from the READ,
#     with the doctrine line "aging is the CONSUMER's call, not the read
#     path's", and delegated liveness to the reaper.
#   2026-07-06 (John, verbatim): "The tuple has a timestamp. Using the
#     timestamp is the responsibility of the consumer." Field proof: an
#     8.1-day fossil won the databasehost election all day because the reaper
#     guarantee was not operating. The election consumer now exercises the
#     call the June-23 doctrine reserved for it: ballots unrefreshed past
#     FROGNET_BALLOT_MAX_AGE_S (1800s >> 300s heartbeat) are inadmissible.
#   The June-21 disease stays dead by ARITHMETIC, not by never aging: a
#   living node's ballots sit at <=~300s, an order below the horizon, so
#   no boundary race exists in steady state; only a DYING node's ballot
#   crosses, once, for at most one merge of transient disagreement.
NOW_FRESH  = T_WRITE + 100     # age 100s
NOW_FRESH2 = T_WRITE + 130     # 30s later - the June-21 race window
NOW_OLD    = T_WRITE + 100000  # age 100000s: past ANY sane horizon

# ---- A/B: the June-21 anti-race property, at ages living nodes actually have
pool_a, pool_b = pool_at(NOW_FRESH), pool_at(NOW_FRESH2)
print("  pool @t:", pool_a, "  pool @t+30s:", pool_b)
check(pool_a == pool_b == ["10.120.120.1", "10.130.130.1", "10.250.250.1"],
      "A reads 30s apart at heartbeat-fresh ages: IDENTICAL pool (race dead)")
check(winner_at(NOW_FRESH) == winner_at(NOW_FRESH2) == "10.120.120.1",
      "B identical winner across the race window (no split-brain)")

# ---- C: [RE-INVERTED per CAPABILITY_DOES_NOT_AGE_V1] an all-fossil pool still
# elects, because age is not the question.
#
# This check has now been written both ways. It asserted the 2026-07-06 ballot
# horizon; frognet_role_elect.gather_candidates supersedes it BY NAME -- "The
# age gate is GONE. Superseded [BALLOT_ADMISSIBILITY_V1], which refused any
# ballot past a max-age."
#
# The reasoning there is what this check now encodes: the 8.1-day fossil that
# won for a day was not a stale DESCRIPTION -- that box still had its cores --
# it was an UNREACHABLE box. Age only correlated with liveness and the
# correlation broke both ways: a live mediahost whose advertiser write timed out
# was refused at ts_age=2040s (measured, 10.28.28.1), while a node that wrote a
# minute before dying was admitted. Capability rows may live forever; the reaper
# removes a dead node's rows, and that removal is a SHARED fact every node reads
# identically -- unlike a local probe, which is what [NO_LOCAL_BY_DEFINITION_V1]
# and [NO_REACHABILITY_CULL_V1] exist to keep out of the determination.
#
# So the property under test is that the READ still never ages, at any age.
check(pool_at(NOW_OLD) == ["10.120.120.1", "10.130.130.1", "10.250.250.1"]
      and winner_at(NOW_OLD) == "10.120.120.1",
      "C a 100000s-old pool is STILL admissible in full and elects the same "
      "winner - the read never ages [CAPABILITY_DOES_NOT_AGE_V1]")

# ---- D: read no longer ages, AND the determination no longer culls by reachability --
# [NO_REACHABILITY_CULL_V1] superseded the per-node gate: the role's UnREST callback
# elects over the WHOLE persisted pool, identical on every node. Passing a per-node
# reachable set must NOT change the winner and must NOT drop anyone - that culling is
# gone (it caused the databasehost split). ts still never gates (no-age).
FT.time.t = NOW_FRESH   # [2026-07-06] D's property is reach-cull absence - orthogonal to age
logs = []
reach = {"10.120.120.0/24", "10.130.130.0/24"}   # would have dropped 250 under the old gate
lines = service_host_lines(dbhost=DBHOST, logger=logs.append, reachable_subnets=reach)
db_line = next((l for l in lines if l.endswith("databasehost.frognet")), "")
print("  databasehost line @old-ts (reach set ignored):", db_line or "<none>")
check(db_line != "",
      "D1 a databasehost is elected from the old-ts pool (read never ages it out)")
check(not any("SERVICE_DROP" in l for l in logs),
      "D2 NO candidate is dropped by a reachability cull (the per-node gate is retired)")
lines_all = service_host_lines(dbhost=DBHOST, logger=lambda s: None, reachable_subnets=None)
db_all = next((l for l in lines_all if l.endswith("databasehost.frognet")), "")
check(db_all == db_line and db_all != "",
      "D3 winner is identical with vs without a reachable set - the determination is a "
      "pure function of the tuples, not of this node's routes")

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
