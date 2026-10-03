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
sim/loopcheck.py - exercise the reflect-probe loop detector + proposed dup check.

LOOP DETECTION here mirrors the SHIPPED proxy handler
(proxy/proxy_main.py FrogNetReflectHandler, [REFLECT_PROBE_V1]) on its own
reflect port: /reflect?o=<origin_ip>&c=<counter>, where each transited box runs

    target is one of my IPs        -> REFLECT_OK   (200, reached destination)
    o is one of my IPs and c > 0   -> REFLECT_LOOP (508, our probe reflected back)
    c >= MAX_HOPS                   -> REFLECT_MAXHOPS (508, runaway-loop net)
    else                           -> c++ , forward toward target

The SHIPPED probe carries only o (origin IP) and c (counter) - NO GUID.

DUPLICATE-IP DETECTION below is a PROPOSED EXTENSION, NOT in REFLECT_PROBE_V1:
it adds the install-time GUID (/etc/fnid) to the probe so a reflecting box can
tell "my own probe returned" (guid==mine -> unique) from "another box holds my
IP" (guid!=mine -> 409). With the shipped o/c scheme alone, both return 508 and
are indistinguishable - which is exactly why the extension is needed.

Run:  python3 -m discovery.sim.loopcheck
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from discovery.sim.system import System, TopologySpec, NodeSpec
from discovery.sim.fabric import route_egress

FAILS: list[str] = []


def check(label, problems):
    if problems:
        FAILS.extend(problems)
        print(f"  [FAIL] {label}")
        for p in problems:
            print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")


def loop_scenario():
    """Seattle6 shape: O has the tunnels; L is a LAN child of O (its default
    route is O); Rmt is a remote tunnel peer. Rmt only reaches L's /24 by
    routing back through O - so a probe for L sent out the tunnel hairpins home,
    while the LAN-direct probe terminates at L."""
    spec = TopologySpec(
        nodes=[
            NodeSpec("O",   "10.250.250"),
            NodeSpec("L",   "10.160.160", guest_on=("O", "10.250.250.221")),
            NodeSpec("Rmt", "10.102.60"),
        ],
        tunnels=[("O", "Rmt")],
    )
    s = System(spec)
    s.converge()
    probs = []

    # Precondition: real discovery gave Rmt a route to L's /24 that points back
    # at O (the bend). If this isn't true the loop test proves nothing.
    eg = route_egress(s.routes["Rmt"], "10.160.160.1")
    dev_peer = {d: p for p, d in s._wg_devs("Rmt").items()}
    if not (eg and dev_peer.get(eg[0]) == "O"):
        probs.append(f"setup: Rmt's route to L does not bend back through O (eg={eg})")

    # 1. Probe for L sent OUT THE TUNNEL (via Rmt) must be caught as a loop.
    out = s.loop_probe("O", "10.160.160.1", via="Rmt")
    if out != "LOOP":
        probs.append(f"tunnel path to L should be LOOP, got {out}")

    # 2. Probe for L sent LAN-DIRECT (via L itself) must terminate at L.
    out = s.loop_probe("O", "10.160.160.1", via="L")
    if out != "DELIVERED":
        probs.append(f"LAN-direct path to L should be DELIVERED, got {out}")

    check("[SHIPPED] loop probe: tunnel path to a LAN child hairpins home (LOOP); "
          "LAN-direct terminates (DELIVERED)", probs)


def valid_long_path_scenario():
    """Multi-exit / long-but-valid: a probe that leaves and reaches a genuinely
    remote target several hops away must be DELIVERED, not rejected for hop
    count. O -> M (tunnel) -> D (tunnel): D is two tunnels out, never the
    origin, so the probe terminates there. (Counter > 0 alone must NOT reject.)"""
    spec = TopologySpec(
        nodes=[NodeSpec("O", "10.10.10"),
               NodeSpec("M", "10.20.20"),
               NodeSpec("D", "10.40.40")],
        tunnels=[("O", "M"), ("M", "D")],
    )
    s = System(spec)
    s.converge()
    probs = []
    out = s.loop_probe("O", "10.40.40.1", via="M")
    if out != "DELIVERED":
        probs.append(f"two-hop remote target should be DELIVERED, got {out}")
    check("[SHIPPED] valid long path: leaves and terminates at a distant target "
          "(DELIVERED, not rejected for hop count)", probs)


def _seed_routes(s, name, *lines):
    s.routes[name] = list(lines)


def dup_collision_scenario():
    """Startup dup check, address TAKEN: X boots with 10.50.50; Y already holds
    10.50.50 with a DIFFERENT GUID; both tunnel to relay R, whose route for
    10.50.50 points at Y. X's outward probe reaches Y -> origin==Y's 10. but
    GUID!=Y -> 409 collision."""
    spec = TopologySpec(
        nodes=[NodeSpec("X", "10.50.50", guid="GX"),
               NodeSpec("Y", "10.50.50", guid="GY"),
               NodeSpec("R", "10.70.70")],
        tunnels=[("X", "R"), ("Y", "R")],
    )
    s = System(spec)
    # duplicate subnets won't converge; hand-build the relevant tables.
    # R: sorted peers [X, Y] -> wg0=X, wg1=Y. Route 10.50.50 toward Y.
    _seed_routes(s, "R", "10.50.50.0/24 dev wg1 metric 22")
    _seed_routes(s, "X", "10.50.50.0/24 dev eth0 metric 22")
    _seed_routes(s, "Y", "10.50.50.0/24 dev eth0 metric 22")
    probs = []
    out = s.dup_probe("X")
    if out != "COLLISION":
        probs.append(f"duplicate 10.50.50 (diff GUID) should be COLLISION, got {out}")
    check("[PROPOSED GUID EXT] dup probe: another box holds my 10. (diff GUID) -> COLLISION (409)", probs)


def dup_unique_scenario():
    """Startup dup check, address FREE: only X holds 10.50.50; relay R's only
    route for it goes back to X. X's probe returns to its own GUID -> SELF, no
    collision -> UNIQUE (safe to claim)."""
    spec = TopologySpec(
        nodes=[NodeSpec("X", "10.50.50", guid="GX"),
               NodeSpec("R", "10.70.70")],
        tunnels=[("X", "R")],
    )
    s = System(spec)
    _seed_routes(s, "R", "10.50.50.0/24 dev wg0 metric 22")   # wg0 = peer X
    _seed_routes(s, "X", "10.50.50.0/24 dev eth0 metric 22")
    probs = []
    out = s.dup_probe("X")
    if out != "UNIQUE":
        probs.append(f"sole holder should be UNIQUE, got {out}")

    # And the dead-end variant: relay has no route to the IP at all -> UNIQUE.
    s2 = System(spec)
    _seed_routes(s2, "R")                                     # R knows nothing
    _seed_routes(s2, "X", "10.50.50.0/24 dev eth0 metric 22")
    if s2.dup_probe("X") != "UNIQUE":
        probs.append("dead-end (no route) should be UNIQUE")
    check("[PROPOSED GUID EXT] dup probe: sole holder / no claimant -> UNIQUE (free to claim)", probs)


def emitter_wiring_scenario():
    """The EMITTER side: discovery.walk must fire the reflect probe for a proven
    candidate and reject it on an explicit LOOP verdict (keep it otherwise).
    Drives a single tunnel walk with a fake reflect backend."""
    from discovery.kernel import FakeKernel
    from discovery.routes import Routes
    from discovery.sources import HostStore
    from discovery.discovery import Discovery

    class FakeEcho:
        def echo_probe(self, pip): return "T,10.200.200.1,0.0.0.0,0.0.0.0"
    class FakeRtt:
        def measure_rtt(self, dev, pip): return "5"
    class FakeGetHosts:
        def get_hosts(self, host_path, pip): return []
    class FakeBroker:
        def broker_for_peer_ip(self, dest1): return None
        def channel_for_iface(self, dev): return ""
    class FakeVerify:
        def __init__(self, verdict): self.verdict = verdict; self.calls = []
        def measure_or_loop(self, target, dev):
            self.calls.append(target)
            return "LOOP" if self.verdict == "LOOP" else 5.0
        def alive(self, dest1, dev): return True

    def run_walk(verdict):
        k = FakeKernel()
        routes = Routes(k, logger=lambda s: None, clock=lambda: 0.0)
        refl = FakeVerify(verdict)
        disc = Discovery(routes, FakeEcho(), FakeRtt(), FakeGetHosts(), FakeBroker(),
                         HostStore(), {"10.99.99.1", "127.0.0.1"}, {"wg0": "10.99.99.1"},
                         logger=lambda s: None, verify=refl)
        disc.walk("wg0", "10.200.200.1", 1)
        return disc, refl

    probs = []
    d_loop, r_loop = run_walk("LOOP")
    if not r_loop.calls:
        probs.append(":9009 loop check was not called during walk")
    if d_loop.CAND:
        probs.append(f"LOOP verdict must drop the candidate, but CAND={dict(d_loop.CAND)}")
    d_ok, _ = run_walk("OK")
    if not d_ok.CAND:
        probs.append("OK verdict must keep the candidate, but CAND is empty")
    check("[EMITTER] discovery.walk fires the :9009 loop check; drops on LOOP, keeps on OK", probs)


def main():
    print("=== ADMIN-CHANNEL LOOP + DUPLICATE-IP PROBE ===")
    loop_scenario()
    valid_long_path_scenario()
    dup_collision_scenario()
    dup_unique_scenario()
    emitter_wiring_scenario()
    print("\n" + ("ALL LOOP/DUP PROBE CHECKS PASS" if not FAILS
                  else f"LOOP/DUP PROBE CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
