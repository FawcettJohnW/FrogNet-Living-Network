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
sim_converge.py -- [ONE_DERIVATION_V1] does it settle, and stay settled?

The system as it actually is: one program, many threads, one shared memory.
Each thread writes only what it alone knows, reads everything, and derives the
rate with the SAME pure function. No message is sent. Nothing waits on anybody.

This is the test that would have caught nearly every regression of 2026-08-11,
because every one of them was two controllers moving one knob, each reading the
other's effect as fresh evidence. A single-participant test cannot see that.
Two threads over one store can.

  X1  two ends converge on one rate, and both hold it
  X2  a slow consumer pulls the whole call down, and it stays down
  X3  it stops where the link carries, not at the floor
  X4  a third participant joining does not disturb a settled call
  X5  the slowest SENDER bounds everybody
  X6  no oscillation: a handful of changes over a long run, not one per cycle
  X7  every participant agrees across the settled tail
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


import comms_control as CC

LADDER = [{"w": 1920, "h": 1080}, {"w": 1280, "h": 720}, {"w": 854, "h": 480},
          {"w": 640, "h": 360}, {"w": 480, "h": 360}, {"w": 320, "h": 240},
          {"w": 160, "h": 120}]
DERIVE = CC.ControlPlane.derive_rate
BIG = 1920 * 1080 * 24


class Store:
    """The shared memory. One lock, last write wins -- like the real one."""

    def __init__(self):
        self._rows = {}
        self._lk = threading.Lock()

    def put(self, user, session, fact, value):
        with self._lk:
            self._rows.setdefault((user, session), {})[fact] = dict(value)

    def state(self, session):
        with self._lk:
            rows = [(u, dict(f)) for (u, s), f in self._rows.items()
                    if s == session]
        senders, getters = [], []
        for u, f in rows:
            if "sending" in f:
                senders.append(dict(f["sending"], user=u))
            if "getting" in f:
                getters.append(dict(f["getting"], user=u))
        return {"senders": senders, "getters": getters}


class Participant(threading.Thread):
    """One end. Writes what it knows, reads everything, derives. Nothing else.

    `capacity` is what this end's link physically carries, in pixels/second. It
    is never published and never read by anybody -- it is the truth this end
    discovers by measuring, exactly as a real one does.
    """

    def __init__(self, name, store, session, capacity_pps, tick=0.02):
        super().__init__(daemon=True)
        self.name, self.store, self.session = name, store, session
        self.capacity = capacity_pps
        self.tick = tick
        self.rate = None
        self.history = []
        self.agreed = []
        self._halt = threading.Event()
        self._good = 0
        self._bad = 0
        self.too_big = 0
        self._steady_since = 0.0

    def stop(self):
        self._halt.set()

    def run(self):
        self._halt.clear()
        while not self._halt.is_set():
            # 1. write what only I know
            g = self.rate or LADDER[0]
            self.store.put(self.name, self.session, "sending",
                           {"w": g["w"], "h": g["h"], "fps": 24.0})
            if self.rate:
                asked = self.rate["w"] * self.rate["h"] * 24.0
                got = 24.0 * min(1.0, self.capacity / asked)
                ok = got >= 24.0 * 0.80
                self._good = self._good + 1 if ok else 0
                self._bad = 0 if ok else self._bad + 1
                # [A_RATE_THAT_FAILED_IS_NOT_A_CANDIDATE_V1] The sim keeps the
                # same durable fact the real report does. Without it the sim
                # measured a derivation that had no memory and oscillated --
                # which was true of the code at the time, and stayed true of
                # the sim after the code was fixed.
                struggling = self._bad >= 2
                happy = self._good >= 3
                px = self.rate["w"] * self.rate["h"]
                if struggling:
                    self.too_big = px if not self.too_big else min(self.too_big, px)
                    self._steady_since = 0.0
                elif happy and self.too_big:
                    # [A_CEILING_MUST_BE_ABLE_TO_LIFT_V1] One step per N steady
                    # reports -- lifting on EVERY one lifts faster than the link
                    # can be re-tested and the whole thing oscillates, which X6
                    # caught.
                    if not self._steady_since:
                        self._steady_since = time.time()
                    elif time.time() - self._steady_since >= CC.CEILING_LIFT_S:
                        self._steady_since = time.time()
                        self.too_big *= 2
                        if self.too_big >= 1920 * 1080:
                            self.too_big = 0
                self.store.put(self.name, self.session, "getting",
                               {"w": self.rate["w"], "h": self.rate["h"],
                                "fps": round(got, 1),
                                "happy": happy, "struggling": struggling,
                                "too_big": self.too_big})
            # 2. read everything   3. derive
            st = self.store.state(self.session)
            want = DERIVE(st, LADDER, self.rate)
            self.agreed.append(None if want is None else (want["w"], want["h"]))
            if want and want != self.rate:
                self.rate = dict(want)
                self.history.append((want["w"], want["h"]))
            self._halt.wait(self.tick)


def run_call(parts, seconds):
    for p in parts:
        if not p.is_alive():
            p.start()
    time.sleep(seconds)
    for p in parts:
        p.stop()
    for p in parts:
        p.join(timeout=2.0)


# ---- X1 --------------------------------------------------------------------
S = Store()
dave = Participant("Dave", S, "s1", BIG)
john = Participant("John", S, "s1", BIG)
run_call([dave, john], 2.0)
ck("X1 both ends settled on a rate", bool(dave.rate and john.rate),
   (dave.rate, john.rate))
ck("X1 and it is the SAME rate", dave.rate == john.rate, (dave.rate, john.rate))
ck("X1 on a link that carries it, that is the top rung",
   dave.rate == LADDER[0], dave.rate)

# ---- X2/X3/X6/X7 -----------------------------------------------------------
S = Store()
SLOW = 854 * 480 * 24
dave = Participant("Dave", S, "s2", BIG)
john = Participant("John", S, "s2", SLOW)
run_call([dave, john], 5.0)
ck("X2 a slow end pulls the whole call down",
   bool(dave.rate) and dave.rate["w"] <= 854, dave.rate)
ck("X2 and both ends are still agreed", dave.rate == john.rate,
   (dave.rate, john.rate))
ck("X3 it stops where the link carries, not at the floor",
   bool(dave.rate) and dave.rate["w"] >= 640, dave.rate)
ck("X6 it settles rather than hunting (%d cycles)" % len(dave.agreed),
   len(dave.history) <= 8, dave.history)
_tail = dave.agreed[-25:]
ck("X6 and the settled tail is one rate", len(set(_tail)) == 1,
   sorted(set(_tail)))
ck("X7 both ends agree across that tail",
   set(dave.agreed[-25:]) == set(john.agreed[-25:]),
   (sorted(set(dave.agreed[-25:])), sorted(set(john.agreed[-25:]))))

# ---- X4 --------------------------------------------------------------------
S = Store()
a = Participant("A", S, "s3", BIG)
b = Participant("B", S, "s3", BIG)
run_call([a, b], 2.0)
settled = dict(a.rate) if a.rate else None
c = Participant("C", S, "s3", BIG)
a2 = Participant("A", S, "s3", BIG)
a2.rate = dict(a.rate) if a.rate else None
b2 = Participant("B", S, "s3", BIG)
b2.rate = dict(b.rate) if b.rate else None
run_call([a2, b2, c], 2.0)
ck("X4 a third participant does not disturb a settled call",
   a2.rate == settled and c.rate == settled, (settled, a2.rate, c.rate))

# ---- X5 --------------------------------------------------------------------
S = Store()
# Lil is on a link that only carries 640x360, so it settles there by measuring
# -- not by being told, which is the whole point. Setting .rate directly tested
# nothing: the first loop iteration overwrites it from the derivation.
big = Participant("Big", S, "s4", BIG)
lil = Participant("Lil", S, "s4", 640 * 360 * 24)
run_call([big, lil], 5.0)
ck("X5 nobody sends faster than the slowest sender",
   bool(big.rate) and big.rate["w"] * big.rate["h"] <= 640 * 360, big.rate)

# ---- X8: [A_SENDER_BOUND_ALONE_CANNOT_CLIMB_V1] --------------------------
# With no receive reports the only input is what everyone is SENDING -- which
# is where they already are, so the answer is always "stay". The rate can then
# only ratchet down, one keyframe walk at a time, and never comes back.
# Measured 2026-08-11: both ends walked to 160x120, neither ever published a
# getting row, and it sat there for the rest of the call.
FLOOR = {"senders": [{"user": "D", "w": 160, "h": 120, "fps": 24.0},
                     {"user": "J", "w": 160, "h": 120, "fps": 24.0}],
         "getters": []}
_up = DERIVE(FLOOR, LADDER, None)
ck("X8 at the floor with nobody reporting, it tries one step up",
   _up and _up["w"] > 160, _up)
ck("X8 one step, not a leap to the top",
   _up == LADDER[LADDER.index({"w": 160, "h": 120}) - 1], _up)

_ceil = {"senders": FLOOR["senders"],
         "getters": [{"user": "J", "w": 160, "h": 120, "fps": 24.0,
                      "happy": True, "struggling": False,
                      "too_big": 320 * 240}]}
ck("X8 but a known-bad size still holds it down",
   DERIVE(_ceil, LADDER, None) == {"w": 160, "h": 120},
   DERIVE(_ceil, LADDER, None))

# and it must not undo convergence: a call that settled stays settled
_ok = {"senders": [{"user": "D", "w": 854, "h": 480, "fps": 24.0}],
       "getters": [{"user": "J", "w": 854, "h": 480, "fps": 23.5,
                    "happy": False, "struggling": False, "too_big": 1280 * 720}]}
ck("X8 a settled call with reports is unaffected",
   DERIVE(_ok, LADDER, None) == {"w": 854, "h": 480}, DERIVE(_ok, LADDER, None))

# ---- X9: [A_SHRINKING_READ_IS_NOT_NEWS_V1] --------------------------------
# The store 503s constantly and a half-succeeded request returns a SUBSET.
# Losing the other end's row removes the evidence holding the rate down, so it
# climbs; the next read finds it again and it drops back. Measured 2026-08-11,
# alternating every cycle for the length of the call:
#   call rate 160x120 -- derived from 2 sender(s), 1 report(s)
#   call rate 320x240 -- derived from 1 sender(s), 0 report(s)
# Nothing about the call changed either time.
def _thin_filter(counts, patience=3):
    """The rule as written: a thinner read waits for a full one, up to a point."""
    seen, thin, used = 0, 0, []
    for n in counts:
        if n < seen and thin < patience:
            thin += 1
            continue
        thin = 0
        seen = n
        used.append(n)
    return used

ck("X9 a one-cycle dropout is ignored",
   _thin_filter([3, 3, 1, 3, 3]) == [3, 3, 3, 3], _thin_filter([3, 3, 1, 3, 3]))
ck("X9 alternating dropouts never take effect",
   _thin_filter([3, 1, 3, 1, 3, 1, 3]) == [3, 3, 3, 3],
   _thin_filter([3, 1, 3, 1, 3, 1, 3]))
ck("X9 but a real departure is believed once it persists",
   _thin_filter([3, 1, 1, 1, 1, 1])[-1] == 1, _thin_filter([3, 1, 1, 1, 1, 1]))
ck("X9 and growth is never delayed",
   _thin_filter([1, 3]) == [1, 3], _thin_filter([1, 3]))

# ---- X10: [A_CEILING_MUST_BE_ABLE_TO_LIFT_V1] -----------------------------
# too_big cleared only on a report of HAPPY AT OR ABOVE the failed size -- which
# the rate is pinned below by that very ceiling, so that size is never sent and
# can never be proven good. The ceiling was permanent by construction. Measured
# 2026-08-11: one struggling report at 854x480 pinned the call at 160x120 for
# the rest of its life, on a gigabit link, zero drops, 23.4/24 fps throughout.
#
# A link that recovers must be able to climb back out.
S = Store()
TIGHT = 640 * 360 * 24
slow = Participant("Slow", S, "s5", TIGHT)
fast = Participant("Fast", S, "s5", BIG)
run_call([slow, fast], 4.0)
_pinned = dict(slow.rate) if slow.rate else None
ck("X10 a tight link settles low", _pinned and _pinned["w"] <= 854, _pinned)

# the link opens up: same participants, capacity restored
S2 = Store()
a10 = Participant("Slow", S2, "s6", BIG)
b10 = Participant("Fast", S2, "s6", BIG)
# carry the scar across: a10 has already failed at a big size
a10.too_big = 854 * 480
# Long enough for the ceiling to lift twice on the wall clock: the lift is in
# TIME by design, so a test shorter than CEILING_LIFT_S proves nothing about
# whether it lifts -- only that this test was impatient.
# The lift is in wall-clock time and the shipped interval is minutes, so
# running it at production speed makes a test that cannot finish. Scale the
# constant down for the run: what is under test is that the ceiling DOES lift
# and clears, not how long the operator waits.
_LIFT_REAL = CC.CEILING_LIFT_S
CC.CEILING_LIFT_S = 6.0
try:
    run_call([a10, b10], CC.CEILING_LIFT_S * 3.5)
finally:
    CC.CEILING_LIFT_S = _LIFT_REAL
ck("X10 and climbs back out once the link carries it",
   a10.rate and a10.rate["w"] * a10.rate["h"] >= 854 * 480,
   (a10.rate, a10.too_big))
ck("X10 both ends still agreed on where", a10.rate == b10.rate,
   (a10.rate, b10.rate))
ck("X10 and the scar is gone, not merely stepped past",
   a10.too_big == 0, a10.too_big)

# ---- X11: [SETTLE_BEFORE_YOU_CLIMB_V1] ------------------------------------
# Good windows alone are not enough to promote. Promoting changes the rate,
# which restarts the measurement, so a window count on its own gives: three
# good windows, climb, three more, climb again -- and a link that is fine at
# one rung and not at the next spends its life alternating. Reported
# 2026-08-12 as "switching a lot".
import fnav as _fn

ck("X11 the happy flag also requires wall-clock settling",
   _fn.Call.SETTLE_S >= 10.0, _fn.Call.SETTLE_S)
ck("X11 which is longer than the measurement horizon, or it decides nothing",
   _fn.Call.SETTLE_S > _fn.Call.RATE_HORIZON_S,
   (_fn.Call.SETTLE_S, _fn.Call.RATE_HORIZON_S))
ck("X11 forgiving a failed size takes LONGER than settling at the one below",
   CC.CEILING_LIFT_S > _fn.Call.SETTLE_S,
   (CC.CEILING_LIFT_S, _fn.Call.SETTLE_S))

# the alternator, run: a link fine at one rung and not at the next
def _alternate(settle_s, lift_s, secs=300.0, tick=2.0):
    t = changed = steady_since = 0.0
    good = flips = too_big = 0
    rate = 3
    for _ in range(int(secs / tick)):
        t += tick
        ok = rate >= 3
        good = good + 1 if ok else 0
        settled = (t - changed) >= settle_s if changed else True
        happy = good >= _fn.Call.HAPPY_WINDOWS and settled
        strug = not ok
        if strug:
            px = 1000 * (5 - rate)
            too_big = px if not too_big else min(too_big, px)
            steady_since = 0.0
        elif happy and too_big:
            if not steady_since:
                steady_since = t
            elif t - steady_since >= lift_s:
                steady_since = t
                too_big *= 2
            if too_big >= 1920 * 1080:
                too_big = 0
        new = rate
        if strug:
            new = rate + 1
        elif happy and rate > 0 and not (too_big and 1000 * (6 - rate) >= too_big):
            new = rate - 1
        if new != rate:
            rate, changed, good = new, t, 0
            flips += 1
    return flips

_loose = _alternate(0.0, 10.0)
_ship = _alternate(_fn.Call.SETTLE_S, CC.CEILING_LIFT_S)
ck("X11 the shipped timings churn less than no settle at all",
   _ship < _loose, (_ship, _loose))
ck("X11 and settle at a rate for a useful fraction of a minute",
   _ship <= 12, "%d changes in 300s" % _ship)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
