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
test_role_election_uncoupled_oracle.py -- every registered service's platform-selection
callback runs at the end of every converged runMerge, and its line is committed.

The bug this pins: the databasehost completeness barrier gated the WHOLE election
loop. On a pond whose barrier never opened, no role's callback ran, mediahost never
reached /etc/hosts, and the Communicator dialled a name that did not resolve. Nothing
raised - the else-branch wrote databasehost and returned - so it looked like the media
election had been removed rather than skipped.

Drives the REAL commit_service_lines. The CONTROL reproduces the old inline gate from
live.py verbatim, so these cases fail on the old behaviour rather than agreeing with
themselves.

Run: python3 test_role_election_uncoupled_oracle.py
"""
import sys

sys.path.insert(0, "/opt/frognet_semantic")

from core.frognet_service_hosts import commit_service_lines

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
# what the one generic loop returns when every registered role elects
ELECTED = ["10.102.60.1 mediahost.frognet",
           "10.160.160.1 databasehost.frognet",
           "10.102.60.1 boardgame.frognet"]


def names(lines):
    return {l.split()[1] for l in lines if l.split()[1].endswith(".frognet")}


def host_of(lines, role):
    for l in lines:
        if l.endswith(" %s.frognet" % role):
            return l.split()[0]
    return None


# -- CONTROL: the old inline gate, transcribed from live.py ------------------
def old_gate(etc_hosts, role_lines, ready, ctl):
    if ready:
        names_ = {ln.split()[1] for ln in role_lines}
        out = [l for l in etc_hosts
               if not (len(l.split()) >= 2 and l.split()[1] in names_)]
        out.extend(role_lines)
        return out
    out = [l for l in etc_hosts if not l.endswith(" databasehost.frognet")]
    out.append("%s databasehost.frognet" % ctl)
    return out


# =============================================================================
print("-- barrier OPEN: every role elected and committed ----------------------")
ALL = {"databasehost", "mediahost", "boardgame"}
open_ = commit_service_lines(BLOCK, ELECTED, ALL, CTL)
ck("mediahost is committed", "mediahost.frognet" in names(open_))
ck("databasehost is committed", "databasehost.frognet" in names(open_))
ck("boardgame is committed", "boardgame.frognet" in names(open_))
ck("each carries the winner its callback named",
   host_of(open_, "mediahost") == "10.102.60.1"
   and host_of(open_, "databasehost") == "10.160.160.1")
ck("the node lines are untouched",
   all(l in open_ for l in BLOCK))


# =============================================================================
print("-- barrier SHUT: databasehost pinned, everything else still elected ----")
shut = commit_service_lines(BLOCK, ELECTED, ALL - {"databasehost"}, CTL)
ck("mediahost is STILL committed -- this is the whole point",
   "mediahost.frognet" in names(shut), names(shut))
ck("and still names its own callback's winner",
   host_of(shut, "mediahost") == "10.102.60.1", host_of(shut, "mediahost"))
ck("boardgame is still committed too",
   "boardgame.frognet" in names(shut))
ck("databasehost is pinned at the control, not at its elected winner",
   host_of(shut, "databasehost") == CTL, host_of(shut, "databasehost"))
ck("exactly one databasehost line",
   sum(1 for l in shut if l.endswith(" databasehost.frognet")) == 1)

# CONTROL -- the old gate on the same inputs
old = old_gate(BLOCK, ELECTED, False, CTL)
ck("CONTROL: the old gate dropped mediahost entirely",
   "mediahost.frognet" not in names(old), names(old))
ck("CONTROL: it dropped boardgame too -- the whole loop was skipped",
   "boardgame.frognet" not in names(old))
ck("CONTROL: and it pinned databasehost, which is why nothing looked broken",
   host_of(old, "databasehost") == CTL)
ck("the new gate keeps a role the old one lost",
   names(shut) - names(old) == {"mediahost.frognet", "boardgame.frognet"},
   names(shut) - names(old))


# =============================================================================
print("-- a committed line is replaced, never duplicated ----------------------")
was = BLOCK + ["10.9.9.1 mediahost.frognet", "10.9.9.1 databasehost.frognet"]
again = commit_service_lines(was, ELECTED, ALL, CTL)
ck("the stale mediahost line is gone",
   host_of(again, "mediahost") == "10.102.60.1")
ck("one line per role after a re-elect",
   all(sum(1 for l in again if l.endswith(" %s.frognet" % r)) == 1
       for r in ("mediahost", "databasehost", "boardgame")))
ck("committing twice is idempotent",
   commit_service_lines(again, ELECTED, ALL, CTL) == again)


# =============================================================================
print("-- the election naming nobody is reported as nobody --------------------")
none = commit_service_lines(BLOCK, [], ALL - {"databasehost"}, CTL)
ck("no election result means no role lines invented",
   names(none) == {"databasehost.frognet"}, names(none))
ck("except the barrier pin, which is a pin and not a fabricated winner",
   host_of(none, "databasehost") == CTL)
open_none = commit_service_lines(BLOCK, [], ALL, CTL)
ck("with the barrier open and no result, nothing is written at all",
   names(open_none) == set(), names(open_none))

# NO_FLOOR still governs: absence is absence. A role whose callback named no winner
# simply has no line, and the Communicator will fail to resolve it -- loudly, at
# connect, which is the correct visible failure rather than a fabricated local host.
partial = commit_service_lines(BLOCK, ["10.160.160.1 databasehost.frognet"], ALL, CTL)
ck("a role that elected nobody gets no line, not a floor",
   "mediahost.frognet" not in names(partial))


# =============================================================================
print("-- each role waits on its OWN records, not on databasehost's ------------")
# mediahost information complete, databasehost information not: media must decide.
mh = commit_service_lines(BLOCK, ELECTED, {"mediahost", "boardgame"}, CTL)
ck("mediahost decides while databasehost is still waiting",
   host_of(mh, "mediahost") == "10.102.60.1")
ck("and databasehost is pinned meanwhile", host_of(mh, "databasehost") == CTL)

# The reverse: database information complete, media not.
db = commit_service_lines(BLOCK, ELECTED, {"databasehost"}, CTL)
ck("databasehost decides while mediahost is still waiting",
   host_of(db, "databasehost") == "10.160.160.1")
# This case used to assert that mediahost was withheld while its own barrier was
# shut. That rule is what deadlocked a live pond: with no line standing, withholding
# means the name never exists at all. A shut barrier defers a CHANGE only.
ck("mediahost with no standing line is committed provisionally, not withheld",
   host_of(db, "mediahost") == "10.102.60.1", host_of(db, "mediahost"))
ck("while a mediahost line that DOES stand is left alone by its shut barrier",
   host_of(commit_service_lines(BLOCK + ["10.7.7.7 mediahost.frognet"],
                                ELECTED, {"databasehost"}, CTL), "mediahost")
   == "10.7.7.7")

# A role whose barrier is shut keeps the line it already had -- a media host agreed
# last merge must not vanish (and take live calls with it) because a new machine is
# mid-publish.
had = BLOCK + ["10.102.60.9 mediahost.frognet"]
keep = commit_service_lines(had, ELECTED, {"databasehost"}, CTL)
ck("a shut barrier holds the previously committed line rather than dropping it",
   host_of(keep, "mediahost") == "10.102.60.9", host_of(keep, "mediahost"))
ck("and does not duplicate it",
   sum(1 for l in keep if l.endswith(" mediahost.frognet")) == 1)

ck("no barriers open at all still yields a usable coordination plane",
   host_of(commit_service_lines(BLOCK, ELECTED, set(), CTL), "databasehost") == CTL)


# =============================================================================
print("-- a shut barrier defers a CHANGE, never the FIRST assignment -----------")
# Transcribed from a live pond, 2026-08-01. Four machines in the hosts block had not
# published an admissible record in 34,000-330,000 s, so every barrier was shut and
# stayed shut. The election named mediahost=10.250.250.1 on every pass and the line
# never reached /etc/hosts, so the Communicator got NXDOMAIN for mediahost.frognet.
LIVE = ["10.250.250.1 databasehost_control.frognet",
        "10.250.250.1 databasehost.frognet"]
live_out = commit_service_lines(LIVE, ELECTED, set(), CTL)
ck("mediahost is committed even with every barrier shut",
   host_of(live_out, "mediahost") == "10.102.60.1", host_of(live_out, "mediahost"))
ck("boardgame too", host_of(live_out, "boardgame") == "10.102.60.1")
ck("databasehost is still pinned at the control", host_of(live_out, "databasehost") == CTL)
ck("no role is left without a line when the election named a winner",
   names(live_out) >= {"mediahost.frognet", "databasehost.frognet",
                       "boardgame.frognet"}, names(live_out))

# But once a line stands, a shut barrier holds it -- that is the anti-flap the
# barrier exists for, and it must survive this change.
stands = LIVE + ["10.9.9.9 mediahost.frognet"]
held_out = commit_service_lines(stands, ELECTED, set(), CTL)
ck("a standing line is NOT replaced while the barrier is shut",
   host_of(held_out, "mediahost") == "10.9.9.9", host_of(held_out, "mediahost"))
ck("and is not duplicated",
   sum(1 for l in held_out if l.endswith(" mediahost.frognet")) == 1)

# Barrier open replaces it as normal.
ck("an open barrier replaces the standing line",
   host_of(commit_service_lines(stands, ELECTED, ALL, CTL), "mediahost")
   == "10.102.60.1")

# CONTROL -- the version that shipped this morning left it absent forever.
def strict_gate(etc_hosts, role_lines, ready, ctl):
    out = list(etc_hosts)
    for ln in role_lines:
        role = ln.split()[1][:-len(".frognet")]
        if role not in ready:
            continue
        out = [l for l in out if l.split()[1] != ln.split()[1]]
        out.append(ln)
    if "databasehost" not in ready and ctl:
        out = [l for l in out if not l.endswith(" databasehost.frognet")]
        out.append(f"{ctl} databasehost.frognet")
    return out

ck("CONTROL: the strict per-role gate left mediahost absent forever",
   "mediahost.frognet" not in names(strict_gate(LIVE, ELECTED, set(), CTL)))
ck("the fix supplies the line the strict gate withheld",
   "mediahost.frognet" in names(live_out))


print()
print("=== %d passed, %d failed ===" % (_p, _f))
print("ORACLE " + ("GREEN" if _f == 0 else "RED"))
sys.exit(0 if _f == 0 else 1)
