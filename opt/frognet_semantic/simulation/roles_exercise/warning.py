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
"""warning.py -- [ROLES_EXERCISE_V1] the Warning Role: sensor stations. It owns the sensors and the detections (the
design's 10.2). A sensor an injection fails stays failed until the injection is released; a phantom is a detection
an injection calls for, which Warning writes and keeps while the injection is active."""
import sys

import rules
from role_service import serve


def body(r):
    sc = r.scenario()
    r.present()
    own = {str(s["n"]): {"x": s["x"], "y": s["y"], "range": s["range"], "status": "ok", "quality": s["quality"]} for s in sc["sensors"]}
    for n, v in own.items():
        r.publish("sensor", n, v)
    r.ready(sc)
    for s in range(1, sc["steps"] + 1):
        truth = r.wait("exercise", "SD:truth.%d" % s, lambda t: ("truth", str(s)) in t, "ground truth for step %d" % s)[("truth", str(s))]
        mine = r.look_mine("SD:sensor.%", "SD:phantom.%")
        sensors = {n: r.injected("sensor", n, v) for n, v in own.items()}
        phantoms = {k.split(".", 1)[1]: r.injected("phantom", k.split(".", 1)[1], {}) for k in r.injections if k.startswith("phantom.")}
        for scope, v in sorted(phantoms.items()):               # Warning writes the phantom an injection calls for
            if {k: x for k, x in (mine.get(("phantom", scope)) or {}).items() if k != "ts"} != v:
                r.publish("phantom", scope, v)
        for (var, scope), v in sorted(mine.items()):            # and writes that it is gone once the injection is released
            if var == "phantom" and scope not in phantoms and not v.get("gone"):
                r.publish("phantom", scope, {"gone": True})
        for n, v in sensors.items():
            if {k: x for k, x in (mine.get(("sensor", n)) or {}).items() if k != "ts"} != v:
                r.publish("sensor", n, own[n])                 # the injected value, or its own again once the injection is released
        r.publish("detection", s, {"step": s, "tracks": rules.detect(truth["objects"], sensors, phantoms),
                                   "sensors": {n: v["status"] for n, v in sorted(sensors.items())}})
    r.counters()


if __name__ == "__main__":
    sys.exit(serve("warning", "", body))
