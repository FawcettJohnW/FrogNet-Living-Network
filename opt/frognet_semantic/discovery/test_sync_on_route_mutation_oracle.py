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
test_sync_on_route_mutation_oracle.py - [SYNC_ON_ROUTE_MUTATION_V1]

Folds in John's 2026-06-06 finding: sync was gated on /etc/hosts ("HOSTS_KEEP
unchanged" -> sync_required=0) while the ROUTING TABLE churned every merge. Wrong
trigger. sync_required must fire on ANY committed route add/replace/del, and must
NOT fire for transient probe /32s (metric 6) that churn every walk.

This drives the REAL Routes.rtmut and asserts the route_table_mutated flag.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes

def main():
    ok = True
    def check(cond, label):
        nonlocal ok; ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")

    # A. a committed winner install (metric 22) flags a real table change
    r = Routes(FakeKernel(), clock=lambda: 0.0)
    r.install_if_changed("10.250.250.0/24", "", "wg2", 22, 0, "10.102.60.1")
    check(r.route_table_mutated,
          "A   committed route install (metric 22) sets route_table_mutated")

    # B. a verify-backout delete of a committed route flags a change. (The real
    # flow installs the winner THEN backs it out on alive-fail, so the route
    # exists; deleting an absent route is rc!=0 and correctly not a mutation.)
    k = FakeKernel()
    k.seed("10.250.250.0/24 via 10.102.60.230 dev eth0 metric 22")
    r = Routes(k, clock=lambda: 0.0)
    r.rtmut("route", "del", "10.250.250.0/24", "metric", "22", caller="verify_backout")
    check(r.route_table_mutated,
          "B   verify-backout del of an EXISTING committed route sets the flag")

    # C. probe /32 churn (metric 6 install+del) must NOT set the flag
    r = Routes(FakeKernel(), clock=lambda: 0.0)
    r.probe_install("10.250.250.2", "wg2", "", "10.102.60.1")
    r.probe_delete("10.250.250.2")
    check(not r.route_table_mutated,
          "C   probe /32 churn (metric 6) does NOT set the flag (no sync noise)")

    # D. an unchanged route (KEEP/already_correct) does NOT set the flag
    k = FakeKernel()
    k.seed("10.179.179.0/24 dev wg1 scope link metric 22")
    r = Routes(k, clock=lambda: 0.0)
    r.install_if_changed("10.179.179.0/24", "", "wg1", 22, 0)
    check(not r.route_table_mutated,
          "D   already-correct route (KEEP) does NOT set the flag")

    print()
    print("ALL SYNC-ON-ROUTE-MUTATION ORACLE CHECKPOINTS PASS" if ok
          else "SYNC-ON-ROUTE-MUTATION PROOF FAILED")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
