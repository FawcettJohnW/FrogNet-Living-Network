#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_the_whole_cycle.py BUILD_DIR [CALLS] [OUT_DIR] -- [THE_WHOLE_USE_AS_A_SCRIPT_V1] the Communicator used as a person
uses it, twice over and again, with every switch made from the window while the call runs.

  (John 2026-11-26: "I need to be able to see who is there, to call them, to answer the call, stay in the call, and
  hang up, then do it all over again. While it is running I want to be able to switch ciphers, keys, protocols, etc.
  from the app in real time. The perf overlay should show all.")

  Two comms-ram (A and B), one comms-media, and two windows (comms-app, offscreen), John and Donna, both under the
  frame. John's window runs a script; Donna's answers what rings. In each of CALLS calls (default 3):

    John sees Donna in the room, calls her; Donna's window answers; each sees the other's picture
    while the call runs John's window changes, one after another: the cipher (aes-256-gcm, chacha20-poly1305), the
    key, the protocol (chorus, openqasm, tcp), where the memory is (A to B, or B to A), the link (300 kb/s, then
    unlimited), the cipher to "not sealed" and back
    John hangs up; Donna's window sees him leave and ends its side; both are back in the room

  Checked: every step of the script was done; every change was answered by every end running it; in every call each
  end received pictures throughout, and the longest time between two pictures across each change; Donna's window
  ended each call by itself and answered the next; the overlay's own numbers at the end (the switches each end made
  and saw, frames sent again, frames not opened).
"""
import os, re, subprocess, sys, tempfile, time

B = os.path.abspath(sys.argv[1]); ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
CALLS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
T = tempfile.mkdtemp(prefix="comms-cycle-")
OUT = os.path.abspath(sys.argv[3]) if len(sys.argv) > 3 else T
os.makedirs(OUT, exist_ok=True)
SECRETS = os.path.join(T, "secrets"); os.mkdir(SECRETS); open(os.path.join(SECRETS, "default"), "wb").write(os.urandom(48))
env = dict(os.environ, FROGNET_CHANNEL_SECRETS=SECRETS, FROGNET_GUID_FILE=os.path.join(T, "no-fnid"), QT_QPA_PLATFORM="offscreen")
A, RB, MEDIA, ROLE = 21600, 21601, 21620, "mediahost"
GAP_MS = 1500                                              # the longest time between two pictures allowed across a change
procs, logs = [], {}
def start(name, args):
    logs[name] = os.path.join(OUT, name + ".log")
    p = subprocess.Popen(args, stdout=open(logs[name], "w"), stderr=subprocess.STDOUT, env=env); procs.append(p); return p
fail = 0
def check(ok, what):
    global fail
    print(("PASS  " if ok else "FAIL  ") + what, flush=True); fail += 0 if ok else 1

try:
    for port, n in ((A, "ramA"), (RB, "ramB")):
        start(n, [os.path.join(B, "comms-ram"), "--listen", "127.0.0.1:%d" % port])
    time.sleep(1)
    r = subprocess.run([os.path.join(B, "comms-channel"), "--ram", "127.0.0.1:%d" % A, "set", ROLE, "--seal"], capture_output=True, text=True, env=env)
    if r.returncode: raise RuntimeError("comms-channel set: " + r.stdout + r.stderr)
    start("media", [os.path.join(B, "comms-media"), "--listen", "127.0.0.1:%d" % MEDIA, "--ram", "127.0.0.1:%d" % A])
    time.sleep(1)
    video = os.path.join(ROOT, "media", "stock-720p.mp4")
    common = ["--ram", "127.0.0.1:%d" % A, "--media", "127.0.0.1:%d" % MEDIA, "--no-audio", "--channel", ROLE]
    donna = start("donna", [os.path.join(B, "comms-app")] + common + ["--name", "Donna", "--synthetic", "640x360", "--auto-answer"])
    steps, at = ["wait-for Donna"], A
    for k in range(1, CALLS + 1):
        to = RB if at == A else A
        steps += ["call Donna", "stay 4",
                  "cipher chacha20-poly1305", "stay 2", "key", "stay 2",
                  "protocol chorus", "stay 2", "protocol openqasm", "stay 2", "key", "stay 2",
                  "ram 127.0.0.1:%d" % to, "stay 3",
                  "bandwidth 300", "stay 5", "bandwidth 0", "stay 4",
                  "cipher not sealed", "stay 2", "cipher aes-256-gcm", "stay 2", "protocol tcp", "stay 2",
                  "shot %s" % os.path.join(OUT, "john-call-%d.png" % k), "hang", "stay 3"]
        at = to
    steps += ["quit"]
    t0 = time.time()
    john = start("john", [os.path.join(B, "comms-app")] + common + ["--name", "John", "--video", video, "--script", ";".join(steps)])
    limit = 60 + CALLS * 75
    while john.poll() is None and time.time() - t0 < limit: time.sleep(1)
    if john.poll() is None: john.kill()
    time.sleep(1)
    jl, dl = open(logs["john"]).read(), open(logs["donna"]).read()

    check(john.returncode == 0 and "[script] finished: every step done" in jl,
          "John's window did every step of its script (%d steps, %d calls, %.0f s)%s" % (len(steps), CALLS, time.time() - t0,
          "" if john.returncode == 0 else ": exit %s; %s" % (john.returncode, (re.findall(r"\[script\] FAILED.*", jl) or ["no FAILED line"])[0])))
    done = re.findall(r"\[script\] done (\d+): (.*?) \((\d+) ms\)(?:; pictures since the step before: (\d+) in (\d+) ms, longest between two (\d+) ms)?(?:; (.*))?", jl)
    calls = [d for d in done if d[1].startswith("call ")]
    check(len(calls) == CALLS, "John called Donna %d time(s) and saw her picture each time: first picture after %s ms" % (len(calls), ", ".join(re.findall(r"first picture (\d+) ms", jl))))
    answered = re.findall(r"\[app\] in call (\S+) with John", dl)
    check(len(answered) == CALLS and len(set(answered)) == CALLS, "Donna's window answered each of the %d calls (%d different sessions)" % (CALLS, len(set(answered))))
    left = re.findall(r"\[app\] John left the call", dl)
    check(len(left) == CALLS, "Donna's window ended its side each time John hung up (%d of %d)" % (len(left), CALLS))
    changes = [d for d in done if d[1].split()[0] in ("cipher", "key", "protocol", "ram")]
    want = CALLS * 9
    ok_changes = [d for d in changes if re.search(r"(\d+) end\(s\) there: \1 (running it|attached there)", d[6] or "")]
    check(len(changes) == want and len(ok_changes) == want,
          "every change made from John's window was answered by every end running it (%d of %d)%s" % (len(ok_changes), want,
          "" if len(ok_changes) == want else "; not: " + "; ".join("%s -> %s" % (d[1], d[6]) for d in changes if d not in ok_changes)[:400]))
    took = sorted(int(m) for d in changes for m in re.findall(r"\((\d+) ms after the write\)", d[6] or ""))
    if took: print("      a change, from the write until every end had answered: median %d ms, longest %d ms" % (took[len(took) // 2], took[-1]))
    # pictures across each change: the step after a change is a "stay"; its line carries what arrived since the change began
    worst, starved = [], []
    for i, d in enumerate(done):
        if d[1].split()[0] in ("cipher", "key", "protocol", "ram") and i + 1 < len(done) and done[i + 1][3]:
            gap = max(int(d[5] or 0), int(done[i + 1][5] or 0)); n = int(d[3] or 0) + int(done[i + 1][3] or 0)
            worst.append((gap, d[1]));
            if n == 0: starved.append(d[1])
    check(worst and not starved and max(worst)[0] <= GAP_MS,
          "John's pictures kept arriving across every change: longest time between two %d ms (%s); allowed %d ms" % (max(worst)[0] if worst else -1, max(worst)[1] if worst else "none", GAP_MS))
    by = {}
    for g, what in worst: by.setdefault(what.split()[0], []).append(g)
    for k in ("cipher", "key", "protocol", "ram"):
        if k in by: print("      %-8s %d changes; longest time between two pictures: median %d ms, worst %d ms" % (k, len(by[k]), sorted(by[k])[len(by[k]) // 2], max(by[k])))
    for name, text in (("John", jl), ("Donna", dl)):
        bad = [l for l in text.splitlines() if "IGNORED" in l or "terminate called" in l or "Segmentation" in l or "No video" in l]
        check(not bad, "%s's window: nothing ignored, no crash%s" % (name, "" if not bad else ": " + bad[0][:200]))
    shots = [os.path.join(OUT, "john-call-%d.png" % k) for k in range(1, CALLS + 1)]
    check(all(os.path.exists(s) and os.path.getsize(s) > 20000 for s in shots), "a picture of John's window was taken in each call: " + ", ".join(os.path.basename(s) for s in shots))
finally:
    for p in procs:
        if p.poll() is None: p.terminate()
    time.sleep(0.5)
    for p in procs:
        if p.poll() is None: p.kill()
print("logs and pictures in " + OUT)
print("RESULT " + ("PASS" if not fail else "FAIL (%d)" % fail))
sys.exit(1 if fail else 0)
