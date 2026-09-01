#!/opt/frognet_semantic/venv/bin/python3
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
daemon/pool_resizer.py - background pool sizer for the daemon.

Two threads, started by daemon_main at boot:

  1. Re-evaluation timer.  Wakes once per FROGNET_DB_REEVAL_INTERVAL
     seconds (default 3600), pulls stats from the TemplateStore pool,
     decides whether to grow / shrink / hold, calls pool.resize().

  2. Sentinel watcher.  Polls FROGNET_DB_REEVAL_TRIGGER (default
     /etc/sentinels/frognet_db_pool_reeval) once per minute.  If the
     file exists, removes it and triggers an immediate re-eval.

Heuristics (window = stats since previous re-eval):
  * p95 acquire wait > 100ms        -> grow 25%
  * p95 in-flight utilization < 50% -> shrink 25%
  * else hold

Bounds: never below sizing.lower_bound(), never above sizing.upper_bound().

Logging: one [DB-POOL-REEVAL] line per evaluation, regardless of action.
With the default hourly cadence that's 24 lines/day - useful for tuning.
"""

from __future__ import annotations

import os
import threading
import time

from core import sizing
from core.store import TemplateStore


_REEVAL_INTERVAL = float(os.environ.get("FROGNET_DB_REEVAL_INTERVAL", "3600"))
_SENTINEL_PATH   = os.environ.get(
    "FROGNET_DB_REEVAL_TRIGGER",
    "/etc/sentinels/frognet_db_pool_reeval",
)
_SENTINEL_POLL   = float(os.environ.get("FROGNET_DB_REEVAL_SENTINEL_POLL", "60"))

# Tuning knobs for the heuristic.
_GROW_WAIT_MS    = float(os.environ.get("FROGNET_DB_GROW_WAIT_MS", "100"))
_SHRINK_UTIL_PCT = float(os.environ.get("FROGNET_DB_SHRINK_UTIL_PCT", "50"))
_RESIZE_STEP_PCT = float(os.environ.get("FROGNET_DB_RESIZE_STEP_PCT", "25"))


def _decide_target(snap: dict) -> tuple[int, str]:
    """Given a stats snapshot, return (target_size, reason)."""
    cur = snap["size"]
    lo = sizing.lower_bound()
    hi = sizing.upper_bound()

    p95_wait_ms = snap.get("p95_wait_ms", 0)
    p95_util    = snap.get("p95_util", 0)
    util_pct    = (p95_util / cur * 100.0) if cur else 0.0

    step = max(1, int(round(cur * _RESIZE_STEP_PCT / 100.0)))

    # No data -> hold.  Avoids resizing on empty windows at startup.
    if snap.get("acquires", 0) == 0:
        return (cur, "no-traffic")

    if p95_wait_ms > _GROW_WAIT_MS:
        target = min(hi, cur + step)
        if target == cur:
            return (cur, f"grow-needed-but-at-ceiling({hi})")
        return (target, f"grow (p95_wait={p95_wait_ms:.1f}ms > {_GROW_WAIT_MS:.0f}ms)")

    if util_pct < _SHRINK_UTIL_PCT:
        target = max(lo, cur - step)
        if target == cur:
            return (cur, f"shrink-needed-but-at-floor({lo})")
        return (target, f"shrink (p95_util={util_pct:.0f}% < {_SHRINK_UTIL_PCT:.0f}%)")

    return (cur, "hold")


def _run_reeval(reason: str) -> None:
    pool = TemplateStore.get_pool()
    if pool is None:
        print(f"[DB-POOL-REEVAL] trigger={reason} pool=not-yet-initialized")
        return

    snap = pool.snapshot_and_reset()
    target, decision = _decide_target(snap)

    if target != snap["size"]:
        old, new = pool.resize(target)
        action = f"{old}->{new}"
    else:
        action = f"{snap['size']} (no change)"

    print(
        f"[DB-POOL-REEVAL] trigger={reason} "
        f"size={snap['size']} acquires={snap['acquires']} "
        f"p95_wait={snap['p95_wait_ms']:.1f}ms p95_util={snap['p95_util']} "
        f"stale_pings={snap['stale_pings']} "
        f"failed_acquires={snap['failed_acquires']} "
        f"healed={snap['healed_slots']} "
        f"decision={decision} action={action}"
    )


def _timer_loop():
    """Periodic re-evaluation."""
    while True:
        time.sleep(_REEVAL_INTERVAL)
        try:
            _run_reeval("timer")
        except Exception as e:
            print(f"[DB-POOL-REEVAL] timer error: {e!r}")


def _sentinel_loop():
    """Sentinel-file trigger.

    Rate-limited: even if the file is dropped repeatedly, we evaluate
    at most once per re-eval window.  Otherwise stats over a tiny
    window produce noisy decisions.
    """
    last_run = 0.0
    min_gap = max(60.0, _REEVAL_INTERVAL / 60.0)  # never more than ~1/min

    while True:
        time.sleep(_SENTINEL_POLL)
        try:
            if not os.path.exists(_SENTINEL_PATH):
                continue
            try:
                os.remove(_SENTINEL_PATH)
            except OSError as e:
                # Couldn't remove - log and skip rather than re-fire next minute.
                print(f"[DB-POOL-REEVAL] sentinel remove failed: {e!r}")
                continue

            now = time.monotonic()
            if now - last_run < min_gap:
                print(f"[DB-POOL-REEVAL] sentinel ignored (rate-limited, "
                      f"{now - last_run:.0f}s since last run)")
                continue
            last_run = now

            _run_reeval("sentinel")
        except Exception as e:
            print(f"[DB-POOL-REEVAL] sentinel error: {e!r}")


def start():
    """Spawn the timer and sentinel threads.  Idempotent."""
    if getattr(start, "_started", False):
        return
    start._started = True

    t1 = threading.Thread(target=_timer_loop, daemon=True, name="db-pool-reeval-timer")
    t1.start()

    # Make sure the sentinel directory exists; the user said it's 0777.
    sentinel_dir = os.path.dirname(_SENTINEL_PATH)
    if sentinel_dir and not os.path.isdir(sentinel_dir):
        try:
            os.makedirs(sentinel_dir, mode=0o777, exist_ok=True)
        except OSError as e:
            print(f"[DB-POOL-REEVAL] could not create {sentinel_dir}: {e!r}")

    t2 = threading.Thread(target=_sentinel_loop, daemon=True, name="db-pool-reeval-sentinel")
    t2.start()

    print(
        f"[DB-POOL-REEVAL] started "
        f"interval={_REEVAL_INTERVAL:.0f}s sentinel={_SENTINEL_PATH} "
        f"poll={_SENTINEL_POLL:.0f}s "
        f"grow_wait={_GROW_WAIT_MS:.0f}ms shrink_util={_SHRINK_UTIL_PCT:.0f}% "
        f"step={_RESIZE_STEP_PCT:.0f}%"
    )
