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
test_verify_route_oracle.py - [VERIFY_ROUTE_V1] regression.

The bash original called verifyRoute after FINALIZING each route change (a targeted
ping). The port had dropped it - it validated the pre-promote .2/metric-6 probe but
never re-checked the finalized .1/metric-22 winner. This proves promote() now sends a
targeted alive to the dest's .1 over the route it just installed, and:

  A. a winner whose finalized route does NOT carry (alive fails) is BACKED OUT and the
     next candidate (different dev) is promoted instead - the route that wins is one
     that actually verified.
  B. a winner that verifies is kept (VERIFY_OK).
  C. with no verify edge (verify=None) behaviour is unchanged (covered by the other
     oracles, which all run verify=None).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               FakeVerify, HostStore)

DEST = "10.50.50.0/24"


def _promote_with(dead):
    log = []
    k = FakeKernel()
    routes = Routes(k, logger=log.append, clock=lambda: 0.0)
    disc = Discovery(routes, FakeEcho(), FakeRtt(), FakeGetHosts(), FakeBroker(),
                     HostStore(), local_ips=set(), dev_src_map={},
                     logger=log.append, verify=FakeVerify(dead=set(dead)))
    # two proven candidates for the same dest on different tunnels: wg0 nearer
    # (rtt 50) but its finalized route does not carry; wg1 (rtt 90) does.
    disc.CAND[DEST] = ["50||wg0|0||tunnel|HostA", "90||wg1|0||tunnel|HostA"]
    disc.promote()
    return k, log


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + msg)
        ok = ok and cond

    # [ALIVE_NONDESTRUCTIVE_V1] wg0 is the nearest winner. Its :9009 alive does
    # NOT answer (dead), but the route is KEPT - verify is advisory, never a
    # backout. The lowest-rtt candidate still wins; a dead alive logs SUSPECT.
    k, log = _promote_with(dead={"wg0"})
    winner = " ".join((k.route_show(DEST) or "").replace(";", " ").split())
    logtxt = "\n".join(log)
    print(f"    winner route: {winner}")
    print()

    check("dev wg0" in winner,
          "A nearest winner (wg0) is KEPT even though its :9009 alive is dead")
    check("VERIFY_SUSPECT" in logtxt and "dev=wg0" in logtxt,
          "A VERIFY_SUSPECT logged for wg0 (advisory) - route NOT backed out")
    check("verify_backout" not in logtxt and "VERIFY_FAIL" not in logtxt,
          "A no backout/delete happened - the table is never emptied by a probe")

    # control: nothing dead -> wg0 verifies clean and is kept
    k2, log2 = _promote_with(dead=set())
    winner2 = " ".join((k2.route_show(DEST) or "").replace(";", " ").split())
    check("dev wg0" in winner2 and "VERIFY_OK" in "\n".join(log2),
          "B clean verify keeps the nearest winner (wg0) and logs VERIFY_OK")

    print()
    print("ALL VERIFY-ROUTE ORACLE CHECKPOINTS PASS" if ok else "VERIFY-ROUTE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
