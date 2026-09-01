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
test_lan_chain_oracle.py - proves the simulator reproduces the Seattle LAN-chain
"deep children don't learn their ancestors" scenario from the four runMerge logs,
and that the base (return-path-blind) fabric does NOT reproduce it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.lan_chain import build_seattle_chain, ChainSystem, REMOTES
from discovery.sim.system import System

REMOTE_S3 = set(REMOTES)                       # {10.102.60, 10.28.28, 10.179.179}
S = {"Seattle5": "10.250.250", "Seattle6": "10.160.160",
     "Seattle3": "10.130.130", "Seattle2": "10.120.120"}
NAME = {v: k for k, v in {**S, **{k: k for k in []}}.items()}
NAME.update({v: n for v, n in zip(REMOTES.keys(), REMOTES.values())})


def _names(s3set):
    return sorted(NAME.get(x, x) for x in s3set)


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + msg)
        ok = ok and cond

    # --- reproduction: ChainSystem models the return-path failure ----------
    cs = ChainSystem(build_seattle_chain())
    cs.converge()
    k = cs.known
    print("ChainSystem converged. Known host subnets per node:")
    for n in ("Seattle5", "Seattle6", "Seattle3", "Seattle2"):
        print(f"    {n:9} -> {_names(k[n])}")
    print()

    # [VOUCH_ROUTE_V1] every node must learn EVERY other host. Pre-fix, the
    # _echo_carries=False return-path constraint blocked the deep nodes: a node
    # 2+ hops in could not echo a tunnel remote over its borrowed uplink lease, so
    # it learned none of them and the gap cascaded. Routing is now authority-driven
    # (the upstream's getHosts vouch establishes the route regardless of echo), so
    # the constraint no longer limits the known set.
    ALL = set(S.values()) | set(REMOTES)
    for n in ("Seattle5", "Seattle6", "Seattle3", "Seattle2"):
        want = ALL - {S[n]}
        check(want <= k[n],
              f"{n} learns ALL other hosts (full mesh); "
              f"missing={_names(want - k[n])}")

    check(REMOTE_S3 <= k["Seattle3"],
          "Seattle3 (2 hops) now learns tunnel remotes NY1/NY2/BAMacBook via vouch")
    check(REMOTE_S3 <= k["Seattle2"],
          "Seattle2 (3 hops) now learns the tunnel remotes via vouch (cascade)")

    # The constrained ChainSystem now converges to the SAME mesh as the
    # unconstrained base System -> proves vouch (not echo) is the mechanism: the
    # echo return-path failure is irrelevant to what gets routed.
    base = System(build_seattle_chain())
    base.converge()
    print()
    print(f"    base Seattle3 -> {_names(base.known['Seattle3'])}")
    for n in ("Seattle3", "Seattle2"):
        check((k[n] & ALL) == (base.known[n] & ALL),
              f"{n}: vouch routing converges despite the echo constraint "
              f"(same host set as unconstrained base)")

    print()
    print("ALL LAN-CHAIN ORACLE CHECKPOINTS PASS" if ok else "LAN-CHAIN PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
