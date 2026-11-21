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
rules.py -- [ROLES_EXERCISE_V1] each Role's rule, as the design's section 10 states it. Whole numbers in, whole numbers
out: quality and confidence in thousandths, angles in thousandths of a radian. Nothing here reads or writes a memory,
so each rule can be run on its own.
"""
PHANTOM = 1000          # a phantom's track number is this plus its own
CIRCUIT = ('OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[3] q;\nbit[1] c;\nry(%s) q[0];\nry(%s) q[1];\n'
           'ccx q[0], q[1], q[2];\nc[0] = measure q[2];\n')

# The design's 10.3 (version 0.2): (quality, sensors, the probability of measuring 1, in thousandths). The simulator
# is tested against these before it is used in a run (reference_check).
REFERENCE = ((900, 1, 674), (900, 2, 833), (900, 4, 975), (500, 2, 426), (0, 1, 0), (0, 4, 0))


def far(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


def move(objects, launches, step, grid, protected):
    """Exercise, order 2: each object moves by its dx, dy; the launches due at this step are added. Returns (the
    objects now on the grid, the ids of those that reached a protected cell this step)."""
    out, reached = [], []
    for o in objects:
        x, y = o["x"] + o["dx"], o["y"] + o["dy"]
        if [x, y] in protected:
            reached.append(o["id"])
        elif 0 <= x < grid and 0 <= y < grid:
            out.append({"id": o["id"], "x": x, "y": y, "dx": o["dx"], "dy": o["dy"]})
    for l in launches:
        if l["step"] == step:
            out.append({"id": l["id"], "x": l["x"], "y": l["y"], "dx": l["dx"], "dy": l["dy"]})
    return sorted(out, key=lambda o: o["id"]), sorted(reached)


def detect(objects, sensors, phantoms):
    """Warning, the design's 10.2. sensors: {n: {x, y, range, quality, status}}; phantoms: {n: {x, y, quality}}."""
    working = [s for _n, s in sorted(sensors.items()) if s.get("status") == "ok"]
    tracks = []
    for o in sorted(objects, key=lambda o: o["id"]):
        readings = [s["quality"] * (s["range"] + 1 - far(s["x"], s["y"], o["x"], o["y"])) // (s["range"] + 1)
                    for s in working if far(s["x"], s["y"], o["x"], o["y"]) <= s["range"]]
        if readings:
            tracks.append({"track": o["id"], "x": o["x"], "y": o["y"], "quality": max(readings), "sensors": len(readings)})
    for n, p in sorted(phantoms.items()):
        if working:
            tracks.append({"track": PHANTOM + int(n), "x": p["x"], "y": p["y"], "quality": p["quality"], "sensors": len(working)})
    return tracks


def angle(thousandths):
    return "%d.%03d" % (thousandths // 1000, thousandths % 1000)


def circuit(quality, sensors):
    """Assessment, the design's 10.3 (version 0.2): the two angles, in thousandths of a radian, and the program.
    The measured qubit is 1 only when both others are: quality moves the first angle, the number of sensors the
    second."""
    a = quality * 3141593 // 1000000
    b = (4 + min(sensors, 4)) * 392699 // 1000
    return CIRCUIT % (angle(a), angle(b))


def reference_check(probability_ppm):
    """The design's reference values against a simulator's probability_ppm(program). Returns the list of the ones
    that differ, each (quality, sensors, wanted, got); empty when the simulator is fit to use."""
    wrong = []
    for quality, sensors, want in REFERENCE:
        got = int(round(probability_ppm(circuit(quality, sensors)) / 1000.0))
        if got != want:
            wrong.append((quality, sensors, want, got))
    return wrong


def assign(tracks, processors):
    """Compute's planner, the design's 10.4. processors: {n: {available, error_rate, limit}} as published for the
    step. Returns (jobs, the reason there are none)."""
    qualified = [int(n) for n, p in sorted(processors.items(), key=lambda kv: int(kv[0]))
                 if p.get("available") and p.get("error_rate", 0) <= p.get("limit", 0)]
    if not qualified:
        return [], "no processor is available and within its calibration limit"
    return [{"track": t, "processor": qualified[i % len(qualified)]} for i, t in enumerate(sorted(tracks))], ""


def alert(tracks, protected, console=None):
    """Command, the design's 10.5. tracks: [{track, x, y, confidence}]."""
    if console is not None:
        return int(console)
    if not tracks:
        return 5
    sure = [t for t in tracks if t["confidence"] >= 600]
    if not sure:
        return 4
    near = lambda t: min(far(t["x"], t["y"], p[0], p[1]) for p in protected)
    if any(t["confidence"] >= 800 and near(t) <= 4 for t in sure):
        return 1
    if any(near(t) <= 8 for t in sure):
        return 2
    return 3


def orders(level, tracks, units, protected):
    """Command's orders at this level. units: {n: {x, y, reach, readiness, comms, stores}}."""
    if level == 3:
        return [{"unit": int(n), "action": "ready", "track": 0, "x": 0, "y": 0} for n, _u in sorted(units.items(), key=lambda kv: int(kv[0]))]
    if level > 2:
        return []
    near = lambda t: min(far(t["x"], t["y"], p[0], p[1]) for p in protected)
    out, given = [], set()
    for t in sorted([t for t in tracks if t["confidence"] >= 600], key=lambda t: (near(t), t["track"])):
        able = [(far(u["x"], u["y"], t["x"], t["y"]), int(n)) for n, u in units.items()
                if u.get("readiness") == "ready" and u.get("comms") == "ok" and u.get("stores", 0) > 0 and int(n) not in given]
        if able:
            _d, n = min(able)
            given.add(n)
            out.append({"unit": n, "action": "respond", "track": t["track"], "x": t["x"], "y": t["y"]})
    return out


def act(order_list, units):
    """Units, the design's 10.6. Returns (the units after the step, the actions taken)."""
    after = {n: dict(u) for n, u in units.items()}
    actions = []
    for o in sorted(order_list, key=lambda o: (o["unit"], o["track"])):
        u = after.get(str(o["unit"]))
        if u is None or u.get("comms") != "ok":
            continue                                       # a unit that has lost communications does not see the order
        if o["action"] == "ready":
            u["readiness"] = "ready"
        elif o["action"] == "respond" and u.get("stores", 0) > 0:
            u["stores"] -= 1
            actions.append({"unit": o["unit"], "track": o["track"], "x": o["x"], "y": o["y"],
                            "done": far(u["x"], u["y"], o["x"], o["y"]) <= u["reach"]})
    return after, actions


def score(objects, actions, reached, was):
    """Exercise, order 10. Returns (the objects left, the score after this step)."""
    s = dict(was)
    done = {a["track"] for a in actions if a["done"]}
    left = [o for o in objects if o["id"] not in done]
    hit = len(objects) - len(left)
    false = len([t for t in done if t >= PHANTOM])
    s["intercepted"] += hit
    s["false_responses"] += false
    s["reached"] += len(reached)
    s["score"] += 5 * hit - 2 * false - 10 * len(reached)
    return left, s
