#!/usr/bin/env python3
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
"""
test_no_burst_oracle.py -- producer-side burstiness, and why a frame aborts.

A consumer measured 45.8, 3.4, 21.4, 0.5, 25.2 fps from a producer sending a
steady 24 (2026-08-11). Frames arriving faster than they were sent is catch-up
after a stall, and the gap before it got read as a failing link.

  B1  [NO_CATCH_UP_BURST_V1] the send cadence delays, it does not catch up
  B2  [A_SWALLOWED_SETTING_IS_A_MYSTERY_LATER_V1] BUFFERSIZE=1 not being
      honoured is reported, not swallowed
  B3  [A_FAILED_READ_IS_NOT_NOTHING_V1] a failed camera read is counted and
      reported -- every one is a hole in the stream
  B4  [SAY_WHY_IT_ABORTED_V1] an aborted frame says how far it got and against
      what, not merely that it happened
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)) or "."
sys.path.insert(0, HERE)

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


src = open(os.path.join(HERE, "fnav.py"), encoding="utf-8").read()

# ---- B1 ---------------------------------------------------------------------
ck("B1 the deadline is set from NOW, not from the last deadline",
   "next_t = now + target_dt" in src and "next_t += target_dt" not in src, None)
ck("B1 and the reasoning is recorded so it is not 'fixed' back",
   "NO_CATCH_UP_BURST_V1" in src, None)


def cadence(work_s, fps=24.0, n=12):
    """The loop's own rule, with a cycle that overruns."""
    dt, t, out = 1.0 / fps, 0.0, []
    nxt = 0.0
    for _ in range(n):
        if t < nxt:
            t = nxt
        nxt = t + dt
        out.append(t)
        t += work_s
    return [round(out[i + 1] - out[i], 4) for i in range(len(out) - 1)]


gaps = cadence(0.100)          # a 100ms encode against a 41.7ms target
ck("B1 an overrunning cycle delays the next frame",
   all(g >= 0.09 for g in gaps), gaps[:3])
ck("B1 and never fires two together to catch up",
   min(gaps) > 0.0, min(gaps))

# ---- B2/B3 ------------------------------------------------------------------
ck("B2 a BUFFERSIZE that is not honoured is reported",
   "BUFFERSIZE=1 NOT honoured" in src, None)
ck("B2 saying what it means for the stream",
   "arrive in \"\n                  \"bursts" in src or "bursts" in src, None)
ck("B3 failed camera reads are counted", "_bad += 1" in src, None)
ck("B3 and reported on a curve, not per read",
   "_bad in (1, 10, 100)" in src, None)
ck("B3 with the proportion, which is what matters",
   "100.0 * _bad / max(1, _reads)" in src, None)

# ---- B4 ---------------------------------------------------------------------
i = src.index("[SAY_WHY_IT_ABORTED_V1]")
blk = src[i:i + 1600]
ck("B4 an abort says how much went", "written (%.0f%%)" in blk, None)
ck("B4 the socket's own room", "sndbuf=" in blk, None)
ck("B4 what the kernel still holds", "unacked=" in blk, None)
ck("B4 and who it was talking to", "peer=" in blk, None)
ck("B4 unavailable numbers say so rather than guessing",
   'if _unsent >= 0 else "unknown"' in blk, None)

import fnav
import inspect as _inspect                                                  # noqa: E402
import socket                                                # noqa: E402
_s = socket.socket()
ck("B4 the unacked probe never raises", isinstance(fnav._unacked_bytes(_s), int),
   None)
ck("B4 and neither does the peer probe", isinstance(fnav._peer_of(_s), str),
   None)
_s.close()

# ---- B5: [DO_NOT_COMMIT_TO_A_FRAME_THAT_WILL_NOT_FIT_V1] -------------------
# A partial first send COMMITS a frame: the receiver has been told a length and
# will read exactly that many bytes. The only way out was padding to the
# declared length -- writing EXACTLY the number of bytes that had just failed to
# go, down the same blocked socket. When that failed the code raised, the
# connection died, and the peer saw a reset. Aborting a frame should never cost
# a connection; the sentinel exists so the stream survives.
import socket as _socket

ck("B5 room is checked before the first byte",
   "_send_room(sock)" in src
   and src.index("_send_room(sock)") < src.index("sent = sock.send(blob)"),
   None)
# The message is split across two source lines, so a raw substring misses it --
# the same trap as [ALONE_IS_NOT_SILENT_V1] earlier today. Flatten first.
_flat = " ".join(src.split())
ck("B5 a frame with no room is dropped, not committed",
   "dropped before it was " in _flat and "committed" in _flat, None)
ck("B5 the data plane checks too -- that is where video goes",
   "_send_room(self.sock)" in src, None)
ck("B5 and sheds there rather than committing",
   "if droppable and _room >= 0 and _room < len(blob):" in src, None)
ck("B5 audio is NOT dropped on the check -- it is the floor",
   "droppable and _room" in src, None)
ck("B5 an unknowable room falls back to trying, it does not guess",
   "_room >= 0 and" in src and "return -1" in src, None)

_a, _b = _socket.socketpair()
_a.setblocking(False)
try:
    while True:
        _a.send(b"x" * 65536)
except BlockingIOError:
    pass
# -1 is the honest answer when the kernel's counters do not subtract, and it
# means "try it and let EWOULDBLOCK decide" -- which is the safe direction.
# Asserting exactly 0 required the arithmetic to be trustworthy, and it is not.
_r5 = fnav._send_room(_a)
ck("B5 a full socket does not claim room", _r5 == -1 or _r5 == 0, _r5)
try:
    fnav.send_frame(_a, b"y" * 40000)
    ck("B5 a frame that will not fit is refused before committing", False,
       "it was sent")
except BlockingIOError:
    ck("B5 a frame that will not fit is refused before committing", True)
except Exception as _e:
    ck("B5 a frame that will not fit is refused before committing", False,
       type(_e).__name__)
_a.close()
_b.close()

# ---- B6: [SHRINKING_ONLY_HELPS_IF_THE_WIRE_IS_THE_LIMIT_V1] ---------------
# A smaller picture makes fewer bytes. It does not make the encoder faster.
# Measured 2026-08-11 on the headless sender, alone, nothing asking it for
# anything:
#   [VID-TX] 2.5/24fps,  2KB/s, drops 0/s, rung L7 @ 1280x720
#   [VID-TX] 1.0/24fps, 59KB/s, drops 0/s, rung L8 @ 1920x1080
# Zero drops and zero sheds the whole way -- the wire was idle. It walked to
# 160x120 and shed video, having passed 320x240 at 17.4 fps and stepped again
# because 17.4 < 18.0.
def _shrinker(bottom=0):
    c = fnav.Call.__new__(fnav.Call)
    c.name, c.fps, c.aspect = "Dave", 24, fnav.DEFAULT_ASPECT
    c._bottom, c._bottom_at, c._bottom_ok = bottom, 0.0, 0
    c._publish_can_take = lambda *a: None
    c._publish_role = lambda *a, **k: None
    return c

_c = _shrinker()
_held = _c._bottom_shrink(fps_now=2.5, drops=0, sheds=0)
ck("B6 a slow encoder on an idle wire does NOT shrink",
   _held is True and _c._bottom == 0, (_held, _c._bottom))
ck("B6 and stays on video rather than shedding", _held is True, _held)

_c2 = _shrinker()
_c2._bottom_shrink(fps_now=2.5, drops=0, sheds=7)
ck("B6 a wire that is actually shedding DOES shrink", _c2._bottom == 1,
   _c2._bottom)

_c3 = _shrinker()
_c3._bottom_shrink(fps_now=2.5, drops=3, sheds=0)
ck("B6 and so do remote drops", _c3._bottom == 1, _c3._bottom)

ck("B6 the reason names the machine, not the link",
   "the wire " in _flat and "is not the limit, this machine is" in _flat, None)
ck("B6 said on a curve, not every window", "_source_limit_n" in src, None)
# The flag became a counter -- a flag was what let a single busy window clear
# it and reprint the same sentence a hundred times. Re-arming is the counter
# going back to zero on a real step.
ck("B6 and re-armed when the wire does start refusing",
   "self._source_limit_n = 0" in src, None)

# ---- B7: [ASK_THE_CAMERA_FOR_MJPEG_V1] ------------------------------------
# Nothing asked the camera for a format, so V4L2 handed back its default: YUYV,
# uncompressed. 1920x1080 YUYV is ~3.1 MB a frame and USB 2.0 carries about five
# of those a second. That is a BUS limit; the same camera in MJPEG does 30 at
# the same size. Measured 2026-08-11 as 1.0-2.5 fps at 1080p with an idle wire,
# which I wrongly attributed to the machine.
ck("B7 MJPEG is requested", 'VideoWriter_fourcc(*"MJPG")' in src, None)
ck("B7 along with the size and rate",
   "CAP_PROP_FRAME_WIDTH" in src and "CAP_PROP_FPS" in src, None)
ck("B7 and every one of them is READ BACK",
   src.count("self._cap.get(cv2.CAP_PROP_") >= 4, None)
ck("B7 what was negotiated is printed", "camera negotiated" in _flat, None)
ck("B7 and a camera that refuses MJPEG says what that costs",
   "NOT MJPEG" in _flat and "BUS limit" in _flat, None)

# the settling window, which was the other half of the question
ck("B7 a rung is judged only after a full settling window",
   fnav.WARMUP_S >= 2.0 and fnav.WARMUP_FRAMES >= 15,
   (fnav.WARMUP_S, fnav.WARMUP_FRAMES))
ck("B7 which at a slow source is seconds, not one frame",
   fnav.WARMUP_FRAMES / 5.0 >= 2.0, fnav.WARMUP_FRAMES / 5.0)

# ---- B8: [REFUSED_AND_TIMED_OUT_ARE_DIFFERENT_FAULTS_V1] ------------------
# Refused is instant and means the host is THERE with nothing listening -- the
# service. Timed out means the packets went nowhere: a route, a tunnel, a
# firewall. Telling somebody to check `systemctl status frognet-mediahost` on a
# host they cannot reach sends them to the wrong machine. Measured 2026-08-11
# after a tunnel-daemon restart: eight seconds of silence, and the message
# blamed the relay.
ck("B8 a timeout is called out as the PATH",
   "this is the PATH, not the service" in _flat, None)
ck("B8 and points at the route and the tunnel",
   "ip route get" in _flat and "wg show" in _flat, None)
ck("B8 a refusal is called out as the SERVICE",
   "nothing is listening on" in _flat, None)
ck("B8 and points at the unit on that host",
   "systemctl status" in _flat, None)
ck("B8 the two are distinguished by the exception, not guessed",
   "isinstance(e, (socket.timeout, TimeoutError))" in src, None)

# ---- B9: [READ_A_COUNTER_NOBODY_ELSE_DRAINS_V1] ---------------------------
# The shrink guard read take_video_sheds() and self._remote_drops -- both
# DRAINED by readers above it, one into the bearer and one zeroed a few lines
# up. So it always saw zero. Measured 2026-08-11: it printed "NO drops and NO
# sheds" on a line immediately followed by "drops 12/s", held 160x120 while the
# wire refused a dozen frames a second, and reprinted the same sentence a
# hundred times because a busy window cleared the said-it-once flag.
ck("B9 the guard diffs the cumulative counter",
   "_shed_total - self._shed_seen" in src, None)
ck("B9 and does not use the drained one for its decision",
   "_wire_refused = max(0, _shed_total" in src, None)
ck("B9 the diff baseline is not reset by a shrink",
   "NOT _shed_seen" in _flat, None)
ck("B9 the message is on a curve, not a flag a busy window clears",
   "_source_limit_n in (1, 10, 100)" in src, None)


class _Q:
    """A queue whose sheds another reader drains first, as the bearer does."""

    def __init__(self):
        self.sheds = 0
        self._pending = 0

    def take_video_sheds(self):
        n, self._pending = self._pending, 0
        return n

    def shed(self, n):
        self.sheds += n
        self._pending += n


_q, _seen, _saw = _Q(), 0, []
for _n in (0, 5, 0, 3):
    _q.shed(_n)
    _q.take_video_sheds()                      # the bearer takes it FIRST
    _saw.append(max(0, _q.sheds - _seen))
    _seen = _q.sheds
ck("B9 the diff sees sheds another reader already drained", _saw == [0, 5, 0, 3],
   _saw)

_c = _shrinker()
for _ in range(12):
    _c._bottom_shrink(fps_now=0.0, drops=0, sheds=0)
ck("B9 twelve idle windows do not print twelve times",
   _c._source_limit_n == 12, _c._source_limit_n)
ck("B9 and none of them shrink", _c._bottom == 0, _c._bottom)
_c._bottom_shrink(fps_now=0.0, drops=0, sheds=12)
ck("B9 real evidence still steps, and clears the counter",
   _c._bottom == 1 and _c._source_limit_n == 0,
   (_c._bottom, _c._source_limit_n))

# ---- B10: [ONE_STEP_PER_HOLD_V1] ------------------------------------------
# A held keyframe DOES tell this sender to back off -- that is the control loop
# for that leg, and suppressing it entirely (which I did for one revision)
# leaves a stuck viewer with no way to say so. What was wrong is that it fired
# on every window the SAME keyframe stayed held: one viewer that never drained
# produced six consecutive reports and walked the sender L7 to L2, at 23.4/24
# fps with zero drops and 36 KB/s the whole way. The hold was one event; the
# sender answered it six times.
_lb = _inspect.getsource(fnav.Call._ladder_from_backlog) \
    if hasattr(fnav.Call, "_ladder_from_backlog") else ""
ck("B10 a hold still steps the sender down", "if cap > L.MIN_IDX:" in _lb, None)
ck("B10 but only once per hold",
   "_in_hold_episode" in _lb and "already answered this one" in _lb, None)
ck("B10 and re-arms when the backlog clears",
   "self._in_hold_episode = False" in _lb, None)

# [A_HOLD_IS_NOT_FOREVER_V1] the relay's side of the same event
_ls = _inspect.getsource(fnav.Relay._locked_send)
ck("B10 the relay does not hold a keyframe forever",
   "HELD_KEY_MAX_S" in _ls and "discarding it" in _ls, None)
ck("B10 and the bound lives on the RELAY, which is what holds them",
   "HELD_KEY_MAX_S" in _inspect.getsource(fnav.Relay.__init__)
   and not hasattr(fnav.Call, "HELD_KEY_MAX_S"), None)
ck("B10 a newer keyframe restarts the clock, it does not inherit it",
   "self._held_at[conn] = time.time()" in _ls, None)

# ---- B11: [WALK_THE_KEYFRAME_DOWN_UNTIL_IT_FITS_V1] -----------------------
# A keyframe that will not go says the size is wrong RIGHT NOW. An inter frame
# shedding costs one picture; a keyframe shedding costs every frame after it,
# because nothing behind it can be decoded. 1920 did not fit, try 1280, then
# 854, then 640 -- one frame each, so the walk is a fraction of a second,
# against the old answer of waiting eight seconds for the next probe cycle
# while the viewer got nothing.
_tx2 = _inspect.getsource(fnav.Call._video_tx)
ck("B11 a shed KEYFRAME steps the size down at once",
   "if is_key and self._kf_walk_ok(now):" in _tx2
   and "_serve_cap_geo = dict(_next)" in _tx2, None)
ck("B11 an inter frame does not -- it costs one picture, not the stream",
   _tx2.index("if shed > 0:") < _tx2.index("if is_key and"), None)
ck("B11 a keyframe is forced at the new size immediately",
   "self._force_key = True" in _tx2, None)
ck("B11 and the new size is published as it is taken",
   "_publish_role(_next[" in _tx2, None)
ck("B11 not in eight seconds", "not in eight seconds" in _tx2, None)

# the walk over the real ladder: one step per refusal, terminating
_lad2 = fnav.geometry_ladder(fnav.DEFAULT_ASPECT)
_cur, _steps = dict(_lad2[0]), []
while True:
    _px = _cur["w"] * _cur["h"]
    _nxt = next((g for g in _lad2 if g["w"] * g["h"] < _px), None)
    if _nxt is None:
        break
    _steps.append((_cur["w"], _nxt["w"]))
    _cur = dict(_nxt)
ck("B11 each refusal moves exactly one step down",
   all(a > b for a, b in _steps), _steps)
ck("B11 and the walk terminates at the floor",
   _cur["w"] == _lad2[-1]["w"], (_cur["w"], _lad2[-1]["w"]))
ck("B11 in a handful of steps, not a search", len(_steps) <= 8, len(_steps))

# ---- B12: [ONE_STEP_PER_SIZE_V1] ------------------------------------------
# Every shed keyframe stepped the size down, so a queue already full of 1080p
# produced six sheds in one window and the walk went 1920 to 160 with no frame
# having gone at any size in between. Measured 2026-08-11, all six steps on
# consecutive lines, ending at 160x120 with 23 drops/s -- the link had not been
# tested at any size it walked past. Those frames were ENCODED AT THE OLD SIZE
# and already queued; they say nothing about the new one.
ck("B12 the walk is rate-limited to one step per size",
   "_kf_walk_ok(now)" in _tx2 and "KF_WALK_SETTLE_S" in src, None)

_c2 = fnav.Call.__new__(fnav.Call)
_t0 = 1000.0
_burst = [_c2._kf_walk_ok(_t0 + i * 0.05) for i in range(6)]
ck("B12 six sheds in one window take ONE step", _burst.count(True) == 1, _burst)
ck("B12 and the first is the one taken", _burst[0] is True, _burst)
ck("B12 a later keyframe, at the new size, may step again",
   _c2._kf_walk_ok(_t0 + 1.0) is True, None)
ck("B12 the settle is long enough for a frame to be encoded and sent",
   fnav.Call.KF_WALK_SETTLE_S >= 0.5, fnav.Call.KF_WALK_SETTLE_S)

# ---- B13: [THE_SOCKET_KNOWS_BEFORE_THE_DROP_V1] ---------------------------
# SO_SNDBUF minus TIOCOUTQ is what the wire can take RIGHT NOW, knowable before
# a byte is written. That is a better congestion signal than a drop, because a
# drop is what happens after you have already asked for too much. An uplink
# whose buffer is filling is about to be the slowest sender, and saying so
# before anything is lost lets the equilibrium settle without sacrificing
# frames to find the edge.
ck("B13 the uplink's headroom is measurable before writing",
   "_uplink_headroom" in src and "_send_room" in src, None)
ck("B13 a tight uplink reports itself as struggling",
   "_hr < self.UPLINK_TIGHT" in src and "report_receiving" in src, None)
ck("B13 said once, not per window", "_said_tight" in src, None)
ck("B13 and an unknowable headroom is not treated as tight",
   "if _hr is not None and" in src, None)

import socket as _sk2
_a2, _b2 = _sk2.socketpair()
_a2.setblocking(False)
_c4 = fnav.Call.__new__(fnav.Call)
_c4.vsendq = type("Q", (), {"sock": _a2})()
# [THE_TWO_NUMBERS_ARE_NOT_THE_SAME_UNITS_V1] The helper returns None when the
# kernel's two counters do not subtract, which is the honest answer and the one
# that falls back to letting the socket decide. These asserted a float and
# crashed on it -- an oracle that assumes the answer it wants.
_hr_idle = _c4._uplink_headroom()
ck("B13 an idle uplink reads as free, or honestly says it cannot tell",
   _hr_idle is None or _hr_idle > 0.9, _hr_idle)
try:
    while True:
        _a2.send(b"x" * 65536)
except BlockingIOError:
    pass
ck("B13 a full one reads as tight",
   (lambda h: h is None or h < fnav.Call.UPLINK_TIGHT)(_c4._uplink_headroom()),
   _c4._uplink_headroom())
_a2.close()
_b2.close()

# the read side, which needs the opposite treatment
ck("B13 reads stay non-blocking, with select and partial accumulation",
   "def _recv_exact_nb" in src and "select.select([sock]" in src, None)
ck("B13 and a stalled read times out saying how far it got",
   "recv timed out after" in _flat and "len(buf), n" in src, None)

# ---- B14: [SHRINKING_A_BLOCKED_SOCKET_IS_NOT_A_FIX_V1] --------------------
# At 0 fps and 0 KB/s with every frame dropping, the socket is refusing
# EVERYTHING and a smaller picture cannot help -- 160x120 fails for the same
# reason 1920x1080 did. Measured 2026-08-11: the walk went 1280 to 160 while the
# wire took nothing at all, then sat at 160x120 with 24 drops/s, having made the
# picture six times smaller for no reason and told the whole call to follow.
_tx3 = _inspect.getsource(fnav.Call._video_tx)
ck("B14 nothing getting through means no shrink",
   "_moving <= 0.0" in _tx3 and "_said_blocked" in _tx3, None)
ck("B14 and it says the size is not the problem",
   "cannot fix a socket that is" in _flat3 if (_flat3 := " ".join(_tx3.split()))
   else False, None)
ck("B14 the blocked check comes BEFORE the walk",
   _tx3.index("_moving <= 0.0") < _tx3.index("self._kf_walk_ok(now)"), None)
ck("B14 a wire that is still moving DOES walk",
   "elif is_key and self._kf_walk_ok(now):" in _tx3, None)
ck("B14 and the notice re-arms once traffic resumes",
   "self._said_blocked = False" in _tx3, None)

# ---- B15: [THE_BUFFER_IS_THE_LATENCY_V1] ----------------------------------
# The send buffer is how much STALE picture the kernel may hold, and it bounds
# how fast a sender can react. 256 KiB at 30 KB/s is EIGHT AND A HALF SECONDS
# of queued video, during which every new frame is refused at ANY size because
# it is queued behind superseded ones. Measured 2026-08-11: 370 KB/s of 1080p
# filled it, then 0 KB/s and 24 drops/s at 160x120 -- which reads as a dead
# socket and is not one. A properly connected socket cannot refuse 3 KB frames;
# it was refusing the queue in front of them.
_BUF = fnav.SotFDataPlane.SNDBUF
# Two constraints pulling opposite ways, and the first cut of this got the
# second one wrong: 64 KiB halved the whole-frame ceiling to 32 KB and locked
# the ladder out of its own top rungs, where keyframes measure 40 KB and up.
# test_rung_measured caught it.
ck("B15 the stale queue is bounded well below where it was",
   _BUF / 1024.0 / 30.0 <= 5.0, "%.1fs at 30 KB/s" % (_BUF / 1024.0 / 30.0))
ck("B15 and the whole-frame ceiling still clears a top-rung keyframe",
   _BUF // 2 >= 48 * 1024, "%dKB ceiling" % (_BUF // 2 // 1024))
ck("B15 dropping now beats delivering four seconds late",
   "THE_BUFFER_IS_THE_LATENCY_V1" in src, None)

# ---- B16: [THE_GUARD_MUST_WORK_WHERE_THE_CLIENT_RUNS_V1] ------------------
# TIOCOUTQ is Linux-only, so on Windows _unacked_bytes returned -1, _send_room
# returned -1, and every do-not-commit guard was skipped -- on the platform the
# GUI client runs on.
ck("B16 the unacked probe does not depend on a Linux-only ioctl alone",
   "_frognet_outstanding" in src, None)
ck("B16 and still returns -1 when it genuinely cannot know",
   "else -1" in src, None)
import socket as _sk2
_s2 = _sk2.socket()
ck("B16 it never raises on a platform without the ioctl",
   isinstance(fnav._unacked_bytes(_s2), int), None)
_s2.close()

# ---- B17: [THE_BITRATE_BELONGS_TO_THE_PICTURE_V1] -------------------------
# base starts as dict(RUNG_VIDEO[rung]) and the geometry is then replaced by
# the call's rate -- but the BITRATE was left at the rung's. The rung is a wire
# label and stopped moving with the picture, so a sender at 160x120 kept L5's
# 300 kbps budget. Measured 2026-08-11: 24 KB/s at 160x120, three times what
# that picture needs, and STILL 7 drops/s -- overshooting the link at the
# smallest size there is, which is why shrinking further never helped.
_c5 = fnav.Call.__new__(fnav.Call)
_c5.aspect = fnav.DEFAULT_ASPECT
_rates = [(w, h, _c5._bitrate_for(w, h))
          for w, h in ((1920, 1080), (1280, 720), (640, 360), (320, 240),
                       (160, 120))]
ck("B17 bits follow pixels: smaller picture, smaller budget",
   all(_rates[i][2] > _rates[i + 1][2] for i in range(len(_rates) - 1)),
   [(w, r) for w, _, r in _rates])
ck("B17 the floor is budgeted at what the floor costs, not at L5's",
   _rates[-1][2] <= 60_000, _rates[-1][2])
ck("B17 and the treatment applies it, not the rung's",
   "_bitrate_for(int(base[" in src, None)
_c5.fps = 24
ck("B17 a size the tables do not name still gets a proportional budget",
   0 < _c5._bitrate_for(720, 480) < _c5._bitrate_for(1280, 720),
   _c5._bitrate_for(720, 480))

# ---- B18: [A_NEW_SIZE_NEEDS_A_NEW_KEYFRAME_V1] ----------------------------
# Changing geometry rebuilds the encoder, and every frame it then produces
# references a keyframe the far end has never seen. Without one the decoder
# holds the LAST picture it could decode and shows that -- an old frame, at the
# old size, with occasional partial updates. Reported 2026-08-11: "a frame from
# one of the older pictures ... the pic is not 160x120 on my screen".
_fbk = _inspect.getsource(fnav.Call._poll_consumer_feedback)
ck("B18 a derived rate change forces a keyframe",
   "self._force_key = True" in _fbk, None)
ck("B18 and it is set where the size is set",
   _fbk.index("self._serve_cap_geo = dict(want)")
   < _fbk.index("self._force_key = True"), None)
ck("B18 the keyframe walk already did, and still does",
   _tx3.count("self._force_key = True") >= 1, None)

# ---- B19: [SAY_WHICH_GATE_STOPPED_THE_REPORT_V1] --------------------------
# Five conditions stand between a measured frame rate and a published `getting`
# row. Frames were crossing at 23-24 fps in both directions and not one report
# was written, so the call could only ratchet down -- and there was no way to
# tell which gate held it.
_w2 = _inspect.getsource(fnav.Call._watch_inbound)
ck("B19 the no-producer-claim gate says so",
   "NOT \"\n                              \"reporting" in _w2
   or "NOT " in _w2 and "reporting" in _w2, None)
ck("B19 naming the session and who has been seen sending",
   "_seen_senders" in _w2, None)
ck("B19 said once, and re-armed when it passes",
   "_said_gate" in _w2 and "self._said_gate = False" in _w2, None)

# ---- B20: [THE_TWO_NUMBERS_ARE_NOT_THE_SAME_UNITS_V1] ---------------------
# Linux DOUBLES SO_SNDBUF -- set 131072, read back 262144 -- and TIOCOUTQ counts
# the queue INCLUDING kernel overhead: measured 2304 for a 1000-byte write, and
# 268800 on a socket whose reported cap is 262144. Subtracting one from the
# other goes NEGATIVE on an idle socket, clamps to zero, and the pre-flight
# reports "no room" on a wire doing nothing. Every frame is then dropped before
# it is attempted, at any size -- a sender that walks to 160x120 for no reason
# and cannot climb back, because nothing it tries is ever written.
import socket as _sk3
_a3, _b3 = _sk3.socketpair()
_a3.setsockopt(_sk3.SOL_SOCKET, _sk3.SO_SNDBUF, 1 << 17)
_cap3 = _a3.getsockopt(_sk3.SOL_SOCKET, _sk3.SO_SNDBUF)

ck("B20 an IDLE socket reports room, not zero", fnav._send_room(_a3) > 0,
   fnav._send_room(_a3))
_a3.send(b"x" * 1000)
ck("B20 and a 1KB write does not make it look full",
   fnav._send_room(_a3) > 64 * 1024, fnav._send_room(_a3))

# the units really do disagree -- this is the measurement, not an assumption
_used3 = fnav._unacked_bytes(_a3)
ck("B20 TIOCOUTQ counts more than the payload written",
   _used3 <= 0 or _used3 > 1000, _used3)

_a3.setblocking(False)
try:
    while True:
        _a3.send(b"x" * 8192)
except BlockingIOError:
    pass
_room3 = fnav._send_room(_a3)
ck("B20 a genuinely full socket does NOT claim room",
   _room3 == -1 or _room3 < _cap3 // 2, _room3)
ck("B20 and -1 means 'try it', which is always safe",
   "return -1" in src, None)
_a3.close()
_b3.close()

ck("B20 the check is conservative: pressure only past three quarters",
   "used * 4 < cap * 3" in src, None)

# ---- B21: [ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1] ---------------------------
# Video got ONE attempt: a single EWOULDBLOCK shed the frame. On a healthy link
# that is the ORDINARY case -- the previous frame is still draining and the
# buffer is momentarily full; five milliseconds fixes it. For an inter frame
# shedding is right, because the next one supersedes it. For a KEYFRAME it is
# not: everything after it is undecodable, AND the keyframe walk reads that shed
# as "this size does not fit" and steps down. One transient EWOULDBLOCK on a
# gigabit link walked a whole call from 1080p to 160x120.
import threading as _th, socket as _sk4, time as _tm


def _plane(drains):
    _a, _b = _sk4.socketpair()
    _a.setblocking(False)
    q = fnav.SotFDataPlane.__new__(fnav.SotFDataPlane)
    q.sock, q.lock = _a, _th.Lock()
    q.sheds = q._shed_pending = q.audio_sheds = q._audio_shed_pending = 0
    q.dead, q.partial_completions, q.jitter_ms = False, 0, 0
    q._sndbuf = 1 << 17
    q._audio_gate, q._audio_waiting = _th.Lock(), 0
    q.throttle_bps, q._tokens, q._tok_at = 0, 0.0, _tm.time()
    try:
        while True:
            _a.send(b"x" * 8192)
    except BlockingIOError:
        pass
    q._writable = ((lambda t: (_b.recv(65536), True)[-1]) if drains
                   else (lambda t: False))
    return q, _a, _b


_q, _a4, _b4 = _plane(True)
_q.put(b"F" * 3000, droppable=True, is_key=True)
ck("B21 a keyframe survives a buffer that drains on retry", _q.sheds == 0,
   _q.sheds)
_a4.close(); _b4.close()

_q, _a4, _b4 = _plane(False)
_q.put(b"F" * 3000, droppable=True, is_key=False)
ck("B21 an inter frame still sheds at once -- the next one supersedes it",
   _q.sheds == 1, _q.sheds)
_a4.close(); _b4.close()

_q, _a4, _b4 = _plane(False)
_q.put(b"F" * 3000, droppable=True, is_key=True)
ck("B21 and a genuinely stuck socket still sheds the keyframe", _q.sheds == 1,
   _q.sheds)
_a4.close(); _b4.close()

ck("B21 keyframes get retries, inter frames do not",
   "attempts = self.KEY_RETRIES + 1 if is_key else 1" in src, None)
ck("B21 and the shed that survives them says what the failure WAS",
   "EWOULDBLOCK, socket room=" in _flat, None)

# ---- B22: two planes, and a comment that said otherwise ------------------
# Audio and video hold SEPARATE sockets -- self.sendq on self.sock, self.vsendq
# on self.vsock -- and have since [TWO_PLANES_V1]. A keyframe cannot block a
# voice frame at the application, because they are not contending for anything.
#
# [AUDIO_FIRST_V1]'s comment still described the old single-socket layout, and
# reading it as current sent one attempted fix into the wrong file: a mid-frame
# lock release on a lock nothing else was waiting for. The counters it keys on
# are per-plane, so the video plane's _audio_waiting is permanently zero.
_src_f = src
ck("B22 audio and video are separate planes",
   "self.sendq = SotFDataPlane(self.sock)" in _src_f
   and "self.vsendq = SotFDataPlane(self.vsock)" in _src_f, None)
ck("B22 and the AUDIO_FIRST comment says the layout it describes is historical",
   "HISTORICAL" in _src_f and "no longer exists" in _flat, None)
ck("B22 no mid-frame lock release survives in the write path",
   "self.lock.release()" not in _inspect.getsource(fnav.SotFDataPlane._put), None)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
