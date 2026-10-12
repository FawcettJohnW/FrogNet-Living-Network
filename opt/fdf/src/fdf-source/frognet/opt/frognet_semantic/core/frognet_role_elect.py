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

  gather_candidates(handler) -> hosts_list               every real host, pond-wide
  elect(handler) -> preferred candidate dict | None       (gather + handler.evaluate)

This file owns the mechanics (gather, apply winner); the handler owns the criteria
(score/evaluate). A new role is a new handler; this file never changes.
"""
from __future__ import annotations
import os
import sys
import time
from typing import List, Dict, Optional

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
    Candidates need NOT be .1 - a specialist advertises at its own address. A corrupted
    identity that landed in the chorus plane must never be an electable candidate."""
    if not ip or not ip.startswith("10."):
        return False
    o = ip.split(".")
    return len(o) == 4 and o[0] == "10" and o[1] not in ("253", "254")


def _slash24(ip: str) -> str:
    o = ip.split(".")
    return ".".join(o[:3]) + ".0/24" if len(o) == 4 else ""


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


def gather_candidates(handler, dbhost: str = "databasehost_control.frognet", report=None):
    """Build the candidate list from capability memory in ONE pass: every REAL 10/8
    host (253/254 excluded), pond-wide. databasehost elects over this.
    [ONE_ROLE_V1] There is no LAN bucket: it existed for the retired A/V role (which
    had to stay on this node's own LAN) and cost a traceroute per candidate per merge.
    UNIFIED source: anyone who can serve this role writes its capability under
    `<role>/capability` at its own address. We read every row once and attach '_perf'
    from the blob's own fresh loadavg/temps (self-contained - no second read). The only
    I/O is reading tuples; never probes a node. Returns hosts_list."""
    role = getattr(handler, "ROLE_NAME", None) or getattr(handler, "role", "")
    # [NO_DEAD_STORE_READ_V1] `wan = _wan_subnets(dbhost)` stood here: a reach_plane read from the store on
    # databasehost_control, once per role per election, whose result nothing below used. In region mode it was the
    # one store read left on the election path -- against the databasehost, which is the host that has just died
    # when a switchover is due -- and its except clause swallowed the simulator's "store was read" assertion, so
    # dbhost_switch_sim.py passed while the read happened. Removed, not guarded.
    hosts_list: List[Dict] = []
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
        from core import region_capability as _RC          # [ELECTION_FROM_FACTS_V1] region mode: the members' facts
        _src = _RC.rows(role)
        for r in (_src if _src is not None else T.get(role, "capability", dbhost=dbhost, fresh_s=0)):
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
            # [ROW_PICK_IS_NOT_READ_ORDER_V1 - John 2026-09-14] Order rows by the
            # STORE's own envelope timestamp, never by the reader's clock.
            #
            # This was `ts = _clock() - r["age_s"]`, with _clock() called freshly
            # per row. age_s is whole seconds and a host's rows are written in
            # bursts, so rows for one IP routinely carry the SAME age_s -- and
            # then the comparison below was decided by the microseconds of
            # _clock() drift between loop iterations. The row the database
            # happened to return LAST won, and T.get issues no ORDER BY, so that
            # is not stable across queries. Different rows carry different
            # capability blobs and score differently, which is precisely the
            # split [DBHOST_DETERMINISTIC_ROW_V1] exists to prevent -- and it
            # became reachable when [CAPABILITY_DOES_NOT_AGE_V1] let rows live
            # forever, so hosts accumulate several.
            #
            # ts_env is the UpdatedAt envelope the store recorded. It is absolute,
            # identical for every reader, and does not move between passes. A real
            # tie (same envelope second) breaks on the SensorName, which is a
            # property of the row rather than of when it was read.
            try:
                _env = r.get("ts_env")
                ts = 0.0 if _env is None else float(_env)
            except (TypeError, ValueError):
                ts = 0.0
            _key = (ts, str(r.get("name", "")))
            if ip in by_ip and by_ip[ip][0] >= _key:
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
            # write timed out for half an hour was refused (measured,
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
            by_ip[ip] = (_key, c)
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
    except Exception as e:
        # [GATHER_NOT_SILENT_V1] Never collapse a read failure into a silent empty set -
        # that turns "couldn't read" into "no candidates" with no trace, which is exactly
        # how a single malformed row hid as hosts=0. Log it; the accumulated
        # (possibly partial) list still returns so one role can't wedge the whole merge.
        #
        # [READ_FAILED_IS_NOT_EMPTY_V1 - John 2026-09-12] Logging it was not enough. The
        # caller got an empty list and could not tell it apart from a store that
        # answered and held nothing -- so a store returning HTTP 500 looked exactly like
        # a pond where nobody had published, and the election "decided" on no
        # information at all. Report it, so the caller can decline to have an opinion.
        if report is not None:
            report["read_failed"] = True
            report["read_error"] = f"{type(e).__name__}: {e}"
        try:
            print(f"[ROLE_ELECT] gather_candidates role={role} read_exc err={e!r}",
                  file=sys.stderr, flush=True)
        except Exception:
            pass
    return hosts_list


def elect(handler, dbhost: str = "databasehost_control.frognet") -> Optional[Dict]:
    """Gather the candidate list and let the handler's pure evaluate(hosts_list) pick.
    Returns the preferred candidate dict (with lan_ip), or None if there is no
    eligible candidate. `handler` comes from core.role_registry.role_handler()."""
    hosts_list = gather_candidates(handler, dbhost=dbhost)
    if not hosts_list:
        return None
    return handler.evaluate(hosts_list)


def elect_role(role_name: str, dbhost: str = "databasehost_control.frognet") -> Optional[Dict]:
    """Convenience: look the handler up in the registry by role name and elect."""
    from core.role_registry import role_handler
    h = role_handler(role_name)
    return elect(h, dbhost=dbhost) if h else None
