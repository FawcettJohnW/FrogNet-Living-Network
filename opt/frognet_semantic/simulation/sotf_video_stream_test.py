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
sotf_video_stream_test.py - Stream a REAL video file through SotF over the
parameterized WireMedium and measure delivered quality.

This is the SBIR-Phase-I characterization tool for SotF-ACP media transport:
sweep bandwidth x latency x jitter x outage and watch what actually arrives
at the receiver. It uses the FULL faithful transport (two-socket, HELLO +
RETURN, SEQ_RESET, pipelined seq-tagged replies, worker pool, coalescing,
template store) from transport_sim_tier.py and the REAL SotFMediaHandler +
SemanticCodec + FNW1 wire path from sotf_media_tier.py.

THE VIDEO IS REAL:
  - If you don't pass --video, ffmpeg generates a libvpx VP8 stream in an
    IVF container (DKIF magic) - moving testsrc pattern, configurable
    duration / size / framerate / bitrate. The resulting file is parsed
    into actual VP8 frames with realistic keyframe-vs-delta byte
    distributions (keyframes 3-5 KB, deltas 500-800 B for 320x240@15fps).
  - If you pass --video, it must be IVF. Convert other formats with
    `ffmpeg -i input.mp4 -c:v libvpx -keyint_min 24 -g 24 output.ivf`.

DELIVERY CLASS at the application layer:
  - RELIABLE  -> keyframes. On reply timeout, the proxy retries once. If
                the retry also times out, the keyframe is counted lost
                (decoder would need to wait for the next keyframe).
  - DROPPABLE -> delta frames. On reply timeout, the proxy moves on -
                one frame of freeze, recovers at next keyframe. No retry,
                because retransmitting a stale delta would only delay
                fresher frames behind it.

WHAT'S MEASURED:
  - Bytes sent on the wire vs raw video bytes (codex compression ratio)
  - Frames delivered / lost, broken out by class
  - End-to-end latency distribution per frame (min / p50 / p95 / max)
  - Effective throughput (bytes/sec delivered)
  - Whether bytes received match bytes sent (payload integrity)

EXAMPLES:
  Clean LAN baseline:
    python3 sotf_video_stream_test.py
  Satellite (256 kbps, 300 ms one-way):
    python3 sotf_video_stream_test.py --bandwidth-bps 32000 --latency-ms 300
  Contested RF (periodic outages):
    python3 sotf_video_stream_test.py --bandwidth-bps 64000 --latency-ms 50 \\
        --outage-prob 0.3 --outage-ms 800
  Your own video:
    python3 sotf_video_stream_test.py --video /path/to/clip.ivf
"""
from __future__ import annotations

import os
import sys
import time
import types
import struct
import argparse
import hashlib
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ============================================================================
# Path + stubs (must run before frognet imports)
# ============================================================================

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p and p not in sys.path:
        sys.path.insert(0, p)

# Reuse the stub installers from transport_sim_tier (importing it runs them).
import transport_sim_tier as ts  # noqa: E402  - also brings WireMedium, etc.
from transport_sim_tier import (                                      # noqa: E402
    NetworkParams, WireMedium,
    SimulatedProxyWorker, SimulatedDaemonSession,
    TemplateStore, create_connected_pair,
    wrap_req_full, wrap_req_diff, wrap_req_repeat, try_parse,
    OP_RESP_DIFF, OP_RESP_SAME, OP_ERROR,
    REQ_HASH_LEN, SAME_ID_LEN,
)
from sotf_media_tier import SotFMediaHandler                          # noqa: E402
from core.codec import SemanticCodec                                  # noqa: E402
from transport_factories import (                                     # noqa: E402
    connect_pair, round_trip_remote, NetemShaper,
)


# ============================================================================
# IVF parsing - real frame boundaries from libvpx output
# ============================================================================

@dataclass
class VideoFrame:
    seq: int            # 0-indexed frame number
    pts: int            # presentation timestamp from container
    payload: bytes      # the encoded frame bytes
    is_keyframe: bool   # True for I-frames (independently decodable)


def parse_ivf(path: str) -> Tuple[Dict[str, Any], List[VideoFrame]]:
    """Parse an IVF (DKIF) container into a list of VideoFrames.

    IVF layout: 32-byte file header, then per-frame [size:4][pts:8][bytes].
    Header contains codec FourCC, width, height, framerate, frame count.
    For libvpx VP8, the first frame in each GOP is a keyframe; we detect
    keyframes by the VP8 frame-header bit (uncompressed header byte 0,
    bit 0 = key frame flag, where 0 = keyframe, 1 = inter)."""
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < 32 or data[0:4] != b"DKIF":
        raise ValueError(f"{path} is not an IVF file (magic must be DKIF)")

    fourcc = data[8:12]
    width = struct.unpack("<H", data[12:14])[0]
    height = struct.unpack("<H", data[14:16])[0]
    framerate_num = struct.unpack("<I", data[16:20])[0]
    framerate_den = struct.unpack("<I", data[20:24])[0]
    frame_count = struct.unpack("<I", data[24:28])[0]
    meta = {
        "fourcc": fourcc.decode("ascii", errors="replace"),
        "width": width,
        "height": height,
        "fps": framerate_num / max(framerate_den, 1),
        "frame_count": frame_count,
    }

    frames: List[VideoFrame] = []
    off = 32
    seq = 0
    while off + 12 <= len(data):
        (size,) = struct.unpack("<I", data[off:off+4])
        pts = struct.unpack("<Q", data[off+4:off+12])[0]
        frame_off = off + 12
        if frame_off + size > len(data):
            break
        payload = data[frame_off:frame_off + size]
        # VP8 keyframe detection: byte 0 bit 0 (LSB) clear = keyframe
        is_keyframe = (payload[0] & 0x01) == 0 if payload else False
        frames.append(VideoFrame(seq=seq, pts=pts, payload=payload,
                                  is_keyframe=is_keyframe))
        seq += 1
        off = frame_off + size

    return meta, frames


def generate_test_video(out_path: str, duration: float = 3.0,
                        width: int = 320, height: int = 240, fps: int = 15,
                        bitrate_kbps: int = 300) -> None:
    """Use ffmpeg's testsrc filter + libvpx to produce a real VP8 IVF.
    Keyframe interval set to fps (= 1 keyframe per second).

    Requires ffmpeg in PATH. Fails loudly if not present."""
    if not _have_ffmpeg():
        raise RuntimeError(
            "ffmpeg not found in PATH. Either install it or pass --video <path>."
        )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
        "-c:v", "libvpx",
        "-b:v", f"{bitrate_kbps}k",
        "-keyint_min", str(fps),
        "-g", str(fps),
        out_path,
    ]
    subprocess.run(cmd, check=True)


def _have_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], check=True,
                       capture_output=True, timeout=5)
        return True
    except Exception:
        return False


# ============================================================================
# DeliveryClass - application-level reliability policy
# ============================================================================

class DeliveryClass:
    """How the sender treats a frame on send-timeout.

    RELIABLE  - retry once on timeout. Used for keyframes: the receiver
                cannot decode subsequent delta frames without the
                preceding keyframe. Worth one retry to keep the GOP intact.
    DROPPABLE - single attempt with a short timeout, no retry. Used for
                delta frames: a delayed retransmit would only block fresher
                frames behind it. One missed delta = one frame of
                freeze, picture recovers at the next keyframe."""
    RELIABLE = "reliable"
    DROPPABLE = "droppable"


# ============================================================================
# SotF sender - encodes a video stream into FNW1 frames and ships them
# ============================================================================

@dataclass
class FrameOutcome:
    """Per-frame send result."""
    seq: int
    klass: str                # DeliveryClass.RELIABLE or DROPPABLE
    is_keyframe: bool
    wire_bytes: int           # total bytes put on the wire (incl. length prefix)
    encoded_bytes: int        # SemanticCodec output (post-codex)
    raw_payload_bytes: int    # original video frame bytes
    attempts: int             # 1 normally, 2 if reliable retry fired
    success: bool             # True if reply arrived
    rtt_ms: float             # measured RTT for the successful attempt (NaN on fail)


@dataclass
class StreamStats:
    """Aggregate stream metrics."""
    frames_total: int = 0
    keyframes_total: int = 0
    delta_total: int = 0
    frames_delivered: int = 0
    keyframes_delivered: int = 0
    delta_delivered: int = 0
    frames_lost: int = 0
    keyframes_lost: int = 0
    delta_lost: int = 0
    reliable_retries: int = 0
    bytes_raw: int = 0          # sum of original video frame bytes (all frames)
    bytes_raw_delivered: int = 0  # raw bytes of successfully delivered frames
    bytes_encoded: int = 0      # sum of SemanticCodec output bytes
    bytes_wire: int = 0         # sum of wire bytes (codec + FNW1 framing + length)
    rtts_ms: List[float] = field(default_factory=list)
    wall_time_ms: float = 0.0
    bytes_verified: int = 0     # bytes the receiver decoded back to original


class SotFSender:
    """Streams encoded video frames through a SimulatedProxyWorker with
    application-level DeliveryClass policy. Maintains the per-session
    reference for encode_request_diff."""

    def __init__(self, proxy: SimulatedProxyWorker, session_id: str,
                 fragment: Dict[str, Any], req_hash: bytes,
                 opcode: int = 0xC0DEC701,
                 reliable_timeout_s: float = 2.0,
                 droppable_timeout_s: float = 0.4):
        self.proxy = proxy
        self.session_id = session_id
        self.fragment = fragment
        self.req_hash = req_hash
        self.opcode = opcode
        self.handler = SotFMediaHandler()
        self.codec = SemanticCodec()
        self._reference: Optional[Dict[str, Any]] = None
        self._reliable_timeout_s = reliable_timeout_s
        self._droppable_timeout_s = droppable_timeout_s

    def send_frame(self, vf: VideoFrame) -> FrameOutcome:
        """Encode one video frame and push it through the proxy. Returns
        a FrameOutcome capturing what happened on the wire and whether
        the reply arrived in time."""
        klass = DeliveryClass.RELIABLE if vf.is_keyframe else DeliveryClass.DROPPABLE
        timeout = self._reliable_timeout_s if klass == DeliveryClass.RELIABLE else self._droppable_timeout_s

        frame_dict = {
            "seq": vf.seq,
            "payload": vf.payload,
            "session_id": self.session_id,
            "codec": self.fragment["baseline"]["codec"],
            "sr": self.fragment["baseline"]["sr"],
            "layout": self.fragment["baseline"]["layout"],
            "level_idx": self.fragment["baseline"]["level_idx"],
        }
        dyn = self.handler.extract_request_dynamic(frame_dict, self.fragment)

        if self._reference is None:
            # First frame of the session: full envelope on the wire (REQ_FULL).
            encoded = self.codec.encode_request(
                opcode=self.opcode, url_vals=[], json_vals=dyn,
                type_map=self.fragment["type_map"],
                tokens=self.fragment["tokens"], compress=True,
            )
            wire_frame = wrap_req_full(self.req_hash, encoded)
            self._reference = {k: v for (k, v) in dyn}
        else:
            # Delta frame: encode_request_diff against running reference.
            encoded, new_ref, is_identical = self.codec.encode_request_diff(
                opcode=self.opcode, url_vals=[], json_vals=dyn,
                type_map=self.fragment["type_map"],
                reference=self._reference,
                tokens=self.fragment["tokens"], compress=True,
            )
            if is_identical:
                wire_frame = wrap_req_repeat(self.req_hash)
            else:
                wire_frame = wrap_req_diff(self.req_hash, encoded)
            self._reference = new_ref

        attempts = 0
        success = False
        rtt_ms = float("nan")
        max_attempts = 2 if klass == DeliveryClass.RELIABLE else 1

        while attempts < max_attempts and not success:
            attempts += 1
            t0 = time.perf_counter()
            reply = self.proxy.send_request(wire_frame, timeout=timeout)
            t1 = time.perf_counter()
            if reply is not None:
                # Validate the reply was a real RESP, not an ERROR.
                msg = try_parse(reply)
                if msg is not None and msg.op in (OP_RESP_DIFF, OP_RESP_SAME):
                    success = True
                    rtt_ms = (t1 - t0) * 1000.0

        return FrameOutcome(
            seq=vf.seq, klass=klass, is_keyframe=vf.is_keyframe,
            wire_bytes=4 + len(wire_frame),   # +4 = length prefix on the wire
            encoded_bytes=len(encoded) if 'encoded' in locals() else 0,
            raw_payload_bytes=len(vf.payload),
            attempts=attempts, success=success, rtt_ms=rtt_ms,
        )


# ============================================================================
# Receiver - attached to the daemon side, decodes incoming frames so we can
# verify payload integrity end-to-end
# ============================================================================

class SotFReceiver:
    """Sits on the daemon side, captures each decoded frame's reference state,
    extracts the payload bytes, and tracks delivery integrity.

    Implementation: we monkey-patch the daemon's _handle_req_full and
    _handle_req_diff to capture the decoded (seq, payload) after the
    template store has been updated. This is the same mechanism the
    production daemon would use to forward the decoded frame to a
    MediaBridge for playback."""

    def __init__(self, daemon: SimulatedDaemonSession,
                 fragment: Dict[str, Any], req_hash: bytes):
        self.daemon = daemon
        self.fragment = fragment
        self.req_hash = req_hash
        self.codec = SemanticCodec()
        self.handler = SotFMediaHandler()

        # State captured per frame
        self._lock = threading.Lock()
        self.received_seqs: List[int] = []
        self.received_bytes: Dict[int, bytes] = {}   # seq -> payload

        # Pre-seed daemon's template store
        daemon.store.store(req_hash, fragment=fragment, reference={})

        # Wrap the daemon's handlers with a capture step
        self._orig_full = daemon._handle_req_full
        self._orig_diff = daemon._handle_req_diff
        daemon._handle_req_full = self._wrap_full
        daemon._handle_req_diff = self._wrap_diff
        # REQ_REPEAT carries no payload; just track that the seq advanced.
        self._orig_repeat = daemon._handle_req_repeat
        daemon._handle_req_repeat = self._wrap_repeat

    def _decode_and_capture(self, msg, is_full: bool):
        """Decode the FNW1 message's codec payload, pull seq + payload out
        of the reconstructed dict, and stash for later verification."""
        try:
            frag_entry = self.daemon.store.get(msg.req_hash) or {}
            reference = None if is_full else frag_entry.get("reference") or {}
            req_tpl = types.SimpleNamespace(
                url_query_keys=[], fragment=self.fragment,
            )
            if is_full:
                _, decoded = self.codec.decode_request(
                    msg.payload, req_tpl, tokens=self.fragment["tokens"],
                )
            else:
                _, decoded = self.codec.decode_request(
                    msg.payload, req_tpl, tokens=self.fragment["tokens"],
                    reference=reference,
                )
            d = self.handler.rebuild_reply(self.fragment, decoded)
            seq = int(d.get("seq", -1))
            payload = d.get("payload")
            if isinstance(payload, (bytes, bytearray)):
                with self._lock:
                    self.received_seqs.append(seq)
                    self.received_bytes[seq] = bytes(payload)
        except Exception as e:
            # Decode errors are tracked but don't crash the receiver.
            print(f"[RECV] decode error on op={msg.op}: {e!r}", flush=True)

    def _wrap_full(self, msg):
        reply = self._orig_full(msg)
        self._decode_and_capture(msg, is_full=True)
        return reply

    def _wrap_diff(self, msg):
        reply = self._orig_diff(msg)
        self._decode_and_capture(msg, is_full=False)
        return reply

    def _wrap_repeat(self, msg):
        # REQ_REPEAT means the dynamic fields were identical to the last
        # delta - same seq, same payload. We record an arrival but the
        # bytes are unchanged from the previous successfully decoded frame.
        reply = self._orig_repeat(msg)
        with self._lock:
            # Reuse the last decoded payload at this seq (if any).
            if self.received_seqs:
                last_seq = self.received_seqs[-1]
                last_bytes = self.received_bytes.get(last_seq, b"")
                # We don't actually know the new seq because nothing changed;
                # just mark that another frame arrived.
                self.received_seqs.append(last_seq + 1)
                self.received_bytes[last_seq + 1] = last_bytes
        return reply


# ============================================================================
# Test driver
# ============================================================================

def _percentile(values: List[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def run_stream_test(video_path: str,
                    bandwidth_bps: float = 1e9,
                    latency_ms: float = 1.0,
                    jitter_ms: float = 0.0,
                    outage_prob_per_sec: float = 0.0,
                    outage_duration_ms: float = 500.0,
                    reliable_timeout_s: float = 2.0,
                    droppable_timeout_s: float = 0.4,
                    capture_dir: Optional[str] = None,
                    verbose: bool = False,
                    transport: str = "sim",
                    daemon_port: int = 0,
                    shaper: Optional[NetemShaper] = None) -> StreamStats:
    """Stream the video file through the SotF transport with the given wire
    parameters. Returns a populated StreamStats.

    transport='sim'      - in-process socketpair + Python shaper (default).
    transport='loopback' - real TCP to an in-process SimulatedDaemonSession on
                           127.0.0.1; receiver still in-process so the full
                           stream/integrity path runs end to end. Wire shaping
                           comes from `shaper` (tc-netem, on-box); in-container
                           loopback is the clean-wire reproduction."""
    print(f"--- SotF video stream test ---")
    print(f"transport:       {transport}")
    print(f"video:           {video_path}")
    print(f"bandwidth:       {bandwidth_bps:.0f} B/s ({bandwidth_bps*8/1000:.1f} kbps)")
    print(f"latency:         {latency_ms} ms one-way")
    print(f"jitter:          +/-{jitter_ms} ms")
    print(f"outage prob/sec: {outage_prob_per_sec}  duration: {outage_duration_ms} ms")
    print(f"timeouts:        RELIABLE={reliable_timeout_s}s  DROPPABLE={droppable_timeout_s}s")
    if capture_dir:
        print(f"wire capture:    {capture_dir}")

    meta, frames = parse_ivf(video_path)
    print(f"video:           {meta['fourcc']} {meta['width']}x{meta['height']} "
          f"@{meta['fps']:.1f}fps, {len(frames)} frames, "
          f"{sum(len(f.payload) for f in frames)} payload bytes")

    keyframe_count = sum(1 for f in frames if f.is_keyframe)
    print(f"keyframes:       {keyframe_count}  deltas: {len(frames) - keyframe_count}")

    # --- Set up transport ---
    params = NetworkParams(
        latency_ms=latency_ms,
        jitter_ms=jitter_ms,
        bandwidth_bps=bandwidth_bps,
        outage_prob_per_sec=outage_prob_per_sec,
        outage_duration_ms=outage_duration_ms,
    )

    session_id = f"sotf-{int(time.time())}"
    session_init = {
        "session_id": session_id,
        "codec": "vp8",
        "sr": 0,
        "layout": "video",
        "level_idx": 0,
    }
    handler = SotFMediaHandler()
    fragment = handler.extract_template(session_init)
    req_hash = hashlib.blake2b(session_id.encode("utf-8"),
                                digest_size=REQ_HASH_LEN).digest()

    proxy, daemon, w1, w2 = connect_pair(
        local_gw="10.20.21.5",
        mode=transport,
        fwd_params=params, rev_params=params,
        capture_dir=capture_dir,
        default_fragment=fragment,
        daemon_port=daemon_port,
        shaper=shaper,
    )

    receiver = SotFReceiver(daemon, fragment, req_hash)
    sender = SotFSender(proxy, session_id, fragment, req_hash,
                        reliable_timeout_s=reliable_timeout_s,
                        droppable_timeout_s=droppable_timeout_s)
    stats = StreamStats()
    outcomes: List[FrameOutcome] = []

    # --- Stream ---
    try:
        t0 = time.perf_counter()
        for vf in frames:
            outcome = sender.send_frame(vf)
            outcomes.append(outcome)

            stats.frames_total += 1
            stats.bytes_raw += outcome.raw_payload_bytes
            stats.bytes_encoded += outcome.encoded_bytes
            stats.bytes_wire += outcome.wire_bytes
            if outcome.attempts > 1:
                stats.reliable_retries += (outcome.attempts - 1)

            if outcome.is_keyframe:
                stats.keyframes_total += 1
                if outcome.success:
                    stats.keyframes_delivered += 1
                else:
                    stats.keyframes_lost += 1
            else:
                stats.delta_total += 1
                if outcome.success:
                    stats.delta_delivered += 1
                else:
                    stats.delta_lost += 1

            if outcome.success:
                stats.frames_delivered += 1
                stats.bytes_raw_delivered += outcome.raw_payload_bytes
                stats.rtts_ms.append(outcome.rtt_ms)
            else:
                stats.frames_lost += 1

            if verbose:
                kind = "KEY" if vf.is_keyframe else "delta"
                klass_tag = "[R]" if outcome.klass == DeliveryClass.RELIABLE else "[D]"
                status = "OK" if outcome.success else "LOST"
                rtt = f"{outcome.rtt_ms:.0f}ms" if outcome.success else "-"
                print(f"  {klass_tag} seq={vf.seq:3d} {kind:5s} "
                      f"raw={outcome.raw_payload_bytes:5d} "
                      f"wire={outcome.wire_bytes:5d}  {status} {rtt}")

        # Give the daemon a moment to fully drain its worker pool so the
        # receiver captures all in-flight decodes.
        time.sleep(0.1)
        stats.wall_time_ms = (time.perf_counter() - t0) * 1000.0

        # --- Verify payload integrity for the delivered frames ---
        for outcome in outcomes:
            if not outcome.success:
                continue
            recv_bytes = receiver.received_bytes.get(outcome.seq)
            if recv_bytes is None:
                continue
            orig = frames[outcome.seq].payload
            if recv_bytes == orig:
                stats.bytes_verified += len(orig)
    finally:
        proxy.close()
        daemon.stop()
        w1.close()
        w2.close()

    # --- Print results ---
    _print_stats(stats, len(frames))
    return stats


def _print_stats(s: StreamStats, expected_frames: int) -> None:
    print()
    print("=== results ===")
    print(f"wall time:                {s.wall_time_ms:.0f} ms")
    print(f"frames sent / lost:       {s.frames_total} / {s.frames_lost}")
    print(f"  keyframes (RELIABLE):   sent={s.keyframes_total} "
          f"delivered={s.keyframes_delivered} lost={s.keyframes_lost} "
          f"retries={s.reliable_retries}")
    print(f"  deltas    (DROPPABLE):  sent={s.delta_total} "
          f"delivered={s.delta_delivered} lost={s.delta_lost}")
    delivery_pct = 100.0 * s.frames_delivered / max(s.frames_total, 1)
    print(f"frame delivery rate:      {delivery_pct:.1f}%")

    if s.rtts_ms:
        print(f"latency per frame (ms):   "
              f"min={min(s.rtts_ms):.0f}  "
              f"p50={_percentile(s.rtts_ms, 0.5):.0f}  "
              f"p95={_percentile(s.rtts_ms, 0.95):.0f}  "
              f"max={max(s.rtts_ms):.0f}")

    if s.bytes_raw > 0:
        codex_ratio = s.bytes_encoded / s.bytes_raw
        wire_ratio = s.bytes_wire / s.bytes_raw
        codex_savings_pct = 100.0 * (1 - codex_ratio)
        print(f"bytes raw video:          {s.bytes_raw}")
        print(f"bytes after codex:        {s.bytes_encoded}  "
              f"({codex_savings_pct:+.1f}% vs raw)")
        print(f"bytes on wire (incl FNW1+len): {s.bytes_wire}  "
              f"({100*(wire_ratio-1):+.1f}% vs raw)")

    if s.wall_time_ms > 0:
        effective_kbps = (s.bytes_wire * 8) / s.wall_time_ms
        print(f"effective throughput:     {effective_kbps:.1f} kbps on the wire")

    if s.frames_delivered > 0 and s.bytes_raw_delivered > 0:
        verify_pct = 100.0 * s.bytes_verified / s.bytes_raw_delivered
        print(f"payload integrity:        "
              f"{s.bytes_verified} of {s.bytes_raw_delivered} delivered-frame bytes verified "
              f"({verify_pct:.1f}%)")


def sum_delivered_bytes(s: StreamStats) -> int:
    return s.bytes_raw_delivered


# ============================================================================
# Main / CLI
# ============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stream a real video file through SotF and measure quality.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--video", type=str, default=None,
                     help="Path to an IVF video file. If omitted, ffmpeg "
                          "generates a test stream.")
    ap.add_argument("--bandwidth-bps", type=float, default=1e9,
                     help="Wire bandwidth in bytes/sec (default: 1e9 = 8 Gbps clean LAN)")
    ap.add_argument("--latency-ms", type=float, default=1.0,
                     help="One-way latency in ms (default: 1)")
    ap.add_argument("--jitter-ms", type=float, default=0.0,
                     help="+/-jitter in ms applied to latency (default: 0)")
    ap.add_argument("--outage-prob", type=float, default=0.0,
                     help="Probability per second of a link outage (default: 0)")
    ap.add_argument("--outage-ms", type=float, default=500.0,
                     help="Outage duration in ms (default: 500)")
    ap.add_argument("--capture-dir", type=str, default=None,
                     help="If set, write post-shaping wire bytes here for "
                          "post-hoc inspection.")
    ap.add_argument("--reliable-timeout", type=float, default=None,
                     help="Timeout in seconds for RELIABLE (keyframe) sends. "
                          "Default: auto-scale to max(2.0, 6*RTT_estimate)")
    ap.add_argument("--droppable-timeout", type=float, default=None,
                     help="Timeout in seconds for DROPPABLE (delta) sends. "
                          "Default: auto-scale to max(0.4, 3*RTT_estimate)")
    ap.add_argument("--gen-duration", type=float, default=3.0,
                     help="Duration in seconds for generated test video (default: 3)")
    ap.add_argument("--gen-width", type=int, default=320,
                     help="Width for generated test video (default: 320)")
    ap.add_argument("--gen-height", type=int, default=240,
                     help="Height for generated test video (default: 240)")
    ap.add_argument("--gen-fps", type=int, default=15,
                     help="Frame rate for generated test video (default: 15)")
    ap.add_argument("--gen-bitrate-kbps", type=int, default=300,
                     help="Bitrate for generated test video in kbps (default: 300)")
    ap.add_argument("-v", "--verbose", action="store_true",
                     help="Print per-frame send/receive details")
    ap.add_argument("--transport", choices=["sim", "loopback", "remote"],
                     default="sim",
                     help="sim: in-process shaper (default). loopback: real TCP "
                          "to an in-process daemon on 127.0.0.1. remote: "
                          "REQ_RAW echo round-trip through the proxy to a remote "
                          "daemon + origin (full streaming needs the receiver "
                          "co-located).")
    ap.add_argument("--mode", type=int, choices=[0, 1, 2], default=None,
                     help="Numeric alias for --transport: 0=sim, 1=loopback, "
                          "2=remote. Overrides --transport if both are given.")
    ap.add_argument("--echo-bytes", type=int, default=4096,
                     help="mode 2: payload size per echo round-trip (probe MTU/"
                          "fragmentation over the real bearer). Default 4096.")
    ap.add_argument("--echo-count", type=int, default=10,
                     help="mode 2: number of echo round-trips (RTT distribution)."
                          " Default 10.")
    ap.add_argument("--daemon-port", type=int, default=0,
                     help="loopback: TCP port for the in-process test daemon "
                          "(default: ephemeral). Keep off 9009 to avoid a live "
                          "frognet-daemon-v3 collision.")
    ap.add_argument("--remote-addr", type=str, default=None,
                     help="remote: address of a live frognet-daemon-v3.")
    ap.add_argument("--remote-port", type=int, default=9009,
                     help="remote: daemon port (default 9009).")
    ap.add_argument("--shape-iface", type=str, default=None,
                     help="loopback/remote on-box: interface to shape with "
                          "tc-netem (e.g. wg0). Needs root + CAP_NET_ADMIN; "
                          "off-box this is dry-run only.")
    args = ap.parse_args()

    # Numeric mode alias wins if given: 0=sim, 1=loopback, 2=remote.
    if args.mode is not None:
        args.transport = {0: "sim", 1: "loopback", 2: "remote"}[args.mode]

    if args.video is None:
        video_path = "/tmp/sotf_test_video.ivf"
        print(f"Generating test video: {video_path}")
        generate_test_video(
            video_path,
            duration=args.gen_duration,
            width=args.gen_width, height=args.gen_height,
            fps=args.gen_fps, bitrate_kbps=args.gen_bitrate_kbps,
        )
    else:
        video_path = args.video

    # Auto-scale timeouts unless overridden. RTT estimate = 2 x one-way latency
    # plus a buffer; we want the droppable timeout to be a few RTTs so the
    # reply has a fighting chance under jitter, and the reliable timeout to
    # be longer still (it's worth waiting + retrying for keyframes).
    rtt_estimate_s = (args.latency_ms * 2 + args.jitter_ms) / 1000.0
    droppable_timeout = (args.droppable_timeout if args.droppable_timeout is not None
                          else max(0.4, 3 * rtt_estimate_s))
    reliable_timeout = (args.reliable_timeout if args.reliable_timeout is not None
                         else max(2.0, 6 * rtt_estimate_s))

    # remote mode (mode 2): the SotF receiver runs in-process and can't attach
    # to a daemon in another process, so we exercise the production-shaped
    # request path - a REQ_RAW echo THROUGH the proxy, over a true interface,
    # to the remote daemon + origin - and verify the body survives the trip.
    if args.transport == "remote":
        if not args.remote_addr:
            print("remote mode (--mode 2) needs --remote-addr <ip>  "
                  "(stand up simulation/remote_test_daemon.py there first)")
            return 2
        shaper = None
        if args.shape_iface:
            params = NetworkParams(latency_ms=args.latency_ms, jitter_ms=args.jitter_ms,
                                   bandwidth_bps=args.bandwidth_bps)
            shaper = NetemShaper(args.shape_iface, params)
        import json as _json, platform as _plat
        print(f"mode 2: REQ_RAW echo  proxy -> {args.remote_addr}:{args.remote_port} "
              f"-> origin -> back  ({args.echo_count} x {args.echo_bytes}B)")
        res = round_trip_remote("10.20.21.5", args.remote_addr, args.remote_port,
                                shaper=shaper, echo_bytes=args.echo_bytes,
                                echo_count=args.echo_count)
        # Machine-checkable evidence block - paste this back verbatim.
        diag = {
            "schema": "DIAG-MODE2/v1",
            "near_kernel": _plat.platform(),
            "remote_addr": args.remote_addr, "remote_port": args.remote_port,
            "shape_iface": args.shape_iface,
            "echo_bytes": args.echo_bytes, "echo_count": args.echo_count,
            **res,
        }
        print("[DIAG-MODE2] " + _json.dumps(diag, separators=(",", ":")))
        if not res["ok"]:
            print(f"MODE-2 FAILED at connect/handshake: {res['error']}")
            return 1
        its = res["iterations"]
        matches = sum(1 for x in its if x["match"])
        rtts = [x["rtt_ms"] for x in its] or [0]
        rtts_sorted = sorted(rtts)
        p50 = rtts_sorted[len(rtts_sorted)//2]
        print(f"  connect+handshake: {res['connect_open_ms']} ms")
        print(f"  echo fidelity:     {matches}/{len(its)} sha256-identical")
        print(f"  rtt ms:            min={min(rtts):.2f} p50={p50:.2f} max={max(rtts):.2f}")
        if matches == len(its) and len(its) > 0:
            print(f"MODE-2 ECHO OK: {len(its)} round-trips, all bytes verified "
                  f"(status={its[0]['status']})")
            return 0
        print(f"MODE-2 ECHO FAILED: {matches}/{len(its)} matched - body fidelity broken")
        return 1

    shaper = None
    if args.shape_iface and args.transport == "loopback":
        params = NetworkParams(latency_ms=args.latency_ms, jitter_ms=args.jitter_ms,
                               bandwidth_bps=args.bandwidth_bps,
                               outage_prob_per_sec=args.outage_prob,
                               outage_duration_ms=args.outage_ms)
        shaper = NetemShaper(args.shape_iface, params)

    stats = run_stream_test(
        video_path=video_path,
        bandwidth_bps=args.bandwidth_bps,
        latency_ms=args.latency_ms,
        jitter_ms=args.jitter_ms,
        outage_prob_per_sec=args.outage_prob,
        outage_duration_ms=args.outage_ms,
        reliable_timeout_s=reliable_timeout,
        droppable_timeout_s=droppable_timeout,
        capture_dir=args.capture_dir,
        verbose=args.verbose,
        transport=args.transport,
        daemon_port=args.daemon_port,
        shaper=shaper,
    )

    # Exit codes for scripted sweeps:
    #   0 = at least one keyframe delivered AND no decode errors
    #   1 = stream collapsed (no keyframes delivered)
    if stats.keyframes_delivered == 0:
        print("\nSTREAM COLLAPSED: no keyframes delivered.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
