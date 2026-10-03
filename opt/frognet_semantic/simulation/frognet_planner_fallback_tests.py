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
frognet_planner_fallback_tests.py - unit tests for [FALLBACK_METRIC_V1].

The frogsim covers orchestration end-to-end but doesn't have a clean
multi-observation-per-dest scenario (a /24 with both a direct WG
observation and a recursive LAN observation), which is the case the
fallback-metric logic exists to handle.

These tests call planner.plan() directly with hand-crafted
Observation/Route lists and check that:

  1. Winner gets installed at ROUTE_METRIC.
  2. Non-winner observation gets installed at ROUTE_FALLBACK_METRIC.
  3. Existing kernel route matching the winner is not re-installed.
  4. Existing kernel route matching a fallback observation is not
     re-installed (already correct).
  5. Existing route at the wrong metric is removed (so it can be
     reinstalled at the correct one by the install loop).
  6. Stale routes (no matching observation) are removed.
  7. Stickiness ignores fallback-metric routes (only the winner-metric
     route can be a sticky candidate).
  8. Single-observation case behaves like the pre-fallback design
     (winner installed, no fallback noise).

Run with:
  FROGNET_PROXY_ROOT=/home/claude/src/opt/frognet_semantic \
  FROGNET_BIN_ROOT=/home/claude/src/usr/local/bin \
    python3 /home/claude/frogsim/frognet_planner_fallback_tests.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# planner lives in /home/claude/src/opt/frognet_semantic - discoverable
# through the frognet_sim environment used elsewhere in this session.
proxy_root = os.environ.get(
    "FROGNET_PROXY_ROOT",
    "/home/claude/src/opt/frognet_semantic")
sys.path.insert(0, proxy_root)

from frognet_route.planner import (
    plan, Observation, Route, InstallRoute, RemoveRoute,
    ROUTE_METRIC, ROUTE_FALLBACK_METRIC, ADMIN_ALIAS_METRIC,
)


FAILS: list[str] = []


def obs(dest, dev, via, rtt_ms, kind="lan", source="sync_wave1",
        ch_name=""):
    """Build an Observation. The planner only reads a fixed subset of
    fields, but keep the signature realistic."""
    return Observation(
        dest=dest, dev=dev, via=via, rtt_ms=rtt_ms,
        kind=kind, host_path=dest.replace(".0/24", ".1"),
        source=source, ch_name=ch_name,
    )


def route(dest, dev, via, metric):
    return Route(dest=dest, dev=dev, via=via, metric=metric)


def assert_(cond, where: str):
    if cond:
        print(f"  [OK  ] {where}")
    else:
        FAILS.append(where)
        print(f"  [FAIL] {where}")


def show_plan(p):
    print(f"    installs ({len(p.installs)}):")
    for i in p.installs:
        print(f"      {i.dest} dev {i.dev} via {i.via or '-'} "
              f"metric {i.metric} kind={i.kind} rtt={i.rtt_ms}")
    print(f"    removals ({len(p.removals)}):")
    for r in p.removals:
        print(f"      {r.dest} dev {r.dev} via {r.via or '-'} "
              f"metric {r.metric} reason={r.reason}")


# ---------------------------------------------------------------------------

def test_winner_plus_fallback_installed_from_clean():
    """Two observations for one dest, no existing kernel routes.
    Lower-RTT becomes winner at ROUTE_METRIC; higher-RTT becomes
    fallback at ROUTE_FALLBACK_METRIC."""
    print("\n--- both winner and fallback installed when kernel is empty ---")
    obs_list = [
        # The wg-direct path is faster: rtt=50ms.
        obs("10.178.178.0/24", "wg1", "", 50, kind="tunnel",
            source="sync_wave2"),
        # The LAN-recursive path through BABox is slower: rtt=200ms.
        obs("10.178.178.0/24", "wg0", "10.111.11.1", 200, kind="lan"),
    ]
    p = plan(obs_list, [], active_tunnel_channels=[], owned_subnets=[])
    show_plan(p)

    winner = [i for i in p.installs
              if i.dest == "10.178.178.0/24" and i.metric == ROUTE_METRIC]
    fallback = [i for i in p.installs
                if i.dest == "10.178.178.0/24"
                and i.metric == ROUTE_FALLBACK_METRIC]
    assert_(len(winner) == 1, "exactly one ROUTE_METRIC install for the /24")
    assert_(len(fallback) == 1, "exactly one ROUTE_FALLBACK_METRIC install")
    if winner:
        assert_(winner[0].dev == "wg1", "winner dev = wg1 (lower RTT)")
        assert_(winner[0].via == "", "winner via = '' (direct WG)")
    if fallback:
        assert_(fallback[0].dev == "wg0",
                "fallback dev = wg0 (the LAN-recursive path)")
        # _winning_via for wave-1 LAN returns obs.host_path (dest's .1),
        # NOT obs.via (which is the LAN underlay contact for wave-1).
        assert_(fallback[0].via == "10.178.178.1",
                "fallback via = dest's .1 (per _winning_via for wave-1 LAN)")
    assert_(not p.removals, "no removals - kernel started empty")


def test_winner_already_installed_no_redundant_install():
    """Winner already in kernel at ROUTE_METRIC. Only the fallback
    install should fire."""
    print("\n--- winner already present: only fallback installed ---")
    obs_list = [
        obs("10.178.178.0/24", "wg1", "", 50, kind="tunnel",
            source="sync_wave2"),
        obs("10.178.178.0/24", "wg0", "10.111.11.1", 200, kind="lan"),
    ]
    existing = [route("10.178.178.0/24", "wg1", "", ROUTE_METRIC)]
    p = plan(obs_list, existing, active_tunnel_channels=[], owned_subnets=[])
    show_plan(p)
    installs = [i for i in p.installs if i.dest == "10.178.178.0/24"]
    assert_(len(installs) == 1, "exactly one install (only the fallback)")
    if installs:
        assert_(installs[0].metric == ROUTE_FALLBACK_METRIC,
                "the one install is at ROUTE_FALLBACK_METRIC")
    assert_(not p.removals, "no removals - winner is already correct")


def test_both_already_installed_no_op():
    """Both winner and fallback already in kernel at correct metrics.
    No installs, no removals."""
    print("\n--- both winner and fallback already present: no-op ---")
    obs_list = [
        obs("10.178.178.0/24", "wg1", "", 50, kind="tunnel",
            source="sync_wave2"),
        obs("10.178.178.0/24", "wg0", "10.111.11.1", 200, kind="lan"),
    ]
    existing = [
        route("10.178.178.0/24", "wg1", "", ROUTE_METRIC),
        # _winning_via for wave-1 LAN obs returns obs.host_path
        # (10.178.178.1), so the kernel route already matching the
        # fallback observation has via=dest's-own-.1, not via=BABox.
        route("10.178.178.0/24", "wg0", "10.178.178.1", ROUTE_FALLBACK_METRIC),
    ]
    p = plan(obs_list, existing, active_tunnel_channels=[], owned_subnets=[])
    show_plan(p)
    installs = [i for i in p.installs if i.dest == "10.178.178.0/24"]
    removals = [r for r in p.removals if r.dest == "10.178.178.0/24"]
    assert_(not installs, "no installs - both already at correct metrics")
    assert_(not removals, "no removals - both routes are correct")


def test_swap_winner_and_fallback_when_rtt_inverts():
    """Previously wg1 was the winner at metric 22 and wg0-via-BABox
    was the fallback at metric 100. New cycle: the wg0 path is now
    faster than wg1. After the planner runs, wg1 should be DEMOTED
    (removed at 22, reinstalled at 100) and wg0 PROMOTED (removed at
    100, reinstalled at 22)."""
    print("\n--- swap winner and fallback when RTT inverts ---")
    obs_list = [
        # wg0-via-BABox is now faster: 40ms
        obs("10.178.178.0/24", "wg0", "10.111.11.1", 40, kind="lan"),
        # wg1 is now slower: 800ms (well above the 100ms + 20% threshold
        # to override sticky behavior in case it kicks in)
        obs("10.178.178.0/24", "wg1", "", 800, kind="tunnel",
            source="sync_wave2"),
    ]
    existing = [
        route("10.178.178.0/24", "wg1", "", ROUTE_METRIC),
        # _winning_via for wave-1 LAN obs returns obs.host_path
        # (=10.178.178.1), so the existing kernel row uses dest's .1
        # as via.
        route("10.178.178.0/24", "wg0", "10.178.178.1", ROUTE_FALLBACK_METRIC),
    ]
    p = plan(obs_list, existing, active_tunnel_channels=[], owned_subnets=[])
    show_plan(p)

    # New winner must be wg0 at metric 22; new fallback must be wg1
    # at metric 100.  Old kernel rows (wg1@22, wg0@100) must be
    # removed so the install loop can put them in at the right slots.
    inst_winner = [i for i in p.installs
                   if i.metric == ROUTE_METRIC
                   and i.dev == "wg0" and i.via == "10.178.178.1"]
    inst_fallback = [i for i in p.installs
                     if i.metric == ROUTE_FALLBACK_METRIC
                     and i.dev == "wg1" and i.via == ""]
    rem_old_winner = [r for r in p.removals
                      if r.dev == "wg1" and r.via == ""
                      and r.metric == ROUTE_METRIC]
    rem_old_fallback = [r for r in p.removals
                        if r.dev == "wg0" and r.via == "10.178.178.1"
                        and r.metric == ROUTE_FALLBACK_METRIC]
    assert_(len(inst_winner) == 1,
            "new winner (wg0 dev, via dest's .1) installed at ROUTE_METRIC")
    assert_(len(inst_fallback) == 1,
            "new fallback (wg1 direct) installed at ROUTE_FALLBACK_METRIC")
    assert_(len(rem_old_winner) == 1,
            "old winner (wg1 at ROUTE_METRIC) removed")
    assert_(len(rem_old_fallback) == 1,
            "old fallback (wg0 at ROUTE_FALLBACK_METRIC) removed")


def test_stale_route_with_no_observation_gets_removed():
    """Kernel has a route to a path that has NO observation this
    cycle. That path is stale and gets removed."""
    print("\n--- truly stale route (no matching observation) removed ---")
    obs_list = [
        obs("10.178.178.0/24", "wg1", "", 50, kind="tunnel",
            source="sync_wave2"),
    ]
    existing = [
        route("10.178.178.0/24", "wg1", "", ROUTE_METRIC),     # correct
        route("10.178.178.0/24", "wg9", "10.99.99.1", 22),     # stale path
    ]
    p = plan(obs_list, existing, active_tunnel_channels=[], owned_subnets=[])
    show_plan(p)
    removed_stale = [r for r in p.removals
                     if r.dev == "wg9" and r.via == "10.99.99.1"]
    assert_(len(removed_stale) == 1, "stale wg9 path removed")
    installs = [i for i in p.installs if i.dest == "10.178.178.0/24"]
    assert_(not installs, "no install: winner already at correct metric")


def test_single_observation_no_fallback():
    """One observation: winner installs, no fallback at all."""
    print("\n--- single observation: no fallback created ---")
    obs_list = [
        obs("10.130.130.0/24", "wg2", "10.250.250.1", 60, kind="tunnel",
            source="sync_wave2"),
    ]
    p = plan(obs_list, [], active_tunnel_channels=[], owned_subnets=[])
    show_plan(p)
    installs = [i for i in p.installs]
    assert_(len(installs) == 1, "exactly one install")
    if installs:
        assert_(installs[0].metric == ROUTE_METRIC,
                "single install is at ROUTE_METRIC")


def test_sticky_ignores_fallback_metric_route():
    """The sticky-route filter only considers ROUTE_METRIC routes.
    A fallback-metric route matching an observation should NOT
    prevent the winner from changing under the switch threshold."""
    print("\n--- sticky candidates restricted to ROUTE_METRIC routes ---")
    # Setup:
    #   - wg1 observation at 110ms - slightly faster
    #   - wg0 observation at 250ms - slower, but exists in kernel
    #     at FALLBACK metric (would have qualified as sticky under
    #     the unfiltered code).  Without the filter, the old code
    #     would have made wg0 sticky and the 110ms diff (140ms abs,
    #     56% rel) would have flipped to wg1 anyway, so this test
    #     would have passed even with the bug.
    #
    # To really test the filter, contrive a scenario where:
    #   - The "would be sticky if filter is broken" route is at the
    #     fallback metric
    #   - The RTT delta is below the switch threshold
    # If filter is correct: the planner ignores the fallback as a
    # sticky candidate, picks lowest RTT (wg1 110ms) cleanly.
    # If filter is broken: wg0 becomes sticky at 200ms, wg1 at 110ms,
    # delta 90ms (<100), so wg0 wins.
    obs_list = [
        obs("10.178.178.0/24", "wg1", "", 110, kind="tunnel",
            source="sync_wave2"),
        obs("10.178.178.0/24", "wg0", "10.111.11.1", 200, kind="lan"),
    ]
    existing = [
        # ONLY the fallback-metric route exists - no ROUTE_METRIC route
        route("10.178.178.0/24", "wg0", "10.111.11.1", ROUTE_FALLBACK_METRIC),
    ]
    p = plan(obs_list, existing, active_tunnel_channels=[], owned_subnets=[])
    show_plan(p)

    winners = [i for i in p.installs
               if i.dest == "10.178.178.0/24" and i.metric == ROUTE_METRIC]
    assert_(len(winners) == 1, "exactly one winner installed")
    if winners:
        # The bug would have made wg0 the sticky winner.  Correct
        # behavior is wg1 (lower RTT) wins clean.
        assert_(winners[0].dev == "wg1" and winners[0].via == "",
                "winner is wg1 (correct: fallback-metric route ignored "
                "for sticky)")


# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Planner fallback-metric regression tests [FALLBACK_METRIC_V1]")
    print("=" * 70)
    print(f"ROUTE_METRIC          = {ROUTE_METRIC}")
    print(f"ROUTE_FALLBACK_METRIC = {ROUTE_FALLBACK_METRIC}")
    print(f"ADMIN_ALIAS_METRIC    = {ADMIN_ALIAS_METRIC}")

    test_winner_plus_fallback_installed_from_clean()
    test_winner_already_installed_no_redundant_install()
    test_both_already_installed_no_op()
    test_swap_winner_and_fallback_when_rtt_inverts()
    test_stale_route_with_no_observation_gets_removed()
    test_single_observation_no_fallback()
    test_sticky_ignores_fallback_metric_route()

    print("\n" + "=" * 30 + " SUMMARY " + "=" * 30)
    if FAILS:
        for f in FAILS:
            print(f"  FAIL: {f}")
        sys.exit(1)
    print("ALL FALLBACK-METRIC PLANNER TESTS PASS")


if __name__ == "__main__":
    main()
