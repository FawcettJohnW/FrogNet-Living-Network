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
frognet_route - single owner of FrogNet /24 routing decisions.

This package replaces the three overlapping route-policy implementations
that used to live in sync_interfaces.sh (install_route_for_hostpath,
with its 2x-RTT promotion rule and non-transit class preference),
addHost.bash (the metric-50 install), and poll.py
(_reconcile_tunnels_inner's RTT-sort winner picker).  All three are
gone.  This package is the one authority.

The pipeline per merge pass:

  1. tunnel bring-up phase (in poll.py, UNCHANGED) brings every broker
     channel's wgN up.  Interfaces present => sync_interfaces can probe
     them alongside LAN paths as peers.

  2. sync_interfaces.sh (REFACTORED) is now a pure gatherer:
       wave 1: /32-probe every seed via its candidate (dev, via_ip),
               record an Observation per successful echo, delete the
               /32 whether or not the probe succeeded.  On success,
               fetch getHosts to enqueue children for wave 2.
       commit provisional: call this package's committer, which
               installs /24s for wave-1 winners so that wave-2 via's
               are reachable.
       wave 2: same as wave 1, but for the enqueued children.
       commit final: call this package's committer again with all
               observations combined.  Winners reshape the route
               table, losers are removed one-at-a-time, tunnels that
               won nothing are torn down.

  3. poll.py reconcile tail (SIMPLIFIED): after sync_interfaces,
     synthesize Observations for any tunnels that weren't observed in
     sync_interfaces' waves (broker-only peers), feed them to the
     committer, act on its tear_down_tunnels result.

The three rules this package enforces that the old code did not:

  A. Exactly one /24 per destination.  Losers get deleted, not demoted.
     Metric=22 for every FrogNet /24; routes are distinguished by
     (dev, via) alone.

  B. Lowest RTT wins.  No 2x threshold.  No non-transit preference.
     No kind-based tiebreak.  Tiebreak is deterministic by
     (dev asc, via asc) for stability.

  C. Every /32 probe route is cleaned up in a trap, not a conditional.
     The committer defensively sweeps any 10.x.y.2/32 routes it finds
     in the kernel at the end of its run, in case something else
     leaked one.
"""

from .observation import Observation, write_observations, read_observations
from .planner import plan, Plan, InstallRoute, RemoveRoute, Route, ROUTE_METRIC

__all__ = [
    "Observation", "write_observations", "read_observations",
    "plan", "Plan", "InstallRoute", "RemoveRoute", "Route", "ROUTE_METRIC",
]
