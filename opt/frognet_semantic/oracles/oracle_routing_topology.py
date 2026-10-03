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
"""oracle_routing_topology.py — [LEASE_IS_NOT_A_NODE_V1]

Drives frognet_routing_topology.build() against fixture sentinels holding the
REAL values from the fleet, and checks the payload says what a map needs.

The failure this exists to prevent: publishing a DHCP lease as if it were a node.
Seattle2's uplink is 10.160.160.47 -- that is a lease on Seattle6's segment, and
the node is 10.160.160.1. The live peer observations already carry 10.250.250.85,
10.250.250.69, 10.160.160.63, 10.120.120.63 and 10.120.120.155, so a consumer fed
raw addresses draws five machines that do not exist.

Run:  python3 oracle_routing_topology.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.normpath(os.path.join(HERE, "..", "..", "..",
                                    "usr", "local", "bin",
                                    "frognet_routing_topology.py"))
if not os.path.exists(MOD):
    MOD = "/usr/local/bin/frognet_routing_topology.py"

FAIL, PASS = [], []


def ok(m):
    PASS.append(m)
    print("  ok    %s" % m)


def bad(m):
    FAIL.append(m)
    print("  FAIL  %s" % m)


def load_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("frt", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    if not os.path.exists(MOD):
        print("FATAL: cannot find frognet_routing_topology.py at %s" % MOD)
        return 1
    frt = load_module()
    _real_broker_config = frt.broker_config

    print("=== [LEASE_IS_NOT_A_NODE_V1] dot_one ===")
    # Real addresses seen in the fleet's peer observations and echo lines.
    cases = [
        ("10.160.160.47", "10.160.160.1", "Seattle2's uplink lease -> Seattle6"),
        ("10.250.250.191", "10.250.250.1", "Seattle3's uplink lease -> Seattle5"),
        ("10.250.250.221", "10.250.250.1", "Seattle6's uplink lease -> Seattle5"),
        ("10.120.120.63", "10.120.120.1", "brokerhost lease -> Seattle2"),
        ("10.250.250.85", "10.250.250.1", "stray lease -> Seattle5"),
        ("10.160.160.63", "10.160.160.1", "stray lease -> Seattle6"),
        ("10.130.130.2", "10.130.130.1", "secondary .2 -> the node"),
        ("10.160.160.1", "10.160.160.1", "a .1 maps to itself"),
    ]
    for src, want, why in cases:
        got = frt.dot_one(src)
        (ok if got == want else bad)("%s -> %s   (%s)" % (src, got or "''", why))

    print("\n=== addresses that are NOT a node identity ===")
    for src, why in [("10.253.203.85", "tunnel transit"),
                     ("10.254.1.9", "chorus virtual"),
                     ("192.168.0.42", "not 10/8 - a real uplink"),
                     ("172.16.26.199", "not 10/8 - BABox's uplink"),
                     ("127.0.0.1", "loopback"),
                     ("", "empty")]:
        got = frt.dot_one(src)
        (ok if got == "" else bad)("%s rejected (%s)%s"
                                   % (src or "''", why,
                                      "" if got == "" else " -- returned " + got))

    print("\n=== payload from fixture sentinels (Seattle6: child AND parent) ===")
    rig = tempfile.mkdtemp(prefix="frt-")
    os.environ["FROGNET_SENTINEL_DIR"] = rig

    # Seattle6 sits on Seattle5's segment at .221 and fronts Seattle3 and
    # Seattle2, which hold leases on ITS segment.
    with open(os.path.join(rig, "exit_host.tsv"), "w") as f:
        f.write("10.250.250.1\t10.250.250.1\twlan1\n")
    with open(os.path.join(rig, "frognet_neighbor_via"), "w") as f:
        f.write("# lease on our segment -> their FrogNet .1\n")
        f.write("10.160.160.47 10.120.120.1\n")     # Seattle2
        f.write("10.160.160.63 10.130.130.1\n")     # Seattle3
    with open(os.path.join(rig, "tunnel_peers.tsv"), "w") as f:
        f.write("10.102.60.1\tNew-York-1\twg2\n")
    with open(os.path.join(rig, "frognet_hosts"), "w") as f:
        f.write("10.250.250.1 FrogNetHost.Seattle5\n"
                "10.160.160.1 FrogNetHost.Seattle6\n"
                "10.130.130.1 FrogNetHost.Seattle3\n"
                "10.120.120.1 FrogNetHost.Seattle2\n"
                "10.102.60.1 FrogNetHost.New-York-1\n")

    frt.identity = lambda: ("Seattle6", "", "10.160.160.1", "10.250.250.221")
    frt.kernel_routes = lambda: [
        {"dest": "10.120.120.0/24", "via": "10.160.160.1", "dev": "wlan0", "kind": "HOP"},
        {"dest": "default", "via": "10.250.250.1", "dev": "wlan1", "kind": "DEFAULT"},
    ]
    frt.broker_config = lambda: {"host": "streamingfrog.com", "port": 18257,
                                 "configured": True, "up": True}

    payload, err = frt.build()
    if payload is None:
        bad("build() returned nothing: %s" % err)
    else:
        (ok if payload["node"] == "Seattle6" else bad)("node = %s" % payload["node"])
        (ok if payload["node_ip"] == "10.160.160.1" else bad)(
            "node_ip = %s" % payload["node_ip"])
        (ok if payload["uplink"]["parent"] == "10.250.250.1" else bad)(
            "parent resolved from lease .221 -> %s" % payload["uplink"]["parent"])
        (ok if payload["uplink"]["parent_name"] == "Seattle5" else bad)(
            "parent named %s" % payload["uplink"]["parent_name"])
        (ok if payload["is_gateway"] is False else bad)(
            "Seattle6 is NOT a gateway (its uplink is a FrogNet segment)")

        ds = sorted(d["ip"] for d in payload["downstreams"])
        want = ["10.120.120.1", "10.130.130.1"]
        (ok if ds == want else bad)("downstreams = %s (want %s)" % (ds, want))
        leases = [d["ip"] for d in payload["downstreams"]
                  if d["ip"].rsplit(".", 1)[1] != "1"]
        (ok if not leases else bad)(
            "no lease published as a downstream" if not leases
            else "leases leaked: %s" % leases)

        (ok if payload["broker"]["configured"] else bad)("broker carried")
        (ok if payload["tunnels"] and payload["tunnels"][0]["peer"] == "10.102.60.1"
         else bad)("tunnel peer normalised to a .1")

        blob = json.dumps(payload)
        (ok if "10.160.160.47" not in blob and "10.160.160.63" not in blob else bad)(
            "no raw lease anywhere in the published payload")

    print("\n=== a gateway looks different ===")
    frt.identity = lambda: ("Seattle5", "", "10.250.250.1", "192.168.0.42")
    with open(os.path.join(rig, "exit_host.tsv"), "w") as f:
        f.write("10.250.250.1\t192.168.0.1\twlan1\n")
    payload, _ = frt.build()
    if payload is None:
        bad("gateway build() returned nothing")
    else:
        (ok if payload["is_gateway"] else bad)(
            "Seattle5 IS a gateway (uplink 192.168.0.42 is not FrogNet)")
        (ok if payload["uplink"]["parent"] == "" else bad)(
            "a gateway has no parent (got %r)" % payload["uplink"]["parent"])
        (ok if payload["internet"]["gw"] == "192.168.0.1" else bad)(
            "internet gw = %s" % payload["internet"]["gw"])

    print("\n=== broker liveness is evidence, not a probe ===")
    # Restore the REAL broker_config: an earlier section stubbed it to keep the
    # payload deterministic, and testing a stub proves nothing.
    frt.broker_config = _real_broker_config
    # A live tunnel is what the broker is FOR, so a tunnel existing is the
    # evidence that matters. Two cheap execs, no network, no guessing.
    frt._wg_up = lambda: True
    frt._tunnel_daemon_up = lambda: False
    b = frt.broker_config()
    (ok if b.get("up") else bad)("wg interface present -> broker up")
    frt._wg_up = lambda: False
    frt._tunnel_daemon_up = lambda: True
    b = frt.broker_config()
    (ok if b.get("up") else bad)("tunnel daemon active -> broker up")
    frt._wg_up = lambda: False
    frt._tunnel_daemon_up = lambda: False
    b = frt.broker_config()
    (ok if not b.get("up") else bad)("neither -> broker not claimed up")

    print("\n=== the flusher can call it like any other producer ===")
    frt.identity = lambda: ("Seattle6", "", "10.160.160.1", "10.250.250.221")
    snap = frt.routing_topology_snapshot()
    (ok if isinstance(snap, dict) and snap.get("node") == "Seattle6" else bad)(
        "routing_topology_snapshot() returns the payload")
    frt.identity = lambda: None
    snap = frt.routing_topology_snapshot()
    (ok if snap == {} else bad)(
        "returns {} without an identity, so _add() skips it (falsy, like the others)")

    print("\n=== identity failure is not papered over ===")
    frt.identity = lambda: None
    payload, err = frt.build()
    (ok if payload is None and err else bad)(
        "build() refuses without an identity" if payload is None
        else "published anyway under an unknown name")

    print()
    print("%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED")
        return 1
    print("PASS - [LEASE_IS_NOT_A_NODE_V1]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
