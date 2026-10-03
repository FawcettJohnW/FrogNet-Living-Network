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
fnphone.py - a minimal FrogNet audio phone. From scratch. Audio only.

WHAT THIS IS (and is NOT):
  * It is a 2+ party voice call that rides ONE dedicated TCP socket per client, framed in
    FNWP-1 (length-prefixed, source-labeled). Capture is ffmpeg; playback is ffplay.
  * It does NOT use the media "engine", the adaptive ladder, the tuple control plane, the
    rung/codex machinery, or video. None of that is imported. This file stands alone on
    Python + ffmpeg/ffplay.

TOPOLOGY (simplest thing that gives 2+ parties hearing each other):
      client A --+
      client B --+  one dedicated FNWP socket each --?  relay host (--serve)
      client C --+                                       fans each peer's audio
                                                         to every OTHER peer
  The relay is a dumb forwarder: a frame in from one peer goes out to all the others,
  unchanged, tagged with WHO sent it. Each client plays every remote source it hears.
  (A real mix-sum can replace the forwarder later; forwarding is enough for a call.)

WIRE (the real FNWP-1 forms, copied byte-for-byte from call_media.py so this stays
compatible with the rest of FrogNet if you ever want to bridge):
      on the socket:   [!I frame_len][frame]
      frame (src-labeled): [!H src_len][src utf-8][!I seq][!Q ts][!B key][!I alen][!I vlen][audio][video]
  Here video is always empty (alen>0, vlen=0). key is always 1 for audio.

RUN:
  Relay (on the box that will host the call, e.g. the LAN .1):
      python3 fnphone.py --serve 0.0.0.0:9000

  Each caller:
      python3 fnphone.py --call 10.250.250.1:9000 --name Alice
      python3 fnphone.py --call 10.250.250.1:9000 --name Gorp
      (a third --call makes it a three-way; the relay fans to all)

  Options:
      --mic "<device>"   override capture device (default: OS default mic)
      --no-audio-out     capture/send only, don't play (useful for one box echo test)
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

# -- audio format (matches the rest of FrogNet: 16 kHz mono s16le) ------------
AUDIO_RATE = 16000
AUDIO_CH = 1
AUDIO_FMT = "s16le"
FRAME_MS = 20                                   # 20 ms of audio per FNWP frame
FRAME_BYTES = AUDIO_RATE * AUDIO_CH * 2 * FRAME_MS // 1000   # 640 bytes @16k/20ms
AUDIO_BYTES_PER_SEC = AUDIO_RATE * AUDIO_CH * 2             # 32000 B/s @16k mono s16le

# -- FNWP-1 framing (identical layout to call_media.py) -----------------------
_LEN = struct.Struct("!I")                      # socket length prefix per frame
_RAW_HDR = struct.Struct("!IQBII")              # seq, ts, key, alen, vlen
_SRC_HDR = struct.Struct("!H")                  # src_id length prefix


def pack_audio(src: str, seq: int, ts: int, audio: bytes) -> bytes:
    """One FNWP-1 src-labeled frame carrying a slice of audio (no video)."""
    s = src.encode("utf-8")
    body = _RAW_HDR.pack(seq, ts, 1, len(audio), 0) + audio
    return _SRC_HDR.pack(len(s)) + s + body


def unpack_audio(payload: bytes):
    """Returns (src, seq, ts, audio). Raises on a malformed frame."""
    (slen,) = _SRC_HDR.unpack_from(payload, 0)
    off = _SRC_HDR.size
    src = payload[off:off + slen].decode("utf-8", "replace"); off += slen
    seq, ts, key, alen, vlen = _RAW_HDR.unpack_from(payload, off)
    off += _RAW_HDR.size
    audio = payload[off:off + alen]
    return src, seq, ts, audio


def send_frame(sock: socket.socket, frame: bytes):
    sock.sendall(_LEN.pack(len(frame)) + frame)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def recv_frame(sock: socket.socket) -> bytes:
    (n,) = _LEN.unpack(recv_exact(sock, _LEN.size))
    return recv_exact(sock, n)


# -- audio capture / playback (ffmpeg + ffplay; the working primitives) -------
def _ffmpeg():
    ff = shutil.which("ffmpeg")
    if not ff:
        sys.exit("ffmpeg not found on PATH - install ffmpeg")
    return ff


def _ffplay(ff):
    return (ff[:-6] + "ffplay") if ff.endswith("ffmpeg") else (shutil.which("ffplay") or "ffplay")


def _default_mic(osn: str):
    """OS default mic spec for the capture demuxer. dshow has no 'default' pseudo-device,
    so the caller should pass --mic on Windows if this guess is wrong."""
    if osn == "Windows":
        return None                              # force explicit --mic on Windows
    if osn == "Darwin":
        return ":0"                              # avfoundation: default audio in
    return "default"                             # alsa


def mic_cmd(ff: str, osn: str, mic: str):
    out = ["-ac", str(AUDIO_CH), "-ar", str(AUDIO_RATE), "-f", AUDIO_FMT, "pipe:1"]
    base = [ff, "-hide_banner", "-loglevel", "error"]
    if osn == "Windows":
        return base + ["-f", "dshow", "-i", mic, *out]       # raw name, no escaping (argv)
    if osn == "Darwin":
        return base + ["-f", "avfoundation", "-i", mic, *out]
    return base + ["-f", "alsa", "-i", mic, *out]


class MicCapture:
    """ffmpeg mic -> raw PCM on stdout, read in FRAME_BYTES slices, with a retry loop
    (the C920-style camera-grabs-the-device-first race recovers instead of dying)."""
    def __init__(self, ff, osn, mic):
        self.cmd = mic_cmd(ff, osn, mic)
        self._stop = threading.Event()
        self.proc = None

    def frames(self):
        """Yield FRAME_BYTES PCM slices forever (until stop). Instrumented: ffmpeg's own
        stderr is NOT swallowed, and each respawn prints why the last attempt ended."""
        print(f"  [MIC] capture command:\n        {' '.join(self.cmd)}", file=sys.stderr, flush=True)
        backoff = 0.5
        while not self._stop.is_set():
            try:
                # stderr=None -> inherit our stderr so ffmpeg's -loglevel error messages
                # (bad device, busy device, unsupported rate) are VISIBLE, not hidden.
                self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=None)
                backoff = 0.5
                got_any = False
                while not self._stop.is_set():
                    pcm = self.proc.stdout.read(FRAME_BYTES)
                    if not pcm:
                        break                    # EOF: ffmpeg exited
                    got_any = True
                    if len(pcm) < FRAME_BYTES:
                        # final short slice at shutdown; send what we have, then loop ends
                        yield pcm
                        break
                    yield pcm
                rc = self.proc.poll()
                if not self._stop.is_set():
                    if not got_any:
                        print(f"  [MIC] ffmpeg produced NO audio and exited (rc={rc}). "
                              f"The device string is almost certainly wrong or the device "
                              f"is busy. See the ffmpeg error above.", file=sys.stderr, flush=True)
                    else:
                        print(f"  [MIC] capture stream ended (rc={rc}).", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"  [MIC] capture error: {e}", file=sys.stderr, flush=True)
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 5.0)
            print("  [MIC] retrying capture...", file=sys.stderr, flush=True)

    def stop(self):
        self._stop.set()
        if self.proc:
            try: self.proc.terminate()
            except Exception: pass


class Playback:
    """One ffplay per remote source so multiple speakers don't fight one stdin.
    play(src, pcm) lazily starts a sink for each new src."""
    def __init__(self, ff, coalesce=3):
        self.ffplay = _ffplay(ff)
        self.sinks = {}                          # src -> Popen
        self.fed = {}                            # src -> bytes written (instrumentation)
        self.buf = {}                            # src -> bytearray play-out accumulator
        self.coalesce = max(1, int(coalesce))    # frames per write; ~20ms each. higher=smoother+more delay
        self.lock = threading.Lock()

    def play(self, src: str, pcm: bytes):
        with self.lock:
            p = self.sinks.get(src)
            if p is None:
                if not self.ffplay or (shutil.which(self.ffplay) is None
                                       and not os.path.isfile(self.ffplay)):
                    print(f"  [AUDIO-OUT] ffplay not found ({self.ffplay!r}); cannot play. "
                          f"Install ffplay or put it next to ffmpeg.", file=sys.stderr, flush=True)
                    self.sinks[src] = False
                    return
                try:
                    # stderr inherited (NOT DEVNULL): if ffplay can't open the output
                    # device, you SEE the error instead of silence.
                    # raw s16le defaults to 1 channel (no channel flag; -ac rejected by
                    # some builds). LOW-LATENCY playback: nobuffer + low_delay stop ffplay
                    # pre-filling a big smoothing queue (that queue was the ~4s delay);
                    # probesize/analyzeduration skip probing (raw PCM is fully specified);
                    # sync ext plays as fed. aresample=async=1 still smooths frame seams.
                    p = subprocess.Popen(
                        [self.ffplay, "-hide_banner", "-loglevel", "error", "-nodisp",
                         "-autoexit", "-fflags", "nobuffer", "-flags", "low_delay",
                         "-probesize", "32", "-analyzeduration", "0", "-sync", "ext",
                         "-f", AUDIO_FMT, "-ar", str(AUDIO_RATE),
                         "-af", "aresample=async=1:first_pts=0",
                         "-i", "pipe:0"],
                        stdin=subprocess.PIPE, stderr=None)
                    self.sinks[src] = p
                    self.fed[src] = 0
                    print(f"  [AUDIO-OUT] playing source '{src}' via {self.ffplay}",
                          file=sys.stderr, flush=True)
                except Exception as e:
                    print(f"  [AUDIO-OUT] cannot start playback for '{src}': {e}",
                          file=sys.stderr, flush=True)
                    self.sinks[src] = False
                    return
            if p:
                try:
                    # Coalesce frames into a small play-out block instead of writing a
                    # 640-byte sliver every 20ms (the stop-start feed ffplay clicks on).
                    # ~4 frames = 80ms: one flushed write per block, steady stream out.
                    buf = self.buf.get(src)
                    if buf is None:
                        buf = bytearray(); self.buf[src] = buf
                    buf.extend(pcm)
                    if len(buf) >= FRAME_BYTES * self.coalesce:
                        block = bytes(buf); buf.clear()
                        p.stdin.write(block); p.stdin.flush()
                        self.fed[src] = self.fed.get(src, 0) + len(block)
                        if self.fed[src] // 64000 != (self.fed[src] - len(block)) // 64000:
                            print(f"  [AUDIO-OUT] '{src}': {self.fed[src]} bytes played "
                                  f"(~{self.fed[src]//AUDIO_BYTES_PER_SEC}s)", file=sys.stderr, flush=True)
                    rc = p.poll()
                    if rc is not None:
                        print(f"  [AUDIO-OUT] ffplay for '{src}' exited (rc={rc}); "
                              f"restarting on next frame.", file=sys.stderr, flush=True)
                        self.sinks[src] = None
                except (OSError, ValueError):
                    self.sinks[src] = None       # pipe broke; respawn next frame

    def close(self):
        with self.lock:
            for p in self.sinks.values():
                if not p:
                    continue
                # close stdin first: ffplay --autoexit ends cleanly on EOF. Then escalate
                # terminate -> kill so a stuck ffplay can never keep the audio device.
                try:
                    if p.stdin: p.stdin.close()
                except Exception: pass
                try: p.terminate()
                except Exception: pass
                try:
                    p.wait(timeout=1.0)
                except Exception:
                    try: p.kill()
                    except Exception: pass
            self.sinks.clear()


# -- RELAY HOST (--serve): fan each peer's audio to every other peer ----------
class Relay:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.peers = {}                          # conn -> name
        self.lock = threading.Lock()

    def serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(8)
        print(f"[relay] FNWP audio relay on {self.host}:{self.port} - waiting for callers",
              flush=True)
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self.lock:
                self.peers[conn] = f"{addr[0]}:{addr[1]}"
            print(f"[relay] caller joined: {addr[0]}:{addr[1]}  ({len(self.peers)} on the call)",
                  flush=True)
            threading.Thread(target=self._pump, args=(conn,), daemon=True).start()

    def _pump(self, conn):
        try:
            while True:
                frame = recv_frame(conn)         # opaque FNWP-1 frame
                self._fan(conn, frame)
        except Exception:
            pass
        finally:
            with self.lock:
                self.peers.pop(conn, None)
            try: conn.close()
            except Exception: pass
            print(f"[relay] caller left  ({len(self.peers)} remain)", flush=True)

    def _fan(self, sender, frame):
        """Forward this frame to every peer EXCEPT the sender. Non-blocking-ish:
        a dead/slow peer is dropped, never blocks the others."""
        with self.lock:
            targets = [c for c in self.peers if c is not sender]
        dead = []
        for c in targets:
            try:
                send_frame(c, frame)
            except Exception:
                dead.append(c)
        if dead:
            with self.lock:
                for c in dead:
                    self.peers.pop(c, None)
                    try: c.close()
                    except Exception: pass


# -- CLIENT (--call): one socket, send mic up, play everything that comes down -
class Phone:
    def __init__(self, host, port, name, mic, audio_out=True, coalesce=3):
        self.host, self.port, self.name = host, port, name
        self.mic = mic
        self.audio_out = audio_out
        self.ff = _ffmpeg()
        self.sock = None
        self.cap = None
        self.play = Playback(self.ff, coalesce) if audio_out else None
        self._seq = 0

    def call(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect((self.host, self.port))
        print(f"[{self.name}] connected to relay {self.host}:{self.port}", flush=True)

        # receiver thread: play every remote source
        threading.Thread(target=self._recv_loop, daemon=True).start()

        # sender: capture mic, frame, send
        osn = platform.system()
        mic = self.mic or _default_mic(osn)
        if not mic:
            sys.exit(f"[{self.name}] no --mic given and no OS default on {osn}; "
                     f'pass --mic "audio=<device name>"')
        self.cap = MicCapture(self.ff, osn, mic)
        print(f"[{self.name}] on the call - talk. Ctrl-C to hang up.", flush=True)
        sent = 0
        try:
            for pcm in self.cap.frames():
                self._seq += 1
                frame = pack_audio(self.name, self._seq, int(time.time() * 1000), pcm)
                try:
                    send_frame(self.sock, frame)
                    sent += len(pcm)
                    if sent // 32000 != (sent - len(pcm)) // 32000:
                        # is the mic producing actual sound, or digital silence?
                        peak = max(abs(int.from_bytes(pcm[i:i+2], "little", signed=True))
                                   for i in range(0, len(pcm) - 1, 2)) if pcm else 0
                        print(f"  [MIC-TX] sent {sent} bytes (~{sent//AUDIO_BYTES_PER_SEC}s), "
                              f"peak amplitude this frame={peak} "
                              f"{'(SILENCE - mic capturing zeros)' if peak < 30 else ''}",
                              file=sys.stderr, flush=True)
                except Exception:
                    print(f"[{self.name}] relay connection lost", file=sys.stderr)
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.hangup()

    def _recv_loop(self):
        last_seq = {}          # src -> last seq seen
        gaps = {}              # src -> total dropped frames
        rxcount = {}           # src -> frames received
        try:
            while True:
                frame = recv_frame(self.sock)
                try:
                    src, seq, ts, audio = unpack_audio(frame)
                except Exception:
                    continue                     # ignore a malformed frame, keep going
                if src and src != self.name:
                    prev = last_seq.get(src)
                    if prev is not None and seq > prev + 1:
                        gaps[src] = gaps.get(src, 0) + (seq - prev - 1)
                    last_seq[src] = seq
                    rxcount[src] = rxcount.get(src, 0) + 1
                    if rxcount[src] % 50 == 0:    # ~1s at 20ms frames
                        print(f"  [RX] '{src}': {rxcount[src]} frames, "
                              f"{gaps.get(src,0)} dropped (gaps in seq)",
                              file=sys.stderr, flush=True)
                if self.play and audio and src != self.name:
                    self.play.play(src or "peer", audio)
        except Exception:
            pass

    def hangup(self):
        if self.cap: self.cap.stop()
        if self.play: self.play.close()
        if self.sock:
            try: self.sock.close()
            except Exception: pass
        print(f"[{self.name}] hung up.", flush=True)


def _hostport(s: str, default_port=9000):
    if ":" in s:
        h, p = s.rsplit(":", 1)
        return h, int(p)
    return s, default_port


def main(argv=None):
    ap = argparse.ArgumentParser(description="Minimal FNWP audio phone (audio only).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--serve", metavar="BIND", help="run the relay host, e.g. 0.0.0.0:9000")
    g.add_argument("--call", metavar="HOST", help="join a call via the relay, e.g. 10.250.250.1:9000")
    ap.add_argument("--name", default=socket.gethostname(), help="your name on the call")
    ap.add_argument("--mic", default=None, help='capture device (e.g. "audio=Microphone (...)" on Windows)')
    ap.add_argument("--no-audio-out", action="store_true", help="send only, don't play received audio")
    ap.add_argument("--buffer", type=int, default=3, metavar="FRAMES",
                    help="playout smoothing, ~20ms per frame (default 3=60ms). raise if you hear ticks, lower for less delay")
    args = ap.parse_args(argv)

    if args.serve:
        host, port = _hostport(args.serve)
        Relay(host, port).serve()
    else:
        host, port = _hostport(args.call)
        Phone(host, port, args.name, args.mic, audio_out=not args.no_audio_out,
              coalesce=args.buffer).call()


if __name__ == "__main__":
    main()
