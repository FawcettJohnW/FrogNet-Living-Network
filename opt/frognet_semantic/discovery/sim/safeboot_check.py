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
sim/safeboot_check.py - proves the boot-time databasehost neutralizer: a stale remote becomes
localhost, the name is added if absent, co-located names and other entries survive, it is
idempotent, AND the converge path (apply_to_etc_hosts) drops the localhost placeholder BY NAME
so it never wedges re-election.
"""
from __future__ import annotations
import os, sys

_HERE = os.path.abspath(__file__)
_FS = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))     # opt/frognet_semantic
_WORK = os.path.dirname(os.path.dirname(_FS))                      # work
sys.path.insert(0, os.path.join(_WORK, "usr", "local", "bin"))
sys.path.insert(0, os.path.join(_WORK, "etc", "frognet_bundles", "communicator"))

from frognet_hosts_safeboot import neutralize

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

def run():
    # 1. stale remote -> localhost; control + others untouched
    probs = []
    lines = ["127.0.0.1 localhost", "10.5.5.5 databasehost.frognet",
             "10.250.250.1 databasehost_control.frognet", "10.250.250.1 mediahost.frognet"]
    n = neutralize(lines)
    if "127.0.0.1 databasehost.frognet" not in n:
        probs.append("databasehost not pointed at localhost")
    if any("10.5.5.5" in l and "databasehost.frognet" in l for l in n):
        probs.append("stale remote databasehost survived")
    if "10.250.250.1 databasehost_control.frognet" not in n or "10.250.250.1 mediahost.frognet" not in n:
        probs.append("control/mediahost lines must be untouched")
    if "127.0.0.1 localhost" not in n:
        probs.append("unrelated localhost line clobbered")
    check("[STALE] stale remote databasehost.frognet -> localhost; control/media/others intact", probs)

    # 2. absent -> added
    probs = []
    n2 = neutralize(["127.0.0.1 localhost"])
    if "127.0.0.1 databasehost.frognet" not in n2:
        probs.append("databasehost not added when absent")
    check("[ADD] missing databasehost.frognet is added at localhost", probs)

    # 3. co-located names preserved at their IP
    probs = []
    n3 = neutralize(["10.5.5.5 databasehost.frognet kitchen.frognet"])
    if "127.0.0.1 databasehost.frognet" not in n3:
        probs.append("databasehost not neutralized when sharing a line")
    if not any(l.startswith("10.5.5.5") and "kitchen.frognet" in l for l in n3):
        probs.append("co-located name dragged off its real IP")
    check("[COLOCATE] a name sharing the line keeps its real IP", probs)

    # 4. idempotent
    probs = []
    if neutralize(n) != n:
        probs.append("second pass changed an already-neutralized file")
    check("[IDEMPOTENT] re-running safeboot is a no-op", probs)

    # 5. converge: a real databasehost winner REPLACES the localhost placeholder (dropped by
    #    name); with NO winner the placeholder correctly stays (localhost = self-fallback).
    probs = []
    try:
        import frognet_service_hosts as FSH
        placeholder = ["127.0.0.1 databasehost.frognet",
                       "10.250.250.1 databasehost_control.frognet"]
        orig = FSH.service_host_lines
        try:
            # [SIGNATURE_SYNC] The stub takes **kw rather than enumerating the
            # keywords. It was pinned to dbhost/logger/reachable_subnets/
            # self_ip/lan_subnets; service_host_lines has since grown local_ips
            # and etc_hosts, and the call blew up with "unexpected keyword
            # argument 'etc_hosts'". Nothing in this check cares what the caller
            # passes -- only what comes back -- so enumerating the parameters
            # bought nothing and guaranteed this breaks again on the next one.
            FSH.service_host_lines = lambda **kw: ["10.77.77.50 databasehost.frognet"]
            won = FSH.apply_to_etc_hosts(list(placeholder), dbhost="x")
            FSH.service_host_lines = lambda **kw: []
            none = FSH.apply_to_etc_hosts(list(placeholder), dbhost="x")
        finally:
            FSH.service_host_lines = orig
        if any(l.strip() == "127.0.0.1 databasehost.frognet" for l in won):
            probs.append("winner did not replace the localhost placeholder")
        if "10.77.77.50 databasehost.frognet" not in won:
            probs.append("elected databasehost not written")
        if "10.250.250.1 databasehost_control.frognet" not in won:
            probs.append("control line clobbered during data re-election")
        if "127.0.0.1 databasehost.frognet" not in none:
            probs.append("no-winner case dropped localhost - would leave databasehost unset")
    except Exception as e:
        probs.append(f"apply_to_etc_hosts import/run failed: {e}")
    check("[CONVERGE] winner replaces the placeholder (by name); no winner keeps localhost (self)", probs)

def main():
    print("=== boot-time databasehost neutralizer: never send to a stale host ===")
    run()
    print("\n" + ("ALL SAFEBOOT CHECKS PASS" if not FAILS
                  else f"SAFEBOOT CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
