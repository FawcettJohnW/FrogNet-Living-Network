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
"""exercise.py -- [ROLES_EXERCISE_V1] the Exercise Role: the simulated world. It owns the clock, ground truth and the
score (the design's 9.3, 10.1 orders 2 and 10, 10.8)."""
import sys

import rules
from role_service import ROLES, RUN, serve


def body(r):
    sc = r.scenario()
    r.present()
    steps, grid, protected = sc["steps"], sc["grid"], sc["protected"]
    r.ready(sc)
    for role in ROLES:
        r.wait(role, "SD:ready.%", lambda t: ("ready", RUN) in t, "%s at initial conditions" % role)
    r.publish("clock", RUN, {"step": 0, "state": "setup"})
    r.say("every Role is at initial conditions")
    r.wait("instructor", "SD:control.run", lambda t: (t.get(("control", RUN)) or {}).get("command") == "start", "start")
    objects, total = [], {"score": 0, "intercepted": 0, "reached": 0, "false_responses": 0}
    r.publish("clock", RUN, {"step": 1, "state": "running"})
    for s in range(1, steps + 1):
        r.wait("instructor", "SD:applied.run", lambda t: (t.get(("applied", RUN)) or {}).get("step", 0) >= s, "the injections for step %d" % s)
        objects, reached = rules.move(objects, sc["launches"], s, grid, protected)
        r.publish("truth", s, {"step": s, "objects": objects})
        acts = r.wait("units", "SD:action.%d" % s, lambda t: ("action", str(s)) in t, "the actions of step %d" % s)[("action", str(s))]["actions"]
        objects, total = rules.score(objects, acts, reached, total)
        r.publish("score", s, dict(total, step=s))
        r.publish("score", RUN, dict(total, step=s))
        if (r.look("instructor", "SD:control.run").get(("control", RUN)) or {}).get("command") == "freeze":
            r.publish("clock", RUN, {"step": s, "state": "frozen"})
            r.wait("instructor", "SD:control.run", lambda t: (t.get(("control", RUN)) or {}).get("command") == "resume", "resume")
        r.publish("clock", RUN, {"step": s + 1, "state": "running"} if s < steps else {"step": s, "state": "ended"})
        r.say("step %d done" % s)
    r.counters()


if __name__ == "__main__":
    sys.exit(serve("exercise", "", body))
