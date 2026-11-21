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
"""units.py -- [ROLES_EXERCISE_V1] the Units Role: response crews. Each unit carries out the orders its readiness and
communications allow, and says what it did (the design's 10.6)."""
import sys

import rules
from role_service import serve


def body(r):
    sc = r.scenario()
    r.present()
    own = {str(u["n"]): {"x": u["x"], "y": u["y"], "reach": u["reach"], "readiness": "standby", "comms": "ok", "stores": u["stores"]} for u in sc["units"]}
    for n, v in own.items():
        r.publish("unit", n, v)
    r.ready(sc)
    for s in range(1, sc["steps"] + 1):
        order = r.wait("command", "SD:order.%d" % s, lambda t: ("order", str(s)) in t, "the orders of step %d" % s)[("order", str(s))]
        r.look_mine("SD:unit.%")
        now = {n: r.injected("unit", n, v) for n, v in own.items()}
        after, actions = rules.act(order["orders"], now)
        for n in own:
            for k in ("readiness", "stores"):
                own[n][k] = after[n][k]
            if {k: x for k, x in (r.mine.get(("unit", n)) or {}).items() if k != "ts"} != r.injected("unit", n, own[n]):
                r.publish("unit", n, own[n])                   # changed, or its own value again once the injection is released
        r.publish("action", s, {"step": s, "actions": actions,
                                "units": {n: {k: r.injected("unit", n, own[n])[k] for k in ("readiness", "comms", "stores")} for n in sorted(own)}})
    r.counters()


if __name__ == "__main__":
    sys.exit(serve("units", "", body))
