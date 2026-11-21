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
"""command.py -- [ROLES_EXERCISE_V1] the Command Role: the decision cell. It sets the alert level from the confidence
figures and issues orders (the design's 10.5). A console input in the scenario for a step replaces the computed level."""
import sys

import rules
from role_service import RUN, serve


def body(r):
    sc = r.scenario()
    r.present()
    console = {c["step"]: c["input"]["level"] for c in sc.get("console", []) if c["role"] == "command"}
    r.publish("alert", RUN, {"step": 0, "level": 5})
    r.ready(sc)
    for s in range(1, sc["steps"] + 1):
        tracks = r.wait("assessment", "SD:confidence.%d" % s, lambda t: ("confidence", str(s)) in t, "the confidence of step %d" % s)[("confidence", str(s))]["tracks"]
        units = {scope: v for (var, scope), v in r.look("units", "SD:unit.%").items() if var == "unit"}
        r.look_mine("SD:alert.run")
        level = r.injected("alert", RUN, {"step": s, "level": rules.alert(tracks, sc["protected"], console.get(s))})["level"]
        r.publish("alert", RUN, {"step": s, "level": level})
        r.publish("order", s, {"step": s, "level": level, "orders": rules.orders(level, tracks, units, sc["protected"])})
    r.counters()


if __name__ == "__main__":
    sys.exit(serve("command", "", body))
