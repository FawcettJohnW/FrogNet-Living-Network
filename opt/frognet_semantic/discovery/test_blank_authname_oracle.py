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
"""[BLANK_AUTH_NAME_GUARD_V1] - replays the sea5 2026-06-21 12:38 livelock.

Real log, every pass:
  HOSTS_NAME_CHANGE ip=10.130.130.1 old=Seattle3 new= reason=authoritative_supersedes_deprecated_name
  HOSTS_NAME_CHANGE ip=10.120.120.1 old=Seattle2 new=
  ...
  HOSTS_KEEP path=/etc/hosts reason=unchanged          <- /etc/hosts NEVER actually changed
  converge_decision ... runAgain=1 ... name_changed=1   <- but re-run fired anyway, forever

An echo handed back a BLANK authoritative name for an IP that already has a real
name. reconcile_added recorded the blank as authoritative; its empty domain differs
from the real prior name, so host_name_changed flipped True -> runAgain. The written
line, though, used `auth_name.get() or vouch_name.get()`, and blank is falsy, so the
real name was written and /etc/hosts came out byte-identical. A re-run on a name that
never changed.

Asserts, driving the REAL reconcile_added:
  - a blank authoritative name does NOT set host_name_changed (the loop driver), and
  - the written line still carries the real name.
FAILS on the upload (no blank guard -> host_name_changed=True). PASSES on the fix.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.hosts import reconcile_added, derive_domain

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

# Prior /etc/hosts had the real names (parsed into prior_names {ip: domain}).
prior = {"10.130.130.1": derive_domain("Seattle3"),
         "10.120.120.1": derive_domain("Seattle2")}

# This pass: a BLANK authoritative echo for each .1, plus a relayed vouch that still
# carries the real name (exactly the sea5 shape: blank auth + real vouch present).
added = [
    ("",         "10.130.130.1", "wlan0", True),   # blank authoritative  <- the poison
    ("Seattle3", "10.130.130.1", "wlan0", False),  # relayed vouch, real name
    ("",         "10.120.120.1", "wlan0", True),
    ("Seattle2", "10.120.120.1", "wlan0", False),
]

lines, host_name_changed = reconcile_added(added, prior_names=prior)

check(host_name_changed is False,
      "blank authoritative name does NOT flag host_name_changed (no false runAgain)")
joined = "\n".join(lines)
check("Seattle3" in joined and "Seattle2" in joined,
      "written lines still carry the real names (Seattle3 / Seattle2)")
check(all(l.strip() for l in lines),
      "no blank host line emitted")

# A REAL rename must still be detected (don't over-suppress).
added2 = [("Seattle3Renamed", "10.130.130.1", "wlan0", True)]
_, changed2 = reconcile_added(added2, prior_names=prior)
check(changed2 is True, "a genuine authoritative rename still flags host_name_changed")

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
