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
"""assessment.py -- [ROLES_EXERCISE_V1] the Assessment Role: analysts. For each detection it writes a circuit, whose
two angles are set from the detection, and turns the counts Compute returns into a confidence (the design's 10.3)."""
import sys

import rules
from role_service import serve


def body(r):
    sc = r.scenario()
    r.present()
    r.ready(sc)
    for s in range(1, sc["steps"] + 1):
        tracks = r.wait("warning", "SD:detection.%d" % s, lambda t: ("detection", str(s)) in t, "the detections of step %d" % s)[("detection", str(s))]["tracks"]
        r.look_mine()
        for t in tracks:
            r.publish("circuit", t["track"], {"step": s, "track": t["track"], "qasm": rules.circuit(t["quality"], t["sensors"])})
        plan = r.wait("compute", "SD:assignment.%d" % s, lambda t: ("assignment", str(s)) in t, "the assignments of step %d" % s)[("assignment", str(s))]
        want = [str(j["track"]) for j in plan["jobs"]]
        res = {}
        for k in want:                                           # one held read for each track's result, by its name
            res.update(r.wait("compute", "SD:result." + k, lambda t: (t.get(("result", k)) or {}).get("step") == s, "track %s's result at step %d" % (k, s)))
        out = []
        for t in tracks:
            x = res.get(("result", str(t["track"])))
            ones, shots = (x["ones"], x["shots"]) if x and str(t["track"]) in want else (0, 1)
            out.append({"track": t["track"], "x": t["x"], "y": t["y"], "quality": t["quality"], "sensors": t["sensors"],
                        "ones": ones, "shots": shots, "confidence": ones * 1000 // shots})
        r.publish("confidence", s, {"step": s, "tracks": out})
    r.counters()


if __name__ == "__main__":
    sys.exit(serve("assessment", "", body))
