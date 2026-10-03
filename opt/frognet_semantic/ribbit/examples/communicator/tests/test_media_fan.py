#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_media_fan.py FNAV_DIR MEDIA_BIN -- comms-media step 1, driven by clients that speak fnav.py's own wire
(fnav.pack_typed / send_frame / recv_frame): sessions, audio fan, keyframe anchoring, segmented delivery, teardown."""
import os, socket, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import fnav  # noqa: E402

PORT = 18700
srv = subprocess.Popen([sys.argv[2], "--listen", "127.0.0.1:%d" % PORT], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(0.4)
fails = []
def check(ok, what):
    if not ok: fails.append(what); print("FAIL", what)

def plane(name, kind, session, tag):
    s = socket.create_connection(("127.0.0.1", PORT))
    fnav.send_frame(s, fnav.pack_typed(fnav.KIND_PLANE, name, kind + tag + session.encode()))
    s.setblocking(False)
    return s

def drain(s, secs=0.6):
    """Every frame that arrives within `secs`, reassembling VSEG pieces the way fnav's receive path does."""
    out, asm, end = [], {}, time.time() + secs
    import select
    while time.time() < end:
        # fnav.recv_frame waits on select() with no timeout; read only when something is there
        if not select.select([s], [], [], 0.02)[0]:
            continue
        try:
            body = fnav.recv_frame(s)
        except fnav.AbortedFrame:
            out.append(("aborted", "", b"")); continue
        except (ConnectionError, OSError):
            out.append(("closed", "", b"")); break
        k, src, pl = fnav.unpack_typed(body)
        if k == fnav.KIND_VSEG:
            fid, ix, fl = fnav._VSEG.unpack_from(pl, 0)
            cur = asm.get(src)
            if cur is None or cur[0] != fid:
                cur = asm[src] = (fid, [])
            cur[1].append(pl[fnav._VSEG.size:])
            if fl & fnav.VSEG_LAST:
                out.append((fnav.KIND_VIDEO, src, b"".join(asm.pop(src)[1])))
            continue
        out.append((k, src, pl))
    return out

T = lambda c: c * 8
da, dv = plane("Donna", b"A", "S1", T(b"d")), plane("Donna", b"V", "S1", T(b"d"))
ja, jv = plane("John", b"A", "S1", T(b"j")), plane("John", b"V", "S1", T(b"j"))
ma = plane("Mallory", b"A", "S2", T(b"m"))
ea = plane("Eve", b"A", "", T(b"e"))
time.sleep(0.3)

# audio: Donna's 50 frames reach John only
for i in range(50):
    fnav.send_frame(da, fnav.pack_typed(fnav.KIND_AUDIO, "Donna", bytes([i]) * 40))
got = [f for f in drain(ja) if f[0] == fnav.KIND_AUDIO]
check(len(got) == 50 and all(src == "Donna" for _, src, _ in got) and got[7][2] == bytes([7]) * 40, "John hears all 50 of Donna's audio frames in order: %d" % len(got))
check(not drain(ma, 0.2), "Mallory (another session) hears nothing")
check(not drain(ea, 0.2), "Eve (no session) hears nothing")
check(not [f for f in drain(da, 0.2) if f[0] == fnav.KIND_AUDIO], "Donna does not hear herself")

# video: an inter before any keyframe is shed and Donna is asked for a keyframe; then a keyframe anchors John
fnav.send_frame(dv, fnav.pack_typed(fnav.KIND_VIDEO, "Donna", fnav.pack_video(7, b"inter-0", 0, False)))
got = drain(jv, 0.4)
check(not [f for f in got if f[0] == fnav.KIND_VIDEO], "no inter frame reaches an unanchored viewer")
req = [f for f in drain(dv, 0.3) if f[0] == fnav.KIND_KEYREQ]
check(len(req) == 1, "Donna is asked for a keyframe: %d request(s)" % len(req))
key = os.urandom(20000)                                   # bigger than one segment: arrives as VSEG pieces
fnav.send_frame(dv, fnav.pack_typed(fnav.KIND_VIDEO, "Donna", fnav.pack_video(7, key, 0, True)))
for i in range(5):
    fnav.send_frame(dv, fnav.pack_typed(fnav.KIND_VIDEO, "Donna", fnav.pack_video(7, b"inter-%d" % (i + 1), 0, False)))
got = [f for f in drain(jv, 0.8) if f[0] == fnav.KIND_VIDEO]
vids = [fnav.unpack_video(pl) for _, _, pl in got]
check(len(vids) == 6 and vids[0][2] == key and vids[0][0] == 7, "the 20 KB keyframe arrives whole (reassembled), then 5 inters: %d frames" % len(vids))
check([v[2] for v in vids[1:]] == [b"inter-%d" % (i + 1) for i in range(5)], "inters arrive in order after the keyframe")

# teardown: Donna's audio closes -> her video plane (same tag) is shut down too
da.close()
end = drain(dv, 1.0)
check(any(f[0] == "closed" for f in end), "Donna's video plane is torn down with her audio plane")
check(not any(f[0] == "closed" for f in drain(jv, 0.3)), "John's planes stay up")

srv.terminate(); out = srv.communicate(timeout=5)[0]
check("declared video plane" in out and "session=NONE" in out, "the server logs declarations, Eve's as session=NONE")
print("RESULT %s media server step 1 vs fnav.py clients: %d fail" % ("FAIL" if fails else "PASS", len(fails)))
sys.exit(1 if fails else 0)
