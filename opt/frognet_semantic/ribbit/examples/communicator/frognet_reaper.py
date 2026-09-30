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
frognet_reaper.py - the tuple reaper: the GUARANTEE behind atexit.

A tuple is alive only while its writer keeps re-asserting it (every variable
carries a ts, stamped by frognet_tuples.put). A writer that exited cleanly
deletes its own tuples via atexit - but atexit does NOT fire on SIGKILL, a
crash, power loss, or a hard reboot, which on a contested mesh is exactly when
nodes die. The reaper closes that gap: it sweeps the transient and deletes any
tuple whose ts is older than the reap age (default 30 min), by SensorID
(SensorData cascades). What atexit misses, the reaper collects.

Coupling note: the reap age must always exceed the SLOWEST re-assert interval in
the system, or a live node's still-valid tuple gets reaped. The beacon/caps
heartbeat is 5 min, well inside 30. If any future variable re-asserts slower
than ~25 min, raise REAP_AGE_S accordingly - the threshold and the heartbeats
are one coupled decision.

Run on/near the transient (re-resolves databasehost.frognet so it reaps wherever
the transient currently lives):
    python3 frognet_reaper.py
    python3 frognet_reaper.py --dbhost databasehost.frognet --age 1800 --interval 300 --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time

import frognet_tuples as T

REAP_AGE_S = 30 * 60          # 30 minutes
SWEEP_INTERVAL_S = 300        # check every 5 min


def sweep(dbhost: str, age_s: int, dry_run: bool = False) -> int:
    """One pass: reap stale SERVICE VARIABLES only. A tuple is a reap candidate iff
    its SensorName carries the SD: marker (ephemeral coordination state). Observed
    sensor data (DHT/GPS/System/LinkState - no SD: prefix) is NEVER touched: a
    sensor that reports hourly is stale-but-valid, not dead. Among SD: tuples,
    anything older than age_s (writer stopped re-asserting - dead service or dead
    PID) is reaped by SensorID; SensorData cascades."""
    now = int(time.time())
    rows = T._values_raw(T.SERVICE_ANY, dbhost)        # all tuples; we filter by marker
    reaped = 0
    for row in rows:
        name = row.get("SensorName", "")
        if not name.startswith(T.SD_PREFIX):
            continue                                   # not a service variable - leave it
        data = row.get("data")
        ts = int(data.get("ts", 0) or 0) if isinstance(data, dict) else 0
        sid = row.get("SensorID")
        if sid is None:
            continue
        age = now - ts
        if ts == 0 or age > age_s:
            label = f"{row.get('SensorType','?')}/{name}"
            if dry_run:
                print(f"  would reap [{sid}] {label} (age {age}s)", flush=True)
                reaped += 1
            elif T._delete_by_id(dbhost, sid):
                print(f"  reaped [{sid}] {label} (age {age}s)", flush=True)
                reaped += 1
    return reaped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="frognet_reaper")
    ap.add_argument("--dbhost", default=T.DEFAULT_DBHOST,
                    help="transient host (re-resolved each sweep; follows the float)")
    ap.add_argument("--age", type=int, default=REAP_AGE_S,
                    help="reap tuples older than this many seconds (default 1800)")
    ap.add_argument("--interval", type=int, default=SWEEP_INTERVAL_S,
                    help="seconds between sweeps")
    ap.add_argument("--once", action="store_true", help="sweep once and exit")
    ap.add_argument("--dry-run", action="store_true", help="report, don't delete")
    a = ap.parse_args(argv)

    print(f"FrogNet reaper - sweeping {a.dbhost}, reap age {a.age}s, "
          f"every {a.interval}s{' [dry-run]' if a.dry_run else ''}", flush=True)
    while True:
        try:
            n = sweep(a.dbhost, a.age, a.dry_run)
            print(f"  sweep done: {n} reaped", flush=True)
        except Exception as e:
            print(f"  sweep error: {e}", flush=True)
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
