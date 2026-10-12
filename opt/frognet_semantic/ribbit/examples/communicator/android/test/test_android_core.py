#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_android_core.py COMMS_BUILD COMMS_ROOT CORE_CLASSES MEDIA_DIR OUT_DIR

The Android Communicator's core (Java, run on the JVM by Probe) against the real comms-ram and comms-media and the C++
and Python Communicators. Every check names what it saw.
  A room:   phone + Python + C++ in one comms-ram: lobby, chat, invitations both ways, close time
  B watch:  phone watches the C++ comms-feed (stock video + 440 Hz): VP8 1280x720, ~50 audio packets/s, the tone
  C feed:   phone as an unattended feed: the C++ viewer (test_feed_view) and a Python viewer decode it, 660 Hz
  D call:   phone calls a Python participant: invitation answered, video and audio both ways, chat both ways
  E window: the C++ window (comms-app) calls the phone; the phone answers; screenshot of the C++ window"""
import math, os, subprocess, sys, threading, time
B, ROOT, CP, MD, OUT = [os.path.abspath(x) for x in sys.argv[1:6]]
sys.path.insert(0, os.path.join(ROOT, "python")); os.environ.setdefault("FROGCOMMS_LIB", B)
import av  # noqa: E402
import numpy as np  # noqa: E402
from frogcomms.room import Room  # noqa: E402
from frogcomms.call import Call  # noqa: E402
from frogcomms.media import SyntheticSource  # noqa: E402
from frogcomms.audio import ToneSource  # noqa: E402

RAM, MEDIA = 22200, 22220
IVF, OPUS = os.path.join(MD, "phone720.ivf"), os.path.join(MD, "tone660.opus")
procs, fail = [], 0


def bad(w):
    global fail
    fail += 1
    print("FAIL", w, flush=True)


def probe(*args, timeout=60):
    return subprocess.run(["java", "-cp", CP, "Probe"] + [str(a) for a in args], capture_output=True, text=True, timeout=timeout).stdout


def probe_bg(*args):
    out = {}
    def run(): out["o"] = probe(*args, timeout=120)
    t = threading.Thread(target=run); t.start()
    return t, out


def tone_of(pcm):
    if len(pcm) == 0:
        return 0.0
    zc = np.count_nonzero(np.diff(np.signbit(pcm).astype(np.int8)))
    return zc / 2 / (len(pcm) / 16000)


def decode_dump(path, src):
    """Opus packets the phone received (Probe's dump) -> 16 kHz PCM, decoded by libopus through PyAV."""
    import struct
    dec = av.CodecContext.create("libopus", "r")
    rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
    out, n = [], 0
    with open(path, "rb") as f:
        d = f.read()
    o = 0
    while o < len(d):
        sl = struct.unpack_from(">H", d, o)[0]; s = d[o + 2:o + 2 + sl].decode(); o += 2 + sl
        pl = struct.unpack_from(">I", d, o)[0]; p = d[o + 4:o + 4 + pl]; o += 4 + pl
        if s != src:
            continue
        n += 1
        for fr in dec.decode(av.Packet(p)):
            for r in rs.resample(fr):
                out.append(r.to_ndarray().reshape(-1).astype(np.int16))
    return n, (np.concatenate(out) if out else np.zeros(0, np.int16))


def field(out, key):
    return [l for l in out.splitlines() if l.startswith(key)]


try:
    procs.append(subprocess.Popen([os.path.join(B, "comms-ram"), "--listen", "127.0.0.1:%d" % RAM], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(1)
    procs.append(subprocess.Popen([os.path.join(B, "comms-media"), "--listen", "127.0.0.1:%d" % MEDIA, "--ram", "127.0.0.1:%d" % RAM],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(1)

    # -- A room ----------------------------------------------------------------------------------------------------
    print("== A room", flush=True)
    py = Room("127.0.0.1", RAM, "Pythonia")
    t, o = probe_bg("room", RAM)
    time.sleep(2.5)
    cp = subprocess.run([os.path.join(B, "room_probe"), str(RAM), "Cplus", "S1", "hello from c++", "Phone"], capture_output=True, text=True, timeout=20).stdout
    t.join(); out = o["o"]
    print(out.strip())
    print("C++ room_probe:", cp.strip().splitlines()[0] if cp.strip() else "(nothing)")
    if "Phone" not in (cp.strip().splitlines() or [""])[0]: bad("C++ does not see the phone in its lobby")
    if "chat: Phone: hello from android" not in cp: bad("C++ did not read the phone's chat")
    ros = (field(out, "ROSTER ") or ["ROSTER "])[0]
    if "Pythonia" not in ros or "Cplus" not in ros or "Phone" not in ros: bad("the phone's lobby is %s" % ros)
    if "CHAT Cplus: hello from c++" not in out: bad("the phone did not read C++'s chat")
    if not any(l.startswith("INVITATION S1 from Cplus at 127.0.0.1:8994") for l in out.splitlines()): bad("C++'s invitation did not reach the phone intact")
    lines = ["%s: %s" % (l.from_name, l.text) for l in py.read_chat("S1")]
    inv = py.invitations()
    print("python sees chat:", lines, "| invitations:", inv)
    if "Phone: hello from android" not in lines: bad("Python did not read the phone's chat")
    if not any(x.session == "S2" and x.offered_by == "Phone" and x.media_port == 8994 for x in inv): bad("the phone's invitation did not reach Python intact")
    closed = float((field(out, "CLOSED ") or ["CLOSED 99"])[0].split()[1])
    if closed > 1.0: bad("the phone's room took %.2f s to close (the held read must end at once)" % closed)
    fresh = Room("127.0.0.1", RAM, "Checker")          # a fresh read of the memory: removals are not 'newer' rows
    if any(m.name == "Phone" for m in fresh.roster()): bad("the phone's presence outlived its close (own rows must go)")
    else: print("the phone's presence row is gone from the memory after it closed:", [m.name for m in fresh.roster()])
    fresh.close()
    py.close()

    # -- B watch ---------------------------------------------------------------------------------------------------
    print("== B the phone watches the C++ feed", flush=True)
    feed = subprocess.Popen([os.path.join(B, "comms-feed"), "--ram", "127.0.0.1:%d" % RAM, "--media", "127.0.0.1:%d" % MEDIA,
                             "--name", "Chapel camera", "--video", os.path.join(ROOT, "media", "stock-720p.mp4"), "--tone", "440"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(feed)
    time.sleep(3)
    dump = os.path.join(OUT, "watch.opus")
    out = probe("watch", RAM, "Chapel camera", 6, dump)
    print("\n".join(l for l in out.splitlines() if not l.startswith("[room]")))
    v = field(out, "VIDEO Chapel camera")
    if not v: bad("the phone received no video from the C++ feed")
    else:
        n = int(v[0].split("frames=")[1].split()[0])
        if n < 110: bad("the phone received %d video frames in 6 s from a 24 fps feed" % n)
        if "keyframe_size=1280x720" not in v[0]: bad("no 1280x720 keyframe reached the phone: %s" % v[0])
    na, pcm = decode_dump(dump, "Chapel camera")
    hz = tone_of(pcm[-16000 * 3:])
    print("phone's audio from the C++ feed: %d Opus packets, decoded by libopus: %.1f s, tone %.1f Hz" % (na, len(pcm) / 16000, hz))
    if na < 260: bad("audio from C++ to the phone: %d packets in 6 s (~300 expected)" % na)
    if abs(hz - 440) > 5: bad("the tone reached the phone at %.1f Hz" % hz)
    feed.terminate(); procs.remove(feed)

    # -- C feed ----------------------------------------------------------------------------------------------------
    print("== C the phone as a feed: C++ and Python watch it", flush=True)
    t, o = probe_bg("feed", RAM, MEDIA, "JavaCam", IVF, OPUS, 16)
    time.sleep(2.5)
    cv = subprocess.run([os.path.join(B, "test_feed_view"), str(RAM), "JavaCam"], capture_output=True, text=True, timeout=40).stdout
    print("C++ viewer:", " | ".join(cv.strip().splitlines()[-3:]))
    if "RESULT PASS" not in cv: bad("the C++ viewer could not watch the phone's feed")
    pv = Room("127.0.0.1", RAM, "Pythonia")
    on = pv.calls_of("JavaCam")
    offer = next((x for x in pv.offers() if x.session in on), None)
    if not offer: bad("Python did not find the phone feed's call")
    else:
        c = Call(offer.media_host, offer.media_port, "Pythonia", offer.session)
        pk = []; orig = c.opus.decode
        def tap(src, p):
            r = orig(src, p); pk.append(r); return r
        c.opus.decode = tap
        c.start(); time.sleep(1.5)
        v0, a0 = c.stats.v_recv, c.stats.a_recv
        time.sleep(4)
        vv, aa = c.stats.v_recv - v0, c.stats.a_recv - a0
        pic = c.latest("JavaCam")
        hz = tone_of(np.frombuffer(b"".join(pk[-150:]), np.int16))
        print("phone -> Python: %d video frames, %d audio blocks in 4 s; picture %s; tone %.1f Hz" % (vv, aa, "%dx%d" % (pic.w, pic.h) if pic else "none", hz))
        if vv < 80: bad("video from the phone to Python: %d frames in 4 s" % vv)
        if not (180 <= aa <= 220): bad("audio from the phone to Python: %d blocks in 4 s (200 expected)" % aa)
        if not pic or (pic.w, pic.h) != (1280, 720): bad("Python decoded no 1280x720 picture from the phone")
        if abs(hz - 660) > 5: bad("the phone's tone arrived at %.1f Hz" % hz)
        c.stop()
    pv.close()
    t.join(); print("\n".join(field(o["o"], "SENT")))

    # -- D call ----------------------------------------------------------------------------------------------------
    print("== D the phone calls a Python participant", flush=True)
    home = Room("127.0.0.1", RAM, "Home")
    dump = os.path.join(OUT, "call.opus")
    t, o = probe_bg("call", RAM, MEDIA, "Phone", "Home", IVF, OPUS, 10, dump)
    inv, end = None, time.time() + 8
    while not inv and time.time() < end:
        inv = next(iter(home.invitations()), None)
        if not inv: home.wait_change(home.version, 0.5)
    if not inv: bad("Python got no invitation from the phone")
    else:
        print("Python: invitation from %s, session %s at %s:%d" % (inv.offered_by, inv.session, inv.media_host, inv.media_port))
        home.join_call(inv.session, inv.media_host, inv.media_port)
        c = Call(inv.media_host, inv.media_port, "Home", inv.session, SyntheticSource(1280, 720), 24, ToneSource(440))
        pk = []; orig = c.opus.decode
        def tap2(src, p):
            r = orig(src, p); pk.append(r); return r
        c.opus.decode = tap2
        c.start(); time.sleep(2)
        home.send_chat(inv.session, "hi from home")
        v0, a0 = c.stats.v_recv, c.stats.a_recv
        time.sleep(4)
        vv, aa = c.stats.v_recv - v0, c.stats.a_recv - a0
        pic = c.latest("Phone")
        hz = tone_of(np.frombuffer(b"".join(pk[-150:]), np.int16))
        lines = ["%s: %s" % (l.from_name, l.text) for l in home.read_chat(inv.session)]
        print("Python (Home) from the phone: %d video, %d audio in 4 s; picture %s; tone %.1f Hz; chat %s"
              % (vv, aa, "%dx%d" % (pic.w, pic.h) if pic else "none", hz, lines))
        h = home.my_hold(inv.session)
        print("media server's MediaHold for Home: present=%s passthrough=%s size %dx%d aimed=%d delivered=%d"
              % (h.present, h.passthrough, h.w, h.h, h.aimed, h.delivered))
        ladder = {(1280, 720), (854, 480), (640, 360), (480, 360), (320, 240), (160, 120)}
        if vv < 80: bad("call: phone -> Home video %d frames in 4 s" % vv)
        if not (180 <= aa <= 220): bad("call: phone -> Home audio %d blocks in 4 s" % aa)
        if not pic or (pic.w, pic.h) != (1280, 720): bad("call: Home decoded no 1280x720 picture from the phone")
        if abs(hz - 660) > 5: bad("call: the phone's tone reached Home at %.1f Hz" % hz)
        if "Phone: hi from the phone" not in lines: bad("call: Home did not read the phone's chat")
        t.join()
        c.stop()
    out = o["o"]
    print("\n".join(l for l in out.splitlines() if not l.startswith("[room]")))
    v = field(out, "VIDEO Home")
    if not v: bad("call: the phone received no video from Home")
    elif "keyframe_size=" not in v[0] or "none" in v[0]: bad("call: no keyframe from Home reached the phone: %s" % v[0])
    elif int(v[0].split("frames=")[1].split()[0]) < 100: bad("call: Home -> phone video: %s" % v[0])
    na, pcm = decode_dump(dump, "Home")
    hz = tone_of(pcm[-16000 * 3:])
    print("phone's audio from Home: %d packets, tone %.1f Hz" % (na, hz))
    if na < 300: bad("call: Home -> phone audio: %d packets in ~8 s" % na)
    if abs(hz - 440) > 5: bad("call: Home's tone reached the phone at %.1f Hz" % hz)
    if "CHAT Home: hi from home" not in out: bad("call: the phone did not read Home's chat")
    home.close()

    # -- E window --------------------------------------------------------------------------------------------------
    print("== E the C++ window calls the phone; the phone answers", flush=True)
    dump = os.path.join(OUT, "answer.opus")
    t, o = probe_bg("answer", RAM, "Phone", "Julie", IVF, OPUS, 14, dump)
    time.sleep(2)
    shot = os.path.join(OUT, "cpp-window-calling-phone.png")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    w = subprocess.run([os.path.join(B, "comms-app"), "--ram", "127.0.0.1:%d" % RAM, "--media", "127.0.0.1:%d" % MEDIA,
                        "--name", "Julie", "--auto-join", "Phone", "--synthetic", "1280x720", "--no-audio",
                        "--screenshot", shot, "--after", "9"], capture_output=True, text=True, timeout=60, env=env)
    t.join(); out = o["o"]
    print("\n".join(l for l in out.splitlines() if not l.startswith("[room]")))
    print("comms-app:", " | ".join([l for l in (w.stdout + w.stderr).splitlines() if "[app]" in l][-3:]))
    if "ANSWERING Julie" not in out: bad("the phone did not answer the C++ window's call")
    v = field(out, "VIDEO Julie")
    if not v or "keyframe_size=1280x720" not in v[0]: bad("the phone did not receive the C++ window's 1280x720 video: %s" % v)
    if not os.path.exists(shot): bad("no screenshot of the C++ window")
    else: print("screenshot:", shot)
finally:
    for p in reversed(procs):
        p.terminate()
print("RESULT %s the Android core against comms-ram, comms-media, C++ and Python: %d fail" % ("FAIL" if fail else "PASS", fail))
sys.exit(1 if fail else 0)
