#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""gen_segment.py FNAV_DIR SEED N -- fnav.py's OWN rate meter (_note_sent) and ceiling (whole_frame_max), run on
a stand-in object with a controlled clock, as vectors for the C++ segmentation layer.
Lines:  step <t> <nbytes> <rate_after> <whole_max_after>"""
import random, sys, types
sys.path.insert(0, sys.argv[1])
import fnav  # noqa: E402

rng = random.Random(int(sys.argv[2])); n = int(sys.argv[3])
clock = [1000.0]
fnav.time = types.SimpleNamespace(time=lambda: clock[0])       # fnav's _note_sent reads time.time()
obj = types.SimpleNamespace(_sndbuf=fnav.SotFDataPlane.SNDBUF * 2, RATE_HALFLIFE_S=fnav.SotFDataPlane.RATE_HALFLIFE_S,
                            WHOLE_FRAME_MS=fnav.SotFDataPlane.WHOLE_FRAME_MS)
for i in range(n):
    clock[0] += rng.choice([0.0, 0.001, 0.02, 0.04, 0.3, 2.5])
    nb = rng.choice([60, 1368, 9000, 30000, 120000])
    fnav.SotFDataPlane._note_sent(obj, nb)
    print("step %.6f %d %.9f %d" % (clock[0], nb, obj._rate_bps, fnav.SotFDataPlane.whole_frame_max(obj)))
