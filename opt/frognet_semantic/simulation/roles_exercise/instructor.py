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
"""instructor.py -- [ROLES_EXERCISE_V1] the Instructor Role: the exercise director (the design's 3.3, 10.7, 10.8).

    instructor.py serve <scenario file>     the Instructor's service: loads the scenario, and at each step applies and
                                            releases the injections due, then says the step's injections are in place
    instructor.py console <command>         the Instructor's console: start, freeze, resume, end, close

An injection is one write, in the Instructor's own memory (the design's 10.7, version 0.2):

    (instructor, injection, <n>) = {role, var, scope, value, from_step, state: active | released, by}

The Role it names holds a read on these and applies the active ones to its own memory. To release, the Instructor
writes the same tuple with state released. The Instructor writes into nobody else's memory: every tuple in the system
is written only by its owner. The Instructor writes the injections due at a step and then (instructor, applied, run)
for that step; Exercise publishes no ground truth until it has seen it, so no Role computes a step before its
injections are in place."""
import json
import sys
from role_service import RAM, RUN, memory, serve


def main(path):
    def body(r):
        sc = json.load(open(path))
        r.present()
        r.publish("scenario", RUN, sc)
        r.publish("control", RUN, {"command": "setup"})
        r.ready(sc)
        pending = list(enumerate(sc.get("injections", []), 1))
        active = {}                                              # injection number -> (its tuple, the step it is released at)
        for s in range(1, sc["steps"] + 1):
            r.clock(s)
            level = (r.look("command", "SD:alert.run").get(("alert", RUN)) or {}).get("level", 5)
            for n, (tup, until) in sorted(active.items()):
                if until is not None and s >= until:
                    r.publish("injection", n, dict(tup, state="released", released=s))
                    del active[n]
                    r.say("step %d: injection %d released" % (s, n))
            for n, inj in list(pending):
                when = inj.get("when") or {}
                if inj.get("step") == s or ("alert_at_most" in when and level <= when["alert_at_most"]):
                    tup = {"role": inj["role"], "var": inj["var"], "scope": str(inj["scope"]), "value": inj["value"],
                           "from_step": s, "state": "active", "by": "instructor"}
                    r.publish("injection", n, tup)
                    pending.remove((n, inj))
                    active[n] = (tup, inj.get("release") or (s + inj["release_after"] if "release_after" in inj else None))
                    r.say("step %d: injection %d for %s (%s, %s.%s) = %s" % (s, n, inj["role"], inj["role"], inj["var"], inj["scope"], json.dumps(inj["value"])))
            r.publish("applied", RUN, {"step": s})
        r.wait("exercise", "SD:clock.run", lambda t: (t.get(("clock", RUN)) or {}).get("state") == "ended", "the end of the exercise")
        r.counters()
    return serve("instructor", "", body)


def console(command):
    ok = RAM.upsert("instructor", "control", RUN, {"command": command}, RAM.DURABLE, dbhost=memory("instructor"))
    print("@@ " + json.dumps({"ok": bool(ok), "command": command}), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[2]) if sys.argv[1] == "serve" else console(sys.argv[2]))
