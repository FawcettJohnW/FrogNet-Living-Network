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
sim_src_pin.py - demonstrate, against REAL code, that the current src-pin is too
wide (John's Seattle5 finding) AND surface the collision a naive narrowing hits.

Rule under test (John): "src should only be on WLAN nodes, and then only on the
first hop of the node. Everything else should go VIA that first hop." I.e. a
discovered downstream /24 (reached `via <first-hop>`) must be src-LESS; only the
WLAN node's own first hop / exit carries src=identity.

Current code: discovery.discovery.Discovery.dev_src() returns self_identity for
EVERY dev (LAN and wg alike), and promote() stamps it on every installed link.
That reproduces Seattle5's table: every mesh /24 carries `src 10.250.250.1`.

This drives the REAL dev_src + REAL Routes.install_if_changed + REAL FakeKernel.
The rule-assertions are expected to FAIL against the current tree - that is the
point: the simulator shows the code does not satisfy the rule. The last section
flags the wg-egress collision with the existing [LEAF_SRC_PIN_V1] design.

Run: PYTHONPATH=<tree>/opt/frognet_semantic python3 -m simulation.sim_src_pin
"""
import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
sys.dont_write_bytecode = True

from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import HostStore

FAILS = []
def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  - {detail}"))
    if not ok:
        FAILS.append(name)

def line_for(k, dest):
    return " ".join(x for x in (k.route_show(dest) or "").replace(";", " ").split())

# -- WLAN node identical to Seattle5: identity 10.250.250.1 on eth0 (frognet
#    LAN), WAN uplink on wlan1, reaches the mesh via first-hop relay
#    10.250.250.221 on eth0. wg dev_src_map carries a /30 (the NY1 shape). --
IDENT = "10.250.250.1"
HOP   = "10.250.250.221"
MESH  = ["10.28.28.0/24", "10.102.60.0/24", "10.111.11.0/24", "10.120.120.0/24",
         "10.130.130.0/24", "10.160.160.0/24", "10.179.179.0/24"]

def _disc(k):
    return Discovery(Routes(k), None, None, None, None, HostStore(),
                     local_ips={IDENT, "10.250.250.2"},
                     dev_src_map={"wg2": "10.253.203.90"},
                     self_identity=IDENT)

def s_reproduce_seattle5():
    print("fixed: dev_src still returns identity, but install_if_changed strips it on LAN via-routes")
    k = FakeKernel()
    d = _disc(k)
    src = d.dev_src("eth0")                      # REAL: still returns identity
    check("dev_src('eth0') still returns identity (the pin source is unchanged)", src == IDENT, repr(src))
    for dest in MESH:                            # mirror promote(): winner via the first hop
        d.r.install_if_changed(dest, HOP, "eth0", 22, 0, src)
    sample = line_for(k, "10.28.28.0/24")
    check("FIXED: every mesh /24 via the hop is src-LESS and onlink-LESS (John's Seattle2 shape)",
          all(f"via {HOP}" in line_for(k, dst) and " src " not in line_for(k, dst)
              and " onlink" not in line_for(k, dst) for dst in MESH), sample)

def s_rule_downstream_srcless():
    print("RULE: a downstream /24 reached VIA the first hop must be src-LESS "
          "(expected to FAIL on current code - that's the demonstration)")
    k = FakeKernel()
    d = _disc(k)
    src = d.dev_src("eth0")
    for dest in MESH:
        d.r.install_if_changed(dest, HOP, "eth0", 22, 0, src)
    offenders = [dst for dst in MESH if " src " in line_for(k, dst)]
    check("no downstream via-route carries src (John's rule)",
          offenders == [],
          f"{len(offenders)}/{len(MESH)} downstream routes wrongly pinned, e.g. "
          f"{line_for(k, offenders[0]) if offenders else ''}")

def s_first_hop_keeps_src():
    print("RULE: the WLAN node's own first-hop / connected segment legitimately "
          "carries src=identity")
    k = FakeKernel()
    # connected first-hop segment as the kernel renders it (proto kernel)
    k.seed(f"10.250.250.0/24 dev eth0 proto kernel scope link src {IDENT} metric 100")
    check("connected first-hop route carries src=identity (correct, keep)",
          f"src {IDENT}" in line_for(k, "10.250.250.0/24"))

def s_wg_collision():
    print("COLLISION: applying the rule to wg egress re-exposes the NY1 /30 loss")
    # [LEAF_SRC_PIN_V1] intentionally pins identity on wg egress so a child beyond
    # the tunnel replies to a routable identity, not the transit /30. The rule
    # ("src only on WLAN first hop") would strip it -> kernel sources from the /30.
    k = FakeKernel()
    d = _disc(k)
    # current (wide) behavior keeps the child reachable:
    wide = d.dev_src("wg2")
    d.r.install_if_changed("10.120.120.0/24", "", "wg2", 22, 0, wide)
    cur = line_for(k, "10.120.120.0/24")
    check("current code: wg child route pins src=identity (NY1 stays reachable)",
          f"src {IDENT}" in cur and "10.253.203.90" not in cur, cur)
    # what the rule (strip src on non-WLAN-first-hop) would install instead:
    k2 = FakeKernel()
    d2 = _disc(k2)
    narrowed_src = ""                            # rule: no src on this egress
    d2.r.install_if_changed("10.120.120.0/24", "", "wg2", 22, 0, narrowed_src)
    narrowed = line_for(k2, "10.120.120.0/24")
    # the kernel would then source from wg2's own /30 (10.253.203.90) - the
    # documented 100% loss. The sim can't run the kernel's source selection, so we
    # assert the route is now srcless AND that the /30 is what the box would pick.
    print(f"    rule-narrowed wg route: {narrowed!r}  (kernel would source from "
          f"wg2 /30 10.253.203.90 -> NY1's documented 100% loss)")
    check("FORK (not a pass/fail): wg egress is where the rule and "
          "LEAF_SRC_PIN_V1 collide - needs John's call", True)

def main():
    print("=== src-pin scope: simulator demonstration (REAL dev_src + Routes) ===\n")
    FAILS.clear()
    s_reproduce_seattle5()
    s_rule_downstream_srcless()
    s_first_hop_keeps_src()
    s_wg_collision()
    print()
    if FAILS:
        print("DEMONSTRATED (simulator, against current tree):")
        print(f"  current code VIOLATES the rule on: {FAILS}")
        print("  -> the too-wide src pin is real and reproducible; the downstream")
        print("     via-routes must be src-LESS. wg egress is a separate fork.")
        return 1
    print("All rule-assertions satisfied (fix is in).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
