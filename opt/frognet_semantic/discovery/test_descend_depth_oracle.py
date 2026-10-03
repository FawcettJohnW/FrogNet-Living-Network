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
"""test_descend_depth_oracle — descend must crawl to a FIXPOINT, not stop at grandchildren.

The crawl used to run a fixed two levels ("children", "grandchildren"), so in a chain
deeper than root->child->grandchild the far node was never discovered in a single merge:
NY1 reaches Sea5(immediate)->Sea6(child)->Sea3(grandchild) and STOPPED, never learning
Sea2 (great-grandchild). Every node must know every other node. This drives the REAL
_crawl_to_fixpoint with a direct-children-only getHosts (worst case) and asserts the whole
chain is found; it also shows the OLD 2-level cap missing the far end.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.descend import _crawl_to_fixpoint

FAILS = []
def check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

# NY1's real branch: immediate Sea5, each node advertises ONLY its direct child.
SEA5, SEA6, SEA3, SEA2 = "10.250.250.1", "10.160.160.1", "10.130.130.1", "10.120.120.1"
CHAIN = {SEA5: [(SEA6, "Seattle6")], SEA6: [(SEA3, "Seattle3")],
         SEA3: [(SEA2, "Seattle2")], SEA2: []}

def child_fn(pip):
    return CHAIN.get(pip, [])
def echo_fn(cip):
    return ""
def skip_fn(cip, cname):
    return not (cip and cip.startswith("10."))
def quiet(*_a, **_k):
    pass

def crawl(max_levels):
    fr = [(SEA5, "wg0", "")]                 # NY1's immediate neighbour is Sea5 (tunnel)
    return _crawl_to_fixpoint(fr, child_fn, echo_fn, skip_fn, quiet, max_levels=max_levels)

def main():
    print("=== DESCEND DEPTH (fixpoint) ORACLE ===")

    # OLD behaviour: cap at grandchildren -> Sea2 is never reached
    old = crawl(max_levels=2)
    check("old 2-level cap MISSES Sea2 (the bug)", SEA2 not in old)

    # NEW behaviour: crawl to fixpoint -> the whole chain, including Sea2
    new = crawl(max_levels=64)
    check("fixpoint crawl reaches Sea6", SEA6 in new)
    check("fixpoint crawl reaches Sea3", SEA3 in new)
    check("fixpoint crawl reaches Sea2 (great-grandchild)", SEA2 in new)
    check("NY1 now knows the whole branch", {SEA6, SEA3, SEA2} <= set(new))

    # arbitrary depth: a 7-node chain is fully discovered
    deep = {f"10.{i}.{i}.1": [(f"10.{i+1}.{i+1}.1", f"N{i+1}")] for i in range(1, 7)}
    deep["10.7.7.1"] = []
    got = _crawl_to_fixpoint([("10.1.1.1", "eth0", "10.1.1.1")],
                             lambda p: deep.get(p, []), echo_fn, skip_fn, quiet)
    check("7-node chain fully discovered (arbitrary depth)",
          all(f"10.{i}.{i}.1" in got for i in range(2, 8)))

    print("\n" + ("ALL DESCEND-DEPTH CHECKS PASS" if not FAILS
                  else f"DESCEND-DEPTH ORACLE FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
