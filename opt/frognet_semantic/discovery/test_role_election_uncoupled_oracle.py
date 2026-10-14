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
"""
test_role_election_uncoupled_oracle.py -- every registered service's platform-selection
callback runs at the end of every converged runMerge, and its line is committed.

The bug this pinned: the databasehost completeness barrier gated the WHOLE election
loop, so on a pond whose barrier never opened no role's callback ran and the role's
name never reached /etc/hosts. [ABSENT_IS_A_DECIDABLE_STATE_V1] removed the barrier;
[ONE_ROLE_V1] (T2, 2026-10-12) left one role, databasehost. What stays pinned here is
the commit rule for it: the elected winner is committed every merge whatever
`ready_roles` says, a standing line is replaced (never duplicated), the commit is
idempotent, and an election that names nobody gets the deterministic floor at the
control -- a floor, not a fabricated winner.

Drives the REAL commit_service_lines and the REAL role registry.

Run: python3 test_role_election_uncoupled_oracle.py
"""
import sys

sys.path.insert(0, "/opt/frognet_semantic")

from core.frognet_service_hosts import commit_service_lines, no_host_marker
from core.role_registry import ROLE_HANDLERS

_p = _f = 0


def ck(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, extra))


BLOCK = ["10.102.60.1 FrogNetHost", "10.160.160.1 FrogNetHost"]
CTL = "10.160.160.1"
ELECTED = ["10.102.60.1 databasehost.frognet"]      # the one generic loop's answer
ALL = set(ROLE_HANDLERS)


def names(lines):
    return {l.split()[1] for l in lines if len(l.split()) > 1 and l.split()[1].endswith(".frognet")}


def host_of(lines, role):
    for l in lines:
        if l.endswith(" %s.frognet" % role):
            return l.split()[0]
    return None


def count(lines, role):
    return sum(1 for l in lines if l.endswith(" %s.frognet" % role))


# =============================================================================
print("-- the role set --------------------------------------------------------")
ck("databasehost is the one registered role", ALL == {"databasehost"}, ALL)


# =============================================================================
print("-- the elected winner is committed, whatever ready_roles says ----------")
open_ = commit_service_lines(BLOCK, ELECTED, ALL, CTL)
ck("databasehost is committed with the winner its callback named",
   host_of(open_, "databasehost") == "10.102.60.1", host_of(open_, "databasehost"))
ck("the node lines are untouched", all(l in open_ for l in BLOCK))
ck("no other role line is invented", names(open_) == {"databasehost.frognet"}, names(open_))
for ready in (set(), {"databasehost"}, {"somethingelse"}):
    ck("ready_roles=%s is ignored: same block" % sorted(ready),
       commit_service_lines(BLOCK, ELECTED, ready, CTL) == open_)


# =============================================================================
print("-- a committed line is replaced, never duplicated ----------------------")
was = BLOCK + ["10.9.9.1 databasehost.frognet"]
again = commit_service_lines(was, ELECTED, ALL, CTL)
ck("the stale databasehost line is gone", host_of(again, "databasehost") == "10.102.60.1")
ck("one line after a re-elect", count(again, "databasehost") == 1)
ck("committing twice is idempotent", commit_service_lines(again, ELECTED, ALL, CTL) == again)
LIVE = ["10.250.250.1 databasehost_control.frognet", "10.250.250.1 databasehost.frognet"]
live_out = commit_service_lines(LIVE, ELECTED, set(), CTL)
ck("a standing line is replaced by the elected winner, no ready roles needed",
   host_of(live_out, "databasehost") == "10.102.60.1" and count(live_out, "databasehost") == 1,
   live_out)
ck("the control line is untouched", "10.250.250.1 databasehost_control.frognet" in live_out)


# =============================================================================
print("-- the election naming nobody gets the floor, not a fabricated winner ---")
none = commit_service_lines(BLOCK, [], ALL, CTL)
ck("no election result: only the deterministic databasehost floor is written",
   names(none) == {"databasehost.frognet"} and host_of(none, "databasehost") == CTL, none)
ck("no control known and no result: no line at all",
   names(commit_service_lines(BLOCK, [], ALL, None)) == set())


# =============================================================================
print("-- a no-host marker from a previous pass is dropped ---------------------")
marker = no_host_marker("databasehost")
stale = BLOCK + [marker]
cleared = commit_service_lines(stale, ELECTED, ALL, CTL)
ck("the stale marker is dropped when a winner is committed", marker not in cleared, cleared)
ck("and the winner is there", host_of(cleared, "databasehost") == "10.102.60.1")


print()
print("=== %d passed, %d failed ===" % (_p, _f))
print("ORACLE " + ("GREEN" if _f == 0 else "RED"))
sys.exit(0 if _f == 0 else 1)
