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
"""fabric.py - the faked wire. Forwards hop-by-hop by asking each node's FAKED
KERNEL (fake_ip) for its next hop. No routing logic here; it only consults the
tables the real code installed."""
import os, subprocess
ROOT = "/home/claude/frogsim_proc"; IP = f"{ROOT}/bin/ip"

def _net24(ip): return ".".join(ip.split(".")[:3])

def _route_get(node, dst):
    env = dict(os.environ)
    env["FROGSIM_KTABLE"] = f"{ROOT}/nodes/{node}/ktable"
    env["FROGSIM_KADDR"]  = f"{ROOT}/nodes/{node}/kaddr"
    r = subprocess.run([IP, "route", "get", dst], env=env, capture_output=True, text=True)
    if "unreachable" in (r.stderr + r.stdout) or not r.stdout.strip():
        return None, None, None
    p = r.stdout.strip().splitlines()[0].split()
    via = p[p.index("via")+1] if "via" in p else ""
    dev = p[p.index("dev")+1] if "dev" in p else ""
    src = p[p.index("src")+1] if "src" in p else ""
    return via, dev, src

def traceroute(topo, src, dst, max_hops=12):
    owner, peer, serves = topo["ip_owner"], topo["tunnel_peer"], topo["serves"]
    path, cur, seen = [], src, set()
    for hop in range(1, max_hops+1):
        if serves.get(cur) == _net24(dst):
            return path, "ARRIVED"
        via, dev, _src = _route_get(cur, dst)
        if dev is None:
            path.append((hop, cur, "", "BLACKHOLE")); return path, "UNREACHABLE"
        nxt = peer.get((cur, dev)) if dev.startswith("wg") else owner.get(via)
        path.append((hop, cur, via or f"onlink/{dev}", dev))
        if cur in seen:
            return path, "LOOP"
        if nxt is None:
            return path, f"NO_NEXT_NODE(via={via} dev={dev})"
        seen.add(cur); cur = nxt
    return path, "MAX_HOPS"
