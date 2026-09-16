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
[RUNAGAIN_ON_REAL_DELTA_V1] oracle.

The rule: a pass re-runs IFF the routing table is actually different at the end
of the pass than it was at the start. Writes do not vote.

Case 1 is the one that was livelocking the mesh: reap deletes a /24 and promote
re-installs the same /24 in the same pass. Both writes return rc=0,
route_table_mutated goes True, and the table is byte-identical at both ends. Old
gate: runAgain. New gate: clean.
"""
from .kernel import FakeKernel, RouteEntry
from .routes import Routes, table_fingerprint, fingerprint_delta

FAIL = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAIL.append(label)


def _k():
    return FakeKernel(entries=[
        RouteEntry(dest="10.120.120.0/24", via="", dev="wg2", metric=22, scope="link"),
        RouteEntry(dest="10.250.250.0/24", via="10.199.199.1", dev="wlan1", metric=22),
    ])


# -- 1. delete-then-reinstall in one pass is NOT a change --------------------
k = _k()
r = Routes(k, logger=lambda s: None)
before = table_fingerprint(k)
r.rtmut("route", "del", "10.120.120.0/24", caller="reap", flag_mutation=False)
r.install_if_changed("10.120.120.0/24", "", "wg2", 22, 0)
after = table_fingerprint(k)
ck("1a writes happened (route_table_mutated is True)", r.route_table_mutated is True)
ck("1b table is identical at both ends -> clean", before == after)
ck("1c no delta to report", fingerprint_delta(before, after) == ([], []))

# -- 2. a winner that really moves IS a change ------------------------------
k = _k()
r = Routes(k, logger=lambda s: None)
before = table_fingerprint(k)
r.install_if_changed("10.120.120.0/24", "", "wg3", 22, 0)   # dev wg2 -> wg3
after = table_fingerprint(k)
added, removed = fingerprint_delta(before, after)
ck("2a table differs -> runAgain", before != after)
ck("2b delta names the dest that moved",
   any("10.120.120.0/24" in x for x in added + removed))

# -- 3. replace with identical spec: KEEP, no write, no change --------------
k = _k()
r = Routes(k, logger=lambda s: None)
before = table_fingerprint(k)
r.install_if_changed("10.120.120.0/24", "", "wg2", 22, 0)
after = table_fingerprint(k)
ck("3a already-correct route is a KEEP (no mutation flagged)",
   r.route_table_mutated is False)
ck("3b table unchanged -> clean", before == after)

# -- 4. scratch /32s never participate ---------------------------------------
k = _k()
r = Routes(k, logger=lambda s: None)
before = table_fingerprint(k)
r.install_if_changed("10.120.120.2/32", "", "wg2", 6, 0)    # PROBE_METRIC
r.install_if_changed("10.250.250.2/32", "10.199.199.1", "wlan1", 5, 0)  # ALIAS
after = table_fingerprint(k)
ck("4a probe/alias host routes are outside the fingerprint -> clean",
   before == after)

# -- 5. a route that only disappears IS a change -----------------------------
k = _k()
r = Routes(k, logger=lambda s: None)
before = table_fingerprint(k)
r.rtmut("route", "del", "10.120.120.0/24", caller="reap", flag_mutation=False)
after = table_fingerprint(k)
added, removed = fingerprint_delta(before, after)
ck("5a a genuine removal is a change even when reap did not flag it",
   before != after and removed and not added)

# -- 6. fallback metrics never participate [FALLBACK_NOT_CONVERGENCE_V1] ----
k = _k()
r = Routes(k, logger=lambda s: None)
before = table_fingerprint(k)
r.install_if_changed("10.123.123.0/24", "", "wg2", 101, 0)   # secondary path
r.install_if_changed("10.155.155.0/24", "", "wg2", 101, 0)
after = table_fingerprint(k)
ck("6a a jittery secondary tunnel (metric >= 100) is not a change",
   before == after)
ck("6b the fallback route is still actually installed",
   any("metric 101" in ln for ln in k.table()))

# -- 7. [WRITE_ONLY_REAL_CHANGES_V1] no write when nothing would change -----
k = _k()
calls = []
_real = k.route
k.route = lambda *a: (calls.append(" ".join(a)), _real(*a))[1]
r = Routes(k, logger=lambda s: None)
r.probe_install("10.120.120.2", "wg2", "")            # new -> writes
n_after_install = len(calls)
r.probe_install("10.120.120.2", "wg2", "")            # identical -> must not
ck("7a an identical probe install issues no kernel write",
   len(calls) == n_after_install)
r.probe_install("10.120.120.2", "wg3", "")            # different dev -> writes
ck("7b a probe install onto a different dev does write",
   len(calls) == n_after_install + 1)
r.probe_delete("10.120.120.2")                        # present -> writes
n = len(calls)
r.probe_delete("10.120.120.2")                        # absent -> must not
ck("7c deleting an already-absent route issues no kernel write", len(calls) == n)
r.install_if_changed(STEADY_DEST := "10.250.250.0/24", "10.199.199.1", "wlan1", 22, 0)
n = len(calls)
r.rtmut("route", "replace", "10.250.250.0/24", "via", "10.199.199.1",
        "dev", "wlan1", "metric", "22", caller="test")
ck("7d a bare rtmut replace of an identical route issues no kernel write",
   len(calls) == n)

print()
print("FAILURES:", FAIL if FAIL else "none")
raise SystemExit(1 if FAIL else 0)
