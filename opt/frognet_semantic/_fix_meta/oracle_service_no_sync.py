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
"""[SERVICE_HOSTS_NO_SYNC_V1] Oracle - a change confined to <role>.frognet SERVICE
lines must NOT count as a sync trigger; a node-identity/topology line change MUST.

The old _commit_hosts keyed sync_required on `changed` = ANY /etc/hosts write, and
/etc/hosts carries the service lines -> a mediahost/databasehost flip fired sync ->
propagate -> merge storm. The new code keys the host-table trigger on
_topology_changed(), which strips `.frognet` lines first.

Proof: the PATCHED _topology_changed is extracted from live.py and run directly.
OLD semantics modeled as raw (old != new), which is exactly what `changed` captured.
"""
import os, re

HERE = os.path.dirname(os.path.abspath(__file__))
def _find(rel):
    for base in (os.path.join(HERE, ".."), os.path.join(HERE, "..", "opt", "frognet_semantic"),
                 HERE, os.path.join(HERE, "opt", "frognet_semantic")):
        p = os.path.join(base, rel)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(rel)
src = open(_find("discovery/live.py")).read()
m = re.search(r"\ndef _topology_changed\(.*?\n(?=\ndef _commit_hosts)", src, re.S)
assert m, "could not extract _topology_changed from patched live.py"
ns = {}
exec(m.group(0), ns)
topo = ns["_topology_changed"]

raw = lambda a, b: a != b   # old `changed` semantics: any byte difference

# /etc/hosts BEFORE: 3 node lines + an elected mediahost service line
BEFORE = ("10.102.60.1 New-York-1\n"
          "10.250.250.1 Seattle5\n"
          "10.28.28.1 Seattle2\n"
          "10.160.160.1 mediahost.frognet\n"
          "10.250.250.1 databasehost_control.frognet\n")

# Case A: mediahost flips 10.160.160.1 -> 10.102.60.1 (SERVICE line only)
SERVICE_FLIP = BEFORE.replace("10.160.160.1 mediahost.frognet",
                              "10.102.60.1 mediahost.frognet")
# Case B: a new node appears (TOPOLOGY line added)
NODE_ADDED = BEFORE.replace("10.28.28.1 Seattle2\n",
                            "10.28.28.1 Seattle2\n10.111.11.1 BABox\n")

a_old, a_new = raw(BEFORE, SERVICE_FLIP), topo(BEFORE, SERVICE_FLIP)
b_old, b_new = raw(BEFORE, NODE_ADDED),  topo(BEFORE, NODE_ADDED)

print(f"service-flip:  old(changed)={a_old}  new(topo)={a_new}")
print(f"node-added:    old(changed)={b_old}  new(topo)={b_new}")

# FAIL on old: service flip fired sync (True). PASS on new: it does not (False).
assert a_old is True,  "old code should have fired sync on a service-only change"
assert a_new is False, "FAIL(new): service-only change still trips sync_required"
# Real topology change must STILL fire under the new code.
assert b_new is True,  "FAIL(new): a node-identity change must still trip sync_required"
print("PASS(new): service-line change -> no sync; node-identity change -> sync")
