#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_app_cycle.py COMMS_BUILD COMMS_ROOT CLASSPATH ROBO_DEPS -- [SWITCHED_FROM_THE_CALL_V1] runs AppCycleTest (the
app's CommunicatorActivity under Robolectric) against two comms-ram and a comms-media, the Role sealed, playing "Home"
(the Python Communicator, its planes under the frame): Home calls the phone and sends video and a 440 Hz tone; the
phone makes its eight changes from the call; Home hangs up; then Home answers the phone's call and stays until the
phone hangs up. Home counts what it could not open."""
import os, subprocess, sys, tempfile, threading, time
B, ROOT, CP, DEPS = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]), sys.argv[3], os.path.abspath(sys.argv[4])
T = tempfile.mkdtemp(prefix="app-cycle-")
SECRETS = os.path.join(T, "secrets"); os.mkdir(SECRETS)
open(os.path.join(SECRETS, "default"), "wb").write(os.urandom(48))
os.environ.update(FROGCOMMS_LIB=B, FROGNET_CHANNEL_SECRETS=SECRETS, FROGNET_GUID_FILE=os.path.join(T, "no-fnid"))
sys.path.insert(0, os.path.join(ROOT, "python"))
from frogcomms import channel  # noqa: E402
from frogcomms.room import Room  # noqa: E402
from frogcomms.call import Call  # noqa: E402
from frogcomms.media import SyntheticSource  # noqa: E402
from frogcomms.audio import ToneSource  # noqa: E402

RAM, RAMB, MEDIA, ROLE = 24500, 24501, 24520, "mediahost"
procs, seen, ready = [], {}, threading.Event()
for port in (RAM, RAMB):
    procs.append(subprocess.Popen([os.path.join(B, "comms-ram"), "--listen", "127.0.0.1:%d" % port], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
time.sleep(1)
r = subprocess.run([os.path.join(B, "comms-channel"), "--ram", "127.0.0.1:%d" % RAM, "set", ROLE, "--seal"], capture_output=True, text=True)
if r.returncode: sys.exit("comms-channel set: " + r.stdout + r.stderr)
procs.append(subprocess.Popen([os.path.join(B, "comms-media"), "--listen", "127.0.0.1:%d" % MEDIA, "--ram", "127.0.0.1:%d" % RAM],
                              stdout=open(os.path.join(T, "media.log"), "w"), stderr=subprocess.STDOUT))
time.sleep(1)


def home():
    channel.for_program(ROLE, "Home", "127.0.0.1", RAM)
    h = Room("127.0.0.1", RAM, "Home")
    end = time.time() + 120
    while time.time() < end and not any(m.name == "Phone" for m in h.roster()):
        h.wait_change(h.version, 0.5)
    if not any(m.name == "Phone" for m in h.roster()):
        print("[home] the phone never appeared", flush=True); return
    s = "home%06d" % (int(time.time()) % 1000000)
    print("[home] the phone is in the room; calling it (session %s)" % s, flush=True)
    h.offer_call(s, "127.0.0.1", MEDIA); h.invite("Phone", s, "127.0.0.1", MEDIA); h.join_call(s, "127.0.0.1", MEDIA)
    c = Call("127.0.0.1", MEDIA, "Home", s, SyntheticSource(640, 360), 24, ToneSource(440))
    c.start()
    ready.wait(240)                                      # the phone has made its eight changes
    seen["call1_media_from_phone"] = c.stats.a_recv
    seen["changes_home_took"] = channel.watching().changes
    print("[home] hanging up call 1 (took %d setting(s); %d audio packets from the phone)" % (seen["changes_home_took"], c.stats.a_recv), flush=True)
    c.stop(); h.leave_call(s)
    inv, end = None, time.time() + 60                    # again: the phone calls Home
    while not inv and time.time() < end:
        inv = next((x for x in h.invitations() if x.offered_by == "Phone" and x.session != s), None)
        if not inv: h.wait_change(h.version, 0.5)
    seen["invited"] = bool(inv)
    if inv:
        print("[home] the phone is calling (session %s): answering" % inv.session, flush=True)
        h.join_call(inv.session, inv.media_host, inv.media_port)
        c2 = Call(inv.media_host, inv.media_port, "Home", inv.session, SyntheticSource(640, 360), 24, ToneSource(440))
        c2.start()
        end = time.time() + 90                           # until the phone hangs up: its offer leaves the room
        while time.time() < end and any(o.session == inv.session for o in h.offers()):
            h.wait_change(h.version, 0.5)
        seen["offer_withdrawn"] = not any(o.session == inv.session for o in h.offers())
        seen["call2_media_from_phone"] = c2.stats.a_recv
        c2.stop(); h.leave_call(inv.session)
    seen["home_totals"] = channel.watching().totals()
    seen["changes_home_took"] = channel.watching().changes
    h.close()


t = threading.Thread(target=home, daemon=True); t.start()
fail = 0
try:
    p = subprocess.Popen(["java", "-Drobolectric.offline=true", "-Drobolectric.enabledSdks=33", "-Drobolectric.dependency.dir=" + DEPS,
                          "-Dram=127.0.0.1:%d" % RAM, "-Dramb=127.0.0.1:%d" % RAMB, "-Dmedia=127.0.0.1:%d" % MEDIA,
                          "-Dsecret=" + os.path.join(SECRETS, "default"), "-cp", CP, "AppCycleTest"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = []
    for line in p.stdout:
        out.append(line)
        if "READY-FOR-HOME-TO-HANG-UP" in line: ready.set()
        if line.startswith(("PASS", "FAIL", "[robo]", "ROBO", "FAILURE")) or "Exception" in line: print(line, end="", flush=True)
    p.wait(); ready.set(); t.join(60)
    text = "".join(out)
    def check(ok, what):
        global fail
        print(("PASS  " if ok else "FAIL  ") + what, flush=True); fail += 0 if ok else 1
    fail += text.count("\nFAIL  ")
    check("ROBO PASS" in text, "the app's activity ran the whole cycle under Robolectric")
    check(seen.get("changes_home_took", 0) >= 11, "Home took every setting the phone wrote (1 at the start + 8 in call 1 + 2 in call 2): %s" % seen.get("changes_home_took"))
    check(seen.get("invited") and seen.get("offer_withdrawn"), "Home was rung by the phone's call and saw the phone hang up")
    n = seen.get("home_totals") or {}
    check(n.get("received", 0) > 0 and seen.get("call1_media_from_phone", 0) > 0 and seen.get("call2_media_from_phone", 0) > 0,
          "Home received the phone's audio under the frame in both calls: %s and %s packets (%s kB in all; %s frame(s) did not open at first and were sent again)"
          % (seen.get("call1_media_from_phone"), seen.get("call2_media_from_phone"), n.get("received", 0) // 1000, n.get("not_opened")))
finally:
    for q in reversed(procs):
        q.terminate()
print("RESULT %s the phone's activity: who is there, answer, eight changes from the call, the other end hangs up, call again, two more changes, hang up: %d fail" % ("FAIL" if fail else "PASS", fail))
sys.exit(1 if fail else 0)
