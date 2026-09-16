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
"""frogsim_merge_storm - [RUNAGAIN_ON_REAL_DELTA_V1] proof on the real engine.

Drives ONE node's real merge against ONE persistent kernel, repeatedly.

runAgain is NOT tested here, because the merge no longer has an opinion about
it: runMerge.bash clears the sentinel at the top of a pass, and the pass re-runs
iff something EXTERNAL set it while the pass was in flight. That contract is
independent of anything the merge itself did, and frogsim_runagain_external
covers it.

What IS tested is the propagate gate, which is where the merge's own verdict
still matters:

  OLD  chain_dirty = route_table_mutated  (a committed /24 write returned rc=0)
  NEW  chain_dirty = table_fingerprint(before) != table_fingerprint(after)

A dirty chain ends in propogateNotification, which pokes every neighbour's merge
accumulator. So a false positive here is not a local cost -- it is a merge this
node asks the rest of the mesh to run.

Two worlds:

  A. SETTLED. The mesh is converged and nothing moves. reap deletes a non-winner
     /24 and promote re-installs it inside the same pass -- the delete-then-
     reinstall the code already warns about in REAP_NOT_CONVERGENCE_V1. Writes
     return rc=0, so OLD announces to the neighbours forever. The table is
     identical at both ends of every pass, so NEW announces nothing.

  B. A PEER IS ACTUALLY FLAPPING. A neighbour's tunnel daemon restarts on a
     cycle, so its /24 really does leave and re-enter this node's table. Both
     gates announce, and they must: the table genuinely changed. NEW must not be
     so quiet that it stops noticing real movement -- that is the failure mode
     of a fix like this, and it is what world B exists to catch.

The point of the change is the gap between A and B, not the absolute counts.
"""
import os
import sys

sys.path.insert(0, os.environ.get(
    "FROGNET_SEMANTIC_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("FROGNET_ROUTE_ALIVE_STORE", "/tmp/frogsim_merge_storm.tsv")
os.environ.setdefault("FROGNET_OFFLINE_TUPLES", "1")

from discovery.kernel import FakeKernel, RouteEntry
from discovery.routes import Routes, table_fingerprint, fingerprint_delta

PASSES = 8

# The two subnets the MacBook watched enter and leave its table all hour.
FLAPPER = "10.120.120.0/24"
STEADY = "10.130.130.0/24"
LOCAL = "10.199.199.0/24"


def _kernel():
    return FakeKernel(entries=[
        RouteEntry(dest=LOCAL, dev="eth0", metric=22, scope="link"),
        RouteEntry(dest=STEADY, via="10.199.199.1", dev="wlan1", metric=22),
        RouteEntry(dest=FLAPPER, dev="wg2", metric=22, scope="link"),
    ])


def _settled_pass(r):
    """One merge pass on a converged node. reap drops a /24 that did not verify
    this pass; promote puts the same one back. Nothing else moves."""
    r.rtmut("route", "del", FLAPPER, caller="reap_unverified_winners",
            flag_mutation=False)
    r.install_if_changed(FLAPPER, "", "wg2", 22, 0)
    # the walk's scratch, installed and deleted every pass by construction
    r.install_if_changed("10.130.130.2/32", "10.199.199.1", "wlan1", 5, 0)
    r.rtmut("route", "del", "10.130.130.2/32", caller="probe_sweep",
            flag_mutation=False)
    # re-assert the routes that did not move; each is a KEEP
    r.install_if_changed(STEADY, "10.199.199.1", "wlan1", 22, 0)
    r.install_if_changed(LOCAL, "", "eth0", 22, 0)


def _flapping_pass(r, pass_no):
    """The peer owning FLAPPER restarts its tunnel daemon on odd passes, so the
    route is genuinely absent for that pass and genuinely back on the next."""
    if pass_no % 2 == 1:
        r.rtmut("route", "del", FLAPPER, caller="reap_unverified_winners",
                flag_mutation=False)
    else:
        r.install_if_changed(FLAPPER, "", "wg2", 22, 0)
    r.install_if_changed(STEADY, "10.199.199.1", "wlan1", 22, 0)
    r.install_if_changed(LOCAL, "", "eth0", 22, 0)


def _drive(body):
    """Run PASSES merges against one persistent kernel. Returns (old, new) as
    lists of the chain_dirty verdict per pass."""
    k = _kernel()
    old, new = [], []
    for p in range(1, PASSES + 1):
        r = Routes(k, logger=lambda s: None, clock=lambda: 0.0)
        before = table_fingerprint(k)
        body(r, p) if body.__code__.co_argcount == 2 else body(r)
        after = table_fingerprint(k)
        old.append(1 if r.route_table_mutated else 0)
        new.append(1 if before != after else 0)
    return old, new


FAILS = []


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  - {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def main():
    print("=== MERGE STORM / propagate-on-real-delta (real engine) ===")

    print("\nA. settled node, reap-then-reinstall every pass")
    old, new = _drive(_settled_pass)
    print(f"     OLD announce per pass: {old}")
    print(f"     NEW announce per pass: {new}")
    check("OLD announces to the mesh on every pass of a node that never moved",
          sum(old) == PASSES, f"old={sum(old)}/{PASSES}")
    check("NEW announces on none of them",
          sum(new) == 0, f"new={sum(new)}/{PASSES}")

    print("\nB. neighbour's tunnel daemon restarting on a cycle")
    old2, new2 = _drive(_flapping_pass)
    print(f"     OLD announce per pass: {old2}")
    print(f"     NEW announce per pass: {new2}")
    check("NEW still announces when the table really moves",
          sum(new2) == PASSES, f"new={sum(new2)}/{PASSES}")
    check("NEW catches the removals OLD never flagged (reap does not set the flag)",
          sum(new2) >= sum(old2), f"old={sum(old2)} new={sum(new2)}")

    print("\nC. the delta names the route that moved")
    k = _kernel()
    r = Routes(k, logger=lambda s: None, clock=lambda: 0.0)
    before = table_fingerprint(k)
    _flapping_pass(r, 1)
    added, removed = fingerprint_delta(before, table_fingerprint(k))
    print(f"     added={added}")
    print(f"     removed={removed}")
    check("removal of the flapping /24 is reported by dest",
          any(FLAPPER in x for x in removed))
    check("nothing spurious reported", not added)

    print("\n" + ("ALL MERGE-STORM CHECKS PASS" if not FAILS
                  else f"MERGE-STORM SIM FAILED: {FAILS}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
