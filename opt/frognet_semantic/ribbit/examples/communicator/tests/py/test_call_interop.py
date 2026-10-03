#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_call_interop.py BUILD_DIR -- calls across the two implementations, through the C++ media server:
  C++ -> Python: the C++ comms-feed (stock video + 440 Hz tone) watched by a Python room + call
  Python -> C++: a Python unattended feed, found and watched by the C++ test_feed_view"""
import math, os, subprocess, sys, time
B = os.path.abspath(sys.argv[1]); ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "python")); os.environ.setdefault("FROGCOMMS_LIB", B)
import numpy as np  # noqa: E402
from frogcomms.room import Room  # noqa: E402
from frogcomms.call import Call  # noqa: E402
from frogcomms.media import SyntheticSource  # noqa: E402
from frogcomms.audio import ToneSource  # noqa: E402

RAM, MEDIA = 21200, 21220
procs = [subprocess.Popen([os.path.join(B, "comms-ram"), "--listen", "127.0.0.1:%d" % RAM], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
time.sleep(1)
procs.append(subprocess.Popen([os.path.join(B, "comms-media"), "--listen", "127.0.0.1:%d" % MEDIA, "--ram", "127.0.0.1:%d" % RAM],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
time.sleep(1)
fail = 0
def bad(w):
    global fail; fail += 1; print("FAIL", w)
try:
    # -- C++ -> Python ----------------------------------------------------------------------------------------
    procs.append(subprocess.Popen([os.path.join(B, "comms-feed"), "--ram", "127.0.0.1:%d" % RAM, "--media", "127.0.0.1:%d" % MEDIA,
                                   "--name", "Chapel camera", "--video", os.path.join(ROOT, "media", "stock-720p.mp4"), "--tone", "440"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(3)
    py = Room("127.0.0.1", RAM, "Pythonia")
    on = py.calls_of("Chapel camera")
    offer = next((o for o in py.offers() if o.session in on), None)
    print("Python lobby:", [m.name for m in py.roster()], "| the feed's call:", offer)
    if not offer: bad("Python did not find the C++ feed's call")
    else:
        c = Call(offer.media_host, offer.media_port, "Pythonia", offer.session)
        pkts = []
        orig = c.opus.decode
        def tap(src, pkt):                                   # keep the decoded audio, to check the tone
            pcm = orig(src, pkt); pkts.append(pcm); return pcm
        c.opus.decode = tap
        c.start(); time.sleep(2)
        v0, a0 = c.stats.v_recv, c.stats.a_recv
        time.sleep(5)
        v, a = c.stats.v_recv - v0, c.stats.a_recv - a0
        pic = c.latest("Chapel camera")
        pcm = np.frombuffer(b"".join(pkts[-150:]), np.int16)
        zc = np.count_nonzero(np.diff(np.signbit(pcm).astype(np.int8)))
        hz = zc / 2 / (len(pcm) / 16000) if len(pcm) else 0
        print("C++ -> Python: %d video frames and %d audio blocks in 5 s; picture %s; tone %.1f Hz"
              % (v, a, "%dx%d" % (pic.w, pic.h) if pic else "none", hz))
        if v < 100: bad("video from C++ to Python: %d frames in 5 s" % v)
        if not (230 <= a <= 270): bad("audio from C++ to Python: %d blocks in 5 s (250 expected)" % a)
        if not pic or (pic.w, pic.h) != (1280, 720): bad("Python decoded no 1280x720 picture")
        if abs(hz - 440) > 5: bad("the tone arrived at %.1f Hz" % hz)
        c.stop()
    # -- Python -> C++ ----------------------------------------------------------------------------------------
    feed = Room("127.0.0.1", RAM, "*PyCam", "cam=1,mic=0,unattended=1")
    sess = "py%06d" % (int(time.time()) % 1000000)
    feed.offer_call(sess, "127.0.0.1", MEDIA); feed.join_call(sess, "127.0.0.1", MEDIA)
    pc = Call("127.0.0.1", MEDIA, "PyCam", sess, SyntheticSource(1280, 720), 24, ToneSource(660))
    pc.start(); time.sleep(2)
    out = subprocess.run([os.path.join(B, "test_feed_view"), str(RAM), "PyCam"], capture_output=True, text=True, timeout=40).stdout
    print("Python -> C++:", " | ".join(out.strip().splitlines()[-3:]))
    if "RESULT PASS" not in out: bad("the C++ viewer could not watch the Python feed")
    pc.stop(); feed.close(); py.close()
finally:
    for p in reversed(procs): p.terminate()
print("RESULT %s calls across Python and C++: %d fail" % ("FAIL" if fail else "PASS", fail))
sys.exit(1 if fail else 0)
