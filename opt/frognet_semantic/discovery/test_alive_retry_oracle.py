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
"""Oracle - [ALIVE_9009_RETRY_V1] a transient :9009 miss must not reap a route.

Field case (New-York-2 runMerge, 2026-06-20): the 10.130.130 (Seattle3) transit
verified inconsistently across merge passes - VERIFY_OK, then a walk FAIL_ECHO
(:9009 verdict=none), then VERIFY_SUSPECT, then FAIL_ECHO again. Each missed pong
dropped the measured candidate to vouch-only, so the single-winner roles
(mediahost/boardgame) flapped pass to pass. The :9009 ping-pong is acknowledged
to false-negative on high-RTT/tunnel paths, yet one miss at the walk was
destructive.

Fix: the walk RETRIES a negative verdict (None / alive False) before believing
it; a decisive verdict (float rtt, "LOOP", alive True) is never retried. A truly
dead path fails all tries (gate unchanged).

This drives the REAL Discovery._alive_measure / _alive_ok with a flapping verify
backend (None-then-float per target, like the field). Fail-on-old is the
single-try regime (FROGNET_ALIVE_9009_TRIES=1); pass-on-new is the default 3.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore)


class FlappingVerify:
    """measure_or_loop/alive that MISS the first `misses` calls per (target,dev),
    then answer. Models the high-RTT false-negative the field showed."""
    def __init__(self, misses=1, rtt=200.0, loops=None):
        self.misses = misses
        self.rtt = rtt
        self.loops = set(loops or ())
        self._n = {}

    def _count(self, key):
        self._n[key] = self._n.get(key, 0) + 1
        return self._n[key]

    def measure_or_loop(self, target, dev):
        if target in self.loops or (target, dev) in self.loops:
            return "LOOP"
        return self.rtt if self._count(("m", target, dev)) > self.misses else None

    def alive(self, dest1, dev):
        return self._count(("a", dest1, dev)) > self.misses


def _disc(verify):
    k = FakeKernel()
    routes = Routes(k, clock=lambda: 0.0)
    return Discovery(routes, FakeEcho(answers={}), FakeRtt(table={}),
                     FakeGetHosts(children={}), FakeBroker(), HostStore(),
                     local_ips={"10.28.28.1", "127.0.0.1"},
                     dev_src_map={}, self_identity="10.28.28.1", verify=verify)


T, DEV = "10.130.130.2", "eth1"   # Seattle3 admin alias over the NY-1 transit


def run():
    fails = []

    def check(cond, label):
        if not cond:
            fails.append(label)

    # ---- OLD regime: a single try -> the first miss is believed -> dropped ----
    os.environ["FROGNET_ALIVE_9009_TRIES"] = "1"
    d = _disc(FlappingVerify(misses=1))
    check(d._alive_measure(T, DEV) is None,
          "OLD (tries=1): one miss -> None (candidate dropped) [fail-on-old]")
    d = _disc(FlappingVerify(misses=1))
    check(d._alive_ok("10.130.130.1", DEV) is False,
          "OLD (tries=1): one miss -> alive False")

    # ---- NEW regime: retry absorbs the transient miss -> route kept ----
    os.environ["FROGNET_ALIVE_9009_TRIES"] = "3"
    d = _disc(FlappingVerify(misses=1, rtt=200.0))
    v = d._alive_measure(T, DEV)
    check(v == 200.0,
          f"NEW: one transient miss recovered -> rtt kept (got {v})")
    d = _disc(FlappingVerify(misses=1))
    check(d._alive_ok("10.130.130.1", DEV) is True,
          "NEW: one transient miss recovered -> alive True")

    # two misses then answer, still within 3 tries
    d = _disc(FlappingVerify(misses=2, rtt=150.0))
    check(d._alive_measure(T, DEV) == 150.0,
          "NEW: two transient misses still recovered within 3 tries")

    # ---- a LOOP is decisive and must NOT be retried away ----
    d = _disc(FlappingVerify(misses=1, loops={T}))
    check(d._alive_measure(T, DEV) == "LOOP",
          "LOOP returned immediately (a hairpin is never a transient miss)")

    # ---- a genuinely dead path fails all tries (gate unchanged) ----
    os.environ["FROGNET_ALIVE_9009_TRIES"] = "3"
    dead = FlappingVerify(misses=999)   # never answers
    d = _disc(dead)
    check(d._alive_measure(T, DEV) is None, "dead path -> None after retries")
    check(dead._n[("m", T, DEV)] == 3, "dead path probed exactly tries=3 times")
    d = _disc(FlappingVerify(misses=999))
    check(d._alive_ok("10.130.130.1", DEV) is False, "dead path -> alive False")

    # ---- a healthy path answers first try, no wasted retries ----
    healthy = FlappingVerify(misses=0, rtt=12.0)
    d = _disc(healthy)
    check(d._alive_measure(T, DEV) == 12.0, "healthy -> rtt")
    check(healthy._n[("m", T, DEV)] == 1, "healthy probed exactly once (no waste)")

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS: one transient :9009 miss is absorbed (route kept); LOOP and dead "
          "paths unchanged; healthy paths probed once. tries=1 reproduces the old "
          "drop (fail-on-old).")
    return 0


if __name__ == "__main__":
    sys.exit(run())
