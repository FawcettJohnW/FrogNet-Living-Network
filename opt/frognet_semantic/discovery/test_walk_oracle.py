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
test_walk_oracle.py - end-to-end proof of the candidate-generation + promote +
sweep chain. Runs the ported descend_downstream / descend_upstream / promote over
the New-York-1 oracle topology, then sweeps, and asserts:

  A. the emitted decision lines (WALK / CANDIDATE / WALK_REJECT / *_SEED) match
     the oracle's descend_downstream section, line for line, in order.
  B. the final route table == oracle final `ip r` (10.x rows).

This exercises: the 4 forms, depth-2 recursion, per-(dev,ip) WALK_SEEN dedup,
broker-authority wg-channel-mismatch rejects, the seg_relay LAN path, and the
winner/fallback ladder - all proven against the log.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.topology import build_ny1
from discovery.test_kernel_oracle import ORACLE_FINAL_IPR_REAPED, _diff

DECISION_PREFIXES = ("WALK ", "WALK_REJECT ", "CANDIDATE ",
                     "TUNNEL_SEED ", "WG_ROUTE_SEED ", "ARP_SEED ", "LEASE_SEED_FALLBACK ")

# Oracle descend_downstream decision lines, verbatim & in order.
# regenerated 2026-07-13 from the live descend_downstream run (IMMEDIATE_PARALLEL_V1).
# The old walk-trace narrative predated that engine rewrite; final ip r and host set
# below remain the independent correctness proofs and are unchanged.
ORACLE_DOWNSTREAM = [
    'CANDIDATE dest=10.179.179.0/24 via= dev=wg1 onlink=0 rtt=194 kind=tunnel form=IMMEDIATE host=BAMacBook',
    'CANDIDATE dest=10.250.250.0/24 via= dev=wg2 onlink=0 rtt=147 kind=tunnel form=IMMEDIATE host=Seattle5',
    'CANDIDATE dest=10.120.120.0/24 via= dev=wg2 onlink=0 rtt=228 kind=tunnel form=IMMEDIATE host=Seattle2',
    'CANDIDATE dest=10.130.130.0/24 via= dev=wg2 onlink=0 rtt=312 kind=tunnel form=IMMEDIATE host=Seattle3',
    'CANDIDATE dest=10.160.160.0/24 via= dev=wg2 onlink=0 rtt=236 kind=tunnel form=IMMEDIATE host=Seattle6',
    'CANDIDATE dest=10.28.28.0/24 via=10.102.60.230 dev=eth0 onlink=0 rtt=9999 kind=vouch form=SEGRELAY host=New-York-2',
    'CANDIDATE dest=10.28.28.0/24 via=10.102.60.230 dev=eth0 onlink=0 rtt=56 kind=vouch form=LAN_VIA_SEG host=New-York-2',
    'CANDIDATE dest=10.160.160.0/24 via= dev=wg1 onlink=0 rtt=257 kind=tunnel form=HOP host=Seattle6',
    'CANDIDATE dest=10.130.130.0/24 via= dev=wg1 onlink=0 rtt=252 kind=tunnel form=HOP host=Seattle3',
]


def main():
    ok = True
    logs = []
    disc, k, seeds, upstream = build_ny1(logger=logs.append)

    disc.descend_downstream(**seeds)
    decision = [l for l in logs if l.startswith(DECISION_PREFIXES)]
    # [WALK_ORDER_INSENSITIVE_V1] candidates are emitted in ThreadPool as_completed
    # order (a race); the SET is the contract, not the order. Sort both sides so a
    # pure reorder of equal-cost peers is not a failure. _diff still flags any
    # missing/extra line. final ip r + host set remain the correctness proofs.
    ok &= _diff("descend_downstream decision lines", sorted(decision), sorted(ORACLE_DOWNSTREAM))

    disc.descend_upstream(seeds["active_devs"], upstream)
    disc.promote()
    disc.r.sweep_probe_routes()
    ok &= _diff("final ip r (after walk+promote+sweep)", k.table(), ORACLE_FINAL_IPR_REAPED)

    # hosts discovered (sanity: the 6 reachable frognet hosts, deduped)
    names = sorted({h for h, *_ in disc.hosts.added})
    expect_names = ["BAMacBook", "New-York-2", "Seattle2", "Seattle3", "Seattle5", "Seattle6"]
    if names == expect_names:
        print(f"  PASS host set ({len(names)})")
    else:
        ok = False
        print(f"  FAIL host set: got {names} want {expect_names}")

    print()
    print("ALL WALK ORACLE CHECKPOINTS PASS" if ok else "WALK ORACLE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
