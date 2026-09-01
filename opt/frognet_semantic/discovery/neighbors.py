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
neighbors.py - [NEIGHBOR_SCOPE_V1] directly-attached neighbor set for gossip.

propogateNotification used to curl every 10.A.B.1 in /etc/hosts (O(fleet) fan-out
from every node). Epidemic re-propagation only needs neighbor edges to cover the
whole connected mesh, so we notify ONLY directly-attached neighbors:

  - LAN neighbors: the distinct next-hops (`via X`) of FrogNet /24 routes on a
    PHYSICAL device. Every FrogNet subnet reached over Ethernet/WiFi exits via an
    L2-adjacent neighbor; a relayed-but-distant node is a destination, never a
    next-hop, so it falls out automatically. Captures both our downstream LAN
    clients and our upstream gateway.
  - Tunnel peers: the .1 identity of each active WG tunnel (channel_name is
    "<PeerName>-<subnet3>"; AllowedIPs is always 10/8 so it can't be used).

This cuts per-node fan-out from O(fleet) to O(neighbors) while preserving
mesh-wide reach via the receivers' own re-propagation.

CLI: `python3 -m discovery.neighbors` prints one target IP per line (reads the
live `ip route`, active tunnel state, and local addresses). Exit non-zero only on
an internal error so the caller can fall back to the legacy /etc/hosts scan.
"""
from __future__ import annotations

# [SIM_ROOT_REDIRECT_V1] Resolve the sentinel through FROGNET_SENTINEL_DIR so a
# simulation can point it at a per-run temp dir instead of inheriting the live
# mesh's neighbour map.  Default is the shipped path: production unchanged.
def _sent_neighbor_via():
    import os
    return os.path.join(os.environ.get("FROGNET_SENTINEL_DIR", "/etc/sentinels"),
                        "frognet_neighbor_via")



def direct_neighbor_targets(route_text, channel_names, local_ips,
                            via_identity=None):
    """Pure derivation. route_text = `ip -4 route show`; channel_names = active
    tunnels' channel_name strings; local_ips = this node's own addresses to skip.
    via_identity = {dhcp_next_hop: FrogNet .1} for downstream LAN children, from
    discovery's seg_relay map (NEIGHBOR_FROGNET_ADDR_V1). A LAN next-hop that is a
    child's DHCP lease is translated to the child's FrogNet .1 so the notification
    reaches the address its proxy actually serves; an upstream next-hop already IS
    a .1 identity and is left as-is (absent from the map).
    Returns a sorted list of neighbor target IPs."""
    local = set(local_ips or ())
    vmap = via_identity or {}

    lan = set()
    installed24 = set()                        # every /24 net present in the table
    for line in (route_text or "").splitlines():
        p = line.split()
        if not p:
            continue
        dest = p[0]
        if not dest.endswith("/24"):
            continue
        net = dest[:-3]                       # "10.28.28.0/24" -> "10.28.28.0"
        if not net.startswith("10."):
            continue
        if net.startswith("10.253.") or net.startswith("10.254."):
            continue                          # transit /30 segs, chorus overlay
        # Record the net BEFORE the LAN-only skips: a tunnel winner is
        # "<sub3>.0/24 dev wgN scope link" (no via), and the tunnel gate below
        # needs to see it.
        installed24.add(net)
        if "via" not in p or "dev" not in p:
            continue                          # scope-link (wg/own subnet) -> skip
        gw = p[p.index("via") + 1]
        dev = p[p.index("dev") + 1]
        if dev.startswith("wg") or dev == "frognet0":
            continue                          # tunnel/overlay, not a LAN neighbor
        if not gw.startswith("10."):
            continue                          # WAN default gw etc.
        lan.add(vmap.get(gw, gw))             # DHCP lease -> FrogNet .1 if known

    # [NEIGHBOR_CLIENT_DOT1_V1] The most important neighbor for a CLIENT node - the
    # .1 identity of a subnet we hold a NON-.1 address on - is reached over the
    # kernel connected route (no `via`) and the default route, so the /24-via scan
    # above never sees it. Seattle2 (.66/.47 on 10.130.130.0/24) derived 0 targets
    # and propagated to nobody. Add each such .1 directly: it is L2-adjacent and
    # reachable via the connected route regardless of what FrogNet installed.
    for ip in local:
        o = ip.split(".")
        if len(o) != 4 or not ip.startswith("10.") or o[3] == "1":
            continue
        if ip.startswith("10.253.") or ip.startswith("10.254."):
            continue
        lan.add(f"{o[0]}.{o[1]}.{o[2]}.1")

    # [NEIGHBOR_TUNNEL_ROUTE_GATE_V1] A tunnel peer is a target only if its /24 is
    # actually installed. A channel whose subnet has no route this pass - the peer
    # crashed, dropped off the net, was renamed, or the tunnel is dead (BABox over
    # a dead wg0, reaped) - is skipped, so we don't curl a black hole and stall
    # propagation (the dead peer ate 5.5s on an rc=22 timeout otherwise).
    tun = set()
    for ch in (channel_names or ()):
        if not ch or "-" not in ch:
            continue
        sub3 = ch.rsplit("-", 1)[-1]          # "Seattle5-10.250.250" -> "10.250.250"
        if sub3.startswith("10.") and sub3.count(".") == 2:
            if (sub3 + ".0") in installed24:
                tun.add(sub3 + ".1")

    targets = (lan | tun) - local
    targets.discard("")
    return sorted(targets)


def _main():
    import subprocess, glob, json, sys

    def _ip(*args):
        for binp in ("/usr/sbin/ip", "/sbin/ip", "ip"):
            try:
                return subprocess.run([binp, *args], capture_output=True,
                                      text=True).stdout
            except FileNotFoundError:
                continue
        raise FileNotFoundError("ip")

    route_text = _ip("-4", "route", "show")

    channel_names = []
    for f in glob.glob("/var/lib/frognet-tunnel/active/*.json"):
        try:
            with open(f) as fh:
                channel_names.append(json.load(fh).get("channel_name", ""))
        except (OSError, ValueError):
            continue

    local_ips = {"127.0.0.1"}
    addr = _ip("-4", "-o", "addr", "show")
    for line in addr.splitlines():
        parts = line.split()
        for i, t in enumerate(parts):
            if t == "inet" and i + 1 < len(parts):
                ip = parts[i + 1].split("/")[0]
                if ip.startswith("10."):
                    local_ips.add(ip)

    # [NEIGHBOR_FROGNET_ADDR_V1] discovery persists {dhcp_next_hop: FrogNet .1}
    # for directly-attached downstream children to this sentinel (one
    # "<dhcp> <dot1>" per line). Translate downstream vias to the FrogNet address.
    via_identity = {}
    try:
        for ln in open(_sent_neighbor_via()):
            f = ln.split()
            if len(f) >= 2 and f[0].startswith("10.") and f[1].startswith("10."):
                via_identity[f[0]] = f[1]
    except OSError:
        via_identity = {}

    for tgt in direct_neighbor_targets(route_text, channel_names, local_ips,
                                       via_identity=via_identity):
        print(tgt)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
