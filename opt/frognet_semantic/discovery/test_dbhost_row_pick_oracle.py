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
"""test_dbhost_row_pick_oracle.py

Two things this pins, both measured on the live pond 2026-09-14 (databasehost
alternating Seattle7 <-> HappyDog every 2-4 minutes, on Seattle3B and BABox
together, while Seattle7 itself never re-merged):

A. [ROW_PICK_IS_NOT_READ_ORDER_V1] When a host holds more than one capability
   row, which row survives must not depend on the order the database returned
   them. The old code computed `ts = _clock() - r["age_s"]` with _clock() called
   per row, so rows sharing an integer age_s were ordered by microseconds of
   loop drift: the last row read won. T.get issues no ORDER BY, so that is not
   stable between passes. Different rows carry different capability blobs and
   score differently -> the winner moves with nothing but read order.

B. [NO_MEM_AVAILABLE_V1] score() must not read mem_available_kb at all. It was
   a fallback under mem_total_kb, and frognet_capability_probe.sh:181 publishes
   mem_total_kb=0 on its /proc/meminfo exception path -- so the fallback was
   reachable in production and swapped the dominant RAM term for a load-varying
   one.

Fail-on-old is asserted directly: plane A drives the SAME rows in both orders
and requires one answer; the old implementation returns whichever came last.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import frognet_role_elect as RE          # noqa: E402
from core.database_handler import DatabaseRoleHandler  # noqa: E402

FAILS = []
PASSES = 0


def check(ok, what):
    global PASSES
    if ok:
        PASSES += 1
        print(f"  [PASS] {what}")
    else:
        FAILS.append(what)
        print(f"  [FAIL] {what}")


def _row(name, ip, ts_env, age_s, **cap):
    cap.setdefault("mysql_running", 1)
    cap.setdefault("cores", 4)
    cap.setdefault("cpu_mhz", 1800)
    cap.setdefault("disk_class", "ssd")
    cap.setdefault("disk_free_gb", 50.0)
    cap["lan_ip"] = ip
    return {"var": "databasehost", "scope": "capability", "name": name,
            "addr": ip, "value": {"capability": cap},
            "ts_env": ts_env, "age_s": age_s}


# One host, two persisted rows written in the SAME second - so identical age_s,
# which is what the old ordering had nothing left to sort on. Different blobs,
# so the two rows score differently.
ROWS = [
    _row("SD:capability.host:10.170.170.1:databasehost#a", "10.170.170.1",
         1789400000, 30, mem_total_kb=2 * 1024 * 1024),     # small
    _row("SD:capability.host:10.170.170.1:databasehost#b", "10.170.170.1",
         1789400000, 30, mem_total_kb=16 * 1024 * 1024),    # large
]


def gather(rows):
    """Run gather_candidates against a stubbed store returning `rows` verbatim."""
    real_get = RE.T.get

    def fake_get(var, scope, dbhost=None, fresh_s=None, **kw):
        if scope == "capability":
            return list(rows)
        return []                      # no reach_plane rows: WAN bucket, fine here

    RE.T.get = fake_get
    try:
        hosts, _lan = RE.gather_candidates(DatabaseRoleHandler(),
                                           dbhost="databasehost_control.frognet",
                                           lan_subnets=[], local_ips=[],
                                           report={})
        return hosts
    finally:
        RE.T.get = real_get


def plane_row_order():
    print("=== PLANE A: the surviving row does not depend on read order ===")
    fwd = gather(ROWS)
    rev = gather(list(reversed(ROWS)))

    check(len(fwd) == 1 and len(rev) == 1,
          "one host with two rows collapses to one candidate")
    if not (fwd and rev):
        return

    f, r = fwd[0], rev[0]
    check(f.get("mem_total_kb") == r.get("mem_total_kb"),
          f"same row picked either way (fwd mem_total_kb={f.get('mem_total_kb')}, "
          f"rev={r.get('mem_total_kb')})")
    check(f.get("_row_name") == r.get("_row_name"),
          f"same row identity either way ({f.get('_row_name','?')[-2:]} vs "
          f"{r.get('_row_name','?')[-2:]})")

    h = DatabaseRoleHandler()
    check(h.score(f) == h.score(r),
          f"same score either way ({h.score(f)} vs {h.score(r)})")

    # and repeated passes are stable, which is the property the pond lost
    scores = {h.score(gather(ROWS)[0]) for _ in range(5)}
    scores |= {h.score(gather(list(reversed(ROWS)))[0]) for _ in range(5)}
    check(len(scores) == 1, f"ten passes in both orders yield one score: {scores}")

    # newer envelope must still win outright
    newer = [ROWS[0],
             _row("SD:capability.host:10.170.170.1:databasehost#c", "10.170.170.1",
                  1789400500, 5, mem_total_kb=32 * 1024 * 1024)]
    got = gather(newer)[0]
    check(got.get("mem_total_kb") == 32 * 1024 * 1024,
          "a genuinely newer ts_env still wins")
    got2 = gather(list(reversed(newer)))[0]
    check(got2.get("mem_total_kb") == 32 * 1024 * 1024,
          "...in either read order")


def plane_no_mem_available():
    print("\n=== PLANE B: score() ignores mem_available_kb ===")
    h = DatabaseRoleHandler()
    base = dict(mysql_running=1, cores=4, cpu_mhz=1800,
                disk_class="ssd", disk_free_gb=50.0, lan_ip="10.1.1.1")

    busy = dict(base, mem_total_kb=8 * 1024 * 1024, mem_available_kb=200 * 1024)
    idle = dict(base, mem_total_kb=8 * 1024 * 1024, mem_available_kb=7 * 1024 * 1024)
    check(h.score(busy) == h.score(idle),
          f"installed RAM equal -> equal score regardless of free ({h.score(busy)})")

    # the reachable production path: probe failed to read /proc/meminfo
    z_busy = dict(base, mem_total_kb=0, mem_available_kb=200 * 1024)
    z_idle = dict(base, mem_total_kb=0, mem_available_kb=7 * 1024 * 1024)
    check(h.score(z_busy) == h.score(z_idle),
          f"mem_total_kb=0 no longer falls back to a moving input "
          f"({h.score(z_busy)})")

    check(h.score(z_idle) < h.score(idle),
          "a candidate that published no installed RAM scores lower, not higher")

    # AST, not a text search: a text search matches the comment that explains
    # why the field is gone, which is exactly the false alarm that would train
    # someone to delete this check.
    import ast
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "core", "database_handler.py"),
        encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "score")
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]                       # drop the docstring
    consts = {n.value for stmt in body for n in ast.walk(stmt)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    check("mem_available_kb" not in consts,
          "score() does not reference mem_available_kb (AST, comments excluded)")


if __name__ == "__main__":
    plane_row_order()
    plane_no_mem_available()
    print()
    if FAILS:
        print(f"DBHOST ROW-PICK ORACLE: {len(FAILS)} FAILED, {PASSES} passed")
        for f in FAILS:
            print(f"  - {f}")
        sys.exit(1)
    print(f"ALL DBHOST ROW-PICK ORACLE CHECKPOINTS PASS ({PASSES})")
    sys.exit(0)
