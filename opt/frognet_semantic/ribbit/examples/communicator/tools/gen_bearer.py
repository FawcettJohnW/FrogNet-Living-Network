#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""gen_bearer.py FNAV_DIR SEED N -- fnav.py's OWN Bearer.sample, driven from a controlled clock through calls,
congestion and recovery, as vectors for the C++ bearer. One line per sample: t backlog dropped fps_sent fps_target
audio_shed idx ('-' for None)."""
import random, sys, types
sys.path.insert(0, sys.argv[1])
import fnav  # noqa: E402

rng = random.Random(int(sys.argv[2])); n = int(sys.argv[3])
clock = [5000.0]
fnav.time = types.SimpleNamespace(time=lambda: clock[0])
ceiling, floor = rng.choice([(8, 0), (7, 3), (8, 4)])
b = fnav.Bearer(ceiling, floor)
print("init", ceiling, floor, repr(clock[0]))
phase = "clean"
for i in range(n):
    if i % 120 == 0:
        phase = rng.choice(["clean", "clean", "drops", "burst", "slow", "audio", "backlog"])
    clock[0] += rng.choice([1 / 24.0, 1 / 24.0, 1 / 24.0, 0.2, 1.1])
    tgt = rng.choice([24.0, 24.0, 30.0, None])
    fps = None if rng.random() < 0.1 else ((tgt or 24.0) * rng.choice([1.0, 0.99, 0.97]) if phase != "slow" else rng.choice([6.0, 9.0, 14.0]))
    dropped = rng.choice([0, 0, 1, 2]) if phase in ("drops", "burst") else 0
    if phase == "burst" and rng.random() < 0.2:
        dropped = rng.choice([6, 9])
    backlog = rng.choice([0, 3, 8, 12]) if phase == "backlog" else 0
    ashed = rng.choice([0, 0, 0, 1]) if phase == "audio" else 0
    idx = b.sample(backlog, dropped, fps_sent=fps, fps_target=tgt, audio_shed=ashed)
    f = lambda v: "-" if v is None else repr(v)
    print(repr(clock[0]), backlog, dropped, f(fps), f(tgt), ashed, idx)
