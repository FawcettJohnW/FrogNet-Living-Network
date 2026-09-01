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
host_records - /etc/hosts from the transient DB, and the self-record each node writes.

Replaces the vouch-and-gate naming path. Every node authors ONE record (its own
name + subnet + .1) into the control plane; /etc/hosts is the FULL set of those
records with NO route gate. Naming is the identity plane (the table); routing is
the reachability plane (the walk). A named host with no installed route resolves
and rides the default - it is not dropped. This removes the premise of both
HOSTS_GATE (no_route_to_subnet drops a name) and the whole VOUCH machinery
(hearsay about who exists): existence is self-asserted, never vouched.

Record: service="frognet_echo", scope=node_scope() (host:<ip>, one row per node),
value={"name","subnet","dot1","ts"}, written own=False so it outlives the merge
process and ages out by ts like the capability tuples.
"""
from __future__ import annotations
import time

from core import frognet_tuples as _T
from .hosts import addhost_lines

ECHO_SERVICE = "frognet_echo"
CONTROL = "databasehost_control.frognet"
# A self-record names a host while fresh; older than this it ages out of the file
# (the node stopped writing => gone). Generous vs the ~5-min capability re-assert.
DEFAULT_FRESH_S = 1800


def write_self(name: str, subnet: str, dot1: str,
               dbhost: str = CONTROL, ts=None) -> bool:
    """This node authors its own identity record. Call every merge. own=False so
    it survives the process and ages by ts (a corpse stops writing and drops out
    of every reader's fresh window on its own - no reaper, no vouch)."""
    val = {"name": name, "subnet": subnet, "dot1": dot1,
           "ts": int(ts) if ts is not None else int(time.time())}
    return _T.put(ECHO_SERVICE, "host", _T.node_scope(), val,
                  dbhost=dbhost, own=False)


def read_all(dbhost: str = CONTROL, fresh_s: int = DEFAULT_FRESH_S):
    """Every node's self-record, freshest-per-node. Returns
    [{"dot1","name","subnet"}]. Stale (un-reasserted) records age out via fresh_s.
    DB-unreachable returns [] (never raises into the merge) - a brief control-host
    outage degrades to "no records this pass," not a crashed merge."""
    try:
        rows = _T.get_all(ECHO_SERVICE, dbhost=dbhost, fresh_s=fresh_s)
    except Exception:
        return []
    best = {}   # dot1 -> (ts, record)
    for r in rows:
        v = r.get("value") or {}
        dot1 = v.get("dot1") or r.get("addr") or ""
        name = v.get("name") or ""
        if not dot1 or not name:
            continue
        try:
            ts = int(v.get("ts", 0) or 0)
        except (TypeError, ValueError):
            ts = 0
        if dot1 not in best or ts > best[dot1][0]:
            best[dot1] = (ts, {"dot1": dot1, "name": name,
                               "subnet": v.get("subnet", "")})
    return [rec for _ts, rec in best.values()]


def hosts_lines(records):
    """The FULL /etc/hosts frognet block from records - NO route gate. Every known
    host is named; reachability is the walk's separate concern. Deterministic
    order (by .1) so the file is byte-stable across passes and never churns the
    HOSTS_KEEP/rewrite decision."""
    out = []
    seen = set()
    for rec in sorted(records, key=lambda r: _ipkey(r["dot1"])):
        d1 = rec.get("dot1") or ""
        name = rec.get("name") or ""
        if not d1 or not name or d1 in seen:
            continue
        seen.add(d1)
        out.extend(addhost_lines(name, d1))
    return out


def build_from_db(dbhost: str = CONTROL, fresh_s: int = DEFAULT_FRESH_S):
    """Convenience: read the record set and render the /etc/hosts frognet block in
    one call. The whole naming path, gate-free."""
    return hosts_lines(read_all(dbhost=dbhost, fresh_s=fresh_s))


def _ipkey(ip):
    try:
        return tuple(int(o) for o in ip.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0, 0)
