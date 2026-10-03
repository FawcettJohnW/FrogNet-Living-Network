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
fnphone_pa.py -- the FNWP audio phone, IN-PROCESS audio via PortAudio (sounddevice).

WHY THIS BUILD: fnphone.py drives audio by spawning ffmpeg (capture) and ffplay
(playback) and piping PCM between processes. That works and is dependency-light, but the
process pipes + ffplay's player queue put a latency floor of (at best) ~100ms and a
tick-vs-delay tradeoff you have to tune. This build removes the subprocesses: it captures
and plays through PortAudio in-process, so mouth-to-ear latency drops toward the tens of
milliseconds and the feed is steady (no seam ticks, no player queue).

SAME WIRE, SAME RELAY: the FNWP-1 framing and the relay are byte-for-byte identical to
fnphone.py -- this client interoperates with that relay and with fnphone.py clients on the
same call. Only the two ENDS (mic in, speaker out) changed.

DEPENDENCY (the cost of in-process audio): PortAudio.
  Windows: `pip install sounddevice` -- the wheel bundles PortAudio. Nothing else.
  Linux/Pi: `pip install sounddevice` AND `sudo apt install libportaudio2`.

RUN (relay is the same program/relay as fnphone.py; you can even use fnphone.py --serve):
  Relay:   python3 fnphone_pa.py --serve 0.0.0.0:9000
  Caller:  python3 fnphone_pa.py --call 10.250.250.1:9000 --name Alice
           python3 fnphone_pa.py --call 10.250.250.1:9000 --name Gorp

  --in  <dev>   input device (index or name substring; default = system default)
  --out <dev>   output device (index or name substring; default = system default)
  --list        list audio devices and exit
  --block-ms N  capture/playback block size in ms (default 20; lower = less latency)
  --jitter-ms N playout cushion per source in ms (default 40; raise if choppy)
"""
from __future__ import annotations

import argparse
import select
import socket
import os
import struct
import sys
import threading
import time

# -- audio format (identical to fnphone.py / the rest of FrogNet) -------------
AUDIO_RATE = 16000
AUDIO_CH = 1
SAMPLE_BYTES = 2                                 # s16le


# ---------------------------------------------------------------------------
# [LIVE_TAP_V1] One run, one directory, one clock. Every stage logged from the
# SAME run so the lines can be laid side by side and compared directly.
#
# Each tap writes two things:
#   live_<stage>_<rate>hz.raw   the audio itself
#   live_<stage>.log            one line per write: monotonic ms since the run
#                               started, bytes this write, cumulative bytes,
#                               cumulative audio ms, and DRIFT -- cumulative
#                               audio ms minus wall ms. Drift is the number
#                               that matters: if a stage is producing less
#                               audio than real time, drift goes negative and
#                               the amount IS the gap.
#
# All stages share RUN_T0, so a timestamp in one log means the same instant as
# the same timestamp in another. That is the whole point: a stage that stalls
# shows a jump in ITS log at a wall time where the others kept running.
#
# OFF by default. Two ways to arm it, no code edit needed:
#   FROGNET_TAP_DIR=/tmp                    (env, per run)
#   fnphone_pa.LIVE_TAP_DIR = "/tmp"        (from a script)
# Left in deliberately. It is the only instrument that shows every audio stage
# of one run against one clock, and the run that found the wrong capture device
# on John (2026-08-15) was the run that had it armed. Costs nothing when off:
# live_tap() returns on the first line.
LIVE_TAP_DIR = os.environ.get("FROGNET_TAP_DIR") or None

_TAPS = {}
RUN_T0 = None          # first write of the run; every tap shares it


def live_tap(name, pcm, rate):
    """Append PCM and log the write. Never raises -- a tap must not kill a call."""
    if not LIVE_TAP_DIR or not pcm:
        return
    try:
        import os, time
        global RUN_T0
        if RUN_T0 is None:
            RUN_T0 = time.monotonic()
        st = _TAPS.get(name)
        if st is None:
            base = os.path.join(LIVE_TAP_DIR, "live_%s" % name)
            raw = open("%s_%dhz.raw" % (base, rate), "wb")
            log = open("%s.log" % base, "w")
            log.write("# stage=%s rate=%d  wall_ms bytes cum_bytes cum_audio_ms "
                      "drift_ms\n" % (name, rate))
            st = _TAPS[name] = {"raw": raw, "log": log, "cum": 0, "rate": rate}
            print("  [TAP] %s -> %s_%dhz.raw + %s.log" % (name, base, rate, base),
                  flush=True)
        st["raw"].write(pcm); st["raw"].flush()
        st["cum"] += len(pcm)
        wall = (time.monotonic() - RUN_T0) * 1000.0
        aud = st["cum"] / (st["rate"] * 2 / 1000.0)
        st["log"].write("%9.1f %6d %10d %10.1f %8.1f\n"
                        % (wall, len(pcm), st["cum"], aud, aud - wall))
        st["log"].flush()
    except Exception:
        pass

AUDIO_BYTES_PER_SEC = AUDIO_RATE * AUDIO_CH * SAMPLE_BYTES   # 32000 (the WIRE rate)

# -- high-quality arbitrary-ratio resampling (libsamplerate via the 'samplerate' lib) --
# PortAudio won't resample and devices run at their own rate (48000, 44100, ...). We run the
# LOCAL streams at the device's native rate and convert to/from the 16k wire with a STREAMING
# resampler that keeps filter state across blocks (no seams) and handles ANY ratio -- so 44100
# works as cleanly as 48000. s16le bytes <-> float32 at the boundary; the wire stays 16k.
try:
    import numpy as _np
    import samplerate as _sr
    _HAVE_SR = True
except Exception as _e:                          # missing numpy or libsamplerate
    _HAVE_SR = False
    _SR_IMPORT_ERR = _e


def _s16_to_f32(b):
    return _np.frombuffer(b, dtype="<i2").astype("float32") / 32768.0


def _f32_to_s16(a):
    a = _np.clip(a, -1.0, 1.0)
    return (a * 32767.0).astype("<i2").tobytes()


class StreamResampler:
    """Streaming s16le resampler at a fixed ratio (out_rate/in_rate). Keeps internal filter
    state across calls so consecutive blocks join seamlessly. 'sinc_fastest' is realtime-cheap
    and clean for voice; bump to 'sinc_medium' for more quality if CPU allows."""
    # [SINC_BEST_IS_WORTH_IT_V1] Was "sinc_fastest". Changed 2026-08-15 after a
    # listening test on the Windows client: with sinc_best the far end's voices
    # became materially more intelligible on the SAME call. The playout ratio
    # there is 16000 -> 44100, a non-integer 2.756, which is the hard case for a
    # cheap converter -- its wide transition band lets content fold back into the
    # voice band. Sandy's capture ratio, 32000 -> 16000, is an exact 2:1 and
    # never showed the problem, which is why this only ever bit one end.
    #
    # It is more CPU per block. That is the trade, made deliberately: a 20 ms
    # block at these rates is still far inside the callback budget on every box
    # in the pond, and the audio is what people judge the system by.
    def __init__(self, in_rate, out_rate, quality="sinc_best"):
        self.ratio = out_rate / float(in_rate)
        self.in_rate, self.out_rate = int(in_rate), int(out_rate)
        # [RESAMPLER_NO_DEP_AT_MATCHED_RATE_V1] When the device already runs at the
        # wire rate, no resampling is needed -- pass bytes straight through and never
        # touch libsamplerate, so a matched-rate device needs no extra dependency.
        self._passthrough = (int(in_rate) == int(out_rate))
        if self._passthrough:
            self._r = None
            return
        # [RESAMPLER_HONOR_HAVE_SR_V1] The module-level import sets _HAVE_SR=False when
        # the 'samplerate' lib (libsamplerate) is missing; honor it with a clear,
        # actionable error instead of a bare NameError on the unbound _sr.
        if not _HAVE_SR:
            raise RuntimeError(
                f"audio resampling {in_rate}->{out_rate}Hz needs the 'samplerate' "
                f"library (libsamplerate): pip install samplerate  "
                f"(Linux/Pi also: sudo apt install libsamplerate0). "
                f"import error was: {_SR_IMPORT_ERR!r}")
        self._r = _sr.Resampler(quality, channels=AUDIO_CH)

    def process(self, pcm_s16: bytes) -> bytes:
        out = self._process(pcm_s16)
        # [LIVE_TAP_V1] name the stage by which direction this resampler runs
        if self.out_rate == AUDIO_RATE and self.in_rate != AUDIO_RATE:
            live_tap("A_captured", out, self.out_rate)      # mic -> wire rate
        elif self.in_rate == AUDIO_RATE and self.out_rate != AUDIO_RATE:
            live_tap("D_speaker", out, self.out_rate)       # wire rate -> device
        return out

    def _process(self, pcm_s16: bytes) -> bytes:
        if not pcm_s16:
            return b""
        if self._passthrough:
            return pcm_s16
        out = self._r.process(_s16_to_f32(pcm_s16), self.ratio)
        return _f32_to_s16(out) if len(out) else b""

# -- FNWP-1 framing (identical layout to call_media.py / fnphone.py) ----------
_LEN = struct.Struct("!I")                       # socket length prefix per frame
_RAW_HDR = struct.Struct("!IQBII")               # seq, ts, key, alen, vlen
_SRC_HDR = struct.Struct("!H")                   # src_id length prefix


def pack_audio(src: str, seq: int, ts: int, audio: bytes) -> bytes:
    s = src.encode("utf-8")
    body = _RAW_HDR.pack(seq, ts, 1, len(audio), 0) + audio
    return _SRC_HDR.pack(len(s)) + s + body


def unpack_audio(payload: bytes):
    (slen,) = _SRC_HDR.unpack_from(payload, 0)
    off = _SRC_HDR.size
    src = payload[off:off + slen].decode("utf-8", "replace"); off += slen
    seq, ts, key, alen, vlen = _RAW_HDR.unpack_from(payload, off)
    off += _RAW_HDR.size
    audio = payload[off:off + alen]
    return src, seq, ts, audio


def send_frame(sock, frame):
    sock.sendall(_LEN.pack(len(frame)) + frame)


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def recv_frame(sock):
    (n,) = _LEN.unpack(recv_exact(sock, _LEN.size))
    return recv_exact(sock, n)


def apply_gain(pcm: bytes, gain: float) -> bytes:
    """Scale an s16le block by `gain` (0..1), clipping. gain>=0.999 is a no-op."""
    if gain >= 0.999:
        return pcm
    if gain <= 0.0:
        return b"\x00" * len(pcm)
    out = bytearray(len(pcm))
    for i in range(0, len(pcm) - 1, 2):
        v = int(int.from_bytes(pcm[i:i+2], "little", signed=True) * gain)
        if v > 32767: v = 32767
        elif v < -32768: v = -32768
        out[i:i+2] = v.to_bytes(2, "little", signed=True)
    return bytes(out)


def apply_gain_ramp(pcm: bytes, g0: float, g1: float) -> bytes:
    """Scale an s16le block with gain LINEARLY RAMPED from g0 (first sample) to g1 (last).
    Ramping across the block -- and starting each block where the previous one ended --
    removes the amplitude STEP at block seams that a per-block flat gain produces. That step
    is an audible click at the block rate; the ramp makes a changing duck gain inaudible."""
    if g0 >= 0.999 and g1 >= 0.999:
        return pcm
    n = len(pcm) // 2
    if n == 0:
        return pcm
    out = bytearray(len(pcm))
    for i in range(n):
        frac = i / (n - 1) if n > 1 else 1.0
        g = g0 + (g1 - g0) * frac
        v = int(int.from_bytes(pcm[2*i:2*i+2], "little", signed=True) * g)
        if v > 32767: v = 32767
        elif v < -32768: v = -32768
        out[2*i:2*i+2] = int(v).to_bytes(2, "little", signed=True)
    return bytes(out)


class EchoDucker:
    """Soft echo suppression: when the FAR side is playing out the speaker, duck THIS mic so
    the speaker bleed isn't captured and sent back. Not true cancellation -- a level-driven
    gain. Fast attack (duck quickly when they talk), slow release (recover gently so your own
    speech isn't chopped). enabled=False -> always full gain (no-op)."""
    def __init__(self, enabled=True, floor=0.15, speaker_thresh=400):
        self.enabled = enabled
        self.floor = floor                       # minimum mic gain while far side is loud
        self.thresh = speaker_thresh             # speaker peak above which we duck fully
        self._env = 0.0                          # smoothed speaker envelope

    def mic_gain(self, speaker_peak: int) -> float:
        if not self.enabled:
            return 1.0
        if speaker_peak > self._env:
            self._env = 0.5 * self._env + 0.5 * speaker_peak       # fast attack
        else:
            self._env = 0.92 * self._env + 0.08 * speaker_peak     # slow release
        if self._env <= 30:                      # far side effectively silent
            return 1.0
        frac = min(1.0, self._env / self.thresh)
        return max(self.floor, 1.0 - (1.0 - self.floor) * frac)


# -- s16le mixing (sum with clipping) -- pure bytes, no numpy ------------------
def mix_s16le(chunks, nbytes):
    """Sum several equal-length s16le byte chunks into one, clipping to int16.
    Shorter chunks are treated as padded with silence. Returns nbytes of PCM."""
    if not chunks:
        return b"\x00" * nbytes
    if len(chunks) == 1 and len(chunks[0]) == nbytes:
        return chunks[0]
    n = nbytes // SAMPLE_BYTES
    acc = [0] * n
    for c in chunks:
        m = min(n, len(c) // SAMPLE_BYTES)
        for i in range(m):
            acc[i] += int.from_bytes(c[2*i:2*i+2], "little", signed=True)
    out = bytearray(nbytes)
    for i in range(n):
        v = acc[i]
        if v > 32767: v = 32767
        elif v < -32768: v = -32768
        out[2*i:2*i+2] = int(v).to_bytes(2, "little", signed=True)
    return bytes(out)


# -- per-source playout buffers + an output mixer (one OutputStream for all) --
class Mixer:
    """Holds a small jitter/playout buffer per remote source. The output callback pulls
    one block from each source, sums them, and returns the mix. A source that has run dry
    contributes silence (graceful gap concealment) rather than stalling the stream."""
    def __init__(self, block_bytes, jitter_bytes, max_buffer_bytes=None):
        self.block_bytes = block_bytes
        self.jitter_bytes = jitter_bytes
        # Cap playout backlog near the jitter cushion, NOT a full second. A buffer allowed to
        # grow large becomes accumulating audio latency (audio drifts behind video over time);
        # keeping it small bounds mouth-to-ear delay. Default: 2x cushion, floor ~240ms.
        floor = AUDIO_RATE * 240 // 1000 * AUDIO_CH * SAMPLE_BYTES
        self.max_buffer_bytes = max_buffer_bytes or max(floor, jitter_bytes * 2)
        self.bufs = {}                           # src -> bytearray
        self.started = {}                        # src -> bool (waited for jitter fill?)
        self.out_bytes = 0                       # total mixed bytes pulled (instrumentation)
        self.last_peak = 0                        # peak amplitude of last mixed block
        self.active = 0                          # sources contributing to last block
        self.trimmed = 0                         # bytes dropped to keep latency bounded
        # [MIXER_DAMAGE_IS_COUNTED_V1] The mixer has exactly two places it can
        # damage the signal, and until now NEITHER was visible.
        #
        #   feed()       -- del b[:drop], a hard splice out of the waveform
        #   pull_block() -- a short chunk zero-padded to a full block by
        #                   mix_s16le, i.e. a hard step to silence and back
        #
        # The [SPK] "output underrun" counter in fnav cannot fire: pull_block
        # ALWAYS returns exactly block_bytes because mix_s16le pads. So the
        # accumulator in on_out always fills, _ur never increments, and the
        # fade-to-silence concealment beneath it is unreachable code. The gap
        # is manufactured HERE, one layer up, and reported nowhere.
        #
        # On speech these hide in the pauses. On a continuous source -- fan
        # noise off a camera pointed at a rack -- every one is audible, and a
        # run of pads at the block rate is a periodic low-frequency ring rather
        # than a click.
        # NOTE on which of these actually fires: a decoded Opus packet at
        # AUDIO_RATE is exactly one block (20 ms), so a buffer draining under a
        # steady feed hits zero EXACTLY and never leaves a remainder. The
        # partial-pad branch therefore only fires on a non-block-aligned
        # buffer -- rare. The common starvation case is `dry`: the source
        # contributes NOTHING and mix_s16le emits a whole block of silence for
        # it. dry is the counter to watch for gaps; pads is for mid-block cuts.
        self.pads = 0                            # blocks completed with silence
        self.pad_bytes = 0                       # silence bytes inserted
        self.dry = 0                             # blocks where a source gave nothing
        self.trims = 0                           # splice events (trimmed = bytes)
        self.lock = threading.Lock()

    def damage_report(self, rate=None, ch=None, width=None):
        """[MIXER_DAMAGE_IS_COUNTED_V1] One line, safe to call from any thread.

        Bytes are converted to milliseconds of audio so the number is directly
        comparable to the 20 ms block and to the relay's s/s column.
        """
        rate = rate or AUDIO_RATE
        ch = ch or AUDIO_CH
        width = width or SAMPLE_BYTES
        bps = float(rate * ch * width) / 1000.0          # bytes per millisecond
        return ("  [MIX] pad %d blocks (%.0f ms silence inserted), "
                "trim %d splices (%.0f ms cut), dry %d, active %d, peak %d"
                % (self.pads, self.pad_bytes / bps,
                   self.trims, self.trimmed / bps,
                   self.dry, self.active, self.last_peak))

    def feed(self, src, pcm):
        with self.lock:
            b = self.bufs.get(src)
            if b is None:
                b = bytearray(); self.bufs[src] = b; self.started[src] = False
            b.extend(pcm)
            # keep latency bounded: if the buffer outgrows the cushion, drop OLDEST audio so
            # playout stays near-real-time instead of slowly accumulating delay.
            if len(b) > self.max_buffer_bytes:
                drop = len(b) - self.max_buffer_bytes
                del b[:drop]; self.trimmed += drop; self.trims += 1

    def pull_block(self):
        """One mixed block of exactly block_bytes for the output callback."""
        with self.lock:
            chunks = []
            short = 0
            for src, b in self.bufs.items():
                if not self.started[src]:
                    if len(b) < self.jitter_bytes:
                        continue                 # still filling the cushion -> silence
                    self.started[src] = True
                if len(b) >= self.block_bytes:
                    chunks.append(bytes(b[:self.block_bytes]))
                    del b[:self.block_bytes]
                elif b:
                    # [MIXER_DAMAGE_IS_COUNTED_V1] Partial block. mix_s16le will
                    # zero-pad this to block_bytes: a hard step to silence and a
                    # hard step back. Counted, not silent.
                    short += self.block_bytes - len(b)
                    chunks.append(bytes(b)); b.clear()
                else:
                    self.dry += 1                # source contributed nothing at all
            if short:
                self.pads += 1
                self.pad_bytes += short
        out = mix_s16le(chunks, self.block_bytes)
        live_tap("C_mixed", out, AUDIO_RATE)     # [LIVE_TAP_V1] what will play
        # counters only (no printing in the realtime path)
        self.out_bytes += len(out)
        self.last_peak = max((abs(int.from_bytes(out[i:i+2], "little", signed=True))
                              for i in range(0, len(out) - 1, 2)), default=0)
        self.active = len(chunks)
        return out


# -- RELAY HOST (identical behavior to fnphone.py) ----------------------------
class Relay:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.peers = {}
        self.lock = threading.Lock()

    def serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(8)
        print(f"[relay] FNWP audio relay on {self.host}:{self.port} -- waiting", flush=True)
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self.lock:
                self.peers[conn] = f"{addr[0]}:{addr[1]}"
            print(f"[relay] caller joined {addr[0]}:{addr[1]} ({len(self.peers)} on call)", flush=True)
            threading.Thread(target=self._pump, args=(conn,), daemon=True).start()

    def _pump(self, conn):
        try:
            while True:
                self._fan(conn, recv_frame(conn))
        except Exception:
            pass
        finally:
            with self.lock:
                self.peers.pop(conn, None)
            try: conn.close()
            except Exception: pass
            print(f"[relay] caller left ({len(self.peers)} remain)", flush=True)

    def _fan(self, sender, frame):
        with self.lock:
            targets = [c for c in self.peers if c is not sender]
        dead = []
        for c in targets:
            try: send_frame(c, frame)
            except Exception: dead.append(c)
        if dead:
            with self.lock:
                for c in dead:
                    self.peers.pop(c, None)
                    try: c.close()
                    except Exception: pass


# -- CLIENT: PortAudio capture + playback, FNWP wire --------------------------
class PhonePA:
    def __init__(self, host, port, name, in_dev=None, out_dev=None,
                 block_ms=20, jitter_ms=40, device_rate=None, duck=True):
        self.host, self.port, self.name = host, port, name
        self.in_dev, self.out_dev = in_dev, out_dev
        self.block_ms = block_ms
        self.device_rate = device_rate            # None -> auto from device default_samplerate
        # WIRE side stays at AUDIO_RATE (16k); mixer/jitter are in WIRE bytes.
        self.wire_block_frames = max(1, AUDIO_RATE * block_ms // 1000)
        self.block_bytes = self.wire_block_frames * AUDIO_CH * SAMPLE_BYTES
        self.jitter_bytes = max(self.block_bytes, AUDIO_RATE * jitter_ms // 1000 * AUDIO_CH * SAMPLE_BYTES)
        self.sock = None
        self._seq = 0
        self._stop = threading.Event()
        self.mixer = Mixer(self.block_bytes, self.jitter_bytes)
        self.ducker = EchoDucker(enabled=duck)
        self._rx = {"frames": 0, "gaps": 0, "last": {}}

    def call(self):
        import sounddevice as sd   # imported here so --list/errors are friendly

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect((self.host, self.port))
        print(f"[{self.name}] connected to relay {self.host}:{self.port}", flush=True)

        if not _HAVE_SR:
            print(f"[{self.name}] resampler unavailable: {_SR_IMPORT_ERR!r}\n"
                  f"  Install:  pip install samplerate numpy\n"
                  f"  (Linux/Pi may also need: sudo apt install libsamplerate0)", flush=True)
            self.sock.close(); return

        # INPUT and OUTPUT devices have their own native rates (mic 48000, HDMI 44100, ...).
        # Resolve each; the streaming resampler converts each to/from the 16k wire -- ANY rate.
        def _native(dev, kind):
            try:
                info = sd.query_devices(dev, kind) if dev is not None else sd.query_devices(kind=kind)
                return int(round(info["default_samplerate"])), info["name"]
            except Exception:
                return 48000, "?"
        self.in_rate = self.device_rate or _native(self.in_dev, "input")[0]
        self.out_rate = self.device_rate or _native(self.out_dev, "output")[0]
        # device block sizes (~ block_ms of audio at each device's own rate)
        self.in_block_frames = max(1, self.in_rate * self.block_ms // 1000)
        self.out_block_frames = max(1, self.out_rate * self.block_ms // 1000)
        # streaming resamplers: capture in_rate->16k, playback 16k->out_rate
        self.cap_rs = StreamResampler(self.in_rate, AUDIO_RATE)
        self.play_rs = StreamResampler(AUDIO_RATE, self.out_rate)
        print(f"[{self.name}] input {self.in_rate}Hz -> wire {AUDIO_RATE}Hz -> output {self.out_rate}Hz "
              f"(any rate, libsamplerate)", flush=True)

        threading.Thread(target=self._recv_loop, daemon=True).start()

        # input callback: each captured block -> FNWP frame -> relay
        # The audio callback MUST NOT block or do I/O. It only drops captured bytes into a
        # queue; a separate sender thread does the blocking socket write. (A blocking
        # send_frame INSIDE the callback stalls PortAudio, which aborts the stream -- that
        # was the "~3s then drop".)
        import queue as _queue
        txq = _queue.Queue(maxsize=200)

        def on_in(indata, frames, t, status):
            if status:
                print(f"  [IN] {status}", flush=True)
            try:
                txq.put_nowait(self.cap_rs.process(bytes(indata)))   # in-rate -> 16k wire
            except _queue.Full:
                pass                              # drop a block rather than stall the callback

        def sender():
            sent = 0
            while not self._stop.is_set():
                try:
                    pcm = txq.get(timeout=0.2)
                except _queue.Empty:
                    continue
                # echo suppression: duck the mic when the far side is currently on the speaker
                pcm = apply_gain(pcm, self.ducker.mic_gain(self.mixer.last_peak))
                self._seq += 1
                frame = pack_audio(self.name, self._seq, int(time.time() * 1000), pcm)
                try:
                    send_frame(self.sock, frame)
                    sent += len(pcm)
                    if sent // AUDIO_BYTES_PER_SEC != (sent - len(pcm)) // AUDIO_BYTES_PER_SEC:
                        peak = max((abs(int.from_bytes(pcm[i:i+2], "little", signed=True))
                                    for i in range(0, len(pcm) - 1, 2)), default=0)
                        print(f"  [MIC-TX] sent {sent} bytes (~{sent//AUDIO_BYTES_PER_SEC}s), "
                              f"peak={peak} {'(SILENCE -- mic capturing zeros)' if peak < 30 else ''}",
                              flush=True)
                except Exception as e:
                    print(f"  [TX] send failed, dropping call: {e!r}", flush=True)
                    self._stop.set()
                    return

        # output callback: fill outdata at the OUTPUT device rate. The mixer is at 16k; we
        # resample 16k->out_rate (stateful, seamless) into an accumulator and serve exactly
        # the bytes the device asks for this block. No I/O, no blocking.
        self._out_acc = bytearray()
        def on_out(outdata, frames, t, status):
            if status:
                print(f"  [OUT] {status}", flush=True)
            need = len(outdata)
            # top up the accumulator from the mixer until we have a full device block
            guard = 0
            while len(self._out_acc) < need and guard < 8:
                wire = self.mixer.pull_block()             # 16k mix (block_bytes)
                self._out_acc.extend(self.play_rs.process(wire))  # 16k -> out_rate
                guard += 1
            if len(self._out_acc) >= need:
                outdata[:] = bytes(self._out_acc[:need])
                del self._out_acc[:need]
            else:
                got = len(self._out_acc)
                outdata[:got] = bytes(self._out_acc)
                outdata[got:] = b"\x00" * (need - got)     # underrun -> pad silence
                self._out_acc.clear()

        threading.Thread(target=sender, daemon=True).start()

        def monitor():
            last = 0
            while not self._stop.is_set():
                time.sleep(1.0)
                ob = self.mixer.out_bytes
                print(f"  [SPK] {ob} bytes to speaker (~{ob//AUDIO_BYTES_PER_SEC}s), "
                      f"last peak={self.mixer.last_peak}, sources={self.mixer.active} "
                      f"{'(silent)' if self.mixer.last_peak < 30 else ''}",
                      flush=True)
                last = ob
        threading.Thread(target=monitor, daemon=True).start()

        import traceback
        try:
            # Probe the devices BEFORE opening. check_*_settings raises a descriptive error
            # (invalid rate/format for THIS device) instead of a silent failure inside start().
            di = sd.query_devices(self.in_dev, "input") if self.in_dev is not None else sd.query_devices(kind="input")
            do = sd.query_devices(self.out_dev, "output") if self.out_dev is not None else sd.query_devices(kind="output")
            print(f"[{self.name}] input dev '{di['name']}' @ {self.in_rate}Hz", flush=True)
            print(f"[{self.name}] output dev '{do['name']}' @ {self.out_rate}Hz", flush=True)
            sd.check_input_settings(device=self.in_dev, channels=AUDIO_CH, dtype="int16", samplerate=self.in_rate)
            sd.check_output_settings(device=self.out_dev, channels=AUDIO_CH, dtype="int16", samplerate=self.out_rate)
            print(f"[{self.name}] both devices accepted -- opening streams", flush=True)

            def in_done():
                print("  [IN] input stream FINISHED/aborted", flush=True)
                self._stop.set()
            def out_done():
                print("  [OUT] output stream FINISHED/aborted", flush=True)
                self._stop.set()

            instream = sd.RawInputStream(samplerate=self.in_rate, channels=AUDIO_CH, dtype="int16",
                                         blocksize=self.in_block_frames, device=self.in_dev,
                                         latency="low", callback=on_in, finished_callback=in_done)
            instream.start()
            print(f"[{self.name}] input stream OK (active={instream.active})", flush=True)
            outstream = sd.RawOutputStream(samplerate=self.out_rate, channels=AUDIO_CH, dtype="int16",
                                           blocksize=self.out_block_frames, device=self.out_dev,
                                           latency="low", callback=on_out, finished_callback=out_done)
            outstream.start()
            print(f"[{self.name}] output stream OK (active={outstream.active})", flush=True)
            print(f"[{self.name}] on the call -- talk. Ctrl-C to hang up.", flush=True)
            try:
                while not self._stop.is_set():
                    time.sleep(0.2)
            finally:
                instream.stop(); instream.close()
                outstream.stop(); outstream.close()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"[{self.name}] AUDIO OPEN FAILED: {type(e).__name__}: {e!r}", flush=True)
            traceback.print_exc()
            print("  ^ This is the real reason. If it mentions sample rate, the device won't\n"
                  "    do 16000Hz directly (PortAudio doesn't resample). Tell me the\n"
                  "    default_sr printed above and I'll capture at the native rate.", flush=True)
        finally:
            self._stop.set()
            try: self.sock.close()
            except Exception: pass
            print(f"[{self.name}] hung up.", flush=True)

    def _recv_loop(self):
        try:
            while not self._stop.is_set():
                frame = recv_frame(self.sock)
                try:
                    src, seq, ts, audio = unpack_audio(frame)
                except Exception:
                    continue
                if not src or src == self.name:
                    continue
                prev = self._rx["last"].get(src)
                if prev is not None and seq > prev + 1:
                    self._rx["gaps"] += (seq - prev - 1)
                self._rx["last"][src] = seq
                self._rx["frames"] += 1
                if self._rx["frames"] % 100 == 0:
                    print(f"  [RX] {self._rx['frames']} frames, {self._rx['gaps']} dropped",
                          flush=True)
                if audio:
                    self.mixer.feed(src, audio)
        except Exception as e:
            print(f"  [RX] receive loop ended: {type(e).__name__}: {e!r}", flush=True)
            self._stop.set()
        else:
            print("  [RX] receive loop exited (stop set elsewhere)", flush=True)


def _hostport(s, default_port=9000):
    if ":" in s:
        h, p = s.rsplit(":", 1); return h, int(p)
    return s, default_port


def _resolve_device(spec):
    """Accept an int index, a name substring, or None (default)."""
    if spec is None:
        return None
    try:
        return int(spec)
    except (TypeError, ValueError):
        return spec        # sounddevice matches a substring of the device name


def main(argv=None):
    ap = argparse.ArgumentParser(description="FNWP audio phone, in-process PortAudio audio.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--serve", metavar="BIND", help="run the relay, e.g. 0.0.0.0:9000")
    g.add_argument("--call", metavar="HOST", help="join a call, e.g. 10.250.250.1:9000")
    ap.add_argument("--name", default=socket.gethostname(), help="your name on the call")
    ap.add_argument("--in", dest="in_dev", default=None, help="input device (index or name)")
    ap.add_argument("--out", dest="out_dev", default=None, help="output device (index or name)")
    ap.add_argument("--list", action="store_true", help="list audio devices and exit")
    ap.add_argument("--block-ms", type=int, default=20, help="capture/playback block ms (default 20)")
    ap.add_argument("--jitter-ms", type=int, default=40, help="per-source playout cushion ms (default 40)")
    ap.add_argument("--rate", type=int, default=None, metavar="HZ",
                    help="device sample rate; default auto from device.")
    ap.add_argument("--no-duck", action="store_true",
                    help="disable echo ducking (mic is full-gain even while the far side talks)")
    args = ap.parse_args(argv)

    if args.list:
        import sounddevice as sd
        print(sd.query_devices()); return
    if args.serve:
        host, port = _hostport(args.serve)
        Relay(host, port).serve(); return
    if args.call:
        host, port = _hostport(args.call)
        PhonePA(host, port, args.name,
                in_dev=_resolve_device(args.in_dev), out_dev=_resolve_device(args.out_dev),
                block_ms=args.block_ms, jitter_ms=args.jitter_ms,
                device_rate=args.rate, duck=not args.no_duck).call(); return
    ap.error("one of --serve / --call / --list is required")


if __name__ == "__main__":
    main()
