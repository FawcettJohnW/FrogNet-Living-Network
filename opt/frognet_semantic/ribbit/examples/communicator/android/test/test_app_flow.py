#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_app_flow.py COMMS_BUILD COMMS_ROOT CLASSPATH -- runs AppFlowTest (the app's MainActivity under Robolectric)
against a live comms-ram + comms-media, playing "Home" (the Python Communicator): Home waits for the phone in the
lobby, calls it, sends video + a 440 Hz tone and a chat line, and reads the phone's chat."""
import os, subprocess, sys, threading, time
B, ROOT, CP = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]), sys.argv[3]
sys.path.insert(0, os.path.join(ROOT, "python")); os.environ.setdefault("FROGCOMMS_LIB", B)
from frogcomms.room import Room  # noqa: E402
from frogcomms.call import Call  # noqa: E402
from frogcomms.media import SyntheticSource  # noqa: E402
from frogcomms.audio import ToneSource  # noqa: E402

RAM, MEDIA = 24400, 24420
procs = [subprocess.Popen([os.path.join(B, "comms-ram"), "--listen", "127.0.0.1:%d" % RAM], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
time.sleep(1)
procs.append(subprocess.Popen([os.path.join(B, "comms-media"), "--listen", "127.0.0.1:%d" % MEDIA, "--ram", "127.0.0.1:%d" % RAM],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
time.sleep(1)
seen = {}


def home():
    h = Room("127.0.0.1", RAM, "Home")
    end = time.time() + 120
    while time.time() < end and not any(m.name == "Phone" for m in h.roster()):
        h.wait_change(h.version, 0.5)
    if not any(m.name == "Phone" for m in h.roster()):
        print("[home] the phone never appeared", flush=True); return
    print("[home] the phone is in the lobby; calling it", flush=True)
    s = "home%06d" % (int(time.time()) % 1000000)
    h.offer_call(s, "127.0.0.1", MEDIA); h.invite("Phone", s, "127.0.0.1", MEDIA); h.join_call(s, "127.0.0.1", MEDIA)
    c = Call("127.0.0.1", MEDIA, "Home", s, SyntheticSource(640, 360), 24, ToneSource(440))
    c.start()
    end = time.time() + 60
    while time.time() < end and s not in h.calls_of("Phone"):
        h.wait_change(h.version, 0.5)
    seen["joined"] = s in h.calls_of("Phone")
    print("[home] the phone joined the call: %s" % seen["joined"], flush=True)
    time.sleep(3)
    h.send_chat(s, "hi phone")
    end = time.time() + 30
    while time.time() < end and not any(l.from_name == "Phone" for l in h.read_chat(s)):
        h.wait_change(h.version, 0.5)
    seen["chat"] = [l.text for l in h.read_chat(s) if l.from_name == "Phone"]
    print("[home] chat from the phone: %s" % seen["chat"], flush=True)
    end = time.time() + 30
    while time.time() < end:
        st = [m.status for m in h.roster() if m.name == "Phone"]
        if st and st[0] == "online":
            break
        time.sleep(0.5)
    seen["status_after"] = [m.status for m in h.roster() if m.name == "Phone"]
    print("[home] the phone's status after it hung up: %s" % seen["status_after"], flush=True)
    c.stop()
    inv, end = None, time.time() + 40                    # the other way: the phone calls Home
    while not inv and time.time() < end:
        inv = next((x for x in h.invitations() if x.offered_by == "Phone" and x.session != s), None)
        if not inv: h.wait_change(h.version, 0.5)
    seen["invited"] = bool(inv)
    print("[home] invitation from the phone: %s" % (inv,), flush=True)
    if inv:
        seen["phone_offers"] = any(o.session == inv.session and o.offered_by == "Phone" for o in h.offers())
        h.join_call(inv.session, inv.media_host, inv.media_port)
        c2 = Call(inv.media_host, inv.media_port, "Home", inv.session, SyntheticSource(640, 360), 24, ToneSource(440))
        c2.start(); time.sleep(3)
        seen["home_got_phone_media"] = c2.stats.a_recv + c2.stats.v_recv
        end = time.time() + 15                           # the phone hangs up: its offer leaves the room
        while time.time() < end and any(o.session == inv.session for o in h.offers()):
            h.wait_change(h.version, 0.5)
        seen["offer_withdrawn"] = not any(o.session == inv.session for o in h.offers())
        print("[home] the phone withdrew its offer after hanging up: %s" % seen["offer_withdrawn"], flush=True)
        print("[home] in the phone's call %s; the phone offers it: %s" % (inv.session, seen["phone_offers"]), flush=True)
        c2.stop()
    h.close()


t = threading.Thread(target=home, daemon=True); t.start()
fail = 0
try:
    r = subprocess.run(["java", "-Drobolectric.offline=true", "-Drobolectric.enabledSdks=33", "-Drobolectric.dependency.dir=" + os.environ.get("ROBO_DEPS", "/home/claude/robo/deps"), "-Dram=127.0.0.1:%d" % RAM, "-Dmedia=127.0.0.1:%d" % MEDIA, "-cp", CP, "AppFlowTest"],
                       capture_output=True, text=True, timeout=400)
    out = r.stdout + r.stderr
    print("\n".join(l for l in out.splitlines() if "JAVA_TOOL" not in l and not l.startswith("WARNING")))
    t.join(60)
    if "ROBO PASS" not in out: fail += 1; print("FAIL the app flow under Robolectric")
    if not seen.get("joined"): fail += 1; print("FAIL Home never saw the phone join the call")
    if "hi from the android app" not in seen.get("chat", []): fail += 1; print("FAIL Home did not read the phone's chat")
    if not seen.get("invited"): fail += 1; print("FAIL Home got no invitation when the phone pressed Call")
    if not seen.get("phone_offers"): fail += 1; print("FAIL the phone's call was not offered in the room")
    if not seen.get("offer_withdrawn"): fail += 1; print("FAIL the phone's offer outlived its hang-up")
    if seen.get("status_after") != ["online"]: fail += 1; print("FAIL the phone's status after hanging up: %s" % seen.get("status_after"))
finally:
    for p in reversed(procs):
        p.terminate()
print("RESULT %s the app's own activity, end to end against comms-ram/comms-media and Python: %d fail" % ("FAIL" if fail else "PASS", fail))
sys.exit(1 if fail else 0)
