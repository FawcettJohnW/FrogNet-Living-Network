#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_call_under_the_frame.py BUILD_DIR -- [THE_CALL_IS_UNDER_THE_FRAME_V1] a real call, video and audio, across the
two implementations and through the media server, while its owner switches the Role's key, algorithm and protocol.

  the C++ comms-feed (the stock 720p video and a 440 Hz tone, --channel mediahost) is watched by a Python room and call
  (frogcomms, under the same frame). The Role is sealed before anyone joins. For 6 s nothing is switched: the baseline.
  Then the owner's command, comms-channel, is run nine times, 2 s apart: a new key, chorus, chacha20-poly1305, openqasm,
  a new key, not sealed, sealed, tcp, a new key.

  1  with nothing switched: frames and blocks per second, and the longest time with no new video frame and no new block
  2  across the nine switches: the same numbers. Every switch reached both ends; the picture is still 1280x720 and the
     tone still 440 Hz at the end; the longest time without a frame is within 150 ms of the baseline's
  3  a Python feed under the frame, watched by the C++ viewer (test_feed_view) under the frame
  4  a viewer on a machine with another secret joins the sealed call: it gets no frame and no block
"""
import os, subprocess, sys, tempfile, threading, time
B = os.path.abspath(sys.argv[1]); ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "python")); os.environ.setdefault("FROGCOMMS_LIB", B)
T = tempfile.mkdtemp(prefix="comms-frame-")
SECRETS, OTHER = os.path.join(T, "secrets"), os.path.join(T, "other")
for d in (SECRETS, OTHER):
    os.mkdir(d); open(os.path.join(d, "default"), "wb").write(os.urandom(48))
os.environ["FROGNET_CHANNEL_SECRETS"] = SECRETS
os.environ["FROGNET_GUID_FILE"] = os.path.join(T, "no-fnid")       # this machine is no node: each program sends as comms-<name>
import numpy as np  # noqa: E402
from frogcomms import channel  # noqa: E402
from frogcomms.room import Room  # noqa: E402
from frogcomms.call import Call  # noqa: E402
from frogcomms.media import SyntheticSource  # noqa: E402
from frogcomms.audio import ToneSource  # noqa: E402

RAM, MEDIA, ROLE = 21300, 21320, "mediahost"
ram = "127.0.0.1:%d" % RAM
logs = {n: open(os.path.join(T, n + ".log"), "w") for n in ("ram", "media", "feed")}
procs = [subprocess.Popen([os.path.join(B, "comms-ram"), "--listen", ram], stdout=logs["ram"], stderr=subprocess.STDOUT)]
time.sleep(1)
fail = 0
def check(ok, what):
    global fail
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    fail += 0 if ok else 1
def owner(*args):
    r = subprocess.run([os.path.join(B, "comms-channel"), "--ram", ram, "set", ROLE] + list(args), capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("comms-channel %s: %s" % (" ".join(args), r.stdout + r.stderr))
    return r.stdout.strip()

try:
    owner("--seal")                                         # the Role is sealed before anyone joins
    procs.append(subprocess.Popen([os.path.join(B, "comms-media"), "--listen", "127.0.0.1:%d" % MEDIA, "--ram", ram], stdout=logs["media"], stderr=subprocess.STDOUT))
    time.sleep(1)
    procs.append(subprocess.Popen([os.path.join(B, "comms-feed"), "--ram", ram, "--media", "127.0.0.1:%d" % MEDIA, "--name", "Chapel camera",
                                   "--video", os.path.join(ROOT, "media", "stock-720p.mp4"), "--tone", "440", "--channel", ROLE],
                                  stdout=logs["feed"], stderr=subprocess.STDOUT))
    time.sleep(3)
    ch = channel.for_program(ROLE, "Pythonia", "127.0.0.1", RAM)
    py = Room("127.0.0.1", RAM, "Pythonia")
    offer = next((o for o in py.offers() if o.session in py.calls_of("Chapel camera")), None)
    if not offer:
        check(False, "Python did not find the C++ feed's call"); raise SystemExit(1)
    c = Call(offer.media_host, offer.media_port, "Pythonia", offer.session)
    pkts, orig = [], c.opus.decode
    def tap(src, pkt):
        pcm = orig(src, pkt); pkts.append(pcm); return pcm
    c.opus.decode = tap
    c.start(); time.sleep(2.5)

    samples, stop = [], False                               # (time, video frames, audio blocks), every 10 ms
    def sample():
        while not stop:
            samples.append((time.monotonic(), c.stats.v_recv, c.stats.a_recv)); time.sleep(0.01)
    threading.Thread(target=sample, daemon=True).start()
    def measure(t0, t1):
        s = [x for x in samples if t0 <= x[0] <= t1]
        def longest(k):
            worst, last = 0.0, s[0][0]
            for x, y in zip(s, s[1:]):
                if y[k] != x[k]:
                    worst, last = max(worst, y[0] - last), y[0]
            return max(worst, s[-1][0] - last) * 1000
        d = s[-1][0] - s[0][0]
        return (s[-1][1] - s[0][1]) / d, (s[-1][2] - s[0][2]) / d, longest(1), longest(2)

    t0 = time.monotonic(); time.sleep(6.0); t1 = time.monotonic()
    base = measure(t0, t1)
    check(base[0] > 20 and 45 < base[1] < 55, "1 nothing switched, 6 s, sealed (aes-256-gcm, tcp): %.1f video frames/s and %.1f audio blocks/s; the longest time with "
          "no new frame %.0f ms, with no new block %.0f ms" % base)

    steps = [("a new key", ["--new-key"]), ("chorus", ["--protocol", "chorus"]), ("chacha20-poly1305", ["--algorithm", "chacha20-poly1305"]),
             ("openqasm", ["--protocol", "openqasm"]), ("a new key", ["--new-key"]), ("not sealed", ["--unseal"]), ("sealed", ["--seal"]),
             ("tcp", ["--protocol", "tcp"]), ("a new key", ["--new-key"])]
    seen0, at, worst = ch.changes, [], []
    t2 = time.monotonic()
    for name, args in steps:
        time.sleep(2.0)
        a = time.monotonic(); owner(*args); at.append((name, a))
    time.sleep(2.5); t3 = time.monotonic()
    during = measure(t2, t3)
    for i, (name, a) in enumerate(at):
        m = measure(a, a + 1.5)
        worst.append("%s %.0f/%.0f" % (name, m[2], m[3]))
    pic = c.latest("Chapel camera")
    pcm = np.frombuffer(b"".join(pkts[-150:]), np.int16)
    zc = np.count_nonzero(np.diff(np.signbit(pcm).astype(np.int8)))
    hz = zc / 2 / (len(pcm) / 16000) if len(pcm) else 0
    media_log = open(os.path.join(T, "media.log")).read(); feed_log = open(os.path.join(T, "feed.log")).read()
    took = (ch.changes - seen0, media_log.count("is now"), feed_log.count("is now"))
    check(during[0] > 0.9 * base[0] and during[1] > 0.95 * base[1] and during[2] <= base[2] + 150 and during[3] <= base[3] + 150
          and took[0] == 9 and took[1] >= 10 and took[2] >= 10 and pic and (pic.w, pic.h) == (1280, 720) and abs(hz - 440) < 5 and c.stats.decode_errors == 0,
          "2 nine switches in %.0f s under the call: %.1f video frames/s and %.1f audio blocks/s; the longest time with no new frame %.0f ms, with no "
          "new block %.0f ms. The viewer took %d settings, the media server %d, the feed %d. Picture %s, tone %.1f Hz, decode errors %d.\n"
          "        after each switch, the longest ms without a frame / without a block in the next 1.5 s: %s"
          % ((t3 - t2,) + during + took + ("%dx%d" % (pic.w, pic.h) if pic else "none", hz, c.stats.decode_errors, "; ".join(worst))))
    stop = True

    # -- a viewer whose machine holds another secret -------------------------------------------------------------
    code = ("import os,sys,time; sys.path.insert(0,%r); from frogcomms import channel; from frogcomms.room import Room; from frogcomms.call import Call\n"
            "channel.for_program(%r,'Eve','127.0.0.1',%d); r=Room('127.0.0.1',%d,'Eve'); o=[x for x in r.offers() if x.session==%r][0]\n"
            "c=Call(o.media_host,o.media_port,'Eve',o.session); c.start(); time.sleep(4); print('EVE', c.stats.v_recv, c.stats.a_recv); os._exit(0)\n"
            % (os.path.join(ROOT, "python"), ROLE, RAM, RAM, offer.session))
    eve = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, FROGNET_CHANNEL_SECRETS=OTHER), capture_output=True, text=True, timeout=60)
    got = [l for l in eve.stdout.splitlines() if l.startswith("EVE")]
    v1, a1 = c.stats.v_recv, c.stats.a_recv; time.sleep(1.0)
    check(bool(got) and got[0] == "EVE 0 0" and c.stats.v_recv - v1 > 15,
          "4 a viewer on a machine with another secret joined the sealed call for 4 s: it got %s video frames and audio blocks (the call went on: "
          "%d frames in the next second)%s" % (got[0][4:] if got else "?", c.stats.v_recv - v1, "" if got else " " + (eve.stdout + eve.stderr)[-300:]))
    c.stop()

    # -- Python -> C++, both under the frame ---------------------------------------------------------------------
    feed = Room("127.0.0.1", RAM, "*PyCam", "cam=1,mic=0,unattended=1")
    sess = "py%06d" % (int(time.time()) % 1000000)
    feed.offer_call(sess, "127.0.0.1", MEDIA); feed.join_call(sess, "127.0.0.1", MEDIA)
    pc = Call("127.0.0.1", MEDIA, "PyCam", sess, SyntheticSource(1280, 720), 24, ToneSource(660))
    pc.start(); time.sleep(2)
    out = subprocess.run([os.path.join(B, "test_feed_view"), str(RAM), "PyCam"], env=dict(os.environ, COMMS_CHANNEL=ROLE), capture_output=True, text=True, timeout=40).stdout
    check("RESULT PASS" in out and "under the frame" in out, "3 a Python feed under the frame, sealed, watched by the C++ viewer under the frame: %s"
          % " | ".join(l for l in out.strip().splitlines()[-2:] if not l.startswith("[channel]")))
    pc.stop(); feed.close(); py.close()
finally:
    for p in reversed(procs): p.terminate()
    print("logs in", T)
print("RESULT %s a call under the frame: %d fail" % ("FAIL" if fail else "PASS", fail))
os._exit(1 if fail else 0)
