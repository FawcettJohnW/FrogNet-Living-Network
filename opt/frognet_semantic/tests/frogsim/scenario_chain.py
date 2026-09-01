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
"""Scenario: Seattle guest chain Sea2->Sea3->Sea6->Sea5 ==wg==> NY1.
Nodes stood up at the HARDWARE-VALIDATED converged state (folded_seattle_chain_
routes, John 2026-06-06), installed in the shape the fix produces: mesh-ward via
uplinkGW (no src, no onlink); downstream via neighbor.1 (onlink); gateway via wg.
Then fabric-traceroute Sea2 -> NY1 over the REAL faked kernels."""
import os
ROOT = "/home/claude/frogsim_proc"
import sys; sys.path.insert(0, ROOT)
from fabric import traceroute

MESH = ["10.120.120", "10.130.130", "10.160.160", "10.250.250", "10.102.60", "10.179.179"]

# node -> (kaddr lines, ktable lines)  -- ktable = connected + mesh at converged state
NODES = {
 "Seattle2": (
   ["wlan0 10.120.120.1/24", "wlan1 10.130.130.47/24"],
   ["10.120.120.0/24 dev wlan0 proto kernel scope link src 10.120.120.1",
    "10.130.130.0/24 dev wlan1 proto kernel scope link src 10.130.130.47"]
   + [f"{d}.0/24 via 10.130.130.1 dev wlan1 metric 22"          # all mesh-ward, no src/onlink
      for d in MESH if d not in ("10.120.120","10.130.130")]),
 "Seattle3": (
   ["wlan0 10.130.130.1/24", "wlan1 10.160.160.191/24"],
   ["10.130.130.0/24 dev wlan0 proto kernel scope link src 10.130.130.1",
    "10.160.160.0/24 dev wlan1 proto kernel scope link src 10.160.160.191",
    "10.120.120.0/24 dev wlan0 metric 22"]                     # downstream to Two (scope-link)
   + [f"{d}.0/24 via 10.160.160.1 dev wlan1 metric 22"          # mesh-ward via Six
      for d in MESH if d not in ("10.120.120","10.130.130","10.160.160")]),
 "Seattle6": (
   ["wlan0 10.160.160.1/24", "wlan1 10.250.250.222/24"],
   ["10.160.160.0/24 dev wlan0 proto kernel scope link src 10.160.160.1",
    "10.250.250.0/24 dev wlan1 proto kernel scope link src 10.250.250.222",
    "10.120.120.0/24 via 10.130.130.1 dev wlan0 metric 22 onlink",  # downstream, ONLINK
    "10.130.130.0/24 via 10.130.130.1 dev wlan0 metric 22 onlink"]
   + [f"{d}.0/24 via 10.250.250.1 dev wlan1 metric 22"          # mesh-ward via Five
      for d in ("10.28.28","10.102.60","10.111.11","10.179.179")]),
 "Seattle5": (
   ["eth0 10.250.250.1/24", "wg2 10.253.203.90/30", "wg1 10.253.203.106/30"],
   ["10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1",
    "10.102.60.0/24 dev wg2 scope link metric 22",              # tunnel to NY1
    "10.179.179.0/24 dev wg1 scope link metric 22"]             # tunnel to BAMacBook
   + [f"{d}.0/24 via 10.250.250.221 dev eth0 metric 22"          # downstream to Six's lease
      for d in ("10.28.28","10.111.11","10.120.120","10.130.130","10.160.160")]),
 "NY1": (
   ["eth0 10.102.60.1/24", "wg2 10.253.203.89/30"],
   ["10.102.60.0/24 dev eth0 proto kernel scope link src 10.102.60.1"]
   + [f"{d}.0/24 dev wg2 scope link metric 22"                   # back through the tunnel
      for d in MESH if d != "10.102.60"]),
}

TOPO = {
 "serves": {"Seattle2":"10.120.120","Seattle3":"10.130.130","Seattle6":"10.160.160",
            "Seattle5":"10.250.250","NY1":"10.102.60"},
 "ip_owner": {"10.130.130.1":"Seattle3","10.160.160.1":"Seattle6","10.250.250.1":"Seattle5",
              "10.250.250.221":"Seattle6","10.130.130.47":"Seattle2","10.102.60.1":"NY1"},
 "tunnel_peer": {("Seattle5","wg2"):"NY1",("NY1","wg2"):"Seattle5",("Seattle5","wg1"):"BAMacBook"},
}

def setup():
    for node,(kaddr,ktable) in NODES.items():
        d = f"{ROOT}/nodes/{node}"; os.makedirs(d, exist_ok=True)
        open(f"{d}/kaddr","w").write("\n".join(kaddr)+"\n")
        open(f"{d}/ktable","w").write("\n".join(ktable)+"\n")

if __name__ == "__main__":
    setup()
    dst = "10.102.60.1"
    print(f"traceroute Seattle2 -> NY1 ({dst})  [over the real faked kernels]\n")
    path, status = traceroute(TOPO, "Seattle2", dst)
    for hop, node, via, dev in path:
        print(f"  {hop:>2}  {node:<9}  next: {via:<18} dev {dev}")
    print(f"\n  RESULT: {status}")
