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
frognet_role_elect - generic role election MECHANICS. Knows nothing about what any
role wants; it gathers the candidate lists from capability memory and asks a
RoleHandler which candidate is preferred. Memory, not messages: reads tuples,
never probes a node.

  gather_candidates(handler) -> (hosts_list, lan_list)   WAN-incl + LAN-only
  gather_lan_candidates(handler) -> [candidate dicts]     (LAN list, back-compat)
  elect(handler) -> preferred candidate dict | None       (gather + handler.evaluate)

The LAN/WAN split is NOT computed here and NOT re-probed here. The loopback
detector already ran in the discovery walk; the merge persisted its verdict (the
wg* winner /24s = the WAN plane) into the `discovery/reach_plane` tuple. We READ
that tuple. This file owns the mechanics (gather, apply winner); the handler owns
the criteria (score/evaluate). A new role is a new handler; this file never changes.
"""
from __future__ import annotations
from typing import List, Dict, Optional

import sys

from core import frognet_tuples as T

SYSTEM_SENSOR = "System"      # .1 hosts publish capability in the System/Perf sensor

# [CAPABILITY_FRESH_RETIRED - 2026-06-21] The election read no longer ages tuples by
# ts (see gather_candidates [CAPABILITY_READ_NO_AGE_V1]). A ts cutoff in the read was a
# liveness PROXY that predated the measured reachability gate; it only made each node
# age the same persisted DB at a different wall-clock instant, producing the
# quiescent-net databasehost split-brain. Liveness is the consumer's call, decided by
# [SERVICE_ELECT_REACHABLE_V1] (did the candidate's /24 win a route THIS merge), not by
# the read. Do NOT reintroduce a fresh_s>0 into the capability read. Kept (unused) only
# so an old import doesn't break; no reader consults it.
CAPABILITY_FRESH_S = 0  # retired: read aging removed; reachability owns liveness


def _is_real_host(ip: str) -> bool:
    """A real, electable host address: 10/8, four octets, and NOT the 10.253.x plane -
    those are synthetic cross-NAT transit addresses, not real hosts, so they are
    excluded from BOTH lists. Candidates need NOT be .1 - a specialist advertises at
    its own address."""
    if not ip or not ip.startswith("10."):
        return False
    o = ip.split(".")
    return len(o) == 4 and not (o[0] == "10" and o[1] == "253")


def _slash24(ip: str) -> str:
    o = ip.split(".")
    return ".".join(o[:3]) + ".0/24" if len(o) == 4 else ""


def _wan_subnets(dbhost: str = "databasehost_control.frognet"):
    """Read THIS node's loopback-validated WAN /24 set from the `discovery/reach_plane`
    tuple the merge wrote (computed once per merge from the walk's wg* winners - the
    candidates that trip loopback / are reached across the overlay). Memory, not
    messages: we READ the precomputed verdict, NEVER re-probe. Match the tuple THIS
    node wrote (SensorAddress == our own IP) and take the freshest. Empty set on any
    miss -> no candidate is classified WAN (conservative: media stays inclusive; the
    split self-corrects on the next merge)."""
    try:
        me = T.my_ip()
        best, best_ts = set(), -1
        for r in T.get("discovery", "reach_plane", dbhost=dbhost):
            v = r.get("value", {}) or {}
            if (r.get("addr") or v.get("self_ip")) != me:
                continue
            ts = int(v.get("ts", 0) or 0)
            if ts >= best_ts:
                best_ts, best = ts, set(v.get("wan_subnets", []) or [])
        return best
    except Exception:
        return set()


def gather_candidates(handler, dbhost: str = "databasehost_control.frognet"):
    """Build BOTH candidate lists from capability memory in ONE pass:
      hosts_list - every REAL 10/8 host (253 excluded), LAN and WAN alike: the
                   pond-wide / federated set. databasehost elects over this.
      lan_list   - only the LOCAL hosts (whose /24 is NOT in this node's WAN plane).
                   mediahost elects over this - A/V must stay on-LAN, off the overlay.
    The LAN/WAN split is decided by the LOOPBACK DETECTOR, but it already ran in the
    walk: a WAN candidate trips loopback, its /24 lands in the reach_plane tuple's
    wan_subnets, and we just READ that here - no re-probe. Standalone island: no wg
    winners, so wan_subnets is empty and both lists equal the local set (DB and media
    both elect locally, correct). UNIFIED source: anyone who can serve this role - a
    .1 FrogNetHost OR a dropped-in specialist - writes its capability under
    `<role>/capability` at its own address. We read every row once and attach '_perf'
    from the blob's own fresh loadavg/temps (self-contained - no second read). The only
    I/O is reading tuples; never probes a node. Returns (hosts_list, lan_list)."""
    role = getattr(handler, "ROLE_NAME", None) or getattr(handler, "role", "")
    wan = _wan_subnets(dbhost)
    hosts_list: List[Dict] = []
    lan_list: List[Dict] = []
    by_ip: Dict[str, tuple] = {}     # ip -> (ts, candidate)
    try:
        # [CAPABILITY_READ_NO_AGE_V1] fresh_s=0: the READ never ages a tuple. A
        # capability tuple persists until a newer value overwrites it (the 30-min
        # reaper is GC, not election aging).
        # [DBHOST_DETERMINISTIC_ROW_V1] With no aging, a host can have more than one
        # persisted capability row (per-pid/per-publish). First-by-DB-order would let
        # two nodes pick DIFFERENT rows for the same IP and score them differently, so
        # the winner splits even with a load-free score. Keep the NEWEST-ts row per IP:
        # a deterministic choice every node makes identically off the same DB.
        for r in T.get(role, "capability", dbhost=dbhost, fresh_s=0):
            blob = r.get("value", {}) or {}
            cap = blob.get("capability") or blob      # tolerate {capability:{...}} or flat
            ip = cap.get("lan_ip") or r.get("addr", "")
            if not _is_real_host(ip):
                continue
            try:
                ts = float(blob.get("ts") or cap.get("ts") or 0)
            except (TypeError, ValueError):
                ts = 0.0
            if ip in by_ip and by_ip[ip][0] >= ts:
                continue                              # keep the newer row already held
            c = dict(cap)
            c["lan_ip"] = ip
            # live load/temps ride inside the same self-contained blob
            c["_perf"] = {"loadavg": blob.get("loadavg") or cap.get("loadavg") or {},
                          "temps_c": blob.get("temps_c") or cap.get("temps_c") or []}
            by_ip[ip] = (ts, c)
        # deterministic emit order: by IP, so the lists are identical on every node
        for ip in sorted(by_ip):
            c = by_ip[ip][1]
            hosts_list.append(c)            # entire/WAN-inclusive set -> databasehost
            if _slash24(ip) not in wan:     # not on the WAN plane -> mediahost (LAN)
                lan_list.append(c)
    except Exception as e:
        # [GATHER_NOT_SILENT_V1] Never collapse a read failure into a silent empty set -
        # that turns "couldn't read" into "no candidates" with no trace, which is exactly
        # how a single malformed row hid as mediahost hosts=0. Log it; the accumulated
        # (possibly partial) lists still return so one role can't wedge the whole merge.
        try:
            print(f"[ROLE_ELECT] gather_candidates role={role} read_exc err={e!r}",
                  file=sys.stderr, flush=True)
        except Exception:
            pass
    return hosts_list, lan_list


def gather_lan_candidates(handler, dbhost: str = "databasehost_control.frognet") -> List[Dict]:
    """Back-compat shim: just the LAN list (real local hosts, off the WAN plane)."""
    return gather_candidates(handler, dbhost=dbhost)[1]


def elect(handler, dbhost: str = "databasehost_control.frognet") -> Optional[Dict]:
    """Gather BOTH candidate lists and let the handler pick. The handler's pure
    evaluate(hosts_list, lan_list) chooses which plane its role lives on: databasehost
    scores the pond-wide hosts_list (WAN-inclusive), mediahost the LAN-only lan_list.
    Returns the preferred candidate dict (with lan_ip), or None if neither list has an
    eligible candidate. `handler` comes from format_registry.role_handler()."""
    hosts_list, lan_list = gather_candidates(handler, dbhost=dbhost)
    if not hosts_list and not lan_list:
        return None
    return handler.evaluate(hosts_list, lan_list)


def elect_role(role_name: str, dbhost: str = "databasehost_control.frognet") -> Optional[Dict]:
    """Convenience: look the handler up in the registry by role name and elect."""
    try:
        from core.role_registry import role_handler
    except Exception:
        try:
            from role_registry import role_handler
        except Exception:
            try:
                from core.format_registry import role_handler
            except Exception:
                try:
                    from format_registry import role_handler
                except Exception:
                    return None
    h = role_handler(role_name)
    return elect(h, dbhost=dbhost) if h else None
