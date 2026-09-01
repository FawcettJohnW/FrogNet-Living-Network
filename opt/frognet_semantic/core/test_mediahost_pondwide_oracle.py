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
"""Oracle - mediahost is a single NETWORK-WIDE winner, not a per-node LAN winner.

mediahost.frognet must resolve to the same box on every node, exactly like
databasehost.frognet. The handler used to elect over the per-node `lan_list`
(LAN-only, computed against each node's own WAN plane), so two nodes with
different WAN planes elected different mediahosts - divergent resolution. The fix
elects over the pond-wide `hosts_list`, identical to DatabaseRoleHandler.

Fail-on-old / pass-on-new is expressed by comparing the NEW handler against the
OLD selection (a local pick over lan_list) on a two-node topology:
  - OLD (lan_list): node A -> X, node B -> Y   (diverges)
  - NEW (hosts_list): node A -> Y, node B -> Y (converges, == databasehost)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _mk(ip, bench, ffmpeg=True, libvpx=True):
    return {"lan_ip": ip, "ffmpeg": ffmpeg, "libvpx": libvpx,
            "cpu_bench_total": bench, "mem_avail_kb": 4_000_000,
            "_perf": {"loadavg": {"1": 0.1}, "temps_c": [40]}}


def _old_media_pick(handler, lan_list):
    """The pre-fix behavior: best over the LAN-only list (what we replaced)."""
    best = None
    for cand in lan_list or []:
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

    # Two real hosts. Y out-scores X (higher measured CPU bench).
    X = _mk("10.130.130.1", bench=15000)   # Seattle3
    Y = _mk("10.120.120.1", bench=30000)   # Seattle2

    if not (media.score(Y) > media.score(X) > 0):
        fails.append(f"score setup wrong: X={media.score(X)} Y={media.score(Y)} "
                     f"(need both >0 and Y>X)")

    # Pond-wide candidate set is the SAME on every node (read from the control DB).
    hosts_list = [X, Y]
    # ... but each node's LAN list differs by its own WAN plane:
    nodeA_lan = [X]            # to A, Y's /24 is on the WAN plane
    nodeB_lan = [Y]            # to B, X's /24 is on the WAN plane

    # OLD behavior diverges across nodes.
    old_A = _old_media_pick(media, nodeA_lan)
    old_B = _old_media_pick(media, nodeB_lan)
    if not (old_A and old_B and old_A["lan_ip"] != old_B["lan_ip"]):
        fails.append(f"expected OLD lan-list election to DIVERGE; "
                     f"A={old_A and old_A['lan_ip']} B={old_B and old_B['lan_ip']}")

    # NEW behavior converges: same winner on both nodes, and it's the pond-wide best
    # by the media score (NOT the per-node LAN pick). We compare against databasehost
    # only structurally - it elects over hosts_list too - but it scores a different
    # capability tuple (mysql/disk), so we do NOT require the same winning box.
    new_A = media.evaluate(hosts_list, nodeA_lan)
    new_B = media.evaluate(hosts_list, nodeB_lan)
    expected = max(hosts_list, key=lambda c: (media.score(c), media._ip_to_int(c["lan_ip"])))
    if not (new_A and new_B and new_A["lan_ip"] == new_B["lan_ip"]):
        fails.append(f"NEW mediahost still diverges: "
                     f"A={new_A and new_A['lan_ip']} B={new_B and new_B['lan_ip']}")
    if not (new_A and new_A["lan_ip"] == expected["lan_ip"]):
        fails.append(f"NEW mediahost picked {new_A and new_A['lan_ip']}, expected pond-wide "
                     f"best {expected['lan_ip']} (must elect over hosts_list, not lan_list)")
    # and the switch is real: NEW (hosts_list) != OLD (lan_list) on node A
    if new_A and old_A and new_A["lan_ip"] == old_A["lan_ip"]:
        fails.append("NEW winner equals OLD lan-list winner on node A - election scope "
                     "did not actually move from lan_list to hosts_list")
    # databasehost elects over hosts_list too (structural parity check, db-eligible cands)
    dbX = dict(X, mysql_running=True, disk_write_mbps=5.0, fsync_ms=60.0,
               disk_free_gb=10.0, cpu_bench=15000)
    dbY = dict(Y, mysql_running=True, disk_write_mbps=8.0, fsync_ms=55.0,
               disk_free_gb=9.0, cpu_bench=30000)
    db_win = db.evaluate([dbX, dbY], [])
    if db_win is None:
        fails.append("databasehost evaluate returned None on db-eligible pond-wide set")

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1

    print(f"PASS: OLD diverged A={old_A['lan_ip']} B={old_B['lan_ip']}; "
          f"NEW converges A=B={new_A['lan_ip']} = pond-wide best (elects over hosts_list "
          f"like databasehost, winner={db_win['lan_ip']} on its own caps)")
    return 0


if __name__ == "__main__":
    sys.exit(run())
