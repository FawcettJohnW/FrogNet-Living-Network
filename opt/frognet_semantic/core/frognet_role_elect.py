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
import os
import sys
import time
from typing import List, Dict, Optional

import sys

from core import frognet_tuples as T

_clock = time.time   # [BALLOT_ADMISSIBILITY_V1] injectable clock for deterministic oracles

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
    """A real, electable host address: 10/8, four octets, and NOT a reserved plane -
    10.253.x are synthetic cross-NAT transit addresses and 10.254.x are chorus
    addresses, neither of which is a real host, so both are excluded from BOTH lists.
    Candidates need NOT be .1 - a specialist advertises at its own address. Excluding
    10.254 here matters for the media/game elections the same way it does for
    databasehost: a corrupted identity that landed in the chorus plane must never be
    an electable mediahost/boardgame candidate."""
    if not ip or not ip.startswith("10."):
        return False
    o = ip.split(".")
    return len(o) == 4 and o[0] == "10" and o[1] not in ("253", "254")


def _slash24(ip: str) -> str:
    o = ip.split(".")
    return ".".join(o[:3]) + ".0/24" if len(o) == 4 else ""


TUNNEL_PLANE = "10.253."      # wg /30 transit addresses: a hop here crossed a tunnel


def trace_hops(ip: str, max_hops: int = 8, wait: int = 1) -> Optional[List[str]]:
    """The hops to `ip`, from this node's own kernel. None if the trace could not run.

    Not a probe of the destination - a look at the PATH. That distinction is the whole
    point: a node with no tunnels of its own still crosses somebody else's, and nothing
    in its own routing table or its own reach_plane says so. Seattle6 owns no wg
    interface, so its reach_plane is empty and every destination looks local to it,
    including New-York-1. The trace shows the 10.253 hop where the path enters
    Seattle5's tunnel.
    """
    import subprocess
    try:
        # -I: ICMP echo, NOT the default UDP mode. UDP traceroute needs the
        # DESTINATION to answer port-unreachable, and Linux rate-limits ICMP errors
        # to one per second per host (net.ipv4.icmp_ratelimit=1000,
        # icmp_ratemask=6168 has the dest-unreachable bit set). The election traces
        # several candidates and re-traces every merge, so back-to-back probes to the
        # same box alternate between a clean trace and all stars - measured, exactly
        # alternating - which would drop a real LAN node out of the pool every other
        # merge and flap the media host. An echo reply is not covered by that mask.
        r = subprocess.run(["traceroute", "-n", "-I", "-q", "1", "-w", str(wait),
                            "-m", str(max_hops), ip],
                           capture_output=True, text=True, timeout=max_hops * wait + 5)
    except Exception as e:
        print(f"[ROLE_ELECT] TRACE ip={ip} unavailable err={e!r}",
              file=sys.stderr, flush=True)
        return None
    hops: List[str] = []
    for line in r.stdout.splitlines()[1:]:
        for tok in line.split():
            if tok.count(".") == 3 and tok.replace(".", "").isdigit():
                hops.append(tok)
                break
    return hops


# [CAPABILITY_DOES_NOT_AGE_V1] A capability blob describes the MACHINE -- cores,
# disk, RAM, camera, mic. Hardware does not expire, so a timestamp on it answers
# no question anyone is asking. Capability rows may live forever.
#
# What the election actually needed from the age gate was never freshness, it was
# LIVENESS: do not elect a box that is not there. That is a different question and
# it gets its own answer -- ask the candidate, on the port that decides liveness
# everywhere else in FrogNet.
# [CAPABILITY_DOES_NOT_AGE_V1] The reachability probe that lived here is gone.
# See gather_candidates: a per-node measurement cannot produce a pond-wide
# agreement, however correct each measurement is.


def is_on_lan(ip: str, local_ips=None, hops=None) -> bool:
    """[LAN_IS_UNTUNNELLED_V1] A node is on the LAN if reaching it does not traverse a
    tunnel and it is not a loopback.

    [SELF_IS_ALWAYS_LAN_V1] With one thing that is not a probe result: THIS NODE is on
    its own LAN, by definition, and is never traced to establish it. You do not prove
    reachability to yourself.

    Without that, a node had to traceroute its own .1 to qualify as its own candidate.
    On a box with no traceroute binary, or with ICMP echo to self filtered, every
    candidate INCLUDING ITSELF came back False, the LAN pool was empty, and the node
    elected no media host at all - reporting no media host on a LAN it is the only
    member of. Seattle3, 2026-08-01. The rule it violated is stated in
    role_barrier_ready: "A lone FrogNetHost is trivially ready for every role... so a
    stand-alone node elects ITSELF for everything."

    A core election must not be able to fail because a diagnostic tool is missing. The
    trace decides who ELSE is local; it has no business deciding whether you exist.

    Beyond that the rule is the only rule: a hop in the 10.253 transit plane means the
    path crossed a tunnel, a hop returning to one of this node's own addresses is a
    loop, and no path means no LAN.
    """
    mine = set(local_ips or ())
    if not mine:
        try:
            mine = {T.my_ip()}
        except Exception:
            mine = set()
    if ip in mine:
        return True
    if hops is None:
        hops = trace_hops(ip)
    if not hops:
        return False
    for h in hops:
        if h.startswith(TUNNEL_PLANE):
            return False
    for h in hops[:-1]:
        if h in mine:
            return False
    return True


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


def gather_candidates(handler, dbhost: str = "databasehost_control.frognet",
                      lan_subnets=None, local_ips=None):
    """Build BOTH candidate lists from capability memory in ONE pass:
      hosts_list - every REAL 10/8 host (253 excluded), LAN and WAN alike: the
                   pond-wide / federated set. databasehost elects over this.
      lan_list   - only the DIRECTLY-ATTACHED hosts. mediahost elects over this -
                   A/V must stay on this node's own LAN, never on a relayed or
                   overlay path.
    [LAN_IS_ATTACHED_V1] LAN means a candidate whose /24 is one of THIS node's
    directly-attached interface segments (`lan_subnets`, as '10.x.y.0/24'). The old
    test was "/24 NOT in the wg-WAN plane", which wrongly bucketed a node reached
    over a ROUTED RELAY (no wg winner, so absent from wan_subnets) as LAN - e.g. a
    gateway with no tunnels reaches the whole pond via a neighbor and called all of
    it LAN, so an off-LAN box could win the LAN mediahost. Membership in the attached
    set is the only correct LAN test. When `lan_subnets` is None (older callers /
    sims), fall back to the legacy wg-plane heuristic.
    UNIFIED source: anyone who can serve this role writes its capability under
    `<role>/capability` at its own address. We read every row once and attach '_perf'
    from the blob's own fresh loadavg/temps (self-contained - no second read). The only
    I/O is reading tuples; never probes a node. Returns (hosts_list, lan_list)."""
    role = getattr(handler, "ROLE_NAME", None) or getattr(handler, "role", "")
    wan = _wan_subnets(dbhost)
    lan_set = set(lan_subnets) if lan_subnets is not None else None
    _lan_memo: Dict[str, bool] = {}             # one trace per candidate per merge
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
            # [CAPABILITY_DOES_NOT_AGE_V1] ts is used for ONE thing only, below:
            # picking which row wins when a host has more than one
            # ([DBHOST_DETERMINISTIC_ROW_V1]). It is a tiebreak, not a gate.
            # A missing envelope no longer disqualifies anything -- refusing a
            # ballot for having no timestamp is an age judgement by another name,
            # and it took out every node pond-wide against an older api.php.
            try:
                _env = r.get("age_s")
                ts = 0.0 if _env is None else float(_clock() - _env)
            except (TypeError, ValueError):
                ts = 0.0
            if ip in by_ip and by_ip[ip][0] >= ts:
                continue                              # keep the newer row already held
            # [CAPABILITY_DOES_NOT_AGE_V1] The age gate is GONE. Superseded
            # [BALLOT_ADMISSIBILITY_V1], which refused any ballot past a max-age.
            #
            # It was answering the right question with the wrong evidence. The
            # 8.1-day fossil that won the databasehost election for a full day
            # was not a stale DESCRIPTION -- that box still had its cores. It was
            # an UNREACHABLE box, and the fix is to ask whether it is there, not
            # how recently it wrote. Age only correlated with liveness, and the
            # correlation broke in both directions: a live node whose advertiser
            # write timed out for half an hour was refused (measured, mediahost
            # 10.28.28.1, ts_age=2040s), while a dead node that wrote a minute
            # before dying was admitted.
            #
            # Capability rows may now live forever. Reachability is checked
            # below, once per candidate per gather.
            c = dict(cap)
            c["lan_ip"] = ip
            c["_ts"] = ts                 # [ELECT_EVIDENCE_V1] ride along for the ballot dump
            c["_row_name"] = r.get("name", "?")   # writer's scope (SensorName)
            c["_row_addr"] = r.get("addr", "?")   # writer's recorded address
            # live load/temps ride inside the same self-contained blob
            c["_perf"] = {"loadavg": blob.get("loadavg") or cap.get("loadavg") or {},
                          "temps_c": blob.get("temps_c") or cap.get("temps_c") or []}
            by_ip[ip] = (ts, c)
        # deterministic emit order: by IP, so the lists are identical on every node
        for ip in sorted(by_ip):
            c = by_ip[ip][1]
            # [CAPABILITY_DOES_NOT_AGE_V1] No reachability probe here. There was
            # one and it was wrong.
            #
            # apply_to_etc_hosts states the rule this file has to keep: "there is
            # NO floor and NO per-node reachability cull: the determination is
            # identical on every node BY CONSTRUCTION." A local TCP probe is a
            # PER-NODE input. Every node probes from its own vantage, gets a
            # different pool, and elects a different winner -- which is exactly
            # the split measured 2026-08-08, three simultaneous answers for
            # databasehost across six nodes.
            #
            # Correct without it: the pool is the capability rows in the
            # database, every node reads the same rows from
            # databasehost_control.frognet, and a dead node's rows are removed by
            # the reaper. Liveness is already a SHARED fact. It does not need to
            # be re-measured locally, and it must not be, because a fact each
            # node measures for itself is not a fact they can agree on.
            hosts_list.append(c)            # entire/WAN-inclusive set -> databasehost
            # [LAN_IS_UNTUNNELLED_V1] Ask the path. Traced once per candidate per
            # merge and memoised, because this runs inside the merge.
            if ip not in _lan_memo:
                _lan_memo[ip] = is_on_lan(ip, local_ips=local_ips)
            is_lan = _lan_memo[ip]
            if is_lan:
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
