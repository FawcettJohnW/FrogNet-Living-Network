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
"""Oracle - [MEDIAHOST_ONSEGMENT_V1] mediahost prefers an ON-SEGMENT host.

Field case (New-York-2 runMerge, 2026-06-20): mediahost elected 10.130.130.1
(Seattle3) - a beefy box reached across a ~206ms transit hop - because it scored
highest on raw capability (56.8 vs NY2 24.9 vs NY1 2.1). For an A/V relay that is
backwards: a local box must beat a stronger one across a transit hop. Seattle3
slipped in because the lan/wan split only flags wireguard-OVERLAY hosts as remote;
Seattle3 is reached via transit, so it landed in bucket=lan and won on capability.
Both the old lan_list code and the pond-wide code pick it (lan_list == hosts_list
on this no-overlay island), so this is a pre-existing locality gap, not a
regression.

Fix: candidates are tagged `_onseg` (their /24 is one of the electing node's
directly-connected segments). mediahost elects among on-segment candidates when
any exist, else falls back to the whole set (never empty). databasehost ignores
the tag (no locality need). Co-segment nodes share the on-segment set, so they
agree - the earlier divergence fix is preserved.

This drives the REAL SotFMediaHandler.evaluate and DatabaseRoleHandler.evaluate
with the New-York-2 candidate set. Fail-on-old = the pre-fix pond-wide pick
(Seattle3); pass-on-new = the on-segment pick (10.28.28.1).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _mk(ip, bench, onseg=None):
    c = {"lan_ip": ip, "ffmpeg": True, "libvpx": True,
         "cpu_bench_total": bench,
         "mem_avail_kb": 4_000_000, "mem_available_kb": 4_000_000,
         "mysql_running": True, "disk_write_mbps": 20.0,
         "disk_fsync_ms": 10.0, "disk_free_gb": 12.0,
         "_perf": {"loadavg": {"1": 0.1}, "temps_c": [40]}}
    if onseg is not None:
        c["_onseg"] = onseg
    return c


def _pondwide_pick(handler, hosts_list):
    """Pre-fix behavior: best over the whole set, ignoring locality."""
    best = None
    for cand in hosts_list or []:
        ip = cand.get("lan_ip", "")
        if not ip:
            continue
        sc = handler.score(cand)
        if sc < 0:
            continue
        key = (sc, handler._ip_to_int(ip))
        if best is None or key > best[0]:
            best = (key, cand)
    return best[1] if best else None


def run():
    try:
        from core.sotf_handler import SotFMediaHandler
        from core.database_handler import DatabaseRoleHandler
    except Exception:
        from sotf_handler import SotFMediaHandler
        from database_handler import DatabaseRoleHandler

    media = SotFMediaHandler()
    db = DatabaseRoleHandler()
    fails = []

    def check(c, label):
        if not c:
            fails.append(label)

    # New-York-2's view. on-segment = {10.28.28, 10.102.60} (its own NICs).
    # Seattle3 is the strongest box but reached via transit (off-segment).
    onseg = {"10.28.28.0/24", "10.102.60.0/24"}

    def tag(c):
        c2 = dict(c)
        c2["_onseg"] = (".".join(c["lan_ip"].split(".")[:3]) + ".0/24") in onseg
        return c2

    seattle3 = _mk("10.130.130.1", bench=30000)   # off-seg, strongest
    ny2 = _mk("10.28.28.1", bench=14000)          # on-seg, mid
    ny1 = _mk("10.102.60.1", bench=3000)          # on-seg, weak
    cands = [tag(seattle3), tag(ny2), tag(ny1)]

    # sanity: raw capability really does favor the remote box
    check(media.score(seattle3) > media.score(ny2) > media.score(ny1) > 0,
          "score setup: seattle3 > ny2 > ny1 > 0")

    # ---- fail-on-old: pond-wide pick is the remote Seattle3 ----
    old = _pondwide_pick(media, cands)
    check(old and old["lan_ip"] == "10.130.130.1",
          "OLD pond-wide picks Seattle3 10.130.130.1 [fail-on-old]")

    # ---- pass-on-new: on-segment preference picks the best LOCAL host ----
    new = media.evaluate(cands, cands)
    check(new and new["lan_ip"] == "10.28.28.1",
          f"NEW mediahost picks on-segment 10.28.28.1 (got "
          f"{new['lan_ip'] if new else None})")
    check(new and new["lan_ip"] in ("10.102.60.1", "10.28.28.1"),
          "NEW mediahost is one of the two local hosts John named")

    # ---- databasehost is unaffected: still the global best (Seattle3) ----
    dbwin = db.evaluate(cands, cands)
    check(dbwin and dbwin["lan_ip"] == "10.130.130.1",
          f"databasehost still global -> Seattle3 (got "
          f"{dbwin['lan_ip'] if dbwin else None})")

    # ---- fallback: no on-segment candidate -> best overall, never empty ----
    remote_only = [tag(_mk("10.130.130.1", 30000)),
                   tag(_mk("10.111.11.1", 20000)),
                   tag(_mk("10.250.250.1", 25000))]
    fb = media.evaluate(remote_only, remote_only)
    check(fb and fb["lan_ip"] == "10.130.130.1",
          "fallback: no on-seg candidate -> best overall (never empty)")

    # ---- single on-segment candidate wins even if far weaker ----
    weak_local = [tag(_mk("10.130.130.1", 30000)),     # off-seg strong
                  tag(_mk("10.102.60.1", 1000))]        # on-seg very weak
    wl = media.evaluate(weak_local, weak_local)
    check(wl and wl["lan_ip"] == "10.102.60.1",
          "a weak on-segment host still beats a strong off-segment one")

    # ---- no on-segment signal at all (older caller) -> pond-wide behavior ----
    untagged = [_mk("10.130.130.1", 30000), _mk("10.28.28.1", 14000)]
    un = media.evaluate(untagged, untagged)
    check(un and un["lan_ip"] == "10.130.130.1",
          "no _onseg signal -> pond-wide (backward compatible)")

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS: mediahost elects the best ON-SEGMENT host (NY-2 -> 10.28.28.1, "
          "Seattle3 excluded); databasehost stays global; fallback never empty; "
          "untagged callers keep pond-wide behavior. Old pond-wide pick (Seattle3) "
          "is the fail-on-old.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
