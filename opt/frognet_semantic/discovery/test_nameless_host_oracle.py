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
"""[NAMELESS_HOST_SKIP_V1] - replays the sea 2026-06-25 05:59 merge crash.

Real traceback, every merge after echo went :80-only:
  reconcile_added -> addhost_lines(name, host_path) -> derive_domain(name)
  AttributeError: 'NoneType' object has no attribute 'split'

An echo-less candidate (gated alive on :9009 but no :80 echo identity) records a
BLANK authoritative name. If NO neighbor relays a vouch name for that same .1, then
`name = auth_name.get() or vouch_name.get()` is None - and addhost_lines/derive_domain
crashed the whole merge (rc=1, every pass).

Asserts, driving the REAL reconcile_added:
  - a blank-auth IP with NO vouch does NOT crash and emits NO host line, and
  - a blank-auth IP WITH a vouch still gets the vouch name (skip didn't over-reach), and
  - a normally-named IP is unaffected.
CRASHES on the upload (no skip) -> FAIL. PASSES on the fix.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.hosts import reconcile_added

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

# 10.28.28.1: blank authoritative echo, NO vouch anywhere -> the poison (name=None).
# 10.179.178.1: blank authoritative, but a relayed vouch carries the real name.
# 10.130.130.1: a normal authoritative name.
added = [
    ("",         "10.28.28.1",   "wg2",   True),    # blank auth, no vouch -> was the crash
    ("",         "10.179.178.1", "wg2",   True),    # blank auth ...
    ("BAMacBook","10.179.178.1", "wg2",   False),   # ... but vouch names it
    ("Seattle3", "10.130.130.1", "eth0",  True),    # normal
]

try:
    lines, changed = reconcile_added(added, prior_names={})
    crashed = False
except Exception as e:
    crashed = True
    print(f"  [FAIL] reconcile_added raised {type(e).__name__}: {e}")

check(not crashed, "blank-auth + no-vouch IP does NOT crash the merge")
if not crashed:
    joined = "\n".join(lines)
    check("10.28.28.1" not in joined,
          "nameless IP (10.28.28.1) emits NO host line")
    check("BAMacBook" in joined,
          "blank-auth IP WITH a vouch still gets the vouch name (skip didn't over-reach)")
    check("Seattle3" in joined,
          "a normally-named IP is unaffected")
    check(all(l.strip() and "  " not in l for l in lines),
          "no blank/garbage host line emitted")

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
