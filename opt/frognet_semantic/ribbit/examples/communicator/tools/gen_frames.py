#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""gen_frames.py FNAV_DIR SEED N -- frames built by fnav.py's OWN packing functions, as test vectors for the C++
frame layer. One line per case: kind of case, the inputs, and fnav.py's bytes, all hex."""
import os, random, sys
sys.path.insert(0, sys.argv[1])
import fnav  # noqa: E402

rng = random.Random(int(sys.argv[2])); n = int(sys.argv[3])
names = ["Donna", "John", "Gorp", "*Chapel camera", "Zoë", "", "名前", "a" * 300]
hx = lambda b: b.hex() or "-"
for i in range(n):
    src = rng.choice(names)
    payload = bytes(rng.getrandbits(8) for _ in range(rng.choice([0, 1, 5, 60, 1368, 9000])))
    kind = rng.randrange(0, 7)
    print("typed", kind, hx(src.encode()), hx(payload), hx(fnav.pack_typed(kind, src, payload)))
    level, key, codec = rng.randrange(0, 9), rng.random() < 0.3, rng.randrange(0, 3)
    v = fnav.pack_video(level, payload, codec, key)
    print("video", level, int(key), codec, hx(payload), hx(v))
    body = fnav.pack_typed(fnav.KIND_VIDEO, src, v)
    print("iskey", hx(body), int(fnav.video_is_key(body)))
    fid, idx, fl = rng.randrange(0, 65536), rng.randrange(0, 65536), rng.choice([0, 1, 2, 3])
    print("seg", fid, idx, fl, hx(payload), hx(fnav._VSEG.pack(fid, idx, fl) + payload))
