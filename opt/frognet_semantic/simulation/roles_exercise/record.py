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
"""record.py collect <out file> <roles> -- [ROLES_EXERCISE_V1] the two records of a run, read back from every Role's
memory: collect() at each site for the memories that site holds, then build() from them all.

The exercise record is what happened in the exercise: the scenario, and for each step the ground truth, the detections
and each sensor's state, each track's angles' inputs, counts and confidence, the alert level and orders, the actions
and each unit's state, and the score. It is what is compared between runs, byte for byte, so it holds nothing that says
how the run was carried: no times, no addresses, no processor assignments.

The engineering record is how the run was carried: which processor ran which circuit, the injections with their
author, each service's own count of its calls and failures, and the build each said it was."""
import json
import sys

from role_service import ROLES, RUN, Role


def clean(v):
    if isinstance(v, dict):
        return {k: clean(x) for k, x in sorted(v.items()) if k != "ts"}
    if isinstance(v, list):
        return [clean(x) for x in v]
    return v


def collect(roles):
    """{role: {"<var>.<scope>": value}}: each named Role's memory as it stands. Run at the site that holds those
    memories, so the read of a whole memory crosses no link."""
    r = Role("exercise", "record")
    return {role: {"%s.%s" % k: v for k, v in r.look(role).items()} for role in roles}


def build(parts):
    """(the exercise record, the engineering record) from every Role's memory, as collect() gave them."""
    mem = {role: {tuple(k.split(".", 1)): v for k, v in parts[role].items()} for role in ROLES}
    sc = mem["instructor"][("scenario", RUN)]
    steps = {}
    for s in range(1, sc["steps"] + 1):
        k = str(s)
        steps[k] = {"truth": mem["exercise"].get(("truth", k)), "detection": mem["warning"].get(("detection", k)),
                    "confidence": mem["assessment"].get(("confidence", k)), "order": mem["command"].get(("order", k)),
                    "action": mem["units"].get(("action", k)), "score": mem["exercise"].get(("score", k))}
    exercise = clean({"scenario": sc, "steps": steps, "final": mem["exercise"].get(("score", RUN)), "clock": mem["exercise"].get(("clock", RUN))})
    engineering = clean({
        "assignments": {scope: v for (var, scope), v in mem["compute"].items() if var == "assignment"},
        "injections": {scope: v for (var, scope), v in mem["instructor"].items() if var == "injection"},
        "counters": {role: {scope: v for (var, scope), v in mem[role].items() if var == "counters"} for role in ROLES},
        "present": {role: {scope: v for (var, scope), v in mem[role].items() if var == "present"} for role in ROLES},
    })
    return exercise, engineering


def text(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    # record.py collect <out file> <role,role,...>
    got = collect(sys.argv[3].split(","))
    with open(sys.argv[2], "w") as f:
        json.dump(got, f, sort_keys=True)
    print("@@ " + json.dumps({"ok": True, "roles": sorted(got), "tuples": sum(len(v) for v in got.values())}), flush=True)
