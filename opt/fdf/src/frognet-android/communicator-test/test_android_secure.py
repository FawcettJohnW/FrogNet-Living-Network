#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_android_secure.py BUILD COMMS JVM TESTMEDIA -- the Android Communicator's core under the protocol layer's frame.

BUILD is the built desktop Communicator (comms-ram, comms-media, comms-feed, comms-channel, test_feed_view); COMMS its
source directory; JVM the compiled Java core and Probe; TESTMEDIA holds phone720.ivf and tone660.opus (run_tests.sh
makes them). The phone's core runs on the JVM (Probe.java): the same classes the app runs.

  Two comms-ram run, A and B. The media server and the C++ comms-feed (the stock 720p video, a 440 Hz tone) are on A,
  under the frame, and the Role is sealed before anyone joins. The phone watches the feed under the frame.

  1  6 s with nothing changed: frames and blocks per second at the phone, longest time without one
  2  nine switches made by the phone's owner code (core/Channel.java change), 2 s apart: a new key, chorus,
     chacha20-poly1305, openqasm, a new key, not sealed, sealed, tcp, a new key. Every end ran each one; the numbers
  3  the phone's owner code moves the memory to B; A is killed 3 s later. The numbers across it; at the end the phone
     is attached to B, sees the feed and its call there, and has the chat it said at A (it took 12 settings: the
     first, the nine, the move, and the setting as it stands at B)
  4  a phone whose secret is another one watches the sealed call: no frame, no block
  5  a phone that does not speak the frame is not let into the sealed call
  6  the phone as an unattended feed under the frame, sealed, watched by the C++ viewer under the frame
"""
import os, subprocess, sys, tempfile, threading, time

B, COMMS, JVM, MEDIA_DIR = [os.path.abspath(x) for x in sys.argv[1:5]]
T = tempfile.mkdtemp(prefix="android-secure-")
SECRETS, OTHER = os.path.join(T, "secrets"), os.path.join(T, "other")
for d in (SECRETS, OTHER):
    os.mkdir(d); open(os.path.join(d, "default"), "wb").write(os.urandom(48))
ENV = dict(os.environ, FROGNET_CHANNEL_SECRETS=SECRETS, FROGNET_GUID_FILE=os.path.join(T, "no-fnid"))
A, RB, MEDIA, ROLE = 22300, 22301, 22320, "mediahost"
logs = {n: open(os.path.join(T, n + ".log"), "w") for n in ("ramA", "ramB", "media", "feed")}
fail = 0


def check(ok, what):
    global fail
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    fail += 0 if ok else 1


def start(name, *args):
    return subprocess.Popen([os.path.join(B, args[0])] + list(args[1:]), stdout=logs[name], stderr=subprocess.STDOUT, env=ENV)


def probe(args, channel=ROLE, secrets=SECRETS, **kw):
    env = dict(ENV, PROBE_SECRETS=secrets)
    if channel:
        env["PROBE_CHANNEL"] = channel
    return subprocess.Popen(["java", "-cp", JVM, "Probe"] + [str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env, **kw)


def owner(port, *args):
    """The phone's owner code, as the Security tab runs it."""
    out = probe(["owner", port] + list(args)).communicate(timeout=60)[0]
    lines = [l for l in out.splitlines() if l.startswith(("DONE", "LINE", "REFUSED"))]
    return lines


ram_a, ram_b = start("ramA", "comms-ram", "--listen", "127.0.0.1:%d" % A), start("ramB", "comms-ram", "--listen", "127.0.0.1:%d" % RB)
procs = [ram_a, ram_b]
time.sleep(1)
try:
    first = owner(A, "set", "--seal")                       # the Role is sealed before anyone joins, by the phone's owner code
    procs.append(start("media", "comms-media", "--listen", "127.0.0.1:%d" % MEDIA, "--ram", "127.0.0.1:%d" % A))
    time.sleep(1)
    procs.append(start("feed", "comms-feed", "--ram", "127.0.0.1:%d" % A, "--media", "127.0.0.1:%d" % MEDIA, "--name", "Chapel camera",
                       "--video", os.path.join(COMMS, "media", "stock-720p.mp4"), "--tone", "440", "--channel", ROLE))
    time.sleep(3)

    phone = probe(["watchlog", A, "Chapel camera", 44])
    samples, tail, t_start = [], [], [None]

    def read():
        for line in phone.stdout:
            if line.startswith("S "):
                _, ms, v, a = line.split()
                samples.append((t_start[0] + int(ms) / 1000.0, int(v), int(a)))
            elif line.startswith("WATCHING"):
                t_start[0] = time.monotonic()
            else:
                tail.append(line.rstrip("\n"))
    threading.Thread(target=read, daemon=True).start()
    t_end = time.monotonic() + 15
    while t_start[0] is None and time.monotonic() < t_end:
        time.sleep(0.05)
    if t_start[0] is None:
        check(False, "the phone did not join the sealed call: %s" % " | ".join(tail[-4:])); raise SystemExit(1)
    time.sleep(2.5)

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

    t0 = time.monotonic(); time.sleep(6.0); t1 = time.monotonic(); time.sleep(0.3)
    base = measure(t0, t1)
    check(base[0] > 20 and 45 < base[1] < 55, "1 the phone watches the sealed call (aes-256-gcm, tcp), nothing changed, 6 s: %.1f video frames/s and %.1f audio blocks/s; the "
          "longest time with no new frame %.0f ms, with no new block %.0f ms" % base)

    steps = [("a new key", ["--new-key"]), ("chorus", ["--protocol", "chorus"]), ("chacha20-poly1305", ["--algorithm", "chacha20-poly1305"]),
             ("openqasm", ["--protocol", "openqasm"]), ("a new key", ["--new-key"]), ("not sealed", ["--unseal"]), ("sealed", ["--seal"]),
             ("tcp", ["--protocol", "tcp"]), ("a new key", ["--new-key"])]
    t2 = time.monotonic(); said = []
    for name, args in steps:
        time.sleep(1.2)
        said.append((name, owner(A, "set", *args)))
    time.sleep(1.5); t3 = time.monotonic(); time.sleep(0.3)
    during = measure(t2, t3)
    all_ran = [n for n, l in said if l and l[-1].startswith("DONE 3 end(s) there: 3 running it")]
    check(len(all_ran) == 9 and during[0] > 0.9 * base[0] and during[1] > 0.95 * base[1] and during[2] <= base[2] + 150 and during[3] <= base[3] + 150,
          "2 nine switches by the phone's owner code in %.0f s under the call; %d of 9 answered \"3 end(s) there: 3 running it\" (the last: \"%s\"). %.1f video "
          "frames/s and %.1f audio blocks/s; the longest time with no new frame %.0f ms, with no new block %.0f ms"
          % (t3 - t2, len(all_ran), said[-1][1][-1][5:] if said[-1][1] else "?", during[0], during[1], during[2], during[3]))

    t4 = time.monotonic()
    moved = owner(A, "move", "127.0.0.1:%d" % RB)
    time.sleep(3.0)
    ram_a.terminate(); ram_a.wait()
    time.sleep(4.0); t5 = time.monotonic(); time.sleep(0.3)
    across = measure(t4 - 0.5, t5)
    phone.wait(timeout=60)
    time.sleep(0.5)
    end = next((l for l in tail if l.startswith("END ")), "")
    check(bool(moved) and moved[-1].startswith("DONE 3 end(s) there: 3 attached there") and across[0] > 0.9 * base[0] and across[1] > 0.95 * base[1]
          and across[2] <= base[2] + 150 and across[3] <= base[3] + 150 and ("memory=127.0.0.1:%d attached=1" % RB) in end and "settings=12" in end
          and "feed_call=true" in end and "Chapel camera" in end and "chat=said by the phone before the memory moved|" in end,
          "3 the phone's owner code moves the memory to B (\"%s\"), and A is killed 3 s later. Across %.0f s: %.1f video frames/s and %.1f audio blocks/s; the longest "
          "time with no new frame %.0f ms, with no new block %.0f ms. The phone at the end: %s"
          % (moved[-1][5:] if moved else "?", t5 - t4, across[0], across[1], across[2], across[3], end[4:]))

    out = probe(["watch", RB, "Chapel camera", 4, "/dev/null"], secrets=OTHER).communicate(timeout=60)[0]
    got = [l for l in out.splitlines() if l.startswith(("VIDEO", "AUDIO", "ENDED", "NO-OFFER"))]
    check("WATCHING" in out and not any(l.startswith(("VIDEO", "AUDIO")) for l in got),
          "4 a phone whose secret is another one watched the sealed call for 4 s: %s" % ("no video frame and no audio block" if not got else " | ".join(got)))

    out = probe(["watch", RB, "Chapel camera", 4, "/dev/null"], channel="").communicate(timeout=60)[0]
    got = [l for l in out.splitlines() if l.startswith(("VIDEO", "AUDIO", "ENDED"))]
    check("WATCHING" in out and not any(l.startswith(("VIDEO", "AUDIO")) for l in got) and any(l.startswith("ENDED") for l in got),
          "5 a phone that does not speak the frame joined the sealed call: it got %s, and its planes were closed (%s)"
          % ("no video frame and no audio block" if not any(l.startswith(("VIDEO", "AUDIO")) for l in got) else " | ".join(got),
             " | ".join(l[6:] for l in got if l.startswith("ENDED"))))

    feed = probe(["feed", RB, MEDIA, "JavaCam", os.path.join(MEDIA_DIR, "phone720.ivf"), os.path.join(MEDIA_DIR, "tone660.opus"), 12])
    time.sleep(3)
    out = subprocess.run([os.path.join(B, "test_feed_view"), str(RB), "JavaCam"], env=dict(ENV, COMMS_CHANNEL=ROLE), capture_output=True, text=True, timeout=40).stdout
    feed.communicate(timeout=60)
    view = [l for l in out.strip().splitlines() if not l.startswith("[")]
    check("RESULT PASS" in out and "under the frame" in out, "6 the phone as a feed under the frame, sealed, watched by the C++ viewer under the frame: %s" % " | ".join(view[-2:])[:260])
finally:
    for p in reversed(procs):
        p.terminate()
    print("logs in", T)
print("RESULT %s the Android core under the frame: %d fail" % ("FAIL" if fail else "PASS", fail))
sys.exit(1 if fail else 0)
