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
folded_seattle_chain_routes.py - HARDWARE-VALIDATED route tables for the Seattle
guest chain, folded back from a physical run (John, 2026-06-06). These are the
oracle the directional-next-hop implementation must reproduce. They forward both
ways on the real boxes - confirmed by ping in both directions after install.

This is data, not a live discovery driver: current discovery does NOT produce
these (it lacks uplink-awareness - see SIM_STATUS DIRECTIONAL_NEXT_HOP). The point
of folding them in is that the target is now real-world ground truth, not a guess,
so the eventual code change can be verified against it instead of against my model.

Topology (arrow = "is a DHCP guest on the AP of"; gateway holds the wg tunnels):

    Seattle2 -> Seattle3 -> Seattle6 -> Seattle5 == wg(broker-relayed) ==> NY/etc
    10.120.120  10.130.130  10.160.160  10.250.250

THE RULE the working tables prove (this is what to implement):
  For each node, classify every remote /24 by direction along the chain:
    - MESH-WARD (toward the gateway/tunnel, i.e. NOT in this node's downstream
      branch): next hop = this node's UPSTREAM neighbor == its own default-via
      gateway, on its uplink dev.   (Three: via 10.160.160.1 dev wlan1;
      Six: via 10.250.250.1 dev wlan1.)
    - DOWNSTREAM (a node this one is the AP/relay for, and everything below it):
      next hop = the DOWNSTREAM neighbor on the AP-facing dev.
      (Six: via 10.130.130.1 dev wlan0; Five: via 10.250.250.221 dev eth0.)
    - GATEWAY (Five): the mesh-ward/remote side egresses the TUNNEL (dev wgN),
      not a LAN via. Downstream still via the AP-facing neighbor.
  The discriminator the boxes use is the node's own `default via` = the upstream
  direction. Mesh-ward follows it; downstream opposes it. The earlier bug aimed
  mesh-ward routes DOWN the chain (3 -> via Two, 6 -> via Three), which black-holed
  because the downstream leaf has no path back up/out.

PLUMBING the implementation needs (discovery does not have it today):
  - discovery must learn its uplink (default dev + gw). NOTE: fixdefault runs
    AFTER the walk in run_merge_live, so discovery must read the EXISTING kernel
    default route at walk start (prior cycle's), not expect this cycle's.
  - then select next-hop by direction per the rule above, instead of HOP_VIA's
    "via the address that answered".

VALIDATED TABLES (mesh /24s only; connected/default/wg-/30 lines omitted). Each
entry: dest -> (via, dev, onlink). via="" + dev=wgN means tunnel scope-link.
"""

# Seattle5 (gateway). Upstream = tunnel; downstream neighbor = Six's lease
# 10.250.250.221 on eth0. (10.179.179 rode wg1 directly in the capture.)
SEATTLE5 = {
    "10.102.60.0/24":  ("",             "wg2",  False),   # tunnel to NY-1 (works, ttl63)
    "10.179.179.0/24": ("",             "wg1",  False),   # tunnel to BAMacBook
    "10.28.28.0/24":   ("10.250.250.221", "eth0", False),
    "10.111.11.0/24":  ("10.250.250.221", "eth0", False),
    "10.120.120.0/24": ("10.250.250.221", "eth0", False),
    "10.130.130.0/24": ("10.250.250.221", "eth0", False),
    "10.160.160.0/24": ("10.250.250.221", "eth0", False),
}

# Seattle6. Upstream = Five (10.250.250.1) on wlan1; downstream = Three
# (10.130.130.1) on wlan0.
SEATTLE6 = {
    # mesh-ward / tunnel side -> UP to Five
    "10.28.28.0/24":   ("10.250.250.1", "wlan1", False),
    "10.102.60.0/24":  ("10.250.250.1", "wlan1", False),
    "10.111.11.0/24":  ("10.250.250.1", "wlan1", False),
    "10.179.179.0/24": ("10.250.250.1", "wlan1", False),
    "10.250.250.0/24": ("",             "wlan1", False),   # connected uplink seg
    # downstream -> DOWN to Three
    "10.120.120.0/24": ("10.130.130.1", "wlan0", True),
    "10.130.130.0/24": ("10.130.130.1", "wlan0", True),
}

# Seattle3. Upstream = Six (10.160.160.1) on wlan1; downstream = Two
# (10.120.120.1) on wlan0.
SEATTLE3 = {
    # mesh-ward -> UP to Six
    "10.250.250.0/24": ("10.160.160.1", "wlan1", False),
    "10.28.28.0/24":   ("10.160.160.1", "wlan1", False),
    "10.102.60.0/24":  ("10.160.160.1", "wlan1", False),
    "10.111.11.0/24":  ("10.160.160.1", "wlan1", False),
    "10.179.179.0/24": ("10.160.160.1", "wlan1", False),
    # downstream -> DOWN to Two
    "10.120.120.0/24": ("",             "wlan0", False),   # connected to Two's seg
}

# Seattle2 (leaf). Single uplink to Three; everything mesh-ward via Three.
SEATTLE2 = {
    "_default_via": ("10.130.130.1", None, False),
}

VALIDATED = {"Seattle5": SEATTLE5, "Seattle6": SEATTLE6,
             "Seattle3": SEATTLE3, "Seattle2": SEATTLE2}

if __name__ == "__main__":
    print("HARDWARE-VALIDATED Seattle-chain routes (forward + return confirmed by ping).")
    print("Directional rule: mesh-ward via UPSTREAM (default-via dir); downstream via leaf neighbor;")
    print("gateway uses the tunnel. Discovery must learn its uplink to reproduce these.\n")
    for node, tbl in VALIDATED.items():
        print(f"{node}:")
        for dest, (via, dev, onl) in tbl.items():
            shape = (f"dev {dev}" if not via else f"via {via} dev {dev}") + (" onlink" if onl else "")
            print(f"  {dest:18} -> {shape}")
        print()
