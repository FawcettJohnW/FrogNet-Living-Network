#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""gen_mixer.py FNAV_DIR SEED N -- fnphone_pa.py's OWN Mixer driven through feeds and pulls (sources joining,
bursting, running dry, overflowing the cap), as vectors for the C++ mixer. Lines: 'init block jitter',
'feed src pcmhex', 'pull outhex trimmed trims pads pad_bytes dry active peak'."""
import random, sys
sys.path.insert(0, sys.argv[1])
import fnphone_pa as A  # noqa: E402

rng = random.Random(int(sys.argv[2])); n = int(sys.argv[3])
block = 640
jitter = rng.choice([0, 640, 1280, 3200, 8000])
m = A.Mixer(block, jitter)
print("init", block, jitter)
srcs = ["Donna", "John", "Julie"]
for i in range(n):
    if rng.random() < 0.55:
        src = rng.choice(srcs)
        ln = rng.choice([0, 2, 100, 640, 640, 1280, 4000, 9000])
        pcm = bytes(rng.getrandbits(8) for _ in range(ln))
        m.feed(src, pcm)
        print("feed", src, pcm.hex() or "-")
    else:
        out = m.pull_block()
        print("pull", out.hex(), m.trimmed, m.trims, m.pads, m.pad_bytes, m.dry, m.active, m.last_peak)
