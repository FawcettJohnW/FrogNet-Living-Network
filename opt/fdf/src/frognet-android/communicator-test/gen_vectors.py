#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""gen_vectors.py COMMS_ROOT CP WORK -- the Python bearer / abort pad / reassembler (frogcomms/fnav.py) against the
Java ones (Vectors.java), on the same random inputs; every decision must be equal."""
import os, random, subprocess, sys
ROOT, CP, W = sys.argv[1:4]
sys.path.insert(0, os.path.join(ROOT, "python"))
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("fnav", os.path.join(ROOT, "python", "frogcomms", "fnav.py"))
F = importlib.util.module_from_spec(spec); spec.loader.exec_module(F)

rnd = random.Random(20261010)
lines, want = [], []
for seed in range(6):                                  # the bearer: 6 runs x 4000 samples, mixed weather
    ceiling = rnd.choice([5, 6, 7, 8]); t = 1000.0
    b = F.Bearer(ceiling, 0, t); lines.append("B %d 0 %r" % (ceiling, t))
    for k in range(4000):
        t += rnd.choice([0.02, 0.041, 0.042, 0.05, 0.2])
        storm = (k // 300) % 3 == 1
        dropped = rnd.choice([0, 0, 0, 1, 2]) if storm else (1 if rnd.random() < 0.01 else 0)
        fps = None if rnd.random() < 0.1 else round(rnd.uniform(8 if storm else 20, 24.5), 3)
        ashed = 1 if storm and rnd.random() < 0.05 else 0
        backlog = rnd.choice([0, 0, 0, 9]) if storm else 0
        if k % 997 == 500:
            b.resume_at(5, t); lines.append("R 5 %r" % t); want.append(str(b.idx))
        idx = b.sample(t, backlog, dropped, fps, 24.0, ashed)
        lines.append("S %r %d %d %s 24.0 %d" % (t, backlog, dropped, "None" if fps is None else repr(fps), ashed)); want.append(str(idx))
for n in range(0, 70):                                 # the abort pad
    lines.append("P %d" % n); want.append(F.abort_pad(n).hex() if n else "")
R = F.Reassembler()                                    # the reassembler: random segment streams from two sources
for k in range(3000):
    src = rnd.choice(["A", "B"]); fid = rnd.randrange(4); idx = rnd.choice([0, 0, 1, 2, 3]); flags = rnd.choice([0, 0, 1, 2])
    piece = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 6)))
    p = F.pack_seg(fid, idx, flags, piece) if rnd.random() > 0.02 else b"\x01\x02"
    lines.append("G %s %s" % (src, p.hex()))
    d = R.feed(src, p); want.append("-" if d is None else (d.hex() or "="))
lines.append("C"); want.append("%d %d %d %d %d %d" % (R.done, R.aborted, R.partial, R.gap, R.superseded, R.bad))
open(os.path.join(W, "vectors.in"), "w").write("\n".join(lines) + "\n")
subprocess.run(["java", "-cp", CP, "Vectors", os.path.join(W, "vectors.in"), os.path.join(W, "vectors.out")], check=True)
got = open(os.path.join(W, "vectors.out")).read().split("\n")[:len(want)]
diff = [(i, w, g) for i, (w, g) in enumerate(zip(want, got)) if w != g]
nb = sum(1 for l in lines if l[0] in "SR"); changes = sum(1 for i in range(1, nb) if want[i] != want[i - 1])
print("bearer decisions %d (%d rung changes), abort pads 70, reassembler steps 3000 + counters" % (nb, changes))
for d in diff[:5]:
    print("DIFF at", d)
print("RESULT %s Java == Python on %d decisions, %d differ" % ("PASS" if not diff and len(got) == len(want) else "FAIL", len(want), len(diff)))
sys.exit(1 if diff else 0)
