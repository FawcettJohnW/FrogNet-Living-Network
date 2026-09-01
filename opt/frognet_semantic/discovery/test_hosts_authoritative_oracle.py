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
test_hosts_authoritative_oracle.py - [HOSTS_AUTHORITATIVE_NAME_V1 /
HOSTS_NAME_CHANGE_RUNAGAIN_V1]

John's 2026-06-07 FrogNetHost.FrogNetHost bug: New-York-1 (10.102.60.1) is
reached BOTH by a LAN-relayed vouch (the relay served the generic hostname
"FrogNetHost") and by a direct wg2 echo (the node's authoritative self-report
"New-York-1"). The old code added BOTH to /etc/hosts, so the IP got two names -
including the degenerate "FrogNetHost.FrogNetHost".

Rule: the authoritative echo name is the only thing that goes into /etc/hosts;
a vouch name is a fallback used only when no echo named the peer. If an
authoritative name differs from what is already in /etc/hosts, the node changed
names - the old name is deprecated and a re-run is triggered.

Drives the REAL HostStore.add_host (source tagging) and the REAL reconcile_added
+ build_etc_hosts.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sources import HostStore
from discovery.hosts import reconcile_added, build_etc_hosts

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

# Walk record: NY-1 by vouch (generic) AND by echo (authoritative); Seattle2 by
# vouch only; Seattle6 by echo (HOP). Mirrors the real add_host call order.
hs = HostStore()
hs.add_host("FrogNetHost", "10.102.60.1", "eth0", authoritative=False)  # vouch
hs.add_host("New-York-1",  "10.102.60.1", "wg2",  authoritative=True)   # echo
hs.add_host("Seattle2",    "10.120.120.1", "eth0", authoritative=False) # vouch-only
hs.add_host("Seattle6",    "10.160.160.1", "eth0", authoritative=True)  # echo

# the store actually carries the authoritative flag
check(any(t == ("New-York-1", "10.102.60.1", "wg2", True) for t in hs.added),
      "store records authoritative=True for the echo add")

lines, changed = reconcile_added(hs.added)
etc = build_etc_hosts(lines, self_ip="10.250.250.1")
blob = "\n".join(etc)
print("  /etc/hosts peer lines:")
for l in etc:
    if l.startswith("10."):
        print("   ", l)

# A: authoritative wins, the degenerate generic line is gone
check("FrogNetHost.New-York-1" in blob,
      "A New-York-1 authoritative name is in /etc/hosts")
check("FrogNetHost.FrogNetHost" not in blob,
      "A the generic vouch name FrogNetHost.FrogNetHost is GONE")
check(blob.count("10.102.60.1 ") == 1,
      "A 10.102.60.1 has exactly one name line (no duplicate)")

# B: vouch-only peer keeps its (best-available) vouch name
check("FrogNetHost.Seattle2" in blob,
      "B vouch-only peer keeps its relayed name (no echo to supersede it)")

# C: authoritative name differing from the existing /etc/hosts -> name change
_, changed_c = reconcile_added(hs.added,
                               prior_names={"10.102.60.1": "FrogNetHost"})
check(changed_c is True,
      "C authoritative New-York-1 superseding prior FrogNetHost flags name_changed")

# D: authoritative name matching the existing /etc/hosts -> NO name change
_, changed_d = reconcile_added(hs.added,
                               prior_names={"10.102.60.1": "New-York-1",
                                            "10.160.160.1": "Seattle6"})
check(changed_d is False,
      "D a settled authoritative name does NOT flag name_changed (no spurious re-run)")

# E: a vouch-only peer changing its relayed name does NOT deprecate (hearsay)
_, changed_e = reconcile_added(hs.added,
                               prior_names={"10.120.120.1": "OldSeattle2"})
check(changed_e is False,
      "E a vouch (non-authoritative) name never triggers name_changed")

print("\n[ PASS ] test_hosts_authoritative_oracle - ALL CHECKPOINTS PASS" if ok
      else "\n[ FAIL ] test_hosts_authoritative_oracle")
sys.exit(0 if ok else 1)
