#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_fnav.py FNAV_DIR -- the Python fnav wire against fnav.py, with the SAME vectors that proved the C++ one:
frames, the rate meter and ceiling, the reassembler, the bearer, and the data plane over real TCP (echo, abort)."""
import os, socket, subprocess, sys, time
FN = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(ROOT, "python"))
from frogcomms import fnav as F  # noqa: E402

fail = 0
def bad(what):
    global fail
    fail += 1
    if fail <= 10: print("FAIL", what)
def gen(tool, seed, n):
    return subprocess.run([sys.executable, os.path.join(ROOT, "tools", tool), FN, str(seed), str(n)],
                          capture_output=True, text=True, check=True).stdout.splitlines()
unhex = lambda h: b"" if h == "-" else bytes.fromhex(h)

cases = 0
for seed in (1, 2, 3):                                             # the frame layer
    for line in gen("gen_frames.py", seed, 400):
        w = line.split(); cases += 1
        if w[0] == "typed":
            kind, src, pay, want = int(w[1]), unhex(w[2]), unhex(w[3]), unhex(w[4])
            if F.pack_typed(kind, src, pay) != want: bad("pack_typed %d" % cases)
            k, s, p = F.unpack_typed(want)
            if (k, s.encode(), p) != (kind, src, pay): bad("unpack_typed %d" % cases)
        elif w[0] == "video":
            lvl, key, codec, pay, want = int(w[1]), w[2] == "1", int(w[3]), unhex(w[4]), unhex(w[5])
            if F.pack_video(lvl, pay, codec, key) != want: bad("pack_video %d" % cases)
            if F.unpack_video(want) != (lvl, key, codec, pay): bad("unpack_video %d" % cases)
        elif w[0] == "iskey":
            if F.video_is_key(unhex(w[1])) != (w[2] == "1"): bad("video_is_key %d" % cases)
        elif w[0] == "seg":
            fid, idx, fl, pay, want = int(w[1]), int(w[2]), int(w[3]), unhex(w[4]), unhex(w[5])
            if F.pack_seg(fid, idx, fl, pay) != want: bad("pack_seg %d" % cases)
            if F.unpack_seg(want) != (fid, idx, fl, pay): bad("unpack_seg %d" % cases)
print("frame layer vs fnav.py: %d cases" % cases)

steps = 0                                                          # rate meter and whole-frame ceiling
for seed in (1, 2):
    m = F.RateMeter()
    for line in gen("gen_segment.py", seed, 2000):
        _, now, nb, rate, wmax = line.split(); steps += 1
        m.on_accepted(int(nb), float(now))
        if abs(m.rate - float(rate)) > 1e-6 * max(1.0, float(rate)): bad("rate step %d" % steps)
        if F.whole_frame_max(m.rate, 1 << 18) != int(wmax): bad("whole_frame_max step %d" % steps)
print("rate meter and ceiling vs fnav.py: %d steps" % steps)

r = F.Reassembler()                                                # the reassembler's cases
seg = lambda f, i, fl, s: F.pack_seg(f, i, fl, s.encode())
if r.feed("A", seg(1, 0, 0, "he")) or r.feed("A", seg(1, 1, 0, "ll")) or r.feed("A", seg(1, 2, F.VSEG_LAST, "o")) != b"hello": bad("in order")
r.feed("A", seg(2, 0, 0, "x")); r.feed("A", seg(2, 0, F.VSEG_ABORT, ""))
r.feed("A", seg(3, 1, F.VSEG_LAST, "y")); r.feed("A", seg(4, 0, 0, "a")); r.feed("A", seg(4, 2, F.VSEG_LAST, "c"))
if (r.aborted, r.partial, r.gap) != (1, 1, 1): bad("reassembler counts %s" % ((r.aborted, r.partial, r.gap),))

samples = moves = 0                                                # the bearer
for seed in (1, 2, 3):
    lines = gen("gen_bearer.py", seed, 3000)
    _, ceil_, floor_, t0 = lines[0].split()
    b = F.Bearer(int(ceil_), int(floor_), float(t0)); last = int(ceil_)
    for line in lines[1:]:
        t, backlog, dropped, fs, ft, ashed, want = line.split(); samples += 1
        got = b.sample(float(t), int(backlog), int(dropped), None if fs == "-" else float(fs), None if ft == "-" else float(ft), int(ashed))
        if got != last: moves += 1; last = got
        if got != int(want): bad("bearer sample %d: Python L%d, fnav L%s" % (samples, got, want))
print("bearer vs fnav.py: %d samples, %d rung changes" % (samples, moves))

def peer(port, mode):                                              # the data plane, against fnav.py's own peer
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "fnav_peer.py"), FN, str(port), mode],
                         stdout=subprocess.PIPE, text=True)
    p.stdout.readline()
    return p
p = peer(18701, "echo"); pl = F.Plane(F.Plane.connect("127.0.0.1", 18701))
sizes = [0, 1, 7, 60, 1368, 8192, 9000, 60000]
for i in range(200):
    pay = os.urandom(sizes[i % 8])
    body = F.pack_typed(F.KIND_VIDEO, "Donna", F.pack_video(i % 9, pay, 0, i % 5 == 0)) if i % 3 == 0 else F.pack_typed(i % 7, "名前" if i % 2 else "John", pay)
    while (st := pl.send(body, F.KEYFRAME)) == F.DROPPED: time.sleep(0.002)
    state, back = pl.recv(5)
    if state != F.FRAME or back != body: bad("echo frame %d" % i)
pl.shutdown_both(); out = p.communicate()[0]
if "ECHOED 200" not in out: bad("fnav.py echoed: %s" % out)
print("data plane echo: 200 frames Python -> fnav.py -> Python")
p = peer(18702, "stall"); pl = F.Plane(F.Plane.connect("127.0.0.1", 18702))
r1 = pl.send(F.pack_typed(F.KIND_VIDEO, "Donna", F.pack_video(7, b"\x5a" * (200 << 10), 0, True)), F.VIDEO)
small = F.pack_typed(F.KIND_AUDIO, "Donna", b"\x11" * 60)
for _ in range(400):
    if pl.send(small, F.AUDIO) == F.WHOLE: break
    time.sleep(0.01)
time.sleep(1.5); pl.shutdown_both(); out = p.communicate()[0]
print("data plane abort: big frame %s; fnav.py reported %s" % (r1, " ".join(out.split())))
if r1 != F.ABORTED: bad("big frame should abort, got %s" % r1)
if "ABORTED" not in out or "OK %d" % len(small) not in out: bad("fnav.py must see the abort and then the next frame")
print("RESULT %s Python fnav wire vs fnav.py: %d fail" % ("FAIL" if fail else "PASS", fail))
sys.exit(1 if fail else 0)
