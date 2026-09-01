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
test_runmerge_oracle.py - top-level proof: a full runMerge pass over the NY-1
oracle topology reproduces the converge_decision line AND both artifacts.
"""
import os as _os  # [OFFLINE_TUPLES_GATE_V1] build-oracle floors deterministically:
_os.environ.setdefault("FROGNET_OFFLINE_TUPLES", "1")  # never read live capability
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.topology import build_ny1
from discovery.runmerge import run_merge
from discovery.test_kernel_oracle import ORACLE_FINAL_IPR_REAPED, _diff
from discovery.test_hosts_oracle import ORACLE_ETC_HOSTS, BRINGUP_PEERS

ORACLE_CONVERGE = "converge_decision sync_required=1 runAgain=1 depth=0 cap=10"


def main():
    ok = True
    logs = []
    disc, k, seeds, upstream = build_ny1()
    out = run_merge(disc, k, seeds, upstream,
                    local=("10.102.60.1", "New-York-1"),
                    bringup_peers=BRINGUP_PEERS,
                    depth=0, concurrent_attempt=True,   # oracle had runAgain=1
                    logger=logs.append)

    conv = [l for l in logs if l.startswith("converge_decision")]
    # [RUNAGAIN_ON_MUTATION_V1] the line now carries extra diagnostic fields
    # (routes_mutated/transit_changed/concurrent); the invariant the oracle pins
    # is the leading decision tuple. Match by prefix so the diagnostics can grow
    # without re-blessing, while still asserting the exact decision.
    if len(conv) == 1 and conv[0].startswith(ORACLE_CONVERGE):
        print("  PASS converge_decision line")
    else:
        ok = False
        print(f"  FAIL converge_decision: got {conv} want prefix [{ORACLE_CONVERGE!r}]")

    ok &= _diff("final ip r", out["final_table"], ORACLE_FINAL_IPR_REAPED)
    ok &= _diff("/etc/hosts", out["etc_hosts"], ORACLE_ETC_HOSTS)

    print()
    print("ALL RUNMERGE ORACLE CHECKPOINTS PASS" if ok else "RUNMERGE ORACLE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
