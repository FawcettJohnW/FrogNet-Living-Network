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
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
role_service.py -- [ROLES_EXERCISE_V1] what every Role's service is made of.

The design is "CroakOS Protocol Layer Demonstration: Requirements and Detailed Design" (version 0.2), sections 9, 10
and 12.2. A Role is a function in the exercise with a memory of its own, found by its name (exerciseHost.frognet, and <role>Host.exercise for every other Role).
A Role's service writes only into its own memory and learns everything else by a held read on another Role's memory.
Nothing here polls and nothing sleeps: every wait is frognet_held.Holder.wait_until, and every look at a memory is a
hold, whose reply is the memory's current rows.

A service does not import a driver, name a key or read a channel's setting. It names a memory by its host name and
nothing else about how it is reached; the tier fails the build if this directory names a protocol or a key.

An injected value stays (the design's 10.7, version 0.2). An injection is one tuple the Instructor writes in its own
memory, (instructor, injection, <n>) = {role, var, scope, value, from_step, state, by}. Every tuple is written only by
its owner, so nobody writes into another Role's memory: before a service writes a tuple it owns, it takes the
injections that name its Role and are active, and for each field one sets, the injected value is what it writes.

A Role's admin tool gives it an operator's input the same way: (<role>, console, <var>.<scope>) holds the fields the
operator set for that tuple (a sensor out of service, a processor out of service, the alert level), until a later one
says "off". The scenario's own console inputs are in the scenario file, which is what makes a run repeatable.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SEM = os.path.dirname(os.path.dirname(HERE))
for p in (SEM, os.path.join(SEM, "core")):
    if p not in sys.path:
        sys.path.insert(0, p)
import frognet_held as H  # noqa: E402
import frognet_ram as RAM  # noqa: E402
import frognet_tuples as T  # noqa: E402

BUILD = "roles-exercise-services-v2-20261121b"
ROLES = ("warning", "assessment", "compute", "command", "units", "exercise", "instructor")
RUN = "run"
HOLD_S = 300.0
SAY_WAITS = os.environ.get("FROGNET_ROLES_SAY") == "1"     # each wait says what it is for, in the service's own log


APP = "exercise"


def memory(role):
    """A Role's memory is an application memory: the application's general one for Exercise, a Team's for the rest."""
    return "%sHost.frognet" % APP if role == APP else "%sHost.%s" % (role, APP)


def index(rows, writers=None):
    """{(var, scope): value} of a hold's rows. A tuple's name is SD:<var>.<scope>.

    A memory keeps each writer's tuple of a name as that writer's own (a tuple is its service, its name and the
    address it was written from), so two writers of one name are two rows: the two processors' results for one track
    are. The reader decides which it means, and here that is the one for the later step. (Found 21 Nov at step 40 of
    the first full run: a reader that kept whichever row came last waited for ever on the other processor's.)
    writers, when given, is filled with {(var, scope): the address that row was written from}."""
    out, rank = {}, {}
    for r in rows or []:
        name = str(r.get("SensorName") or "")
        if not name.startswith("SD:") or "." not in name:
            continue
        var, scope = name[3:].split(".", 1)
        data = r.get("data")
        if isinstance(data, dict):
            k = (data.get("step") if isinstance(data.get("step"), int) else -1, int(r.get("SensorID") or 0))
            if (var, scope) not in out or k > rank[(var, scope)]:
                out[(var, scope)], rank[(var, scope)] = data, k
                if writers is not None:
                    writers[(var, scope)] = str(r.get("SensorAddress") or "")
    return out


def chebyshev(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


def _which(like):
    """One tuple by its name, or a family by its pattern. A read names no more of a memory than it needs: a reply
    lists every tuple it matches, and a list crosses whole when any of them has changed."""
    return {"name_like": like} if "%" in like else {"name": like}


class Role:
    def __init__(self, role, part=""):
        self.role, self.part = role, part
        self.who = role + (("." + part) if part else "")
        self.calls = self.failed = 0
        self.mine = {}                  # this Role's own memory as last looked at
        self.injections = {}            # "<var>.<scope>" -> [(n, the active injection naming this Role and that tuple)]
        self.t0 = time.time()
        self.ms = {}                    # a memory's Role -> the time each read of it or write into it took, ms

    def say(self, text):
        print("[ROLE %s] %.3f %s" % (self.who, time.time(), text), flush=True)

    # ---- reading: a hold's reply is the memory's current rows
    def look(self, role, like="SD:%", writers=None):
        self.calls += 1
        t = time.time()
        try:
            _hid, rows = H.holder().hold(role, memory(role), timeout=120.0, **_which(like))
        except Exception as e:
            self.failed += 1
            raise RuntimeError("%s: the read of %s (%s) failed: %s" % (self.who, memory(role), like, e))
        self.ms.setdefault(role, []).append((time.time() - t) * 1000)
        return index(rows, writers)

    def wait(self, role, like, done, what):
        """Held until done(the memory's matching tuples). The read is held again each time it returns undone: a hold
        whose time passes carries no work of its own."""
        box = {}
        if SAY_WAITS:
            self.say("waiting on %s %s for %s" % (memory(role), like, what))

        def _done(rows):
            box["v"] = index(rows)
            return bool(done(box["v"]))
        while True:
            self.calls += 1
            try:
                H.holder().wait_until(role, memory(role), _done, wait_s=HOLD_S, timeout=120.0, **_which(like))
            except Exception as e:
                self.failed += 1
                raise RuntimeError("%s: the held read on %s (%s), waiting for %s, failed: %s" % (self.who, memory(role), like, what, e))
            if done(box.get("v") or {}):
                return box["v"]

    def look_mine(self, *likes):
        """This Role's own memory (its admin tool's inputs, and the tuples of its own it names: a family, SD:<var>.%,
        or one tuple), and the Instructor's injections that name this Role and are active. A service calls this once
        the step's injections are written: Exercise publishes no ground truth before (instructor, applied, run) shows
        the step, and everything else in a step follows ground truth."""
        self.mine = {}
        for like in ("SD:console.%",) + likes:
            self.mine.update(self.look(self.role, like))
        self.injections = {}
        for (var, n), v in sorted(self.look("instructor", "SD:injection.%").items(), key=lambda kv: int(kv[0][1])):
            if var == "injection" and v.get("role") == self.role and v.get("state") == "active":
                self.injections.setdefault("%s.%s" % (v.get("var"), v.get("scope")), []).append((int(n), v))
        return self.mine

    def injected(self, var, scope, value):
        """value as this Role writes it: every field an active injection sets has the injected value (a later
        injection over an earlier one), and then the Role's own admin tool's input."""
        for _n, inj in self.injections.get("%s.%s" % (var, scope), ()):
            value = dict(value, **(inj.get("value") or {}))
        c = self.mine.get(("console", "%s.%s" % (var, scope)))
        if c and not c.get("off"):                       # the Role's own admin tool: an operator's input for this tuple
            value = dict(value, **{k: v for k, v in c.items() if k not in ("ts", "off")})
        return value

    # ---- writing: only into this Role's own memory
    def publish(self, var, scope, value, lifetime=RAM.DURABLE):
        value = self.injected(var, scope, value)
        self.calls += 1
        t = time.time()
        ok = RAM.upsert(self.role, var, str(scope), value, lifetime, dbhost=memory(self.role))
        self.ms.setdefault(self.role, []).append((time.time() - t) * 1000)
        if not ok:
            self.failed += 1
            raise RuntimeError("%s: the write of (%s, %s, %s) into %s failed" % (self.who, self.role, var, scope, memory(self.role)))
        return value

    def present(self):
        self.publish("present", "%s.%s.%d" % (T.my_ip(), self.part or "service", os.getpid()), {"build": BUILD}, RAM.EPHEMERAL)

    def ready(self, scenario):
        self.publish("ready", self.part or RUN, {"scenario": scenario["name"], "step": 0})

    def scenario(self):
        v = self.wait("instructor", "SD:scenario.run", lambda t: ("scenario", RUN) in t, "the scenario")
        return v[("scenario", RUN)]

    def clock(self, at_least):
        """Held until the exercise clock shows a step of at least this one, running or ended."""
        v = self.wait("exercise", "SD:clock.run", lambda t: (t.get(("clock", RUN)) or {}).get("state") in ("running", "ended")
                      and ((t[("clock", RUN)].get("step") or 0) >= at_least or t[("clock", RUN)]["state"] == "ended"), "step %d" % at_least)
        return v[("clock", RUN)]

    def counters(self):
        took = {}
        for role, ms in sorted(self.ms.items()):
            ms = sorted(ms)
            took[role] = {"n": len(ms), "median_ms": round(ms[len(ms) // 2], 1), "p95_ms": round(ms[min(len(ms) - 1, len(ms) * 95 // 100)], 1)}
        self.publish("counters", "%s.%d" % (self.part or "service", os.getpid()),
                     {"calls": self.calls + 1, "failed": self.failed, "build": BUILD, "took": took, "site": T.my_ip()})


def serve(role, part, body):
    """Run a Role's service: body(Role). It ends with the reason if a read or write fails (no fallback: a channel
    whose driver fails ends with the reason, and the exercise stops there)."""
    r = Role(role, part)
    r.say("%s started, pid %d" % (BUILD, os.getpid()))
    try:
        body(r)
        r.say("the exercise ended; %d calls, %d failed" % (r.calls, r.failed))
        H.holder().wait_until("instructor", memory("instructor"), lambda rows: (index(rows).get(("control", RUN)) or {}).get("command") == "close",
                              name_like="SD:control.%", wait_s=3600.0)          # it stays up for review
        return 0
    except Exception as e:
        r.say("ENDED with the reason: %s: %s" % (type(e).__name__, e))
        return 1
