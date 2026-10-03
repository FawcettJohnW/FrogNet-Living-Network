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
"""frognet_routing_topology.py — publish <domain>.Routing.Topology.

WHY THIS EXISTS
---------------
Nothing in the shared memory says how a message actually travels. Measured on
the live fleet 2026-08-14, across all 84 links of all seven nodes' Topology.Links
sensors:

    peer=10.250.250.1   dev=        via=10.250.250.1   kind=FAST
    peer=10.160.160.1   dev=        via=10.160.160.1   kind=FAST

`via` equals `peer_ip` on every single row and `dev` is empty on every non-`lo`
row. That is not a bug in Topology.Links -- it records what the PROXY observed,
and the proxy's `via` is the adjacency it dialled (proxy_main.py:370), so "via ==
peer" is the honest answer to the question it is answering. It simply is not the
question a map asks.

The result is that every node appears to have a direct link to every other node:
Seattle2 publishes 18 links including New-York-1, BABox and BAMacBook. No tunnel
is visible anywhere -- no `wg` device ever appears. A topology map built from
this can only draw a full mesh, which is not the network.

The forwarding decision lives in the kernel routing table and in the merge's own
route planner, and the merge already writes every part of it to sentinels. This
module reads those and publishes them, so a consumer in any language can draw the
real shape without reading a single file off the node.

WHAT IT PUBLISHES
-----------------
    SensorType: Routing.Topology
    SensorName: <domain>.Routing.Topology

    {
      "timestamp":   <epoch>,
      "node":        "Seattle6",
      "node_ip":     "10.160.160.1",
      "is_gateway":  false,
      "uplink":      {"addr","dev","parent","parent_name"},
      "internet":    {"gw","dev"},          # gateways only
      "broker":      {"host","port","configured"},
      "downstreams": [{"ip","name"}],       # who I forward FOR
      "tunnels":     [{"peer","peer_name","dev"}],
      "routes":      [{"dest","via","dev","kind"}]
    }

[LEASE_IS_NOT_A_NODE_V1]
Every address in this payload is normalised to the .1 that owns its /24 before
being published. A DHCP lease identifies a SEGMENT, not a machine: Seattle2's
uplink lease 10.160.160.47 says "I am on Seattle6's segment", and the node is
10.160.160.1. Publishing the lease makes consumers draw leases as nodes -- the
live data already shows 10.250.250.85, 10.250.250.69, 10.160.160.63,
10.120.120.63 and 10.120.120.155 accumulating in peer observations, and a map fed
those would invent five machines that do not exist. Lop the last octet, put a 1
in its place, and the answer is the node.

    python3 frognet_routing_topology.py            # publish once
    python3 frognet_routing_topology.py --dry-run  # print, publish nothing
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

SENSOR_TYPE = "Routing.Topology"
METRIC_NAME = "Topology"          # -> <domain>.Routing.Topology

METRIC_UPSERT = "/usr/local/bin/metric_upsert.sh"
GETFROGNET = "/usr/local/bin/getFrogNet.bash"
IP = "/usr/sbin/ip"

_IPRE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def _sent(name=""):
    d = os.environ.get("FROGNET_SENTINEL_DIR", "/etc/sentinels")
    return os.path.join(d, name) if name else d


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def dot_one(ip):
    """[LEASE_IS_NOT_A_NODE_V1] The .1 that owns this address's /24.

    Returns "" for anything that is not a 10/8 address, and for the two ranges a
    node carries but is not identified by: 10.253/16 (tunnel transit) and
    10.254/16 (chorus virtual).
    """
    if not ip or not _IPRE.match(ip.strip()):
        return ""
    s = ip.strip()
    if not s.startswith("10."):
        return ""
    if s.startswith("10.253.") or s.startswith("10.254."):
        return ""
    o = s.split(".")
    if any(not p.isdigit() or int(p) > 255 for p in o):
        return ""
    return "%s.%s.%s.1" % (o[0], o[1], o[2])


def is_frognet(ip):
    return bool(dot_one(ip))


# ---------------------------------------------------------------- identity
def identity():
    """(domain, eth0, wlan0, wlan1) from getFrogNet.bash, or None.

    [IDENTITY_FAILS_LOUD_V1] Four fields with a non-empty name, or it failed.
    A sensor published under a name that is not this node's identity files this
    node's routing as some other node's.
    """
    try:
        p = subprocess.run([GETFROGNET], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    line = (p.stdout or "").strip().split("\n")[0].strip()
    if not line:
        return None
    f = [x.strip() for x in line.split(",")]
    if len(f) != 4 or not f[0]:
        return None
    return tuple(f)


# ---------------------------------------------------------------- sentinels
def exit_sentinel():
    """exit_host.tsv line 1 -> (exit_host, next_hop, dev). Empty tuple if absent.

    Written by fixdefault.install_exit_defaults as `{host}\\t{nh}\\t{dev}`.
    """
    line = _read(_sent("exit_host.tsv")).split("\n")[0]
    if not line.strip():
        return ("", "", "")
    c = line.rstrip("\n").split("\t")
    while len(c) < 3:
        c.append("")
    return (c[0].strip(), c[1].strip(), c[2].strip())


def neighbor_via():
    """frognet_neighbor_via -> {lease_on_our_segment: their_dot1}."""
    out = {}
    for ln in _read(_sent("frognet_neighbor_via")).splitlines():
        if ln.lstrip().startswith("#"):
            continue
        f = ln.split()
        if len(f) >= 2:
            out[f[0].strip()] = f[1].strip()
    return out


def tunnel_peers():
    """tunnel_peers.tsv -> [{peer, peer_name, dev}] with peers normalised."""
    out = []
    for ln in _read(_sent("tunnel_peers.tsv")).splitlines():
        if ln.lstrip().startswith("#") or not ln.strip():
            continue
        f = [x.strip() for x in ln.split("\t")]
        # Be tolerant of column count: take the first field that is an address.
        peer = ""
        for x in f:
            if _IPRE.match(x):
                peer = dot_one(x) or x
                break
        if not peer:
            continue
        name = next((x for x in f if x and not _IPRE.match(x) and "/" not in x), "")
        dev = next((x for x in f if x.startswith("wg")), "")
        out.append({"peer": peer, "peer_name": name, "dev": dev})
    return out


def hosts_by_dot1():
    """{dot1: name} from /etc/sentinels/frognet_hosts or /etc/hosts."""
    out = {}
    text = _read(_sent("frognet_hosts")) or _read("/etc/hosts")
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        if len(parts) < 2:
            continue
        d1 = dot_one(parts[0])
        if not d1:
            continue
        for p in parts[1:]:
            if "FrogNetHost." in p:
                out[d1] = p.split("FrogNetHost.")[-1]
                break
    return out


def _wg_up():
    """True if wireguard has at least one interface. Cheap and sufficient.

    `wg show interfaces` is one exec and no network. If a tunnel is up, the
    broker did its job -- that is what the broker is FOR. This is not a probe of
    the broker itself and does not pretend to be.
    """
    for exe in ("/usr/bin/wg", "/usr/sbin/wg", "wg"):
        try:
            p = subprocess.run([exe, "show", "interfaces"],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if p.returncode == 0:
            return bool((p.stdout or "").strip())
    return False


def _tunnel_daemon_up():
    """True if the tunnel daemon unit is active. One exec, no network."""
    try:
        p = subprocess.run(["systemctl", "is-active", "--quiet",
                            "frognet-tunnel-daemon-v3"], timeout=5)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def broker_config():
    """Broker from /etc/frognet/tunnel.conf, plus whether it is evidently working.

    `up` is true when a tunnel exists or the tunnel daemon is running. Neither is
    a probe of the broker, and it does not need to be: the broker's job is to get
    tunnels established, so a live tunnel is the evidence that matters. Cheap,
    two execs, no network.
    """
    text = _read("/etc/frognet/tunnel.conf")
    m = re.search(r"^\s*BROKER(?:_URL)?\s*=\s*\"?([^\"\s#]+)", text, re.M)
    up = _wg_up() or _tunnel_daemon_up()
    if not m:
        return {"host": "", "port": 0, "configured": False, "up": up}
    url = m.group(1)
    hm = re.match(r"(?:https?://)?([^:/]+)(?::(\d+))?", url)
    if not hm:
        return {"host": "", "port": 0, "configured": False, "up": up}
    return {"host": hm.group(1),
            "port": int(hm.group(2)) if hm.group(2) else 0,
            "configured": True,
            "up": up}


# ---------------------------------------------------------------- kernel
def kernel_routes():
    """FrogNet-relevant routes -> [{dest, via, dev, kind}].

    kind: LAN (on-link), HOP (via another FrogNet node), TUNNEL (wg device),
    DEFAULT (the exit). `via` is normalised to the owning .1 so a next hop reads
    as the node that forwards, not as the lease it answers on.
    """
    try:
        out = subprocess.run([IP, "-4", "route", "show"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    routes = []
    for ln in out.splitlines():
        f = ln.split()
        if not f:
            continue
        dest = f[0]
        via = dev = ""
        for i, tok in enumerate(f):
            if tok == "via" and i + 1 < len(f):
                via = f[i + 1]
            elif tok == "dev" and i + 1 < len(f):
                dev = f[i + 1]
        if dest != "default" and not dest.startswith("10."):
            continue
        if dev.startswith("wg"):
            kind = "TUNNEL"
        elif dest == "default":
            kind = "DEFAULT"
        elif via:
            kind = "HOP"
        else:
            kind = "LAN"
        routes.append({"dest": dest,
                       "via": dot_one(via) or via,
                       "dev": dev,
                       "kind": kind})
    return routes


# ---------------------------------------------------------------- payload
def build():
    ident = identity()
    if ident is None:
        return None, "identity failed - refusing to publish routing under a name that is not this node's"
    domain, eth0, wlan0, wlan1 = ident

    names = hosts_by_dot1()
    node_ip = dot_one(wlan0) or dot_one(eth0) or ""

    # The uplink is whichever interface address is NOT this node's own /24.
    # If it is a 10/8 address, its .1 is this node's parent. If it is not, this
    # node reaches the Internet directly and is a gateway.
    uplink_addr = ""
    for cand in (wlan1, eth0, wlan0):
        if not cand or cand == "0.0.0.0":
            continue
        if dot_one(cand) and dot_one(cand) == node_ip:
            continue                      # our own segment, not an uplink
        uplink_addr = cand
        break

    parent = dot_one(uplink_addr) if uplink_addr else ""
    is_gateway = bool(uplink_addr) and not parent

    exit_host, exit_nh, exit_dev = exit_sentinel()

    # Downstreams: who I forward FOR. frognet_neighbor_via maps a lease on my
    # segment to that neighbour's .1, which is exactly the set of nodes that
    # reach the rest of the fabric through me.
    downstreams = []
    for lease, their_dot1 in sorted(neighbor_via().items()):
        d1 = dot_one(their_dot1) or dot_one(lease)
        if not d1 or d1 == node_ip:
            continue
        downstreams.append({"ip": d1, "name": names.get(d1, "")})

    payload = {
        "timestamp": int(time.time()),
        "node": domain,
        "node_ip": node_ip,
        "is_gateway": is_gateway,
        "uplink": {
            "addr": uplink_addr,
            "dev": exit_dev,
            "parent": parent,
            "parent_name": names.get(parent, ""),
        },
        "internet": ({"gw": exit_nh, "dev": exit_dev} if is_gateway
                     else {"gw": "", "dev": ""}),
        "broker": broker_config(),
        "downstreams": downstreams,
        "tunnels": tunnel_peers(),
        "routes": kernel_routes(),
        "exit": {"host": dot_one(exit_host) or exit_host,
                 "next_hop": dot_one(exit_nh) or exit_nh,
                 "dev": exit_dev},
    }
    return payload, ""


def routing_topology_snapshot():
    """The payload, or {} if this node cannot state its identity.

    Named and shaped like the other producers proxy_metrics collects, so the
    flusher can _add() it alongside them: one coalesced batch, the existing
    random stagger, and the heartbeat cycle already wrapped around it. A separate
    timer would be a second thing to schedule and a second thing that can stop
    without anyone noticing.
    """
    payload, _err = build()
    return payload or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    payload, err = build()
    if payload is None:
        print("frognet_routing_topology: %s" % err, file=sys.stderr)
        return 1

    blob = json.dumps(payload, separators=(",", ":"))
    if a.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    try:
        p = subprocess.run([METRIC_UPSERT, SENSOR_TYPE, METRIC_NAME, blob],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        print("frognet_routing_topology: publish failed: %r" % (e,), file=sys.stderr)
        return 1
    if p.returncode != 0:
        print("frognet_routing_topology: metric_upsert rc=%d %s"
              % (p.returncode, (p.stderr or "").strip()), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
