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
flaky - per-dest liveness timestamps + the FLAKY / OFFLINE reap gate.

A route to a .1 whose dest echoes intermittently must NOT be reaped on a single
failed pass. It is FLAKY: hold the installed route as long as the dest passed
"once in a while." Only a SUSTAINED failure - no successful echo for longer than
the offline window - is a real device-offline event, and only that reaps.

State is a per-dest last-good-echo epoch, persisted so the verdict survives a
pass. Anything that proves a dest's .1 reachable (walk echo, promote alive-check,
tunnel ping-pong health) calls mark_alive(dest); the reap consults classify().

Threshold: FROGNET_FLAKY_OFFLINE_SECS (default 45s). "A fail for more than a few
seconds is offline" - but the real floor is the interval between alive probes:
last-good is only refreshed when SOMETHING probes the dest, so the window must
exceed the probe cadence, or one missed probe reaps a live host. With merges
~10s apart, keep it at a few multiples; with a faster ping-pong health loop
feeding mark_alive it can be a literal few seconds. This is a state question and
ultimately belongs in the transient DB (per-node self-record); the local sentinel
here is the bootstrap-floor copy for the reap that runs before the DB is read.
"""
import os
import time

STORE = os.environ.get("FROGNET_ROUTE_ALIVE_STORE", "/etc/sentinels/route_alive.tsv")
OFFLINE_SECS = float(os.environ.get("FROGNET_FLAKY_OFFLINE_SECS", "45"))


def _load():
    d = {}
    try:
        with open(STORE) as f:
            for ln in f:
                p = ln.split()
                if len(p) >= 2:
                    try:
                        d[p[0]] = float(p[1])
                    except ValueError:
                        pass
    except OSError:
        pass
    return d


def _save(d):
    try:
        os.makedirs(os.path.dirname(STORE), exist_ok=True)
        tmp = STORE + ".tmp"
        with open(tmp, "w") as f:
            for k, v in d.items():
                f.write(f"{k}\t{v:.3f}\n")
        os.replace(tmp, STORE)
    except OSError:
        pass


def mark_alive(dest, now=None):
    """Record that `dest` echoed alive just now. Call from EVERY place a .1 is
    proven reachable - that history is what lets a later miss read as FLAKY
    instead of OFFLINE."""
    d = _load()
    d[dest] = now if now is not None else time.time()
    _save(d)


def classify(dest, alive_now, now=None, offline_secs=None):
    """Reap-gate verdict for a /24 that is NOT a fresh winner this pass.

    alive_now : result of probing this dest's .1 right now - True if it echoed,
                False/None if it did not.
    Returns:
      "ALIVE"   -> hold; the stamp was just refreshed.
      "FLAKY"   -> hold; failed now but passed within the offline window.
      "OFFLINE" -> reap; no pass within the offline window (device gone).
      "UNKNOWN" -> reap; never seen alive, nothing proves it was ever real.
    """
    now = now if now is not None else time.time()
    win = OFFLINE_SECS if offline_secs is None else offline_secs
    if alive_now:
        mark_alive(dest, now)
        return "ALIVE"
    last = _load().get(dest)
    if last is None:
        return "UNKNOWN"
    return "FLAKY" if (now - last) <= win else "OFFLINE"


def forget(dest):
    """Drop a dest's history - e.g. after a deliberate reap so a later
    reappearance starts clean."""
    d = _load()
    if dest in d:
        del d[dest]
        _save(d)
