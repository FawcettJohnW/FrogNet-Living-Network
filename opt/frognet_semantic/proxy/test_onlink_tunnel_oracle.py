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
"""test_onlink_tunnel_oracle — [ONLINK_TUNNEL_TO_SEMANTIC_V1].

The on-link/HAM bypass ([ONLINK_DIRECT_V1]) sent any on-link frognet target straight to
the wire, skipping the daemon. That is correct for a LOCAL-subnet neighbour (avoids a
proxy<->daemon loop) but WRONG for a WireGuard tunnel peer: a wg route is on-link too
(via='' scope-link over AllowedIPs), so a remote high-latency peer like NY-1 got HAM and
never reached SAME/DIFF or coalescing -> not one SAME, no burst collapse. Fix: exclude wg,
like wl. This drives the REAL decide_path_for_target with a mocked route table.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import proxy.decision as D
from proxy.constants import DecisionPath

FAILS = []
def check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

def _run(dev, via, out="", frognet=True, local=False):
    D.route_get = lambda ip: (dev, via, out)
    D.is_local_ip = lambda ip: local
    D._is_frognet_target = lambda ip: frognet
    D.refresh_policy_if_changed = lambda: None
    path, *_ = D.decide_path_for_target("10.102.60.1", 9009, set())
    return path

def main():
    print("=== ON-LINK TUNNEL ORACLE ===")
    # wg tunnel, on-link (via='') -> must NOT be HAM anymore (goes semantic/fast -> coalesces)
    check("wg2 on-link peer is NOT HAM (reaches the daemon -> coalescing)",
          _run("wg2", "") is not DecisionPath.HAM)
    check("wg0 on-link peer is NOT HAM",
          _run("wg0", "") is not DecisionPath.HAM)
    # a REAL local-subnet neighbour on eth0 -> still HAM (loop protection intact)
    check("eth0 on-link neighbour is STILL HAM (loop protection intact)",
          _run("eth0", "") is DecisionPath.HAM)
    # wireless on-link -> unchanged (was already excluded from HAM)
    check("wlan0 on-link is NOT HAM (unchanged)",
          _run("wlan0", "") is not DecisionPath.HAM)
    # a target reached via a gateway -> never the on-link branch (unchanged)
    check("gateway'd target is NOT HAM", _run("eth0", "10.102.60.254") is not DecisionPath.HAM)

    print("\n" + ("ALL ON-LINK TUNNEL CHECKS PASS" if not FAILS
                  else f"ON-LINK TUNNEL ORACLE FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
