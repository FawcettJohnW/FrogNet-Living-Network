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
live_prober_removal.py - regression for the SeattleFive 2026-06-05 21:34
self-inflicted disconnection.

INCIDENT (from SeattleFive journal, PID 2100 proxy + 2098 daemon):
  - 10.102.60.0/24 (New-York-1) and 10.179.179.0/24 answered the proxy's
    data-path RPCs to their .1 with ewma 0.50s, 100% success, for 3h+.
  - Every ~5 min the proxy's far-edge .2 probe to 10.102.60.2 / 10.179.179.2
    timed out at the 15s budget (reader_alive=True the whole time) - i.e. the
    gateway (.1) forwarded fine but the .2 prover never answered.
  - At 21:34:02-03 wg0/wg1/wg2 were deleted (NetworkManager logged
    activated -> unmanaged 'removed'); 10.102.60 and 10.179.179 went 0% reach
    and never recovered. 10.130.130 / 10.160.160 (reached by other paths) were
    untouched.

This harness drives the REAL frognet_route.planner with exactly that input
shape and shows that [PROBE_FAILURE_REMOVAL_V1] removes the /24 carrying the
HEALTHY .1 solely because the .2 prober failed - even though the channel is
still broker-valid (not an orphan) and the gateway was up.

The decisive committer/tunnel-daemon journal lines (reason="all_probes_failed",
"won_no_subnet", BRINGUP_PHASE teardown) run in a different process and are NOT
in the uploaded proxy/daemon journal - this is the decision-layer proof that
those lines would have fired.

Run: PYTHONPATH=<tree>/opt/frognet_semantic python3 -m simulation.live_prober_removal
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from frognet_route import planner as P
from frognet_route.observation import Observation, Failure

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  - {detail}"))
    if not ok:
        FAILS.append(name)


# --- The two peers that got disconnected, modeled as live tunnel channels ---
PEERS = [
    # (name,            subnet24,            iface, channel_name)
    ("New-York-1",     "10.102.60.0/24",   "wg1", "New-York-1-10.102.60"),
    ("Node-179",       "10.179.179.0/24",  "wg0", "Node-179-10.179.179"),
]
GW = lambda net: net.replace("0/24", "1")   # 10.102.60.0/24 -> 10.102.60.1


def build_incident_inputs():
    """Reconstruct one merge pass at the moment of the incident:
       - kernel HAS the healthy /24 route to each peer (.1 path, metric 22).
       - this pass recorded a terminal far-edge .2 Failure for each /24
         (the prover never answered).
       - this pass has NO winning observation for those /24s (the .2 probe
         was the only probe for the dest, and it failed).
       - the channels are STILL in the broker list -> not orphans.
       - a healthy peer (10.130.130, reached on a LAN/transit path) DID win,
         to prove the planner is otherwise behaving.
    """
    current_routes = []
    failures = []
    active_channels = []
    broker_channels = []

    for name, net, iface, ch in PEERS:
        current_routes.append(P.Route(dest=net, dev=iface, via=GW(net), metric=P.ROUTE_METRIC))
        failures.append(Failure(dest=net, dev=iface, via=GW(net),
                                kind="tunnel", ch_name=ch, source="far_edge_probe"))
        active_channels.append(ch)
        broker_channels.append(ch)   # broker still advertises them -> NOT orphan

    # A peer that is genuinely healthy this pass (won its /24): planner sanity.
    healthy_obs = [Observation(dest="10.130.130.0/24", host_path="10.130.130.1",
                               dev="wg2", via="10.130.130.1", rtt_ms=1,
                               kind="tunnel", ch_name="Seattle3-10.130.130")]
    current_routes.append(P.Route(dest="10.130.130.0/24", dev="wg2",
                                  via="10.130.130.1", metric=P.ROUTE_METRIC))
    active_channels.append("Seattle3-10.130.130")
    broker_channels.append("Seattle3-10.130.130")

    return dict(
        observations=healthy_obs,
        current_routes=current_routes,
        active_tunnel_channels=active_channels,
        owned_subnets=["10.250.250.0/24"],     # SeattleFive's own /24
        broker_channels=broker_channels,
        failures=failures,
    )


def main():
    print("=== SeattleFive .2-prover-failure -> /24 removal (REAL planner) ===")
    FAILS.clear()
    args = build_incident_inputs()
    plan = P.plan(args["observations"], args["current_routes"],
                  args["active_tunnel_channels"],
                  owned_subnets=args["owned_subnets"],
                  broker_channels=args["broker_channels"],
                  failures=args["failures"])

    removed_nets = {r.dest: r.reason for r in plan.removals}
    print(f"  planner removals : {removed_nets}")
    print(f"  tear_down_tunnels: {plan.tear_down_tunnels}")

    # The bug: each disconnected peer's /24 is removed despite a live, broker-
    # present channel and a healthy .1 - purely because the .2 prober failed.
    for name, net, iface, ch in PEERS:
        check(f"{name} {net} route SURVIVES prover-only failure",
              net not in removed_nets,
              f"removed reason={removed_nets.get(net)!r} "
              f"(channel {ch} still broker-valid; .1 gateway was healthy)")

    # The broker still lists the channels, so they must NOT be torn down here.
    for name, net, iface, ch in PEERS:
        check(f"{name} channel {ch} NOT torn down (broker-valid)",
              ch not in plan.tear_down_tunnels)

    # Sanity: the genuinely-healthy peer is not removed by the prover-failure
    # branch.  (A metric replace from a winning observation is normal and fine.)
    check("healthy 10.130.130.0/24 not removed as all_probes_failed",
          removed_nets.get("10.130.130.0/24") != "all_probes_failed")

    print()
    if FAILS:
        print(f"REPRO CONFIRMS BUG (expected pre-fix): {FAILS}")
        return 1
    print("PASS: prover-only failure no longer yanks a live broker-valid /24.")

    # --- legitimate-removal case must still fire ----------------------------
    # A dest whose channel genuinely went away (dropped from the broker list)
    # SHOULD still be removed on terminal failure.  This proves the guard
    # narrowed the rule to prover-only failures, not gutted it.
    print()
    print("--- legitimate removal still fires (channel gone from broker) ---")
    gone_net, gone_ch = "10.28.28.0/24", "Node-28-10.28.28"
    plan2 = P.plan(
        [],
        [P.Route(dest=gone_net, dev="wg3", via="10.28.28.1", metric=P.ROUTE_METRIC)],
        ["Node-28-10.28.28"],                      # locally still up...
        owned_subnets=["10.250.250.0/24"],
        broker_channels=["Seattle3-10.130.130"],   # ...but broker no longer lists it
        failures=[Failure(dest=gone_net, dev="wg3", via="10.28.28.1",
                          kind="tunnel", ch_name=gone_ch, source="far_edge_probe")],
    )
    removed2 = {r.dest: r.reason for r in plan2.removals}
    check(f"{gone_net} (broker-removed) IS still removed on failure",
          removed2.get(gone_net) == "all_probes_failed",
          f"removals={removed2}")

    print()
    if FAILS:
        print(f"REGRESSION FAILED: {FAILS}")
        return 1
    print("ALL PASS: prover-only failure preserved; genuine path-loss still removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
