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
test_ny2_alive_gate_oracle.py - [ALIVE_GATE_VOUCH_V1] regression for the
10.28.28-on-eth0 black hole + oscillation folded in from John's 2026-06-07
Seattle5 runMerge.

THE OBSERVED BUG: New-York-2 (10.28.28, behind New-York-1's wg2 tunnel) echoed
on NEITHER path - the .2 probe failed over eth0 (LAN) and over wg2. With no
passing echo, every candidate was a vouch carrying the sentinel rtt=1000000:
Seattle6 vouched it down the LAN (eth0), New-York-1 vouched it over wg2.
promote()'s stable sort broke the tie on INSERTION ORDER (the eth0 walk runs
first), so the LAN vouch won and installed `via 10.250.250.221 dev eth0` - a
loop, since NY-2 is only reachable through the NY-1 tunnel. That route was then
(a) advertised as transit Seattle5 serves and (b) oscillated: the reflect gate
is inert with no route installed, so the bent vouch installs; next pass the
now-installed route loops so the reflect gate skips it and it is reaped; the
pass after, the loop is gone so it reinstalls - slash24_mutated every pass,
runAgain never converges.

THE RULE (this oracle's contract, from the RealVerify docstring): a VOUCH wins
only if the dest's .1 answers a FrogNet-Alive :9009 PONG over THAT candidate's
route. No exemption for vouches. If nothing answers, install NOTHING - honest
absence, never a fallback to a dead route ("if you can't reach it, don't add
it"). A MEASURED winner (real .2 echo) is unchanged: install and KEEP, alive
advisory (the :9009 probe false-negatives over tunnels and must not back out a
route the echo proved).

This oracle drives the REAL promote() with the exact tie-on-sentinel candidate
set and pins: (1) a dead LAN vouch does not install; (2) a live tunnel vouch
does; (3) all-dead installs nothing AND advertises no transit; (4) a measured
winner is kept even when alive fails; (5) with no verify backend the pre-gate
observe-and-install behaviour is preserved.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore, FakeVerify)

DEST = "10.28.28.0/24"
DEST1 = "10.28.28.1"


def _promote(cands, verify):
    """Build a minimal Discovery, seed a candidate set for 10.28.28, run the
    REAL promote(), and return (kernel, discovery) for inspection."""
    k = FakeKernel()
    routes = Routes(k, clock=lambda: 0.0)
    disc = Discovery(routes, FakeEcho(answers={}), FakeRtt(table={}),
                     FakeGetHosts(children={}), FakeBroker(), HostStore(),
                     local_ips={"10.250.250.1", "127.0.0.1"},
                     dev_src_map={}, self_identity="10.250.250.1", verify=verify)
    # gateway shape so TRANSIT_FROM_WINNERS would classify an eth0 winner as
    # transit (this is what advertised the bogus 10.28.28 transit in the field).
    disc.own_subnet = "10.250.250.0/24"
    disc.has_own_uplink = True
    disc.uplink_dev = ""
    disc.CAND[DEST] = list(cands)
    disc.promote()
    return k, disc


# The exact field set from the live walk: eth0 LAN vouch FIRST (wins the stable
# tie pre-gate), wg2 tunnel vouch second. rtt|via|dev|onlink|src|kind|host
LAN_VOUCH = "1000000|10.250.250.221|eth0|0||vouch|New-York-2"
WG2_VOUCH = "1000000||wg2|0|10.250.250.1|tunnel|New-York-2"
# A measured (echoed) winner for the non-destructive control.
MEAS_ETH0 = "31|10.250.250.221|eth0|0||lan|Seattle3"


def _line(k, dest):
    return " ".join(x for x in (k.route_show(dest) or "").replace(";", " ").split())


def main():
    ok = True

    def check(cond, label):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")

    # A. eth0 LAN vouch is DEAD, wg2 vouch is DEAD -> NOTHING installs, and
    #    10.28.28 is NOT advertised as transit. (The field bug: this installed
    #    the eth0 black hole and advertised it.)
    k, d = _promote([LAN_VOUCH, WG2_VOUCH],
                    FakeVerify(dead={(DEST1, "eth0"), (DEST1, "wg2")}))
    check(not _line(k, DEST),
          "A1  all vouches dead -> NO /24 installed (honest absence)")
    check(DEST not in d.computed_transit,
          "A2  all vouches dead -> 10.28.28 NOT advertised as transit")

    # B. eth0 LAN vouch DEAD, wg2 vouch ALIVE -> the LAN black hole is skipped
    #    and the route installs via wg2 (the real path toward NY-1).
    k, d = _promote([LAN_VOUCH, WG2_VOUCH], FakeVerify(dead={(DEST1, "eth0")}))
    line = _line(k, DEST)
    check("dev wg2" in line and "10.250.250.221" not in line,
          "B   eth0 vouch skipped, route installs via wg2 (not the LAN loop)")

    # C. eth0 LAN vouch ALIVE -> a vouch that actually answers is allowed to win
    #    (the legit authority-relay case is not broken by the gate).
    k, d = _promote([LAN_VOUCH], FakeVerify(dead=set()))
    check("10.250.250.221" in _line(k, DEST),
          "C   a LIVE vouch still wins (gate only rejects dead vouches)")

    # D. MEASURED winner with alive failing on its dev -> still KEPT
    #    (ALIVE_NONDESTRUCTIVE preserved for echo-proved winners).
    k, d = _promote([MEAS_ETH0], FakeVerify(dead={(DEST1, "eth0"), "eth0"}))
    check(bool(_line(k, DEST)),
          "D   measured winner KEPT even when :9009 alive fails (advisory)")

    # E. No verify backend wired -> pre-gate observe-and-install preserved
    #    (sim oracles that pass verify=None keep working).
    k, d = _promote([LAN_VOUCH, WG2_VOUCH], None)
    check(bool(_line(k, DEST)),
          "E   no verify backend -> route installs (pre-gate behaviour)")

    # F. CONVERGENCE: the field bug oscillated - slash24_mutated tripped every
    #    pass because the dead eth0 vouch installed, got reaped, reinstalled...
    #    With the gate, an all-dead vouch installs nothing, so a SECOND promote
    #    pass over the same table mutates no /24 -> route_table_mutated stays
    #    False -> runAgain does not fire -> the merge converges.
    k = FakeKernel()
    routes = Routes(k, clock=lambda: 0.0)
    disc = Discovery(routes, FakeEcho(answers={}), FakeRtt(table={}),
                     FakeGetHosts(children={}), FakeBroker(), HostStore(),
                     local_ips={"10.250.250.1", "127.0.0.1"},
                     dev_src_map={}, self_identity="10.250.250.1",
                     verify=FakeVerify(dead={(DEST1, "eth0"), (DEST1, "wg2")}))
    disc.own_subnet = "10.250.250.0/24"
    disc.has_own_uplink = True
    disc.uplink_dev = ""
    disc.CAND[DEST] = [LAN_VOUCH, WG2_VOUCH]
    disc.promote()                                  # pass 1
    routes.route_table_mutated = False              # runMerge clears between passes
    disc.CAND[DEST] = [LAN_VOUCH, WG2_VOUCH]         # walk re-derives the same vouches
    disc.promote()                                  # pass 2
    check(not routes.route_table_mutated,
          "F   second pass mutates no /24 -> converges (no oscillation)")

    print()
    print("ALL NY-2 ALIVE-GATE ORACLE CHECKPOINTS PASS" if ok
          else "NY-2 ALIVE-GATE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
