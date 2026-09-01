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
"""frogsim_transport.py - the faked WIRE, installed into a real service process
before its main() runs. Route-gates :9009: a node may open a control connection to
a peer ONLY if its own faked kernel has a route to that peer; the connection is
then delivered to the peer node's real daemon. No app code changes - we patch the
socket boundary underneath it, exactly like the fake `ip` patches the kernel."""
import os, sys, socket
sys.path.insert(0, "/home/claude/frogsim_proc")
import fabric, scenario_chain as sc

NODE = os.environ.get("FROGSIM_NODE", "")
PORTS = {"NY1":9101,"Seattle2":9102,"Seattle3":9103,"Seattle5":9105,"Seattle6":9106}
HTTP_PORTS = {"NY1":8101,"Seattle2":8102,"Seattle3":8103,"Seattle5":8105,"Seattle6":8106}
SUBNET_SERVER = {v:k for k,v in sc.TOPO["serves"].items()}   # 10.x.y -> node

def owner_of_ip(ip):
    return sc.TOPO["ip_owner"].get(ip) or SUBNET_SERVER.get(".".join(ip.split(".")[:3]))

_SO_MARK = getattr(socket, "SO_MARK", 36)
_LOCAL_PROXY_REDIRECT = int(os.environ.get("FROGSIM_LOCAL_PROXY_PORT", "0"))  # this node's proxy :80 handler

_MARKS = {}   # fileno -> mark (socket objects forbid custom attrs)
_real_setsockopt = socket.socket.setsockopt
def _rec_setsockopt(self, level, optname, value, *a):
    # iptables data plane: record SO_MARK=1 (discovery/proxy-upstream DIRECT intent).
    # The faked network can't honor a real mark, so we record it (keyed by fileno,
    # set right before connect) and report success (STRICT_MARK stays happy).
    if level == socket.SOL_SOCKET and optname == _SO_MARK:
        try: _MARKS[self.fileno()] = int(value)
        except Exception: pass
        return
    return _real_setsockopt(self, level, optname, value, *a)

_real_connect = socket.socket.connect
def _gated_connect(self, addr):
    try:
        host, port = addr[0], int(addr[1])
    except Exception:
        return _real_connect(self, addr)
    if isinstance(host, str) and host.startswith("10.") and port in (80, 9009):
        via, dev, src = fabric._route_get(NODE, host)
        if dev is None:
            raise ConnectionRefusedError(111, f"[fabric] {NODE} has NO ROUTE to {host} (route-gated)")
        owner = owner_of_ip(host)
        if owner is None or owner not in PORTS:
            raise ConnectionRefusedError(111, f"[fabric] no owner node for {host}")
        # RETURN-PATH gating: the peer must be able to route back to OUR source addr,
        # or the reply is lost. Depth-2+ nodes source from a borrowed uplink lease that
        # the far side never learned -> this is where the real chain silently fails.
        if src:
            rvia, rdev, _ = fabric._route_get(owner, src)
            if rdev is None:
                raise ConnectionRefusedError(111,
                  f"[fabric] RETURN-PATH FAIL: {owner} cannot route back to {NODE} src {src}")
        return _real_connect(self, ("127.0.0.1", PORTS[owner]))
    return _real_connect(self, addr)

def install():
    socket.socket.setsockopt = _rec_setsockopt
    socket.socket.connect = _gated_connect
    sys.stderr.write(f"[fabric] transport installed for node={NODE} (iptables :80 model on)\n")
