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
frognet_avhost - the FLOATING media-host ROLE, as UnREST memory (not messages).

Naming, precisely: FrogNetHost.<domain> IS the node identity and is ALWAYS the LAN's
10.x.x.1 - it never moves. A ROLE name (databasehost.frognet, mediahost.frognet) is a
separate name that maps to whichever machine currently HOLDS that role. Today those
role names happen to point at the .1, but they need not: a specialist machine on the
LAN - a heavy DB box, an AI host, a sensor platform - can register as a candidate for
a role at its OWN non-.1 address and WIN it on merit. When it does, the role name maps
to that machine while FrogNetHost.<domain> stays the .1. Identity and role assignment
are fully decoupled.

The media-host is such a role. It is re-evaluated at the END of a converged, change-
bearing runMerge pass (the _dirty branch that calls propogateNotification) - NOT a
heartbeat. SAME/DIFF makes re-eval free when the winner didn't change.

Election scores TWO sources together, LAN-only (10.253 WAN transit excluded):
  1. the .1 FrogNetHosts - via the System/Perf sensor's `capability` block, and
  2. registered specialist candidates - via <Role>Candidate tuples (register_candidate).
Score = static capability ceiling x live load; best wins; highest IP only as the
tiebreaker. A dropped-in DB/AV box therefore beats the .1 if it is genuinely better.

Connection discipline (memory, not messages):
  - To connect: read the role tuple, establish the FNWP link, stream.
  - A merge may re-elect the role and rewrite the tuple, but an EXISTING HEALTHY
    stream is NOT interrupted - the tuple is where you look when you need to connect.
  - On failure: re-read the tuple (now possibly post-merge) and re-establish.
"""
from __future__ import annotations
import time
from typing import Optional, Tuple

import frognet_tuples as T

SERVICE = "communicator"
SERVICE_SYS = "System"        # live perf sensor - now also carries the static
                              # `capability` block (sysperf_outputter extended), so
                              # one read gives both load and capability.
AV_PORT_DEFAULT = 9000


def _ip_to_int(ip: str) -> int:
    try:
        a, b, c, d = (int(x) for x in ip.split("."))
        return (a << 24) | (b << 16) | (c << 8) | d
    except Exception:
        return 0


def register_candidate(role: str, address: str, capability: dict,
                       dbhost: str = "databasehost_control.frognet") -> bool:
    """A specialist machine advertises itself as a candidate for a ROLE. This is how a
    non-.1 box - a heavy DB machine, an AI host, a sensor platform - 'runs just enough
    FrogNet' to be considered: it publishes a <Role>Candidate tuple at ITS OWN LAN
    address carrying its capability block. It does NOT claim FrogNetHost or a .1; the
    .1 identity is untouched. The role election scores this candidate alongside the
    .1 hosts, and if it wins, the ROLE NAME (e.g. databasehost.frognet) maps to this
    machine's address while FrogNetHost.<domain> stays the .1.

      role    -> "DatabaseCandidate" | "MediaCandidate" | "AIHost" | "SensorPlatform"
      address -> this machine's real LAN 10/8 address (need NOT be a .1)
      capability -> {cores, cpu_mhz, mem_total_kb, ffmpeg, libvpx, hw_encoder, mysql,
                     mysql_innodb_buffer_pool_size, ...}
    """
    cap = dict(capability)
    cap.setdefault("lan_ip", address)
    return T.put(role, "Candidate", T.host_scope(address),
                 {"capability": cap, "ts": int(time.time())}, dbhost=dbhost)


def elect_host(dbhost: str = "databasehost_control.frognet"):
    """Elect the best LAN media-host. Criteria live in the MediaRoleHandler; the
    gather-and-apply mechanics live in frognet_role_elect. Returns (addr, port) or
    None. Memory, not messages: consults capability tuples, never probes a node."""
    try:
        from frognet_role_elect import elect_role
    except Exception:
        return None
    winner = elect_role("mediahost", dbhost=dbhost)   # registry handler evaluates the list
    if not winner or not winner.get("lan_ip"):
        return None
    return (winner["lan_ip"], int(winner.get("av_port", winner.get("port", AV_PORT_DEFAULT))))

def reevaluate(port: int = AV_PORT_DEFAULT,
               dbhost: str = "databasehost_control.frognet") -> Optional[Tuple[str, int]]:
    """Called at the END of a converged, change-bearing runMerge pass. Runs the
    capability-scored, LAN-filtered election over AVCap + System sensors and SETS the
    host tuple. SAME/DIFF makes this free when the winner didn't change. The election
    is deterministic, so every node computes the same winner. Returns (addr,port)."""
    win = elect_host(dbhost=dbhost)
    if not win:
        return None
    addr, p = win
    T.put(SERVICE, "avhost", "current",
          {"address": addr, "port": int(p), "ts": int(time.time())}, dbhost=dbhost)
    return (addr, int(p))


def resolve_host(dbhost: str = "databasehost_control.frognet") -> Optional[Tuple[str, int]]:
    """Read the current media-host from memory - what a sender or viewer consults to
    find where to connect. Falls back to a live election if the tuple isn't present
    yet (e.g. immediately after a float, before re-eval ran)."""
    for r in T.get(SERVICE, "avhost", dbhost=dbhost):
        v = r.get("value", {})
        if v.get("address"):
            return (v["address"], int(v.get("port", AV_PORT_DEFAULT)))
    return elect_host(dbhost=dbhost)


if __name__ == "__main__":
    # Invoked from runMerge.bash on the converged dirty pass:
    #   frognet_avhost.py reeval   -> re-elect + set tuple (SAME/DIFF)
    #   frognet_avhost.py resolve  -> print current host
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "resolve"
    if cmd == "reeval":
        print("avhost reeval ->", reevaluate())
    else:
        print("avhost resolved ->", resolve_host())
