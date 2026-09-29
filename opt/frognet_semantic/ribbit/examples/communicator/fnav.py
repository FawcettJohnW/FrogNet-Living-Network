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
fnav.py -- FrogNet SotF A/V phone (the SotF infrastructure replacement).

Built on the proven in-process audio spine (fnphone_pa) and the SotF degradation ladder.
Audio is the protected floor (rungs L3/L4); video rides on top (L5-L7) and sheds FIRST when
the network can't sustain it -- exactly the SotF invariant. One type-agnostic relay fans all
peers (two now, N later). All media is in-process: PortAudio for audio, OpenCV+PyAV (VP8) for
video. No engine, no codex, no tuple plane -- just typed frames on the FNWP wire.

WIRE: each frame is [!I len][!B kind][!H srclen][src][payload].
  kind=0 AUDIO  payload = 16k mono s16le PCM block         (protected; never shed)
  kind=1 VIDEO  payload = [!I level_idx][VP8 packet bytes]  (droppable; sheds under load)
The relay forwards frames unchanged to every other peer; it never inspects kind. Audio and
video are INDEPENDENT streams -- dropping video never touches audio.

DATA PLANE (SotF): the socket is NON-BLOCKING and the sender is SEND-OR-DROP -- each frame is
written whole or dropped whole, with NO queue. Newest-wins falls out for free: the camera
grabber keeps only the freshest frame, and with nothing stockpiled the next frame offered is
always the newest, so latency never builds. When the wire is full a video frame is dropped at
once (audio, the protected floor, gets a brief writability grace first). The receiver is the
mirror image -- it keeps only the latest decoded frame per source and the audio mixer trims to
a bounded cushion, so a frame that arrives when the app isn't ready is simply superseded. This
IS Streams-over-the-Fabric: bounded latency by dropping, never by blocking or buffering.

LADDER (sotf_ladder): the sender picks send_level from its ceiling (camera+mic capability)
and a bearer index driven by telemetry -- the DROP count (frames the wire refused), not a queue
depth, since SotF keeps no queue. Video rungs require camera+mic; audio rungs require mic;
L0/L1 always available so a call never dies.
When the bearer falls to <=L4, the sender simply stops emitting video frames (audio only);
when the bearer rises again, video resumes. Nothing is held back and nothing replays -- the
far side adapts to what arrives, and what it missed is simply gone. Memory, not messages.

DEPS: sounddevice, samplerate, numpy (audio); opencv-python(-headless), av (video).
  Windows: pip install sounddevice samplerate numpy opencv-python av
  Linux/Pi: + sudo apt install libportaudio2 libsamplerate0

RUN:
  Relay:  python3 fnav.py --serve 0.0.0.0:9000
  Caller: python3 fnav.py --call 10.250.250.1:9000 --name Alice --in 23 --out 5 --cam 0
          python3 fnav.py --call 10.250.250.1:9000 --name Gorp  --cam 0
  --no-video  audio only (ceiling L4)   --no-audio  text floor only
  --cam N     camera index (default 0)  --list      list audio devices + cameras
"""
from __future__ import annotations

import argparse
import contextlib
import math
import os
import random
import select
import socket
import struct
import sys
import threading
import uuid          # [HEADLESS_PUBLISHER_V1] session/me_id minting
import time

# Reuse the PROVEN audio engine verbatim (single-sourced; fixes there propagate here).
import fnphone_pa as A
import sotf_ladder as L


@contextlib.contextmanager
def _silence_cv2_stderr():
    """Swallow OS-level stderr (fd 2) for the duration of an OpenCV camera open.
    OpenCV's V4L2/FFMPEG/obsensor backends write 'Not a video capture device' /
    'index out of range' / 'Inappropriate ioctl' to the C-level stderr fd for every
    index they fail to open -- Python logging can't suppress that. Used by BOTH the
    call-time probe and --list so neither sprays the console."""
    try:
        import cv2
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
    except Exception:
        pass
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


AUDIO_RATE = A.AUDIO_RATE
AUDIO_CH = A.AUDIO_CH

# -- typed FNWP frame: 1-byte kind tag on a source-labeled frame --------------
_LEN = struct.Struct("!I")
_KIND = struct.Struct("!B")
_SRC = struct.Struct("!H")
_LVL = struct.Struct("!I")
KIND_AUDIO = 0
KIND_VIDEO = 1
# [DOWNLINK_BACKPRESSURE_V1] Relay -> sender. Payload is one int: video frames
# the relay shed for a viewer since the last report.
#
# The relay cannot re-encode, so all it can do with a capped viewer is drop whole
# video frames -- which yields a SLIDESHOW at full resolution, not a lower rung.
# The rung lives in the sender, and the sender's own uplink is clean, so it has
# no idea. This frame is how it finds out: the relay's drops are fed to the
# bearer exactly like the sender's own, and the ladder walks down until the
# picture fits the link.
KIND_BACKPRESSURE = 2
# [TWO_PLANES_V1] First frame on each connection declares which plane it is.
# Payload is b"A" or b"V". Sent once, immediately after connect, before anything
# else -- it reuses the existing framing so there is no handshake to negotiate.
#
# Why two sockets: audio and video sharing one wire means something must
# arbitrate, and every arbitration scheme leaks. A voice packet waiting behind a
# 40 KB keyframe is a pause you can hear, and no lock ordering fixes it because
# the TCP send window is shared too. Two connections and there is nothing to
# arbitrate: separate windows, separate buffers, the kernel schedules both.
#
# It also makes the per-viewer cap honest -- the relay budgets the VIDEO plane
# and never touches the audio plane, so audio survives by construction rather
# than by a branch that someone can get wrong later. Twice today, someone did.
KIND_PLANE = 3
PLANE_AUDIO = b"A"

# [VIDEO_IS_SEGMENTED_V1] A video frame goes out as a run of MSS-sized units
# instead of one indivisible write.
#
# The point is not TCP ordering -- audio and video hold separate sockets, so
# nothing on the video socket delays the audio socket at that layer. The point
# is the SHARED UPLINK. When the link is the bottleneck both sockets drain into
# one pipe, and a 32 KB keyframe handed to it in one write occupies that pipe
# as an indivisible burst; audio queued behind it in the interface queue waits
# for the whole thing no matter which socket it is on. Segment the write and
# the interface drains between the pieces, so the audio socket gets serviced in
# the gaps. That is the release point.
#
# 1368 bytes because that is what ss reports as mss on every tunnel leg
# (pmtu 1420, WireGuard overhead off the top). Not a guess, and not an MTU
# claim about the application layer: TCP re-segments regardless. The number
# that matters is the YIELD INTERVAL -- how long the sender is committed before
# it can look at the audio queue again -- and it must be shorter than one audio
# frame or audio simply queues between yields.
#
# Measured 2026-08-16, segment size against worst audio latency at a fixed
# 16 KiB buffer: 512B -> 10.4ms, 1024B -> 12.9ms, 4096B -> 108.9ms,
# 8192B -> 143.0ms, 16384B (== the buffer) -> 142.2ms. Setting the segment to
# the buffer size is the same as not segmenting at all.
KIND_VSEG = 6              # one piece of a video frame
_VSEG = struct.Struct("!HHB")   # frame_id, segment index, flags
VSEG_LAST = 0x01           # this piece completes the frame
VSEG_ABORT = 0x02          # give up on the frame being assembled; discard it

# Small enough that the yield interval beats a 20 ms audio frame on the links
# these calls actually run at, and aligned to the tunnel's mss.
VSEG_BYTES = 1368

# [SEGMENT_ONLY_WHAT_WOULD_NOT_FIT_V1] Frames at or under this go out whole.
# Above it they are segmented, because a write this large is what owns the
# shared uplink long enough to starve the audio socket behind it. 8 KiB at the
# 150 KB/s these calls run at is ~55 ms of pipe -- already longer than two
# audio frames, and the point where breaking it up starts to pay for the extra
# shed exposure it costs.
VSEG_WHOLE_MAX = 8192

PLANE_VIDEO = b"V"

# [KEYFRAME_ON_REQUEST_V1] Relay -> SENDER: "N viewers of yours are unanchored;
# emit a keyframe now."
#
# A viewer that has shed a keyframe decodes against a reference it never got and
# stays pixellated until the NEXT keyframe. Nothing used to ask for one, so the
# wait was however long libvpx felt like: measured on real libvpx under this
# program's own VP8 options, keyframes came 128 frames apart -- 5.3 s at 24 fps,
# on synthetic noise, which forces them MORE often than real video does.
#
# The recovery is not bandwidth-bound and slowing the sender does not help. _fan
# already sheds inters for an unanchored viewer WITHOUT charging them, so budget
# accrues and the held keyframe goes out the instant there is room. The viewer is
# waiting on the ENCODER. So ask it.
#
# This is the relay->sender channel [DOWNLINK_BACKPRESSURE_V1] was withdrawn for:
# it wrote to peer sockets from _fan, which runs on each SENDER'S pump thread,
# with no per-socket lock -- the bytes interleaved and the length prefix desynced.
# [SOCKET_WRITE_LOCK_V1] exists now and its own note calls itself "the
# prerequisite for any relay->sender channel". This uses it. The drop-COUNT feed
# stays withdrawn: one capped viewer must not walk the rung down for everybody.
# A keyframe request is different in kind -- it costs one frame, helps every
# viewer, and cannot bias the ladder.
KIND_KEYREQ = 4
# [AUDIO_BACKPRESSURE_V1] The relay telling a sender that its AUDIO is not
# reaching a viewer.
#
# KIND_BACKPRESSURE (2) is emitted inside `if _pl == PLANE_VIDEO`, so it can only
# ever describe video. The relay could shed audio steadily and there was NO PATH
# by which the sender learned it -- not a logging gap, the protocol had no frame
# for it. Meanwhile Bearer.sample treats audio_shed as the FIRST and strongest
# down-signal, ahead of drops and fps... but only for audio the sender's OWN
# uplink queue refused. Audio lost downstream produced nothing.
#
# So the one condition that must collapse the video rung hardest exerted exactly
# zero pressure on it, and a static camera sat at 720p while its viewer heard
# gaps. Audio drives the resolution; this is the wire that lets it.
#
# Payload is the same shape as KIND_BACKPRESSURE: one !I of starved viewers.
KIND_AUDIO_BACKPRESSURE = 5

# [MEDIAHOLD_IS_MEMORY_V1] The namespace comms_control writes and reads under. The
# relay's cap read already used this literal; the hold publish must use the SAME one
# or the Communicator looks in a namespace nothing writes.
SERVICE_COMMS = "communicator"



def camera_backend():
    """[WIN_DSHOW_V1] Which cv2 capture backend to open a camera with.

    On Windows cv2.VideoCapture(index) defaults to Media Foundation, which does not
    enumerate reliably -- indices that exist report no device, and an open can hang
    for seconds before failing. DirectShow is the one that works, and it is what this
    project already used: the retired client drove capture through ffmpeg -f dshow.

    Not a fallback and not a probe of alternatives: it is the correct API for the
    platform, chosen by the platform.
    """
    if not sys.platform.startswith("win"):
        return 0                               # CAP_ANY: let cv2 choose elsewhere
    try:
        import cv2 as _cv2
        return getattr(_cv2, "CAP_DSHOW", 0)
    except Exception:
        return 0


def open_camera(index):
    """Open a camera the ONE way. Every caller uses this -- Camera and sound, the
    lobby preview, fnav's probe and its capture loop. A chooser that opened a device
    with a different backend from the one the call uses would find a camera the call
    then could not."""
    import cv2 as _cv2
    b = camera_backend()
    return _cv2.VideoCapture(index, b) if b else _cv2.VideoCapture(index)


def media_capability():
    """[MEDIA_DEPS_ARE_CHECKED_V1] What can this machine actually send?

    None of these ship in the bundle -- they are pip installs -- and until now nothing
    checked. The client started, the lobby looked normal, and pressing Start a call
    died inside a Tk callback with ModuleNotFoundError: No module named 'av'. A
    missing dependency is a fact about the machine, knowable at startup, and the user
    should be told it there rather than by a traceback at the worst moment.

    Returns {"video", "audio", "missing", "why"} -- what works, what to install.
    """
    missing, why = [], []
    video = audio = True
    try:
        import cv2                                    # noqa: F401
    except Exception:
        video = False
        missing.append("opencv-python")
        why.append("no camera capture (cv2)")
    try:
        import av                                     # noqa: F401
    except Exception:
        video = False
        missing.append("av")
        why.append("no video encoder (PyAV)")
    try:
        import sounddevice                            # noqa: F401
    except Exception:
        audio = False
        missing.append("sounddevice")
        why.append("no audio device access")
    try:
        import samplerate                             # noqa: F401
    except Exception:
        audio = False
        missing.append("samplerate")
        why.append("no 48k->16k resampling")
    return {"video": video, "audio": audio,
            "missing": missing, "why": "; ".join(why)}


def media_install_hint(missing):
    if not missing:
        return ""
    return "pip install " + " ".join(missing)



def pack_typed(kind: int, src: str, payload: bytes) -> bytes:
    s = src.encode("utf-8")
    return _KIND.pack(kind) + _SRC.pack(len(s)) + s + payload


def unpack_typed(frame: bytes):
    kind = _KIND.unpack_from(frame, 0)[0]
    off = _KIND.size
    (slen,) = _SRC.unpack_from(frame, off); off += _SRC.size
    src = frame[off:off + slen].decode("utf-8", "replace"); off += slen
    return kind, src, frame[off:]


# [KEYFRAME_FLAG_V1] The level field is a 4-byte int carrying a value of 0..7.
# The high bit marks a keyframe, so the relay can tell one without parsing the
# bitstream -- it is deliberately content-blind, and a codec-specific parser in
# the fan path would end that.
#
# The sender already knows: on_video_sent(len, is_key) has taken the flag all
# along. It simply never reached the wire, so the relay shed keyframes exactly
# like any other frame -- and being the largest frame in the stream, a keyframe
# is the one most likely to exceed a remaining budget. Dropping it means every
# inter-frame after it references a picture the decoder never got: full
# pixellation until the next keyframe, which is also the most likely to be shed.
_KEYFRAME_BIT = 0x80000000


def pack_video(level_idx: int, vp8: bytes, codec_id: int = 0,
               is_key: bool = False) -> bytes:
    lvl = (int(level_idx) & 0x7FFFFFFF) | (_KEYFRAME_BIT if is_key else 0)
    return _LVL.pack(lvl) + _KIND.pack(codec_id) + vp8


def unpack_video(payload: bytes):
    raw = _LVL.unpack_from(payload, 0)[0]
    codec_id = _KIND.unpack_from(payload, _LVL.size)[0]
    return (raw & 0x7FFFFFFF), codec_id, payload[_LVL.size + _KIND.size:]


def video_is_key(frame: bytes) -> bool:
    """True if this KIND_VIDEO frame carries a keyframe.

    Reads the flag by offset without decoding the source name -- the relay does
    this per frame per viewer and must not pay for a utf-8 decode to find out.
    """
    try:
        off = _KIND.size
        (slen,) = _SRC.unpack_from(frame, off)
        off += _SRC.size + slen
        return bool(_LVL.unpack_from(frame, off)[0] & _KEYFRAME_BIT)
    except Exception:
        return False


# [ABORT_SENTINEL_V1] Written in place of the remainder of a frame the sender
# could not finish. The receiver is already blocked waiting for the declared
# length, so it reads this where payload should have been, discards the frame in
# progress, and resyncs on the next length prefix.
#
# Why it is needed at all: there is no portable way to ask a socket whether N
# bytes will fit. select()/FD_SET report only that SOME room exists
# (SO_SNDLOWAT: 2048 on Linux, 1 on Windows), and SIOCOUTQ is Linux-only --
# fcntl and termios do not exist on Windows, and the Communicator is a Windows
# client. So a partial write cannot be prevented, only recovered from.
#
# Without it, a partial write on a length-prefixed stream is fatal: the receiver
# consumes the NEXT frame's bytes as this frame's payload and every frame after
# that is garbage, on every plane at once, while the sender reports full rate
# and zero drops.
ABORT_SENTINEL = b"\xde\xad\xbe\xef"


def send_frame(sock, frame):
    """Whole-frame-or-nothing send. Safe on a NON-BLOCKING socket.

    [WHOLE_FRAME_SEND_V1] This was a bare sendall(). On a blocking socket that
    always completes; on a non-blocking one sendall() can write PART of the
    buffer and then raise BlockingIOError -- and it does not report how much
    went. The stream is length-prefixed, so a partial write leaves a length with
    no body, the receiver consumes the NEXT frame's bytes as this frame's
    payload, and every frame after that is garbage. Symptom: audio and video
    both freeze at once while the sender reports full rate and zero drops.

    That became reachable the moment the relay's accepted sockets were made
    non-blocking ([ALL_NONBLOCKING_V1]).

    Nothing written yet -> raise, and the caller drops the frame cleanly.
    Partially written -> the frame is COMMITTED, so finish it; bounded, because
    this must never park a fan-out thread.
    """
    blob = _LEN.pack(len(frame)) + frame

    # [DO_NOT_COMMIT_TO_A_FRAME_THAT_WILL_NOT_FIT_V1] Check for room BEFORE the
    # first byte goes.
    #
    # A partial first send COMMITS the frame: the receiver has been told a
    # length and will read exactly that many bytes. The only way out was to pad
    # to the declared length -- writing EXACTLY the number of bytes that had
    # just failed to go, down the same blocked socket. When that failed too the
    # code raised and the connection died, so the peer saw a reset. The abort
    # sentinel is the right idea and the amount of padding made it unusable at
    # precisely the moment it was needed.
    #
    # A frame that does not fit is dropped before it is committed. Dropping is
    # free, the stream stays framed, nothing is padded, and no connection dies.
    # An abort is now what it was meant to be: the rare case where the socket
    # accepted part of a frame and then stalled, not the routine outcome of a
    # full buffer.
    _room = _send_room(sock)
    if _room >= 0 and _room < len(blob):
        raise BlockingIOError(
            "no room for a %d byte frame (%d free) -- dropped before it was "
            "committed" % (len(blob), _room))

    sent = sock.send(blob)                  # raises BlockingIOError if 0 fits
    if sent >= len(blob):
        return
    view = memoryview(blob)
    fin = time.time() + 0.25
    while sent < len(blob):
        if time.time() >= fin:
            # [ABORT_SENTINEL_V1] Cannot finish. The receiver is mid-payload and
            # will keep reading until it has the declared length, so tell it to
            # throw this frame away rather than leaving it to consume the next
            # frame's bytes. Padding to the full declared length keeps the
            # framing invariant intact: every frame on the wire is exactly as
            # long as it said it was.
            # [SAY_WHY_IT_ABORTED_V1] An abort is a 250ms stall on a socket
            # that had already accepted part of this frame. That is a real
            # condition with a real cause, and the log said only that it
            # happened -- not how big the frame was, how far it got, how much
            # room the kernel had, or what the peer was doing about it.
            #
            # Everything the diagnosis needs, at the moment of the decision.
            try:
                _sndbuf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
            except Exception:
                _sndbuf = -1
            try:
                _unsent = _unacked_bytes(sock)
            except Exception:
                _unsent = -1
            print("  [ABORT] gave up on a %d byte frame after 250ms: %d/%d "
                  "written (%.0f%%), sndbuf=%d, unacked=%s, peer=%s. The "
                  "receiver had already been told the length, so the rest goes "
                  "as abort padding to keep the stream framed."
                  % (len(frame), sent, len(blob),
                     100.0 * sent / max(1, len(blob)), _sndbuf,
                     _unsent if _unsent >= 0 else "unknown",
                     _peer_of(sock)), flush=True)
            pad = ABORT_SENTINEL * ((len(blob) - sent) // 4 + 1)
            try:
                _abort_write(sock, pad[:len(blob) - sent])
                return
            except Exception:
                # Even the padding will not go. Nothing can rescue the stream
                # now -- the connection has to die.
                raise ConnectionError(
                    "send_frame: %d/%d written and the abort sentinel would not "
                    "go either; stream is desynced" % (sent, len(blob)))
        if not select.select([], [sock], [], 0.01)[1]:
            continue
        try:
            sent += sock.send(view[sent:])
        except (BlockingIOError, InterruptedError):
            continue


def _send_room(sock):
    """Bytes this socket can still take, or -1 where that cannot be known.

    [DO_NOT_COMMIT_TO_A_FRAME_THAT_WILL_NOT_FIT_V1] SO_SNDBUF is what the kernel
    will hold; TIOCOUTQ is what it is already holding.

    [THE_TWO_NUMBERS_ARE_NOT_THE_SAME_UNITS_V1] and they do not subtract.

    Linux DOUBLES SO_SNDBUF: set 131072, read back 262144, because the kernel
    reserves half for its own bookkeeping. TIOCOUTQ then counts the QUEUE
    including that overhead -- measured here, 2304 for a 1000-byte write, and
    268800 on a socket whose reported cap is 262144. Subtracting one from the
    other therefore goes NEGATIVE on a socket that is doing nothing, clamps to
    zero, and this function reports "no room" on an idle wire.

    Every frame is then dropped before it is even attempted -- which is a sender
    that walks itself to 160x120 for no reason and cannot climb back, because
    nothing it tries at any size is ever written. Reported 2026-08-11: "it walks
    down to 160 when there's no fucking reason for it, and it refuses to come
    back up. The sockets have plenty of room."

    A pre-flight has to be CONSERVATIVE: it exists to avoid committing to a
    frame that cannot finish, and being wrong in the "no room" direction is far
    worse than being wrong in the other, because the other way just falls back
    to trying and letting EWOULDBLOCK decide -- which is what the code did
    before this check existed and which is always correct, if less tidy.
    
    So: only report a shortage when the queue is a clear majority of the cap,
    and never report less than a whole frame's worth of room. If the numbers
    disagree, believe the socket, not the arithmetic.
    """
    try:
        import socket as _sk
        cap = sock.getsockopt(_sk.SOL_SOCKET, _sk.SO_SNDBUF)
    except Exception:
        return -1
    used = _unacked_bytes(sock)
    if used < 0 or cap <= 0:
        return -1
    if used >= cap:
        # The two numbers are in different units. This says nothing about the
        # socket -- it says the arithmetic does not apply here.
        return -1
    room = cap - used
    # Only a queue over three quarters of the cap counts as pressure. Below
    # that, say there is room and let the socket answer for itself.
    if used * 4 < cap * 3:
        return cap
    return room


def _unacked_bytes(sock):
    """Bytes the kernel still holds for this socket, or -1 where unknowable.

    [SAY_WHY_IT_ABORTED_V1] The difference between "the peer stopped reading"
    and "we handed the kernel more than the link can carry".

    [THE_GUARD_MUST_WORK_WHERE_THE_CLIENT_RUNS_V1] TIOCOUTQ is Linux-only, so
    on Windows this returned -1, _send_room returned -1, and every
    do-not-commit guard was skipped -- on the platform the GUI client runs on.

    Measured 2026-08-11: John, on Windows, pushed 370 KB/s of 1080p into a
    256 KB kernel buffer on a link carrying a fraction of that. The buffer
    filled with stale 1080p, and from then on EVERY frame was refused no matter
    how small -- 0 KB/s, 24 drops/s, at 160x120. Nothing was wrong with the
    socket and nothing was wrong with 160x120: three-kilobyte frames were
    queued behind a quarter megabyte of superseded picture, which at that link
    rate takes the better part of ten seconds to clear.

    Windows has no TIOCOUTQ and no equivalent. What it does have is
    SIO_IDEAL_SEND_BACKLOG_QUERY, which is not the same thing -- so rather than
    pretend, this tracks the outstanding bytes ITSELF: what was handed to
    send() minus what a zero-length probe says has drained. Approximate, and
    honestly labelled, but bounded -- which -1 was not.
    """
    try:
        import fcntl, struct, termios
        return struct.unpack("I", fcntl.ioctl(
            sock.fileno(), termios.TIOCOUTQ, b"\0\0\0\0"))[0]
    except Exception:
        pass
    # No kernel counter here. Use what this process knows it wrote and has not
    # seen accepted -- see SotFDataPlane._outstanding, which is maintained on
    # every write. A socket object with no such tracker is genuinely unknowable.
    n = getattr(sock, "_frognet_outstanding", None)
    return int(n) if n is not None else -1


def _peer_of(sock):
    try:
        return "%s:%s" % sock.getpeername()[:2]
    except Exception:
        return "?"


def _abort_write(sock, pad):
    """[ABORT_SENTINEL_V1] Best effort completion of an aborted frame."""
    off = 0
    fin = time.time() + 0.25
    while off < len(pad):
        if time.time() >= fin:
            raise ConnectionError("abort sentinel incomplete")
        if not select.select([], [sock], [], 0.01)[1]:
            continue
        try:
            off += sock.send(memoryview(pad)[off:])
        except (BlockingIOError, InterruptedError):
            continue


class RungModel:
    """[RUNG_IS_MEASURED_V1] What each rung ACTUALLY costs on this wire, learned.

    Replaces `VIDEO_SHARE = 0.60` and the RUNG_VIDEO bitrate column as the basis
    for "can this link carry that rung". The 0.60 was one constant standing in
    for audio, FNWP framing, TCP/IP overhead and keyframe burstiness, with no
    term for any of them -- at a 408 kbps link it reserved 163 kbps for costs
    that measure around 26. And the table bitrate is what the encoder was ASKED
    for: with `preset=ultrafast, tune=zerolatency` and no maxrate/bufsize there
    is no VBV, so it overshoots freely. Measured on a 1.2 Mbps L7 target: 61.6
    to 171.0 KB/s inside one call, same geometry, same codec. The table is the
    request; key_kb and delta_kb are what happened.

    Three inputs, all already published by WireStats: mean keyframe bytes, mean
    delta bytes, and the achieved wire rate, per rung, per window.

    [KEYFRAME_SCALES_BY_PIXELS_V1] A rung the ladder has not visited recently has
    no measurement, and the question is always about a rung we are NOT on. The
    geometry ratio bridges it: encoded size at a fixed quantizer grows with pixel
    count, sub-linearly, so scaling a measurement from rung A to rung B by
    px(B)/px(A) OVERSTATES B when B is smaller. That is the safe direction for a
    predicate that decides whether to try -- it under-promotes rather than
    promoting into a wire that cannot hold it. Every visit re-anchors the real
    number and the estimate is discarded.
    """

    # A measurement older than this is not about current conditions. Two
    # BP_WINDOW_S windows: long enough to survive a rung round-trip, short
    # enough that a link change invalidates it.
    STALE_S = 30.0

    def __init__(self):
        self.lock = threading.Lock()
        self.seen = {}          # level -> dict(key_b, delta_b, bps, at, n)

    @staticmethod
    def pixels(level):
        p = RUNG_VIDEO.get(level)
        return (p["w"] * p["h"]) if p else 0

    def observe(self, level, snap):
        """Record one completed window's measurement for `level`.

        Only windows that actually carried video teach anything: key_n is the
        proof a keyframe was measured rather than inherited.
        """
        if level not in RUNG_VIDEO:
            return
        key_n = int(snap.get("key_n") or 0)
        if key_n <= 0:
            return
        with self.lock:
            self.seen[level] = {
                "key_b": float(snap.get("key_kb") or 0.0) * 1024.0,
                "delta_b": float(snap.get("delta_kb") or 0.0) * 1024.0,
                "bps": float(snap.get("v_kbps") or 0.0) * 1024.0 * 8.0,
                "at": time.time(),
                "n": key_n,
            }

    def _fresh(self, now):
        return {lv: m for lv, m in self.seen.items()
                if (now - m["at"]) <= self.STALE_S and m["key_b"] > 0}

    def keyframe_bytes(self, level, now=None):
        """Estimated keyframe size at `level`, and how that estimate was reached.

        Returns (bytes, source) where source is 'measured', 'scaled from Ln', or
        'unknown' -- the caller says which on the log line, because a decision
        made on a scaled estimate and one made on a real measurement are not the
        same decision and must not read the same.
        """
        now = now or time.time()
        with self.lock:
            fresh = self._fresh(now)
            if level in fresh:
                return fresh[level]["key_b"], "measured"
            if not fresh:
                return 0.0, "unknown"
            px = self.pixels(level)
            if px <= 0:
                return 0.0, "unknown"
            # Anchor on the nearest rung by pixel count: the smaller the
            # extrapolation, the less the sub-linearity costs us.
            src = min(fresh, key=lambda lv: abs(self.pixels(lv) - px))
            ratio = px / float(self.pixels(src) or px)
            return fresh[src]["key_b"] * ratio, "scaled from %s" % (
                L.code(src) if hasattr(L, "code") else src)

    def steady_bps(self, level, now=None):
        """Estimated sustained video rate at `level`, same scaling rule."""
        now = now or time.time()
        with self.lock:
            fresh = self._fresh(now)
            if level in fresh and fresh[level]["bps"] > 0:
                return fresh[level]["bps"]
            cands = {lv: m for lv, m in fresh.items() if m["bps"] > 0}
            if not cands:
                return 0.0
            px = self.pixels(level)
            src = min(cands, key=lambda lv: abs(self.pixels(lv) - px))
            return cands[src]["bps"] * (px / float(self.pixels(src) or px))

    def note_failure(self, level, snap):
        """[RUNG_IS_MEASURED_V1] A probe that backlogged still taught us the real
        numbers for the rung it failed at. Record them so the next fit test is
        made against what that rung actually costs rather than the estimate that
        got us into it."""
        self.observe(level, snap)


class VideoFloorProbe:
    """[VIDEO_FLOOR_IS_MEASURED_V1] Find where video actually stops passing.

    There is no defined floor. RUNG_VIDEO's lowest entry -- 640x360 at 300 kbps
    -- was being treated as one, so a link whose budget came out under 300 kbps
    was told it could carry no video at all, when 320x240 would have gone
    through comfortably. That was a fact about the table, reported as a fact
    about the wire.

    The floor is found by sending. Encode a keyframe at the smallest candidate
    geometry and offer it. If it goes and audio is untouched, that geometry
    passes; step up and try the next. Keep going until something stalls.

    THE STOP CONDITION IS AUDIO. Not the relay's backlog report, not a rate
    estimate: audio is the protected floor and video must be shut down entirely
    before a single audio frame is shed, so an audio shed during a probe is the
    definitive answer that this geometry is too much for this wire. It is also
    local, immediate, and needs nobody's cooperation --
    SotFDataPlane.audio_sheds is incremented by the writer itself when the wire
    refuses a protected frame after its retries.

    When a candidate stalls, the settled geometry is THE ONE BELOW IT. If even
    the smallest candidate stalls, video cannot pass and the answer is audio
    only -- and that is now a measurement rather than an arithmetic result.

    The candidate list is data. Adding a smaller or larger geometry is the only
    edit needed; nothing else in the walk is aware of what the entries are.
    """

    # Ascending. The keyframe is what gets sent at each step, so these are
    # geometries and not rungs -- a rung is a wire label, and the probe is
    # deliberately not limited to geometries a rung happens to name.
    # [ASPECT_IS_CHOSEN_V1] Set per call from the operator's aspect: the low
    # end is the 4:3 constrained walk ([CONSTRAINED_GOES_4_3_V1]) and the top is
    # the chosen family's L6/L7.
    #
    # It stops below the top rung: the walk establishes the FLOOR, and anything
    # above is reached by the rung probe sending real frames
    # ([PROBE_IS_GRADED_ON_FRAMES_V1]) rather than by spending connect-time
    # seconds on a size most links will not hold anyway.
    CANDIDATES = ()

    # A stall shows up on the audio counter at the write, but the audio thread
    # is a separate producer: give it a beat to have attempted a frame before
    # reading the counter, or a clean probe and an unobserved one look alike.
    SETTLE_S = 0.25

    # [FLOOR_IS_PROVEN_NOT_GLIMPSED_V1] One keyframe is not proof. SO_SNDBUF is
    # 256 KiB and absorbs a single frame whatever the wire is doing, so a lone
    # keyframe that goes proves the buffer had room at that instant and nothing
    # about a sustained rate. Several keyframes spaced across a second, with
    # audio untouched throughout, is the difference between "it fit once" and
    # "this geometry passes".
    #
    # The whole walk is budgeted so a call is not held at audio-only while the
    # ladder deliberates: BUDGET_S across the candidate list, so three
    # candidates get a second each, and each second carries KEYS_PER_STEP
    # keyframes spaced evenly.
    BUDGET_S = 3.0
    KEYS_PER_STEP = 4

    @classmethod
    def floor_walk_s(cls, candidates=None, aspect=None):
        """The shortest this walk can actually take.

        ready() floors keyframe spacing at SETTLE_S -- the audio thread has to
        have had a chance to attempt a frame between probes or a stall it caused
        lands on the wrong keyframe. So the real duration is
        candidates x KEYS_PER_STEP x SETTLE_S regardless of BUDGET_S, and adding
        a candidate lengthens the walk whether or not the budget says so. The
        budget was a fiction the moment the list outgrew it; this is the number.
        """
        n = len(candidates or cls.CANDIDATES
                or cls.candidates_for(aspect or DEFAULT_ASPECT))
        return n * cls.KEYS_PER_STEP * cls.SETTLE_S

    @staticmethod
    def candidates_for(aspect):
        """Ascending walk for `aspect`: the constrained floor, then the chosen
        family's rungs up to one below the top.

        COARSE ON PURPOSE. Walking every intermediate 4:3 step as well took the
        connect-time walk to 6-7 seconds of audio-only, and the intermediate
        steps are not what it is for: this walk BRACKETS the floor, and
        [BOTTOM_RUNG_SHRINKS_V1] refines it at runtime by stepping down through
        exactly those sizes when the wire actually asks. A floor recorded as
        160x120 when 480x360 would have worked costs one runtime step to
        correct; four extra seconds before any video appears costs every call.
        """
        geo = RUNG_GEO.get(aspect) or RUNG_GEO[DEFAULT_ASPECT]
        floor = dict(BOTTOM_4_3[-1])
        rungs = [dict(geo[i]) for i in sorted(geo) if i <= max(geo) - 1]
        return tuple([floor] + rungs)

    def __init__(self, candidates=None, budget_s=None, aspect=None):
        self.aspect = aspect or DEFAULT_ASPECT
        self.candidates = list(candidates or self.CANDIDATES
                               or self.candidates_for(self.aspect))
        _want = float(budget_s if budget_s is not None else self.BUDGET_S)
        # Never advertise a budget the settle floor cannot honour.
        self.budget_s = max(_want, self.floor_walk_s(self.candidates))
        self.step_s = self.budget_s / max(1, len(self.candidates))
        self.space_s = self.step_s / max(1, self.KEYS_PER_STEP)
        self.idx = 0                 # candidate being tried
        self.sent = 0                # keyframes proven at THIS candidate
        self.passed = -1             # highest index proven to pass
        self.active = False
        self.done = False
        self.result = None           # the settled geometry, or None = no video
        self.measured = {}           # idx -> observed keyframe bytes (largest)
        self.why = ""
        self._a0 = None
        self._started = 0.0

    # -- the walk ------------------------------------------------------------
    def begin(self, audio_sheds_now, now=None):
        """Start the walk at the smallest candidate."""
        self.idx = 0
        self.sent = 0
        self.passed = -1
        self.done = False
        self.result = None
        self.why = ""
        self.active = True
        self._a0 = int(audio_sheds_now)
        self._started = now or time.time()

    def current(self):
        """The geometry to encode right now, or None if the walk is over."""
        if not self.active or self.done or self.idx >= len(self.candidates):
            return None
        return dict(self.candidates[self.idx])

    def ready(self, now=None):
        """Time for the next keyframe at this candidate?

        Spaced by space_s, but never tighter than SETTLE_S -- the audio thread
        has to have had a chance to attempt a frame, or a stall it caused would
        be attributed to the NEXT keyframe instead of this one.
        """
        gap = max(self.SETTLE_S, self.space_s)
        return ((now or time.time()) - self._started) >= gap

    def observe(self, video_shed, audio_sheds_now, key_bytes=0, now=None):
        """Grade the keyframe just offered at the current candidate.

        video_shed  -- did the wire (or the bucket, or the whole-frame guard)
                       refuse the keyframe itself
        audio_sheds_now -- SotFDataPlane.audio_sheds, cumulative and read
                       non-destructively so the bearer's own pending count is
                       not stolen
        """
        if not self.active or self.done:
            return
        stalled_audio = int(audio_sheds_now) > int(self._a0)
        if key_bytes:
            # Largest keyframe seen at this geometry: the budget has to survive
            # the worst frame, not the average one.
            self.measured[self.idx] = max(int(key_bytes),
                                          self.measured.get(self.idx, 0))

        if stalled_audio or video_shed:
            # This geometry is too much. Settle on the one below it -- which is
            # None when even the smallest stalled.
            self.result = (dict(self.candidates[self.passed])
                           if self.passed >= 0 else None)
            _c = self.candidates[self.idx]
            self.why = ("audio shed at %dx%d on keyframe %d of %d"
                        % (_c["w"], _c["h"], self.sent + 1, self.KEYS_PER_STEP)
                        ) if stalled_audio else (
                       "keyframe %d of %d refused at %dx%d"
                       % (self.sent + 1, self.KEYS_PER_STEP, _c["w"], _c["h"]))
            self.active = False
            self.done = True
            return

        # This keyframe went and audio was untouched. That is one data point,
        # not a pass: KEYS_PER_STEP of them across the step's slice is the pass.
        self.sent += 1
        self._a0 = int(audio_sheds_now)
        self._started = now or time.time()
        if self.sent < self.KEYS_PER_STEP:
            return

        self.passed = self.idx
        self.idx += 1
        self.sent = 0
        if self.idx >= len(self.candidates):
            self.result = dict(self.candidates[self.passed])
            self.why = ("every candidate passed (%d keyframes each), top is "
                        "%dx%d" % (self.KEYS_PER_STEP,
                                   self.result["w"], self.result["h"]))
            self.active = False
            self.done = True


class SotFDataPlane:
    """SotF data plane: SEND-OR-DROP on a NON-BLOCKING socket. There is NO queue. Each frame is
    sent whole or dropped whole, so newest-wins falls out for free: the video grabber already
    keeps only the freshest frame, and with nothing stockpiled here the next frame a producer
    offers is always the newest available. Latency cannot build -- a frame the wire can't take
    right now is dropped, not hoarded, and the next (newer) frame gets its turn. That is the
    whole point of SotF, and it is why the bearer can degrade the ladder honestly: its
    congestion signal is the DROP count, not a backlog depth.

    Audio is the protected floor: on a momentarily full send buffer it gets a brief writability
    grace before it, too, is dropped; video is dropped immediately. Two producer threads (audio,
    video) share one socket, so a lock serialises whole frames -- never interleaved on the wire.

    [NONBLOCKING_SEND_V1] The socket is NON-BLOCKING. Send-or-drop is decided by the
    write itself: attempt it, and EWOULDBLOCK means the wire will not take this frame,
    so discard it and wait for the next. That is the whole mechanism.

    It replaces a select() writability gate on a socket deliberately left in BLOCKING
    mode, which was the freeze. select reports writable when the buffer has SOME room --
    SO_SNDLOWAT, 2048 bytes on Linux TCP, 1 on Windows -- not room for len(blob). A
    keyframe is tens of KB, so select said writable, sendall started, filled the buffer,
    and then parked waiting for the peer. Worse, put() holds self.lock across the write,
    so one wedged sendall queued every other producer behind it: audio behind video, one
    window frozen while another crawled.

    The receive loop shares this fd, and blocking is a property of the FD, not of a
    direction -- so recv_exact() waits with select() before each read rather than
    relying on the socket to block. Non-blocking is the mode; readiness is asked for
    explicitly on both sides.

    A SIOCOUTQ free-space pre-check was tried instead and reverted: fcntl and termios do
    not exist on Windows, and the Communicator is a Windows client.

    DEMO knobs: throttle_bps caps the wire with a token bucket -- over budget, the frame is
    DROPPED (a constrained wire sheds), which walks the bearer DOWN the ladder. jitter_ms adds a
    small random pre-send delay to emulate a jittery path. Neither knob queues anything."""

    AUDIO_GRACE_S = 0.020      # protected-audio writability grace before it, too, is shed
    KEY_RETRIES = 3        # [ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1]
    AUDIO_RETRIES = 2          # protected audio is retried at LEAST this many times
    AUDIO_RETRY_WAIT_S = 0.008 # per-retry wait for the buffer to drain
    # [THE_BUFFER_IS_THE_LATENCY_V1] The send buffer is how much STALE picture
    # the kernel is allowed to hold, and it is the real bound on how fast a
    # sender can react.
    #
    # 256 KiB sounds small until you divide it by the link. At 30 KB/s it is
    # EIGHT AND A HALF SECONDS of queued video -- so a sender that overshoots
    # spends that long unable to get anything through at any size, because
    # every new frame is queued behind superseded ones. Measured 2026-08-11:
    # 370 KB/s of 1080p filled it, and from then on 0 KB/s and 24 drops/s at
    # 160x120, which reads as a dead socket and is not one.
    #
    # 128 KiB. The whole-frame ceiling is SNDBUF//2, and keyframes at the top
    # rungs measure 40 KB and up -- 64 KiB put that ceiling at 32 KB and locked
    # the ladder out of its own top, which test_rung_measured caught. The bound
    # has to leave the ladder its range.
    #
    # At 128 KiB the stale queue is about four seconds on the slowest link and
    # well under one on a healthy one, against eight and a half before. The
    # rest of the reaction time comes from not overshooting in the first place
    # -- [DO_NOT_COMMIT_TO_A_FRAME_THAT_WILL_NOT_FIT_V1], which now works on
    # Windows too, is what stops the buffer filling with 1080p on a link that
    # cannot carry it.
    SNDBUF = 1 << 17           # 128 KiB

    def __init__(self, sock, sndbuf=None):
        self.sock = sock
        # [NONBLOCKING_SEND_V1] Non-blocking: a write that cannot complete raises instead
        # of parking the producer. The recv side waits with select() before each read.
        try:
            self.sock.setblocking(False)
        except OSError:
            pass
        self._sndbuf = int(sndbuf or self.SNDBUF)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self._sndbuf)
        except OSError:
            pass
        self.lock = threading.Lock()
        self.sheds = 0
        self.audio_sheds = 0       # protected audio the wire refused, cumulative
        self.partial_completions = 0   # frames that needed a second write to finish
        self._audio_shed_pending = 0   # unread audio sheds -> a video downgrade signal
        # [SHED_IS_READ_FROM_THE_QUEUE_THAT_SHED_IT_V1] Unread sheds on THIS
        # plane, drained by take_video_sheds(). Separate from self.sheds, which
        # is a running total three readers diff against (the [VID-TX] line, the
        # window observer, the floor probe). take_sheds() ZEROES self.sheds, so
        # handing the bearer that method would have made every one of those
        # diffs go negative -- the counter has to be drainable without being
        # reset under the readers that watch it.
        self._shed_pending = 0
        # [AUDIO_FIRST_V1] An order of service for two producers on ONE plane.
        #
        # HISTORICAL, and the comment used to say so badly. Audio and video have
        # held SEPARATE sockets since [TWO_PLANES_V1] -- self.sendq on
        # self.sock, self.vsendq on self.vsock -- so these counters are per
        # plane and the video plane's _audio_waiting is always zero. Nothing
        # contends here any more.
        #
        # It is kept because a plane can carry two producers of its own, and
        # because the rule is still the right one if it ever does. It is NOT a
        # place to fix audio stalls: read the comment above as a description of
        # a socket layout that no longer exists and the fix will be in the wrong
        # file. Measured 2026-08-12, by making exactly that mistake.
        # [AUDIO_INTENT_IS_SHARED_V1] The yield below reads _audio_waiting on
        # THIS object. Audio lives on self.sendq and video on self.vsendq --
        # two different SotFDataPlane instances -- so the video plane's counter
        # is structurally always zero and the yield never fires. The comment a
        # few lines up says exactly that, and the first cut of
        # [VIDEO_IS_SEGMENTED_V1] was designed against it anyway: every segment
        # "yielded to waiting audio" and there was no audio to yield to.
        #
        # Intent now lives in an object BOTH planes hold. attach_audio_gate()
        # hands the video plane the audio plane's gate, so audio declaring
        # itself on sendq is visible to video about to write on vsendq. Falls
        # back to its own private gate when nothing is attached, so a plane used
        # on its own behaves exactly as before.
        self._own_gate = {"waiting": 0, "lock": threading.Lock()}
        self._gate = self._own_gate
        self._audio_gate = self._gate["lock"]
        self.dead = False
        # DEMO pacing
        self.throttle_bps = 0
        self.jitter_ms = 0
        self._bucket = 0.0                         # token bucket: accumulated allowance (bits)
        self._bucket_t = time.time()

    # [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1] How long may one write own the
    # uplink before the audio behind it is late?
    #
    # VSEG_WHOLE_MAX was a flat 8192 -- an arbitrary number that happens to be
    # ~55 ms at 150 KB/s, or nearly THREE audio frames. So a "small" frame that
    # skipped segmentation could still stall audio for three block intervals,
    # and it did, most visibly while the ladder walked resolutions and emitted
    # a fresh keyframe at every rung.
    #
    # The quantity that matters is TIME ON THE WIRE, which is bytes divided by
    # the rate this socket is actually achieving -- not a byte count picked in
    # advance. A frame may go whole only if it drains in less than one audio
    # block. At 150 KB/s that is ~3000 bytes, not 8192; on a fast LAN it rises
    # on its own and small frames stop being segmented for no reason.
    # HONEST LIMIT of this estimator: it measures the rate this socket is
    # ACHIEVING, which equals link capacity only while the link is saturated.
    # When the app offers less than the wire can carry, the estimate reads low
    # and frames get segmented that did not need to be. That bias is deliberate
    # and now cheap: since [A_FRAME_IS_ONE_SHED_V1] an extra unit costs 5 bytes
    # of header and a syscall, not a false congestion signal to the ladder. The
    # opposite bias -- a ceiling that reads high on a congested link -- is the
    # one that stalls audio, and it is the one being avoided.
    WHOLE_FRAME_MS = 20.0          # one audio block: the deadline being protected
    RATE_HALFLIFE_S = 2.0          # EWMA horizon for the achieved send rate

    def _note_sent(self, nbytes):
        """[THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1] Bytes the socket ACCEPTED."""
        now = time.time()
        last = getattr(self, "_rate_t", None)
        if last is None:
            self._rate_t = now
            self._rate_bps = 0.0
            return
        dt = now - last
        self._rate_t = now
        if dt <= 0:
            return
        inst = nbytes / dt
        a = 1.0 - math.exp(-dt / self.RATE_HALFLIFE_S)
        self._rate_bps = (1.0 - a) * getattr(self, "_rate_bps", 0.0) + a * inst

    def whole_frame_max(self):
        """Largest frame allowed to go as ONE write, in bytes.

        Derived from the achieved rate so the bound is a deadline, not a guess.
        Floored at one segment -- below that there is nothing to break up --
        and capped by the buffer, which is the hard whole-frame limit anyway.
        With no rate measured yet, be conservative: assume the frame is big
        enough to matter and segment it.
        """
        hard = self._sndbuf // 2
        rate = getattr(self, "_rate_bps", 0.0)
        if rate <= 0.0:
            return VSEG_BYTES
        return int(max(VSEG_BYTES, min(hard, rate * (self.WHOLE_FRAME_MS / 1000.0))))

    @property
    def _audio_waiting(self):
        """[AUDIO_INTENT_IS_SHARED_V1] How much audio has declared intent on
        the gate this plane watches -- its own, or another plane's."""
        return self._gate["waiting"]

    def attach_audio_gate(self, other):
        """[AUDIO_INTENT_IS_SHARED_V1] Watch the gate another plane declares on.

        The video plane calls this with the audio plane, so [AUDIO_FIRST_V1]
        yields to audio that is waiting on a DIFFERENT socket. Without it the
        yield reads a counter that is structurally always zero.
        """
        self._gate = other._own_gate
        self._audio_gate = self._gate["lock"]

    def _over_budget(self, nbits):
        """Token bucket for the demo throttle: True if sending nbits now would exceed
        throttle_bps, in which case the caller DROPS the frame (a full wire sheds)."""
        bps = self.throttle_bps
        if not bps or bps <= 0:
            return False
        now = time.time()
        self._bucket = min(float(bps), self._bucket + (now - self._bucket_t) * bps)  # cap burst 1s
        self._bucket_t = now
        if self._bucket >= nbits:
            self._bucket -= nbits
            return False
        return True

    def _writable(self, timeout):
        try:
            return bool(select.select([], [self.sock], [], timeout)[1])
        except OSError:
            self.dead = True
            return False

    def put(self, frame: bytes, droppable: bool, is_key: bool = False):
        """Send ONE frame whole, or drop it whole and move on -- never queue. If the send buffer
        can't take it right now, video (droppable=True) is dropped immediately; audio (droppable=
        False) gets a brief writability grace first. A drop bumps self.sheds -- the bearer's
        congestion signal. When the buffer has room, sendall completes promptly (room was just
        confirmed), so producers are never parked on a stalled wire."""
        if self.dead:
            return
        # [AUDIO_FIRST_V1] Declare intent before contending, so a video frame
        # already at the door yields rather than winning on arrival order.
        if not droppable:
            with self._gate["lock"]:
                self._gate["waiting"] += 1
            try:
                return self._put(frame, droppable, is_key)
            finally:
                with self._gate["lock"]:
                    self._gate["waiting"] -= 1
        return self._put(frame, droppable, is_key)

    def put_segmented(self, kind, src, payload, is_key=False,
                      seg_bytes=None):
        """[VIDEO_IS_SEGMENTED_V1] Send one video frame as a run of units.

        Each segment goes through _put() exactly like any other frame, which
        means it passes the SAME [AUDIO_FIRST_V1] yield on the way in: video
        waits for declared-waiting audio before each piece, not just once per
        frame. That is where the interleave actually happens -- one yield point
        per frame becomes one per segment.

        A segment the wire refuses aborts the WHOLE frame: the receiver is
        holding partial pieces and must be told to let go, so an abort unit is
        sent and the remaining segments are dropped. Losing one picture is the
        designed outcome; leaving a half-assembled frame in the receiver is not.
        [NO_FALLBACK_V1] -- the abort is explicit, not inferred from silence.

        Returns True if every segment went, False if the frame was abandoned.
        """
        if self.dead:
            return False
        # [A_FRAME_IS_ONE_SHED_V1] The ladder reads self.sheds as "this size
        # does not fit". Segmenting gave one frame ~25 chances to be shed
        # instead of one, so the shed RATE rose with the unit count and the
        # ladder walked itself to the floor chasing a signal that no longer
        # meant what it used to. Measured 2026-08-16: keyframes "refused" at
        # 320x240 and 160x120 -- sizes that cannot fail to fit a 128 KiB
        # buffer -- and the rung walked 1920x1080 -> 160x120 with a refusal
        # logged at every step. The backoff then pinned it at the bottom on
        # evidence segmentation had manufactured.
        #
        # So the unit sheds are absorbed here and exactly ONE shed is charged
        # for an abandoned frame, which is what the old whole-frame write cost
        # and what the ladder was calibrated against.
        n = int(seg_bytes or VSEG_BYTES)
        self._vseg_id = (getattr(self, "_vseg_id", 0) + 1) & 0xFFFF
        fid = self._vseg_id
        total = len(payload)
        idx = 0
        off = 0
        while off < total:
            chunk = payload[off:off + n]
            off += len(chunk)
            flags = VSEG_LAST if off >= total else 0
            unit = pack_typed(kind, src, _VSEG.pack(fid, idx, flags) + chunk)
            before, before_p = self.sheds, self._shed_pending
            self._put(unit, droppable=True, is_key=is_key)
            if self.sheds != before:
                # [A_FRAME_IS_ONE_SHED_V1] roll the unit's accounting back and
                # charge one shed for the frame, below.
                self.sheds = before
                self._shed_pending = before_p
                # refused -- tell the far end to discard what it has
                try:
                    self._put(pack_typed(kind, src,
                                         _VSEG.pack(fid, idx, VSEG_ABORT)),
                              droppable=False)
                except Exception:
                    pass
                self.sheds += 1                      # ONE, for the frame
                self._shed_pending += 1
                self.vseg_frames_aborted = getattr(
                    self, "vseg_frames_aborted", 0) + 1
                self.vseg_units_shed = getattr(
                    self, "vseg_units_shed", 0) + 1
                return False
            idx += 1
        self.vseg_frames_sent = getattr(self, "vseg_frames_sent", 0) + 1
        self.vseg_units_sent = getattr(self, "vseg_units_sent", 0) + idx
        return True

    def _put(self, frame, droppable=True, is_key=False):
        blob = _LEN.pack(len(frame)) + frame
        # [AUDIO_FIRST_V1] Video yields to waiting audio. Bounded to one audio
        # frame's worth (~20ms) so a wedged audio thread cannot silence the
        # picture -- past that video proceeds regardless.
        if droppable and self._audio_waiting:
            _until = time.time() + 0.020
            while self._audio_waiting and time.time() < _until:
                time.sleep(0.001)
        # A frame bigger than the send buffer can NEVER go out whole, so attempting it
        # guarantees a partial write and a wait. Shed it here, before the lock.
        # [SNDBUF_IS_DOUBLED_V1] Linux reports SO_SNDBUF as ~2x the usable size, so
        # comparing against the raw value lets through frames between usable and 2x
        # usable. Those pass this guard, then partially write -- and a committed
        # partial frame must be finished, which parks this producer with the lock
        # held. Halving it keeps the whole-frame decision where it belongs: BEFORE
        # any byte moves, as a clean drop.
        if len(blob) > (self._sndbuf // 2):
            self.sheds += 1
            self._shed_pending += 1
            if not droppable:
                self.audio_sheds += 1
            return
        with self.lock:
            # DEMO throttle: a rate-capped wire sheds rather than queues -- but it
            # sheds VIDEO. Protected audio is the floor and the throttle is not
            # allowed to silence it: the knob exists to walk the bearer down the
            # ladder, and the bottom of the ladder is a voice, not nothing.
            if droppable and self._over_budget(len(blob) * 8):
                self.sheds += 1
                self._shed_pending += 1
                return
            jm = self.jitter_ms
            if jm and jm > 0:
                time.sleep(random.uniform(0, jm / 1000.0))
            # [AUDIO_RETRIES_V1] Attempt the write. EWOULDBLOCK means the wire will not
            # take this frame right now.
            #
            #   video (droppable)  discarded on the FIRST refusal. There is nothing to
            #                      gain by waiting: the grabber already holds only the
            #                      freshest frame, so the next one supersedes this one.
            #   audio (protected)  RETRIED at least AUDIO_RETRIES times before it is
            #                      given up. Audio is not guaranteed -- but it has
            #                      priority, and a voice is the floor of the ladder.
            #
            # Shedding audio is the loudest congestion signal there is: the wire could
            # not carry the smallest, most protected thing on it. That arms a video
            # downgrade, read by the bearer through take_audio_sheds().
            # [DO_NOT_COMMIT_TO_A_FRAME_THAT_WILL_NOT_FIT_V1] Room first.
            #
            # send() returning a PARTIAL commits the frame: the receiver has
            # been told a length and will read exactly that many bytes, so the
            # only way out is padding to the declared length -- the same number
            # of bytes that just failed to go, down the same blocked socket.
            # When that fails the connection dies and the peer sees a reset.
            #
            # A droppable frame that will not fit whole is shed here, before a
            # byte goes. Shedding is the ordinary, designed outcome and it costs
            # nothing; committing and then aborting is the expensive one. Audio
            # is not dropped on this check -- it is the floor, it is small, and
            # it gets its retries.
            _room = _send_room(self.sock)
            if droppable and _room >= 0 and _room < len(blob):
                self.sheds += 1
                self._shed_pending += 1
                return

            # [ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1] A keyframe gets retries.
            #
            # Video was one attempt: a single EWOULDBLOCK shed the frame. On a
            # healthy link that is the ORDINARY case -- the previous frame is
            # still draining and the buffer is momentarily full. Waiting five
            # milliseconds fixes it.
            #
            # For an inter frame, shedding is right: the next one supersedes it
            # and nothing downstream breaks. For a KEYFRAME it is not, because
            # everything after it is undecodable without it -- and worse, the
            # keyframe walk reads that shed as "this size does not fit" and
            # steps down. One transient EWOULDBLOCK on a gigabit link therefore
            # walked the whole call from 1080p to 160x120. Reported
            # 2026-08-11: "it's on a wide open gigabit connection ... what is
            # the failure? Is it a timeout/would block or something else?"
            #
            # It was EWOULDBLOCK, and it meant nothing.
            if droppable:
                attempts = self.KEY_RETRIES + 1 if is_key else 1
            else:
                attempts = self.AUDIO_RETRIES + 1
            sent = 0
            for attempt in range(attempts):
                try:
                    sent = self.sock.send(blob)
                    self._note_sent(sent)   # [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1]
                    break
                except (BlockingIOError, InterruptedError):
                    if attempt + 1 >= attempts:
                        self.sheds += 1
                        self._shed_pending += 1
                        if not droppable:
                            self.audio_sheds += 1
                            self._audio_shed_pending += 1
                        return
                    # wait briefly for the buffer to drain, then try again
                    if not self._writable(self.AUDIO_RETRY_WAIT_S):
                        if self.dead:
                            return
                except (BrokenPipeError, ConnectionResetError, OSError):
                    self.dead = True
                    return

            # Partial write. The stream is length-prefixed, so the remainder is not
            # optional: stopping here would leave the receiver reading frame bytes as a
            # length. Finish it, waiting on writability rather than on the socket.
            # Rare by construction -- SO_SNDBUF is sized to hold a keyframe whole.
            if sent < len(blob):
                # [PARTIAL_WRITE_MUST_COMPLETE_V1] Once ANY byte of a frame is on the
                # wire the frame is committed. The stream is length-prefixed, so a
                # frame abandoned half-written is not a dropped frame -- it is a
                # TRUNCATED one, and the receiver decodes whatever arrived. Symptom:
                # the bottom half of the far-end picture is green (untouched YUV rows)
                # while the sender reports 23.9/24fps and drops 0/s, because from the
                # sender's point of view nothing was shed.
                #
                # Giving up on a deadline was wrong for the same reason: it cannot
                # proceed anyway. Keep writing until the frame is whole or the socket
                # is genuinely gone. The whole-frame-or-nothing decision belongs
                # BEFORE the first byte (the oversize shed and the EWOULDBLOCK
                # discard above), not after.
                view = memoryview(blob)
                while sent < len(blob):
                    # Poll briefly rather than parking for a second at a time: this
                    # runs under self.lock, so every wait here delays every other
                    # producer on this wire.
                    if not self._writable(0.002):
                        if self.dead:
                            return
                        continue
                    try:
                        sent += self.sock.send(view[sent:])
                    except (BlockingIOError, InterruptedError):
                        continue
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        self.dead = True
                        return

                self.partial_completions += 1

    def depth(self):
        # No queue exists in SotF -- nothing is stockpiled here, so depth is always 0. The
        # bearer's real congestion signal is take_sheds() (frames the wire refused).
        return 0

    def take_audio_sheds(self):
        """[AUDIO_RETRIES_V1] Audio frames the wire refused since the last read, and
        clear. Non-zero means the wire could not carry the protected floor even after
        retries -- the bearer treats that as an immediate video downgrade."""
        with self.lock:
            n = self._audio_shed_pending
            self._audio_shed_pending = 0
            return n

    def take_video_sheds(self):
        """[SHED_IS_READ_FROM_THE_QUEUE_THAT_SHED_IT_V1] Frames THIS plane
        refused since the last read, and clear.

        The bearer's congestion signal. It was reading self.sendq.take_sheds()
        -- the AUDIO plane -- so a video frame the wire refused bumped
        vsendq.sheds and never reached the controller at all. The ladder's
        down-move therefore came from the relay's keyframe backlog report or the
        fps floor: someone else noticing, or the camera failing. The one signal
        that is local, immediate and free was the one not being read.

        Drains a pending count and leaves self.sheds alone, because self.sheds
        is what the stats line, the window observer and the floor probe diff
        against.
        """
        with self.lock:
            n = self._shed_pending
            self._shed_pending = 0
            return n

    def take_sheds(self):
        with self.lock:
            s = self.sheds
            self.sheds = 0
            self._shed_pending = 0
            return s

    def close(self):
        self.dead = True


REF_FPS = 30.0     # standard video-call / broadcast reference for throughput

class WireStats:
    """Throughput-oriented wire stats. The headline is FPS ACTUALLY GETTING THROUGH vs the
    30fps standard-video reference; the byte numbers are REAL bytes-on-wire (frame size +
    FNWP framing overhead), reported as KB/s -- not a vanity ratio against uncompressed.
    Keyframe vs delta sizes are tracked separately (VP8 sends a big keyframe then small
    deltas; the delta size is the real inter-frame efficiency)."""
    FRAME_OVERHEAD = 4 + 1 + 2 + 4 + 1  # FNWP: len+kind+srclen+level+codec (per video frame)

    def __init__(self):
        self.lock = threading.Lock()
        self.win = 2.0                            # rolling window seconds
        self._reset_t = time.time()
        # rolling counters
        self.v_sent = 0; self.v_recv = 0; self.v_dropped = 0
        self.a_sent = 0; self.a_recv = 0
        self.v_wire = 0; self.a_wire = 0          # bytes on wire this window
        # [RECEIVE_IS_MEASURED_V1] The send side was counted in bytes and the
        # receive side only in FRAMES, so no screen could say how fast anything was
        # ARRIVING -- the one number that says whether the far end is actually
        # getting through to us. Count the bytes off the wire, both planes.
        self.v_rx_wire = 0; self.a_rx_wire = 0
        self.key_bytes = 0; self.key_n = 0
        self.delta_bytes = 0; self.delta_n = 0
        # lifetime transfer comparison: DID = real video bytes on wire; WOULD = a
        # conventional full-frame-every-frame stream (no inter-frame compression), i.e.
        # each frame costs a keyframe. The gap is the delta-codec efficiency.
        self.life_did = 0; self.life_would = 0; self._last_key = 0
        # [WINDOW_KNOWS_ITS_RUNG_V1] Which rung(s) the window being accumulated
        # actually covers, and a sequence number for the published one.
        #
        # snapshot() returns the last COMPLETED window, and the rung log line was
        # gated on the rung CHANGING -- so the one line emitted per transition
        # reported the window that closed BEFORE the step, labelled with the rung
        # the sender had just moved to. Every step down to L4 printed L7's fps
        # and KB/s against "rung L4"; every promotion out of L4 printed L4's
        # zeros against "rung L5". Measured across four runs, no exceptions, and
        # it is why a 408 kbps link appeared to be carrying 24fps of 720p.
        #
        # A window is now labelled with the rung it was measured at, and it can
        # say it straddled a change rather than picking one end.
        self._levels = set()
        self._level_now = None
        self.pub_seq = 0
        # published (last full window)
        self.pub = {}

    def note_level(self, level):
        """Record the rung the sender is on RIGHT NOW, for window labelling.

        Called from the video send loop on both the send and the shed path --
        the shed path especially, because at L4 shedding is the only thing that
        happens and a window with no video is exactly the window most likely to
        be misread.
        """
        with self.lock:
            self._levels.add(level)
            self._level_now = level

    def _roll(self):
        dt = time.time() - self._reset_t
        if dt < self.win:
            return
        self.pub_seq += 1
        self.pub = {
            "seq": self.pub_seq,
            "win_s": round(dt, 2),
            # [WINDOW_KNOWS_ITS_RUNG_V1] The rung(s) this measurement was taken
            # at. Sorted, so a window that straddled a change reports both ends
            # instead of the reader guessing which one it belongs to.
            "levels": (sorted(l for l in self._levels if l is not None)
                       if self._levels
                       else ([self._level_now]
                             if self._level_now is not None else [])),
            "v_fps_sent": round(self.v_sent / dt, 1),
            "v_fps_recv": round(self.v_recv / dt, 1),
            "a_fps_sent": round(self.a_sent / dt, 1),
            "v_drop_fps": round(self.v_dropped / dt, 1),
            "ref_fps": REF_FPS,
            "v_kbps": round(self.v_wire / dt / 1024, 1),
            "a_kbps": round(self.a_wire / dt / 1024, 1),
            # [RECEIVE_IS_MEASURED_V1] what is ARRIVING, per plane
            "v_rx_kbps": round(self.v_rx_wire / dt / 1024, 1),
            "a_rx_kbps": round(self.a_rx_wire / dt / 1024, 1),
            "a_fps_recv": round(self.a_recv / dt, 1),
            "key_kb": round(self.key_bytes / self.key_n / 1024, 1) if self.key_n else 0.0,
            "delta_kb": round(self.delta_bytes / self.delta_n / 1024, 1) if self.delta_n else 0.0,
            "key_n": self.key_n, "delta_n": self.delta_n,
            "did_kb": round(self.life_did / 1024, 1),
            "would_kb": round(self.life_would / 1024, 1),
        }
        self.v_sent = self.v_recv = self.v_dropped = self.a_sent = 0
        self.a_recv = 0
        # [WINDOW_KNOWS_ITS_RUNG_V1] Start empty. A window in which the send loop
        # never ran -- video disabled, camera gone -- is labelled from
        # _level_now, the rung still in effect, rather than inheriting the
        # previous window's set. Carrying the set forward made an audio-only
        # window read as "L4..L7" because L7 was still in it from before.
        self._levels = set()
        self.v_wire = self.a_wire = 0
        self.v_rx_wire = self.a_rx_wire = 0
        self.key_bytes = self.key_n = self.delta_bytes = self.delta_n = 0
        self._reset_t = time.time()

    def on_video_sent(self, frame_bytes, is_key):
        with self.lock:
            self.v_sent += 1
            wire = frame_bytes + self.FRAME_OVERHEAD
            self.v_wire += wire
            if is_key: self.key_bytes += frame_bytes; self.key_n += 1
            else:      self.delta_bytes += frame_bytes; self.delta_n += 1
            # DID = what we actually sent; WOULD = same frame as a full (key) frame
            self.life_did += wire
            if is_key: self._last_key = frame_bytes
            self.life_would += (frame_bytes if is_key else (self._last_key or frame_bytes)) \
                               + self.FRAME_OVERHEAD
            self._roll()

    def on_video_dropped(self, n=1):
        with self.lock:
            self.v_dropped += n; self._roll()

    def on_video_recv(self, wire_bytes=0):
        with self.lock:
            self.v_recv += 1
            self.v_rx_wire += int(wire_bytes or 0)
            self._roll()

    def on_audio_recv(self, wire_bytes=0):
        """[RECEIVE_IS_MEASURED_V1] Audio arriving. Below L5 this is the whole of
        the inbound traffic, and nothing was counting it."""
        with self.lock:
            self.a_recv += 1
            self.a_rx_wire += int(wire_bytes or 0)
            self._roll()

    def on_audio_sent(self, wire_bytes):
        with self.lock:
            self.a_sent += 1; self.a_wire += wire_bytes; self._roll()

    def snapshot(self):
        with self.lock:
            return dict(self.pub)


def _recv_exact_nb(sock, n, timeout=None):
    """[NONBLOCKING_SEND_V1] Read exactly n bytes from a NON-BLOCKING socket.

    A.recv_exact() loops on sock.recv() and relies on the socket to block. The send
    side now puts this same fd in non-blocking mode -- blocking is a property of the
    FD, not of a direction -- so readiness is asked for explicitly with select()
    instead. Semantics are otherwise identical: exactly n bytes, or ConnectionError.
    """
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (BlockingIOError, InterruptedError):
            if not select.select([sock], [], [], timeout if timeout else 1.0)[0]:
                if timeout is not None:
                    raise ConnectionError("recv timed out after %.1fs with %d/%d "
                                          "bytes" % (timeout, len(buf), n))
            continue
        if not chunk:
            # [RX_SAYS_WHERE_V1] "peer closed" alone cannot distinguish a clean
            # close BETWEEN frames from a peer that vanished halfway through one.
            # They are different faults: the first is a hang-up, the second is a
            # truncation, and the second means whatever closed the socket did it
            # while bytes were in flight. Say which.
            if buf:
                raise ConnectionError(
                    "peer closed MID-FRAME after %d of %d bytes" % (len(buf), n))
            raise ConnectionError("peer closed cleanly between frames")
        buf += chunk
    return buf


class AbortedFrame(Exception):
    """[ABORT_SENTINEL_V1] The sender could not finish this frame. The stream is
    still in sync -- the declared length was fully accounted for -- so the caller
    discards this frame and reads the next one."""


def recv_frame(sock):
    """Read one whole frame, or raise AbortedFrame if the sender gave up on it.

    [ABORT_SENTINEL_V1] The full declared length is ALWAYS consumed, whether the
    frame is good or aborted, so the stream stays in sync either way and the very
    next read starts on a length prefix. That is the entire point: an aborted
    frame costs one frame, not the connection.
    """
    (n,) = _LEN.unpack(_recv_exact_nb(sock, _LEN.size))
    body = _recv_exact_nb(sock, n)
    # The sentinel is written LAST and pads to the declared length, so it is the
    # tail that identifies an abort. Checking the tail rather than scanning the
    # payload is deterministic -- a legitimate video frame that happens to
    # contain these four bytes is not mistaken for one.
    if n >= 4 and body[-4:] == ABORT_SENTINEL:
        raise AbortedFrame("sender aborted a %d byte frame" % n)
    return body


# -- relay: type-agnostic fan to all other peers (identical doctrine to fnphone) -
# [AUDIO_SECONDS_NOT_FRAMES_V1] How much AUDIO TIME an Opus packet carries.
#
# Frame COUNT does not say whether audio is complete. 16.5 packets/s is a third
# of the feed if they are 20ms packets and all of it if they are 60ms packets,
# and the two are indistinguishable by rate. Byte size does not settle it either:
# Opus is VBR, and 60ms of steady fan noise from a headless box can encode
# smaller than 20ms of a person talking in a room. I claimed loss from exactly
# that comparison and it did not hold.
#
# The packet says so itself. RFC 6716 s3.1: the TOC byte is the first byte of
# every Opus packet. config = toc >> 3 selects the mode and frame size; c = toc &
# 0x3 gives the frame count code. That is a read, not an inference.
_OPUS_FRAME_MS = (
    # config 0-11: SILK, 10/20/40/60ms per bandwidth triple
    10.0, 20.0, 40.0, 60.0,   10.0, 20.0, 40.0, 60.0,   10.0, 20.0, 40.0, 60.0,
    # config 12-15: hybrid, 10/20ms
    10.0, 20.0,               10.0, 20.0,
    # config 16-31: CELT, 2.5/5/10/20ms per bandwidth quad
    2.5, 5.0, 10.0, 20.0,     2.5, 5.0, 10.0, 20.0,
    2.5, 5.0, 10.0, 20.0,     2.5, 5.0, 10.0, 20.0,
)


def opus_packet_ms(pkt: bytes) -> float:
    """Milliseconds of audio in one Opus packet.

    Code 0 = one frame; 1 and 2 = two frames; 3 = an arbitrary count in the
    frame-count byte (low 6 bits).

    [NO_FALLBACK_V1] RAISES on an unreadable packet; it does not return 0.0.
    This value is summed into the "s/s" figure that decides whether an audio
    feed is complete, so a silent 0.0 makes a healthy stream look starved and
    sends someone looking at the wrong end of the wire -- which is exactly the
    afternoon that produced this function. The caller counts and reports the
    refusal instead.
    """
    if not pkt:
        raise ValueError("empty Opus packet: no TOC byte")
    toc = pkt[0]
    ms = _OPUS_FRAME_MS[(toc >> 3) & 0x1F]
    code = toc & 0x03
    if code == 0:
        return ms
    if code in (1, 2):
        return ms * 2
    if len(pkt) < 2:
        raise ValueError("Opus code 3 packet truncated: no frame-count byte")
    return ms * (pkt[1] & 0x3F)


def _close_socket(sk, why=""):
    """[SHUTDOWN_NOT_CLOSE_V1] Close a socket THIS thread owns, with the options
    that make the close well-defined.

    SO_LINGER off (l_onoff=0): close() returns immediately and the kernel drains
    whatever is still queued in the background. The alternative -- lingering --
    parks the calling thread for the timeout on a peer that has already gone, and
    on this relay that thread is a pump or a teardown that other callers are
    waiting on. A relay that blocks to be polite to a dead socket is a relay that
    stops serving the living.

    SO_REUSEADDR on the LISTENER is what lets the relay restart without waiting
    out TIME_WAIT on :9000; it is set at bind (see serve()). Setting it here as
    well is harmless and keeps the property with the close rather than a hundred
    lines away.

    NEVER call this on a socket another thread is inside recv() on -- shutdown()
    that one and let its own pump reach here. Closing it out from under a live
    reader releases the descriptor NUMBER, which another thread's socket can then
    be assigned, so the reader's next call reads someone else's connection or
    fails EBADF. Measured on Seattle6, 2026-08-08: a real error on one plane and
    "OSError: [Errno 9] Bad file descriptor" on its mate, in the same second,
    twice in one capture.
    """
    try:
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                      struct.pack("ii", 0, 0))
    except OSError:
        pass                      # already closed or not a TCP socket
    try:
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except OSError:
        pass
    try:
        sk.close()
    except OSError as e:
        if why:
            print("[relay] close(%s) failed: %r" % (why, e), flush=True)


class Relay:
    """[MEDIASPEED_V1] The media host fnav clients actually speak.

    Under normal conditions the bandwidth of a leg is discovered and the ladder
    finds its own level. MediaSpeed is an ARTIFICIAL constraint laid over that:
    one number per client, published by the client itself, applied to BOTH legs
    because a client's link is symmetric. The client throttles its own uplink;
    this throttles the downlink the relay serves to it. Independent per caller.

    Absent or bps == 0 is unrestricted, and costs nothing -- no bucket exists.
    """

    BP_WINDOW_S   = 2.0    # [DELIVERED_RATE_FEEDBACK_V1] delivery is judged over a
                           # window, not per frame: one shed frame is not a verdict.
    BP_STARVED     = 0.5   # a viewer receiving less than this FRACTION of what is
                           # aimed at them is not keeping up. Measured on hardware:
                           # 1.2 fps delivered against 23.5 fps sent -- a ratio of
                           # 0.05 -- while the sender held L7 and reported perfect
                           # health, because nothing told it otherwise.
    KEYREQ_MIN_S  = 3.0    # [KEYREQ_NEEDS_ROOM_V1] at most one request per sender
                           # per this long. 0.5 was chosen to "beat any encoder
                           # interval", which was the wrong target: a keyframe is the
                           # most expensive frame on the wire and the interval is now
                           # bounded at fps*2 anyway. This is a backstop for a request
                           # that went unheard, not a cadence.
    CAP_FRESH_S   = 120    # a cap from a client that has gone quiet this long
                           # is stale: stop applying it rather than holding the
                           # relay down on behalf of somebody who left
    POLL_QUIET_S  = 30.0   # steady state: the setting rarely changes
    POLL_ACTIVE_S = 3.0    # after a change: someone has a slider in their hand
    SETTLE_S      = 10.0   # ... until it has been stable this long

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.peers = {}
        self.lock = threading.Lock()
        self._caps = {}        # peer_ip -> bps  (only entries with bps > 0)
        self._buckets = {}     # peer_ip -> (tokens, last_t)
        self._capped_drops = 0
        self._aborted_in = 0   # frames a sender abandoned mid-write
        # [KEYFRAME_ANCHOR_V1] per-viewer keyframe state
        self._pending_key = {}   # who -> the newest keyframe not yet sent
        self._await_key = {}     # who -> True while unanchored (shed inters)
        # [SOCKET_WRITE_LOCK_V1] One lock per peer socket. _fan runs on EACH
        # SENDER'S pump thread, so without this two threads can write the same
        # peer socket at once -- the bytes interleave, the length prefix
        # desyncs, and the receiver decodes garbage forever. That is exactly
        # what happened the first time the relay tried to talk back to senders.
        # It is the prerequisite for any relay->sender channel.
        self._wlock = {}         # conn -> threading.Lock
        # [KEYFRAME_BACKLOG_V1] per-sender view of how badly viewers are backing
        # up, reported upstream so the ladder can act on EVIDENCE rather than on
        # a bitrate table.
        self._backlog_at = 0.0
        self._vdrop_of = {}    # who -> video frames shed by the cap, unreported
        # [TWO_PLANES_V1] Each caller holds TWO connections. Fan-out is per
        # plane, and exclusion is by NAME rather than by socket -- otherwise a
        # caller receives their own video on their own video socket.
        self._plane = {}       # conn -> PLANE_AUDIO | PLANE_VIDEO
        self._name_of = {}     # conn -> declared caller name
        self._planes_of = {}   # name -> {planes seen}
        self._bp_last = 0.0    # last backpressure report to senders
        # [KEYFRAME_ON_REQUEST_V1] Last keyframe request per SENDER connection.
        # Rate-limited: a keyframe is the most expensive frame there is, and a
        # request per shed inter would ask for one ~20x a second and swamp the very
        # link that is already short. One per KEYREQ_MIN_S is enough -- the request
        # only has to outpace the encoder's own interval to be worth anything.
        self._keyreq_at = {}   # sender conn -> monotonic time of last request
        # [TEARDOWN_IS_PER_SESSION_V1] Session tag per connection, taken from the
        # plane declaration, so a teardown can tell its own planes from a later
        # reconnect wearing the same name. See the note in _pump's finally.
        self._session_of = {}  # conn -> per-process teardown tag (bytes)
        # [FAN_IS_PER_SESSION_V1] conn -> the CALL session this connection
        # declared. Distinct from _session_of, which is a per-PROCESS uuid used
        # only to pair a caller's two planes at teardown.
        self._call_of = {}
        # [DROP_THE_FRAME_NOT_THE_CALLER_V1] Bytes of a committed frame this socket
        # still owes, to be paid in abort-sentinel padding before anything else is
        # written to it. A socket in debt sheds new frames; it does not get closed.
        self._owed = {}        # conn -> bytes still owed
        self._shed_busy = 0    # frames dropped because the socket had no room
        self._shed_partial = 0 # frames abandoned part-written (debt recorded)
        # [EWOULDBLOCK_FIRST_V1] Audio sheds counted SEPARATELY. Audio is the
        # floor of the ladder, so an audio frame that did not go is a different
        # event from a video frame that did not go -- it is the thing the whole
        # ladder exists to protect, and it was being counted in the same bucket
        # as the frames the ladder deliberately discards. A client's audio plane
        # went silent for 19.0s while its video ran clean and no relay counter
        # said anything had been dropped.
        self._shed_audio = 0
        # [KEYFRAME_SURVIVES_EWOULDBLOCK_V1] conn -> the keyframe waiting for a
        # writable socket, and how many frames have been sacrificed to it.
        self._held_key = {}
        self._held_drops = {}
        # [A_HOLD_IS_NOT_FOREVER_V1] when each held keyframe started waiting
        self._held_at = {}
        # Bounded here, on the RELAY, which is the class that holds them. It
        # first went on Call -- where nothing reads it -- and the relay would
        # have raised AttributeError on the first hold, in a fan-out thread.
        self.HELD_KEY_MAX_S = 3.0
        self._rlog = None
        # [DELIVERED_RATE_FEEDBACK_V1] Per-viewer delivery over the current window:
        # what was FANNED at them versus what actually WENT. The sender cannot see
        # this -- its own uplink is clean at full rate while a viewer receives one
        # frame a second -- so the relay has to say it.
        self._fan_n = {}       # viewer -> VIDEO frames aimed at them this window
        self._deliv_n = {}     # viewer -> VIDEO frames that actually went
        self._bp_window_t0 = time.time()

        # [AUDIO_IS_ACCOUNTED_SEPARATELY_V1] Audio has its OWN counters, on both
        # sides of the relay, because the question "is audio arriving slowly or
        # being dropped" cannot be answered from a rate alone. A client seeing
        # 11.5 KB/s of audio while sending 32 KB/s has two completely different
        # possible causes and the same number for both:
        #     the sender is producing less    -> ingress is low
        #     the relay is discarding it      -> ingress is fine, egress is not
        # Only counting BOTH ends separates them.
        #
        # They are NOT the _fan_n/_deliv_n dicts. Those are consumed and RESET
        # wholesale by the video backpressure window (`self._fan_n = {}`), so an
        # audio count parked in them is destroyed on a beat that has nothing to
        # do with audio.
        self._in_n = {}        # (sender, kind) -> frames received this window
        self._in_b = {}        # (sender, kind) -> bytes received this window
        # [AUDIO_SECONDS_NOT_FRAMES_V1] (sender, kind) -> ms of AUDIO TIME.
        # This is the number that says whether the feed is complete; frames/s
        # does not, because the frame duration is the sender's choice.
        self._in_ms = {}
        # [VIDEO_IN_CARRIES_THE_RUNG_V1] (sender, kind) -> last arriving rung /
        # keyframes this window.
        self._in_lvl = {}
        self._in_key = {}
        self._a_fan_n = {}     # viewer -> AUDIO frames aimed at them
        self._a_deliv_n = {}   # viewer -> AUDIO frames that actually went
        self._acct_t0 = time.time()
        # [AUDIO_BACKPRESSURE_V1] The audio plane's own reporting window.
        self._abp_window_t0 = time.time()
        self._last_change = 0.0
        self._stop = threading.Event()

    # -- MediaSpeed ---------------------------------------------------------
    def _read_caps(self):
        """{peer_ip: bps} across every live session, from the MediaSpeed tuples.

        Not wrapped in a bare except. A control plane that cannot answer is NOT
        a call with no caps: returning {} for both is how a broken reader becomes
        indistinguishable from an unconstrained one. The caller logs and keeps
        the caps it already has, which is the honest degradation -- the last
        known setting, not a silent lift.
        """
        import frognet_tuples as _T
        out = {}
        # [MEDIASPEED_V1] Service is "communicator" -- the namespace
        # comms_control.SERVICE writes under. It is NOT "mediastream": that is
        # the namespace of frognet_mediahost_server.py, a DIFFERENT media host
        # that fnav clients do not speak (see the comment in
        # frognet-mediahost.service). Reading the wrong namespace returns an
        # empty set forever and looks exactly like nobody having set a cap.
        # [MEDIASPEED_FRESHEST_V1] One client leaves MANY MediaSpeed rows: every
        # launch mints a new me_id and the scope carries it, so a client that has
        # been restarted a few times has a row per run -- all with the same addr.
        # We key by ADDRESS, so they collapse onto one entry and "last row wins"
        # picks whichever the store happened to return last. Observed: the relay
        # read one value and never saw another while the slider was moved
        # repeatedly, because the winning row was from an earlier run.
        #
        # Take the FRESHEST row per address. ts is the writer's own stamp; it is
        # only ever compared against other rows from the SAME writer, so no clock
        # is being compared across machines.
        #
        # fresh_s bounds it as well: a client that has gone away stops capping
        # after CAP_FRESH_S instead of holding the relay down forever.
        _best = {}
        for row in _T.get("communicator", "MediaSpeed", fresh_s=self.CAP_FRESH_S):
            v = row.get("value") or {}
            _a = v.get("addr")
            if not _a:
                continue
            _ts = int(v.get("ts") or 0)
            if _a not in _best or _ts >= _best[_a][0]:
                _best[_a] = (_ts, v)
        for addr, (_ts, v) in _best.items():
            bps = int(v.get("bps") or 0)
            if bps > 0 and addr:
                out[addr] = bps
        return out

    # [RELAY_REAPS_DEAD_CALLS_V1] The relay is the only party with ground truth
    # about who is on a call: it holds the sockets. A `call` row is an
    # ASSERTION, written by a client and never withdrawn if that client dies,
    # is killed, or loses the link. Nothing was reaping them, so they piled up
    # -- 25 rows in one snapshot, one real, several duplicated across writers --
    # and a client could pick a dead one out of the list and join it.
    #
    # [FAN_IS_PER_SESSION_V1] makes joining a dead session harmless: connection,
    # declaration, silence. This makes it rare, which is the other half. A dead
    # row that nobody can join is still a dead row in every roster.
    #
    # ONLY rows whose host is THIS relay. Another media host's calls are not
    # ours to judge -- we have no sockets for them and no way to know if they
    # are live, and deleting on that ignorance is worse than leaving a stale row.
    REAP_EVERY_S = 30.0
    # A call row is written BEFORE the caller dials, so a brand-new session has
    # no connections for a moment. Two poll intervals of grace, so a row must be
    # unattended for a full minute before it goes.
    REAP_GRACE_S = 60.0

    def _live_sessions(self):
        with self.lock:
            return {s for s in (self._call_of.get(c) for c in self.peers) if s}

    def _reap_dead_calls(self):
        """Delete call rows for sessions with nobody connected HERE."""
        import frognet_tuples as _T
        import comms_control as _cc
        me = None
        while not self._stop.is_set():
            self._stop.wait(self.REAP_EVERY_S)
            if self._stop.is_set():
                continue
            try:
                if me is None:
                    me = _T.my_ip()
                live = self._live_sessions()
                now = int(time.time())
                gone = []
                for r in _T.get(_cc.SERVICE, "call", fresh_s=0):
                    v = r.get("value") or {}
                    sess = v.get("session")
                    if not sess or sess in live:
                        continue
                    if str(v.get("host") or "") != me:
                        continue          # not ours to judge
                    age = now - int(v.get("ts") or 0)
                    if age < self.REAP_GRACE_S:
                        continue          # written moments ago; the dial is coming
                    gone.append((sess, age, v.get("members") or []))
                if not gone:
                    continue
                n = 0
                for row in _T._values_raw(_cc.SERVICE, _T.DEFAULT_DBHOST):
                    nm = row.get("SensorName", "")
                    if not nm.startswith("%scall." % _T.SD_PREFIX):
                        continue
                    data = row.get("data") or {}
                    if data.get("session") not in {g[0] for g in gone}:
                        continue
                    if row.get("SensorID") is None:
                        continue
                    if _T._delete_by_id(_T.DEFAULT_DBHOST, row["SensorID"]):
                        n += 1
                for sess, age, members in gone:
                    print("[relay] reaping call %s -- no connections here for "
                          "%ds (members claimed: %s)"
                          % (sess, age, ", ".join(members) or "none"),
                          flush=True)
                print("[relay] reaped %d dead call row(s); live sessions: %s"
                      % (n, ", ".join(sorted(live)) or "none"), flush=True)
            except Exception as e:
                # Reaping is housekeeping. It must never take the relay down,
                # and a failure that says nothing is how a reaper quietly stops.
                print("[relay] call reap failed: %r" % (e,), flush=True)

    def _poll_caps(self):
        """Responsive while a slider is moving, quiescent otherwise.

        POLL_ACTIVE_S while the setting is in motion, dropping back to
        POLL_QUIET_S once it has held still for SETTLE_S. A demo knob that takes
        half a minute to take effect is not demonstrating anything; a knob nobody
        is touching should not cost a tuple read every three seconds.
        """
        while not self._stop.is_set():
            try:
                new = self._read_caps()
                if new != self._caps:
                    self._last_change = time.time()
                    with self.lock:
                        for ip in list(self._buckets):
                            if new.get(ip) != self._caps.get(ip):
                                self._buckets.pop(ip, None)   # cap moved -> fresh bucket
                        self._caps = new
                    print("[relay] MediaSpeed caps now %s" % (new or "none"), flush=True)
            except Exception as e:
                # Loud, and the previous caps stand.
                print("[relay] MediaSpeed read failed: %r (keeping %s)"
                      % (e, self._caps or "none"), flush=True)
            active = (time.time() - self._last_change) < self.SETTLE_S
            self._stop.wait(self.POLL_ACTIVE_S if active else self.POLL_QUIET_S)

    def _spend(self, who, nbits):
        """[KEYFRAME_ANCHOR_V1] Charge a MANDATORY item, overdrawing if needed.

        A keyframe can be larger than one second of budget -- 9 KB against a
        30 kbps cap is 72000 bits versus a 30000-bit bucket -- so a bucket that
        only ever refuses would never let one through, and the viewer would stay
        unanchored forever watching pixellation. The keyframe is not optional:
        without it nothing after it decodes.

        So it goes, the bucket goes negative, and inter frames are refused until
        the debt is repaid. Average rate is still honoured; it is paid in
        arrears instead of in advance.
        """
        ip = who.split(":")[0] if who else ""
        bps = self._caps.get(ip, 0)
        if bps <= 0:
            return
        now = time.time()
        tokens, last = self._buckets.get(ip, (0.0, now))
        tokens = min(float(bps), tokens + (now - last) * bps) - nbits
        self._buckets[ip] = (tokens, now)

    # [EWOULDBLOCK_FIRST_V1] How many times an AUDIO frame is offered to a
    # socket that was not ready. Audio is the floor of the ladder: everything
    # above it is negotiable and it is not. A single EWOULDBLOCK on a voice
    # packet is a momentary buffer, not a verdict, so it gets a second look
    # after a short yield. Two, not more -- this runs on a SENDER'S pump thread
    # and a third attempt starts costing the sender its own frame rate.
    AUDIO_SEND_TRIES = 2
    AUDIO_RETRY_WAIT_S = 0.010     # one yield, not a sleep loop

    def _locked_send(self, conn, frame):
        """[SOCKET_WRITE_LOCK_V1] Serialise writes to ONE peer socket.

        [DROP_THE_FRAME_NOT_THE_CALLER_V1] A frame that will not fit is DROPPED. The
        connection is not.

        send_frame() gave a committed frame 0.25s to finish, then tried to pad it out
        with abort sentinels, and if the padding would not go either it raised and the
        caller closed the socket. On a saturated leg that is the relay hanging up on
        its own viewer: measured on hardware, 608 of 647 bytes written, connection
        closed, call over, after six minutes of a link that was carrying 99 kbps of a
        93 kbps cap. Nobody was at fault -- the socket was simply full, which is the
        NORMAL state of a constrained leg, and shedding is what the whole ladder is
        built to do.

        The framing invariant is what made abandoning a frame impossible: the stream
        is length-prefixed, so a frame stopped at 608 of 647 leaves the receiver
        consuming the next frame's bytes as this one's tail. That invariant is kept
        here WITHOUT parking a thread or closing anything: a partially written frame
        leaves this socket OWING the remainder, and the debt is paid in abort-sentinel
        padding at the top of the next write. Until it is paid, new frames are dropped
        -- which is exactly right, because a socket that could not finish the last
        frame has no room for the next one.

        [EWOULDBLOCK_FIRST_V1] The kind of the frame decides how hard we try, and
        readiness is ASKED FOR before anything is written.

          - EWOULDBLOCK is checked FIRST, always, with a zero-timeout select.
            Nothing is offered to a socket that has not said it can take it.
            Discovering it by getting BlockingIOError out of send() is the same
            answer arrived at late, and on a partial write it is arrived at
            AFTER the stream is already committed.
          - AUDIO gets AUDIO_SEND_TRIES attempts. It is the floor of the ladder;
            one momentary full buffer is not a reason to lose a voice packet.
          - VIDEO gets one. Not ready, or short write -> dropped. Video is what
            the ladder exists to shed and a stale frame is worth less than the
            next one.

        Measured, 2026-08-08: a client's audio plane received nothing for 19.0s
        while its video plane ran clean at 148 KB/s on a separate socket. Audio
        and video are on separate sockets precisely so video cannot block audio,
        and they did not -- but audio was given exactly the same one-shot,
        shed-on-busy treatment as video, so nothing about being the floor of the
        ladder was actually expressed in the send path.

        Returns True if `frame` went, False if it was shed. Only a genuine socket
        error propagates, and only that should ever cost a connection.
        """
        with self.lock:
            lk = self._wlock.get(conn)
            if lk is None:
                lk = self._wlock[conn] = threading.Lock()

        # The kind is read from the frame itself, not from which plane the caller
        # thinks it is on. A frame that will not parse is treated as VIDEO --
        # sheddable -- because the one thing we must not do is give unknown bytes
        # the audio guarantee.
        try:
            _kind = _KIND.unpack_from(frame, 0)[0]
        except Exception:
            _kind = KIND_VIDEO
        is_audio = (_kind == KIND_AUDIO)
        tries = self.AUDIO_SEND_TRIES if is_audio else 1

        # [KEYFRAME_SURVIVES_EWOULDBLOCK_V1] A keyframe is not sheddable.
        #
        # Steps 4 and 5 of the send policy differ by frame kind. An inter frame
        # that hits EWOULDBLOCK is dropped and the next one is taken. A KEYFRAME
        # that hits EWOULDBLOCK is HELD, and the frames behind it are dropped
        # instead, until it goes or the socket errors -- because nothing after a
        # keyframe decodes without it, so dropping it does not cost one picture,
        # it costs every picture until the next one.
        #
        # This machinery existed but was gated on `self._caps` -- it only ran for
        # a viewer with a MediaSpeed cap. A viewer with no cap on a momentarily
        # full socket lost the keyframe outright and sat on a frozen frame until
        # the encoder's next interval. The trigger is EWOULDBLOCK, not the
        # presence of a cap.
        # video_is_key takes the WHOLE typed frame: it skips the kind byte and
        # the source name by offset to reach the level field. Handing it the
        # unpacked payload reads the wrong bytes and quietly returns False, so
        # every keyframe looked like an inter frame and none was ever held.
        is_key = (not is_audio) and bool(video_is_key(frame))

        # [KEYFRAME_SURVIVES_EWOULDBLOCK_V1] A NEWER keyframe usurps the held
        # one. The held frame is only worth anything as the picture the viewer
        # resumes from, and the freshest keyframe is a better resume point than
        # a stale one by exactly the age between them -- sending the old one
        # first would put a picture on screen that is already superseded, then
        # make the viewer wait for the new one behind it.
        if is_key and self._held_key.get(conn) is not None:
            self._held_key[conn] = frame
            self._held_at[conn] = time.time()   # a newer hold, newly timed
            if RELAY_DIAG:
                print("[relay] %s: newer keyframe usurps the held one "
                      "(%d frames sacrificed to the old one)"
                      % (self.peers.get(conn, "?"),
                         self._held_drops.get(conn, 0)), flush=True)
            self._held_drops[conn] = 0
            _held = frame
        else:
            _held = self._held_key.get(conn)

        # A held keyframe outranks whatever is being offered now.
        if _held is not None and not is_audio:
            if self._try_once(conn, _held):
                self._held_key.pop(conn, None)
                self._held_at.pop(conn, None)
                self._held_drops[conn] = 0
                return True
            # [A_HOLD_IS_NOT_FOREVER_V1] Still blocked. Drop THIS frame -- the
            # sacrifice the policy calls for -- and keep the keyframe.
            #
            # But not indefinitely. A viewer that never drains kept its keyframe
            # held for the life of the call, and every frame behind it was
            # dropped, so that viewer received NOTHING while the relay reported
            # a backlog on every window. Holding is worth it while the viewer is
            # about to recover; past that it is just a viewer receiving nothing
            # with a stale picture waiting for it.
            #
            # Past the bound the held frame goes and the viewer resumes at the
            # next keyframe on the wire -- which is a fresher resume point than
            # the one being held anyway.
            self._held_drops[conn] = self._held_drops.get(conn, 0) + 1
            _held_since = self._held_at.get(conn) or time.time()
            self._held_at.setdefault(conn, _held_since)
            if time.time() - _held_since > self.HELD_KEY_MAX_S:
                self._held_key.pop(conn, None)
                self._held_at.pop(conn, None)
                print("[relay] %s: keyframe held %.0fs and still will not go "
                      "(%d frames dropped behind it) -- discarding it. This "
                      "viewer resumes at the next keyframe."
                      % (self.peers.get(conn, "?"), self.HELD_KEY_MAX_S,
                         self._held_drops.get(conn, 0)), flush=True)
                self._held_drops[conn] = 0
                return False
            if self._held_drops[conn] == 1 or RELAY_DIAG:
                print("[relay] %s: keyframe held, dropping frames behind it "
                      "(%d so far)" % (self.peers.get(conn, "?"),
                                       self._held_drops[conn]), flush=True)
            self._shed_busy += 1
            return False

        with lk:
            for attempt in range(tries):
                # 1. EWOULDBLOCK FIRST -- before the debt, before the frame.
                #    A socket with no room cannot pay a debt either, and asking
                #    costs one syscall against a committed partial write.
                if not self._writable(conn):
                    if attempt + 1 < tries:
                        time.sleep(self.AUDIO_RETRY_WAIT_S)
                        continue
                    # [KEYFRAME_SURVIVES_EWOULDBLOCK_V1] hold, do not shed.
                    if is_key:
                        self._held_key[conn] = frame
                        self._held_drops[conn] = 0
                        print("[relay] %s: keyframe hit EWOULDBLOCK -- HELD, "
                              "not dropped" % (self.peers.get(conn, "?"),),
                              flush=True)
                        self._shed_busy += 1
                        return False
                    self._shed_busy += 1
                    if is_audio:
                        self._shed_audio += 1
                    return False

                # 2. Pay any debt from a previous frame. Framing before media:
                #    the receiver is mid-payload until the padding lands.
                owed = self._owed.get(conn, 0)
                if owed:
                    if self._pay_debt(conn, owed):
                        if attempt + 1 < tries:
                            time.sleep(self.AUDIO_RETRY_WAIT_S)
                            continue
                        self._shed_busy += 1
                        if is_audio:
                            self._shed_audio += 1
                        return False

                # 3. The frame. Ready means the buffer has room, not that it has
                #    room for ALL of this -- a short write is still possible and
                #    still commits the stream.
                blob = _LEN.pack(len(frame)) + frame
                try:
                    sent = conn.send(blob)
                except (BlockingIOError, InterruptedError):
                    # Ready then not ready: another writer got in between.
                    if attempt + 1 < tries:
                        time.sleep(self.AUDIO_RETRY_WAIT_S)
                        continue
                    if is_key:                 # [KEYFRAME_SURVIVES_EWOULDBLOCK_V1]
                        self._held_key[conn] = frame
                        self._held_drops[conn] = 0
                        self._shed_busy += 1
                        return False
                    self._shed_busy += 1
                    if is_audio:
                        self._shed_audio += 1
                    return False
                if sent >= len(blob):
                    return True

                # 4. Short write. The stream is COMMITTED, so the frame cannot
                #    simply be forgotten -- the receiver is reading a declared
                #    length and would consume the next frame's bytes as this
                #    one's tail. Take what the socket will take now; whatever is
                #    left becomes a debt paid in abort-sentinel padding at the
                #    top of the next write. That is how a video frame is
                #    "dropped" on a length-prefixed stream without desyncing it.
                #
                #    No retry from here even for audio: the frame is already
                #    committed and a second attempt would write a second copy of
                #    a frame the receiver is going to discard anyway.
                remaining = len(blob) - sent
                view = memoryview(blob)
                while remaining:
                    try:
                        n = conn.send(view[len(blob) - remaining:])
                    except (BlockingIOError, InterruptedError):
                        break
                    if not n:
                        break
                    remaining -= n
                if remaining:
                    self._owed[conn] = remaining
                    self._shed_partial += 1
                    if is_audio:
                        self._shed_audio += 1
                    if RELAY_DIAG:
                        print("  [relay] %s took %d/%d bytes of a %s frame; "
                              "owing %d in padding"
                              % (self.peers.get(conn, "?"),
                                 len(blob) - remaining, len(blob),
                                 "audio" if is_audio else "video", remaining),
                              flush=True)
                    return False
                return True
            return False

    def _try_once(self, conn, frame):
        """[KEYFRAME_SURVIVES_EWOULDBLOCK_V1] One non-blocking attempt at a whole
        frame. True if it went. Caller holds the write lock.

        Used for the retry of a HELD keyframe, which must not recurse back into
        _locked_send -- that would re-enter the hold logic on the frame it is
        already holding.
        """
        if not self._writable(conn):
            return False
        owed = self._owed.get(conn, 0)
        if owed and self._pay_debt(conn, owed):
            return False
        blob = _LEN.pack(len(frame)) + frame
        try:
            sent = conn.send(blob)
        except (BlockingIOError, InterruptedError):
            return False
        if sent >= len(blob):
            return True
        # Committed part-way: finish it or record the debt, exactly as the main
        # path does. A truncated frame is not a dropped one.
        remaining = len(blob) - sent
        view = memoryview(blob)
        while remaining:
            try:
                n = conn.send(view[len(blob) - remaining:])
            except (BlockingIOError, InterruptedError):
                break
            if not n:
                break
            remaining -= n
        if remaining:
            self._owed[conn] = remaining
            self._shed_partial += 1
            return False
        return True

    def _writable(self, conn):
        """[EWOULDBLOCK_FIRST_V1] Zero-timeout readiness probe.

        select() reports only that SOME room exists (SO_SNDLOWAT: 2048 on Linux,
        1 on Windows), so a True here does not promise the whole frame fits --
        which is why the short-write path above still exists. What it DOES give
        is the one thing worth having: a definite no, cheaply, before anything is
        committed to a length-prefixed stream.

        A socket in error is reported writable by select and fails on send. That
        is correct: a genuine socket error must reach the caller and cost the
        connection, and only the caller can decide that.
        """
        # [NO_FALLBACK_V1] select() is NOT wrapped. It used to catch OSError and
        # return True -- "I could not tell, so assume yes" -- which is a
        # substituted answer for a failure, and the caller cannot distinguish it
        # from a genuinely writable socket. A closed or invalid fd here means the
        # connection is finished, and _pump's handler already reaps it. Let it
        # say so.
        _r, _w, _x = select.select([], [conn], [], 0)
        return bool(_w)

    def _pay_debt(self, conn, owed):
        """[DROP_THE_FRAME_NOT_THE_CALLER_V1] Write abort-sentinel padding toward a
        partially written frame. Returns what is STILL owed (0 when square).

        Non-blocking and best effort: whatever the socket takes now is taken, the
        rest waits for the next attempt. The receiver is reading a declared length
        and will discard the frame when it sees the sentinel in the tail, so the
        stream stays in sync however long the debt takes to clear.
        """
        pad = ABORT_SENTINEL * (owed // 4 + 1)
        pad = pad[:owed]
        off = 0
        while off < owed:
            try:
                n = conn.send(memoryview(pad)[off:])
            except (BlockingIOError, InterruptedError):
                break
            if not n:
                break
            off += n
        left = owed - off
        if left:
            self._owed[conn] = left
        else:
            self._owed.pop(conn, None)
        return left

    def _over_cap(self, who, nbits):
        """Token bucket for one caller. True == over budget, drop this frame.

        The bucket starts EMPTY, not full: a full one permits an initial burst of
        one bucket-depth, measured at 192 kbps against a 100 kbps cap, which is
        the overshoot the cap exists to prevent. Depth is one second of budget,
        so it still absorbs normal jitter."""
        ip = who.split(":")[0] if who else ""
        bps = self._caps.get(ip, 0)
        if bps <= 0:
            return False
        now = time.time()
        tokens, last = self._buckets.get(ip, (0.0, now))
        tokens = min(float(bps), tokens + (now - last) * bps)
        if tokens < nbits:
            self._buckets[ip] = (tokens, now)
            return True
        self._buckets[ip] = (tokens - nbits, now)
        return False

    # [AUDIO_IS_ACCOUNTED_SEPARATELY_V1] Accounting window. Long enough that a
    # frame or two of jitter does not read as a drop, short enough to catch a
    # gap while it is still happening -- the 19.0s silence measured on 2026-08-08
    # would have produced nine of these lines.
    ACCT_WINDOW_S = 2.0

    HOLD_PUBLISH_S = 2.0   # [MEDIAHOLD_IS_MEMORY_V1] cadence for publishing what
                           # this relay is holding and why

    def _publish_holds(self):
        """[MEDIAHOLD_IS_MEMORY_V1] Publish WHAT THIS RELAY IS HOLDING, and why, as
        FrogNet Memory -- one MediaHold variable per viewer address.

        The relay is the only thing that knows a viewer is unanchored, how many
        keyframes it is sitting on, and how many inter frames it has shed. Nothing
        else can see it: the sender's own uplink is clean, so its screens read
        perfect health while the far end watches stills. That was the whole
        DOWNLINK_BACKPRESSURE problem, and the message-shaped answer to it was
        withdrawn because writing to peer sockets from _fan desynced the stream.

        Memory is the right shape for it. This is state, not an event: it is true
        until it stops being true, any number of readers can look, a reader that was
        not running when it changed still sees it, and nobody has to be told twice.
        Exchange memory, not messages.

        Written per VIEWER address so a reader can say WHICH participant is backing
        up, not merely that someone is. Re-asserted on a cadence because readers age
        it out; a relay that stops publishing simply stops appearing, which is the
        correct reading of a relay that has gone.
        """
        try:
            import frognet_tuples as _T
        except Exception as e:
            print("[relay] MediaHold: frognet_tuples unavailable (%s) -- backlog "
                  "will not be visible to any Communicator" % (type(e).__name__,),
                  flush=True)
            return
        _shed_audio_prev = 0
        while not self._stop.is_set():
            try:
                # [EWOULDBLOCK_FIRST_V1] Audio sheds, reported on this same slow
                # beat. _publish_holds skips non-video planes by construction
                # (`if self._plane.get(conn) != PLANE_VIDEO: continue`), so
                # MediaHold cannot say anything about an audio leg -- a stalled
                # audio plane produced no signal on the relay side at all, which
                # is why a 19.0s silence was only ever visible from the client.
                # A counter nobody reads is not instrumentation.
                _sa = self._shed_audio
                if _sa != _shed_audio_prev:
                    print("[relay] AUDIO SHED %d frame(s) since last report "
                          "(%d total) -- audio is the floor of the ladder and "
                          "should not be shedding"
                          % (_sa - _shed_audio_prev, _sa), flush=True)
                    _shed_audio_prev = _sa
                self._report_audio_accounting()
                with self.lock:
                    _names = dict(self._name_of)
                    _peers = dict(self.peers)
                    _await = dict(self._await_key)
                    _pend = {k: len(v) for k, v in self._pending_key.items()}
                    _vdrop = dict(self._vdrop_of)
                    _caps = dict(self._caps)
                    _fan = dict(self._fan_n)
                    _deliv = dict(self._deliv_n)
                for conn, who in _peers.items():
                    if self._plane.get(conn) != PLANE_VIDEO:
                        continue
                    ip = who.split(":")[0]
                    waiting = bool(_await.get(who))
                    held = _pend.get(who, 0)
                    shed = _vdrop.get(who, 0)
                    # The REASON, named, because "backed up" without a cause sends
                    # the next reader hunting. A cap is a deliberate constraint; no
                    # cap and still holding is a link that cannot carry the rung.
                    if not waiting and not held:
                        reason = "clear"
                    elif _caps.get(ip):
                        reason = "mediaspeed_cap %d bps" % _caps[ip]
                    else:
                        reason = "link_cannot_carry_rung"
                    _T.put(SERVICE_COMMS, "MediaHold", "host:%s" % ip,
                           {"addr": ip,
                            "name": _names.get(conn, ""),
                            "awaiting_keyframe": waiting,
                            "held_keyframe_bytes": held,
                            "inter_frames_shed": shed,
                            "cap_bps": int(_caps.get(ip, 0)),
                            # [DELIVERED_RATE_FEEDBACK_V1] the number the ladder is
                            # acting on, so the screen and the controller cannot
                            # disagree about what this viewer is receiving
                            "frames_aimed": int(_fan.get(who, 0)),
                            "frames_delivered": int(_deliv.get(who, 0)),
                            "reason": reason,
                            "ts": int(time.time())})
            except Exception as e:
                # Loud. A backlog nobody can see is the failure this exists to end.
                print("[relay] MediaHold publish failed: %r" % (e,), flush=True)
            self._stop.wait(self.HOLD_PUBLISH_S)

    def serve(self):
        # [RELAY_WIRE_LOG_V1] Opened before the listeners, so a relay that dies
        # during bring-up still leaves a file with a header.
        self._relay_log_open()
        threading.Thread(target=self._poll_caps, daemon=True).start()
        # [RELAY_REAPS_DEAD_CALLS_V1] the relay holds the sockets, so it is the
        # one party that can tell a live call row from an abandoned one.
        threading.Thread(target=self._reap_dead_calls, daemon=True).start()
        threading.Thread(target=self._publish_holds, daemon=True).start()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(16)
        srv.setblocking(False)                       # [ALL_NONBLOCKING_V1]
        print(f"[relay] FNWP A/V relay on {self.host}:{self.port} -- waiting", flush=True)
        while not self._stop.is_set():
            if not select.select([srv], [], [], 0.5)[0]:
                continue                             # [ALL_NONBLOCKING_V1]
            try:
                conn, addr = srv.accept()
            except (BlockingIOError, InterruptedError):
                continue
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # [ALL_NONBLOCKING_V1] Every socket in this program is non-blocking.
            # A blocking accept()ed connection means a wedged peer parks the
            # thread that serves it, and a close() from another thread does not
            # reliably interrupt a blocked recv -- which is why paired teardown
            # left half a caller behind. recv_frame()/_recv_exact_nb() already
            # wait with select(), so nothing here needs the socket to block.
            conn.setblocking(False)
            with self.lock:
                self.peers[conn] = f"{addr[0]}:{addr[1]}"
            print(f"[relay] caller joined {addr[0]}:{addr[1]} ({len(self.peers)} on call)", flush=True)
            threading.Thread(target=self._pump, args=(conn,), daemon=True).start()

    def _pump(self, conn):
        who = self.peers.get(conn, "?")
        err = None
        try:
            # [TWO_PLANES_V1] The first frame declares the plane. A connection
            # that never declares one defaults to audio: it keeps working and
            # simply gets no video, which is a display state, not a failure.
            first = recv_frame(conn)
            try:
                _k, _src, _pl = unpack_typed(first)
            except Exception:
                _k, _src, _pl = None, "", b""
            if _k == KIND_PLANE:
                plane = PLANE_VIDEO if _pl[:1] == PLANE_VIDEO else PLANE_AUDIO
                with self.lock:
                    self._plane[conn] = plane
                    self._name_of[conn] = _src
                    self._planes_of.setdefault(_src, set()).add(plane)
                    # [TEARDOWN_IS_PER_SESSION_V1] The declaration carries the
                    # caller's SESSION TAG after the plane byte. Ordering cannot do
                    # this job: the two planes of one caller race, so "declared
                    # before me" excludes the mate whenever the video socket happens
                    # to land first -- measured, 1 run in 4.
                    # [FAN_IS_PER_SESSION_V1] plane(1) + teardown tag(8) + call
                    # session (rest). The teardown tag is fixed width so the two
                    # cannot be confused for one another.
                    self._session_of[conn] = bytes(_pl[1:9])
                    self._call_of[conn] = bytes(_pl[9:]).decode(
                        "ascii", "replace")
                _cs = self._call_of.get(conn) or ""
                print("[relay] %s declared %s plane (%s) session=%s"
                      % (_src or who, "video" if plane == PLANE_VIDEO else "audio",
                         who, _cs or "NONE"), flush=True)
                if not _cs:
                    # [FAN_IS_PER_SESSION_V1] An undeclared call session cannot
                    # be matched against anything, so this connection can neither
                    # be fanned to nor fanned from. Saying so is the whole point:
                    # a client in the wrong session used to get a working call
                    # off a shared port while every session-scoped control tuple
                    # went to a session nobody was in. Silence is diagnosable.
                    print("[relay] %s declared NO call session -- it will "
                          "neither send nor receive. Nothing to match on."
                          % (_src or who,), flush=True)
            else:
                with self.lock:
                    self._plane[conn] = PLANE_AUDIO
                print("[relay] %s did not declare a plane -- assuming audio" % who,
                      flush=True)
                self._count_in(conn, first)
                self._fan(conn, first)      # not a declaration: it is media
            while True:
                try:
                    _fr = recv_frame(conn)
                    self._count_in(conn, _fr)
                    self._fan(conn, _fr)
                except AbortedFrame:
                    # [ABORT_SENTINEL_V1] The sender upstream could not finish
                    # this frame. The stream is still in sync; drop it and read
                    # the next. Not a peer failure.
                    self._aborted_in += 1
                    continue
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        finally:
            with self.lock:
                _who_key = self.peers.get(conn, "")      # [PENDING_KEY_LEAK_V1]
                self.peers.pop(conn, None)
                self._plane.pop(conn, None)
                self._wlock.pop(conn, None)
                self._keyreq_at.pop(conn, None)          # [KEYFRAME_ON_REQUEST_V1]
                # [TEARDOWN_IS_PER_SESSION_V1] READ the ordinal before dropping it:
                # the mate lookup below needs to know which session THIS conn
                # belonged to, and popping it first left _mine None, which fell
                # through to the old name-only behaviour.
                _mine = self._session_of.pop(conn, None)
                self._call_of.pop(conn, None)
                # [PENDING_KEY_LEAK_V1] `who` must be read BEFORE peers is popped:
                # after the pop this looked up "" and the held keyframe was never
                # freed.
                self._pending_key.pop(_who_key, None)
                _nm = self._name_of.pop(conn, None)
            # [SHUTDOWN_NOT_CLOSE_V1] OUR OWN socket, from our own pump thread.
            _close_socket(conn, who)
            # [TWO_PLANES_V1] Half a caller is not a caller. Losing one plane
            # closes the other, so a peer cannot linger as a video-only ghost
            # that nobody can hear.
            #
            # [TEARDOWN_IS_PER_SESSION_V1] ...but the mate is the OTHER PLANE OF
            # THIS CONNECTION, not every socket that happens to share a name.
            #
            # Collecting mates by name alone tore down connections that arrived
            # AFTER the dying one. Restart `fnav.py --name Dave` and the new
            # process connects while the old sockets are still unwinding; the old
            # _pump's finally then runs, matches "Dave", and shutdown()s the fresh
            # pair. The new client sees both planes close moments after connecting,
            # with nothing wrong at either end and no relay log line. Reproduced
            # deterministically in sim_relay_teardown.py, scenario C.
            #
            # A caller is identified by the two sockets that declared the same name
            # in the same session, so only conns that were present when THIS one
            # was is a mate. The declaration carries a per-PROCESS session tag after
            # the plane byte; two planes of one caller share it, and a reconnect
            # mints a new one. Match on name AND tag.
            #
            # A client too old to send a tag declares b"" and matches other tagless
            # connections of the same name -- i.e. exactly the old behaviour, for
            # exactly the clients that had it. The relay and the clients ship
            # together (see MANIFEST), so that window is the upgrade itself.
            if _nm:
                with self.lock:
                    _mates = [c for c, n in self._name_of.items()
                              if n == _nm and self._session_of.get(c) == _mine]
                for _c in _mates:
                    if _c is conn:
                        continue          # our own; this finally closes it
                    # [ALL_NONBLOCKING_V1] shutdown() makes the mate's select()
                    # return readable immediately and its recv raise, which
                    # close() alone does not reliably do from another thread.
                    # Without this the surviving plane lingered and the caller
                    # stayed half-present.
                    #
                    # [SHUTDOWN_NOT_CLOSE_V1] shutdown() ONLY. Never close a
                    # socket another thread is inside recv() on.
                    #
                    # close() releases the descriptor number. The mate's pump is
                    # blocked in recv_frame at that moment; shutdown() wakes it,
                    # it goes to read, and the fd it holds is gone -- so instead
                    # of an orderly EOF it gets OSError EBADF, from a file
                    # descriptor that may by then have been REUSED by another
                    # thread's socket. Measured on Seattle6, 2026-08-08, twice in
                    # the same capture:
                    #
                    #   caller left 10.250.250.20:59394 -- recv ended:
                    #       ConnectionResetError: [Errno 104]
                    #   caller left 10.250.250.20:59393 -- recv ended:
                    #       OSError: [Errno 9] Bad file descriptor
                    #
                    # One real reason, and its mate reporting EBADF in the same
                    # second -- the teardown injuring the thread it was trying to
                    # stop. The owning pump's own finally closes its own socket,
                    # which is the only place that can do it safely.
                    try:
                        _c.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass              # already gone; its own pump will reap it
            print(f"[relay] caller left {who} ({len(self.peers)} remain)"
                  f"{' -- recv ended: ' + err if err else ''}", flush=True)

    # [RELAY_WIRE_LOG_V1] The relay's own CSV. The sender has had one since
    # [WIRE_LOG_V1]; the relay is the only place that can distinguish "never got
    # the frames" from "got them and did not write them", and it was printing to
    # stdout only -- which on a systemd unit means hunting a journal, and on a
    # foreground shell means it scrolls.
    #
    # One row per viewer per plane per window, carrying BOTH sides: what arrived
    # from that sender, and what was aimed at and delivered to that viewer.
    RELAY_LOG_PERIOD_S = 2.0

    def _relay_log_open(self):
        path = os.environ.get("FROGNET_RELAY_LOG")
        if path is None:
            import tempfile
            # [RELAY_WIRE_LOG_V1] Relay(host, port) has NO name attribute --
            # only Call does. Using self.name here crashed serve() at startup
            # and systemd restart-looped it 274 times. The relay's identity is
            # its bind address; use that.
            path = os.path.join(tempfile.gettempdir(),
                                "frognet-relay-%s_%s.csv"
                                % (str(self.host).replace(":", "_") or "any",
                                   self.port))
        if not path or path.lower() in ("0", "off", "none"):
            self._rlog = None
            return
        try:
            new = not os.path.exists(path) or os.path.getsize(path) == 0
            self._rlog = open(path, "a", buffering=1)
            if new:
                self._rlog.write(
                    "ts,relay,who,name,plane,"
                    "in_fps,in_kbps,aimed,delivered,dropped,pct,"
                    "shed_busy,shed_partial,shed_audio,owed,cap_bps\n")
            print("[relay] wire log -> %s" % path, flush=True)
        except OSError as e:
            self._rlog = None
            print("[relay] RELAY LOG DISABLED: cannot open %s: %r"
                  % (path, e), flush=True)

    def _relay_log_row(self, who, name, plane, in_n, in_b, aimed, got, dt):
        w = getattr(self, "_rlog", None)
        if w is None:
            return
        ip = who.split(":")[0]
        owed = 0
        try:
            with self.lock:
                for _c, _w in self.peers.items():
                    if _w == who:
                        owed = self._owed.get(_c, 0)
                        break
                cap = self._caps.get(ip, 0)
        except Exception as _e:
            # [NO_FALLBACK_V1] 0 already MEANS "unlimited" in this column, so it
            # cannot also mean "the lookup failed" -- a reader would see a row
            # claiming the viewer is uncapped. Empty is the honest value for a
            # figure we do not have.
            cap = ""
            owed = ""
            print("[relay] wire log: cap/owed lookup failed for %s: %r"
                  % (who, _e), flush=True)
        try:
            w.write("%.3f,%s,%s,%s,%s,%.2f,%.2f,%s,%s,%s,%.1f,%s,%s,%s,%s,%s\n"
                    % (time.time(), "%s:%s" % (self.host, self.port),
                       who, name, plane,
                       (in_n / dt) if dt else 0.0,
                       (in_b / 1024.0 / dt) if dt else 0.0,
                       aimed, got, aimed - got,
                       (100.0 * got / aimed) if aimed else 0.0,
                       self._shed_busy, self._shed_partial, self._shed_audio,
                       owed, cap))
        except (OSError, ValueError) as e:
            print("[relay] wire log write failed: %r -- closing" % (e,),
                  flush=True)
            try:
                w.close()
            except OSError:
                pass
            self._rlog = None

    def _report_audio_accounting(self):
        """[AUDIO_IS_ACCOUNTED_SEPARATELY_V1] Answer, in one line per leg:
        is audio arriving slowly, or is it being dropped?

        IN  = what this relay RECEIVED from that sender on the audio plane.
        AIM/GOT = what it aimed at each audio viewer and what actually went.

            low IN, AIM == GOT        the sender is producing less. Not us.
            healthy IN, AIM > GOT     we are dropping it. Ours.
            low IN and AIM > GOT      both, and the counts say how much of each.

        A rate alone cannot separate those and that is the whole reason this
        exists: a client seeing 11.5 KB/s while sending 32 KB/s has the same
        number for two opposite causes.

        [AUDIO_DRIVES_THE_RESOLUTION_V1] Audio must not be dropped until video
        has been shut down COMPLETELY and the link still cannot keep up. Audio is
        the floor; the video rung is what gives way to protect it, never the
        reverse. So a leg that shed audio while video was STILL BEING DELIVERED
        to the same caller is a doctrine violation, not a busy link, and it says
        so in those words -- because on the client it looks like ordinary
        congestion and nothing else in the system would ever name it.
        """
        now = time.time()
        dt = now - self._acct_t0
        if dt < self.ACCT_WINDOW_S:
            return
        with self.lock:
            _in_n = dict(self._in_n)
            _in_b = dict(self._in_b)
            _in_ms = dict(self._in_ms)
            _in_lvl = dict(self._in_lvl)
            _in_key = dict(self._in_key)
            _afan = dict(self._a_fan_n)
            _agot = dict(self._a_deliv_n)
            _vgot = dict(self._deliv_n)
            _vdrops = dict(self._vdrop_of)
            _names = dict(self._name_of)
            _peers = dict(self.peers)
            _plane = dict(self._plane)
            self._in_n, self._in_b, self._in_ms = {}, {}, {}
            self._in_key = {}          # _in_lvl is a LEVEL, not a count: it
                                       # persists until the sender changes rung
            self._a_fan_n, self._a_deliv_n = {}, {}
            self._acct_t0 = now

        # [WIRE_ACCOUNTING_IS_BOTH_PLANES_V1] VIDEO ingress. _count_in has always
        # recorded it -- keyed by (sender, kind) -- and nothing ever emitted it:
        # the loop below skipped every non-audio kind. So the relay could say
        # what audio arrived and what it delivered, and NOTHING about video
        # arriving, which is half of "is it coming in slowly or being dropped"
        # for the plane the ladder actually shed.
        #
        # Egress here is _vdrop_of, which is CUMULATIVE and is not consumed by
        # the video backpressure window. Deliberately not _fan_n/_deliv_n: those
        # feed the delivered ratio and are reset wholesale on that window, so
        # reading them here would steal the evidence the ladder runs on.
        # [ALONE_IS_NOT_SILENT_V1] A sender with no peers in its session.
        #
        # [FAN_IS_PER_SESSION_V1] made a wrong-session client receive nothing,
        # which was the point. What it did NOT do is tell that client anything:
        # the relay reads its frames, finds no targets, discards them, and says
        # nothing to anybody. TCP never pushes back because the relay is
        # draining the socket, so the ladder sees no drops and no backlog, climbs
        # to the top rung, and sits there. Measured 2026-08-10: 374 KB/s of 1080p
        # into a black hole, indefinitely, with frames=0 on the way back.
        #
        # Discarding is correct. Discarding quietly is not.
        _alone = {}
        with self.lock:
            for c, tag in self.peers.items():
                _cs = self._call_of.get(c) or ""
                _nm = self._name_of.get(c)
                _pl_c = self._plane.get(c, PLANE_AUDIO)
                if any(o is not c and (self._call_of.get(o) or "") == _cs
                       and self._plane.get(o, PLANE_AUDIO) == _pl_c
                       and self._name_of.get(o) != _nm
                       for o in self.peers):
                    continue
                _alone[_nm or tag] = _cs
        for (who, kind), n in sorted(_in_n.items()):
            if kind != KIND_VIDEO:
                continue
            if who in _alone and n:
                print("  [relay] %s is ALONE in session %s -- %.0f frame/s "
                      "arriving and being DISCARDED, nobody to fan to"
                      % (who, _alone[who] or "NONE", n / dt), flush=True)
            kb = _in_b.get((who, kind), 0) / 1024.0 / dt
            _vd = _vdrops.get(who, 0)
            _lv = _in_lvl.get((who, kind))
            _rv = RUNG_VIDEO.get(_lv) or {}
            _dim = ("L%d %dx%d" % (_lv, _rv["w"], _rv["h"])) if _rv else \
                   (("L%s ?x?" % _lv) if _lv is not None else "rung ?")
            self._relay_log_row(who, _names.get(
                next((c for c, w in _peers.items() if w == who), None), "?"),
                "video_in", n, _in_b.get((who, kind), 0), 0, 0, dt)
            print("[relay] VIDEO IN  %-22s %5.1f f/s %7.1f KB/s  %-14s "
                  "keys %d  capped_drops_total=%d"
                  % (who, n / dt, kb, _dim, _in_key.get((who, kind), 0), _vd),
                  flush=True)

        # Ingress, audio, per sender.
        for (who, kind), n in sorted(_in_n.items()):
            if kind != KIND_AUDIO:
                continue
            kb = _in_b.get((who, kind), 0) / 1024.0 / dt
            self._relay_log_row(who, _names.get(
                next((c for c, w in _peers.items() if w == who), None), "?"),
                "audio_in", n, _in_b.get((who, kind), 0), 0, 0, dt)
            _sec = _in_ms.get((who, kind), 0.0) / 1000.0
            print("[relay] AUDIO IN  %-22s %5.1f f/s %7.1f KB/s  "
                  "%5.2f s/s%s  (%s)"
                  % (who, n / dt, kb, _sec / dt,
                     "" if _sec / dt >= 0.95 else "  *** GAP ***",
                     _names.get(
                      next((c for c, w in _peers.items() if w == who), None), "?")),
                  flush=True)

        # Egress, audio only, per viewer -- and the doctrine check.
        for who, aimed in sorted(_afan.items()):
            got = _agot.get(who, 0)
            lost = aimed - got
            self._relay_log_row(who, _names.get(
                next((c for c, w in _peers.items() if w == who), None), "?"),
                "audio_out", 0, 0, aimed, got, dt)
            print("[relay] AUDIO OUT %-22s aimed %d  got %d  dropped %d "
                  "(%5.1f%% delivered)"
                  % (who, aimed, got, lost,
                     100.0 * got / aimed if aimed else 0.0), flush=True)
            if lost > 0:
                # Was video still flowing to the SAME caller while audio was
                # being dropped? Match on IP: the two planes of one caller are
                # two sockets on two ports at one address.
                ip = who.split(":")[0]
                vid = sum(v for w, v in _vgot.items()
                          if w.split(":")[0] == ip)
                if vid > 0:
                    print("[relay] [AUDIO_DRIVES_THE_RESOLUTION_V1] VIOLATION: "
                          "dropped %d audio frame(s) to %s while still "
                          "delivering %d video frame(s) to the same caller. "
                          "Audio is the floor -- video must be shut down "
                          "COMPLETELY before a single audio frame is shed."
                          % (lost, ip, vid), flush=True)

    def _count_in(self, sender, frame):
        """[AUDIO_IS_ACCOUNTED_SEPARATELY_V1] What ARRIVED at the relay, by kind.

        The ingress rate is the half of the picture the client cannot see. From
        the client's seat a quiet audio plane looks identical whether the sender
        went quiet or the relay threw the frames away.
        """
        # [NO_FALLBACK_V1] An unreadable frame is not a video frame. Counting it
        # as one puts a phantom in the VIDEO IN census -- the exact number used
        # to decide whether a sender is starving -- and hides that something
        # unparseable arrived at all. This is accounting; refuse to guess.
        try:
            _k = _KIND.unpack_from(frame, 0)[0]
        except Exception as _e:
            self._in_unparseable = getattr(self, "_in_unparseable", 0) + 1
            if self._in_unparseable in (1, 10, 100, 1000):
                print("[relay] %d unparseable frame(s) from %s: %r -- not "
                      "counted as any kind"
                      % (self._in_unparseable, self.peers.get(sender, "?"), _e),
                      flush=True)
            return
        who = self.peers.get(sender, "?")
        key = (who, _k)
        with self.lock:
            self._in_n[key] = self._in_n.get(key, 0) + 1
            self._in_b[key] = self._in_b.get(key, 0) + len(frame)
            # [VIDEO_IN_CARRIES_THE_RUNG_V1] Bytes alone cannot say whether a low
            # rate is a SMALL PICTURE or a starved one -- 40 KB/s is healthy at
            # 640x360 and a stall at 1280x720. The rung is a byte in the frame,
            # so read it. No decode.
            if _k == KIND_VIDEO:
                try:
                    _k3, _src3, _pl3 = unpack_typed(frame)
                    _lv, _c3, _d3 = unpack_video(_pl3)
                    self._in_lvl[key] = _lv
                    # WHOLE frame, not the payload -- see _locked_send.
                    if video_is_key(frame):
                        self._in_key[key] = self._in_key.get(key, 0) + 1
                except Exception:
                    pass
            # [AUDIO_SECONDS_NOT_FRAMES_V1] and how much TIME arrived.
            if _k == KIND_AUDIO:
                try:
                    _k2, _src2, _pl2 = unpack_typed(frame)
                    if _pl2 and _KIND.unpack_from(_pl2, 0)[0] == AUDIO_FMT_OPUS:
                        try:
                            _ms = opus_packet_ms(_pl2[_KIND.size:])
                        except ValueError as _e:
                            # [NO_FALLBACK_V1] Do not add 0.0 -- that understates
                            # the feed and reads as loss. Count it as unreadable
                            # and say so.
                            self._in_badopus = getattr(self, "_in_badopus", 0) + 1
                            if self._in_badopus in (1, 10, 100):
                                print("[relay] %d unreadable Opus packet(s) from "
                                      "%s: %s -- excluded from the s/s figure"
                                      % (self._in_badopus,
                                         self.peers.get(sender, "?"), _e),
                                      flush=True)
                            _ms = None
                        if _ms is not None:
                            self._in_ms[key] = self._in_ms.get(key, 0.0) + _ms
                    else:
                        # PCM: bytes / (rate * 2) seconds, in ms
                        self._in_ms[key] = (self._in_ms.get(key, 0.0)
                                            + (len(_pl2) - _KIND.size) * 1000.0
                                            / (AUDIO_RATE * 2))
                except Exception:
                    pass

    def _fan(self, sender, frame):
        with self.lock:
            # [TWO_PLANES_V1] Fan only to the SAME plane, and exclude the sender
            # by name so their own frames never come back on their other socket.
            _pl = self._plane.get(sender, PLANE_AUDIO)
            _me = self._name_of.get(sender)
            # [FAN_IS_PER_SESSION_V1] Same CALL SESSION, or no frame crosses.
            #
            # The fan forwarded to every peer on the port and the session id was
            # decoration here while being load-bearing in the control plane.
            # Measured 2026-08-10: two clients joined different sessions on
            # :9000, exchanged video normally, and every MediaSpeed and
            # MediaTreatment row was addressed to a session the other end was
            # not in -- so no cap and no commanded resolution ever crossed while
            # the pictures flowed fine.
            #
            # An empty session matches nothing, INCLUDING another empty one.
            _cs = self._call_of.get(sender) or ""
            targets = [] if not _cs else [
                (c, self.peers[c]) for c in self.peers
                if c is not sender
                and self._plane.get(c, PLANE_AUDIO) == _pl
                and (self._call_of.get(c) or "") == _cs
                and (_me is None or self._name_of.get(c) != _me)]
        # [AUDIO_FIRST_V1] Audio ALWAYS passes. Only video is charged to the
        # budget, and only video is shed.
        #
        # The cap used to charge and drop whichever frame was in hand, which was
        # wrong twice over. It could shed a voice packet -- audio is the floor of
        # the ladder and must survive everything above it. And shedding whole
        # VIDEO frames indiscriminately produces L7 at eight frames a second: a
        # slideshow at full resolution, which is exactly what a low-bandwidth
        # link should NOT look like. Observed: "very degraded picture, not
        # pixellated, appears to have a very low frame rate."
        #
        # A capped viewer should get FEWER BYTES PER FRAME (a lower rung), not
        # fewer frames. The relay cannot re-encode, so what it can do is spend
        # the whole budget on audio first and let video have what is left -- and
        # report the shedding so the rung eventually follows.
        try:
            _kind = _KIND.unpack_from(frame, 0)[0]
        except Exception:
            _kind = KIND_VIDEO          # unreadable: treat as sheddable
        dead = []
        for c, who in targets:
            # [AIM_IS_COUNTED_ONCE_V1] One pass of this loop = one frame AIMED at
            # this viewer. Counted HERE, before any branch can skip it.
            #
            # It used to be incremented in two of the four exits. The one it was
            # missing from is the one that fires most: a capped viewer that is
            # unanchored drops every inter frame at the `continue` below, and
            # that is the STEADY STATE once shedding starts. So _fan_n counted
            # only the handful of frames that reached the later branches,
            # _deliv_n counted the ones that went, and the delivered ratio sat
            # near 1.0 while most of the stream was being thrown away.
            #
            # Consequence, measured 2026-08-08: the ratio never fell below
            # BP_STARVED, no KIND_BACKPRESSURE was ever sent, the sender's
            # _kf_backlog stayed 0, _ladder_from_backlog never walked the rung
            # down, and a headless New York transmitter held L7/720p while its
            # viewer received a few frames a second. That is exactly the
            # slideshow-at-full-resolution [AUDIO_FIRST_V1] says must not happen:
            # the shedding half was implemented, the reporting half had a hole,
            # so the rung could never follow.
            #
            # The rule was already written, one branch further down: "Aimed at,
            # not delivered. A frame the CAP dropped counts against delivery
            # exactly like one the socket refused -- from the viewer's seat they
            # are the same missing picture." This makes every path obey it by
            # construction instead of by remembering.
            # [AUDIO_IS_ACCOUNTED_SEPARATELY_V1] into the dict for THIS plane.
            # _fan_n/_deliv_n feed the video backpressure ratio and are reset on
            # its window; audio must not be counted there or it is both wrong
            # for video and destroyed before anyone reads it.
            if _pl == PLANE_VIDEO:
                self._fan_n[who] = self._fan_n.get(who, 0) + 1
            else:
                self._a_fan_n[who] = self._a_fan_n.get(who, 0) + 1
            # [MEDIASPEED_V1] artificial cap for THIS caller, before the write.
            # A capped leg sheds even when its socket has room: we are simulating
            # a slower link, not a full buffer.
            # [TWO_PLANES_V1] Only the video plane is budgeted. The audio plane
            # is never charged and never shed -- not by a branch that could be
            # got wrong, but because this code only ever runs for video targets.
            # [KEYFRAME_ANCHOR_V1] A capped viewer sheds INTER frames and holds
            # the newest keyframe until it can be sent.
            #
            # Shedding by size alone drops keyframes preferentially -- they are
            # the largest frames in the stream -- and every inter frame after a
            # lost keyframe references a picture the decoder never received.
            # That is full pixellation until the next keyframe, which is then
            # also the most likely to be shed. The viewer can stay broken
            # indefinitely on a link that is only mildly constrained.
            #
            # Instead: once shedding starts, this viewer is WAITING FOR A
            # KEYFRAME. Inter frames are dropped outright -- they are useless
            # without the reference. A keyframe becomes the frame to send; if
            # the budget will not take it yet it is HELD, and a newer keyframe
            # overwrites the held one, because the freshest picture is the only
            # one worth resuming from.
            if _pl == PLANE_VIDEO and self._caps:
                _key = video_is_key(frame)
                _pend = self._pending_key.get(who)
                _waiting = self._await_key.get(who, False)

                if _waiting and not _key:
                    if _pend is not None and not self._over_cap(who, 1):
                        # Any budget at all: the held keyframe goes NOW and
                        # overdraws. It is mandatory -- nothing after it decodes
                        # without it -- and inter frames pay the debt back.
                        try:
                            self._spend(who, (len(_pend) + 4) * 8)
                            _went = self._locked_send(c, _pend)
                        except AbortedFrame:
                            _went = False
                        except (BrokenPipeError, ConnectionResetError, OSError) as e:
                            dead.append(c)
                            print("[relay] send to %s failed: %s: %s -- dropping"
                                  % (who, type(e).__name__, e), flush=True)
                            continue
                        if _went:
                            # [DROP_THE_FRAME_NOT_THE_CALLER_V1] The anchor only
                            # clears if the keyframe ACTUALLY went. Clearing it on a
                            # shed frame told the viewer it was anchored to a picture
                            # that never arrived.
                            self._pending_key.pop(who, None)
                            self._await_key[who] = False
                            # [AIM_IS_COUNTED_ONCE_V1] A held keyframe that ACTUALLY
                            # went is a delivered picture and was counted nowhere,
                            # so a viewer being rescued read as a viewer receiving
                            # nothing.
                            self._deliv_n[who] = self._deliv_n.get(who, 0) + 1
                    self._capped_drops += 1
                    self._vdrop_of[who] = self._vdrop_of.get(who, 0) + 1
                    continue                    # inter frame while unanchored

                if self._over_cap(who, (len(frame) + 4) * 8):
                    # [DELIVERED_RATE_FEEDBACK_V1] Aimed at, not delivered. A frame
                    # the CAP dropped counts against delivery exactly like one the
                    # socket refused -- from the viewer's seat they are the same
                    # missing picture.
                    # [AIM_IS_COUNTED_ONCE_V1] The aim is counted at the top of the
                    # loop now; counting it again here would double this path.
                    if _key:
                        # Hold the newest keyframe; drop whatever we held before.
                        self._pending_key[who] = frame
                        self._await_key[who] = True
                    else:
                        self._await_key[who] = True
                    self._capped_drops += 1
                    self._vdrop_of[who] = self._vdrop_of.get(who, 0) + 1
                    continue

                if _key:
                    # It fits and it is a keyframe: the viewer is anchored again.
                    self._pending_key.pop(who, None)
                    self._await_key[who] = False
            try:
                # [DROP_THE_FRAME_NOT_THE_CALLER_V1] False means the frame was shed
                # because the socket had no room. That is the ladder working, not a
                # peer failure, and it must NOT put this connection in `dead`.
                # [AIM_IS_COUNTED_ONCE_V1] Aim counted at the top of the loop.
                if self._locked_send(c, frame):
                    if _pl == PLANE_VIDEO:
                        self._deliv_n[who] = self._deliv_n.get(who, 0) + 1
                    else:
                        self._a_deliv_n[who] = self._a_deliv_n.get(who, 0) + 1
                else:
                    self._vdrop_of[who] = self._vdrop_of.get(who, 0) + 1
                    self._capped_drops += 1
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                # A genuine socket error. This one really is gone.
                dead.append(c)
                print(f"[relay] send to {who} failed: {type(e).__name__}: {e} -- dropping",
                      flush=True)
        # [DOWNLINK_BACKPRESSURE_V1] REMOVED -- it wrote to peer sockets from
        # inside _fan, which runs on EACH SENDER'S pump thread. In a two-party
        # call every peer is both sender and viewer, so one peer's socket was
        # being written by the other peer's pump thread (media) and by its own
        # (backpressure) at the same time, with no per-socket lock. The bytes
        # interleaved, the length prefix desynced, and the receiver decoded
        # garbage from then on: "the client says it is getting data, but nothing
        # is playing."
        #
        # The counters stay -- _capped_drops and _vdrop_of are still useful --
        # but nothing is transmitted. The sender's ladder no longer consumes
        # them either (see Part XV ch. 50): the transmitter sends its best and
        # the relay serves each client what that client's link allows, which for
        # now means whole-frame drops. Reinstating any relay-to-sender channel
        # needs a per-socket write lock first.

        # [DELIVERED_RATE_FEEDBACK_V1] Tell the SENDER how many of its viewers are
        # not keeping up, so its ladder can pick a rung the link can actually
        # deliver.
        #
        # The consumer for this has existed all along and never had a producer:
        # Call._ladder_from_backlog() acts on self._kf_backlog, which is set only by
        # the KIND_BACKPRESSURE receive handler, and NOTHING has ever sent that
        # frame. So a sender at 23.5 fps with a clean uplink held L7 while the far
        # end received 1.2 fps of 720p, and every screen it owned reported perfect
        # health. That is not a slow link the ladder failed to find -- it is a
        # measurement the sender was never given.
        #
        # DOWNLINK_BACKPRESSURE_V1 was withdrawn for writing to peer sockets from
        # _fan with no per-socket lock. [SOCKET_WRITE_LOCK_V1] fixed that, and
        # [DROP_THE_FRAME_NOT_THE_CALLER_V1] means a report that will not fit is
        # shed rather than fatal. The other objection stands and is unresolved: with
        # one relay stream, the slowest viewer sets the rung for everybody. That is
        # the honest trade until the relay can transcode per client -- and a call
        # where one participant sees nothing is worse than one where everybody sees
        # less.
        # [AUDIO_BACKPRESSURE_V1] The audio plane's own window. Separate from the
        # video one below because it must be able to fire when video is already
        # off: at L4 and under there is no video traffic at all, so a window
        # gated on the video plane would go silent exactly when audio is the only
        # thing left to protect.
        if _pl == PLANE_AUDIO and (time.time() - self._abp_window_t0) >= self.BP_WINDOW_S:
            with self.lock:
                _afanned = dict(self._a_fan_n)
                _adelivered = dict(self._a_deliv_n)
                self._a_fan_n = {}
                self._a_deliv_n = {}
                self._abp_window_t0 = time.time()
                _amine = [w for _c, w in targets]
            _astarved = 0
            for _w in _amine:
                _f = _afanned.get(_w, 0)
                if _f <= 0:
                    continue
                # Audio is the floor. It is not judged on BP_STARVED (0.5) like
                # video -- ANY audio loss to a viewer is a report, because by
                # doctrine video must be shut down COMPLETELY before a single
                # audio frame is shed. If audio is being lost, the rung is wrong
                # no matter how well video is doing.
                if _adelivered.get(_w, 0) < _f:
                    _astarved += 1
                    print("[relay] [AUDIO_BACKPRESSURE_V1] %s received %d/%d "
                          "audio frame(s) this window -- reporting to the sender"
                          % (_w, _adelivered.get(_w, 0), _f), flush=True)
            if _astarved:
                try:
                    self._locked_send(sender, pack_typed(
                        KIND_AUDIO_BACKPRESSURE, "", _LEN.pack(_astarved)))
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    print("[relay] audio backpressure report to %s failed: %s: %s"
                          % (self._name_of.get(sender, "?"), type(e).__name__, e),
                          flush=True)

        if _pl == PLANE_VIDEO and (time.time() - self._bp_window_t0) >= self.BP_WINDOW_S:
            with self.lock:
                _fanned = dict(self._fan_n)
                _delivered = dict(self._deliv_n)
                self._fan_n = {}
                self._deliv_n = {}
                self._bp_window_t0 = time.time()
                _mine = [w for _c, w in targets]
            _starved = 0
            for _w in _mine:
                _f = _fanned.get(_w, 0)
                if _f <= 0:
                    continue
                _ratio = _delivered.get(_w, 0) / float(_f)
                if _ratio < self.BP_STARVED:
                    _starved += 1
                    if RELAY_DIAG:
                        print("  [relay] %s received %d/%d frames (%.0f%%) this "
                              "window -- reporting to the sender"
                              % (_w, _delivered.get(_w, 0), _f, _ratio * 100),
                              flush=True)
            if _starved:
                try:
                    self._locked_send(sender, pack_typed(
                        KIND_BACKPRESSURE, "", _LEN.pack(_starved)))
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    print("[relay] backpressure report to %s failed: %s: %s"
                          % (self._name_of.get(sender, "?"), type(e).__name__, e),
                          flush=True)

        # [KEYFRAME_ON_REQUEST_V1] Ask the SENDER for a keyframe while any of its
        # viewers is unanchored. This runs on the sender's own pump thread and writes
        # to the sender's own socket through _locked_send, so the write is serialised
        # against the media _fan is putting on that same socket for other senders --
        # which is exactly what [DOWNLINK_BACKPRESSURE_V1] lacked when it desynced the
        # stream and was withdrawn.
        #
        # Only the video plane: the audio plane has no keyframes and no anchor.
        if _pl == PLANE_VIDEO:
            # [KEYREQ_NEEDS_ROOM_V1] Ask ONLY for a viewer who is unanchored and for
            # whom we are NOT already holding a keyframe.
            #
            # The first cut asked whenever anyone was unanchored, which under a
            # MediaSpeed cap is a permanent state and produced a storm: measured on
            # hardware, ~2 requests a second, the sender emitting 67-73 KB keyframes
            # roughly once a second into a capped link that could not carry them, so
            # the viewer never anchored, so the request repeated. Half the wire was
            # keyframes and the picture never came back. Asking for the LARGEST frame
            # there is, because a small one would not fit, is precisely backwards.
            #
            # A held _pending_key means the encoder has ALREADY given us a keyframe
            # for this viewer and it is waiting on BUDGET, not on the encoder. Another
            # one cannot help -- the newest simply overwrites the held one. So the
            # only viewer worth asking for is one who is unanchored with nothing held.
            _waiting_n = 0
            with self.lock:
                for _c, _who in targets:
                    if self._await_key.get(_who) and _who not in self._pending_key:
                        _waiting_n += 1
                _last = self._keyreq_at.get(sender, 0.0)
            if _waiting_n and (time.time() - _last) >= self.KEYREQ_MIN_S:
                with self.lock:
                    self._keyreq_at[sender] = time.time()
                try:
                    # A shed keyframe request is no loss: the backstop timer will
                    # come round again.
                    self._locked_send(sender, pack_typed(
                        KIND_KEYREQ, "", _LEN.pack(_waiting_n)))
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    # A sender we cannot talk to is not a reason to stop relaying
                    # its media; its own pump will notice the socket soon enough.
                    print("[relay] keyframe request to %s failed: %s: %s"
                          % (self._name_of.get(sender, "?"), type(e).__name__, e),
                          flush=True)

        if dead:
            with self.lock:
                for c in dead:
                    self.peers.pop(c, None)
                    self._keyreq_at.pop(c, None)
                    self._owed.pop(c, None)
                    # [SHUTDOWN_NOT_CLOSE_V1] `dead` came from a send that raised
                    # a genuine socket error, so this fd is finished. shutdown()
                    # first anyway: that peer's pump may still be in recv, and it
                    # must wake with EOF rather than find its descriptor gone.
                    try:
                        c.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    _close_socket(c, "dead-peer")


# -- video rung -> capture/encode parameters ----------------------------------
# Resolution + grayscale per the canonical ladder (L5 360p gray, L6 480p, L7 720p).
# [RATE_IS_MEASURED_AT_THIS_RUNG_V1] A rung is judged only after a full settling
# window AT that rung. Camera open and the encoder's first frames land inside the
# first second, so a shorter window measured 6 fps on a box that then held 24/24 for
# the next minute -- and shed a rung for it. The encoder is rebuilt on every rung
# change, so this applies at each one, not only at the start.
WARMUP_FRAMES = 15     # frames at this rung before its rate means anything
WARMUP_S = 2.0         # ... and this many seconds of it


# [STANDARD_SIZES_WIDESCREEN_V1] Every rung is a NAMED standard resolution, and
# where a size exists in both aspects the 16:9 one is the rung.
#
# The ladder had drifted into ad-hoc geometries: 320x200 is CGA, 16:10, and not
# a size anything shoots; 1024x768 is XGA, 4:3, and 15% FEWER pixels than 720p,
# so it sat above CHORUS while making the picture smaller. Mixed aspects also
# mean the frame changes SHAPE as the ladder walks, which reads as a glitch
# rather than as degradation.
#
# 640x480 gets no rung for the same reason: VGA is the 4:3 spelling of two rungs
# that already exist -- the height of 854x480 and the width of 640x360.
#
# The whole ladder in one aspect, top to bottom:
#   L8  1920x1080  1080p
#   L7  1280x720   720p
#   L6   854x480   480p
#   L5   640x360   360p   (grayscale: the bottom rung spends colour first)
# and below L5 the bottom walk continues 426x240 (240p), 256x144 (144p),
# 160x90 (the 16:9 floor -- 160x120 would be QQVGA and 4:3).
# [ASPECT_IS_CHOSEN_V1] Two geometry ladders, one cost ladder.
#
# The rung's COST -- its bitrate -- is the thing the ladder orders by and does
# not depend on the shape of the frame. The geometry does, so the shape is a
# separate table the operator selects, defaulting to widescreen.
#
# 5:4 note: 1280x1024 is SXGA and is 5:4, not 4:3. It is in the 4:3 family
# because that is the family it belongs to by intent and by every device that
# lists it, but the aspect check treats it as its own ratio rather than
# pretending it is 1.333.
#
# CAPTURE IS NOT CROPPED. VideoEncoder.encode resizes with a plain cv2.resize
# and no crop or pad, so a 16:9 sensor asked for a 4:3 frame is STRETCHED --
# 33% too tall at 4:3, 42% at 5:4. That is a real defect of choosing an aspect
# the camera does not shoot, and it is the operator's choice to make; the code
# does not silently letterbox it away. A crop/pad path is the fix and does not
# exist yet.
RUNG_GEO = {
    "16:9": {
        8: {"w": 1920, "h": 1080},     # 1080p
        7: {"w": 1280, "h": 720},      # 720p
        6: {"w": 854,  "h": 480},      # 480p
        5: {"w": 640,  "h": 360},      # 360p
    },
    "4:3": {
        8: {"w": 1600, "h": 1200},     # UXGA
        7: {"w": 1280, "h": 1024},     # SXGA (5:4)
        6: {"w": 1024, "h": 768},      # XGA
        5: {"w": 800,  "h": 600},      # SVGA
    },
}
DEFAULT_ASPECT = "16:9"


def geometry_ladder(aspect=None):
    """[SLOWEST_VIEWER_COMMANDS_V1] Every geometry, largest first.

    One list, used by BOTH ends: the receiver walks it to decide what it can
    actually take, and the sender applies whatever the receiver names. They must
    be the same list or a commanded size is one the producer cannot produce.
    """
    geo = RUNG_GEO.get(aspect) or RUNG_GEO[DEFAULT_ASPECT]
    out = [dict(geo[i]) for i in sorted(geo, reverse=True)]
    px5 = geo[min(geo)]["w"] * geo[min(geo)]["h"]
    out += [dict(g) for g in BOTTOM_4_3 if g["w"] * g["h"] < px5]
    return out

# [CONSTRAINED_GOES_4_3_V1] Below L5 the frame turns 4:3 and keeps shrinking.
#
# Widescreen is the right default for a room and the wrong one for a postage
# stamp: a talking head is taller than it is wide, so at the sizes where every
# pixel counts, 4:3 puts more of them on the face. 160x120 is 19,200 pixels
# against 160x90's 14,400 -- a third more, all of it where the face is.
#
# The shape changes once, on the way out of L5, and that is deliberate. Above
# L5 the aspect is whatever the operator chose and does not move.
#
# Descending. Entries at or above the running L5 geometry are skipped, so this
# one list serves both aspect ladders without a second table.
BOTTOM_4_3 = (
    {"w": 640, "h": 480, "gray": True, "bitrate": 400_000},   # VGA
    {"w": 480, "h": 360, "gray": True, "bitrate": 250_000},   # 4:3 360p
    {"w": 320, "h": 240, "gray": True, "bitrate": 150_000},   # QVGA
    {"w": 160, "h": 120, "gray": True, "bitrate": 60_000},    # QQVGA, the floor
)

# Cost per rung. Geometry comes from RUNG_GEO; this is what the ladder orders
# by and what the encoder is asked for.
RUNG_VIDEO = {
    8: {"gray": False, "bitrate": 3_000_000},
    7: {"gray": False, "bitrate": 1_200_000},
    6: {"gray": False, "bitrate": 600_000},
    5: {"gray": True,  "bitrate": 300_000},
}
for _a, _g in RUNG_GEO.items():
    for _i, _wh in _g.items():
        _wh.setdefault("gray", RUNG_VIDEO[_i]["gray"])
        _wh.setdefault("bitrate", RUNG_VIDEO[_i]["bitrate"])
# The default aspect's geometry is folded into RUNG_VIDEO so every existing
# reader -- level_for_link, the reporting lines, the measured model -- keeps
# working unchanged against a table that has w/h in it.
for _i, _wh in RUNG_GEO[DEFAULT_ASPECT].items():
    RUNG_VIDEO[_i].update({"w": _wh["w"], "h": _wh["h"]})


# codec ids carried on the wire so the decoder picks the matching decoder
# [AUDIO_IS_OPUS_V1] Audio on the wire is Opus. Always.
#
# It was raw PCM: AUDIO_RATE (16000) samples/s x 2 bytes mono = 32,000 B/s =
# 256,000 bps. A quarter of a megabit, fixed, unaffected by every rung of the
# ladder -- so at a 336 kbps MediaSpeed cap audio alone consumed 76% of the wire
# and cap_to_link's 60% "video share" was arithmetic on a budget that did not
# exist. Measured 2026-08-08: 11 of 111 frames delivered, video correctly shed
# to L4, and it changed nothing, because video was never the bulk.
#
# Opus at 16 kHz mono voice is 16-24 kbps. Eight to sixteen times less. Audio
# stops being the thing that breaks the ladder.
#
# NOT NEGOTIATED, NOT PROBED-WITH-A-FALLBACK. Opus is platform, like lz4 and
# mysql: if it will not import, this machine is not configured and the client
# must not start. A node that cannot do Opus is not running FrogNet. Exactly the
# treatment [NO_FALLBACK_V1] gave every other first-party dependency.
#
# ffmpeg is already linked in via PyAV -- the H.264 path imports `av` -- and
# ffmpeg carries libopus, so there is no new dependency here, only a refusal to
# guard the one that is already required.
AUDIO_OPUS_BITRATE = int(os.environ.get("FROGNET_OPUS_BITRATE", "24000"))


class OpusCodec:
    """[AUDIO_IS_OPUS_V1] AUDIO_RATE mono int16 <-> Opus. One per direction.

    Not guarded. `import av` and Codec('libopus') are unconditional: a machine
    without them is not configured, and the traceback names exactly what is
    missing. There is no PCM fallback here -- uncompressed is an OPERATOR
    DECISION (--pcm-audio), taken deliberately and warned about, never something
    the code slides into because an import failed.

    THE DECODER RESAMPLES. libopus always decodes at 48 kHz internally
    regardless of the sample_rate asked for, so a decoded frame is 3x the
    samples at AUDIO_RATE=16000. Feeding that straight to the mixer plays every
    voice at a third speed -- caught in a bench round-trip: 6400 bytes in,
    19200 out. The AudioResampler is not optional dressing, it is the thing that
    makes the output the same audio that went in.
    """

    def __init__(self, bitrate=None):
        import av
        import numpy as np
        self._av, self._np = av, np
        self.enc = av.CodecContext.create("libopus", "w")
        self.enc.sample_rate = AUDIO_RATE
        self.enc.format = "s16"
        self.enc.layout = "mono"
        self.enc.bit_rate = int(bitrate or AUDIO_OPUS_BITRATE)
        self.dec = av.CodecContext.create("libopus", "r")
        self.dec.sample_rate = AUDIO_RATE
        self.dec.format = "s16"
        self.dec.layout = "mono"
        self._res = av.AudioResampler(format="s16", layout="mono",
                                      rate=AUDIO_RATE)

    def encode(self, pcm: bytes):
        """int16 mono at AUDIO_RATE -> list of Opus packets.

        [NO_NDARRAY_IN_THE_HOT_PATH_V1] This used from_ndarray(), and PyAV's
        ndarray conversions do a function-local `import numpy` -- measured on
        NY-1 2026-08-15 at ONE importlib._find_and_load per call. At 50 calls a
        second that is 50 filesystem stat()s a second under the global import
        lock, on the encode path, competing with the video encoder for the GIL.
        py-spy put ~10% of the whole process in import machinery.

        s16 mono is packed, so `pcm` is already exactly the plane layout:
        write it straight into the frame's buffer and no conversion happens.
        """
        fr = self._av.AudioFrame(format="s16", layout="mono",
                                 samples=len(pcm) // 2)
        fr.planes[0].update(pcm)
        fr.sample_rate = AUDIO_RATE
        fr.pts = None
        return [bytes(p) for p in self.enc.encode(fr)]

    def decode(self, pkt: bytes) -> bytes:
        """One Opus packet -> int16 mono at AUDIO_RATE."""
        # [NO_NDARRAY_IN_THE_HOT_PATH_V1] see encode(). Read the plane out
        # directly. PyAV pads planes to an alignment boundary, so bytes(plane)
        # is LONGER than the audio -- slice to samples*2 or the mixer is fed
        # trailing garbage on every packet. Short is a real failure, not a
        # short read to paper over: [NO_FALLBACK_V1].
        out = b""
        for fr in self.dec.decode(self._av.Packet(pkt)):
            for r in self._res.resample(fr):
                want = r.samples * 2
                buf = bytes(r.planes[0])
                if len(buf) < want:
                    raise RuntimeError(
                        "opus plane is %d bytes, %d samples needs %d"
                        % (len(buf), r.samples, want))
                out += buf[:want]
        _out = out
        A.live_tap("B_decoded", _out, AUDIO_RATE)   # [LIVE_TAP_V1]
        return _out
AUDIO_FMT_OPUS = 1
AUDIO_FMT_PCM = 0      # operator override only; see --pcm-audio

CODEC_VP8 = 0          # libvpx, software
CODEC_H264_SW = 1      # libx264, software
CODEC_H264_HW = 2      # h264_v4l2m2m, hardware (Pi VideoCore, etc.)

CODEC_ENCODER = {                       # id -> PyAV encoder name
    CODEC_VP8: "libvpx",
    CODEC_H264_SW: "libx264",
    CODEC_H264_HW: "h264_v4l2m2m",
}
CODEC_DECODER = {                       # id -> PyAV decoder name
    CODEC_VP8: "libvpx",
    CODEC_H264_SW: "h264",
    CODEC_H264_HW: "h264",              # any H.264 decoder handles HW-encoded H.264
}
CODEC_NAME = {CODEC_VP8: "VP8/software", CODEC_H264_SW: "H.264/software(libx264)",
              CODEC_H264_HW: "H.264/hardware(v4l2m2m)"}


class VideoEncoder:
    """In-process video encoder whose target follows the ladder rung. Codec is selectable:
    software VP8 (libvpx), software H.264 (libx264), or HARDWARE H.264 (h264_v4l2m2m -- the
    Pi's VideoCore and similar). Hardware encode offloads the CPU, the difference between
    ~24fps software and full-rate hardware on a Pi. Each codec configures differently."""
    def __init__(self, codec_id=CODEC_VP8):
        import av, fractions
        self._av = av; self._frac = fractions
        self.enc = None
        self.cfg = None
        self.treat = None
        self.fps = 24
        self.codec_id = codec_id
        self.bound_name = None          # the codec that actually bound (for reporting)

    @staticmethod
    def probe_best_codec(fps=24):
        """Pick the best encoder that ACTUALLY WORKS on this box, best-first:
          1. H.264 hardware (h264_v4l2m2m) -- offloads the CPU entirely (Pi 4 VideoCore etc.)
          2. H.264 software (libx264)      -- cheaper per-frame than libvpx on most CPUs
          3. VP8 software (libvpx)          -- universal fallback, always present
        Each candidate is tested by REAL-ENCODING a frame with its correct pixel format and
        options. CodecContext.create() succeeds even for a non-functional hardware encoder
        (the Pi 5 lesson), so only a real encode that produces bytes proves usability."""
        import av, fractions
        import numpy as np
        order = [CODEC_H264_HW, CODEC_H264_SW, CODEC_VP8]
        for cid in order:
            try:
                # pixel format + opts per codec (mirror _codec_config without an instance)
                if cid == CODEC_VP8:
                    pix_fmt, opts = "yuv420p", None
                elif cid == CODEC_H264_SW:
                    pix_fmt, opts = "yuv420p", {"preset": "ultrafast", "tune": "zerolatency"}
                else:  # CODEC_H264_HW
                    pix_fmt, opts = "nv12", None
                enc = av.CodecContext.create(CODEC_ENCODER[cid], "w")
                enc.width = 640; enc.height = 480; enc.pix_fmt = pix_fmt
                enc.bit_rate = 400_000; enc.time_base = fractions.Fraction(1, fps)
                if opts:
                    try: enc.options = opts
                    except Exception: pass
                fr = av.VideoFrame.from_ndarray(
                    np.zeros((480, 640, 3), dtype=np.uint8), format="bgr24").reformat(format=pix_fmt)
                fr.pts = 0
                produced = b"".join(bytes(p) for p in enc.encode(fr))
                produced += b"".join(bytes(p) for p in enc.encode(None))
                if not produced:
                    raise RuntimeError("no output")
                print(f"  [CODEC] probe selected {CODEC_NAME[cid]} (best available here)", flush=True)
                return cid
            except Exception as e:
                print(f"  [CODEC] probe: {CODEC_NAME[cid]} unusable ({type(e).__name__}); trying next",
                      flush=True)
        return CODEC_VP8        # should never reach -- VP8 is always present

    def set_codec(self, codec_id):
        """Switch the live codec. Actually TEST-ENCODES a frame with the candidate using THAT
        codec's correct pixel format and options (create() alone lies). A codec that fails is
        remembered and never re-attempted, so a refused switch is logged ONCE."""
        if codec_id == self.codec_id:
            return self.codec_id
        if not hasattr(self, "_failed_codecs"):
            self._failed_codecs = set()
        if codec_id in self._failed_codecs:
            return self.codec_id                 # known-bad here: silently stay put
        try:
            import numpy as np
            # use the candidate codec's real config (nv12 + minimal opts for hardware, etc.)
            saved = self.codec_id; self.codec_id = codec_id
            # [VBV_OR_THE_BUDGET_IS_A_WISH_V1] the probe encodes at 400k below,
            # so it asks for the config at 400k -- a bind test that omits the
            # rate-control options is not testing the config that will run.
            pix_fmt, opts = self._codec_config(bitrate=400_000)
            self.codec_id = saved
            test = self._av.CodecContext.create(CODEC_ENCODER[codec_id], "w")
            test.width = 640; test.height = 480; test.pix_fmt = pix_fmt
            test.bit_rate = 400_000; test.time_base = self._frac.Fraction(1, self.fps)
            if opts:
                try: test.options = opts
                except Exception: pass
            fr = self._av.VideoFrame.from_ndarray(
                np.zeros((480, 640, 3), dtype=np.uint8), format="bgr24").reformat(format=pix_fmt)
            fr.pts = 0
            produced = b"".join(bytes(p) for p in test.encode(fr))
            produced += b"".join(bytes(p) for p in test.encode(None))
            if not produced:
                raise RuntimeError("codec present but produced no output")
        except Exception as e:
            self._failed_codecs.add(codec_id)    # never ask again this session
            print(f"  [CODEC] {CODEC_NAME[codec_id]} not usable on this box "
                  f"({type(e).__name__}: {e}); staying on {CODEC_NAME[self.codec_id]}", flush=True)
            return self.codec_id
        self.codec_id = codec_id
        self.cfg = None
        print(f"  [CODEC] switched to {CODEC_NAME[codec_id]}", flush=True)
        return codec_id

    def _codec_config(self, bitrate=None):
        """Per-codec settings. Critically, the HARDWARE H.264 encoder (v4l2m2m) does NOT accept
        libx264 options (preset/tune/bf) and typically wants nv12, not yuv420p -- handing it the
        x264 config is a likely avcodec_open2 error-22 cause. Each codec gets what it actually
        accepts. Returns (pix_fmt, options_dict).

        [VBV_OR_THE_BUDGET_IS_A_WISH_V1] bit_rate alone is an AVERAGE with no
        window. x264 at preset=ultrafast tune=zerolatency and libvpx in realtime
        will overshoot it freely over any interval a person or a ladder would
        notice, and converge only over the whole stream.

        Measured 2026-08-10 on one call: 320x240 asked for 150 kbps and produced
        140-155, on the nose -- but 160x120 asked for 60 kbps and produced 147,
        2.5x over, the SAME cost as the size above it. So the last shrink step
        bought nothing at exactly the moment the link was worst, and every rung
        target below about 150 kbps was decoration.

        maxrate + bufsize is the VBV that makes the number mean something.
        bufsize is half the rate -- half a second of buffer -- because this is a
        conversation: a big buffer smooths bitrate by adding latency, which is
        the wrong trade here. crf/qmax are left alone; the constraint is the
        rate, and the encoder may spend quality to meet it. That is the point.
        """
        _br = int(bitrate or 0)
        _max = str(_br) if _br > 0 else None
        _buf = str(max(1, _br // 2)) if _br > 0 else None
        if self.codec_id == CODEC_VP8:
            # [KEYFRAME_INTERVAL_IS_BOUNDED_V1] `g` was set for BOTH H.264 paths and
            # not for VP8 -- the codec this program actually runs by default. With no
            # `g`, gop_size stays -1 and libvpx picks its own keyframe distance.
            # Measured on real libvpx with exactly these options: keyframes 128 frames
            # apart, 5.3 s at 24 fps, and that was on random noise, which forces them
            # more often than real video. A viewer that sheds one keyframe stays
            # pixellated for that whole interval. Bound it to the same fps*2 the H.264
            # paths already use so the WORST case is two seconds, then
            # [KEYFRAME_ON_REQUEST_V1] takes the typical case down to one frame.
            _o = {"deadline": "realtime", "cpu-used": "8",
                  "lag-in-frames": "0", "g": str(self.fps * 2)}
            if _max:
                # libvpx names them the same as x264 through libav.
                _o.update({"maxrate": _max, "bufsize": _buf})
            return "yuv420p", _o
        if self.codec_id == CODEC_H264_SW:
            _o = {"tune": "zerolatency", "preset": "ultrafast",
                  "g": str(self.fps * 2), "bf": "0"}
            if _max:
                _o.update({"maxrate": _max, "bufsize": _buf})
            return "yuv420p", _o
        # CODEC_H264_HW (v4l2m2m): hardware wants nv12, minimal/no x264 opts.
        # The hardware encoder does its own rate control and rejects most
        # options; bit_rate is all it is given, as before.
        return "nv12", {"g": str(self.fps * 2)}

    # [GEOMETRY_IS_NOT_A_RUNG_V1] The fields of a treatment bag that reach the
    # codec. The bag is dynamic; anything else rides along on self.treat.
    APPLIED = ("w", "h", "bitrate", "gray", "fps")

    def _ensure(self, treat):
        """Build/rebuild the codec context to match `treat`.

        Keyed on the CONTENTS of the treatment, not on a rung index. The rung is
        a label on the wire; what the encoder runs is whatever geometry it was
        asked for -- including geometries no rung has, which is what makes a
        measured floor possible. There is no lowest entry to be stopped by.
        """
        key = tuple(treat.get(k) for k in self.APPLIED) + (self.codec_id,)
        self.treat = dict(treat)
        if self.cfg == key:
            return
        p = treat
        _missing = [k for k in ("w", "h", "bitrate") if p.get(k) is None]
        if _missing:
            raise KeyError("treatment reached the encoder without %s: %r"
                           % (", ".join(_missing), p))
        if p.get("fps"):
            self.fps = int(p["fps"])
        pix_fmt, opts = self._codec_config(bitrate=p.get("bitrate"))
        enc = self._av.CodecContext.create(CODEC_ENCODER[self.codec_id], "w")
        enc.width = int(p["w"]); enc.height = int(p["h"]); enc.pix_fmt = pix_fmt
        enc.bit_rate = int(p["bitrate"])
        enc.time_base = self._frac.Fraction(1, self.fps)
        if opts:
            try: enc.options = opts
            except Exception: pass
        self.enc = enc
        self.enc_pix_fmt = pix_fmt           # the frame must be reformatted to THIS
        self.cfg = key
        self.bound_name = CODEC_NAME[self.codec_id]
        self._seq = 0

    def encode(self, bgr, treat, force_key=False):
        """Encode one BGR ndarray at the rung's settings -> (bytes, is_key). If the active
        codec can't actually encode here (e.g. h264hw with no hardware -> error 22), fall
        back to VP8 and retry once, keeping that frame so a keyframe reaches the decoder.
        Returns (b'', False) only if even VP8 fails.

        [KEYFRAME_ON_REQUEST_V1] force_key marks THIS frame as an I-frame, so a viewer
        the relay reports as unanchored is rescued on the next frame instead of at the
        encoder's own convenience. Verified against real libvpx: forcing at frame 20
        produced keyframes at [0, 20]. Setting pict_type is advisory to the encoder --
        the caller must read the RETURNED is_key rather than assume it worked."""
        import numpy as np
        for attempt in range(2):
            self._ensure(treat)
            if self.enc is None:
                return b"", False
            p = treat
            try:
                src = bgr
                if (src.shape[1], src.shape[0]) != (int(p["w"]), int(p["h"])):
                    import cv2; src = cv2.resize(src, (int(p["w"]), int(p["h"])))
                # [SMALLEST_WIRE_WINS_V1] Gray stays at the bottom rungs.
                #
                # The deciding factor is bytes on the wire; everything else
                # ranks after it. Flat chroma planes cost a handful of bits per
                # macroblock, so gray is smaller at the same geometry, and that
                # settles it.
                #
                # It was removed for one revision on two arguments that are both
                # "other considerations": the two full-frame conversions per
                # frame (BGR2GRAY then GRAY2BGR, since the encoder takes yuv420p
                # either way), and a suspicion that it was behind the tile which
                # stopped repainting at 320x240 and 160x120. CPU is second to
                # wire bytes, and a suspected defect is something to FIND -- see
                # [TILE_TAKES_WHAT_IT_IS_GIVEN_V1] in communicator_live, which
                # removes the renderer's assumption about channel count so gray
                # cannot be the cause of a silent blank tile.
                #
                # NOT MEASURED. "Gray is smaller" is a claim about H.264 chroma
                # coding, not a number taken off this wire. RungModel records
                # key_kb and delta_kb per geometry and would settle it with an
                # A/B at one size.
                if p.get("gray"):
                    import cv2
                    g = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
                    src = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
                frame = self._av.VideoFrame.from_ndarray(src, format="bgr24").reformat(
                    format=getattr(self, "enc_pix_fmt", "yuv420p"))
                # [ENCODED_SIZE_IS_MEASURED_V1] What the encoder was actually
                # handed, not what the rung table says it should have been.
                #
                # The [VID-TX] line printed RUNG_VIDEO[send_l]['w']x['h'] -- the
                # TABLE entry for the rung. If the resize above silently produced
                # something else, or _ensure() built the encoder at a stale rung,
                # the line reads 1280x720 either way and the only symptom is a
                # bitrate lower than expected. Reporting intent as if it were
                # fact is the same defect as rx_dims and the ceiling table.
                self.last_encoded_wh = (frame.width, frame.height)
                frame.pts = self._seq; self._seq += 1
                if force_key:
                    # [KEYFRAME_ON_REQUEST_V1] Advisory. Wrapped because a codec that
                    # does not honour pict_type must not take the frame down with it:
                    # the frame still encodes, just not as a keyframe, and the caller
                    # learns that from is_key.
                    try:
                        frame.pict_type = self._av.video.frame.PictureType.I
                    except Exception as _e:
                        print(f"  [CODEC] {CODEC_NAME[self.codec_id]} would not accept "
                              f"a forced keyframe ({type(_e).__name__}) -- waiting for "
                              f"the interval instead", flush=True)
                out = b""; is_key = False
                for pkt in self.enc.encode(frame):
                    out += bytes(pkt)
                    if getattr(pkt, "is_keyframe", False):
                        is_key = True
                return out, is_key
            except Exception as e:
                if self.codec_id != CODEC_VP8:
                    print(f"  [CODEC] {CODEC_NAME[self.codec_id]} can't encode at "
                          f"{p['w']}x{p['h']} ({type(e).__name__}: {e}) -- falling back to VP8", flush=True)
                    self.codec_id = CODEC_VP8; self.cfg = None      # rebuild as VP8, retry
                    continue
                print(f"  [CODEC] VP8 encode failed: {type(e).__name__}: {e}", flush=True)
                return b"", False
        return b"", False


class VideoDecoder:
    """Per-(source,codec) decoder -> latest BGR ndarray for that source's tile. The codec id
    is read from each frame, so a peer running VP8 and a peer running H.264 both decode
    correctly, and a peer that switches codecs mid-call is handled."""
    def __init__(self):
        import av
        self._av = av
        self.dec = {}            # (src, codec_id) -> CodecContext
        self.latest = {}         # src -> bgr ndarray (latest decoded)
        # [STALE_TILE_V1] When the far end drops to an audio-only rung its video
        # simply stops. Nothing clears latest[], so the tile kept painting the
        # last decoded picture and the call looked FROZEN rather than audio-only.
        # Record when each source last produced a frame so a caller can tell the
        # difference between "a current picture" and "the last one we ever got".
        self.latest_at = {}      # src -> monotonic time of that frame
        self._diag = {}          # (src,codec) -> counters, see [VDEC_DIAG_V1]
        self.lock = threading.Lock()

    def frame_age(self, src):
        """[STALE_TILE_V1] Seconds since this source last decoded a frame, or
        None if it never has. A caller showing a tile must check this: an image
        in latest[] says nothing about whether it is still arriving."""
        t = self.latest_at.get(src)
        return None if t is None else (time.time() - t)

    def feed(self, src, codec_id, data: bytes):
        import numpy as np
        key = (src, codec_id)
        d = self.dec.get(key)
        if d is None:
            dec_name = CODEC_DECODER.get(codec_id, "libvpx")
            d = self._av.CodecContext.create(dec_name, "r")
            self.dec[key] = d
            # [VDEC_DIAG_V1] libav's own logging is OFF by default, so ffmpeg's
            # "concealing N DC errors", "corrupt macroblock", "no frame!" messages
            # -- which say EXACTLY what is wrong with a picture -- never reach the
            # console. A half-green tile is ffmpeg concealing missing slices; it
            # reports that and we were discarding it.
            if VDEC_DIAG:
                try:
                    self._av.logging.set_level(self._av.logging.WARNING)
                except Exception:
                    pass
        st = self._diag.setdefault(key, {"pkts": 0, "frames": 0, "empty": 0,
                                         "err": 0, "bytes": 0, "last": 0.0})
        st["pkts"] += 1
        st["bytes"] += len(data)
        try:
            pkt = self._av.Packet(data)
            got = 0
            for fr in d.decode(pkt):
                got += 1
                img = fr.to_ndarray(format="bgr24")
                with self.lock:
                    self.latest[src] = img
                    self.latest_at[src] = time.time()
            st["frames"] += got
            if got == 0:
                # A packet the decoder accepted but produced nothing from. Normal
                # for the first packets of a stream; sustained means the decoder is
                # never getting a keyframe or the packets are not whole.
                st["empty"] += 1
        except Exception as e:
            # [VDEC_DIAG_V1] Was `pass`. A decode that fails is the single most
            # informative event in the whole video path and it was invisible.
            st["err"] += 1
            if VDEC_DIAG:
                print("  [VDEC] src=%s codec=%s decode FAILED pkt=%d bytes=%d "
                      "exc=%s: %s" % (src, codec_id, st["pkts"], len(data),
                                      type(e).__name__, e), flush=True)
        if VDEC_DIAG:
            now = time.time()
            if now - st["last"] >= 1.0:
                st["last"] = now
                print("  [VDEC] src=%s codec=%s pkts=%d frames=%d empty=%d err=%d "
                      "avg_pkt=%dB" % (src, codec_id, st["pkts"], st["frames"],
                                       st["empty"], st["err"],
                                       st["bytes"] // max(1, st["pkts"])),
                      flush=True)

    def tiles(self):
        with self.lock:
            return dict(self.latest)


# -- bearer telemetry -> rung pressure ----------------------------------------
# [BEARER_DIAG_OPT_IN_V1] Ladder decision tracing. OFF by default: it fires once
# per VID-TX report and doubles the console volume of a healthy call. Set
# FROGNET_BEARER_DIAG=1, or pass --verbose (communicator_live sets it).
BEARER_DIAG = bool(os.environ.get("FROGNET_BEARER_DIAG"))

# [VDEC_DIAG_V1] Decode-path tracing: per-second packet/frame/error counts, every
# decode exception, and libav's own warnings. OFF by default.
# FROGNET_VDEC_DIAG=1 turns it on.
VDEC_DIAG = bool(os.environ.get("FROGNET_VDEC_DIAG"))

# [KEYFRAME_ON_REQUEST_V1] One line per honoured keyframe request. Off by default;
# FROGNET_KEYREQ_DIAG=1 or --verbose turns it on. A request that is never honoured
# is invisible without this, and "the picture took ages to come back" is exactly
# the complaint it explains.
KEYREQ_DIAG = bool(os.environ.get("FROGNET_KEYREQ_DIAG"))

# [DROP_THE_FRAME_NOT_THE_CALLER_V1] Relay shed/partial-write tracing. Off by
# default; FROGNET_RELAY_DIAG=1 turns it on. A socket that is chronically in debt is
# a leg that cannot carry its rung, and that is worth being able to see.
RELAY_DIAG = bool(os.environ.get("FROGNET_RELAY_DIAG"))


class Bearer:
    """Drives the bearer index from telemetry. Starts at the ceiling and floats.

    [LADDER_RATE_V2] John's rule, 2026-08-03.

    DOWNGRADE IMMEDIATELY on any of:
        - more than BAD_READS consecutive samples with drops
        - a one-second window carrying more than SEC_DROPS drops
        - audio shed by the wire (see below)

    UPGRADE only when BOTH:
        - at least CLEAN_SECS whole seconds have passed with ZERO drops
        - the sender is making the FULL frame rate at the current rung

    The second upgrade condition is the one that matters. A rung that cannot make
    full rate is not a rung to climb off, and clean seconds alone do not prove the
    box is keeping up -- they only prove the wire is not refusing anything. A box
    producing 13 fps sheds nothing on a fast LAN and would otherwise read "clean"
    and climb into a rung it has no chance of holding.

    WHY BOTH A RUN COUNT AND A PER-SECOND RATE
    sample() is called ONCE PER FRAME by the video sender -- about 23 times a
    second -- and `dropped` comes from take_sheds(), which drains the counter. Ten
    drops in a second therefore arrive as ten dirty samples scattered among
    thirteen clean ones, and a pure consecutive-run rule finds runs of BOTH inside
    a single second. Measured: sustained 10-13 drops/s with the run rule alone
    oscillated L6/L7 every few frames and never walked down. The one-second window
    is what makes a RATE visible to a per-frame sampler; the run count is what
    catches a short vicious burst before the second is up. Neither alone is enough.

    Clean seconds are counted in WHOLE SECONDS, not samples, for the same reason.
    """

    BAD_READS  = 3       # MORE than this many consecutive dirty samples -> down
    FPS_DOWN   = 10.0    # absolute floor: below this rate is not a video rung
    FPS_DOWN_FRAC = 0.75 # ... and neither is below this FRACTION of the target
    FPS_DOWN_READS = 3   # ... for MORE than this many consecutive samples -> down
    SEC_DROPS  = 5       # MORE than this many drops in one second -> down
    CLEAN_SECS = 3       # whole seconds at zero drops before an upgrade is considered
    # [PROBE_BACKOFF_V1] A rung that just dropped you is not as good a bet as one
    # you have never tried. Climbing back is a PROBE: if it fails, that rung is
    # held off longer each time, so a link that cannot sustain L6 stops being
    # re-tested every five seconds. Observed: L5 -> L6 -> L5 on a ~5s period for
    # an entire call, because three clean seconds is cheap and the controller had
    # no memory of having just failed there.
    HOLD_BASE_S  = 15.0   # first hold-off after a rung fails
    HOLD_MAX_S   = 300.0  # ceiling on the doubling
    HOLD_CLEAR_S = 60.0   # clean this long AT a rung and its record is forgiven
    # "full frame rate" with a tolerance for measurement noise: the meter reads
    # 23.9/24 on a box that is not missing frames. Tighten to 1.0 for a strict read.
    FULL_RATE_FRAC = 0.98

    def __init__(self, ceiling_idx, floor=None):
        self.idx = ceiling_idx
        self.ceiling = ceiling_idx
        # Protected floor: congestion steps down to the audio rung and STOPS.
        self.floor = L.MIN_IDX if floor is None else int(floor)
        self._bad_reads = 0          # consecutive samples carrying drops
        self._slow_reads = 0         # consecutive samples below FPS_DOWN
        self._sec_drops = 0          # drops accumulated in the current second
        self._sec_fps_min = None     # WORST rate seen in the current second
        self._sec_t0 = time.time()
        self._clean_secs = 0         # consecutive WHOLE seconds at zero drops
        self._fails = {}             # [PROBE_BACKOFF_V1] rung -> consecutive failures
        self._blocked = {}           # rung -> time before which not to retry
        self._held_since = time.time()
        self.last_reason = ""
        # [BEARER-DIAG] every input the last decision looked at, for the log line.
        # Log all of it, not the field currently suspected: an upgrade that fired
        # after one clean read has to be explainable from the line alone.
        self.diag = ""

    def sample(self, backlog, dropped, fps_sent=None, fps_target=None,
               frame_age_s=None, audio_shed=0):
        """Called once per frame by the video sender.

        frame_age_s is accepted and ignored -- it belonged to the reverted stale-frame
        rule. The signature is kept so the call site does not have to change.
        """
        now = time.time()
        dirty = (dropped > 0) or (backlog >= 8)

        # consecutive dirty samples
        self._bad_reads = self._bad_reads + 1 if dirty else 0

        # consecutive samples below the video floor rate. A rung that cannot make
        # ten frames a second is not a video rung whatever was asked for, and that
        # is true whether or not the wire refused anything -- a box producing 8 fps
        # sheds nothing on a fast LAN, so the drop signals never fire and it would
        # sit there rendering a slideshow. Counted consecutively, like the drop
        # rule, so one noisy measurement cannot cascade.
        # The absolute floor alone left a dead band: a box making 12/24 is above
        # FPS_DOWN so nothing steps down, and far below FULL_RATE_FRAC so nothing
        # steps up. It sat at L7 at half rate indefinitely. The down test is now
        # relative like the up test, with the absolute floor kept as a backstop for
        # low targets.
        _fps_floor = self.FPS_DOWN
        if fps_target:
            _fps_floor = max(_fps_floor, float(fps_target) * self.FPS_DOWN_FRAC)
        if fps_sent is not None and fps_sent < _fps_floor:
            self._slow_reads += 1
        else:
            self._slow_reads = 0

        # one-second window: this is what turns per-frame samples into a RATE
        self._sec_drops += int(dropped or 0)
        if fps_sent is not None:
            self._sec_fps_min = (fps_sent if self._sec_fps_min is None
                                 else min(self._sec_fps_min, fps_sent))
        sec_closed = None
        sec_full_rate = None
        if now - self._sec_t0 >= 1.0:
            sec_closed = self._sec_drops
            # A second counts toward the upgrade streak only if it was clean AND
            # ran at full rate THROUGHOUT. Zero drops alone is not health: a box
            # producing 13 fps sheds nothing on a fast LAN, so clean seconds would
            # accumulate while it fails the rung it is already on, and the third
            # one would promote it into a heavier rung it has no chance of holding.
            # Going back up is harder than coming down, and this is where that
            # asymmetry lives.
            sec_full_rate = True
            if self._sec_fps_min is not None and fps_target:
                sec_full_rate = (self._sec_fps_min
                                 >= float(fps_target) * self.FULL_RATE_FRAC)
            if sec_closed == 0 and sec_full_rate:
                self._clean_secs += 1
            else:
                self._clean_secs = 0
            self._sec_drops = 0
            self._sec_fps_min = None
            self._sec_t0 = now

        # ---- DOWN: immediate, on any of the three signals --------------------
        reason = ""
        if audio_shed:
            # The wire could not carry the protected floor even after retries.
            reason = "audio shed (%d)" % audio_shed
        elif self._bad_reads > self.BAD_READS:
            reason = "%d consecutive reads with drops" % self._bad_reads
        elif sec_closed is not None and sec_closed > self.SEC_DROPS:
            reason = "%d drops in one second" % sec_closed
        elif self._slow_reads > self.FPS_DOWN_READS:
            reason = "below %.1f fps (%.1f) for %d reads" % (
                _fps_floor, fps_sent, self._slow_reads)

        if reason:
            # [PROBE_BACKOFF_V1] This rung just failed: record it and hold it off,
            # doubling on each consecutive failure. Forgiven by HOLD_CLEAR_S of
            # clean running, so a transient burst cannot cap the link forever.
            _f = self._fails.get(self.idx, 0) + 1
            self._fails[self.idx] = _f
            self._blocked[self.idx] = now + min(
                self.HOLD_MAX_S, self.HOLD_BASE_S * (2 ** (_f - 1)))
            self.last_reason = reason
            self._bad_reads = 0
            self._slow_reads = 0
            self._clean_secs = 0
            self._sec_drops = 0
            self._sec_fps_min = None
            if self.idx > self.floor:
                self.idx -= 1
                self._held_since = now
            self._set_diag(sec_closed, sec_full_rate, dropped, backlog,
                           audio_shed, fps_sent, fps_target)
            return self.idx

        # ---- UP: clean seconds AND full rate ---------------------------------
        # Every second in the streak was already required to be clean AND at full
        # rate, so reaching CLEAN_SECS is the whole test -- no snapshot re-check.
        # Holding this rung cleanly for a good while means the link improved:
        # forgive its record so it can be probed normally again.
        if (self._clean_secs and (now - self._held_since) >= self.HOLD_CLEAR_S
                and self.idx in self._fails):
            self._fails.pop(self.idx, None)
            self._blocked.pop(self.idx, None)

        if self._clean_secs >= self.CLEAN_SECS and self.idx < self.ceiling:
            _target = self.idx + 1
            _until = self._blocked.get(_target, 0.0)
            if now < _until:
                self.last_reason = "holding L%d for %.0fs more (%d failed probes)" % (
                    _target, _until - now, self._fails.get(_target, 0))
            else:
                self.idx = _target
                self._held_since = now
                self._clean_secs = 0
                self.last_reason = ""
        elif sec_closed is not None and sec_closed == 0 and sec_full_rate is False:
            # Clean wire, short rate: the rung is being met by the WIRE but not by
            # the BOX. Not a downgrade -- nothing was refused -- but it does not
            # earn a step up either.
            self.last_reason = "holding: %.1f/%.0f fps" % (
                self._sec_fps_min or fps_sent or 0.0, float(fps_target or 0))
        self._set_diag(sec_closed, sec_full_rate, dropped, backlog,
                       audio_shed, fps_sent, fps_target)
        return self.idx

    def _set_diag(self, sec_closed, sec_full_rate, dropped, backlog,
                  audio_shed, fps_sent, fps_target):
        """[BEARER-DIAG] Every input the decision looked at. Set before BOTH
        returns -- a downgrade is the decision most worth explaining."""
        self.diag = ("badreads=%d slowreads=%d secdrops=%d cleansecs=%d sec_closed=%s "
                     "sec_full_rate=%s secfpsmin=%s dropped=%s backlog=%s "
                     "audio_shed=%s fps=%s/%s idx=%d floor=%d ceil=%d "
                     "reason=%r" % (
                         self._bad_reads, self._slow_reads, self._sec_drops,
                         self._clean_secs, sec_closed, sec_full_rate,
                         ("%.1f" % self._sec_fps_min)
                         if self._sec_fps_min is not None else None,
                         dropped, backlog, audio_shed,
                         ("%.1f" % fps_sent) if fps_sent is not None else None,
                         fps_target, self.idx, self.floor, self.ceiling,
                         self.last_reason))


class Call:
    # [PRODUCER_LEADS_CONSUMERS_REPORT_V1] Class defaults, so a Call built with
    # __new__ -- which every oracle does -- can exercise the treatment and
    # report paths without constructing a whole call. As instance-only
    # attributes these made three oracles crash with no verdict at all.
    is_producer = False
    _good_windows = 0
    _bad_windows = 0
    _in_hist = None
    _cap_reads = 0
    _cap_bad = 0
    _last_happy = None
    _reported_at = 0.0
    _producer_says = None
    _serve_cap_geo = None
    _step_at = 0.0
    _cap_said = None
    _cp_take = None
    _acted_on = None
    _promoted_at = 0.0
    _settled_at = 0.0
    _uplink_step_at = 0.0
    _rate_changed_at = 0.0
    _cap_rate = None
    _cap_want = 0.0
    _cap_at = 0.0
    _said_mic = False
    _said_source_limit = False
    _shed_seen = 0
    _source_limit_n = 0
    _in_hold_episode = False
    _kf_walk_at = 0.0
    _said_blocked = False
    _said_gate = False
    _seen_senders = None
    _state_seen = 0
    _state_thin = 0
    _said_tight = False
    UPLINK_TIGHT = 0.25    # [THE_SOCKET_KNOWS_BEFORE_THE_DROP_V1]
    """One A/V leg: owns the socket and the typed-frame demux. Audio uses the proven
    fnphone_pa DSP and PortAudio streams; video is an in-process VP8 capture/encode thread
    gated by the ladder. Audio and video are independent typed streams on one socket."""
    def __init__(self, host, port, name, in_dev=None, out_dev=None, cam=0,
                 no_video=False, no_audio=False, duck=True, fps=24, no_display=False,
                 codec_id=None, jitter_ms=120, pcm_audio=False, session=None, audio_rate=0,
                 aspect=None):
        self.host, self.port, self.name = host, port, name
        self.in_dev, self.out_dev, self.cam_idx = in_dev, out_dev, cam
        self.no_video, self.no_audio, self.duck = no_video, no_audio, duck
        self.no_display = no_display
        # [DOWNLINK_BACKPRESSURE_V1] video frames the RELAY shed for a capped
        # viewer, reported in band. Drained into the bearer's drop count.
        self._remote_drops = 0
        # [KEYFRAME_BACKLOG_V1] evidence from the relay
        self._wlog = None
        self._wlog_at = 0.0
        # [AUDIO_IS_OPUS_V1] Built unconditionally. If libopus is missing this
        # raises HERE, at construction, naming it -- rather than the call coming
        # up and being silently unintelligible.
        # [SENDER_READS_MEDIASPEED_V1] The call session this sender belongs to.
        # Without it there is no way to know WHICH viewers' MediaSpeed rows are
        # about us, so the poller does not start and the sender is uncapped --
        # which is the state that has been shipping.
        self.session = session
        # [RUNG_IS_MEASURED_V1] What each rung actually costs on this wire, and
        # what this wire has actually sustained and actually refused. All three
        # start empty: nothing is assumed, everything is learned.
        self._rungs = RungModel()
        self._sustained_bps = 0.0
        self._sustained_at = 0.0
        self._refused_bps = 0.0
        self._refused_at = 0.0
        self._kf_probe_lv = None
        # [VIDEO_FLOOR_IS_MEASURED_V1] The walk, and what it last measured.
        self._floor = None
        self._floor_geo = None
        # [ASPECT_IS_CHOSEN_V1] Geometry family above L5. Below L5 the walk is
        # always 4:3 -- see [CONSTRAINED_GOES_4_3_V1].
        self.aspect = aspect if aspect in RUNG_GEO else DEFAULT_ASPECT
        # [BOTTOM_RUNG_SHRINKS_V1] Where the bottom rung's geometry walk has
        # reached. 0 = the chosen family's L5 entry.
        self._bottom = 0
        self._bottom_at = 0.0
        self._bottom_ok = 0
        self._obs_seq = None
        self._obs_sheds = 0
        # [ASK_THE_DEVICE_NOT_THE_DEFAULT_V1] 0 = believe the device.
        self.audio_rate = int(audio_rate or 0)
        self._pcm_audio = bool(pcm_audio)
        self._opus = None if self._pcm_audio else OpusCodec()
        self._warned_pcm_rx = False
        # [RX_IS_PER_SOURCE_V1] src -> arriving counters, drained by the wire log.
        self._rx_src = {}
        self._rx_src_t0 = time.time()
        # [SLOWEST_VIEWER_COMMANDS_V1] cumulative inbound video frames per
        # source, never drained; the inbound watcher diffs it.
        self._in_src = {}
        self._in_seen = {}        # src -> (last count, last time)
        self._in_idx = {}         # src -> index into geometry_ladder()
        self._in_bad = {}         # src -> consecutive windows below the floor
        self._in_ok = {}          # src -> consecutive windows at rate
        # what WE have been told to serve, smallest across viewers
        self._cmd_geo = None
        # [ALONE_IS_NOT_SILENT_V1] has ANY frame ever arrived, and when we last
        # said it had not.
        self._rx_ever = False
        # [RECEIVE_RATE_IS_LINK_EVIDENCE_V1] what our own inbound rate says we
        # should be sending
        self._rx_cap_geo = None
        # [PRODUCER_LEADS_CONSUMERS_REPORT_V1] role and the network rate
        self.is_producer = False
        self._step_at = 0.0
        self._good_windows = 0
        self._last_happy = None
        self._reported_at = 0.0
        self._producer_says = None
        self._cap_said = None
        self._cap_rate = None       # [THE_MIC_IS_NOT_THE_LINK_V1]
        self._cap_want = 0.0
        self._cap_at = 0.0
        self._said_mic = False
        self._published_at = 0.0
        self._serve_cap_geo = None      # slowest consumer on the call
        self._last_send_l = None        # for [BACKING_DOWN_IS_BOTH_ENDS_V1]
        self._cp_take = None
        self._rx_none_at = 0.0
        # Two receive threads (audio plane, video plane) mutate _rx_src while the
        # wire log rebinds it. Without this the swap can drop a frame's count.
        self._rx_lock = threading.Lock()
        self._warned_fmt = {}
        if self._pcm_audio:
            print("[%s] *** UNCOMPRESSED AUDIO (--pcm-audio) ***\n"
                  "    256,000 bps of raw PCM, ~11x the wire cost of Opus and "
                  "FIXED at every rung of the ladder.\n"
                  "    On any MediaSpeed cap below ~550 kbps this alone exceeds "
                  "the video budget, video will shed to nothing,\n"
                  "    and it will not help -- because audio is the bulk. Use "
                  "this on a LAN or for codec debugging only."
                  % (self.name,), flush=True)
        self._kf_backlog = 0        # viewers with a keyframe held right now
        self._kf_backlog_at = 0.0   # when that report landed
        # [AUDIO_BACKPRESSURE_V1] viewers the relay could not get our AUDIO to.
        self._audio_backlog = 0
        self._audio_backlog_at = 0.0
        # [CEILING_IS_A_MINIMUM_V1] Two controllers constrain the video rung and
        # they are NOT in a race: the viewer's MediaSpeed cap, and the relay's
        # keyframe-backlog step. Both are UPPER BOUNDS, so the effective ceiling
        # is the lowest of them -- not whichever wrote _level_cap last.
        #
        # Writing one variable made them oscillate at the poll interval: the
        # backlog stepped to L6, the viewer poller saw the ceiling had moved and
        # re-asserted "no cap" back to L7, the backlog fired again. Observed
        # live, once every two seconds, indefinitely.
        #
        # None means "this controller has no opinion", which is different from
        # "this controller says unlimited" -- the distinction the single variable
        # could not express.
        self._cap_viewer = None      # level from viewer MediaSpeed
        self._cap_backlog = None     # level from keyframe backlog
        self._cap_local = None       # level from the LOCAL slider (cap_to_link)
        self._kf_clear_since = 0.0  # when the backlog last went to zero
        self._kf_probe_fails = 0    # [PROBE_BACKS_OFF_V1] consecutive failures
        # [KEYFRAME_ON_REQUEST_V1] Set when the relay asks for a keyframe, cleared
        # only when one has actually been ENCODED -- pict_type is advisory, so
        # clearing on request would drop the ask on any codec that ignored it.
        self._force_key = False
        self._force_key_for = 0
        # [TEARDOWN_IS_PER_SESSION_V1] Identifies THIS process's pair of planes.
        # Minted per Call, so a restart under the same --name is a different caller
        # as far as the relay's teardown is concerned.
        self.session_tag = uuid.uuid4().hex[:8].encode("ascii")
        self.fps = max(1, min(60, int(fps)))
        # codec_id None => auto-probe the best encoder this box can actually run (HW H.264 >
        # SW H.264 > VP8). An explicit codec_id forces that codec.
        if codec_id is None:
            codec_id = VideoEncoder.probe_best_codec(fps=self.fps)
        self.codec_id = codec_id
        self.sock = None
        self._stop = threading.Event()
        self._aseq = 0
        self._vseq = 0
        self.stats = WireStats()
        self.show_stats = True       # overlay on by default; toggle with 'e'
        # audio DSP (reused, proven)
        self.block_ms = 20
        self.jitter_ms = jitter_ms   # cushion vs underrun ticks (was 40)
        self.wire_block_bytes = (AUDIO_RATE * self.block_ms // 1000) * AUDIO_CH * 2
        self.mixer = A.Mixer(self.wire_block_bytes,
                             max(self.wire_block_bytes,
                                 AUDIO_RATE * self.jitter_ms // 1000 * AUDIO_CH * 2))
        self.ducker = A.EchoDucker(enabled=duck)
        self.vdec = None         # lazy until we know we have video
        self.have_cam = False

    # [QUALITY_CAP_V1] live video-quality cap. The TX loop reads self.allowed every
    # frame and walks down to the highest allowed rung, so narrowing self.allowed
    # caps resolution on the NEXT frame -- no encoder rebuild, no audio impact. L0/L1
    # stay allowed so a call never dies. cap is a ladder idx: 5=360p, 6=480p, 7=720p,
    # >=7 = Auto (uncapped). Set before or during a call.
    # [WIRE_LOG_V1] One CSV row per second carrying EVERY number that decides
    # what goes on the wire, plus what actually went.
    #
    # The console cannot settle a contradiction. Measured 2026-08-08: a 78 kbps
    # link cap while [VID-TX] reported 24 fps of 1280x720 at 158.9 KB/s -- which
    # is 1271 kbps, sixteen times the cap. The console shows the OUTPUT of the
    # ladder and none of its INPUTS, so there is no way to tell whether the cap
    # never arrived, arrived and was ignored, or arrived and worked while the
    # byte counter is wrong.
    #
    # So the row carries the whole chain: the cap as set, _level_cap as stored,
    # the `allowed` set that send_level() actually consults, the bearer's own
    # rung, the resulting send_l, the resolution that implies, and the measured
    # bytes. If cap=4 and send_l=7 the row says so on the line where it happened.
    WIRE_LOG_PERIOD_S = 1.0

    def _wire_log_open(self):
        path = os.environ.get("FROGNET_WIRE_LOG")
        if path is None:
            # [WIRE_LOG_V1] Platform temp dir, not a hardcoded /tmp. On Windows
            # "/tmp/..." resolves to C:\tmp\, which usually does not exist --
            # the open then raises and the log silently is not there, on the one
            # client where the operator is most likely to be looking for it.
            import tempfile
            path = os.path.join(tempfile.gettempdir(),
                                "frognet-wire-%s.csv" % (self.name or "unnamed"))
        if not path or path.lower() in ("0", "off", "none"):
            self._wlog = None
            return
        # [WIRE_LOG_V1] The header is part of the file's contract. This appends,
        # and it used to write a header only when the file was empty -- so adding
        # a column meant new rows landed under the OLD header, in the same file,
        # with no complaint. A CSV whose columns silently stop matching its
        # header is worse than no CSV. If the existing header is not the one this
        # build writes, roll the file aside and start a clean one.
        _hdr = ("ts,name,role,"
                "cap_bps,level_cap,ceiling,bearer_idx,allowed,send_l,w,h,"
                "fps_sent,v_kbps_tx,a_kbps_tx,"
                "fps_recv,v_kbps_rx,a_kbps_rx,rx_dims,"
                "v_drops_ps,a_sheds,kf_backlog,audio_backlog,"
                "rx_src,rx_v_fps,rx_v_kbps,rx_rung,rx_w,rx_h,rx_keys,"
                "rx_a_fps,rx_a_kbps\n")
        try:
            new = not os.path.exists(path) or os.path.getsize(path) == 0
            if not new:
                with open(path, "r") as _f:
                    _old = _f.readline()
                if _old != _hdr:
                    _bak = path + ".%d.old" % int(time.time())
                    os.replace(path, _bak)
                    print("[%s] wire log columns changed; previous file kept as "
                          "%s" % (self.name, _bak), flush=True)
                    new = True
            self._wlog = open(path, "a", buffering=1)      # line buffered
            if new:
                # [RX_IS_PER_SOURCE_V1] rx_* is who is sending to US and at
                # what rung, taken at the receive loop so it does not depend on
                # a decoder existing.
                self._wlog.write(_hdr)
            # [WIRE_LOG_V1] Say WHICH fnav wrote it and HOW MANY columns.
            #
            # Four uploads of the same CSV went by with the old 22-column header
            # while both of us assumed the new build was running. The console
            # said "wire log -> <path>" either way, so it could not distinguish
            # "deployed" from "not deployed" -- and neither could I. A file that
            # cannot identify the code that produced it is not evidence.
            print("[%s] wire log -> %s  (%d cols, fnav=%s mtime=%s)"
                  % (self.name, path, _hdr.count(",") + 1,
                     os.path.abspath(__file__),
                     time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(os.path.getmtime(__file__)))),
                  flush=True)
        except OSError as e:
            # Not fatal to a call, but do not pretend it is logging.
            self._wlog = None
            print("[%s] WIRE LOG DISABLED: cannot open %s: %r"
                  % (self.name, path, e), flush=True)

    def _wire_log_row(self, send_l, bearer_idx, fps_sent):
        w = getattr(self, "_wlog", None)
        if w is None:
            return
        now = time.time()
        if now - getattr(self, "_wlog_at", 0.0) < self.WIRE_LOG_PERIOD_S:
            return
        self._wlog_at = now
        st = self.stats.snapshot() or {}
        cap = getattr(self, "_level_cap", None)
        allowed = getattr(self, "allowed", None)
        rv = RUNG_VIDEO.get(send_l) or {}
        # [RX_IS_PER_SOURCE_V1] rx_dims is set on the APP (communicator_live's
        # _paint_tiles), not on the Call -- so this getattr never found it and
        # every row printed "". It is left empty here deliberately rather than
        # reaching across objects: the per-source columns below carry the same
        # fact, taken from the level byte at the receive loop, and they work on
        # a headless client and during the window where the decoder is not yet
        # built. One source for the number, and it is this one.
        dims = None

        def _n(v):
            return "" if v is None else v

        # [RX_IS_PER_SOURCE_V1] Drain the per-source counters onto THIS row's
        # window. One row per sender, so a receiver with two peers reports both
        # and neither is averaged into a single meaningless number.
        with self._rx_lock:
            _srcs = self._rx_src
            self._rx_src = {}
            _rxdt = max(1e-6, now - self._rx_src_t0)
            self._rx_src_t0 = now
        if not _srcs:
            _srcs = {"": {"v_n": 0, "v_b": 0, "a_n": 0, "a_b": 0,
                          "lvl": None, "key": 0}}
        for _src, _c in sorted(_srcs.items()):
          _rung = _c.get("lvl")
          _rv = RUNG_VIDEO.get(_rung) or {}
          try:
            w.write("%.3f,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "%s,%.2f,%.2f,%s,%s,%s,%s,%.2f,%.2f\n" % (
                now, self.name, "sender",
                _n(getattr(self, "_throttle_bps", None)),
                _n(cap), _n(getattr(self, "ceiling", None)), _n(bearer_idx),
                # the set send_level() actually walks; quoted, it has commas
                '"%s"' % (sorted(allowed) if allowed is not None else ""),
                _n(send_l), _n(rv.get("w")), _n(rv.get("h")),
                _n(fps_sent), _n(st.get("v_kbps")), _n(st.get("a_kbps")),
                _n(st.get("v_fps_recv")), _n(st.get("v_rx_kbps")),
                _n(st.get("a_rx_kbps")),
                '"%s"' % ("" if not dims else
                          " ".join("%s=%dx%d" % (k, v[0], v[1])
                                   for k, v in sorted(dims.items()))),
                _n(st.get("v_drop_fps")),
                _n(getattr(getattr(self, "sendq", None), "audio_sheds", None)),
                _n(getattr(self, "_kf_backlog", None)),
                _n(getattr(self, "_audio_backlog", None)),
                _src or "-",
                _c["v_n"] / _rxdt, _c["v_b"] / 1024.0 / _rxdt,
                ("L%d" % _rung) if _rung is not None else "-",
                _n(_rv.get("w")), _n(_rv.get("h")), _c["key"],
                _c["a_n"] / _rxdt, _c["a_b"] / 1024.0 / _rxdt,
            ))
          except (OSError, ValueError) as e:
            print("[%s] wire log write failed: %r -- closing" % (self.name, e),
                  flush=True)
            try:
                w.close()
            except OSError:
                pass
            self._wlog = None

    # [SENDER_READS_MEDIASPEED_V1] The sender lowers its OWN resolution because
    # a viewer asked it to, through tuple space. Nothing else was doing this.
    #
    # MediaSpeed has always been written per (session, viewer) by the viewer's
    # slider -- and only the RELAY ever read it (Relay._poll_caps). The SENDER
    # never did. So Dave sat at L7/1280x720 sending ~145 KB/s while John's cap
    # said 50,000 bps: measured 2026-08-08, 23x over, rx_rung=L7 on every row of
    # a 90-second capture, at every cap from 50k to 566k. John's own uplink
    # obeyed perfectly the whole time. Nothing told Dave anything.
    #
    # This is the loop that closes it: read the viewers' MediaSpeed rows for
    # this session, take the MINIMUM, and put it through cap_to_link -- the same
    # path the local slider uses. Viewer writes the bitrate, sender reads it,
    # resolution comes down. No relay involvement, no new wire kind, no
    # negotiation.
    #
    # MINIMUM, not average: the cap is what a viewer can RECEIVE. Sending above
    # the slowest viewer's number is sending it something it has already said it
    # cannot take, and one starved viewer is a broken call for that person.
    VIEWER_CAP_POLL_S = 2.0

    # [SLOWEST_VIEWER_COMMANDS_V1] The receiver's own verdict on what it can
    # take, measured as frames ACTUALLY ARRIVING and written straight into the
    # space for the sender to read.
    #
    # Nothing here infers a resolution from a bandwidth number. MediaSpeed said
    # "594000 bps" and the sender ran that through VIDEO_SHARE and a table to
    # guess a rung; this says "send me 640x360" because 640x360 is what got
    # through. An instruction, not an inference.
    INBOUND_WATCH_S = 2.0
    RELAY_CONNECT_S = 8.0       # [A_HANG_IS_THE_WORST_REPORT_V1]
    RX_NONE_WARN_S = 15.0       # [ALONE_IS_NOT_SILENT_V1] repeat interval
    INBOUND_DOWN_FPS = 15.0     # below this, we are not keeping up
    INBOUND_UP_FPS = 22.0       # at this, we are
    INBOUND_DOWN_WINDOWS = 2    # ... for this many windows before stepping down
    INBOUND_UP_WINDOWS = 3      # ... and this many before asking for more

    def _watch_inbound(self):
        """CONSUMER: measure what is arriving and report it.

        [PRODUCER_LEADS_CONSUMERS_REPORT_V1] A consumer caps nobody and asks
        nobody for anything. It reports the rate it is getting and whether it is
        keeping up; the producers move. Only a consumer has this measurement,
        and it is the only evidence about the link that exists.
        """
        last_n, last_t = {}, {}
        while not self._stop.is_set():
            self._stop.wait(self.INBOUND_WATCH_S)
            if self._stop.is_set():
                continue
            # A producer never measures: it is not receiving, and its own absent
            # downlink is not evidence about anything. Acting on one is how a
            # headless publisher with no viewers became "the slowest consumer"
            # on its own call and walked a good link to 160x120.
            if self.is_producer or not self.session:
                continue
            now = time.time()
            with self._rx_lock:
                counts = dict(self._in_src)
            for src, total in counts.items():
                if src not in last_n:
                    last_n[src], last_t[src] = total, now
                    continue
                # [MEASURE_LONG_ENOUGH_TO_BE_A_MEASUREMENT_V1] A rolling
                # horizon, not one window.
                #
                # Frames do not arrive smoothly. A capture device that stalls
                # and catches up delivers 0.5 fps in one two-second window and
                # 45 in the next, from a producer sending a steady 24. Measured
                # 2026-08-11, from one consumer, consecutively:
                #
                #   45.8 ... 3.4 ... 21.4 ... 0.5 ... 25.2
                #
                # Each window was taken as a verdict, so every other one asked
                # the whole network for a lower rate, and the network obliged.
                # The instantaneous rate of a bursty stream is not the delivered
                # rate; the delivered rate is frames over a span long enough for
                # a burst and its gap to both be inside it.
                if self._in_hist is None:
                    self._in_hist = {}
                _hist = self._in_hist.setdefault(src, [])
                _hist.append((now, total))
                while len(_hist) > 1 and now - _hist[0][0] > self.RATE_HORIZON_S:
                    _hist.pop(0)
                # And the horizon must be FULL. Judging on a partial span is
                # the same mistake one level up: two samples two seconds apart
                # is a two-second window wearing a longer name, and that is
                # exactly what produced 3.5 fps from a stream delivering 24.
                if (len(_hist) < 2
                        or _hist[-1][0] - _hist[0][0] < self.RATE_HORIZON_S * 0.8):
                    continue
                dt = max(1e-6, _hist[-1][0] - _hist[0][0])
                fps = (_hist[-1][1] - _hist[0][1]) / dt
                last_n[src], last_t[src] = total, now

                # [ALONE_IS_NOT_SILENT_V1] Sending into nothing looks exactly
                # like a healthy call from here: the socket accepts every byte,
                # so no drop and no backlog. The one fact that distinguishes
                # them is that NOTHING IS COMING BACK. Dropped by a rewrite of
                # this function and caught by test_fan_session_oracle -- the
                # only oracle in the tree that was green before and red after.
                if total > 0:
                    self._rx_ever = True
                elif not getattr(self, "_rx_ever", False):
                    _sent = (self.stats.snapshot() or {}).get("v_kbps") or 0.0
                    if _sent > 0 and now - self._rx_none_at >= self.RX_NONE_WARN_S:
                        self._rx_none_at = now
                        print("[%s] sending %.0f KB/s and receiving NOTHING -- "
                              "no frame has ever arrived. Session %s: is anyone "
                              "else in it?"
                              % (self.name, _sent, self.session or "NONE"),
                              flush=True)

                # [ABSENCE_IS_NOT_A_MEASUREMENT_V1] Zero frames means the
                # producer sent nothing, not that this consumer is slow. A
                # receiver learns "this is too much for me" by being SENT too
                # much and failing to take it; being sent nothing teaches
                # nothing. Reading absence as slowness was a ratchet: the
                # producer sheds, I report worse, the rate drops, it sheds more.
                if fps <= 0.0:
                    continue

                # [JUDGE_AGAINST_SOMETHING_V1] No claim from the producer means
                # nothing to be unhappy ABOUT.
                #
                # This measured its first window before any producer row had
                # been read, fell back to its own nominal 24, and reported NOT
                # keeping up on the strength of a startup transient -- at 0x0,
                # because there was no geometry to name either. Every
                # participant then stepped the network down on that report, once
                # every two seconds. Measured 2026-08-11: 1280x720 to 160x120 in
                # twenty seconds while the sender held 23.8/24 fps with zero
                # drops the whole way.
                #
                # A consumer judges what a producer SAID it would send. Until
                # somebody has said it, there is no verdict to give.
                p = self._producer_says or {}
                if not p.get("fps"):
                    # [SAY_WHICH_GATE_STOPPED_THE_REPORT_V1] Five conditions
                    # stand between a measured frame rate and a published
                    # `getting` row, and when no row appears there is no way to
                    # tell which one held it. Measured 2026-08-11: frames were
                    # crossing in both directions at 23-24 fps and not one
                    # report was ever written, so the call could only ratchet
                    # down -- and I spent a turn reasoning about which gate it
                    # was instead of asking.
                    if not getattr(self, "_said_gate", False):
                        self._said_gate = True
                        print("[%s] measuring %.1f fps from %s but NOT "
                              "reporting: no producer claim yet (session=%s, "
                              "senders seen=%s). Nothing to judge against."
                              % (self.name, fps, src, self.session or "NONE",
                                 sorted(self._seen_senders or ()) or "none"),
                              flush=True)
                    continue
                self._said_gate = False
                want = self._expected_fps()
                # [THE_FLAG_MEANS_STABLE_V1] Happy is not "this window was
                # fine". It is "this rate has held".
                #
                # The flag is what lets the whole network climb, so one good
                # window setting it means everybody steps up on a single sample
                # -- and a single sample is exactly what a burst of buffered
                # frames looks like just after a step DOWN. That is a loop that
                # climbs straight back into the congestion it just escaped.
                # [NOT_YET_STABLE_IS_NOT_OVERLOADED_V1] Two different things,
                # and they were one flag.
                #
                # `happy` is what lets the network CLIMB, so it takes three
                # consecutive good windows -- a single sample is what a burst of
                # buffered frames looks like right after a step down. But the
                # producer read not-happy as a COMPLAINT, so a consumer taking
                # more than nominal reported "not keeping up" for its first two
                # windows and the network stepped down on it. Measured
                # 2026-08-11: "John is not keeping up (55.4 fps at 1920x1080)"
                # -- fifty-five frames a second, on a rate of twenty-four.
                #
                # Struggling is measured NOW and means the rate is genuinely
                # below the bar. Happy is earned over windows and means it has
                # held. A consumer that is neither is simply not yet sure, and
                # nobody should move on that.
                _ok_now = fps >= want * self.HAPPY_FRACTION
                if _ok_now:
                    self._good_windows += 1
                    self._bad_windows = 0
                else:
                    self._good_windows = 0
                    self._bad_windows += 1
                # [SETTLE_BEFORE_YOU_CLIMB_V1] Good windows are not enough on
                # their own: the rate has to have HELD for a while.
                #
                # happy is what lets the whole call promote, and promoting
                # changes the rate, which restarts the measurement. With only a
                # window count the sequence is: three good windows, climb, three
                # more, climb again -- and a link that is fine at one rung and
                # not at the next spends its life alternating between them.
                # Reported 2026-08-12 as "switching a lot".
                #
                # So the flag also needs SETTLE_S of wall clock since this end
                # last saw the rate change. Down is unaffected: struggling is
                # measured now and steps immediately, which is the asymmetry the
                # whole loop is built on -- fast to relieve, slow to load.
                _at = getattr(self, "_rate_changed_at", 0.0)
                _settled = (now - _at) >= self.SETTLE_S if _at else True
                happy = (self._good_windows >= self.HAPPY_WINDOWS) and _settled
                # Down is fast, but not on a single sample. Two consecutive
                # shortfalls over a rolling horizon is still a few seconds --
                # quick enough that somebody genuinely behind is not left there,
                # slow enough that one gap in a bursty stream is not a verdict.
                struggling = self._bad_windows >= self.BAD_WINDOWS

                if (happy, struggling) != self._last_happy or (
                        now - self._reported_at) >= self.REPORT_EVERY_S:
                    self._last_happy = (happy, struggling)
                    self._reported_at = now
                    # Report the geometry the PRODUCER says it is sending: that
                    # is what we are trying to consume. A headless consumer
                    # decodes nothing and has no measured dimensions of its own,
                    # and inventing some would report a size nobody chose.
                    self._publish_role(int(p.get("w") or 0),
                                       int(p.get("h") or 0), fps, happy,
                                       struggling)
                    print("[%s] getting %.1f fps of the %.1f %s is sending -- %s"
                          % (self.name, fps, want,
                             p.get("producer") or src,
                             "NOT keeping up" if struggling
                             else ("keeping up" if happy
                                   else "keeping up, not yet steady")),
                          flush=True)

    HAPPY_FRACTION = 0.80     # of what the producer says it is sending
    HAPPY_WINDOWS = 3         # consecutive good windows before the flag goes up
    # [SETTLE_BEFORE_YOU_CLIMB_V1] Wall clock at a rate before this end will
    # propose leaving it upward.
    #
    # Twenty seconds was measured as still switching a lot, 2026-08-12. The
    # reason is arithmetic: a promotion is judged over RATE_HORIZON_S of frames
    # and needs HAPPY_WINDOWS of them, so the evidence for a climb takes most of
    # twenty seconds to gather on its own. A settle of the same order does not
    # add patience, it just about covers the measurement -- and the call spends
    # its life alternating between a rung that works and the one above it.
    #
    # Forty-five is roughly twice the time it takes to be sure, so a rate that
    # holds is left alone rather than immediately re-tested. It costs one step
    # of picture quality for that time when a link genuinely improves, and
    # nothing at all when it does not. Down is unaffected: struggling is
    # measured now and steps immediately, which is the asymmetry this whole loop
    # is built on -- fast to relieve, slow to load.
    SETTLE_S = 45.0
    BAD_WINDOWS = 2           # consecutive shortfalls before asking for lower
    RATE_HORIZON_S = 6.0      # frames counted over this span, not one window
    # [A_STEP_NEEDS_TIME_TO_BE_FELT_V1] A climb waits this long after the last
    # change. Longer than RATE_HORIZON_S, because the horizon has to FILL at the
    # new size before the evidence is about the new size at all.
    RATE_SETTLE_S = 15.0
    # [ONE_STEP_PER_SIZE_V1] After lowering for a tight uplink, wait this long
    # before believing the buffer again. It has to hold the old size's bytes
    # long enough to drain them; measuring sooner reads the previous size.
    UPLINK_SETTLE_S = 5.0
    # [A_STEP_NEEDS_TIME_TO_BE_FELT_V1] A drop holds too, briefly. One bad
    # window is not a trend, and dropping on it costs a new encoder and a
    # forced keyframe for a condition that may already be over. Four seconds is
    # under the measurement horizon on purpose: long enough that a single hiccup
    # does not move the picture, short enough that a link genuinely in trouble
    # is answered inside one horizon.
    #
    # This is safe to hold ONLY because audio does not wait for it. Audio is
    # sourced first and video is what gets traded away, so the four seconds cost
    # a coarser picture and never a broken conversation. If that ever stops
    # being true, this constant goes back to zero.
    DROP_SETTLE_S = 4.0
    REPORT_EVERY_S = 8.0      # re-say it even when nothing changed

    def _device_max(self):
        """[EVERYONE_PUBLISHES_THEIR_OWN_CAPABILITY_V1] The best this can do.

        A fact about this machine and its camera, known only here. It is what a
        producer starts at, and what tells the others what is possible before
        anything has been measured.
        """
        g = (RUNG_GEO.get(self.aspect) or {}).get(max(RUNG_VIDEO)) or {}
        return {"w": int(g.get("w") or 0), "h": int(g.get("h") or 0),
                "fps": float(self.fps)}

    def _expected_fps(self):
        """The rate the producer says it is sending, or our own nominal if it
        has not said. Judging "keeping up" against a rate nobody claimed makes
        every consumer unhappy on a producer that is deliberately slow."""
        p = getattr(self, "_producer_says", None) or {}
        f = float(p.get("fps") or 0.0)
        return f if f > 0 else float(self.fps)

    # [ONE_STEP_PER_SIZE_V1] A step down needs a keyframe AT THE NEW SIZE to
    # judge, and the queue still holds the old one's.
    KF_WALK_SETTLE_S = 0.75

    # [A_SIZE_THAT_FAILED_STAYS_FAILED_V1] The climb back to a size that has
    # already been refused is not free: it encodes a full keyframe at that
    # geometry, sheds it after KEY_RETRIES+1 writability waits, then re-encodes
    # one size down. Measured on Windows 2026-08-15: that cycle ran every eight
    # seconds for the whole call -- "call rate 1920x1080" then "keyframe would
    # not fit at 1920x1080 -- trying 1280x720" on repeat, hundreds of times,
    # never once succeeding. The CPU spike it costs is what starved the audio
    # receive loop; [MIX] showed dry climbing to 426 blocks of pure silence
    # during the burst and then 189 trim splices when the backlog landed.
    #
    # Nothing recorded that the size had failed, so every cycle re-derived it
    # as the call rate and tried again. It is recorded now, and the retry backs
    # off: two consecutive failures at a size and the probes go to 60s, then
    # 150s, then 300s and stay there. A probe that fails does NOT re-arm the
    # short interval -- it advances the backoff, because the failure is the
    # evidence that the size is still out of reach.
    KF_FAIL_BEFORE_BACKOFF = 2
    KF_BACKOFF_S = (60.0, 150.0, 300.0)

    def _kf_size_blocked(self, px, now=None):
        """Is this pixel count still inside its post-failure backoff?

        px == 0 means 'the largest size that has failed', used by the derivation
        path to refuse a climb it is about to make.
        """
        st = getattr(self, "_kf_fail", None)
        if not st:
            return False
        rec = st.get(int(px))
        if not rec:
            return False
        now = now or time.time()
        n = rec["n"]
        if n < self.KF_FAIL_BEFORE_BACKOFF:
            return False
        step = min(n - self.KF_FAIL_BEFORE_BACKOFF, len(self.KF_BACKOFF_S) - 1)
        return (now - rec["at"]) < self.KF_BACKOFF_S[step]

    def _kf_note_fail(self, px, now=None):
        """Record that a keyframe at this size was refused, and say the wait."""
        now = now or time.time()
        st = getattr(self, "_kf_fail", None)
        if st is None:
            st = self._kf_fail = {}
        rec = st.setdefault(int(px), {"n": 0, "at": 0.0})
        rec["n"] += 1
        rec["at"] = now
        if rec["n"] < self.KF_FAIL_BEFORE_BACKOFF:
            return None
        step = min(rec["n"] - self.KF_FAIL_BEFORE_BACKOFF,
                   len(self.KF_BACKOFF_S) - 1)
        return self.KF_BACKOFF_S[step]

    def _kf_walk_ok(self, now):
        """May the keyframe walk take another step yet?

        [ONE_STEP_PER_SIZE_V1] Every shed keyframe stepped the size down, so a
        queue already full of 1080p produced six sheds in one window and the
        walk went 1920 to 160 without a single frame having gone at any size in
        between. Measured 2026-08-11: all six steps on consecutive lines, and
        the picture ended at 160x120 with 23 drops a second -- the link had not
        been tested at any of the sizes it walked past.

        The frames that shed immediately after a step were ENCODED AT THE OLD
        SIZE and were already queued. They say nothing about the new one. So a
        step is followed by a short settle, and the next step needs a keyframe
        that was actually encoded at the size being judged.
        """
        if now - getattr(self, "_kf_walk_at", 0.0) < self.KF_WALK_SETTLE_S:
            return False
        self._kf_walk_at = now
        return True

    def _uplink_headroom(self):
        """What fraction of this socket's buffer is free, or None if unknowable.

        [THE_SOCKET_KNOWS_BEFORE_THE_DROP_V1] SO_SNDBUF minus TIOCOUTQ is what
        the wire can take RIGHT NOW, and it is knowable before a byte is
        written. That is a better congestion signal than a drop, because a drop
        is what happens after you have already asked for too much.

        Used as evidence about THIS end's uplink, published like any other
        self-knowledge. A queue that is filling says "I am about to be the
        slowest sender" before anything is lost, and equilibrium is reached
        without a single frame being sacrificed to find the edge.
        """
        try:
            room = _send_room(self.vsendq.sock)
            cap = self.vsendq.sock.getsockopt(socket.SOL_SOCKET,
                                              socket.SO_SNDBUF)
        except Exception:
            return None
        if room < 0 or cap <= 0:
            return None
        return float(room) / float(cap)

    def _publish_role(self, w, h, fps, happy=None, struggling=False):
        """[PRODUCER_LEADS_CONSUMERS_REPORT_V1] Say what only this role knows."""
        try:
            import comms_control as _cc
            if self._cp_take is None:
                self._cp_take = _cc.ControlPlane(self.name, self.name)
                self._cp_take._joined[self.session] = {
                    "host": self.host, "port": int(self.port)}
            if self._cap_said is None:
                self._cap_said = self._device_max()
                self._cp_take.report_capability(
                    self.session, self._cap_said["w"], self._cap_said["h"],
                    self._cap_said["fps"])
            # [SEND_NO_FASTER_THAN_THE_SLOWEST_SENDER_V1] `sending` and
            # `getting` are two different facts and an end can have both. This
            # routed on is_producer, so a two-way participant wrote one and
            # never the other -- its own send rate invisible to everybody, and
            # therefore the one end nobody could be bounded by.
            #
            # `happy is None` marks a send report; a receive report always
            # carries a verdict.
            if happy is None:
                self._cp_take.report_sending(self.session, w, h, fps)
            else:
                # report_receiving keeps the durable `too_big` itself, from the
                # previous report -- [A_RATE_THAT_FAILED_IS_NOT_A_CANDIDATE_V1].
                self._cp_take.report_receiving(self.session, w, h, fps,
                                               bool(happy), bool(struggling))
        except Exception as e:
            print("[%s] could not publish my report: %r" % (self.name, e),
                  flush=True)

    def _poll_consumer_feedback(self):
        """[ONE_DERIVATION_V1] Write what only I know. Read everything. Derive.

        [NO_SYNC_JUST_STATE_V1] Nothing is sent to anybody and nothing waits.
        [ONE_RATE_FOR_THE_NETWORK_V1] Every end runs the same derivation over
        the same rows and lands on the same rate.
        [THE_RATE_IS_A_FUNCTION_OF_THE_ROWS_V1] Not "my index, plus or minus
        one" -- a rate stepped from a local index is a function of THIS end's
        history, and two ends with different histories sit at different sizes.
        [A_PRODUCER_S_RATE_BOUNDS_THE_CALL_V1] The lowest sender bounds it: an
        end that has settled somewhere established that rate by doing it.
        [SAY_WHAT_YOU_ARE_SENDING_UNCONDITIONALLY_V1] The write happens before
        the read, every cycle, waiting on nobody.

        One program, many threads, shared memory. This thread does three things
        in order and nothing else:

          1. WRITE what only this end can know -- what it is sending, and (from
             _watch_inbound) what it is getting.
          2. READ everything anybody published about this call.
          3. DERIVE the rate with ControlPlane.derive_rate, which is pure and
             is the same function every other participant runs.

        There is no message, no request, nobody waiting. Two ends that disagree
        are two ends that read at different instants, and the next read settles
        it -- there is nothing to reconcile because there was never an exchange.

        The logic used to live HERE, inline, which is why it kept diverging from
        what the other end did: two copies of a rule are two rules.
        """
        import comms_control as _cc
        cp = _cc.ControlPlane(self.name, self.name)
        ladder = geometry_ladder(self.aspect)

        while not self._stop.is_set():
            self._stop.wait(self.TREATMENT_POLL_S)
            if self._stop.is_set() or not self.session:
                continue
            try:
                # 1. write what only I know
                if self.have_cam and not self.no_video:
                    g = self._serve_cap_geo or ladder[0]
                    self._publish_role(int(g["w"]), int(g["h"]),
                                       float(self.fps), happy=None)
                    # [THE_SOCKET_KNOWS_BEFORE_THE_DROP_V1] An uplink whose
                    # buffer is filling is about to be the slowest sender.
                    # Saying so before anything is lost is what makes the
                    # equilibrium settle without sacrificing frames to find it.
                    # [A_SENDER_IS_NOT_A_CONSUMER_OF_ITSELF_V1] and reports as
                    # what it is.
                    #
                    # This called report_receiving, which publishes a GETTING
                    # row -- a claim about what is arriving here and whether
                    # this end is keeping up with it. A publisher receives
                    # nothing, so the claim is about a stream that does not
                    # exist, and derive_rate reads it as a struggling consumer
                    # and steps the whole call down. At the smaller size the
                    # uplink is still tight, so it says it again.
                    #
                    # Measured 2026-08-12 on a headless publisher with nobody
                    # watching: "derived from 1 sender(s), 1 report(s)" -- the
                    # one report being its own -- walking 1280x720 to 160x120 in
                    # five steps at 23 fps with zero drops the whole way.
                    #
                    # A tight uplink is a real fact and still moves the rate. It
                    # moves it as what it is: this end cannot SEND what it is
                    # sending, so it lowers what it says it is sending, and
                    # every other end derives from that. A sender has no
                    # downlink and no vote as a consumer -- that was
                    # [PRODUCER_LEADS_CONSUMERS_REPORT_V1], and this was the
                    # path around it.
                    # [ONE_STEP_PER_SIZE_V1] and let the buffer drain first.
                    #
                    # The buffer is full of the OLD size. Dropping 1920 to 1280
                    # does not empty it -- 346 KB/s of 1080p is still in there,
                    # and at the link rate it takes seconds to leave. Measure
                    # again before that and the answer is about the size just
                    # left, so it steps again, and again: 1280 to 160 in five
                    # steps at 23 fps with zero drops, measured 2026-08-12.
                    #
                    # So a step is followed by a wait long enough for the queue
                    # to actually turn over at the new size. Then it measures.
                    # If it has room it stops -- and on a fat link it stops at
                    # the first step, because one step is all it needed.
                    _hr = None
                    _unow = time.time()
                    if _unow - self._uplink_step_at >= self.UPLINK_SETTLE_S:
                        _hr = self._uplink_headroom()
                    if _hr is not None and _hr < self.UPLINK_TIGHT:
                        self._uplink_step_at = _unow
                        _lad = geometry_ladder(self.aspect)
                        _px = int(g["w"]) * int(g["h"])
                        _next = next((x for x in _lad
                                      if x["w"] * x["h"] < _px), None)
                        if _next is not None:
                            self._cp_take.report_sending(
                                self.session, int(_next["w"]), int(_next["h"]),
                                float(self.fps), capped=True)
                        if not self._said_tight:
                            self._said_tight = True
                            print("[%s] uplink buffer %.0f%% free at %dx%d -- "
                                  "saying so now, before frames start dropping"
                                  % (self.name, 100.0 * _hr,
                                     g["w"], g["h"]), flush=True)
                    else:
                        self._said_tight = False

                # 2. read everything
                state = cp.call_state(self.session)
            except Exception as e:
                print("[%s] state read failed: %r (holding at %s)"
                      % (self.name, e,
                         self._serve_cap_geo or "the local ladder"), flush=True)
                continue

            # what the others are sending, for the consumer's own yardstick
            self._seen_senders = sorted(s["user"] for s in state["senders"])
            others = [s for s in state["senders"] if s["user"] != self.name]
            if others:
                _p = min(others, key=lambda s: s["w"] * s["h"])
                if _p != self._producer_says:
                    self._producer_says = _p
                    print("[%s] %s is sending %dx%d @%.1f fps"
                          % (self.name, _p["user"], _p["w"], _p["h"],
                             _p["fps"]), flush=True)

            # [A_SHRINKING_READ_IS_NOT_NEWS_V1] A read that sees FEWER
            # participants than the last one is a partial read, not a departure.
            #
            # The store 503s constantly and a request that half-succeeds returns
            # a subset. Measured 2026-08-11, alternating every cycle for the
            # length of the call:
            #
            #   call rate 160x120 -- derived from 2 sender(s), 1 report(s)
            #   call rate 320x240 -- derived from 1 sender(s), 0 report(s)
            #
            # Losing the other end's row removes the evidence holding the rate
            # down, so it climbs; the next read finds the row again and it drops
            # back. Nothing about the call changed either time.
            #
            # A participant that has really gone stops being re-asserted and
            # ages out under fresh_s, which takes CALL_FRESH_S -- so a genuine
            # departure still lands, just not on the strength of one thin read.
            _n = len(state["senders"]) + len(state["getters"])
            if _n < self._state_seen and self._state_thin < 3:
                self._state_thin += 1
                continue          # thinner than last time: wait for a full one
            self._state_thin = 0
            self._state_seen = _n

            # 3. derive
            want = _cc.ControlPlane.derive_rate(state, ladder,
                                                self._serve_cap_geo)
            if want is None:
                continue          # nobody sending yet: the local ladder rules
            # [A_STEP_NEEDS_TIME_TO_BE_FELT_V1] Settle before moving again.
            #
            # The derivation is a pure function of the rows, so it answers on
            # every poll -- and when the loop moved to derive_rate the
            # hold-down was left behind. A promotion could be followed by
            # another two seconds later, before the first had produced a single
            # measured window at the new size, and the call switched
            # constantly. Reported from a live run, 2026-08-12: "it needs a
            # little more time to settle before it tries to go up again."
            #
            # A change costs a new encoder and a forced keyframe, and the
            # evidence for whether it was right arrives over the next
            # RATE_HORIZON_S of frames. Moving before that is deciding on
            # measurements taken at the OLD size.
            #
            # Asymmetric, deliberately, in the same shape as everything else
            # here: going DOWN is not held, because somebody is already not
            # keeping up and every window spent waiting is a window of frames
            # they cannot take. Only the climb waits.
            _now = time.time()
            _going_up = (self._serve_cap_geo is not None
                         and want["w"] * want["h"]
                         > int(self._serve_cap_geo["w"]) * int(self._serve_cap_geo["h"]))
            _hold = self.RATE_SETTLE_S if _going_up else self.DROP_SETTLE_S
            if (self._serve_cap_geo is not None and self._settled_at
                    and _now - self._settled_at < _hold):
                continue

            # [A_SIZE_THAT_FAILED_STAYS_FAILED_V1] Do not climb onto a size
            # whose keyframe was refused and whose backoff has not expired.
            # Going DOWN is never blocked: a refusal is evidence about the size
            # that failed, not about smaller ones.
            if _going_up and self._kf_size_blocked(want["w"] * want["h"], _now):
                if not getattr(self, "_said_kf_blocked", 0.0) or \
                        _now - self._said_kf_blocked > 30.0:
                    self._said_kf_blocked = _now
                    print("[%s] not climbing to %dx%d -- its keyframe was "
                          "refused and it is still backed off"
                          % (self.name, want["w"], want["h"]), flush=True)
                continue

            if want != self._serve_cap_geo:
                self._settled_at = _now
                self._serve_cap_geo = dict(want)
                # [A_NEW_SIZE_NEEDS_A_NEW_KEYFRAME_V1] Changing geometry rebuilds
                # the encoder, and every frame it then produces references a
                # keyframe the far end has never seen. Without one the decoder
                # holds the LAST picture it could decode and shows that instead
                # -- an old frame, at the old size, with occasional partial
                # updates. Reported 2026-08-11: "a frame from one of the older
                # pictures ... the pic is not 160x120 on my screen".
                #
                # The keyframe walk already forced one on every step; the
                # derivation path, which is now what moves the size most of the
                # time, did not.
                self._force_key = True
                self._promoted_at = time.time()
                # [SETTLE_BEFORE_YOU_CLIMB_V1] the clock the climb waits on.
                # Set on EVERY change, up or down: a rate arrived at by
                # stepping down has proven no more than one arrived at by
                # climbing, and leaving it early is what produces the churn.
                self._rate_changed_at = time.time()
                self._good_windows = 0
                self._kf_backlog = 0        # only what follows the change counts
                print("[%s] call rate %dx%d -- derived from %d sender(s), "
                      "%d report(s)"
                      % (self.name, want["w"], want["h"],
                         len(state["senders"]), len(state["getters"])),
                      flush=True)

    def _poll_media_control(self):
        """[MEDIACONTROL_BELONGS_TO_THE_CALL_V1] Read the CALL's own settings.

        One row, scoped to the call, that everybody in it reads. Whoever moves
        the slider writes it and every picture follows -- nobody sends anything
        to anybody. That is the demonstration.

        What this replaces was pairwise: MediaSpeed scoped per viewer per
        session, MediaTreatment per producer per viewer. "The speed" was N x M
        opinions needing reconciliation, and a client on the wrong end of a pair
        -- or in a session the other end had not joined -- read none of them.
        Two days went into establishing which end was stale. There is one end
        now.
        """
        import frognet_tuples as _T
        import comms_control as _cc
        cp = _cc.ControlPlane(self.name, self.name)
        last = None
        while not self._stop.is_set():
            try:
                # [THE_PIPE_IS_SHARED_V1] the call's setting is the WHOLE pipe;
                # what this sender arms is its share of it.
                row = cp.media_share(self.session) if self.session else None
                if row and row.get("ts") and row != last:
                    last = row
                    self._apply_media_control(row)
            except Exception as e:
                print("[%s] media control read failed: %r (still on %s)"
                      % (self.name, e, last), flush=True)
            self._stop.wait(self.TREATMENT_POLL_S)

    def _apply_media_control(self, row):
        """Put the call's settings into effect here.

        geometry caps the picture; speed_bps arms the wire. Both only ever
        DOWNWARD from what this sender's own ladder has earned: the call's
        setting is what everyone agrees to serve, not permission to exceed what
        this uplink has been shown to carry.
        """
        geo = (row.get("geometry") or "").lower().replace(" ", "")
        if geo and "x" in geo:
            try:
                w, h = (int(x) for x in geo.split("x", 1))
                self._cmd_geo = {"w": w, "h": h}
            except (TypeError, ValueError):
                print("[%s] media control geometry %r is not WxH -- ignored"
                      % (self.name, row.get("geometry")), flush=True)
        elif geo in ("", "auto"):
            self._cmd_geo = None
        bps = int(row.get("share_bps") or 0)
        try:
            self.set_throttle(bps)
        except Exception as e:
            print("[%s] could not apply speed %s: %r" % (self.name, bps, e),
                  flush=True)
        _total = int(row.get("total_bps") or 0)
        _n = int(row.get("senders") or 1)
        print("[%s] call media control from %s: pipe=%s / %d sender(s) %s -> "
              "my share %s, geometry=%s"
              % (self.name, row.get("set_by") or "?",
                 ("%d bps" % _total) if _total else "unconstrained",
                 _n, ",".join(row.get("members") or []) or "?",
                 ("%d bps" % bps) if bps else "unconstrained",
                 row.get("geometry") or "auto"), flush=True)

    TREATMENT_POLL_S = 2.0
    TREATMENT_FRESH_S = 120

    def _poll_viewer_caps(self):
        """Poll MediaSpeed for this session and cap our rung to the slowest
        viewer's number."""
        import frognet_tuples as _T
        last = None
        while not self._stop.is_set():
            try:
                caps = {}
                _seen_sessions = set()
                for row in _T.get("communicator", "MediaSpeed",
                                  fresh_s=self.VIEWER_CAP_FRESH_S):
                    v = row.get("value") or {}
                    if v.get("session") != self.session:
                        _seen_sessions.add(str(v.get("session")))
                        continue
                    # Our own row is a cap on what WE receive, not on what we
                    # send. Capping our uplink with it would double-apply the
                    # local slider, which already went through set_throttle.
                    if v.get("addr") == _T.my_ip():
                        continue
                    bps = int(v.get("bps") or 0)
                    if bps <= 0:
                        continue          # 0 is "unlimited", not "cap at zero"
                    _a = v.get("addr") or "?"
                    _ts = int(v.get("ts") or 0)
                    if _a not in caps or _ts >= caps[_a][0]:
                        caps[_a] = (_ts, bps)
                want = min((b for _t, b in caps.values()), default=0)
                # [SENDER_READS_MEDIASPEED_V1] Re-assert when the CAP changes OR
                # when something else has moved the ceiling since we set it.
                #
                # Two controllers share this knob: this poller and
                # _ladder_from_backlog, which pulls the ceiling down a rung on a
                # keyframe backlog report. Re-applying only on a changed NUMBER
                # meant the backlog's step was permanent -- observed live: a
                # viewer cap of 551000 set L5, a backlog report took it to L4, and
                # the sender stayed audio-only until the viewer touched the slider
                # again, because `want` had not changed.
                #
                # The viewer's number is the standing instruction. A transient
                # backlog may tighten below it; when the backlog clears, the
                # standing instruction is what the rung returns to.
                # [CEILING_IS_A_MINIMUM_V1] Only when OUR number changes. The
                # re-assert that used to be here compared _level_cap against what
                # we installed and rewrote it when anything else had moved -- so
                # it undid the backlog's step every poll and the two fought.
                # Our bound is now recorded separately and combined by min(), so
                # it cannot be lost and cannot overrule anyone.
                if want != last:
                    last = want
                    if want:
                        print("[%s] viewer cap %d bps from %d viewer(s) %s "
                              "-- lowering my rung to fit"
                              % (self.name, want, len(caps),
                                 sorted(caps)), flush=True)
                        self._set_ceiling("viewer", self.level_for_link(want))
                    else:
                        # [SENDER_READS_MEDIASPEED_V1] If rows exist but none
                        # carry OUR session, say so and name theirs. "no viewer
                        # cap" and "there are caps but for another session" look
                        # identical from here and need opposite fixes -- one is a
                        # viewer who has not set a speed, the other is the two
                        # ends of one call not sharing a session id.
                        if _seen_sessions:
                            print("[%s] no viewer cap for session %s -- %d "
                                  "MediaSpeed row(s) exist but carry session(s) "
                                  "%s. The cap cannot cross while the two ends "
                                  "disagree about the session."
                                  % (self.name, self.session,
                                     len(_seen_sessions),
                                     ", ".join(sorted(_seen_sessions))),
                                  flush=True)
                        else:
                            print("[%s] no viewer cap -- rung unconstrained by "
                                  "viewers" % (self.name,), flush=True)
                        self._set_ceiling("viewer", None)
            except Exception as e:
                # Loud, and the last cap STANDS. A control-plane read that fails
                # is not a viewer lifting its cap: dropping to unlimited on a
                # failed read is exactly the silent lift [MEDIASPEED_V1] warns
                # about on the relay side.
                print("[%s] viewer cap read failed: %r (keeping %s)"
                      % (self.name, e, last if last is not None else "none"),
                      flush=True)
            self._stop.wait(self.VIEWER_CAP_POLL_S)

    def _set_ceiling(self, which, level):
        """[CEILING_IS_A_MINIMUM_V1] Record one controller's bound and install
        the lowest of them. `level` None = this controller has no opinion."""
        if which == "viewer":
            self._cap_viewer = level
        elif which == "local":
            self._cap_local = level
        else:
            self._cap_backlog = level
        _bounds = [b for b in (self._cap_viewer, self._cap_backlog,
                               self._cap_local) if b is not None]
        _eff = min(_bounds) if _bounds else L.MAX_IDX
        if getattr(self, "_level_cap", None) != (None if _eff >= L.MAX_IDX else _eff):
            print("[%s] ceiling %s (viewer=%s backlog=%s local=%s)"
                  % (self.name,
                     L.code(_eff) if hasattr(L, "code") else _eff,
                     "-" if self._cap_viewer is None else self._cap_viewer,
                     "-" if self._cap_backlog is None else self._cap_backlog,
                     "-" if self._cap_local is None else self._cap_local),
                  flush=True)
        self.set_level_cap(_eff)

    def _apply_level_cap(self):
        cap = getattr(self, "_level_cap", None)
        base = getattr(self, "_allowed_full", None)
        if base is None:
            if cap is None:
                # Nothing to apply. set_level_cap(MAX_IDX) stores None, and this
                # runs during construction before the ladder exists. Warning here
                # cried wolf twice at every startup and would have taught the
                # operator to ignore the line that matters.
                return
            # [WIRE_LOG_V1] This was a bare `return`: set_level_cap() succeeded,
            # _level_cap was recorded, and the cap reached `allowed` -- which is
            # what send_level() actually consults -- not at all. A silent no-op
            # in the one place a link cap becomes a rung.
            print("[%s] LEVEL CAP NOT APPLIED: cap=%s but _allowed_full is unset, "
                  "so `allowed` is unrestricted and send_level() will ignore it"
                  % (getattr(self, "name", "?"), cap), flush=True)
            return
        if cap is None:
            self.allowed = set(base)
        else:
            self.allowed = {lv for lv in base if lv <= cap or lv <= 1}

    def set_level_cap(self, cap):
        try:
            cap = int(cap)
        except (TypeError, ValueError):
            return
        self._level_cap = None if cap >= L.MAX_IDX else max(0, cap)
        self._apply_level_cap()

    # [SELF_CAP_FROM_LINK_V1] Fraction of the declared link rate that video may
    # claim. The rest is headroom for audio, the FNWP prefixes, TCP/IP overhead
    # and the burstiness of a keyframe -- a rung whose bitrate exactly equals the
    # link rate has no room to be late in.
    VIDEO_SHARE = 0.60

    def level_for_link(self, bps):
        """[CEILING_IS_A_MINIMUM_V1] The rung a link of `bps` can carry, or None
        for no constraint. Pure arithmetic -- it installs nothing.

        Split out of cap_to_link so the viewer poller can hand its bound to
        _set_ceiling and let the minimum decide, instead of writing the ceiling
        itself and overruling the backlog.
        """
        try:
            bps = int(bps or 0)
        except (TypeError, ValueError):
            bps = 0
        if bps <= 0:
            return None
        budget = bps * self.VIDEO_SHARE
        for idx in sorted(RUNG_VIDEO, reverse=True):
            if RUNG_VIDEO[idx].get("bitrate", 0) <= budget:
                return idx
        return max(0, min(RUNG_VIDEO) - 1)

    def cap_to_link(self, bps):
        """Cap the ladder ceiling to a rung this link can actually carry.

        The bearer walks DOWN on evidence -- drops, low frame rate, audio shed.
        None of those fire here: the sender's own uplink refuses nothing, the
        relay absorbs the excess by shedding video for the capped viewer, and
        the sender is never told. So the ladder sat at L7 for 671 consecutive
        samples on a 93 kbps link, pushing 1280x720 keyframes at a wire that
        could carry perhaps a tenth of that. Audio was never shed -- measured,
        audio_shed=0 throughout -- it simply arrived late, queued behind a
        keyframe that took the better part of a second to cross. Dropping the
        picture to 360p by hand fixed it immediately.

        This is that, done automatically. The number is one this client already
        has: it publishes MediaSpeed itself, so it can cap its own ceiling from
        the same value rather than waiting to be told by anyone.

        This is only the OPENING GUESS. [KEYFRAME_BACKLOG_V1] moves the ceiling
        from here on what the relay actually reports: at 93 kbps no keyframes
        backed up, which means the link WAS sustaining video whatever this
        arithmetic says. Evidence beats the table -- this just avoids opening at
        720p on a link that obviously cannot take it.

        A CEILING, not a floor. The bearer still walks down from here on its own
        evidence and climbs back within it. bps == 0 lifts the cap.
        """
        try:
            bps = int(bps or 0)
        except (TypeError, ValueError):
            bps = 0
        if bps <= 0:
            # [CAP_LIFT_ANNOUNCES_ITSELF_V1] Every other path out of this
            # function prints what it did. This one -- the path that throws the
            # ceiling WIDE OPEN -- returned in silence, so the single most
            # consequential thing the ladder can be told to do was the one thing
            # the log never mentioned.
            #
            # Measured, 2026-08-08: the ceiling went L4 -> L7 mid-call with no
            # "link N bps" line anywhere near it, because a malformed link row
            # read as bps=0 and 0 means unlimited. The ladder was doing exactly
            # what it was told; nothing recorded that it had been told anything.
            _was = getattr(self, "_level_cap", None)
            # [CEILING_IS_A_MINIMUM_V1] release OUR bound; the backlog keeps its
            # own. This used to call set_level_cap(MAX_IDX) directly, which threw
            # the ceiling wide open and discarded a keyframe-backlog step that
            # nobody had withdrawn -- the same fight the viewer poller was in.
            self._set_ceiling("local", None)
            # [CEILING_IS_A_MINIMUM_V1] Say what the ceiling IS, not what this
            # one bound would allow. "LIFTED -> L7 unconstrained" was printed
            # while a keyframe-backlog bound still held it at L6: true about the
            # local cap, false about the ladder, and the reader has no way to
            # tell which the sentence means.
            _now = getattr(self, "_level_cap", None)
            _now_s = (L.code(_now) if (hasattr(L, "code") and _now is not None)
                      else (L.code(L.MAX_IDX) if hasattr(L, "code") else "MAX"))
            _others = [n for n, v in (("backlog", self._cap_backlog),
                                      ("viewer", self._cap_viewer))
                       if v is not None]
            print("[%s] link cap RELEASED (bps=%s) -> ceiling now %s%s"
                  % (self.name, bps, _now_s,
                     (" -- still held by " + "+".join(_others)) if _others
                     else " -- the ladder is now unconstrained"),
                  flush=True)
            return None
        budget = bps * self.VIDEO_SHARE
        # Highest rung whose own encode bitrate fits the budget. If none does,
        # fall to the top of the audio-only rungs: a conversation with no
        # picture is the correct answer for a link this small, and it is what
        # the ladder would have reached on its own given the evidence.
        best = None
        for idx in sorted(RUNG_VIDEO, reverse=True):
            if RUNG_VIDEO[idx].get("bitrate", 0) <= budget:
                best = idx
                break
        if best is None:
            best = max(0, min(RUNG_VIDEO) - 1)
        # [CEILING_IS_A_MINIMUM_V1] a bound, not the ceiling.
        self._set_ceiling("local", best)
        print("[%s] link %d bps -> video ceiling %s (%d bps budget)"
              % (self.name, bps, L.code(best) if hasattr(L, "code") else best,
                 int(budget)), flush=True)
        return best

    # [BACKLOG_IS_A_REPORT_NOT_A_LATCH_V1] A backlog report older than this is
    # about a rung this sender has already left. The relay's window is
    # BP_WINDOW_S (2.0s), so anything beyond a couple of windows is stale by
    # construction rather than by guess.
    BACKLOG_STALE_S = 5.0

    # [SENDER_READS_MEDIASPEED_V1] A viewer that has gone away stops capping us
    # after this, instead of holding the sender down forever.
    VIEWER_CAP_FRESH_S = 120

    # [AUDIO_BACKPRESSURE_V1] The highest rung that carries NO video. L5 is the
    # lowest video rung (360p grey, per RUNG_VIDEO), so L4 is video-off with the
    # most headroom left for audio -- the sender's own loop already treats
    # `send_l < 5` as "video shed (audio only)".
    AUDIO_ONLY_IDX = 4

    # [PROBE_BACKS_OFF_V1] Ceiling on the doubling. Ten minutes is long enough
    # that a congested call stops churning and short enough that a link which
    # recovers is not left at the floor for the rest of the day.
    KF_PROBE_MAX_S = 600.0

    KF_PROBE_CLEAR_S = 8.0    # backlog-free this long before trying one rung up
    KF_PROBE_WATCH_S = 2.0    # ... and watch that long before believing it

    # [RUNG_IS_MEASURED_V1] Capacity is not declared, it is observed. Two
    # numbers, both from completed windows:
    #   sustained_bps -- the highest total rate a window carried with NOTHING
    #                    shed. A lower bound on capacity, never an estimate of
    #                    it: the wire took this much, so it can take this much.
    #   refused_bps   -- the lowest total rate at which a window DID shed. An
    #                    upper bound, and the only hard evidence of a ceiling.
    # Until something has been refused there is no known ceiling, and the
    # correct response to an unknown ceiling is to probe -- which is what the
    # data plane was built to do.
    CAPACITY_STALE_S = 30.0

    def _observe_window(self, snap, sheds_delta):
        """Fold one completed measurement window into the measured model."""
        levels = snap.get("levels") or []
        if len(levels) == 1:
            # A window that straddled a rung change describes neither rung and
            # teaches nothing; only a clean window is evidence.
            self._rungs.observe(levels[0], snap)
        total = ((float(snap.get("v_kbps") or 0.0)
                  + float(snap.get("a_kbps") or 0.0)) * 1024.0 * 8.0)
        if total <= 0:
            return
        now = time.time()
        if sheds_delta > 0:
            if (self._refused_bps <= 0 or total < self._refused_bps
                    or now - self._refused_at > self.CAPACITY_STALE_S):
                self._refused_bps = total
                self._refused_at = now
        else:
            if (total > self._sustained_bps
                    or now - self._sustained_at > self.CAPACITY_STALE_S):
                self._sustained_bps = total
                self._sustained_at = now

    def _probe_fits(self, cand):
        """[RUNG_IS_MEASURED_V1] Should we even try `cand`? (ok, why)

        Two tests, both against measured quantities.

        1. Whole-frame ceiling. SotFDataPlane sheds any blob over `_sndbuf // 2`
           before a byte moves, and that is 128 KiB regardless of rung -- exact,
           content-independent, and knowable without trying. A rung whose
           keyframe cannot go out whole can never work, so there is no reason to
           spend a probe finding out.
        2. Measured ceiling. If a window has actually been refused at some total
           rate, that rate is an upper bound. A candidate whose estimated need
           exceeds it is asking for something this wire has already declined.

        No measurement, no refusal: unknown ceiling, and the answer to an unknown
        ceiling is to go and find out.
        """
        key_b, src = self._rungs.keyframe_bytes(cand)
        a_kbps = float((self.stats.snapshot() or {}).get("a_kbps") or 0.0)
        a_bps = a_kbps * 1024.0 * 8.0
        whole = self.vsendq._sndbuf // 2 if getattr(self, "vsendq", None) else 0
        if key_b > 0 and whole and key_b > whole:
            return False, ("keyframe ~%.0fKB (%s) exceeds the %dKB whole-frame "
                           "ceiling" % (key_b / 1024.0, src, whole // 1024))
        need = self._rungs.steady_bps(cand) + a_bps
        now = time.time()
        if (self._refused_bps > 0
                and now - self._refused_at <= self.CAPACITY_STALE_S
                and need > 0 and need >= self._refused_bps):
            return False, ("needs ~%.0f kbps (%s) and %.0f kbps has already "
                           "been refused" % (need / 1000.0, src,
                                             self._refused_bps / 1000.0))
        if src == "unknown" or need <= 0:
            # Nothing has been measured for this rung or anything near it.
            # Printing "needs ~8 kbps" for 1080p is arithmetic on nothing and
            # reads as a finding; say what is actually true instead.
            return True, ("nothing measured for this rung yet -- probing is how "
                          "that changes")
        return True, ("keyframe ~%.0fKB (%s), needs ~%.0f kbps, sustained "
                      "%.0f kbps" % (key_b / 1024.0, src, need / 1000.0,
                                     self._sustained_bps / 1000.0))

    # [BOTTOM_RUNG_SHRINKS_V1] The bottom video rung is not one geometry.
    #
    # 640x360 was the smallest thing ever encoded outside the connect-time walk.
    # Below L5 the send loop hits `send_l < 5` and sheds video ENTIRELY, so the
    # step under "360p is too much" was "no picture" -- there was nothing in
    # between, and the screen never showed anything smaller than 640x360 no
    # matter how tight the wire got.
    #
    # The rung is a wire label; the geometry is separate
    # ([GEOMETRY_IS_NOT_A_RUNG_V1]). So the bottom rung carries a descending
    # list, and the ladder walks THAT before it gives up on video. Audio-only
    # then means what the doctrine says it means: video could not pass at any
    # size, proven by having tried them.
    # [CONSTRAINED_GOES_4_3_V1] The steps below L5, in 4:3, whatever the
    # operator chose above. Built per call because which entries apply depends
    # on where L5 sits: at 16:9 that is 640x360 (230,400 px) so VGA is skipped
    # as bigger; at 4:3 L5 is SVGA (480,000 px) and VGA is the first step down.
    @property
    def BOTTOM_GEOS(self):
        l5 = RUNG_GEO[self.aspect][min(RUNG_GEO[self.aspect])]
        px5 = l5["w"] * l5["h"]
        steps = [dict(l5)] + [dict(g) for g in BOTTOM_4_3
                              if g["w"] * g["h"] < px5]
        return tuple(steps)
    # One step per measurement window at most. The send loop runs at frame rate;
    # without this a single congested second would walk the whole list.
    BOTTOM_STEP_S = 2.0

    def _bottom_shrink(self, now=None, fps_now=None, drops=0, sheds=0):
        """Step the bottom rung's geometry DOWN. True if there was somewhere to go.

        [SHRINKING_ONLY_HELPS_IF_THE_WIRE_IS_THE_LIMIT_V1] A smaller picture
        makes fewer bytes. It does not make the ENCODER faster.

        Measured 2026-08-11 on the headless sender, alone on the call, nothing
        asking it for anything:

            [VID-TX] 2.5/24fps,  2KB/s, drops 0/s, rung L7 @ 1280x720
            [VID-TX] 1.0/24fps, 59KB/s, drops 0/s, rung L8 @ 1920x1080

        Zero drops and zero sheds the whole way: the wire was idle. Software VP8
        at 1080p on that Pi produces about one frame a second, and the rule that
        walks the picture down reads "fps below target" as congestion. Since the
        source cannot reach 24 at ANY size, it walked to 160x120 and then shed
        video entirely -- having passed through 320x240 at 17.4 fps, which was
        perfectly good, and stepped again because 17.4 < 18.0.

        So: shrink when the WIRE is refusing bytes, which is what drops and
        sheds are. With neither, a low frame rate is the camera or the encoder,
        and the answer to that is not a smaller picture -- it is this rate, at
        this size, which is the best this machine can do.
        """
        now = now or time.time()
        if not drops and not sheds:
            # Said on a curve, not on a flag that a single busy window clears.
            # The flag alone reprinted the same sentence a hundred times because
            # the drop counter it was keyed on flapped between windows.
            self._source_limit_n = getattr(self, "_source_limit_n", 0) + 1
            if self._source_limit_n in (1, 10, 100) or self._source_limit_n % 500 == 0:
                print("[%s] %.1f/%.0f fps with NO drops and NO sheds -- the wire "
                      "is not the limit, this machine is. Holding %dx%d: a "
                      "smaller picture makes fewer bytes, not a faster encoder."
                      % (self.name, float(fps_now or 0.0), float(self.fps),
                         self.BOTTOM_GEOS[self._bottom]["w"],
                         self.BOTTOM_GEOS[self._bottom]["h"]), flush=True)
            return True          # stay on video, at this size
        # NOT _shed_seen: that is the diff baseline for the cumulative shed
        # counter and resetting it here would make the next window read every
        # shed since the call began as if it had just happened.
        self._source_limit_n = 0
        if self._bottom + 1 >= len(self.BOTTOM_GEOS):
            return False
        if now - self._bottom_at < self.BOTTOM_STEP_S:
            return True          # already stepped this window; stay on video
        self._bottom += 1
        self._bottom_at = now
        self._bottom_ok = 0      # [GROWTH_IS_EARNED_V1] credit does not survive
        g = self.BOTTOM_GEOS[self._bottom]
        print("[%s] bottom rung -> %dx%d (video is not gone; it is smaller)"
              % (self.name, g["w"], g["h"]), flush=True)
        # [BACKING_DOWN_IS_BOTH_ENDS_V1] Publish it as what I can take, too.
        #
        # A step down was a SENDER-side fact only: my uplink backlogged, I sent
        # less, and my can_take row never moved -- so the call's minimum never
        # learned about the link I had just proved was in trouble, and every
        # other sender kept aiming at me at the old rate.
        #
        # The two directions are not independent evidence about the same wire.
        # If my uplink cannot carry this, my expectation of the downlink should
        # come down with it. Backing down is both ends or it is half a decision.
        return True

    # [GROWTH_IS_EARNED_V1] Clean windows required before the picture gets its
    # size back. One per step, and the count resets on any shed.
    #
    # Shrinking was evidence-driven and growing was a TIMER: _bottom_grow fired
    # whenever the ladder wanted above L5 and BOTTOM_STEP_S had elapsed, with no
    # requirement that the wire had been clean. Measured 2026-08-10, twice in
    # one call: shrink to 160x120, shed, bearer recovers, grow 320x240 ->
    # 480x360 -> 640x360 in six seconds, collapse again at 10 drops/s. The
    # asymmetry IS the oscillation -- the picture lost its size to evidence and
    # won it back to a clock.
    BOTTOM_CLEAN_WINDOWS = 3

    def _bottom_clean(self, sheds_delta):
        """One completed window's verdict, from the send loop."""
        if sheds_delta > 0:
            self._bottom_ok = 0
        else:
            self._bottom_ok += 1

    def _bottom_grow(self, now=None):
        """Step it back UP one, if the wire has earned it."""
        now = now or time.time()
        if self._bottom <= 0:
            return False
        if self._bottom_ok < self.BOTTOM_CLEAN_WINDOWS:
            return True          # not yet earned; stay where we are, on video
        if now - self._bottom_at < self.BOTTOM_STEP_S:
            return True
        self._bottom -= 1
        self._bottom_at = now
        self._bottom_ok = 0      # the next step must be earned again
        g = self.BOTTOM_GEOS[self._bottom]
        print("[%s] bottom rung -> %dx%d (%d clean windows)"
              % (self.name, g["w"], g["h"], self.BOTTOM_CLEAN_WINDOWS),
              flush=True)
        return True

    def _bitrate_for(self, w, h):
        """What a picture THIS SIZE is budgeted at, from the ladder tables.

        [THE_BITRATE_BELONGS_TO_THE_PICTURE_V1] Same lookup as _gray_for and
        for the same reason: the geometry is what is being sent, and the rung
        label no longer tracks it.
        """
        for g in list(BOTTOM_4_3) + [gg for a in RUNG_GEO
                                     for gg in RUNG_GEO[a].values()]:
            if int(g.get("w") or 0) == w and int(g.get("h") or 0) == h:
                if g.get("bitrate"):
                    return int(g["bitrate"])
        # Not a table size: scale from the nearest by pixel count, so a
        # geometry the tables do not name still gets a budget in proportion.
        best = None
        for g in [gg for a in RUNG_GEO for gg in RUNG_GEO[a].values()]:
            if not g.get("bitrate"):
                continue
            if best is None or abs(g["w"] * g["h"] - w * h) < abs(
                    best["w"] * best["h"] - w * h):
                best = g
        if not best:
            return 0
        return max(30_000, int(best["bitrate"] * (w * h)
                               / float(best["w"] * best["h"])))

    def _gray_for(self, w, h):
        """[GRAY_FOLLOWS_THE_PICTURE_V1] Does a picture THIS SIZE want gray?

        Every ladder entry of that geometry, in either aspect family and in the
        constrained walk. Gray is a bottom-end trade -- flat chroma planes cost
        almost nothing to code, so it buys real bits where bits are scarce --
        and it belongs to the small picture, not to whatever rung the local
        ladder happens to be labelled with.
        """
        for g in list(BOTTOM_4_3) + [gg for a in RUNG_GEO
                                     for gg in RUNG_GEO[a].values()]:
            if int(g.get("w") or 0) == w and int(g.get("h") or 0) == h:
                return bool(g.get("gray"))
        return False

    def _video_treatment(self, rung):
        """The geometry to encode at this rung.

        The rung entry is the starting point; the bottom rung's current step and
        a measured floor override it. [VIDEO_FLOOR_IS_MEASURED_V1] means the
        table no longer decides what is possible -- it only supplies a default
        until something has been sent.
        """
        base = dict(RUNG_VIDEO[rung])
        # [ASPECT_IS_CHOSEN_V1] geometry from the operator's family; the cost
        # (bitrate) is the rung's and does not depend on the shape.
        _g = (RUNG_GEO.get(self.aspect) or {}).get(rung)
        if _g:
            base.update({"w": _g["w"], "h": _g["h"]})
        base.setdefault("fps", self.fps)
        if rung <= min(RUNG_VIDEO):
            base.update(self.BOTTOM_GEOS[self._bottom])
            base.setdefault("fps", self.fps)
        # [SLOWEST_VIEWER_COMMANDS_V1] What a viewer told us to serve caps the
        # geometry at EVERY rung, not just the bottom one -- it is an
        # instruction about what gets through, and the rung the ladder happens
        # to be on does not change what the slowest viewer can take.
        #
        # Only ever DOWNWARD. A viewer asking for more than the ladder has
        # earned would be a viewer overruling this sender's own evidence about
        # its own uplink, and the two are different links.
        # [RECEIVE_RATE_IS_LINK_EVIDENCE_V1] Whichever is smaller: what the call
        # asked for, or what this link has been shown to carry INBOUND. Both are
        # caps and neither is permission.
        # [THE_SENDER_SENDS_AT_THE_RECEIVE_RATE_V1] When the call has published
        # a rate, that IS the operating point. Not a cap the local ladder is
        # min'd against -- the value.
        #
        # The sender and the receivers do not move independently. Receivers
        # measure what is arriving and write it into their own rows; every
        # participant reads the others and takes the minimum; everybody sends
        # that. Coordination is the tuple space, in real time, with nobody
        # asking anybody for anything.
        #
        # Treating it as one cap among several left the local ladder still
        # driving: the bearer would walk the rung down on its own evidence and
        # the geometry would follow, so two controllers moved the same knob and
        # disagreed. The local ladder's job is not to choose the rate. It is to
        # NOTICE -- and what it does when it notices is
        # [BACKING_DOWN_IS_BOTH_ENDS_V1]: publish a lower number, which lowers
        # the minimum, which every participant including this one then adopts.
        # One controller, mediated by memory.
        # [SEND_AT_WHAT_YOU_RECEIVE_V1] Before any consumer has reported, a
        # participant that both sends and receives starts SYMMETRIC: the same
        # geometry and rate in both directions.
        #
        # Even is the only defensible opening split. This end knows what it is
        # getting and nothing about how the other direction behaves until it
        # tries, and matching is the assumption that costs least when wrong --
        # too high and the far end says so within a window, too low and
        # everybody reports happy and the network climbs.
        # [GRAY_FOLLOWS_THE_PICTURE_V1] The gray flag belongs to the geometry
        # being sent, not to the rung label.
        #
        # base starts as dict(RUNG_VIDEO[rung]) and the geometry is then
        # replaced by the network rate -- so a sender whose local rung is L5
        # (flagged gray, because gray is a bottom-end trade) sent 1280x720 in
        # gray. Measured 2026-08-11: "rung L5 @ 1280x720", grayscale at every
        # size, for the whole call.
        #
        # Resolved at the end of this function against the geometry that is
        # actually going out: gray only where a ladder entry OF THAT SIZE asks
        # for it.
        _serve = getattr(self, "_serve_cap_geo", None)
        if not _serve and not self.is_producer:
            _p = getattr(self, "_producer_says", None) or {}
            if _p.get("w"):
                _serve = {"w": int(_p["w"]), "h": int(_p["h"]),
                          "fps": float(_p.get("fps") or self.fps)}
        if _serve:
            base["w"], base["h"] = int(_serve["w"]), int(_serve["h"])
            if float(_serve.get("fps") or 0) > 0:
                # The agreed rate is the target; the CAMERA is a hard limit. A
                # call that agrees 60 fps cannot make a 24 fps capture produce
                # 60, and quoting one would put a number on the wire that no
                # frame will ever match.
                base["fps"] = max(1, min(int(self.fps),
                                         int(round(float(_serve["fps"])))))

        # The operator's own setting for this call still applies, and the
        # inbound measurement still applies, but only DOWNWARD from there.
        # Neither is permission to exceed what the call agreed.
        c = self._cmd_geo
        for other in (getattr(self, "_rx_cap_geo", None),):
            if not other:
                continue
            if (not c or int(other["w"]) * int(other["h"])
                    < int(c.get("w", 1 << 30)) * int(c.get("h", 1))):
                c = other
        if c:
            try:
                cw, ch = int(c["w"]), int(c["h"])
                if cw * ch < base["w"] * base["h"]:
                    base.update({"w": cw, "h": ch})
                    if c.get("bitrate"):
                        base["bitrate"] = min(int(base["bitrate"]),
                                              int(c["bitrate"]))
                    if c.get("gray"):
                        base["gray"] = True
            except (KeyError, TypeError, ValueError):
                pass          # malformed rows are refused by pick_media_treatment
        # [THE_CONTRACT_IS_FPS_AND_RESOLUTION_V1] The serving cap carries a rate
        # as well as a size. Capping the geometry and leaving fps alone serves a
        # consumer that said "640x360 at 4 fps" six times what it asked for.
        _sc = getattr(self, "_serve_cap_geo", None)
        if _sc and float(_sc.get("fps") or 0) > 0:
            base["fps"] = min(int(base.get("fps") or self.fps),
                              max(1, int(round(float(_sc["fps"])))))
        g = self._floor_geo
        if g and rung <= min(RUNG_VIDEO) and g["w"] < base["w"]:
            # A measured floor smaller than where the walk down has reached
            # wins: it was proven on this wire.
            base.update(g)
        # [GRAY_FOLLOWS_THE_PICTURE_V1] resolved against the geometry going out
        base["gray"] = self._gray_for(int(base["w"]), int(base["h"]))
        # [THE_BITRATE_BELONGS_TO_THE_PICTURE_V1] Bits follow pixels.
        #
        # base starts as dict(RUNG_VIDEO[rung]) and the geometry is then
        # replaced by the call's rate -- but the BITRATE was left at the rung's.
        # The rung is a wire label and stopped moving with the picture, so a
        # sender at 160x120 kept L5's 300 kbps budget. Measured 2026-08-11:
        # 24 KB/s at 160x120, three times what that picture needs, and still
        # 7 drops/s -- overshooting the link at the smallest size there is,
        # which is why shrinking further never helped.
        #
        # The ladder entry OF THIS SIZE says what it costs. Use that.
        _bps = self._bitrate_for(int(base["w"]), int(base["h"]))
        if _bps:
            base["bitrate"] = _bps
        return base

    def start_floor_probe(self):
        """[VIDEO_FLOOR_IS_MEASURED_V1] Go and find out where video stops."""
        if self._floor is not None and self._floor.active:
            return False
        self._floor = VideoFloorProbe(aspect=self.aspect)
        self._floor.begin(self.sendq.audio_sheds)
        print("[%s] measuring the video floor over %.1fs: %s"
              % (self.name, self._floor.budget_s,
                 " -> ".join("%dx%d" % (c["w"], c["h"])
                             for c in self._floor.candidates)),
              flush=True)
        return True

    def _floor_settled(self, probe):
        """The walk finished. Apply what it measured."""
        self._floor_geo = probe.result
        for i, kb in probe.measured.items():
            c = probe.candidates[i]
            print("[%s]   %dx%d keyframe %.0fKB"
                  % (self.name, c["w"], c["h"], kb / 1024.0), flush=True)
        if probe.result is None:
            # Even the smallest candidate stalled. Video cannot pass -- and this
            # time that is a measurement, not a table lookup.
            self._set_ceiling("backlog", self.AUDIO_ONLY_IDX)
            print("[%s] video floor: NONE -- %s. Audio only, measured."
                  % (self.name, probe.why), flush=True)
        else:
            # Release the audio-only bound the walk started under. The rung it
            # releases to is the highest whose geometry the walk PROVED, and
            # nothing above that has been shown to pass.
            _lv = self.AUDIO_ONLY_IDX
            for idx in sorted(RUNG_VIDEO):
                r = RUNG_VIDEO[idx]
                if r["w"] * r["h"] <= probe.result["w"] * probe.result["h"]:
                    _lv = max(_lv, idx)
            self._set_ceiling("backlog", None if _lv >= self.ceiling else _lv)
            print("[%s] video floor: %dx%d -- %s (ceiling %s)"
                  % (self.name, probe.result["w"], probe.result["h"],
                     probe.why,
                     L.code(_lv) if hasattr(L, "code") else _lv), flush=True)
        self._floor = None

    def _ladder_from_backlog(self):
        """[KEYFRAME_BACKLOG_V1] Move the ceiling on relay evidence.

        DOWN as soon as any viewer has a keyframe held: the relay could not get
        the one frame that must not be dropped out, so this rung does not fit
        the link. Immediate -- a held keyframe already means the far end is
        watching pixellation.

        UP as a PROBE, not a commitment: after KF_PROBE_CLEAR_S with nothing
        backing up, raise the ceiling one rung and watch for KF_PROBE_WATCH_S.
        Backlog during the watch puts it straight back and restarts the clock.
        """
        now = time.time()
        cap = getattr(self, "_level_cap", None)
        if cap is None:
            cap = self.ceiling

        # [BACKLOG_IS_A_REPORT_NOT_A_LATCH_V1] CONSUME the report. One report,
        # one step.
        #
        # _kf_backlog is set by the KIND_BACKPRESSURE handler and was cleared by
        # NOTHING -- three references in the whole file: the initialiser, the
        # assignment, and this test. The relay only sends the frame when
        # _starved > 0; it never sends a zero to say "clear". So one report
        # latched the condition permanently, this ran on EVERY video tick, and
        # the ceiling walked one rung per tick to the floor.
        #
        # Measured 2026-08-08, a STATIC camera holding 23.8/24 fps at ~150 KB/s
        # with drops 0/s: seven consecutive steps, L7 -> L0, one per second,
        # after which no video is sent at all. And no way back -- each step
        # zeroes _kf_probe_at and _kf_clear_since, and the UP probe below
        # requires _kf_backlog to be falsy, which it could never become.
        #
        # The value is a LEVEL: how many viewers are starved RIGHT NOW, as of
        # the report that carried it. Reading a level as a standing condition is
        # what turned a single 2s-window observation into a monotonic descent.
        # Taking it here means the ladder can only move on EVIDENCE THAT ARRIVED
        # SINCE THE LAST MOVE, which is also what rate-limits the descent: the
        # relay reports at most once per BP_WINDOW_S per sender.
        # [AUDIO_BACKPRESSURE_V1] Audio is checked FIRST and separately, and it
        # does not stop where the video ladder stops.
        #
        # A keyframe backlog means the picture is too heavy: step the ceiling
        # down one rung and the picture gets lighter. Audio not arriving means
        # something worse -- the link cannot carry the FLOOR -- and the only
        # correct response is to take video out of the way ENTIRELY, not to walk
        # it down a rung at a time while the voice keeps breaking up. Doctrine:
        # video must be shut down COMPLETELY before a single audio frame is shed,
        # so when one has been shed, video goes.
        #
        # Consumed and staleness-checked exactly like the video report -- same
        # reasons, see [BACKLOG_IS_A_REPORT_NOT_A_LATCH_V1].
        _abacklog = self._audio_backlog
        self._audio_backlog = 0
        _aage = now - getattr(self, "_audio_backlog_at", 0.0)
        if _abacklog and _aage > self.BACKLOG_STALE_S:
            print("[%s] discarding a %.1fs-old audio backlog report "
                  "(%d viewer(s))" % (self.name, _aage, _abacklog), flush=True)
            _abacklog = 0
        if _abacklog:
            # [THE_MIC_IS_NOT_THE_LINK_V1] Audio that was never captured cannot
            # have failed to arrive.
            #
            # The relay reports "this viewer is not receiving your audio" and
            # this shuts video off completely -- correct when the LINK cannot
            # carry the floor. But a starved capture device produces the same
            # report: no audio was sent, so none arrived, and the relay has no
            # way to tell those apart. Measured 2026-08-11, repeatedly:
            #
            #   [AUD-CAP] device 1.5/s (want 50.0/s) ... input overflow
            #   AUDIO BACKPRESSURE ... video OFF (ceiling L8 -> L4)
            #
            # A USB microphone stalling blacked out a video link with plenty of
            # bandwidth, and then the picture had to climb back a rung at a time
            # through 8-second probes.
            #
            # Audio remains the floor. What changes is the diagnosis: if this
            # end is not producing audio, the fault is here and shutting video
            # down does not fix it.
            _cr = getattr(self, "_cap_rate", None)
            _cw = getattr(self, "_cap_want", 0.0) or 0.0
            _fresh = (time.time() - getattr(self, "_cap_at", 0.0)) < 5.0
            if _cr is not None and _fresh and _cw > 0 and _cr < 0.5 * _cw:
                if not getattr(self, "_said_mic", False):
                    self._said_mic = True
                    print("[%s] audio is not reaching %d viewer(s), but this "
                          "end only captured %.1f/s of %.0f/s -- the microphone "
                          "is starving, not the link. Video stays up; fix the "
                          "capture device."
                          % (self.name, _abacklog, _cr, _cw), flush=True)
                _abacklog = 0
            else:
                self._said_mic = False
        if _abacklog:
            _floor = self.AUDIO_ONLY_IDX
            if cap > _floor:
                # [CEILING_IS_A_MINIMUM_V1] a bound, not a direct write.
                self._set_ceiling("backlog", _floor)
                self._kf_probe_at = 0.0
                self._kf_clear_since = 0.0
                print("[%s] AUDIO BACKPRESSURE from the relay (%d viewer(s)) "
                      "-- video OFF (ceiling %s -> %s). Audio drives the "
                      "resolution: the floor does not give way to the picture."
                      % (self.name, _abacklog,
                         L.code(cap) if hasattr(L, "code") else cap,
                         L.code(_floor) if hasattr(L, "code") else _floor),
                      flush=True)
            else:
                # Already audio-only and STILL losing audio. Nothing above the
                # floor is left to give, and that is worth saying rather than
                # silently doing nothing on every report.
                print("[%s] AUDIO BACKPRESSURE from the relay (%d viewer(s)) "
                      "with video ALREADY off at %s -- the link cannot carry "
                      "the floor; nothing left for the ladder to shed"
                      % (self.name, _abacklog,
                         L.code(cap) if hasattr(L, "code") else cap), flush=True)
            return

        _backlog = self._kf_backlog
        self._kf_backlog = 0

        # A report that arrived long enough ago to be about a rung we have
        # already left is not evidence about this one. Without this, a report
        # delivered during a step is still acted on after the encoder has been
        # rebuilt at the lower rung.
        _age = now - getattr(self, "_kf_backlog_at", 0.0)
        if _backlog and _age > self.BACKLOG_STALE_S:
            print("[%s] discarding a %.1fs-old backlog report (%d viewer(s)) "
                  "-- it is about a rung this sender has already left"
                  % (self.name, _age, _backlog), flush=True)
            _backlog = 0

        # [ONE_STEP_PER_HOLD_V1] A held keyframe DOES tell this sender to back
        # off -- once per hold, not once per window the hold persists.
        #
        # The relay holds a keyframe when the viewer's socket will not take it,
        # and telling its own sender to send less is exactly the right response:
        # that is the control loop for this leg, and suppressing it entirely
        # (which I did for one revision) leaves a stuck viewer with no way to
        # say so.
        #
        # What was wrong is that the report fires on every window the SAME
        # keyframe stays held. One viewer that never drains produced six
        # consecutive reports and walked the sender L7 to L2 -- at 23.4/24 fps,
        # zero drops, 36 KB/s the whole way. The hold was one event; the sender
        # answered it six times.
        #
        # So: step once when a hold BEGINS, and not again until the backlog has
        # cleared and a new one starts. Same shape as one-step-per-complaint.
        if _backlog:
            if self._in_hold_episode:
                _backlog = 0          # already answered this one
            else:
                self._in_hold_episode = True
        else:
            self._in_hold_episode = False

        if _backlog:
            if cap > L.MIN_IDX:
                # [CEILING_IS_A_MINIMUM_V1] our bound, not the ceiling itself
                self._set_ceiling("backlog", cap - 1)
                if getattr(self, "_kf_probe_at", 0.0):
                    # This backlog answered a probe: that probe failed.
                    self._kf_probe_fails = getattr(self, "_kf_probe_fails", 0) + 1
                    # [RUNG_IS_MEASURED_V1] The failure is the measurement. We
                    # got there, we sent, it backed up -- so the numbers now in
                    # the window are that rung's REAL cost, not the scaled
                    # estimate that justified the attempt. Record them, and the
                    # next fit test is made against what actually happened.
                    _lv = getattr(self, "_kf_probe_lv", None)
                    if _lv is not None:
                        _snap = self.stats.snapshot()
                        self._rungs.note_failure(_lv, _snap)
                        _k, _src = self._rungs.keyframe_bytes(_lv)
                        # And the rate at which it broke is a refusal.
                        _tot = ((float(_snap.get("v_kbps") or 0.0)
                                 + float(_snap.get("a_kbps") or 0.0))
                                * 1024.0 * 8.0)
                        if _tot > 0 and (self._refused_bps <= 0
                                         or _tot < self._refused_bps):
                            self._refused_bps = _tot
                            self._refused_at = now
                        print("[%s] probe of %s failed -- recorded keyframe "
                              "%.0fKB (%s), refused at %.0f kbps"
                              % (self.name,
                                 L.code(_lv) if hasattr(L, "code") else _lv,
                                 _k / 1024.0, _src, _tot / 1000.0), flush=True)
                self._kf_probe_at = 0.0
                print("[%s] keyframes backing up at the relay (%d viewer(s)) "
                      "-- ceiling down to %s"
                      % (self.name, _backlog,
                         L.code(cap - 1) if hasattr(L, "code") else cap - 1),
                      flush=True)
            self._kf_clear_since = 0.0
            return

        # [BACKLOG_CLEAR_IS_THE_ABSENCE_OF_A_REPORT_V1] Start the clear clock.
        #
        # _kf_clear_since is the clock the climb below waits on, and it began at
        # 0.0 with exactly two things able to set it: a KIND_BACKPRESSURE frame
        # carrying ZERO starved viewers, and the probe-survived path. The relay
        # only sends that frame `if _starved:` -- a zero is never transmitted --
        # and the probe path needs a probe, which needs the climb, which needs
        # this clock. So once anything zeroed it, `if self._kf_clear_since` was
        # false forever: no probe was ever started and the rung never came back.
        #
        # Observed exactly that way: once the sender dropped to audio-only it
        # stayed there for the life of the call.
        #
        # The report is CONSUMED every tick, so reaching this line means no
        # backlog was reported since the last one. That absence IS the clear
        # signal -- there is nothing else for it to be, and waiting for a message
        # that is never sent is not a design, it is a stall.
        if not self._kf_clear_since:
            self._kf_clear_since = now

        # Backlog is clear. Was a probe in flight?
        probe_at = getattr(self, "_kf_probe_at", 0.0)
        if probe_at and (now - probe_at) >= self.KF_PROBE_WATCH_S:
            # [PROBE_IS_GRADED_ON_FRAMES_V1] A probe is gradeable only if the
            # probed rung actually carried frames.
            #
            # This used to pass on the absence of a backlog report alone. At L4
            # nothing is sent, so nothing can back up, so no report can exist --
            # and a rung that was never reached was scored as proven. Combined
            # with the raise below landing on a bound that did not bind, the
            # result was "probing L5" every 8 seconds for the life of the call
            # with 0 failed probes and the rung never moving. Absence of evidence
            # was being read as evidence, on a path where evidence was impossible
            # by construction.
            _sent = self.stats.snapshot().get("v_fps_sent") or 0.0
            if _sent > 0:
                self._kf_probe_at = 0.0      # survived the watch; it stands
                self._kf_probe_fails = 0     # [PROBE_BACKS_OFF_V1]
                self._kf_clear_since = now
                print("[%s] probe of %s held (%.1f fps through) -- it stands"
                      % (self.name, L.code(self._kf_probe_lv)
                         if hasattr(L, "code") else self._kf_probe_lv, _sent),
                      flush=True)
            else:
                # Nothing went out. That is not a pass and not a backlog either;
                # it means the raise never took effect. Say so and let the clear
                # clock restart rather than banking a false success.
                self._kf_probe_at = 0.0
                self._kf_clear_since = now
                print("[%s] probe of %s carried NO frames -- not graded "
                      "(ceiling is %s; something else is holding it)"
                      % (self.name,
                         L.code(self._kf_probe_lv) if hasattr(L, "code")
                         else self._kf_probe_lv,
                         L.code(cap) if hasattr(L, "code") else cap),
                      flush=True)
            return
        if probe_at:
            return                            # still watching

        # [PROBE_BACKS_OFF_V1] A probe that failed does not retry on the same
        # fixed interval forever.
        #
        # Observed: L4 -> probe L5 -> backlog -> L4 -> 8s -> probe L5 -> ... for
        # the life of the call, roughly every 12 seconds, each cycle spending a
        # keyframe and a second of video on a rung the relay has just said it
        # cannot carry. The ladder was right to retry -- conditions do change --
        # and wrong to retry at the same rate after being told no six times.
        #
        # Double the wait per consecutive failure, up to KF_PROBE_MAX_S, and
        # reset the moment a probe survives. Persistent congestion settles into a
        # cheap occasional check; a link that recovers is still found quickly.
        # [THE_RECEIVERS_DECIDE_V1] If the call has published a rate, the sender
        # does not go looking for a different one.
        #
        # Receivers measure what is arriving and write it; senders read the
        # minimum and conform. That is the whole loop, and it needs no
        # coordination -- each participant reads the others' tuples and adjusts
        # its own parameters.
        #
        # The keyframe probe is the sender's own controller, and with a
        # published cap in memory there were two things deciding at once. They
        # disagreed, and each probe was a burst on a link the receivers had just
        # said was slow. The probe is for when NOBODY has published: a call of
        # one, a peer that has not measured yet, a link with no history. Then it
        # is the only evidence there is.
        if getattr(self, "_serve_cap_geo", None):
            return

        _wait = min(self.KF_PROBE_MAX_S,
                    self.KF_PROBE_CLEAR_S * (2 ** getattr(self, "_kf_probe_fails", 0)))
        if not (self._kf_clear_since
                and (now - self._kf_clear_since) >= _wait
                and cap < self.ceiling):
            return
        _cand = cap + 1

        # [CEILING_IS_A_MINIMUM_V1] Would raising OUR bound actually move the
        # effective ceiling? _set_ceiling installs min(viewer, backlog, local),
        # so raising `backlog` while `local` or `viewer` sits lower changes
        # nothing at all -- and _set_ceiling does not even print, because
        # _level_cap did not change. That silent no-op is what produced "probing
        # L5" every 8 seconds against a local bound of 4, forever.
        _others = [b for b in (self._cap_viewer, self._cap_local)
                   if b is not None]
        _bind = min(_others) if _others else L.MAX_IDX
        if _bind < _cand:
            print("[%s] would probe %s but %s is held down by another "
                  "controller (viewer=%s local=%s) -- not spending a probe"
                  % (self.name,
                     L.code(_cand) if hasattr(L, "code") else _cand,
                     L.code(_bind) if hasattr(L, "code") else _bind,
                     "-" if self._cap_viewer is None else self._cap_viewer,
                     "-" if self._cap_local is None else self._cap_local),
                  flush=True)
            self._kf_clear_since = now
            return

        # [RUNG_IS_MEASURED_V1] Ask what that rung costs before spending a
        # keyframe on it. Measured keyframe size against the exact whole-frame
        # ceiling, measured need against a rate this wire has already refused.
        _ok, _why = self._probe_fits(_cand)
        if not _ok:
            print("[%s] not probing %s: %s"
                  % (self.name,
                     L.code(_cand) if hasattr(L, "code") else _cand, _why),
                  flush=True)
            # Not a failed probe -- nothing was tried, so the backoff does not
            # double. But the clear clock restarts so this is re-asked at the
            # same cadence rather than every tick.
            self._kf_clear_since = now
            return

        self._set_ceiling("backlog",
                          None if _cand >= self.ceiling else _cand)
        self._kf_probe_at = now
        self._kf_probe_lv = _cand
        print("[%s] no keyframe backlog for %.0fs (wait %.0fs, %d failed "
              "probe(s)) -- probing %s: %s"
              % (self.name, now - self._kf_clear_since, _wait,
                 getattr(self, "_kf_probe_fails", 0),
                 L.code(_cand) if hasattr(L, "code") else _cand, _why),
              flush=True)

    def set_throttle(self, bps):
        """DEMO: cap uplink throughput to `bps` bits/sec (0 = unlimited).

        A LINK CONDITION, not a ladder input. The writer paces the wire, the
        send queue sheds video, the bearer reads that as congestion and walks
        the rung down -- 720p -> 480p -> 360p -> voice. The dial simulates a
        smaller wire; discovering what a smaller wire can carry is the ladder's
        job, done the same way it would be on a real one.

        [SELF_CAP_FROM_LINK_V1] IS GONE. It read the dialed number, ran it
        through VIDEO_SHARE = 0.60 and RUNG_VIDEO's bitrate column, and wrote
        the answer straight into the ceiling as `_cap_local`. Consequences,
        measured:

          - 408,000 x 0.60 = 244,800 against L5's 300,000 floor, so a 408 kbps
            dial produced audio-only with no frame ever sent. A fact about a
            constant, presented as a fact about the wire.
          - the local bound then held the ceiling at L4 while the keyframe probe
            raised only the `backlog` bound, which min() discarded -- "probing
            L5" every 8 seconds for the life of the call, 0 failed probes, no
            movement.
          - and 0.60 stands in for audio, FNWP framing, TCP/IP overhead and
            keyframe burstiness with no term for any of them. At 408 kbps it
            reserved 163 kbps for costs that measure around 26.

        The reason it was added was real: the sender's uplink appeared to refuse
        nothing, because the bearer was reading sheds off the AUDIO plane. That
        is fixed -- take_video_sheds() on vsendq -- so the evidence path works
        and the shortcut is not needed.

        cap_to_link() and level_for_link() remain for the VIEWER path, where a
        number is a viewer's declared receive capacity rather than a simulated
        wire.
        """
        try: bps = int(bps)
        except (TypeError, ValueError): bps = 0
        self._throttle_bps = max(0, bps)
        # [TWO_PLANES_V1] Both planes. The demo throttle is a link condition, not
        # a per-stream one -- though only video is ever SHED by it (audio is the
        # floor and _over_budget is not consulted for it).
        for _q in (getattr(self, "sendq", None), getattr(self, "vsendq", None)):
            if _q:
                _q.throttle_bps = self._throttle_bps

    def set_jitter(self, ms):
        """DEMO: inject up to `ms` of random latency per frame on the uplink."""
        try: ms = int(ms)
        except (TypeError, ValueError): ms = 0
        self._jitter_ms = max(0, ms)
        for _q in (getattr(self, "sendq", None), getattr(self, "vsendq", None)):
            if _q:
                _q.jitter_ms = self._jitter_ms
        if False and getattr(self, "sendq", None):
            self.sendq.jitter_ms = self._jitter_ms

    # ---- capability + ladder ----
    def _probe(self):
        have_mic = not self.no_audio
        have_cam = False
        self._cap = None
        if not self.no_video:
            try:
                import cv2
                with _silence_cv2_stderr():
                    cap = open_camera(self.cam_idx)
                    opened = cap.isOpened()
                    ok = cap.read()[0] if opened else False
                if not opened:
                    print(f"[{self.name}] camera index {self.cam_idx} did not open "
                          f"(wrong index or busy). Try --list to see indices; video send disabled.", flush=True)
                    cap.release()
                else:
                    if ok:
                        have_cam = True; self._cap = cap
                    else:
                        print(f"[{self.name}] camera {self.cam_idx} opened but read() returned "
                              f"no frame (busy/permission). Video send disabled.", flush=True)
                        cap.release()
            except Exception as e:
                print(f"[{self.name}] camera probe error: {type(e).__name__}: {e!r}. "
                      f"Video send disabled.", flush=True)
        self.have_cam = have_cam
        # Verify the chosen video codec actually binds on this box; if not (e.g. h264hw on a
        # machine with no hardware encoder), fall back to software VP8 LOUDLY.
        if not self.no_video:
            try:
                import av
                av.CodecContext.create(CODEC_ENCODER[self.codec_id], "w")
                print(f"[{self.name}] video codec: {CODEC_NAME[self.codec_id]}", flush=True)
            except Exception as e:
                print(f"[{self.name}] codec {CODEC_NAME[self.codec_id]} unavailable "
                      f"({type(e).__name__}) -- falling back to {CODEC_NAME[CODEC_VP8]}", flush=True)
                self.codec_id = CODEC_VP8
        # receive/display does NOT require a local camera -- only sending does.
        self.want_video = not self.no_video
        self.have_mic = have_mic          # [VIDEO_DOES_NOT_NEED_A_MIC_V1] the shell names the
                                          # reason a ladder is pinned below L5
        self.allowed = L.allowed_levels(have_cam, have_mic, self.no_video, self.no_audio)
        # [QUALITY_CAP_V1] remember the full capability set so a live quality cap can be
        # raised back to Auto, then apply any cap already requested before TX started.
        self._allowed_full = set(self.allowed)
        self._apply_level_cap()
        self.ceiling = L.ceiling(have_cam, have_mic, self.no_video, self.no_audio)
        # [LADDER_CONSECUTIVE_V1] Pass the protected audio floor. Without it the floor
        # defaults to L.MIN_IDX and congestion can walk the ladder below the voice --
        # losing the call is worse than losing the picture.
        # [VIDEO_DOES_NOT_NEED_A_MIC_V1] The protected floor is the lowest AUDIO rung
        # this sender can actually source. With no mic there is no audio rung at all,
        # so the floor is simply the lowest rung available -- congestion may take the
        # video away, and there is no voice underneath it to preserve.
        _audio_rungs = [lv for lv in self.allowed if 3 <= lv <= 4]
        _audio_floor = min(_audio_rungs) if _audio_rungs else (
            min(self.allowed) if self.allowed else L.MIN_IDX)
        self.bearer = Bearer(self.ceiling, floor=_audio_floor)
        print(f"[{self.name}] capability: camera={have_cam} mic={have_mic} "
              f"ceiling={L.code(self.ceiling)} ({L.name(self.ceiling)})", flush=True)

    def run(self):
        import signal
        # [WIRE_LOG_V1] Opened before the planes, so a call that fails during
        # bring-up still leaves a file with a header rather than nothing.
        self._wire_log_open()
        # [TWO_PLANES_V1] Both sockets at connect, unconditionally. "No video" is
        # a DISPLAY state, not a teardown: the video plane stays open and simply
        # carries nothing, so a sender that starts muted can begin sending
        # without renegotiating anything.
        def _open_plane(tag):
            sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sk.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # [A_HANG_IS_THE_WORST_REPORT_V1] Bounded, and it says what it was
            # doing. A bare connect() to a relay that is not accepting waits on
            # the OS default -- minutes on Linux -- with the last line printed
            # being the wire log path. Observed 2026-08-11: the process sat
            # there until Ctrl-C, and the only evidence of where it was came out
            # in the traceback.
            #
            # A dead relay is an ordinary condition. It should read as one.
            sk.settimeout(self.RELAY_CONNECT_S)
            try:
                sk.connect((self.host, self.port))
            except (socket.timeout, OSError) as e:
                sk.close()
                # [REFUSED_AND_TIMED_OUT_ARE_DIFFERENT_FAULTS_V1] Say which,
                # because they point at different machines.
                #
                # Refused is instant and means the host is THERE and nothing is
                # listening -- the service. Timed out means the packets went
                # nowhere: a route, a tunnel, a firewall. Telling somebody to
                # check `systemctl status frognet-mediahost` on a host they
                # cannot reach sends them to the wrong end. Measured
                # 2026-08-11, after a tunnel-daemon restart.
                if isinstance(e, (socket.timeout, TimeoutError)):
                    _diag = ("timed out -- the packets are not arriving, so "
                             "this is the PATH, not the service. `ip route get "
                             "%s`, `wg show`, `ping -c2 %s`"
                             % (self.host, self.host))
                else:
                    _diag = ("%s -- the host answered, so it is reachable and "
                             "nothing is listening on :%s. `systemctl status "
                             "frognet-mediahost` on %s"
                             % (type(e).__name__, self.port, self.host))
                raise ConnectionError(
                    "%s plane: cannot reach the relay at %s:%s after %.0fs. %s"
                    % ("audio" if tag == PLANE_AUDIO else "video",
                       self.host, self.port, self.RELAY_CONNECT_S,
                       _diag)) from e
            # Back to no timeout. setblocking(False) below is what actually puts
            # this socket in non-blocking mode for the media loops
            # [ALL_NONBLOCKING_V1]; this only undoes the connect deadline.
            sk.settimeout(None)
            # Declare the plane while the socket is STILL BLOCKING, then switch.
            #
            # Order matters and getting it wrong is silent: send_frame() makes two
            # sendall() calls, and sendall() on a non-blocking socket raises
            # BlockingIOError if it cannot complete immediately. That exception
            # leaves run() before self.vdec is ever assigned, so the decoder stays
            # None for the life of the call and the tile painter has nothing to
            # paint -- with no error anywhere except the one diagnostic line
            # "vdec.tiles() failed: 'NoneType' object has no attribute 'tiles'".
            #
            # It would also write a length prefix with no payload if the first
            # sendall succeeded and the second did not, desyncing the stream
            # before the first media frame.
            # [TEARDOWN_IS_PER_SESSION_V1] The plane byte carries this process's
            # session tag after it. The relay reads _pl[:1] for the plane exactly as
            # before, and uses the remainder to tell THIS caller's two sockets from a
            # reconnect under the same name -- which used to be torn down by the
            # dying connection's own teardown.
            # [FAN_IS_PER_SESSION_V1] plane(1) + teardown tag(8) + call session.
            # Without the call session the relay has nothing to match on and
            # this connection is isolated -- by design, since a call in the
            # wrong session is worse than no call.
            send_frame(sk, pack_typed(
                KIND_PLANE, self.name,
                tag + self.session_tag
                + (self.session or "").encode("ascii", "replace")))
            sk.setblocking(False)                    # [ALL_NONBLOCKING_V1]
            return sk

        # A plane that cannot be opened is fatal and must say so. Failing here
        # silently is how the decoder ended up None with no explanation.
        try:
            self.sock = _open_plane(PLANE_AUDIO)     # audio plane
            self.vsock = _open_plane(PLANE_VIDEO)    # video plane
        except Exception as e:
            print(f"[{self.name}] FATAL: could not open media planes to "
                  f"{self.host}:{self.port}: {e!r}", flush=True)
            raise
        self.sendq = SotFDataPlane(self.sock)        # audio producers
        self.vsendq = SotFDataPlane(self.vsock)      # video producers
        # [AUDIO_INTENT_IS_SHARED_V1] Video watches the AUDIO plane's gate, so
        # [AUDIO_FIRST_V1] means something across two sockets instead of
        # yielding to a counter that can never be non-zero.
        self.vsendq.attach_audio_gate(self.sendq)
        for _q in (self.sendq, self.vsendq):
            _q.throttle_bps = getattr(self, "_throttle_bps", 0)
            _q.jitter_ms = getattr(self, "_jitter_ms", 0)
        print(f"[{self.name}] connected to relay {self.host}:{self.port} "
              f"(audio + video planes)", flush=True)
        self._probe()

        # Ctrl-C / TERM = clean hang-up: just set _stop and let threads unwind. (Without
        # this, a kill tears the socket out from under live C threads -> Aborted.)
        def _sig(signum, frame):
            print(f"\n[{self.name}] hanging up...", flush=True)
            self._stop.set()
        try:
            signal.signal(signal.SIGINT, _sig)
            signal.signal(signal.SIGTERM, _sig)
        except Exception:
            pass

        self._threads = []
        def spawn(target):
            t = threading.Thread(target=target, daemon=True); t.start()
            self._threads.append(t); return t

        spawn(self._recv_loop)                       # audio plane
        spawn(lambda: self._recv_loop(self.vsock))    # [TWO_PLANES_V1] video plane
        if not self.no_audio:
            spawn(self._audio)
        # [FLOOR_IS_PROVEN_NOT_GLIMPSED_V1] Start the call at audio-only and
        # prove upward.
        #
        # The call used to come up at the capability ceiling -- L7 if a camera
        # and mic exist -- and wait to be told otherwise. On a link that cannot
        # carry it, "otherwise" means a keyframe backlog at the relay, which is
        # a viewer already watching pixellation, and until then 720p is pushed
        # at a wire that will not take it. Assuming the best and being corrected
        # by someone else's degraded experience is backwards.
        #
        # L4 costs nothing to assume: audio is the floor and comes up either
        # way. Video then arrives a few seconds later at a size this wire has
        # been shown to carry, rather than immediately at a size it has not.
        #
        # Audio must be running first -- the stop condition IS an audio shed,
        # and a walk started before the audio thread exists cannot observe one.
        if self.want_video and self.have_cam and not self.no_audio:
            self._set_ceiling("backlog", self.AUDIO_ONLY_IDX)
            print("[%s] starting at %s -- video floor not yet measured"
                  % (self.name, L.code(self.AUDIO_ONLY_IDX)
                     if hasattr(L, "code") else self.AUDIO_ONLY_IDX), flush=True)
            self.start_floor_probe()
        elif self.want_video and self.have_cam:
            # No audio plane, so no stop condition. Say why rather than run a
            # walk that cannot fail.
            print("[%s] no audio: video floor cannot be measured (the stop "
                  "condition is an audio shed) -- starting at the capability "
                  "ceiling" % (self.name,), flush=True)
        # [SENDER_READS_MEDIASPEED_V1] Viewers' caps -> our rung.
        if self.session:
            spawn(self._poll_viewer_caps)
            # [SLOWEST_VIEWER_COMMANDS_V1] both halves: what we can take, and
            # what we have been told to serve.
            spawn(self._watch_inbound)
            spawn(self._poll_media_control)
            spawn(self._poll_consumer_feedback)
        else:
            print("[%s] no session id: viewer MediaSpeed caps will NOT be read, "
                  "so this sender cannot be asked to lower its resolution"
                  % (self.name,), flush=True)
        # [RECV_VIDEO_NOT_GATED_ON_TX_V1] Receiving video is NOT conditional on sending it.
        # A receive-only client (--no-video, no camera) must still build the decoder, or
        # every inbound KIND_VIDEO frame falls through _recv_loop's `vdec is not None` to
        # SKIP. Build vdec unconditionally; gate only the camera TX and the local display
        # window on send-side / display intent.
        self.vdec = VideoDecoder()
        if self.want_video and not self.no_display:
            self._display_thread = spawn(self._video_display)
        elif self.no_display:
            print(f"[{self.name}] display disabled (--no-display); sending/receiving only", flush=True)
        if self.want_video and self.have_cam:
            spawn(self._video_tx)
        elif not self.have_cam:
            print(f"[{self.name}] no local camera -- receive only "
                  f"(others will not see you).", flush=True)

        print(f"[{self.name}] on the call -- Ctrl-C to hang up"
              f"{'' if self.no_display else ' (or q/Esc in the video window)'}.", flush=True)
        try:
            while not self._stop.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            self._stop.set()
        finally:
            self._shutdown()

    def _shutdown(self):
        self._stop.set()
        # 1) stop the socket writer FIRST so nothing writes a closing fd
        try:
            if getattr(self, "sendq", None):
                self.sendq.close()
        except Exception: pass
        # 2) join the DISPLAY thread first so its on-thread OpenCV/Qt window teardown
        #    finishes before process exit (avoids Qt 'timer stopped from another thread').
        dt = getattr(self, "_display_thread", None)
        if dt is not None:
            try: dt.join(timeout=2.0)
            except Exception: pass
        # 3) let the remaining worker threads observe _stop and exit their C-library contexts
        for t in getattr(self, "_threads", []):
            if t is dt: continue
            try: t.join(timeout=1.5)
            except Exception: pass
        # 4) only now close the sockets -- SHUTDOWN FIRST.
        #
        # [HANG_UP_IS_NOT_A_FAULT_V1] close() on a socket with unread data in
        # its receive buffer sends RST, not FIN. Windows does this reliably, and
        # a client that has been receiving a media stream ALWAYS has unread
        # bytes at hangup. So every ordinary hang-up reached the relay as a
        # fault:
        #
        #   [relay] AUDIO OUT 10.250.250.20:49697 aimed 13 got 0 dropped 13
        #   [relay] caller left 10.250.250.20:49697 -- recv ended:
        #           ConnectionResetError: [Errno 104] Connection reset by peer
        #
        # Measured 2026-08-11, on every disconnection all day. shutdown(SHUT_WR)
        # sends FIN and lets the peer finish reading what is already in flight,
        # which is what "peer closed cleanly between frames" looks like from the
        # other end -- and that message is already in the relay, for the planes
        # that happened to be drained.
        for _s in (getattr(self, "sock", None), getattr(self, "vsock", None)):
            if _s is None:
                continue
            try:
                _s.shutdown(socket.SHUT_WR)
            except OSError:
                pass          # already gone: nothing to say goodbye with
            try:
                _s.close()
            except Exception:
                pass
        # 5) release the camera now that the capture thread has stopped reading it --
        #    otherwise /dev/videoN stays held after hang-up and blocks the next launch.
        cap = getattr(self, "_cap", None)
        if cap is not None:
            try: cap.release()
            except Exception: pass
            self._cap = None
        print(f"[{self.name}] hung up.", flush=True)

    # ---- audio: reuse the proven in-process path ----
    def _audio(self):
        import sounddevice as sd, queue as _queue
        try:
            di = sd.query_devices(self.in_dev, "input") if self.in_dev is not None else sd.query_devices(kind="input")
            do = sd.query_devices(self.out_dev, "output") if self.out_dev is not None else sd.query_devices(kind="output")
            out_rate = int(round(do["default_samplerate"]))
            # [ASK_THE_DEVICE_NOT_THE_DEFAULT_V1] default_samplerate is what
            # PortAudio ADVERTISES, not what the hardware will actually clock at.
            #
            # Measured on New-York-1, 2026-08-08: an eMeet C950 USB webcam mic
            # advertised 44100. The stream opened at 44100 with a 20ms block of
            # 882 samples, and PortAudio then delivered 17.7 callbacks/s against
            # the 50/s that implies, flagging nearly every one -- 76 flagged per
            # 5s window out of ~88 delivered. 17.7/50 = 0.354; 16000/44100 =
            # 0.363. The device was clocking at 16k while we asked for 44.1k, so
            # two thirds of the audio never existed. Nothing said so: the queue
            # was never full and the status flag was not being read.
            #
            # So ASK. check_input_settings() opens the device with the settings
            # and raises if they will not work, which is the only answer that is
            # not a guess. AUDIO_RATE is tried first because it is what the wire
            # wants -- getting it means no capture resampling at all.
            # [ASK_THE_DEVICE_NOT_THE_DEFAULT_V1] ADVERTISED rate first.
            #
            # The first version of this preferred AUDIO_RATE, on the theory that
            # it is what the wire wants and skips a resample. That fixed the box
            # whose advertised rate was a lie and BROKE the one whose was not:
            #
            #   NY1, eMeet C950   advertised 44100, clocks 16000
            #                     at 44100 -> 17.7 cb/s;  at 16000 -> 44.3 cb/s
            #   Windows, Razer Seiren Elite  advertised 44100, honours it
            #                     at 44100 -> 50.0 cb/s;  at 16000 ->  8.6 cb/s
            #
            # check_input_settings() cannot tell them apart: on Windows it
            # accepts 16000 and the host API resamples, so the call succeeds and
            # the device then delivers a fifth of the audio. "It opened" is not
            # "it works", and there is no static preference that suits both.
            #
            # So: believe the device, and let the operator override the ones that
            # lie. --audio-rate names the value; the [AUD-CAP] line measures the
            # result and says when the override is needed.
            _forced = getattr(self, "audio_rate", 0)
            _order = ([_forced] if _forced
                      else [int(round(di["default_samplerate"])), AUDIO_RATE])
            in_rate = None
            for _try in _order:
                try:
                    sd.check_input_settings(device=self.in_dev, channels=AUDIO_CH,
                                            dtype="int16", samplerate=_try)
                    in_rate = _try
                    break
                except Exception as _e:
                    print(f"[{self.name}] input device rejects {_try}Hz: {_e}",
                          flush=True)
            if in_rate is None:
                # Not a fallback: there is no rate this device will accept, so
                # there is no audio to capture and saying so is the whole job.
                print(f"[{self.name}] AUDIO CAPTURE UNAVAILABLE: device rejected "
                      f"{_order}", flush=True)
                return
            if in_rate != int(round(di["default_samplerate"])):
                print(f"[{self.name}] input FORCED to {in_rate}Hz, device "
                      f"advertises {int(round(di['default_samplerate']))}Hz "
                      f"({di['name']})", flush=True)
        except Exception as e:
            # [AUDIO_ERROR_NAMES_THE_CAUSE_V1] "Error querying device -1" is PortAudio's
            # no-default-device sentinel, not a lookup failure: sd.default.device is
            # (-1, -1) because this PROCESS enumerated nothing usable. Quoting the index
            # sent people installing PulseAudio, which does not help if the process
            # cannot reach a daemon -- and a root process has no user session to reach.
            # Say what was actually found, and by whom.
            print(f"[{self.name}] audio device error: {e!r}", flush=True)
            try:
                import getpass
                who = getpass.getuser()
            except Exception:
                who = "?"
            try:
                devs = sd.query_devices()
                ins = [(i, d["name"]) for i, d in enumerate(devs)
                       if d.get("max_input_channels", 0) > 0]
                outs = [(i, d["name"]) for i, d in enumerate(devs)
                        if d.get("max_output_channels", 0) > 0]
                apis = [a["name"] for a in sd.query_hostapis()]
                print(f"[{self.name}]   running as {who}; PortAudio host APIs: "
                      f"{apis or 'NONE'}", flush=True)
                print(f"[{self.name}]   inputs:  {ins or 'NONE'}", flush=True)
                print(f"[{self.name}]   outputs: {outs or 'NONE'}", flush=True)
                if not ins and not outs:
                    print(f"[{self.name}]   PortAudio can see no devices at all in this "
                          f"process. PulseAudio is per-user: a root process has no "
                          f"session to connect to. Run as a logged-in user, or name an "
                          f"ALSA device directly with --in hw:X,Y --out hw:X,Y "
                          f"(arecord -l / aplay -l to list).", flush=True)
                elif not ins:
                    print(f"[{self.name}]   no INPUT device: no microphone. Video is "
                          f"unaffected -- the stream simply carries no audio, and the "
                          f"far end is told so. Audio-only rungs (L3 VOICE, L4 SOLO) "
                          f"are unavailable.", flush=True)
            except Exception as e2:
                print(f"[{self.name}]   and enumeration itself failed: {e2!r}",
                      flush=True)
            return
        try:
            cap_rs = A.StreamResampler(in_rate, AUDIO_RATE)
            play_rs = A.StreamResampler(AUDIO_RATE, out_rate)
        except Exception as e:
            # [AUDIO_DEGRADES_TO_VIDEO_V1] A missing/broken resampler must not kill the
            # call -- keep video, drop audio, and say exactly what to install.
            print(f"[{self.name}] audio disabled: {e}", flush=True)
            return
        in_block = max(1, in_rate * self.block_ms // 1000)
        out_block = max(1, out_rate * self.block_ms // 1000)
        txq = _queue.Queue(maxsize=200)
        out_acc = bytearray()

        # [AUDIO_DROP_IS_LOUD_V1] Counters for the capture path. Audio is the
        # floor of the ladder and this is the one place it was thrown away in
        # silence.
        self._cap_in = 0        # callbacks PortAudio delivered
        self._cap_drop = 0      # blocks discarded because txq was full
        self._cap_status = 0    # callbacks PortAudio flagged (overflow etc.)
        self._cap_t0 = time.time()
        self._cap_last = 0.0
        self._cap_in_prev = 0
        self._cap_drop_prev = 0
        self._cap_status_prev = 0

        def on_in(indata, frames, t, status):
            # [AUDIO_DROP_IS_LOUD_V1] This was:
            #     try: txq.put_nowait(...)
            #     except _queue.Full: pass
            # A bare pass on the audio capture path. If the consumer drains
            # slower than the device produces, the queue sits permanently full
            # and exactly the consumer's rate survives -- a steady, silent
            # fraction of the feed, with no counter and no line anywhere.
            # Measured 2026-08-08 at the relay: Dave delivering 16.5 packets/s
            # against John's 50.0, i.e. 0.33 s of audio per second, and nothing
            # on Dave's console said a word.
            #
            # PortAudio's `status` was ignored here as well, while the OUTPUT
            # callback prints it -- so device overflow on the input was equally
            # invisible.
            self._cap_in += 1
            if status:
                self._cap_status += 1
                # [ASK_THE_DEVICE_NOT_THE_DEFAULT_V1] Say WHICH flag, once.
                # A count alone does not distinguish "the device overran because
                # we are reading too slowly" from "we asked for a rate it cannot
                # clock at", and those need opposite fixes.
                if not getattr(self, "_cap_status_named", False):
                    self._cap_status_named = True
                    print("  [AUD-CAP] PortAudio input status: %s" % (status,),
                          flush=True)
            try:
                txq.put_nowait(cap_rs.process(bytes(indata)))
            except _queue.Full:
                self._cap_drop += 1
            _now = time.time()
            if _now - self._cap_last >= 5.0:
                # [AUDIO_DROP_IS_LOUD_V1] THIS WINDOW, not since process start.
                #
                # It was self._cap_in / (now - self._cap_t0) -- a cumulative
                # average, so the second or two before the stream is running is
                # folded into every later reading and never ages out. A device
                # genuinely at 50/s reported 44.5 and crept upward for minutes;
                # I read that as an 11% shortfall and went looking at the
                # hardware. A meter that cannot show the present rate is worse
                # than none.
                _dt = _now - self._cap_t0
                _rate = (self._cap_in - self._cap_in_prev) / _dt if _dt > 0 else 0.0
                _drop_w = self._cap_drop - self._cap_drop_prev
                _stat_w = self._cap_status - self._cap_status_prev
                _sent = ((self._cap_in - self._cap_in_prev)
                         - _drop_w) / _dt if _dt > 0 else 0.0
                self._cap_in_prev = self._cap_in
                self._cap_drop_prev = self._cap_drop
                self._cap_status_prev = self._cap_status
                self._cap_t0 = _now
                # [THE_MIC_IS_NOT_THE_LINK_V1] Remember how the DEVICE did, so
                # the ladder can tell "the network did not carry my audio" from
                # "I never captured any".
                self._cap_rate = _rate
                self._cap_want = 1000.0 / self.block_ms
                self._cap_at = _now
                if _drop_w or _stat_w or _rate < 0.9 * (1000.0 / self.block_ms):
                    print("  [AUD-CAP] device %.1f/s (want %.1f/s), queued %.1f/s, "
                          "DROPPED %d on full queue (%d total), %d flagged by "
                          "PortAudio (%d total)"
                          % (_rate, 1000.0 / self.block_ms, _sent,
                             _drop_w, self._cap_drop,
                             _stat_w, self._cap_status), flush=True)
                # [MIXER_DAMAGE_IS_COUNTED_V1] The playout side had no instrument
                # at all: [SPK] output underrun cannot fire (pull_block always
                # returns a full block, padded), and mixer.trimmed was read
                # nowhere in this file. Both places the mixer damages the signal
                # now report here, on the window that already exists, and ONLY
                # when they are non-zero -- a quiet call stays quiet in the log.
                _m = self.mixer
                _dmg = _m.pads + _m.trims + _m.dry
                if _dmg != getattr(self, "_mix_dmg_last", 0):
                    self._mix_dmg_last = _dmg
                    print(_m.damage_report(), flush=True)
                self._cap_last = _now

        last_out = bytearray(2)            # last emitted sample, for fade-fill on underrun
        self._ur = 0                       # output underruns (accumulator couldn't fill)

        # [TAP_THE_THING_THAT_PLAYS_V1] Raw s16le at out_rate, mono. Convert with
        #   ffmpeg -f s16le -ar <out_rate> -ac 1 -i tap.raw tap.wav
        _tap = None
        _tap_path = os.environ.get("FROGNET_WAV_TAP")
        if _tap_path:
            _tap = open(_tap_path, "wb", buffering=1 << 16)
            print("[%s] WAV TAP -> %s (s16le mono @ %dHz)"
                  % (self.name, _tap_path, out_rate), flush=True)

        def on_out(outdata, frames, t, status):
            if status:                     # PortAudio flags device underflow/overflow HERE
                print(f"  [SPK] PortAudio status: {status}", flush=True)
            if getattr(self, "mute_speaker", False):
                # [QUIET_MODE_V1] Silence the room, but keep draining the mixer so
                # the playout buffer does not run away while muted. The transcript
                # still gets the audio: the tap is upstream of here.
                self.mixer.pull_block()
                outdata[:] = bytes(len(outdata))
                return
            need = len(outdata); guard = 0
            while len(out_acc) < need and guard < 12:
                out_acc.extend(play_rs.process(self.mixer.pull_block())); guard += 1
            if len(out_acc) >= need:
                chunk = bytes(out_acc[:need]); del out_acc[:need]
                outdata[:] = chunk
                last_out[:] = chunk[-2:]            # remember last sample
                # [TAP_THE_THING_THAT_PLAYS_V1] Exactly the bytes handed to the
                # device -- after the mixer, after play_rs, after the ducker.
                # Every stage upstream of here now has a counter and every one
                # of them reads clean while the artifact is audible, so the
                # remaining question is not "which stage" but "what does the
                # output actually look like". A 2-3 Hz artifact is unmistakable
                # in the waveform and its SHAPE says which stage made it:
                # notches = dropouts, an envelope = gain modulation, repeated
                # identical segments = a buffer being replayed. Off unless
                # FROGNET_WAV_TAP names a file.
                if _tap is not None:
                    try:
                        _tap.write(chunk)
                    except Exception:
                        pass
            else:
                self._ur += 1
                if self._ur % 20 == 1:
                    print(f"  [SPK] output underrun #{self._ur} "
                          f"(accumulator {len(out_acc)}/{need}B) -- buffer starving", flush=True)
                # underrun: emit what we have, then ease to silence by fading the LAST sample
                # rather than a hard jump to zero (the hard zero is what ticks).
                g = len(out_acc)
                outdata[:g] = bytes(out_acc)
                if g >= 2:
                    last_out[:] = bytes(out_acc[g - 2:g])
                del out_acc[:g]                     # keep accumulator (now empty), don't clobber state
                # fade the held sample across the gap
                samp = int.from_bytes(bytes(last_out), "little", signed=True)
                pos = g; n = (need - g) // 2; i = 0
                while pos + 1 < need:
                    val = int(samp * max(0.0, 1.0 - (i + 1) / max(1, n)))
                    outdata[pos:pos + 2] = int(val).to_bytes(2, "little", signed=True)
                    pos += 2; i += 1

        def sender():
            while not self._stop.is_set():
                try: pcm = txq.get(timeout=0.2)
                except _queue.Empty: continue
                if getattr(self, "mute_mic", False):
                    continue          # [QUIET_MODE_V1] captured and discarded, not sent
                g_now = self.ducker.mic_gain(self.mixer.last_peak)
                g_prev = getattr(self, "_last_gain", g_now)
                pcm = A.apply_gain_ramp(pcm, g_prev, g_now)   # ramp across block: no seam step
                self._last_gain = g_now
                self._aseq += 1
                try:
                    # [AUDIO_IS_OPUS_V1] One format byte, then the payload. The
                    # byte is not a negotiation -- both ends are FrogNet and both
                    # do Opus -- it is so a receiver can REFUSE something it was
                    # not expecting instead of playing noise. --pcm-audio is the
                    # only thing that ever sets AUDIO_FMT_PCM.
                    if self._pcm_audio:
                        self.sendq.put(
                            pack_typed(KIND_AUDIO, self.name,
                                       _KIND.pack(AUDIO_FMT_PCM) + pcm),
                            droppable=False)
                        self.stats.on_audio_sent(len(pcm) + 5)
                    else:
                        for _pkt in self._opus.encode(pcm):
                            blob = pack_typed(KIND_AUDIO, self.name,
                                              _KIND.pack(AUDIO_FMT_OPUS) + _pkt)
                            self.sendq.put(blob, droppable=False)
                            self.stats.on_audio_sent(len(blob) + 4)
                except Exception:
                    self._stop.set(); return

        threading.Thread(target=sender, daemon=True).start()

        # [CAPTURE_DOES_NOT_NEED_A_SPEAKER_V1] These were opened in ONE `with`.
        # A box with a mic and no output device therefore lost CAPTURE too: the
        # output stream raised, the with never entered, on_in never fired, and
        # the node sent nothing while [AUD-CAP] still read a healthy 47/s.
        # Measured 2026-08-15 on a node whose only devices were an input --
        # "PortAudioError('Error querying device -1')", outputs: NONE -- and
        # the far end read 0 audio in with no clue why.
        #
        # A camera node with a mic and no speaker is a NORMAL deployment.
        # Playout is optional; capture is not. Capture failing is still fatal
        # and still says so: [NO_FALLBACK_V1].
        streams = []
        try:
            streams.append(sd.RawInputStream(
                samplerate=in_rate, channels=AUDIO_CH, dtype="int16",
                blocksize=in_block, device=self.in_dev, latency="low",
                callback=on_in))
        except Exception as e:
            print(f"[{self.name}] audio CAPTURE open failed: {e!r} "
                  f"-- this node will send no audio", flush=True)
            self._stop.set()
            if _tap is not None:
                try: _tap.close()
                except Exception: pass
            return
        try:
            streams.append(sd.RawOutputStream(
                samplerate=out_rate, channels=AUDIO_CH, dtype="int16",
                blocksize=0, device=self.out_dev, latency=0.08,
                callback=on_out))
            _out_hz = out_rate
        except Exception as e:
            _out_hz = None
            print(f"[{self.name}] audio PLAYOUT open failed: {e!r} "
                  f"-- sending only, nothing will be heard on this box",
                  flush=True)
        try:
            for _s in streams:
                _s.start()
            print(f"[{self.name}] audio up ({in_rate}->{AUDIO_RATE}->"
                  f"{_out_hz if _out_hz else 'no playout'}Hz)", flush=True)
            while not self._stop.is_set():
                time.sleep(0.2)
        except Exception as e:
            print(f"[{self.name}] audio run failed: {e!r}", flush=True)
            self._stop.set()
        finally:
            for _s in streams:
                try: _s.stop(); _s.close()
                except Exception: pass
            if _tap is not None:
                try: _tap.flush(); _tap.close()
                except Exception: pass

    # ---- video TX: capture, gate on rung, encode VP8, send typed video frames ----
    def _video_tx(self):
        import cv2, queue as _queue
        enc = VideoEncoder(codec_id=self.codec_id); enc.fps = self.fps
        self._enc_ref = enc
        # [ASK_THE_CAMERA_FOR_MJPEG_V1] Negotiate the format, then say what was
        # actually agreed.
        #
        # Nothing here asked the camera for anything, so V4L2 handed back its
        # default: YUYV, uncompressed. 1920x1080 YUYV is ~3.1 MB per frame, and
        # USB 2.0 carries about 5 of those a second. That is a BUS limit and no
        # amount of CPU changes it -- the same camera in MJPEG does 30 at the
        # same size.
        #
        # Measured 2026-08-11: 1.0-2.5 fps at 1080p with zero drops and zero
        # sheds on an idle wire, which I wrongly read as the machine being
        # incapable. It was the pixel format nobody had asked about.
        #
        # Asked for, then READ BACK. A camera that refuses MJPEG is a fact worth
        # having on the screen rather than a mystery in the frame rate.
        try:
            _want_fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            self._cap.set(cv2.CAP_PROP_FOURCC, _want_fourcc)
            _top = (RUNG_GEO.get(self.aspect) or {}).get(max(RUNG_VIDEO)) or {}
            if _top.get("w"):
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(_top["w"]))
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(_top["h"]))
            self._cap.set(cv2.CAP_PROP_FPS, float(self.fps))
            _got = int(self._cap.get(cv2.CAP_PROP_FOURCC) or 0)
            _cc = "".join(chr((_got >> (8 * i)) & 0xFF) for i in range(4))
            _cw = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            _ch = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            _cf = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
            print("[%s] camera negotiated %s %dx%d @%.0f fps"
                  % (self.name, _cc or "?", _cw, _ch, _cf), flush=True)
            if _cc.upper() not in ("MJPG", "H264", "JPEG"):
                print("[%s] camera is giving %s, NOT MJPEG -- uncompressed at "
                      "%dx%d is about %.1f MB per frame, and USB 2.0 carries a "
                      "few of those a second. The frame rate you see will be a "
                      "BUS limit, not this machine's."
                      % (self.name, _cc or "an unknown format", _cw, _ch,
                         (_cw * _ch * 2) / 1e6), flush=True)
        except Exception as _e:
            print("[%s] could not negotiate a camera format: %r -- whatever the "
                  "driver defaults to is what you get" % (self.name, _e),
                  flush=True)

        # [A_SWALLOWED_SETTING_IS_A_MYSTERY_LATER_V1] BUFFERSIZE=1 is what keeps
        # read() returning the FRESHEST frame. Where the backend ignores it,
        # OpenCV queues frames internally and read() hands back ever-older ones
        # -- which is stale-then-flood, exactly the burst pattern a consumer
        # then reads as a failing link. Swallowing the failure meant the one
        # setting that governs that was invisible.
        try:
            _bs_ok = bool(self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1))
            _bs = self._cap.get(cv2.CAP_PROP_BUFFERSIZE)
        except Exception as _e:
            _bs_ok, _bs = False, "raised %s" % type(_e).__name__
        if not _bs_ok or (isinstance(_bs, float) and _bs > 1):
            print("[%s] camera BUFFERSIZE=1 NOT honoured (set=%s, reads back "
                  "%s) -- this backend queues frames, so capture will arrive in "
                  "bursts and the delivered rate will look like a failing link"
                  % (self.name, _bs_ok, _bs), flush=True)
        latest = {"frame": None, "t": 0.0}
        lk = threading.Lock()

        def grabber():
            # Drain the camera at FULL speed (NO sleep): the camera fills OpenCV's internal
            # buffer at its own fps; if we read slower, read() returns ever-older frames and
            # the backlog grows ~1s. Reading flat-out keeps only the freshest frame.
            _bad = 0
            _bad_at = 0.0
            _reads = 0
            _t0 = time.time()
            while not self._stop.is_set():
                ok, frame = self._cap.read()
                _reads += 1
                if not ok:
                    # [A_FAILED_READ_IS_NOT_NOTHING_V1] A camera that fails a
                    # fraction of its reads produces a stream with holes in it,
                    # and the 20ms sleep here turned each one into a gap nobody
                    # could see. Downstream that reads as a bursty link, and the
                    # consumer asks the whole network for a lower rate on it.
                    _bad += 1
                    if _bad in (1, 10, 100) or _bad % 500 == 0:
                        _el = max(1e-6, time.time() - _t0)
                        print("[%s] camera read FAILED %d time(s) of %d "
                              "(%.1f%%, %.1f reads/s) -- every one is a hole in "
                              "the stream"
                              % (self.name, _bad, _reads,
                                 100.0 * _bad / max(1, _reads), _reads / _el),
                              flush=True)
                    time.sleep(0.02)
                    continue
                self._cap_reads, self._cap_bad = _reads, _bad
                self._self_frame = frame
                with lk:
                    latest["frame"] = frame; latest["t"] = time.time()
        threading.Thread(target=grabber, daemon=True).start()

        vbytes = 0; vframes = 0; last_report = time.time()
        target_dt = 1.0 / self.fps
        next_t = time.time()
        max_age = 0.0
        sent_frames = 0            # [SOURCE_CANNOT_SUSTAIN_V1] warm-up guard
        t_first_send = 0.0
        last_reported_l = None     # [REASON_IS_FOR_A_CHANGE_V1]
        # [RATE_IS_MEASURED_AT_THIS_RUNG_V1] Frames actually sent at the CURRENT rung,
        # since the rung last changed. See below.
        win_rung = None
        win_t0 = time.time()
        win_n = 0
        while not self._stop.is_set():
            now = time.time()
            if now < next_t:
                time.sleep(min(0.005, next_t - now)); continue
            # [NO_CATCH_UP_BURST_V1] The next deadline is set from NOW, not from
            # the previous deadline. A cycle that overruns -- a 1080p software
            # encode on a Pi will -- therefore delays the next frame instead of
            # firing several back to back to "catch up". The cadence drifts
            # under load and never bursts, which is what is wanted: a burst is
            # indistinguishable downstream from a link that stalled and
            # recovered, and gets read as one.
            #
            # Checked while hunting the burstiness of 2026-08-11 and found NOT
            # to be the cause. The producer-side causes are above: a camera
            # backend that ignores BUFFERSIZE=1 and queues frames, and failed
            # reads leaving holes. Recorded here so the cadence is not "fixed"
            # into a catch-up loop by somebody looking for the same thing.
            next_t = now + target_dt
            with lk:
                frame = latest["frame"]; cap_t = latest["t"]
            if frame is None:
                continue
            age = now - cap_t                     # how stale is the frame we're about to send
            max_age = max(max_age, age)
            # [SOURCE_CANNOT_SUSTAIN_V1] Give the bearer the production signals too --
            # but only once there IS production to judge.
            #
            # Two guards, both learned the hard way. A cold start reads 0 fps because
            # nothing has been sent yet, and without the warm-up the bearer read that
            # as "unsustained 0/12" and walked the rung below L5 before a single frame
            # went out -- video never started at all. And below L5 no video is
            # produced by definition, so a zero rate there would ratchet to the floor
            # and stay.
            # [RATE_IS_MEASURED_AT_THIS_RUNG_V1] Judge a rung on frames sent AT THAT
            # RUNG, not on the global rolling meter.
            #
            # stats' v_fps_sent is v_sent/dt over a fixed window. When video has been
            # shed that window fills with zeros, so the instant video resumes the meter
            # still reads near nothing, the rule sees "below 10 fps", and it sheds
            # again. Measured on hardware: a box doing 24/24 at 1280x720 took one burst
            # of wire drops, fell to L4, and then sat there reporting 3/24 fps at
            # 640x360 -- a quarter of the pixels it had just been managing at full
            # rate, in cycles of twelve seconds shed and two seconds sending. The rung
            # was not failing; the measurement was.
            #
            # The window resets whenever the rung changes, so the rate always describes
            # the rung it is judging, and it needs a second of that rung to mean
            # anything.
            _cur = getattr(self, "_send_l", None)      # not set until the first pass
            _fps = None
            if _cur is not None and _cur >= 5 and win_rung == _cur:
                _elapsed = now - win_t0
                # A rung is judged only after a FULL settling window at it. One second
                # was not enough: the first second of a call contains the camera open
                # and the encoder's first frames, so it measured 6 fps and shed a rung
                # the box then held at 24/24 for the next minute. The same applies at
                # every rung change, not just the first -- the encoder is rebuilt for
                # the new geometry each time.
                if _elapsed >= WARMUP_S and win_n >= WARMUP_FRAMES:
                    _fps = win_n / _elapsed
            # [DOWNLINK_BACKPRESSURE_V1] The relay's drops are COUNTED but no
            # longer steer the ladder. Feeding them in made one capped viewer
            # walk the rung down for everybody on the call -- the transmitter
            # should send its best and let the relay serve each client what that
            # client's link allows. It cannot do that yet (see Part XV, ch. 50),
            # so until it can, a slow participant gets a slideshow rather than
            # everyone getting a smaller picture. That is the honest trade.
            # [KEYFRAME_BACKLOG_V1] Evidence beats arithmetic.
            #
            # A bitrate table says 93 kbps cannot carry video. The wire says
            # otherwise: at 93 kbps no keyframes were backing up, which means
            # the link WAS sustaining it. The right test is not what the rung is
            # nominally rated at -- it is whether the relay can get keyframes
            # out. If they back up, back off. If nothing has backed up for a
            # while, try the next rung for a second or two and watch.
            #
            # So the link-rate cap ([SELF_CAP_FROM_LINK_V1]) is now only the
            # OPENING GUESS, and this moves it from there on what actually
            # happens.
            # [THE_CONSUMER_OWNS_THE_RATE_V1] Once a producer/consumer link is
            # established, the consumer's report is the ONLY thing that moves
            # the rate. Three questions, asked by the consumer, nobody else:
            #
            #   1. am I getting everything at this rate?
            #   2. if so, long enough to say it is safe to promote?
            #   3. if not, command a lower one through the tuples.
            #
            # The local ladder is the answer for a sender with nobody listening.
            # The moment somebody IS listening, it stands down completely --
            # backlog controller included. Leaving it running is what made
            # "fairly smooth" become "diving for the floor": the consumer set a
            # rate, the bearer lowered the ceiling underneath it on its own
            # evidence, and the consumer then measured the result of that and
            # complained again.
            if not getattr(self, "_serve_cap_geo", None):
                self._ladder_from_backlog()
            _rd, self._remote_drops = self._remote_drops, 0
            if _rd and BEARER_DIAG:
                print("  [BEARER] relay shed %d frame(s) downstream "
                      "(not counted against this rung)" % _rd, flush=True)
            # [SHED_IS_READ_FROM_THE_QUEUE_THAT_SHED_IT_V1] vsendq, not sendq.
            # Video congestion is measured on the video plane; audio distress
            # arrives separately on audio_shed and must not also be counted here
            # as video drops.
            # [READ_A_COUNTER_NOBODY_ELSE_DRAINS_V1] The wire's evidence, from
            # the CUMULATIVE counter, diffed here.
            #
            # This first read take_video_sheds() and self._remote_drops -- both
            # of which are DRAINED by the readers above, take_video_sheds() into
            # the bearer and _remote_drops zeroed a few lines up. So the shrink
            # guard always saw zero, and printed "NO drops and NO sheds" on a
            # line immediately followed by "drops 12/s". Measured 2026-08-11: it
            # held 160x120 while the wire was refusing a dozen frames a second,
            # and reprinted forever because a window WITH drops reset the
            # said-it-once flag and the next window printed again.
            #
            # vsendq.sheds is the running total three readers already diff
            # against. Diffing is safe; draining is not, because somebody else
            # is always draining first.
            _vsheds = self.vsendq.take_video_sheds()
            _shed_total = int(getattr(self.vsendq, "sheds", 0) or 0)
            _wire_refused = max(0, _shed_total - self._shed_seen)
            self._shed_seen = _shed_total
            rung = self.bearer.sample(self.sendq.depth(),
                                      _vsheds,
                                      fps_sent=_fps, fps_target=float(self.fps),
                                      frame_age_s=age,
                                      audio_shed=self.sendq.take_audio_sheds())
            send_l = L.send_level(self.ceiling, rung, self.allowed)
            # [BACKING_DOWN_IS_BOTH_ENDS_V1] retired by
            # [PRODUCER_LEADS_CONSUMERS_REPORT_V1]. A sender losing a rung is
            # evidence about ITS UPLINK, and under the two-role model the rate
            # is set by what CONSUMERS report about what reaches them. A sender
            # publishing its own uplink trouble as a receive capability was the
            # peer model, and it is gone.
            #
            # The call site outlived the method it called and this loop died on
            # the first step down -- AttributeError, in a thread, so the process
            # kept running with no video. See test_names_oracle: it walks bare
            # names and an attribute call is not one, which is why nothing
            # caught it.
            self._last_send_l = send_l
            # [WIRE_LOG_V1] Every input the decision saw and what it produced,
            # once a second, whether or not anything is being printed. The
            # audio-only branch below `continue`s past the [VID-TX] line, so a
            # row emitted there would go silent at exactly the rungs worth
            # studying.
            self._wire_log_row(send_l, rung, _fps)
            if send_l != win_rung:           # [RATE_IS_MEASURED_AT_THIS_RUNG_V1]
                win_rung, win_t0, win_n = send_l, now, 0
            self._send_l = send_l            # expose current rung for UIs (stats panel)
            # [WINDOW_KNOWS_ITS_RUNG_V1] Label the window BEFORE the shed check.
            # Below L5 nothing else in this loop touches stats, so a window spent
            # entirely audio-only would otherwise carry no rung at all and the
            # reader would attribute its zeros to whatever rung came next.
            self.stats.note_level(send_l)
            # [RUNG_IS_MEASURED_V1] One fold per COMPLETED window, keyed on the
            # window's own sequence number so it happens exactly once and the
            # measurement is never half a window.
            _snap = self.stats.snapshot()
            _sq = _snap.get("seq")
            if _sq is not None and _sq != self._obs_seq:
                self._obs_seq = _sq
                _sh = self.vsendq.sheds
                _delta = _sh - self._obs_sheds
                self._observe_window(_snap, _delta)
                # [GROWTH_IS_EARNED_V1] the same window that teaches the model
                # is the one that earns the picture its size back
                self._bottom_clean(_delta)
                self._obs_sheds = _sh

            # [VIDEO_FLOOR_IS_MEASURED_V1] A floor walk in progress owns the
            # geometry and forces a keyframe: the keyframe IS the probe. It runs
            # ahead of the shed check below, because the whole point is to find
            # out whether video can pass at a size no rung names -- including
            # while the ladder thinks it is at L4.
            _probe_geo = self._floor.current() if self._floor else None
            if _probe_geo is not None:
                if not self._floor.ready():
                    continue
                _probe_geo.setdefault("fps", self.fps)
                _a0 = self.sendq.audio_sheds
                try:
                    _kf, _iskey = enc.encode(frame, _probe_geo, force_key=True)
                except Exception as e:
                    print("[%s] floor probe could not encode %dx%d: %s"
                          % (self.name, _probe_geo["w"], _probe_geo["h"], e),
                          flush=True)
                    self._floor.observe(True, _a0, 0)
                    continue
                _before = self.vsendq.sheds
                if _kf:
                    self.vsendq.put(pack_typed(KIND_VIDEO, self.name,
                                               pack_video(send_l, _kf,
                                                          self.codec_id,
                                                          is_key=True)),
                                    droppable=True)
                _shed = (self.vsendq.sheds - _before) > 0 or not _kf
                time.sleep(self._floor.SETTLE_S)
                self._floor.observe(_shed, self.sendq.audio_sheds, len(_kf))
                if self._floor.done:
                    self._floor_settled(self._floor)
                continue

            # [BOTTOM_RUNG_SHRINKS_V1] Before shedding video, try a smaller
            # picture. The ladder asking for below-L5 means 640x360 is too much,
            # not that video is impossible -- and the difference between those
            # two is the whole point. Only when the bottom list is exhausted is
            # audio-only the honest answer.
            # [THE_RATE_IS_COMMANDED_NOT_NEGOTIATED_V1] When the network has a
            # rate, the local ladder does not touch the picture.
            #
            # _video_treatment was taught to obey the rate, and the bottom walk
            # and the shed path were left acting on their own -- so three
            # controllers moved one knob. Measured 2026-08-11, all within two
            # seconds of each other:
            #
            #   network rate down to 854x480      <- the call's decision
            #   bottom rung -> 160x120            <- the local walk, alone
            #   [VID-TX] 0.0/24fps drops 24/s     <- the shed path, alone
            #
            # The rate is COMMANDED. A sender that has been told what to send
            # sends that, and its own trouble is reported, not acted on: that is
            # what [BACKING_DOWN_IS_BOTH_ENDS_V1] and the consumer reports are
            # for. Acting on it here as well is how "fairly smooth" became
            # "diving for the floor" in fifteen seconds.
            _commanded = getattr(self, "_serve_cap_geo", None)
            if _commanded:
                # The rate decides. Hold send_l at the video floor so the shed
                # path below does not turn the picture off underneath it -- a
                # commanded rate IS a picture, and a sender that cannot manage
                # it says so in its report rather than going dark on its own.
                if send_l < min(RUNG_VIDEO):
                    send_l = min(RUNG_VIDEO)
            elif (send_l < 5 and self.have_cam
                  and self._bottom_shrink(fps_now=_fps, drops=self._remote_drops,
                                          sheds=_wire_refused)):
                send_l = min(RUNG_VIDEO)
            elif send_l > min(RUNG_VIDEO) and self._bottom > 0:
                # The ladder wants to climb but we are still on a shrunken
                # bottom geometry. Give the picture its size back before
                # spending a rung.
                self._bottom_grow()
                send_l = min(RUNG_VIDEO)
                if self._bottom == 0:
                    # [THE_CLIMB_BACK_IS_A_CLIMB_V1] The bottom walk just
                    # finished. Hand back to the ladder AT THE BOTTOM RUNG, not
                    # to whatever ceiling it has been sitting on.
                    #
                    # While _bottom > 0 this clamp holds send_l at L5. The
                    # instant it reached 0 the clamp vanished and the bearer's
                    # ceiling applied on the very next frame -- and that ceiling
                    # never came down with the picture, because the shrink walk
                    # is geometry and the ceiling is rungs. Measured 2026-08-11:
                    #
                    #   bottom rung -> 640x360 (3 clean windows)
                    #   [VID-TX] 23.8/24fps,  29KB/s, rung L8 @ 1920x1080
                    #   [VID-TX] 23.8/24fps, 371KB/s, rung L8 @ 1920x1080
                    #
                    # 480x360 to 1080p in one frame, 29 KB/s to 371 KB/s on a
                    # link that had just finished shedding everything. Every
                    # step of the walk down was earned and the whole way back
                    # was free.
                    #
                    # Pin the ceiling to the bottom rung and let the keyframe
                    # probe climb it, one rung at a time, each one graded on
                    # frames that actually went. The way up is the same
                    # mechanism as the way down.
                    self._set_ceiling("backlog", min(RUNG_VIDEO))
                    print("[%s] bottom walk complete at %dx%d -- ceiling pinned "
                          "to %s; the climb back is by probe"
                          % (self.name,
                             RUNG_VIDEO[min(RUNG_VIDEO)]["w"],
                             RUNG_VIDEO[min(RUNG_VIDEO)]["h"],
                             L.code(min(RUNG_VIDEO))), flush=True)

            if send_l < 5:                        # <=L4: video shed (audio only)
                if time.time() - last_report >= 2.0:
                    print(f"  [VID-TX] video shed (rung {L.code(send_l)}); audio only", flush=True)
                    last_report = time.time()
                continue
            try:
                if enc.codec_id != self.codec_id:        # live codec switch requested
                    self.codec_id = enc.set_codec(self.codec_id)
                    self._codec_dry = 0
                # [KEYFRAME_ON_REQUEST_V1] Consume the relay's ask. The flag is not
                # cleared here: pict_type is advisory and a codec may ignore it, so
                # it stands until is_key comes back True below.
                _want_key = bool(self._force_key)
                # [VIDEO_FLOOR_IS_MEASURED_V1] Geometry comes from the measured
                # floor when there is one, otherwise from the rung entry. The
                # floor can name a size no rung has, which is the point.
                vp8, is_key = enc.encode(frame, self._video_treatment(send_l),
                                         force_key=_want_key)
                if not vp8 and enc.codec_id != CODEC_VP8:
                    self._codec_dry = getattr(self, "_codec_dry", 0) + 1
                    if self._codec_dry >= 10:
                        print(f"  [CODEC] {CODEC_NAME[enc.codec_id]} produced no frames -- "
                              f"reverting to {CODEC_NAME[CODEC_VP8]}", flush=True)
                        self.codec_id = enc.set_codec(CODEC_VP8); self._codec_dry = 0
                elif vp8:
                    self._codec_dry = 0
            except Exception as e:
                # active codec failed mid-stream: if it's not VP8, revert NOW (don't spin at 0fps)
                if enc.codec_id != CODEC_VP8:
                    print(f"  [CODEC] {CODEC_NAME[enc.codec_id]} encode failed "
                          f"({type(e).__name__}: {e}) -- reverting to {CODEC_NAME[CODEC_VP8]}", flush=True)
                    enc.codec_id = CODEC_VP8; enc.cfg = None; self.codec_id = CODEC_VP8
                else:
                    print(f"[{self.name}] encode error: {type(e).__name__}: {e!r}", flush=True)
                continue
            if not vp8:
                continue
            if _want_key and is_key:
                # Honoured. Only now is the request discharged.
                self._force_key = False
                if KEYREQ_DIAG:
                    print("  [KEYREQ] keyframe emitted for %d waiting viewer(s)"
                          % self._force_key_for, flush=True)
                self._force_key_for = 0
            self._vseq += 1; vframes += 1; vbytes += len(vp8)
            try:
                blob = pack_typed(KIND_VIDEO, self.name,
                                   pack_video(send_l, vp8, self.codec_id,
                                              is_key=bool(is_key)))
                # [SHED_IS_READ_FROM_THE_QUEUE_THAT_SHED_IT_V1] vsendq, not sendq.
                #
                # The put goes to self.vsendq (the video wire) and the shed count
                # was read from self.sendq (the AUDIO wire). A video frame the
                # video wire refused bumped vsendq.sheds, this computed shed == 0,
                # and the frame was recorded as SENT. So "drops 0/s" on the
                # [VID-TX] line was not evidence of anything: video sheds could
                # not appear there, by construction.
                before = self.vsendq.sheds
                # [ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1] the queue needs to know
                # which frames are worth waiting a few milliseconds for.
                # [VIDEO_IS_SEGMENTED_V1] The frame goes as a run of
                # mss-sized units, each yielding to waiting audio on the way
                # in, instead of one indivisible write that owns the shared
                # uplink until it drains.
                # [SEGMENT_ONLY_WHAT_WOULD_NOT_FIT_V1] A frame that fits the
                # send buffer whole goes whole. Segmenting is exposure: every
                # extra unit is another chance for the wire to refuse, and an
                # abandoned frame is a lost picture. That exposure is worth it
                # for a frame that would otherwise be shed outright or would
                # own the shared uplink until it drained -- and worth nothing
                # for a 3 KB inter frame that was never at risk.
                #
                # The threshold is the same one _put() already sheds against,
                # so the frames that get segmented are exactly the ones that
                # would otherwise have been thrown away.
                # [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1] the bound is how long
                # this write would own the uplink, measured, not a constant.
                if len(blob) <= self.vsendq.whole_frame_max():
                    self.vsendq.put(blob, droppable=True,
                                    is_key=bool(is_key))   # [TWO_PLANES_V1]
                else:
                    _k, _src, _pl = unpack_typed(blob)
                    self.vsendq.put_segmented(KIND_VSEG, _src, _pl,
                                              is_key=bool(is_key))
                shed = self.vsendq.sheds - before
                if shed > 0:
                    self.stats.on_video_dropped(shed)
                    # [WALK_THE_KEYFRAME_DOWN_UNTIL_IT_FITS_V1] A keyframe that
                    # will not go tells you the size is wrong, right now.
                    #
                    # Not a hard hunt: 1920 did not fit, try 1280, then 854,
                    # then 640. One frame each, so the whole walk is a fraction
                    # of a second -- against the old answer, which was to wait
                    # eight seconds for the next probe cycle while the viewer
                    # got nothing.
                    #
                    # An INTER frame that sheds is ordinary and costs one
                    # picture. A KEYFRAME that sheds costs every frame after it,
                    # because nothing behind it can be decoded. That is the one
                    # worth reacting to immediately.
                    #
                    # And the new size goes into the tuple as it is taken, so
                    # every other end stops exceeding it without being asked.
                    # [SHRINKING_A_BLOCKED_SOCKET_IS_NOT_A_FIX_V1] Walk down
                    # only while something is still getting through.
                    #
                    # At 0 fps and 0 KB/s with every frame dropping, the socket
                    # is refusing EVERYTHING and a smaller picture cannot help:
                    # 160x120 fails for the same reason 1920x1080 did. Measured
                    # 2026-08-11: the walk went 1280 to 160 while the wire was
                    # taking nothing at all, and then sat at 160x120 with 24
                    # drops a second -- having made the picture six times
                    # smaller for no reason and told the whole call to follow.
                    #
                    # A frame getting through is what makes the size the
                    # variable. When nothing is, the size is not the problem and
                    # the fix is elsewhere: the peer stopped reading, or the
                    # path is gone.
                    _moving = (self.stats.snapshot() or {}).get("v_kbps") or 0.0
                    if is_key and _moving <= 0.0:
                        if not getattr(self, "_said_blocked", False):
                            self._said_blocked = True
                            print("[%s] keyframe will not go and NOTHING is "
                                  "getting through (0 KB/s) -- not shrinking: a "
                                  "smaller picture cannot fix a socket that is "
                                  "taking nothing. The peer stopped reading, or "
                                  "the path is gone." % (self.name,), flush=True)
                    elif is_key and self._kf_walk_ok(now):
                        self._said_blocked = False
                        # [ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1] By here the
                        # keyframe has already been retried KEY_RETRIES times
                        # across several milliseconds of writability waiting, so
                        # this shed is not a momentarily-full buffer. Say what
                        # the failure actually was, because "keyframe would not
                        # fit" described a symptom and named no cause.
                        _room = _send_room(self.vsendq.sock)
                        print("[%s] keyframe shed after %d attempt(s): "
                              "EWOULDBLOCK, socket room=%s"
                              % (self.name, self.vsendq.KEY_RETRIES + 1,
                                 "unknown" if _room < 0 else "%dB" % _room),
                              flush=True)
                        _lad = geometry_ladder(self.aspect)
                        # NOT _t: that is bound 55 lines below this, so reading
                        # it here raised UnboundLocalError on the first keyframe
                        # shed -- in a thread, silently. The encoder's own
                        # current size is what was just refused.
                        _cur = (self._serve_cap_geo
                                or {"w": int(getattr(enc, "w", 0) or 0),
                                    "h": int(getattr(enc, "h", 0) or 0)})
                        if not _cur.get("w"):
                            _cur = dict(RUNG_GEO[self.aspect][max(RUNG_VIDEO)])
                        _px = int(_cur["w"]) * int(_cur["h"])
                        _next = None
                        for _g in _lad:
                            if _g["w"] * _g["h"] < _px:
                                _next = _g
                                break
                        # [A_SIZE_THAT_FAILED_STAYS_FAILED_V1] Remember it
                        # BEFORE stepping down, so the derivation path cannot
                        # put the call straight back on the size just refused.
                        _wait = self._kf_note_fail(_px, now)
                        if _next is not None:
                            self._serve_cap_geo = dict(_next)
                            self._force_key = True     # try one at the new size
                            print("[%s] keyframe would not fit at %dx%d -- "
                                  "trying %dx%d now (not in eight seconds)%s"
                                  % (self.name, _cur["w"], _cur["h"],
                                     _next["w"], _next["h"],
                                     ("" if not _wait else
                                      "; not retrying %dx%d for %.0fs (%d "
                                      "failures)" % (_cur["w"], _cur["h"], _wait,
                                                     self._kf_fail[_px]["n"]))),
                                  flush=True)
                            try:
                                self._publish_role(_next["w"], _next["h"],
                                                   float(self.fps), happy=None)
                            except Exception as _pe:
                                print("[%s] could not publish the new size: %r"
                                      % (self.name, _pe), flush=True)
                else:
                    self.stats.on_video_sent(len(vp8), is_key)
                    sent_frames += 1
                    win_n += 1
                    if t_first_send == 0.0:
                        t_first_send = time.time()
            except Exception:
                self._stop.set(); return
            if time.time() - last_report >= 2.0:
                st = self.stats.snapshot()
                # [REASON_IS_FOR_A_CHANGE_V1] Only annotate a report where the rung
                # actually moved. last_reason is set at the step and printing it every
                # two seconds labelled steady lines with a stale cause -- "below 10 fps
                # (6)" against a line reading 19 fps.
                _why = (getattr(self.bearer, "last_reason", "")
                        if send_l != last_reported_l else "")
                last_reported_l = send_l
                # the denominator is what we ASKED the pipeline for (--fps), not the
                # REF_FPS display constant: the report read "5/30fps" on a box running
                # at --fps 24, so the shortfall could not be judged from the line.
                _shown = _fps if _fps is not None else st.get('v_fps_sent', 0)
                # [ENCODED_SIZE_IS_MEASURED_V1] the encoder's own frame size,
                # and a loud mismatch if it is not what was ASKED FOR.
                #
                # This compared against RUNG_VIDEO[send_l] -- the table entry --
                # after the geometry stopped coming from the table. Every
                # honoured instruction then reported as a mismatch: measured
                # 2026-08-10, every frame of the bottom walk printed
                # "*** NOT the rung's 640x360 ***" while running exactly the
                # size it had been told to. A warning that fires on correct
                # behaviour is noise, and noise is worse than no warning: it
                # costs the NEXT diagnosis, not this one.
                #
                # Intent is the treatment. It is what the encoder was handed and
                # it is what last_encoded_wh should equal.
                _t = self._video_treatment(send_l) if send_l in RUNG_VIDEO else None
                _want = (int(_t["w"]), int(_t["h"])) if _t else None
                # [ENCODED_SIZE_IS_MEASURED_V1] `enc` is the local encoder this
                # loop uses; self._enc_ref is the same object, kept for the
                # teardown path. There is no self.venc -- I invented that name
                # and it took the tx thread down with an AttributeError.
                _got = getattr(enc, "last_encoded_wh", None)
                if _got is None:
                    _enc_wh = "?x? (encoder has produced no frame)"
                elif _want is not None and _got != _want:
                    _enc_wh = ("%dx%d  *** NOT the asked-for %dx%d ***"
                               % (_got[0], _got[1], _want[0], _want[1]))
                else:
                    _enc_wh = "%dx%d" % _got
                print(f"  [VID-TX] {_shown:.1f}/{self.fps:.0f}fps, "
                      f"{st.get('v_kbps',0):.0f}KB/s, drops {st.get('v_drop_fps',0):.0f}/s, "
                      f"rung {L.code(send_l)} @ {_enc_wh}"
                      f"{'  <- ' + _why if _why else ''}", flush=True)
                # [BEARER-DIAG] every input the ladder decision saw this sample.
                if BEARER_DIAG:
                    print(f"  [BEARER] {getattr(self.bearer, 'diag', '')}", flush=True)
                last_report = time.time(); max_age = 0.0

    def _video_display(self):
        import cv2, numpy as np
        win = f"FrogNet A/V -- {self.name}"
        try:
            cv2.namedWindow(win, cv2.WINDOW_NORMAL)  # resizable by the user
        except cv2.error as e:
            # OpenCV built without GUI support (no GTK/Qt) -- common on headless Pis.
            # Don't abort the whole process: fall back to headless (capture/send/recv
            # keep running). Use --no-display, or a Tk front-end (communicator_live.py).
            self.no_display = True
            print(f"[{self.name}] video window unavailable (OpenCV has no GUI support) -- "
                  f"running headless. Use --no-display or the Tk client to silence this.",
                  flush=True)
            return
        TW, TH = 320, 180                             # base tile size for compositing
        while not self._stop.is_set():
            imgs = []
            mine = getattr(self, "_self_frame", None)
            if mine is not None:
                t = cv2.resize(mine, (TW, TH))
                cv2.putText(t, f"{self.name} (you)", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 255), 1)
                cv2.rectangle(t, (0, 0), (TW - 1, TH - 1), (0, 220, 255), 1)
                imgs.append(t)
            tiles = self.vdec.tiles() if self.vdec else {}
            for src, img in sorted(tiles.items()):
                t = cv2.resize(img, (TW, TH))
                cv2.putText(t, src, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
                imgs.append(t)
            if imgs:
                grid = np.hstack(imgs) if len(imgs) > 1 else imgs[0]
                try:
                    _, _, ww, wh = cv2.getWindowImageRect(win)
                except Exception:
                    ww = wh = 0
                if ww > 0 and wh > 0:
                    gh, gw = grid.shape[:2]
                    scale = min(ww / gw, wh / gh)
                    if scale > 0:
                        grid = cv2.resize(grid, (max(1, int(gw * scale)), max(1, int(gh * scale))))
                if self.show_stats:
                    self._draw_overlay(grid, cv2)
                cv2.imshow(win, grid)
            key = cv2.waitKey(30) & 0xFF
            if key in (27, ord('q'), ord('Q')):    # Esc or q quits
                self._stop.set(); break
            elif key in (ord('e'), ord('E')):      # toggle efficiency overlay
                self.show_stats = not self.show_stats
            elif key in (ord('c'), ord('C')):      # cycle video codec live (skip known-bad)
                order = [CODEC_VP8, CODEC_H264_SW, CODEC_H264_HW]
                ref = getattr(self, "_enc_ref", None)
                bad = ref._failed_codecs if (ref is not None and hasattr(ref, "_failed_codecs")) else set()
                seq = [c for c in order if c not in bad]
                if len(seq) > 1:
                    i = seq.index(self.codec_id) if self.codec_id in seq else 0
                    self.codec_id = seq[(i + 1) % len(seq)]
        # tear the window down on THIS thread (where it was created) so Qt's timer isn't
        # stopped cross-thread at process exit; pump waitKey so Qt processes the destroy.
        try:
            cv2.destroyAllWindows()
            for _ in range(4): cv2.waitKey(1)
        except Exception: pass

    def _draw_overlay(self, img, cv2):
        s = self.stats.snapshot()
        if not s:
            return
        vfs = s.get("v_fps_sent", 0); vfr = s.get("v_fps_recv", 0); ref = s.get("ref_fps", REF_FPS)
        lines = [
            ("WIRE THROUGHPUT (e/c toggle)", (0, 255, 180)),
            (f"codec {CODEC_NAME.get(self.codec_id,'?')}  (c to switch)", (0, 220, 255)),
            (f"video send {vfs:.0f}/{ref:.0f} fps   recv {vfr:.0f} fps",
             (0, 230, 0) if vfs >= ref - 1 else (0, 200, 255)),
            (f"drops {s.get('v_drop_fps',0):.0f}/s   video {s.get('v_kbps',0):.0f} KB/s   "
             f"audio {s.get('a_kbps',0):.0f} KB/s", (220, 220, 220)),
            (f"keyframe {s.get('key_kb',0):.1f}KB   delta {s.get('delta_kb',0):.1f}KB   "
             f"(k={s.get('key_n',0)} d={s.get('delta_n',0)})", (220, 220, 220)),
        ]
        h = 18 * len(lines) + 10
        ov = img.copy()
        cv2.rectangle(ov, (0, 0), (420, h), (0, 0, 0), -1)
        cv2.addWeighted(ov, 0.55, img, 0.45, 0, img)
        y = 20
        for txt, color in lines:
            cv2.putText(img, txt, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
            y += 18

    # ---- recv demux: route typed frames to audio mixer or video decoder ----
    def _recv_loop(self, sock=None):
        # [TWO_PLANES_V1] One loop per plane. Both feed the same decoder and the
        # same jitter buffer -- the split is about who WAITS on whom, not about
        # keeping the streams apart once they arrive.
        #
        # [RX_SAYS_WHERE_V1] This loop used to end with one line naming only the
        # exception, and BOTH planes printed the identical text. Field logs were
        # therefore two indistinguishable copies of
        #
        #     [RX] receive loop ended: ConnectionResetError(104, ...)
        #
        # from which it is impossible to tell which plane died, which died FIRST,
        # whether either had ever received anything, or whether the loop was simply
        # doing as it was told because _stop was already set. Every one of those
        # questions had to be answered by guesswork for days.
        #
        # Three swallows are removed with it: a frame that would not unpack was
        # `except Exception: continue` -- silent, and a desynced length prefix makes
        # EVERY subsequent frame fail that way, so a stream that has gone to garbage
        # looks exactly like a quiet one. An AbortedFrame was counted nowhere. And a
        # frame from ourselves was dropped with no record.
        sock = self.sock if sock is None else sock
        plane = "video" if sock is getattr(self, "vsock", None) else "audio"
        t0 = time.time()
        n_frames = n_bytes = n_aborted = n_unpack_fail = n_self = 0
        last_at = 0.0
        try:
            _fd = sock.fileno()
        except Exception:
            _fd = -1
        try:
            while not self._stop.is_set():
                try:
                    frame = recv_frame(sock)
                except AbortedFrame as _ab:
                    # [ABORT_SENTINEL_V1] One frame lost, stream intact. Counted now:
                    # a sender abandoning frames is a real condition and it was
                    # invisible from this end.
                    n_aborted += 1
                    if n_aborted in (1, 10, 100) or n_aborted % 1000 == 0:
                        print("  [RX/%s] sender aborted %d frame(s) so far (%s)"
                              % (plane, n_aborted, _ab), flush=True)
                    continue
                n_frames += 1
                n_bytes += len(frame)
                last_at = time.time()
                # [DROP_THE_FRAME_NOT_THE_CALL_V1] Everything from here on is
                # HANDLING one frame, and a failure in it is that frame's
                # problem.
                #
                # The whole loop sat inside one `except Exception` at the
                # bottom, so a decoder refusing a packet, a malformed
                # backpressure record, or any other per-frame fault ended the
                # receive loop -- and one plane ending tears down the call. A
                # single bad frame hung up on a working link.
                #
                # Only a socket error means there is nothing left to read; that
                # still propagates. Anything else: drop the frame and move on.
                try:
                    kind, src, payload = unpack_typed(frame)
                except Exception as _ue:
                    # NOT silent. A length prefix that has desynced makes every
                    # frame after it unparseable, and a stream in that state must
                    # not read as an idle one.
                    n_unpack_fail += 1
                    if n_unpack_fail in (1, 10, 100) or n_unpack_fail % 1000 == 0:
                        print("  [RX/%s] UNPARSEABLE frame #%d (%d bytes): %s: %s "
                              "-- if this keeps climbing the stream has desynced"
                              % (plane, n_unpack_fail, len(frame),
                                 type(_ue).__name__, _ue), flush=True)
                    continue
                if src == self.name:
                    n_self += 1
                    continue

                # [RX_IS_PER_SOURCE_V1] Count what is ARRIVING, per sender, per
                # kind, HERE -- before the decoder and independent of it.
                #
                # rx_dims came off vdec.tiles(), so with --no-display there is no
                # decoder and the only per-source incoming detail was blank on
                # every row. The aggregate v_kbps_rx says a number and not WHO or
                # AT WHAT RUNG, and the rung is the thing in question: measured
                # 2026-08-08, a 365,000 bps cap while 147 KB/s of video kept
                # arriving -- 1.2 Mbps, 3.3x the cap, and nothing anywhere caused
                # the far end to back off.
                #
                # The level byte is in the frame. unpack_video() reads it without
                # decoding a single pixel, so the incoming rung is knowable on a
                # headless receiver.
                with self._rx_lock:
                    _rx = self._rx_src.setdefault(src, {
                        "v_n": 0, "v_b": 0, "a_n": 0, "a_b": 0,
                        "lvl": None, "key": 0})
                    # [SLOWEST_VIEWER_COMMANDS_V1] A SEPARATE cumulative count.
                    # _rx_src is drained wholesale by the wire log every window,
                    # so a second reader that shares it steals the first one's
                    # evidence -- the same trap as take_sheds() zeroing a
                    # counter three readers diff against.
                    self._in_src[src] = self._in_src.get(src, 0) + (
                        1 if kind == KIND_VIDEO else 0)
                if kind == KIND_VIDEO:
                    _rx["v_n"] += 1
                    _rx["v_b"] += len(frame) + 4
                    try:
                        _l, _c, _d = unpack_video(payload)
                        _rx["lvl"] = _l
                        if video_is_key(payload):
                            _rx["key"] += 1
                    except Exception:
                        pass          # counted above; shape is the framing layer's
                elif kind == KIND_AUDIO:
                    _rx["a_n"] += 1
                    _rx["a_b"] += len(frame) + 4

                # [DROP_THE_FRAME_NOT_THE_CALL_V1] Everything below handles ONE
                # frame, and a failure in it is that frame's problem.
                #
                # The whole loop sat inside a single `except Exception` at the
                # bottom, so a decoder refusing a packet, a malformed
                # backpressure record, or any other per-frame fault ended the
                # receive loop -- and one plane ending tears the call down. A
                # single bad frame hung up on a working link.
                #
                # Only a socket error means there is nothing left to read, and
                # that still propagates. Anything else: drop the frame, say so
                # at a rate nobody has to scroll past, and read the next one.
                try:
                    if kind == KIND_AUDIO:
                        # [RECEIVE_IS_MEASURED_V1] count it before the mixer, so a muted
                        # or tapped stream still reports what arrived on the wire.
                        self.stats.on_audio_recv(len(frame) + 4)
                        if payload:
                            # [AUDIO_IS_OPUS_V1] Format byte first. An unknown value
                            # is REFUSED, not guessed at: decoding Opus as PCM (or
                            # the reverse) produces noise at full volume in
                            # somebody's ear, and "play it and hope" is the worst
                            # possible answer for a format we do not recognise.
                            _fmt = _KIND.unpack_from(payload, 0)[0]
                            payload = payload[_KIND.size:]
                            if _fmt == AUDIO_FMT_OPUS:
                                payload = self._opus.decode(payload)
                            elif _fmt == AUDIO_FMT_PCM:
                                if not self._warned_pcm_rx:
                                    self._warned_pcm_rx = True
                                    print("[%s] %s is sending UNCOMPRESSED audio "
                                          "(256 kbps). That is ~11x the wire cost of "
                                          "Opus and will starve video on any capped "
                                          "link." % (self.name, src), flush=True)
                            else:
                                if not self._warned_fmt.get(_fmt):
                                    self._warned_fmt[_fmt] = True
                                    print("[%s] REFUSING audio from %s: unknown "
                                          "format byte %d. Not decoding it as "
                                          "anything." % (self.name, src, _fmt),
                                          flush=True)
                                continue
                        if payload:
                            # [AUDIO_TAP_V1] Optional listener on RECEIVED audio, taken
                            # before the mixer so it is the far side's speech at
                            # AUDIO_RATE mono int16 and is unaffected by local playback
                            # muting. Used by quiet mode to transcribe. No tap set, no
                            # cost; a tap that raises must never take the call down.
                            tap = getattr(self, "audio_tap", None)
                            if tap is not None:
                                try:
                                    tap(src, payload)
                                except Exception:
                                    pass
                            self.mixer.feed(src, payload)
                    elif kind == KIND_KEYREQ:
                        # [KEYFRAME_ON_REQUEST_V1] The relay is holding a keyframe for
                        # one or more viewers, or shedding their inters while they wait
                        # for one. Arm the encoder: the NEXT frame out is an I-frame.
                        # Cleared by the pump once a keyframe actually goes on the wire,
                        # not here -- pict_type is advisory and the request must stand
                        # until the encoder has honoured it.
                        try:
                            _n = _LEN.unpack_from(payload, 0)[0]
                        except Exception:
                            _n = 1
                        self._force_key = True
                        self._force_key_for = int(_n)
                    elif kind == KIND_BACKPRESSURE:
                        # [DOWNLINK_BACKPRESSURE_V1] The relay shed this many video
                        # frames for a capped viewer. Our own uplink is clean, so
                        # without this the bearer would hold its rung while the far
                        # end watched a slideshow. Count them as our drops: the
                        # ladder's job is to make the picture fit the LINK, and the
                        # relay's downlink is part of the link.
                        # [KEYFRAME_BACKLOG_V1] How many viewers the relay is
                        # holding a keyframe for. THIS is the evidence the ladder
                        # acts on -- not a bitrate table. A link that can sustain a
                        # rung shows no held keyframes whatever its nominal rate;
                        # one that cannot shows them at once.
                        try:
                            _n = _LEN.unpack_from(payload, 0)[0]
                        except Exception:
                            _n = 0
                        self._kf_backlog = int(_n)
                        # [BACKLOG_IS_A_REPORT_NOT_A_LATCH_V1] Stamp it. The ladder
                        # discards a report old enough to be about a rung already
                        # left, and cannot do that without knowing when it landed.
                        self._kf_backlog_at = time.time()
                        if _n:
                            self._kf_clear_since = 0.0
                        elif not self._kf_clear_since:
                            self._kf_clear_since = time.time()
                    elif kind == KIND_AUDIO_BACKPRESSURE:
                        # [AUDIO_BACKPRESSURE_V1] The relay could not get this
                        # sender's AUDIO to a viewer.
                        #
                        # Bearer.sample already ranks audio first -- `if audio_shed:`
                        # ahead of drops and fps -- but only for audio this box's OWN
                        # uplink queue refused. Audio lost DOWNSTREAM reached nothing.
                        # [NO_FALLBACK_V1] An unreadable payload is NOT "zero
                        # starved viewers". Defaulting to 0 would silently discard a
                        # report the relay went to the trouble of sending, and the
                        # sender would keep video up while audio was being lost.
                        # Say it and drop the frame.
                        try:
                            _an = _LEN.unpack_from(payload, 0)[0]
                        except Exception as _e:
                            print("[%s] REFUSING malformed AUDIO_BACKPRESSURE from "
                                  "the relay: %r (%d byte payload) -- not treating "
                                  "it as 'no backpressure'"
                                  % (self.name, _e, len(payload or b"")), flush=True)
                            continue
                        self._audio_backlog = int(_an)
                        self._audio_backlog_at = time.time()
                        if _an:
                            print("[%s] AUDIO not reaching %d viewer(s) at the relay "
                                  "-- audio is the floor; video comes down"
                                  % (self.name, _an), flush=True)
                    elif kind == KIND_VSEG:
                        # [VIDEO_IS_SEGMENTED_V1] Rebuild the frame from its
                        # units. ONE frame is open per source at a time, so a
                        # unit carrying a NEW frame id implicitly abandons an
                        # incomplete older one -- that is what makes the id
                        # load-bearing and the explicit abort an optimisation
                        # rather than the only exit. A unit that does not
                        # follow is DISCARDED with its frame, loudly counted,
                        # never guessed at: [NO_FALLBACK_V1].
                        if len(payload) < _VSEG.size:
                            self._vseg_bad = getattr(self, "_vseg_bad", 0) + 1
                            continue
                        _fid, _ix, _fl = _VSEG.unpack_from(payload, 0)
                        _body = payload[_VSEG.size:]
                        _asm = getattr(self, "_vseg", None)
                        if _asm is None:
                            _asm = self._vseg = {}
                        _cur = _asm.get(src)
                        if _fl & VSEG_ABORT:
                            if _cur is not None:
                                _asm.pop(src, None)
                                self._vseg_aborted = getattr(
                                    self, "_vseg_aborted", 0) + 1
                            continue
                        if _cur is None or _cur[0] != _fid:
                            if _ix != 0:
                                # joined mid-frame, or the head was shed --
                                # wait for the next frame rather than decode a
                                # fragment.
                                self._vseg_partial = getattr(
                                    self, "_vseg_partial", 0) + 1
                                _asm.pop(src, None)
                                continue
                            if _cur is not None:
                                self._vseg_superseded = getattr(
                                    self, "_vseg_superseded", 0) + 1
                            _asm[src] = (_fid, 1, [_body])
                        else:
                            _f, _next, _parts = _cur
                            if _ix != _next:
                                # a unit was shed in the middle: this picture is
                                # unrecoverable, drop it whole.
                                self._vseg_gap = getattr(
                                    self, "_vseg_gap", 0) + 1
                                _asm.pop(src, None)
                                continue
                            _parts.append(_body)
                            _asm[src] = (_f, _next + 1, _parts)
                        if _fl & VSEG_LAST:
                            _f, _n, _parts = _asm.pop(src)
                            payload = b"".join(_parts)
                            kind = KIND_VIDEO          # fall through as a frame
                            self._vseg_done = getattr(
                                self, "_vseg_done", 0) + 1
                        else:
                            continue
                        # [NO_DISPLAY_IS_NOT_NO_DECODER_V1] Feed the decoder
                        # whenever there IS one. self.no_display means "fnav
                        # must not open its own cv2 window" -- it does NOT mean
                        # nobody wants decoded frames. communicator_live.py runs
                        # fnav with --no-display precisely because Tk does the
                        # painting, and it reads its pictures out of
                        # vdec.tiles(). Gating the feed on no_display therefore
                        # blacks out the Tk client while the counters happily
                        # report video arriving. Measured 2026-08-16, by
                        # shipping exactly that.
                        if self.vdec is None:
                            continue
                        _lvl, _cid, data = unpack_video(payload)
                        self.vdec.feed(src, _cid, data)
                        self.stats.on_video_recv(len(payload) + 4)
                        self._vrx = getattr(self, "_vrx", 0) + 1
                        continue

                    elif kind == KIND_VIDEO and self.vdec is not None:
                        _lvl, _cid, data = unpack_video(payload)
                        if VDEC_DIAG:
                            # [VDEC_DIAG_V1] What actually came off the wire, before the
                            # decoder sees it. recv_frame() reads a length prefix then
                            # exactly that many bytes, so a short frame here means the
                            # SENDER or the RELAY truncated it, not the socket.
                            self._rxd = getattr(self, "_rxd", 0) + 1
                            if self._rxd % 24 == 1:
                                print("  [VRX] src=%s lvl=%s codec=%s frame=%dB "
                                      "payload=%dB video=%dB"
                                      % (src, _lvl, _cid, len(frame), len(payload),
                                         len(data)), flush=True)
                        self.vdec.feed(src, _cid, data)
                        self.stats.on_video_recv(len(frame) + 4)
                        self._vrx = getattr(self, "_vrx", 0) + 1
                except (ConnectionError, OSError):
                    raise        # the socket is gone: nothing to continue
                except Exception as _fe:
                    n_unpack_fail += 1
                    if n_unpack_fail in (1, 10, 100) or n_unpack_fail % 1000 == 0:
                        print("  [RX/%s] dropped a frame (%d so far): %s: %s"
                              " -- the call continues"
                              % (plane, n_unpack_fail, type(_fe).__name__,
                                 _fe), flush=True)
                    continue

        except Exception as e:
            # [RX_SAYS_WHERE_V1] Everything the decision needs, on one line,
            # naming the plane. `_stop` is reported BEFORE it is set here, so a loop
            # that ended because the call was hanging up is distinguishable from one
            # whose socket was taken away.
            _was_stopping = self._stop.is_set()
            _up = time.time() - t0
            _idle = (time.time() - last_at) if last_at else None
            print("  [RX/%s] receive loop ended: %s: %r\n"
                  "         fd=%s peer=%s up=%.1fs frames=%d bytes=%d "
                  "last_frame=%s aborted=%d unparseable=%d self=%d "
                  "stopping_already=%s"
                  % (plane, type(e).__name__, e, _fd,
                     "%s:%s" % (self.host, self.port), _up, n_frames, n_bytes,
                     ("%.1fs ago" % _idle) if _idle is not None else "NEVER",
                     n_aborted, n_unpack_fail, n_self, _was_stopping), flush=True)
            if not _was_stopping:
                # [TWO_PLANES_V1] A caller with one plane is not a caller, so this
                # ends the call -- but say so, or the OTHER plane's loop reports its
                # own death as if it were independent and the log shows two peers
                # failing at once when one failed and took the other down.
                print("  [RX/%s] this plane was the FIRST to end -- stopping the "
                      "call; any further [RX] line is the consequence, not another "
                      "fault" % plane, flush=True)
            self._stop.set()


def _list_cameras(max_index=10):
    """Probe camera indices 0..max_index-1 the same way a call does (cv2.VideoCapture)
    and print the openable ones. The printed index is exactly what you pass to --cam N.
    cv2 indices are authoritative here -- a single physical camera can expose several
    /dev/videoN nodes, so /dev/video* is not a reliable --cam map; this is. If a camera
    shows at more than one index, use the LOWEST one that reports a resolution."""
    try:
        import cv2
    except Exception as e:
        print(f"  (OpenCV unavailable: {type(e).__name__}: {e!r} -- pip install opencv-python)")
        return

    found = []
    with _silence_cv2_stderr():
        for i in range(max_index):
            cap = open_camera(i)
            try:
                if cap is not None and cap.isOpened():
                    ok, _ = cap.read()
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                    found.append((i, ok, w, h))
            finally:
                if cap is not None:
                    cap.release()

    for i, ok, w, h in found:
        state = f"{w}x{h}" if ok else "opens but read() returned no frame (busy/permission)"
        print(f"  --cam {i}   {state}")
    if not found:
        print(f"  (no camera opened on indices 0..{max_index-1} -- check `ls -l /dev/video*`, "
              f"permissions, or whether something else holds the device)")


class _Publisher:
    """[HEADLESS_PUBLISHER_V1] Keeps an unattended fnav sender visible in the pond.

    Two tuples, re-asserted on a heartbeat because both age out under fresh_s:

      presence   puts this sender in the Communicator's user list. The display
                 name carries a leading '*' so a person reading the roster can
                 tell an unattended camera from someone sitting at a keyboard.
                 mic is reported False -- a headless camera has no one to talk.

      call       originates a session pointing at the relay this sender is
                 already streaming to, so the Communicator has somewhere to
                 join. An already-playing stream cannot be attached to an
                 existing call, so an unattended sender always originates.

    Failure to publish does not stop the stream. The media path is the point;
    the tuples are how people find it. But a publish failure is LOGGED every
    time, never swallowed -- a sender that is streaming and invisible looks
    exactly like a sender that is not running.
    """

    HEARTBEAT_S = 5

    def __init__(self, name, relay, no_video=False, no_audio=False):
        self.name = name
        self.relay = relay
        self.no_video = no_video
        self.no_audio = no_audio
        self.cp = None
        self.session = None
        self._stop = threading.Event()
        self._t = None

    def start(self):
        try:
            import comms_control as _CC
        except Exception as e:
            print("[publish] comms_control unavailable: %r -- this sender will "
                  "stream but will NOT appear in the Communicator" % (e,),
                  flush=True)
            return
        try:
            host, _, port = self.relay.partition(":")
            me_id = "%s-%s" % (self.name.lower().replace(" ", ""),
                               uuid.uuid4().hex[:4])
            self.cp = _CC.ControlPlane(
                me_id, "*" + self.name,          # '*' marks an unattended source
                caps={"cam": not self.no_video, "mic": False,
                      "no_video": bool(self.no_video),
                      "no_audio": True, "unattended": True},
                relay=(host, int(port or 9000)))
            self.cp.announce()
            call = self.cp.start_call()
            self.session = call.get("session")
            print("[publish] *%s is live: session=%s relay=%s:%s"
                  % (self.name, self.session, call.get("host"), call.get("port")),
                  flush=True)
        except Exception as e:
            print("[publish] could not announce: %r -- streaming anyway, but this "
                  "sender will NOT appear in the Communicator" % (e,), flush=True)
            self.cp = None
            return
        self._t = threading.Thread(target=self._beat, daemon=True)
        self._t.start()

    def _beat(self):
        while not self._stop.wait(self.HEARTBEAT_S):
            try:
                self.cp.announce()
                self.cp.refresh_calls()
            except Exception as e:
                # Loud every time. A tuple that stops being re-asserted ages out
                # and the sender vanishes from the roster while still streaming.
                print("[publish] heartbeat failed: %r" % (e,), flush=True)

    def stop(self):
        self._stop.set()
        if self.cp is not None:
            try:
                self.cp.announce("offline")
            except Exception as e:
                print("[publish] could not mark offline: %r" % (e,), flush=True)


def _enable_verbose():
    """[VERBOSE_V1] fnav had no --verbose, so its ladder and decode tracing could
    only be reached through environment variables -- which is fine on a node and
    useless when someone is running it by hand to find out why a stream froze."""
    global BEARER_DIAG, VDEC_DIAG, KEYREQ_DIAG
    BEARER_DIAG = True
    VDEC_DIAG = True
    KEYREQ_DIAG = True


def main(argv=None):
    # Quiet OpenCV's Qt logging (the repeated 'Cannot find font directory' lines on boxes
    # whose OpenCV build lacks the Qt font dir). Cosmetic; must be set before cv2 loads Qt.
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.qpa.*=false")
    ap = argparse.ArgumentParser(description="FrogNet SotF A/V phone (in-process, laddered).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--serve", metavar="BIND")
    g.add_argument("--call", metavar="HOST")
    ap.add_argument("--audio-rate", type=int, default=0, metavar="HZ",
                    help="Force the input sample rate instead of believing the "
                         "device. Some capture devices advertise a rate they do "
                         "not clock at -- an eMeet C950 advertising 44100 while "
                         "running at 16000 delivered 17.7 blocks/s instead of "
                         "50. The [AUD-CAP] line reports the measured rate; if "
                         "it sits well below 'want', try --audio-rate 16000.")
    ap.add_argument("--pcm-audio", action="store_true",
                    help="Send UNCOMPRESSED audio (256,000 bps). [AUDIO_IS_OPUS_V1] "
                         "Opus is mandatory on the wire; this is an explicit "
                         "operator override for LAN use or codec debugging, NOT a "
                         "fallback. Raw PCM is ~11x the cost of Opus and is FIXED "
                         "at every rung -- below a ~550 kbps cap it alone exceeds "
                         "the video budget and no amount of video shedding helps.")
    ap.add_argument("--speed", type=int, default=0, metavar="BPS",
                    help="MediaSpeed for THIS caller in bits/sec, 0 = unlimited. "
                         "[MEDIASPEED_V1] one number, BOTH legs: it throttles this "
                         "client's uplink AND is published so the relay throttles "
                         "the downlink it serves to this client. The GUI slider "
                         "sets the same value; set_throttle() had no CLI caller at "
                         "all, so a headless sender could not be capped.")
    ap.add_argument("--name", default=socket.gethostname())
    ap.add_argument("--in", dest="in_dev", default=None)
    ap.add_argument("--out", dest="out_dev", default=None)
    ap.add_argument("--cam", type=int, default=0, help="camera index (default 0)")
    ap.add_argument("--fps", type=int, default=24, help="video send fps (default 24; raise if encode keeps up)")
    ap.add_argument("--jitter", type=int, default=120, help="audio jitter cushion ms (default 120; raise if you hear ticking)")
    ap.add_argument("--codec", choices=["auto","vp8","h264","h264hw"], default="auto",
                    help="video codec: vp8=software VP8 (default); h264=software H.264; "
                         "h264hw=HARDWARE H.264 (Pi VideoCore via v4l2m2m)")
    ap.add_argument("--aspect", choices=sorted(RUNG_GEO), default=DEFAULT_ASPECT,
                    help="frame shape ABOVE L5 (default 16:9). Below L5 the "
                         "ladder goes 4:3 either way. NOTE: capture is resized, "
                         "not cropped -- choosing an aspect the camera does not "
                         "shoot stretches the picture.")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-duck", action="store_true")
    ap.add_argument("--no-display", action="store_true",
                    help="don't open a video window (capture/send/recv only; for headless/marginal-GUI boxes)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verbose", action="store_true",
                    help="ladder and decode tracing (same switches as the Tk "
                         "client: BEARER_DIAG and VDEC_DIAG)")
    # [HEADLESS_PUBLISHER_V1] An unattended camera. fnav on its own speaks only the
    # media socket -- it never touches the control plane -- so a stream started this
    # way is invisible to the Communicator, whose roster and call list are built
    # entirely from tuples. --publish gives it the two tuples it needs: presence, so
    # it appears in the user list, and a call session, so the stream is joinable.
    # There is no way to attach an already-playing stream to an existing call, so an
    # unattended sender always ORIGINATES one.
    ap.add_argument("--publish", action="store_true",
                    help="announce this sender to the pond: publish presence and "
                         "originate a call session so the Communicator can see and "
                         "join it. The name is shown with a leading '*' to mark it "
                         "as unattended.")
    args = ap.parse_args(argv)
    if getattr(args, "verbose", False):
        _enable_verbose()

    if args.list:
        import sounddevice as sd
        print("=== audio devices ===")
        print(sd.query_devices())
        print("\n=== cameras (pass the index to --cam N) ===")
        _list_cameras()
        return
    if args.serve:
        h, p = A._hostport(args.serve); Relay(h, p).serve(); return
    if args.call:
        h, p = A._hostport(args.call)
        _pub = _Publisher(args.name, args.call,
                          no_video=args.no_video, no_audio=args.no_audio) \
               if args.publish else None
        if _pub is not None:
            _pub.start()
        _call = Call(h, p, args.name,
             in_dev=A._resolve_device(args.in_dev), out_dev=A._resolve_device(args.out_dev),
             cam=args.cam, no_video=args.no_video, no_audio=args.no_audio,
             duck=not args.no_duck, fps=args.fps, no_display=args.no_display,
             codec_id={'auto':None,'vp8':CODEC_VP8,'h264':CODEC_H264_SW,'h264hw':CODEC_H264_HW}[args.codec],
             jitter_ms=args.jitter, pcm_audio=args.pcm_audio,
             session=(getattr(_pub, "session", None) if _pub is not None else None),
             audio_rate=args.audio_rate, aspect=args.aspect)
        # [PRODUCER_LEADS_CONSUMERS_REPORT_V1] --publish is a headless creator:
        # it sends and does not receive, so it has no measurement of the link
        # and no vote in the rate. It starts at what its device can do and moves
        # only when a consumer reports.
        _call.is_producer = bool(args.publish)
        # [MEDIASPEED_CLI_V1] Apply BEFORE run(). set_throttle arms the token
        # bucket on both planes, and the first frames must go out against it
        # rather than against an unthrottled wire -- applying after run() would
        # race them. It no longer touches the ceiling: the dial is a link
        # condition and the ladder discovers what that link carries.
        if args.speed:
            print("[%s] MediaSpeed %d bps from --speed: throttling this uplink "
                  "and publishing for the relay's downlink" % (args.name, args.speed),
                  flush=True)
            _call.set_throttle(args.speed)
        _call.run()
        return
    ap.error("one of --serve / --call / --list is required")


if __name__ == "__main__":
    main()

