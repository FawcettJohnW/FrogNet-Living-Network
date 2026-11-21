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
"""compute_processor.py <n> -- [ROLES_EXERCISE_V1] one of the Compute Role's quantum processors, simulated. It
publishes its own availability and calibration for each step, never chooses its own work, and runs the circuits the
planner's assignment gives its number (the design's 10.4). The two are at different sites and share Compute's memory."""
import sys

import rules
import statevector
from role_service import serve


def main(n):
    def body(r):
        wrong = rules.reference_check(statevector.probability_ppm)       # the design's 10.3: tested before it is used
        if wrong:
            raise RuntimeError("processor %s: the simulator differs from the design's reference values "
                               "(quality, sensors, wanted, got): %r" % (n, wrong))
        sc = r.scenario()
        r.present()
        me = [p for p in sc["processors"] if str(p["n"]) == n][0]
        own = {"step": 0, "available": True, "error_rate": me["error_rate"], "limit": me["limit"]}
        r.publish("processor", n, own)
        r.ready(sc)
        for s in range(1, sc["steps"] + 1):
            r.wait("instructor", "SD:applied.run", lambda t: (t.get(("applied", "run")) or {}).get("step", 0) >= s, "the injections for step %d" % s)
            r.look_mine("SD:processor." + n)
            r.publish("processor", n, dict(own, step=s))
            jobs = r.wait("compute", "SD:assignment.%d" % s, lambda t: ("assignment", str(s)) in t, "the assignments of step %d" % s)[("assignment", str(s))]["jobs"]
            for j in jobs:
                if str(j["processor"]) != n:
                    continue
                c = r.look("assessment", "SD:circuit.%d" % j["track"]).get(("circuit", str(j["track"])))
                if not c or c.get("step") != s:
                    raise RuntimeError("processor %s was assigned track %d at step %d and its circuit is not in the memory" % (n, j["track"], s))
                shots, ones = statevector.run(s, c["qasm"])
                r.publish("result", j["track"], {"step": s, "track": j["track"], "shots": shots, "ones": ones})
        r.counters()
    return serve("compute", "processor" + n, body)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
