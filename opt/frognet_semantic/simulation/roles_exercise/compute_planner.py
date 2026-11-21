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
"""compute_planner.py -- [ROLES_EXERCISE_V1] the Compute Role's planner. It assigns each circuit of a step to a
processor by a fixed rule, from the availability and calibration each processor published for that step (the design's
10.4). Processors do not compete for work: there is no queue and nothing is claimed."""
import sys

import rules
from role_service import RUN, serve


def body(r):
    sc = r.scenario()
    r.present()
    procs = [str(p["n"]) for p in sc["processors"]]
    r.wait("compute", "SD:ready.%", lambda t: all(("ready", "processor" + n) in t for n in procs), "both processors at initial conditions")
    r.ready(sc)
    for s in range(1, sc["steps"] + 1):
        tracks = [t["track"] for t in r.wait("warning", "SD:detection.%d" % s, lambda t: ("detection", str(s)) in t, "the detections of step %d" % s)[("detection", str(s))]["tracks"]]
        for k in tracks:                                         # one held read for each track's circuit, by its name
            r.wait("assessment", "SD:circuit.%d" % k, lambda t: (t.get(("circuit", str(k))) or {}).get("step") == s, "track %d's circuit at step %d" % (k, s))
        mine = r.wait("compute", "SD:processor.%", lambda t: all((t.get(("processor", n)) or {}).get("step", 0) >= s for n in procs), "the processors' values for step %d" % s)
        jobs, reason = rules.assign(tracks, {n: mine[("processor", n)] for n in procs})
        r.look_mine()
        r.publish("assignment", s, dict({"step": s, "jobs": jobs}, **({"reason": reason} if reason else {})))
    r.counters()


if __name__ == "__main__":
    sys.exit(serve("compute", "", body))
