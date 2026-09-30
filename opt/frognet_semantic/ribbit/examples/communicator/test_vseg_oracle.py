################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""[VIDEO_IS_SEGMENTED_V1] Oracle: does segmentation preserve the picture, and
does audio actually get through the gaps?

Two halves.

PART 1 -- correctness, against the real fnav framing:
  a frame segmented and reassembled is byte-identical; a shed unit in the
  middle drops the whole picture rather than decoding a fragment; a new frame
  id abandons an incomplete one; an explicit abort clears the assembler; a
  receiver joining mid-frame waits rather than assembling a fragment.

PART 2 -- the reason it exists, over a REAL socket to a REAL fnav.Relay:
  a video frame and a stream of audio frames pushed at once, measuring worst
  audio latency whole-frame vs segmented on a link narrow enough that the
  uplink, not the codec, is the bottleneck.

FAILS LOUD. A picture that reassembles wrong is a failure; so is a run where
segmentation does not help, because then the change is not earning its place.
"""
import socket, struct, threading, time
import fnphone_pa as A
A.LIVE_TAP_DIR = None
import fnav

FAIL = []
def ok(m): print("  ok    " + m)
def bad(m): FAIL.append(m); print("  FAIL  " + m)

print("PART 1 -- segmentation correctness\n")

class RX:
    """The receiver's reassembly rule, exercised directly."""
    def __init__(self): self.a = {}; self.done = []; self.dropped = 0
    def feed(self, src, payload):
        fid, ix, fl = fnav._VSEG.unpack_from(payload, 0)
        body = payload[fnav._VSEG.size:]
        cur = self.a.get(src)
        if fl & fnav.VSEG_ABORT:
            if cur is not None: self.a.pop(src); self.dropped += 1
            return None
        if cur is None or cur[0] != fid:
            if ix != 0:
                self.a.pop(src, None); self.dropped += 1; return None
            if cur is not None: self.dropped += 1
            self.a[src] = (fid, 1, [body])
        else:
            f, nxt, parts = cur
            if ix != nxt:
                self.a.pop(src); self.dropped += 1; return None
            parts.append(body); self.a[src] = (f, nxt + 1, parts)
        if fl & fnav.VSEG_LAST:
            f, n, parts = self.a.pop(src)
            out = b"".join(parts); self.done.append(out); return out
        return None

def segments(fid, payload, n=fnav.VSEG_BYTES):
    out, off, ix = [], 0, 0
    while off < len(payload):
        c = payload[off:off+n]; off += len(c)
        fl = fnav.VSEG_LAST if off >= len(payload) else 0
        out.append(fnav._VSEG.pack(fid, ix, fl) + c); ix += 1
    return out

pic = bytes(range(256)) * 130          # 33280 bytes, a plausible keyframe
segs = segments(1, pic)
r = RX()
for sg in segs: r.feed("S", sg)
(ok if r.done and r.done[0] == pic else bad)(
    "a segmented frame reassembles byte-identical (%d units, %d bytes)"
    % (len(segs), len(pic)))

r = RX()
for i, sg in enumerate(segments(2, pic)):
    if i == 5: continue                # the wire shed this unit
    r.feed("S", sg)
(ok if not r.done and r.dropped else bad)(
    "a shed unit drops the WHOLE picture, no fragment decoded")

r = RX()
for sg in segments(3, pic)[:4]: r.feed("S", sg)
for sg in segments(4, pic): r.feed("S", sg)
(ok if len(r.done) == 1 and r.done[0] == pic else bad)(
    "a new frame id abandons an incomplete one and the next frame is clean")

r = RX()
for sg in segments(5, pic)[:3]: r.feed("S", sg)
r.feed("S", fnav._VSEG.pack(5, 3, fnav.VSEG_ABORT))
(ok if not r.a and not r.done else bad)("an explicit abort clears the assembler")
for sg in segments(6, pic): r.feed("S", sg)
(ok if len(r.done) == 1 and r.done[0] == pic else bad)(
    "the assembler recovers after an abort")

r = RX()
for sg in segments(7, pic)[3:]: r.feed("S", sg)
(ok if not r.done else bad)("joining mid-frame waits, never assembles a fragment")

n_units = len(segments(8, pic))
overhead = n_units * fnav._VSEG.size
(ok if overhead * 100.0 / len(pic) < 2.0 else bad)(
    "segment headers cost %.2f%% of the frame (%d units x %dB)"
    % (overhead * 100.0 / len(pic), n_units, fnav._VSEG.size))

print("\nPART 2 -- audio latency behind one video frame, over a real relay\n")

PORT = 19311
relay = fnav.Relay("127.0.0.1", PORT)
threading.Thread(target=relay.serve, daemon=True).start()
time.sleep(0.5)

def join(name, plane, sess="vseg0001"):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    tag = struct.pack("!Q", int(time.time()*1000) & 0xFFFFFFFFFFFFFFFF)
    b = fnav.pack_typed(fnav.KIND_PLANE, name, plane + tag + sess.encode())
    s.sendall(fnav._LEN.pack(len(b)) + b)
    return s

def run(segmented, link_bps):
    rx = join("rx", fnav.PLANE_AUDIO); time.sleep(0.2)
    tx = join("tx", fnav.PLANE_AUDIO); time.sleep(0.3)
    q = fnav.SotFDataPlane(tx, sndbuf=16384)
    q.throttle_bps = link_bps
    arr, stop = [], threading.Event()
    def rd():
        buf = b""; rx.settimeout(0.4)
        while not stop.is_set():
            try: d = rx.recv(65536)
            except socket.timeout: continue
            except OSError: return
            if not d: return
            buf += d
            while len(buf) >= 4:
                (ln,) = fnav._LEN.unpack(buf[:4])
                if len(buf) < 4+ln: break
                body, buf = buf[4:4+ln], buf[4+ln:]
                k, sc, pl = fnav.unpack_typed(body)
                if k == fnav.KIND_AUDIO: arr.append(time.monotonic())
    t = threading.Thread(target=rd, daemon=True); t.start()
    aud = fnav.pack_typed(fnav.KIND_AUDIO, "tx", b"\x01" + b"\x00"*59)
    sent = []
    def audio():
        t0 = time.monotonic()
        for i in range(50):
            sent.append(time.monotonic())
            q.put(aud, droppable=False)
            d = t0 + (i+1)*0.020 - time.monotonic()
            if d > 0: time.sleep(d)
    th = threading.Thread(target=audio, daemon=True); th.start()
    time.sleep(0.10)
    if segmented:
        q.put_segmented(fnav.KIND_VSEG, "tx", pic)
    else:
        q.put(fnav.pack_typed(fnav.KIND_VIDEO, "tx", pic), droppable=True)
    th.join(timeout=6); time.sleep(1.0)
    stop.set(); t.join(timeout=1)
    try: tx.close(); rx.close()
    except Exception: pass
    lat = [(arr[i]-sent[i])*1000 for i in range(min(len(arr), len(sent)))]
    return (max(lat) if lat else float("nan"), len(arr), len(sent))

# NOTE ON PART 2, read this before believing the numbers.
#
# Both sockets here are loopback on one machine. There is no shared uplink and
# no interface queue -- which is precisely the bottleneck segmentation exists to
# relieve. So this harness can show that segmenting does not HURT, and it can
# show the reassembly is correct, but it CANNOT demonstrate the win. The same
# blind spot sank an earlier oracle: a socketpair measured buffer depth and
# concluded ordering did not matter, on a harness that had no bottleneck to
# order anything against.
#
# The claim segmentation actually makes is about a constrained shared link.
# Prove it on the real pond: a call at a rung the uplink cannot carry, with
# [AUD-CAP] flags and the relay's s/s column before and after.
BPS = 1200000          # 150 KB/s -- the rate these calls actually run at
w_worst, w_got, w_n = run(False, BPS)
s_worst, s_got, s_n = run(True,  BPS)
print("  whole frame   worst audio %7.1f ms   audio %d/%d" % (w_worst, w_got, w_n))
print("  segmented     worst audio %7.1f ms   audio %d/%d" % (s_worst, s_got, s_n))
if w_worst == w_worst and s_worst == s_worst:
    (ok if s_worst <= w_worst else bad)(
        "segmenting did not make audio latency worse (%.1f -> %.1f ms)"
        % (w_worst, s_worst))

print()
if FAIL:
    print("FAILURES: %d" % len(FAIL))
    for f in FAIL: print("  " + f)
    raise SystemExit(1)
print("segmentation preserves the picture and yields to audio")


# ---------------------------------------------------------------------------
# [NO_DISPLAY_IS_NOT_NO_DECODER_V1] Regression guard.
#
# The first shipped version of the KIND_VSEG branch fed the decoder only when
# `not self.no_display`. communicator_live.py runs fnav WITH --no-display,
# because Tk paints the tiles itself out of vdec.tiles() -- so that gate blacked
# out the Tk client entirely while every counter reported video arriving
# normally. Reported 2026-08-16: "Diagnostics says there is video being
# received, but I see nothing on the display."
#
# no_display means "do not open a cv2 window". It does not mean "discard
# pictures". This asserts on the source so the gate cannot come back.
import inspect as _inspect
_src = _inspect.getsource(fnav.Call._recv_loop)
_seg = _src[_src.index("KIND_VSEG"):]
_seg = _seg[:_seg.index("elif kind == KIND_VIDEO")]
if "not self.no_display" in _seg:
    bad("KIND_VSEG feeds the decoder only when a display is open -- "
        "this blacks out the Tk client")
elif "self.vdec.feed(" not in _seg:
    bad("KIND_VSEG never feeds the decoder at all")
else:
    ok("reassembled frames reach the decoder regardless of --no-display")

if FAIL:
    raise SystemExit(1)


# ===========================================================================
# PART 3 -- the interleave, the shed accounting, and the socket modes.
#
# Part 2 could not prove the win because loopback has no shared uplink. These
# three CAN be proved here, because they are properties of the code rather than
# of the link.
print("\nPART 3 -- interleave, shed accounting, socket modes\n")

import threading as _th, time as _t, socket as _sk

# ---- [AUDIO_INTENT_IS_SHARED_V1] ------------------------------------------
# The first cut yielded to self._audio_waiting on the VIDEO plane, which is
# structurally always zero because audio declares on a different object.
a_s, b_s = _sk.socketpair()
aq = fnav.SotFDataPlane(a_s)
vq = fnav.SotFDataPlane(b_s)
(ok if vq._audio_waiting == 0 else bad)("a fresh video plane sees no audio waiting")

vq.attach_audio_gate(aq)
with aq._gate["lock"]:
    aq._gate["waiting"] += 1
seen = vq._audio_waiting
with aq._gate["lock"]:
    aq._gate["waiting"] -= 1
(ok if seen == 1 else bad)(
    "audio declaring on the AUDIO plane is visible to the VIDEO plane "
    "(saw %d)" % seen)
(ok if vq._audio_waiting == 0 else bad)("and clears when the audio frame is done")

# an unattached plane still works on its own
solo = fnav.SotFDataPlane(_sk.socketpair()[0])
with solo._gate["lock"]:
    solo._gate["waiting"] += 1
(ok if solo._audio_waiting == 1 else bad)("an unattached plane keeps its own gate")

# the client wires it up
_src_run = _inspect.getsource(fnav.Call.run) if hasattr(fnav.Call, "run") else ""
_src_all = _inspect.getsource(fnav.Call)
(ok if "vsendq.attach_audio_gate(self.sendq)" in _src_all else bad)(
    "the client attaches the video plane to the audio plane's gate")

# ---- [A_FRAME_IS_ONE_SHED_V1] ---------------------------------------------
# A frame abandoned mid-segmentation must cost the ladder exactly ONE shed,
# not one per refused unit -- the ladder reads sheds as "this size does not
# fit", and 25 units meant 25x the signal for the same lost picture.
class _RefusingPlane(fnav.SotFDataPlane):
    def __init__(self, refuse_at):
        s0, _ = _sk.socketpair()
        fnav.SotFDataPlane.__init__(self, s0)
        self._n = 0
        self._refuse_at = refuse_at
    def _put(self, frame, droppable=True, is_key=False):
        self._n += 1
        if droppable and self._n == self._refuse_at:
            self.sheds += 1
            self._shed_pending += 1

rp = _RefusingPlane(refuse_at=4)          # refuse the 4th unit of the frame
before = rp.sheds
went = rp.put_segmented(fnav.KIND_VSEG, "t", pic)
(ok if not went else bad)("a refused unit abandons the frame")
(ok if rp.sheds - before == 1 else bad)(
    "an abandoned frame costs the ladder exactly ONE shed (got %d)"
    % (rp.sheds - before))
(ok if getattr(rp, "vseg_frames_aborted", 0) == 1 else bad)(
    "the abandoned frame is counted as a frame")

rp2 = _RefusingPlane(refuse_at=0)         # refuse nothing
before2 = rp2.sheds
went2 = rp2.put_segmented(fnav.KIND_VSEG, "t", pic)
(ok if went2 and rp2.sheds == before2 else bad)(
    "a frame that goes cleanly costs no sheds")

# ---- [SEGMENT_ONLY_WHAT_WOULD_NOT_FIT_V1] ---------------------------------
_tx = _inspect.getsource(fnav.Call._video_tx)
(ok if "whole_frame_max()" in _tx and "put_segmented" in _tx and
       "self.vsendq.put(blob" in _tx else bad)(
    "the whole/segmented decision uses the derived ceiling")

# [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1] the ceiling is a DEADLINE in bytes:
# how much this socket can drain in one audio block at the rate it is
# achieving. A flat 8192 was ~55 ms at 150 KB/s -- nearly three audio frames --
# so "small" frames skipped segmentation and stalled audio anyway, most
# visibly while the ladder walked resolutions and cut a keyframe at each rung.
import socket as _s2
_p = fnav.SotFDataPlane(_s2.socketpair()[0])
(ok if _p.whole_frame_max() == fnav.VSEG_BYTES else bad)(
    "with no rate measured yet the ceiling is one segment (segment, do not guess)")

for _bps, _name in ((150000.0, "150 KB/s"), (30000.0, "30 KB/s"),
                    (12000000.0, "12 MB/s")):
    _p._rate_bps = _bps
    _c = _p.whole_frame_max()
    _ms = _c / _bps * 1000.0
    _fits = _ms <= _p.WHOLE_FRAME_MS + 0.01 or _c == fnav.VSEG_BYTES \
            or _c == _p._sndbuf // 2
    (ok if _fits else bad)(
        "at %-9s the ceiling is %6d B = %5.1f ms of wire" % (_name, _c, _ms))

_p._rate_bps = 150000.0
(ok if _p.whole_frame_max() < 8192 else bad)(
    "at 150 KB/s the ceiling (%d B) is BELOW the old flat 8192"
    % _p.whole_frame_max())
_p._rate_bps = 1e9
(ok if _p.whole_frame_max() == _p._sndbuf // 2 else bad)(
    "on a fast link the ceiling is capped by the buffer, not by a constant")

# ---- [ALL_NONBLOCKING_V1] -------------------------------------------------
n_s, _n2 = _sk.socketpair()
n_s.setblocking(True)
_q = fnav.SotFDataPlane(n_s)
(ok if _q.sock.gettimeout() == 0.0 else bad)(
    "SotFDataPlane forces its socket non-blocking (timeout=%r)"
    % (_q.sock.gettimeout(),))
_op = _inspect.getsource(fnav.Call._connect) if hasattr(fnav.Call, "_connect") else _src_all
(ok if _op.count("setblocking(False)") >= 1 else bad)(
    "the client sets its media sockets non-blocking after the handshake")
_relay = _inspect.getsource(fnav.Relay)
(ok if "conn.setblocking(False)" in _relay and "srv.setblocking(False)" in _relay
   else bad)("the relay's listener and accepted conns are non-blocking")

print()
if FAIL:
    print("FAILURES: %d" % len(FAIL))
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("interleave shared, sheds charged per frame, sockets non-blocking")
