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
test_runagain_convergence_oracle.py - [RUNAGAIN_ON_MUTATION_V1]

John's rule: a merge re-runs iff it changed a /24 route; a run that changes no
/24 is the clean, converged pass. /32 probes (metric 6), /32 aliases (metric 5),
and /30 transit must NOT trip re-run, or the mesh never settles.

Drives the REAL Routes.rtmut and asserts route_table_mutated:
  - trips on a /24 add/replace/del
  - does NOT trip on /32 (probe or alias) or /30 churn
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes, FALLBACK_BASE

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

# --- /24 change trips it (not converged) ---
k = FakeKernel(); r = Routes(k, clock=lambda: 0.0)
r.rtmut("route", "replace", "10.160.160.0/24", "via", "10.250.250.221",
        "dev", "eth0", "metric", "22", caller="t")
check(r.route_table_mutated is True, "A /24 add/replace trips route_table_mutated (re-run)")

# --- /32 alias (metric 5) does NOT trip it ---
k = FakeKernel(); r = Routes(k, clock=lambda: 0.0)
r.rtmut("route", "replace", "10.160.160.2/32", "via", "10.250.250.221",
        "dev", "eth0", "metric", "5", caller="t")
check(r.route_table_mutated is False, "B /32 alias (metric 5) does NOT trip (not a /24)")

# --- /32 probe (metric 6) does NOT trip it ---
k = FakeKernel(); r = Routes(k, clock=lambda: 0.0)
r.rtmut("route", "replace", "10.160.160.2/32", "dev", "wg2", "metric", "6", caller="t")
check(r.route_table_mutated is False, "C /32 probe (metric 6) does NOT trip")

# --- /30 transit does NOT trip it ---
k = FakeKernel(); r = Routes(k, clock=lambda: 0.0)
r.rtmut("route", "replace", "10.253.203.88/30", "dev", "wg2", caller="t")
check(r.route_table_mutated is False, "D /30 transit does NOT trip")

# --- /24 del trips it (must exist first so del rc=0) ---
k = FakeKernel(); r = Routes(k, clock=lambda: 0.0)
r.rtmut("route", "replace", "10.120.120.0/24", "via", "10.250.250.221",
        "dev", "eth0", "metric", "22", caller="t")
r.route_table_mutated = False          # reset; test the del in isolation
r.rtmut("route", "del", "10.120.120.0/24", caller="t")
check(r.route_table_mutated is True, "E /24 del trips it")

# --- [FALLBACK_NOT_CONVERGENCE_V1] a /24 FALLBACK reshuffle (metric 100/101) does
# NOT trip it: NY-2's secondary tunnel swapping wg0/wg1/wg2 under RTT noise while the
# winner (metric 22) is settled must not latch runAgain. ---
k = FakeKernel(); r = Routes(k, clock=lambda: 0.0)
r.rtmut("route", "replace", "10.28.28.0/24", "dev", "wg0",
        "metric", str(FALLBACK_BASE), caller="t")           # fallback rank1
r.rtmut("route", "replace", "10.28.28.0/24", "dev", "wg2",
        "metric", str(FALLBACK_BASE + 1), caller="t")        # fallback rank2
check(r.route_table_mutated is False,
      "E2 /24 fallback reshuffle (metric 100/101) does NOT trip (winner-only gates)")

# --- a winner replace still trips even with fallbacks churning around it ---
k = FakeKernel(); r = Routes(k, clock=lambda: 0.0)
r.rtmut("route", "replace", "10.28.28.0/24", "dev", "wg1",
        "metric", "22", caller="t")
check(r.route_table_mutated is True,
      "E3 /24 WINNER (metric 22) still trips it")

# --- a pass with ONLY /32+/30 churn stays clean (converged) ---
k = FakeKernel(); r = Routes(k, clock=lambda: 0.0)
r.rtmut("route", "replace", "10.28.28.2/32", "via", "10.102.60.230",
        "dev", "eth0", "metric", "5", caller="t")
r.rtmut("route", "del", "10.28.28.2/32", "metric", "6", caller="t")
r.rtmut("route", "replace", "10.253.203.88/30", "dev", "wg2", caller="t")
check(r.route_table_mutated is False,
      "F a pass touching only /32s and /30s is CLEAN (no re-run)")

# --- [REAP_NOT_CONVERGENCE_V1] a reap-only pass is CONVERGED ---
# Seattle5 2026-06-07: a dead /24 (BABox 10.111.11.0/24) re-seeded every cycle
# by the dead-tunnel bringup is reaped every pass. That reap deletes a /24, but
# it is a NON-winner by construction, so it cannot move the winner set and must
# NOT flip route_table_mutated - otherwise an already-converged node livelocks
# runAgain forever. Drive the REAL reap_unverified_winners and assert clean.
k = FakeKernel(); r = Routes(k, clock=lambda: 0.0)
k.seed("10.111.11.0/24 dev wg0 scope link metric 22",                 # dead re-seed
       "10.120.120.0/24 via 10.250.250.221 dev eth0 metric 22",       # real winner
       "10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1")  # own
r.route_table_mutated = False
r.reap_unverified_winners(verified={"10.120.120.0/24"},
                          own_subnets={"10.250.250.0/24"})
check(r.route_table_mutated is False,
      "G a reap-only pass (non-winner /24 deleted) stays CLEAN - no re-run")
check(not k.route_show("10.111.11.0/24"),
      "H the reaped dead /24 is still actually deleted")

print("\n[ PASS ] test_runagain_convergence_oracle - ALL CHECKPOINTS PASS" if ok
      else "\n[ FAIL ] test_runagain_convergence_oracle")
sys.exit(0 if ok else 1)
