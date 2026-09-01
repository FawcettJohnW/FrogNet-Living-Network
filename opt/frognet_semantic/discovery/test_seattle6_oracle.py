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
test_seattle6_oracle.py - Seattle6 real-hardware scenarios as regression-suite
members. Grounded against the runMerge logs (doc-5 wlan0 linkdown, doc-3 wlan0
up). Each scenario captures a COMMON BREAKAGE and the expected behavior/recovery:

  A. GOOD PATH      - clean LAN-relay discovery; 10.x table == box ip r.
  B. BREAKAGE       - identity interface (wlan0, src 10.160.160.1) DOWN: the
                      engine installs src-pinned exit defaults but the kernel
                      won't keep a route sourced from a dead iface, so the node
                      ends with NO working default (the doc-5 silent degrade).
  C. RECOVERY       - bring wlan0 UP: the identical src-pinned defaults are now
                      accepted. (The other recovery - merge failing LOUDLY at
                      scenario B instead of degrading silently - is a pending
                      PRODUCTION fix; the post-fix assertion is marked below.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.topology import build_seattle6, ORACLE_FINAL_S6, S6_IDENT, S6_GW
from discovery.fixdefault import FixDefaultRoute

# mirror the runMerge EXIT_INSTALL ladder: exits all via the single uplink.
EXITS = [("10.250.250.1", "wlan1", S6_GW, "FAST"),
         ("10.250.250.2", "wlan1", S6_GW, "FAST"),
         ("10.179.179.1", "wlan1", S6_GW, "FAST")]


def _discover(wlan0_linkdown):
    disc, k, seeds = build_seattle6(wlan0_linkdown=wlan0_linkdown)
    disc.descend_downstream(**seeds)
    disc.promote()
    disc.r.sweep_probe_routes()
    return disc, k


def _fixdefault(k):
    FixDefaultRoute(
        k,
        dev_ip4={"wlan0": S6_IDENT, "wlan1": "10.250.250.221"},
        up_devs={"wlan0", "wlan1"},
        connected_prefixes={"wlan0": {"10.160.160"}, "wlan1": {"10.250.250"}},
        ping=lambda via, dev: True,
        frognet_interfaces=["wlan0", "wlan1"],
        local_ips={S6_IDENT, "10.160.160.2", "10.250.250.221"},
        logger=lambda s: None,
        self_identity=S6_IDENT,
    ).install_exit_defaults(EXITS)


def main():
    ok = True

    # A. GOOD PATH ------------------------------------------------------------
    # wlan0 UP: the real Seattle6 `ip r` shows 10.160.160.0/24 dev wlan0 with NO
    # linkdown - it's a live AP. The discovered winners now source from identity
    # (10.160.160.1) so hosts beyond Seattle5 can reply (the NY1 return-path fix);
    # that src is valid because wlan0 (bearing it) is up.
    _disc, k = _discover(wlan0_linkdown=False)
    if sorted(k.table()) == sorted(ORACLE_FINAL_S6):
        print("  PASS [A good-path] 10.x table == box ip r (4 winners computed + connected)")
    else:
        ok = False
        print("  FAIL [A good-path] table != box ip r")
        for l in sorted(k.table()):
            if l not in ORACLE_FINAL_S6:
                print("    +got  " + l)
        for l in ORACLE_FINAL_S6:
            if l not in sorted(k.table()):
                print("    -want " + l)

    # B. BREAKAGE: identity iface DOWN -> src-pinned defaults rejected ---------
    _disc, kd = _discover(wlan0_linkdown=True)
    _fixdefault(kd)
    if not kd.show_default():
        print("  PASS [B breakage] identity DOWN -> kernel rejects src-pinned defaults (no default)")
    else:
        ok = False
        print(f"  FAIL [B breakage] expected no default, got: {kd.show_default()}")
    # NOTE (pending PRODUCTION fix): the desired recovery is the MERGE FAILING
    # LOUDLY here (nonzero rc) rather than degrading silently. When the loud-fail
    # gate lands in run_merge_live, add: assert merge aborted with identity-down error.

    # C. RECOVERY: bring identity iface UP -> defaults accepted ----------------
    _disc, ku = _discover(wlan0_linkdown=False)
    _fixdefault(ku)
    d = ku.show_default()
    if d and all(f"src {S6_IDENT}" in ln for ln in d):
        print(f"  PASS [C recovery] identity UP -> src-pinned defaults accepted ({len(d)})")
    else:
        ok = False
        print(f"  FAIL [C recovery] expected src-pinned defaults, got: {d}")

    print()
    print("ALL SEATTLE6 ORACLE CHECKPOINTS PASS" if ok else "SEATTLE6 ORACLE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
