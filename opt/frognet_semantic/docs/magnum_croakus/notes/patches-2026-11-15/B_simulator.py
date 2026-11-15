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
# B -- Chapter 41 brought to the simulator in the 2026-11-15 tree: tier 3 (CroakOS discovery from memory at S=200,
# the hardware baselines, generated topologies and their events, slow links on every bearer) and the LISP tier.
# Sources: opt/frognet_semantic/simulation/SIM_STATUS.md ("Tier 3, all of it"), s_scale_tier.py, s_topo_tier.py,
# slowlink.py, lisp_slow_tier.py, run_all.py.
from patchlib import *

sub('H2("Where it stops"),',
    U('''H2("The whole system, at two hundred members"),
      P([ t("Tier 3 runs CroakOS the way it runs on hardware: a real fdf-ram, a real fdf-client for every member, and discovery done from memory. Each member writes its facts, reads everyone else’s, and builds its own plan. Nothing is modelled that the members could compute themselves, and routes are followed only in the verification plan the run writes out.") ]),
      bullet([ b("Scale. "), t("A star of 200 members with 1, 5 and 20 egresses: every one of the 40,000 ordered pairs reaches its destination by the expected path (s_scale_tier.py).") ]),
      bullet([ b("The hardware topologies. "), t("The twenty route tables recorded on real machines, converted to the facts a member writes. Seventeen come out exactly as the hardware did. Three do not, and the simulator says why: a .1 holder whose only link is to a node that chose another parent, and two transit segments with no .1 holder, which give no lease and therefore no link. That is a finding about the model, and it is the kind of thing the tier exists to catch.") ]),
      bullet([ b("Generated topologies and events. "), t("Trees, a mesh where the exit is chosen by link speed rather than hop count, and a mixed forest, each with every expected path stated hop by hop by the generator. Then the events: a link goes down, an egress is lost and comes back, a child moves, three rounds of churn, a member’s link to the RAM cut past its keepalive. All of them come out exactly as stated (s_topo_tier.py).") ]),
      bullet([ b("Slow links. "), t("slowlink.py shapes each member’s session in user space: latency, jitter, rate, loss and outages, with profiles for LAN, Wi-Fi, 4G, geostationary satellite and 900 MHz HaLow. Members on every bearer down to geostationary and lossy HaLow hold their sessions; a jammed bearer at 2 kb/s with two-second outages does not.") ]),
      bullet([ b("An application over the same links. "), t("lisp_slow_tier.py runs Ribbit-LISP and lispers.net, both unmodified, central, at the edge and at a slow edge, and compares them under the same conditions.") ]),
      P([ t("The commands are in "), mono("simulation/SIM_STATUS.md"), t(". Tier 3 needs the CroakOS binaries built (rinab’s build.sh and build_static_host.sh) and nothing else.") ]),
      H2("Where it stops"),'''))

sub('P([ b("NOT DEMONSTRATED."), t(" Concurrency. Merges run one at a time in a single process, so every race is unreachable by construction. No topology edge carries loss or jitter, and only the real-kernel packet run (tier4_transport.py) puts a delay on a tunnel edge. The transport tier models latency, jitter, bandwidth and outage over a single link only, and the two are not yet joined.',
    'P([ b("NOT DEMONSTRATED."), t(" Concurrency. Merges run one at a time in a single process, so every race is unreachable by construction. Loss, jitter, rate and outages now ride every member\'s session in tier 3; the LAN discovery tiers still carry none on a topology edge.')
done()
