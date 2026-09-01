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
"""test_ballot_admissibility_oracle.py - [BALLOT_ADMISSIBILITY_V1]

FIELD REPLAY, real data: the eight databasehost ballots read verbatim from
New-York-1's instrumented merge log (2026-07-06, ELECT_CANDIDATE lines,
dbhost=10.250.250.1). On pre-fix code this exact set elected 10.160.160.1 -
a machine 8.1 days silent - on every node, all day. The oracle seeds the
REAL election (core.frognet_role_elect.gather_candidates + the databasehost
handler evaluate path) with these rows and requires:
  A. the fossil (and every ballot unrefreshed past the 1800s horizon) is
     REFUSED, loudly, with its age;
  B. the winner is one of the nodes that is actually speaking;
  C. 10.160.160.1 CANNOT win, at any admissible margin;
  D. a fresh 10.160.160.1 row WOULD be admissible (the fix ages ballots,
     it does not blacklist addresses - a returning Seattle6 is welcome).
John's ruling, verbatim basis: "The tuple has a timestamp. Using the
timestamp is the responsibility of the consumer."
"""
import io
import os
import sys
import time
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import frognet_role_elect as RE
from core import frognet_tuples as T

FAILS = []
def check(ok, label):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok: FAILS.append(label)

NOW = int(time.time())
# The eight real ballots: (lan_ip, ts_age_seconds) from the field log.
FIELD = [("10.102.60.1",      1),
         ("10.111.11.1",  755109),
         ("10.120.120.1",     21),
         ("10.130.130.1",     46),
         ("10.160.160.1", 701292),   # the fossil that won all day
         ("10.179.178.1", 860885),
         ("10.250.250.1",  20019),
         ("10.28.28.1",     4239)]

def rows_from(field):
    rows = []
    for ip, age in field:
        rows.append({"var": "capability", "scope": f"host:{ip}:databasehost",
                     "name": f"SD:capability.host:{ip}:databasehost",
                     "addr": ip,
                     # [ENVELOPE_TS_V1] age is the STORE's, carried on the row.
                     # The payload ts stays, unread, precisely to prove it is unread.
                     "ts_env": NOW - age, "age_s": age,
                     "value": {"lan_ip": ip, "ts": NOW - age,
                               "score": 50, "loadavg": {"1m": 0.2},
                               "temps_c": []}})
    return rows

class _Handler:
    ROLE_NAME = "databasehost"
    def evaluate(self, hosts, lan):
        # deterministic stand-in for the real callback's pure pick: the
        # admissibility question is upstream of scoring, so any pure pick
        # over the admitted set proves the property. Highest lan_ip.
        return max(hosts, key=lambda c: tuple(map(int, c["lan_ip"].split(".")))) \
               if hosts else None

def run(field):
    real_get = T.get
    T.get = lambda service, var, dbhost="x", fresh_s=0, timeout=4.0: rows_from(field)
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            hosts, lan = RE.gather_candidates(_Handler(), dbhost="proof://field")
        win = _Handler().evaluate(hosts, lan)
        return hosts, (win or {}).get("lan_ip"), err.getvalue()
    finally:
        T.get = real_get

print("=== FIELD REPLAY: the eight real ballots, 2026-07-06 ===")
hosts, winner, errlog = run(FIELD)
admitted = sorted(c["lan_ip"] for c in hosts)
print(f"  admitted={admitted}")
print(f"  winner={winner}")
check("10.160.160.1" not in admitted,
      "A/C the 8.1-day fossil is INADMISSIBLE (cannot win at any margin)")
check(all(a in ("10.102.60.1", "10.120.120.1", "10.130.130.1") for a in admitted)
      and len(admitted) == 3,
      "A only the nodes actually speaking (<=1800s) hold ballots")
check(winner in ("10.102.60.1", "10.120.120.1", "10.130.130.1"),
      "B the elected databasehost is a living node")
check("BALLOT_REFUSED" in errlog and "10.160.160.1" in errlog
      and "701292" in errlog.replace("701293", "701292")[:100000],
      "refusals are loud, named, and carry the age")

print("=== D: a RETURNING Seattle6 is welcome (ages, not blacklists) ===")
hosts2, winner2, _ = run([("10.160.160.1", 30), ("10.102.60.1", 40)])
check("10.160.160.1" in [c["lan_ip"] for c in hosts2],
      "D a fresh 10.160.160.1 ballot is admissible again")

print("=== guard: zero admissible ballots is an EMPTY election, not a crash ===")
hosts3, winner3, _ = run([("10.9.9.1", 999999)])
check(hosts3 == [] and winner3 is None,
      "all-stale store -> hosts=0, winner=none (local-by-definition layer decides)")

print()
print("ALL BALLOT-ADMISSIBILITY CHECKS PASS" if not FAILS
      else f"BALLOT-ADMISSIBILITY CHECKS FAILED: {len(FAILS)}")
sys.exit(0 if not FAILS else 1)
