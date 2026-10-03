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
sotf_degradation_ladder.py - Under fixed contested-RF conditions, run a
sequence of messaging modes from "full video" down to "single beacon" and
characterize what's actually deliverable at each rung of the ladder.

THE QUESTION:
  When the bearer is bad enough that full video collapses (we saw 2.5%
  delivery at 16 kbps with 20%/600ms outages), what's the smallest message
  type that DOES survive, and what messaging rate can be sustained at
  each rung?

THE LADDER (large frames -> smallest possible wire signal):

  L1  video keyframe       ~4500 B raw -> ~5000 B wire     1/sec
  L2  video delta          ~700 B raw  -> ~900 B wire      ~14/sec
  L3  audio active voice   60 B raw    -> ~120 B wire      50/sec
  L4  audio DTX silence    3 B raw     -> ~50 B wire       50/sec
  L5  JSON sensor diff     ~40 B fields -> ~50 B wire      1-10/sec
  L6  text status          ~20 B body  -> ~40 B wire       0.1-1/sec
  L7  REQ_REPEAT beacon    0 B payload -> 25 B wire        as needed

  L7 (REQ_REPEAT, 25 bytes) is the FNW1 protocol floor: 4-byte length
  prefix + 4-byte MAGIC + 1-byte op + 16-byte req_hash. It carries ZERO
  payload bytes but addresses one shared-state element by its hash. The
  semantic content is "the state at this address has not changed" -
  which is exactly the "I am here, my world is unchanged" signal.

CONTESTED-RF CONDITIONS (fixed for all rungs):
  bandwidth:       2000 B/s   (16 kbps - low-band HF or jammed sub-GHz)
  latency one-way: 100 ms
  jitter:          +/-100 ms
  outage prob/sec: 0.20       (one outage every ~5 sec on average)
  outage duration: 600 ms

OUTAGE MODEL: TCP-effective stall. Bytes pile up at the boundary during
an outage, then drain through afterward. The application sees a latency
spike, not byte loss. A request that spans an outage either completes
late (and might exceed its application-layer timeout) or just experiences
extra latency. This matches real WireGuard-over-radio behavior where TCP
retransmission absorbs bit errors below FNW1.
"""
from __future__ import annotations

import os
import sys
import time
import types
import json
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Imports from the transport tier (also runs the stubs)
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p and p not in sys.path:
        sys.path.insert(0, p)

from transport_sim_tier import (                                # noqa: E402
    NetworkParams, create_connected_pair,
    wrap_req_full, wrap_req_diff, wrap_req_repeat, try_parse,
    OP_RESP_DIFF, OP_RESP_SAME,
    REQ_HASH_LEN,
)
from sotf_media_tier import SotFMediaHandler                    # noqa: E402
from core.codec import SemanticCodec                            # noqa: E402
from core.json_handler import JsonFormatHandler                 # noqa: E402
from core.text_handler import TextFormatHandler                 # noqa: E402
from transport_factories import connect_pair, NetemShaper       # noqa: E402


# ============================================================================
# Contested-RF profile
# ============================================================================

CONTESTED_RF = NetworkParams(
    latency_ms=100.0,
    jitter_ms=100.0,
    bandwidth_bps=2000.0,
    outage_prob_per_sec=0.20,
    outage_duration_ms=600.0,
)


# ============================================================================
# Per-rung result tracking
# ============================================================================

@dataclass
class RungResult:
    name: str
    description: str
    msgs_sent: int = 0
    msgs_delivered: int = 0
    msgs_lost: int = 0
    wire_bytes_per_msg: int = 0     # mean, computed from totals
    wire_bytes_total: int = 0
    raw_payload_per_msg: int = 0
    rtts_ms: List[float] = field(default_factory=list)
    wall_time_ms: float = 0.0
    timeout_s: float = 0.0
    target_rate_msgs_per_sec: float = 0.0


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values); k = (len(s) - 1) * p
    f = int(k); c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _hash_for(s: str) -> bytes:
    return hashlib.blake2b(s.encode("utf-8"), digest_size=REQ_HASH_LEN).digest()


# ============================================================================
# Helpers: encode/decode round-trip for each message class
# ============================================================================

def _make_voice_frame(seq: int, size: int = 60) -> bytes:
    """Deterministic Opus-shaped active-voice bytes - same as sotf_media_tier."""
    seed = (seq * 2654435761) & 0xFFFFFFFF
    out = bytearray(size)
    for i in range(size):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        out[i] = (seed >> 16) & 0xFF
    return bytes(out)


_DTX_SILENCE = bytes([0xF8, 0xFF, 0xFE])   # plausible Opus DTX silence


# ============================================================================
# Run one rung
# ============================================================================

def run_rung(name: str, description: str,
              make_frames: callable,    # () -> List[(wire_frame_bytes, raw_payload_size)]
              count: int,
              timeout_s: float,
              target_rate_msgs_per_sec: float,
              fragment: Optional[Dict[str, Any]] = None,
              req_hash: Optional[bytes] = None,
              pipelined: bool = False,
              transport: str = "sim",
              shaper: Optional[NetemShaper] = None) -> RungResult:
    """Set up a wire under CONTESTED_RF, send `count` messages produced by
    `make_frames(i)`, measure delivery.

    make_frames takes the message index and returns (wire_frame, raw_payload_size).
    The wire_frame is the FNW1 frame to send (output of wrap_req_*); the
    raw_payload_size is the original application-level bytes the frame
    carries (used for the wire-vs-raw ratio).

    pipelined=False (default): each send blocks until its reply arrives, then
        the next send fires (paced to target_rate). Suitable for non-real-time
        messaging where each request is acknowledged before the next.

    pipelined=True: requests are fired at target_rate from a fanout of worker
        threads - multiple requests can be in flight concurrently, replies
        arrive interleaved and are matched by seq. This is how production
        real-time media MUST send: a 50/s Opus frame stream over a 100ms RTT
        link is impossible synchronously, viable pipelined."""
    result = RungResult(
        name=name, description=description,
        timeout_s=timeout_s,
        target_rate_msgs_per_sec=target_rate_msgs_per_sec,
    )

    # CONTESTED_RF shaping lives in the sim path (Python shaper) or in an
    # on-box tc-netem `shaper`. In-container loopback has no kernel netem, so
    # it reproduces the CLEAN-wire transport, not the contested profile -
    # useful for isolating protocol overhead from bearer degradation.
    proxy, daemon, w1, w2 = connect_pair(
        local_gw=f"10.20.22.{abs(hash(name)) % 250 + 1}",
        mode=transport,
        fwd_params=CONTESTED_RF, rev_params=CONTESTED_RF,
        default_fragment=fragment,
        shaper=shaper,
    )

    try:
        if pipelined:
            _run_pipelined(proxy, make_frames, count, timeout_s,
                            target_rate_msgs_per_sec, result)
        else:
            _run_sequential(proxy, make_frames, count, timeout_s,
                             target_rate_msgs_per_sec, result)
    finally:
        proxy.close()
        daemon.stop()
        w1.close()
        w2.close()

    if result.msgs_sent > 0:
        result.wire_bytes_per_msg = result.wire_bytes_total // result.msgs_sent
    return result


def _run_sequential(proxy, make_frames, count, timeout_s,
                     target_rate, result: RungResult) -> None:
    """Synchronous: send one, wait for reply, send next. Paced to target_rate."""
    target_interval = 1.0 / target_rate if target_rate > 0 else 0.0
    t0 = time.perf_counter()
    next_send = t0

    for i in range(count):
        now = time.perf_counter()
        if next_send > now:
            time.sleep(next_send - now)
        send_t = time.perf_counter()

        frame, raw_size = make_frames(i)
        reply = proxy.send_request(frame, timeout=timeout_s)
        done_t = time.perf_counter()

        result.msgs_sent += 1
        result.wire_bytes_total += 4 + len(frame)
        result.raw_payload_per_msg = raw_size

        if reply is not None:
            msg = try_parse(reply)
            if msg is not None and msg.op in (OP_RESP_DIFF, OP_RESP_SAME):
                result.msgs_delivered += 1
                result.rtts_ms.append((done_t - send_t) * 1000.0)
                next_send = send_t + target_interval
                continue

        result.msgs_lost += 1
        next_send = send_t + target_interval

    result.wall_time_ms = (time.perf_counter() - t0) * 1000.0


def _run_pipelined(proxy, make_frames, count, timeout_s,
                    target_rate, result: RungResult) -> None:
    """Fire requests at target_rate from a worker pool; replies arrive
    interleaved and are matched by the proxy's seq-tagging. A reply that
    arrives AFTER its deadline is counted as lost (real-time semantics:
    a late Opus frame is unplayable).

    Each request gets its own deadline = send_time + timeout_s. We fire all
    count requests up front (paced to target_rate), then wait for them all
    to complete (or hit deadline) and tally."""
    import concurrent.futures as cf

    target_interval = 1.0 / target_rate if target_rate > 0 else 0.0
    outcomes_lock = threading.Lock()
    outcomes: List[Tuple[bool, float, int, int]] = []  # (ok, rtt_ms, wire_bytes, raw_size)

    def fire_one(i: int):
        frame, raw_size = make_frames(i)
        send_t = time.perf_counter()
        reply = proxy.send_request(frame, timeout=timeout_s)
        done_t = time.perf_counter()
        rtt_ms = (done_t - send_t) * 1000.0
        ok = False
        if reply is not None:
            msg = try_parse(reply)
            if msg is not None and msg.op in (OP_RESP_DIFF, OP_RESP_SAME):
                ok = True
        with outcomes_lock:
            outcomes.append((ok, rtt_ms, 4 + len(frame), raw_size))

    t0 = time.perf_counter()
    # Use a thread pool sized to allow the target rate even at high RTT.
    # If each request takes ~RTT seconds and target_rate is N/s, we need
    # ~N x RTT workers. With RTT capped at timeout_s, that's count workers
    # worst case; cap at 64 to keep things sane.
    pool_size = min(count, max(16, int(target_rate * timeout_s) + 4))
    with cf.ThreadPoolExecutor(max_workers=pool_size,
                                thread_name_prefix="ladder-pipe") as pool:
        futures = []
        for i in range(count):
            target_send_at = t0 + i * target_interval
            now = time.perf_counter()
            if target_send_at > now:
                time.sleep(target_send_at - now)
            futures.append(pool.submit(fire_one, i))

        # Wait for all sends to finish (or timeout - send_request enforces it)
        for f in futures:
            try:
                f.result(timeout=timeout_s + 2.0)
            except Exception:
                pass

    result.wall_time_ms = (time.perf_counter() - t0) * 1000.0
    for ok, rtt_ms, wire_b, raw_size in outcomes:
        result.msgs_sent += 1
        result.wire_bytes_total += wire_b
        result.raw_payload_per_msg = raw_size
        if ok:
            result.msgs_delivered += 1
            result.rtts_ms.append(rtt_ms)
        else:
            result.msgs_lost += 1


# ============================================================================
# Construct each rung's frame factory
# ============================================================================

# Shared codec
_codec = SemanticCodec()


def _build_sotf_factory(session_id: str, fragment: Dict[str, Any],
                        req_hash: bytes, payload_provider: callable,
                        opcode: int):
    """Returns (make_frames, fragment) - make_frames produces FNW1 frames
    using REAL SotF codex encoding (first = REQ_FULL, then REQ_DIFF)."""
    handler = SotFMediaHandler()
    state = {"reference": None}

    def make_frames(i: int) -> Tuple[bytes, int]:
        payload = payload_provider(i)
        frame_dict = {
            "seq": i, "payload": payload,
            "session_id": session_id,
            "codec": fragment["baseline"]["codec"],
            "sr": fragment["baseline"]["sr"],
            "layout": fragment["baseline"]["layout"],
            "level_idx": fragment["baseline"]["level_idx"],
        }
        dyn = handler.extract_request_dynamic(frame_dict, fragment)
        if state["reference"] is None:
            encoded = _codec.encode_request(
                opcode=opcode, url_vals=[], json_vals=dyn,
                type_map=fragment["type_map"], tokens=fragment["tokens"],
                compress=True,
            )
            wire = wrap_req_full(req_hash, encoded)
            state["reference"] = {k: v for (k, v) in dyn}
        else:
            encoded, new_ref, is_identical = _codec.encode_request_diff(
                opcode=opcode, url_vals=[], json_vals=dyn,
                type_map=fragment["type_map"], reference=state["reference"],
                tokens=fragment["tokens"], compress=True,
            )
            if is_identical:
                wire = wrap_req_repeat(req_hash)
            else:
                wire = wrap_req_diff(req_hash, encoded)
            state["reference"] = new_ref
        return wire, len(payload)

    return make_frames


def _build_json_factory(req_hash: bytes, opcode: int = 0xC0DEC710
                         ) -> Tuple[callable, Dict[str, Any]]:
    """Sensor-reading JSON: first frame REQ_FULL learns the template, then
    REQ_DIFFs carry only changed fields (Value, ts, seq)."""
    handler = JsonFormatHandler()
    body0 = json.dumps({
        "SensorType": "DHT22", "SensorName": "site.sw.dht22.temp",
        "Location": "outdoor", "Units": "celsius",
        "Value": 20.0, "ts": 1700000000, "seq": 0,
    }, separators=(",", ":"))
    fragment = handler.learn_request_template(body0)
    state = {"reference": None}

    def make_frames(i: int) -> Tuple[bytes, int]:
        body = json.dumps({
            "SensorType": "DHT22", "SensorName": "site.sw.dht22.temp",
            "Location": "outdoor", "Units": "celsius",
            "Value": 20.0 + i * 0.1, "ts": 1700000000 + i * 60, "seq": i,
        }, separators=(",", ":"))
        dyn = handler.extract_request_dynamic(body, fragment)
        if state["reference"] is None:
            encoded = _codec.encode_request(
                opcode=opcode, url_vals=[], json_vals=dyn,
                type_map=fragment["type_map"], tokens=fragment["tokens"],
                compress=True,
            )
            wire = wrap_req_full(req_hash, encoded)
            state["reference"] = {k: v for (k, v) in dyn}
        else:
            encoded, new_ref, is_identical = _codec.encode_request_diff(
                opcode=opcode, url_vals=[], json_vals=dyn,
                type_map=fragment["type_map"], reference=state["reference"],
                tokens=fragment["tokens"], compress=True,
            )
            if is_identical:
                wire = wrap_req_repeat(req_hash)
            else:
                wire = wrap_req_diff(req_hash, encoded)
            state["reference"] = new_ref
        return wire, len(body)

    return make_frames, fragment


def _build_status_text_factory(req_hash: bytes) -> Tuple[callable, Dict[str, Any]]:
    """A short status string (e.g., "OK seq=N"). Uses the real text handler.
    Most messages will be REQ_DIFF or REQ_REPEAT (if status unchanged)."""
    handler = TextFormatHandler()
    body0 = "OK seq=0"
    fragment = handler.learn_request_template(body0)
    state = {"reference": None}

    def make_frames(i: int) -> Tuple[bytes, int]:
        body = f"OK seq={i}"
        dyn = handler.extract_request_dynamic(body, fragment)
        if state["reference"] is None:
            encoded = _codec.encode_request(
                opcode=0xC0DEC720, url_vals=[], json_vals=dyn,
                type_map=fragment["type_map"], tokens=fragment["tokens"],
                compress=True,
            )
            wire = wrap_req_full(req_hash, encoded)
            state["reference"] = {k: v for (k, v) in dyn}
        else:
            encoded, new_ref, is_identical = _codec.encode_request_diff(
                opcode=0xC0DEC720, url_vals=[], json_vals=dyn,
                type_map=fragment["type_map"], reference=state["reference"],
                tokens=fragment["tokens"], compress=True,
            )
            if is_identical:
                wire = wrap_req_repeat(req_hash)
            else:
                wire = wrap_req_diff(req_hash, encoded)
            state["reference"] = new_ref
        return wire, len(body)

    return make_frames, fragment


def _build_beacon_factory(req_hash: bytes):
    """The protocol floor: REQ_REPEAT - 25 bytes on the wire (4 length +
    4 MAGIC + 1 op + 16 req_hash). Zero payload bytes; semantic content
    is 'state at this address has not changed' (= 'I am here')."""
    def make_frames(i: int) -> Tuple[bytes, int]:
        return wrap_req_repeat(req_hash), 0
    return make_frames


# ============================================================================
# Compose the ladder
# ============================================================================

def build_all_rungs() -> List[Tuple[str, str, callable,
                                     Optional[Dict[str, Any]],
                                     bytes, int, float, float, bool]]:
    """Returns list of (name, description, make_frames, fragment, req_hash,
    count, timeout_s, target_msgs_per_sec, pipelined)."""
    rungs = []

    # L1, L2: video - encode the actual VP8 IVF generated by the video test
    ivf_path = "/tmp/sotf_test_video.ivf"
    if os.path.exists(ivf_path):
        # Use the existing IVF to drive realistic keyframe + delta sequences.
        # We split into "all keyframes" and "all deltas" for the ladder.
        from sotf_video_stream_test import parse_ivf
        _, frames = parse_ivf(ivf_path)
        keyframes = [f for f in frames if f.is_keyframe]
        deltas = [f for f in frames if not f.is_keyframe]

        # L1: video keyframes - application tolerance: 1.0s (longer = visible freeze)
        sotf_fragment = SotFMediaHandler().extract_template({
            "session_id": "lad-vid-key", "codec": "vp8",
            "sr": 0, "layout": "video", "level_idx": 0,
        })
        rh_key = _hash_for("ladder-video-key")
        provider_key = lambda i: keyframes[i % len(keyframes)].payload
        rungs.append((
            "L1 video keyframe",
            f"VP8 I-frame, ~{sum(len(f.payload) for f in keyframes)//len(keyframes)}B raw per frame",
            _build_sotf_factory("lad-vid-key", sotf_fragment, rh_key,
                                 provider_key, 0xC0DEC701),
            sotf_fragment, rh_key,
            20, 1.0, 1.0, True,    # pipelined: video sender can fan out
        ))

        # L2: video deltas - application tolerance: 0.5s
        sotf_fragment2 = SotFMediaHandler().extract_template({
            "session_id": "lad-vid-delta", "codec": "vp8",
            "sr": 0, "layout": "video", "level_idx": 0,
        })
        rh_delta = _hash_for("ladder-video-delta")
        provider_delta = lambda i: deltas[i % len(deltas)].payload
        rungs.append((
            "L2 video delta",
            f"VP8 P-frame, ~{sum(len(f.payload) for f in deltas)//len(deltas)}B raw per frame",
            _build_sotf_factory("lad-vid-delta", sotf_fragment2, rh_delta,
                                 provider_delta, 0xC0DEC702),
            sotf_fragment2, rh_delta,
            30, 0.5, 14.0, True,
        ))

    # L3: audio active voice - Opus is real-time, 200ms is edge of usability
    sotf_frag_voice = SotFMediaHandler().extract_template({
        "session_id": "lad-voice", "codec": "opus",
        "sr": 48000, "layout": "stereo", "level_idx": 4,
    })
    rh_voice = _hash_for("ladder-voice")
    rungs.append((
        "L3 audio active",
        "Opus L4 active voice, 60B raw per 20ms frame",
        _build_sotf_factory("lad-voice", sotf_frag_voice, rh_voice,
                             lambda i: _make_voice_frame(i, 60), 0xC0DEC703),
        sotf_frag_voice, rh_voice,
        50, 0.2, 50.0, True,
    ))

    # L4: audio DTX silence - same real-time constraint
    sotf_frag_dtx = SotFMediaHandler().extract_template({
        "session_id": "lad-dtx", "codec": "opus",
        "sr": 48000, "layout": "stereo", "level_idx": 4,
    })
    rh_dtx = _hash_for("ladder-dtx")
    rungs.append((
        "L4 audio DTX",
        "Opus DTX silence, 3B raw per frame (REQ_REPEAT after the first)",
        _build_sotf_factory("lad-dtx", sotf_frag_dtx, rh_dtx,
                             lambda i: _DTX_SILENCE, 0xC0DEC704),
        sotf_frag_dtx, rh_dtx,
        50, 0.2, 50.0, True,
    ))

    # L5: JSON sensor telemetry - non-real-time, 2s tolerance is fine
    json_factory, json_frag = _build_json_factory(_hash_for("ladder-json"))
    rungs.append((
        "L5 JSON sensor diff",
        "DHT22 reading via JsonFormatHandler, REQ_DIFF (Value/ts/seq only)",
        json_factory, json_frag, _hash_for("ladder-json"),
        30, 2.0, 1.0, False,    # sequential: telemetry is request/ack
    ))

    # L6: short text status - non-real-time
    text_factory, text_frag = _build_status_text_factory(_hash_for("ladder-text"))
    rungs.append((
        "L6 status text",
        "Short status body via TextFormatHandler",
        text_factory, text_frag, _hash_for("ladder-text"),
        30, 2.0, 1.0, False,
    ))

    # L7: presence beacon - non-real-time
    rungs.append((
        "L7 REQ_REPEAT beacon",
        "Bare REQ_REPEAT, 25B on the wire (the protocol floor)",
        _build_beacon_factory(_hash_for("ladder-beacon")),
        None,
        _hash_for("ladder-beacon"),
        50, 2.0, 1.0, False,
    ))

    return rungs


# ============================================================================
# Main
# ============================================================================

def _print_header():
    print("=" * 100)
    print("SotF DEGRADATION LADDER under contested RF")
    print("=" * 100)
    print(f"  bandwidth:       {CONTESTED_RF.bandwidth_bps:.0f} B/s "
          f"({CONTESTED_RF.bandwidth_bps*8/1000:.1f} kbps)")
    print(f"  latency one-way: {CONTESTED_RF.latency_ms} ms")
    print(f"  jitter:          +/-{CONTESTED_RF.jitter_ms} ms")
    print(f"  outage prob/sec: {CONTESTED_RF.outage_prob_per_sec}")
    print(f"  outage duration: {CONTESTED_RF.outage_duration_ms} ms")
    print(f"  outage model:    STALL (TCP-effective; bytes buffered, drained "
          f"on resume - no data loss, latency spike instead)")
    print()


def _print_results_table(results: List[RungResult]):
    print()
    print("=" * 115)
    print("LADDER RESULTS")
    print("=" * 115)
    hdr = (f"{'rung':<24} "
           f"{'wire B':>7} {'raw B':>6} "
           f"{'sent':>5} {'del':>4} {'lost':>5} {'rate%':>6} "
           f"{'p50':>5} {'p95':>5} "
           f"{'achieved':>9} {'max@wire':>9}")
    print(hdr)
    print("-" * 115)
    for r in results:
        delivery_pct = (100.0 * r.msgs_delivered / r.msgs_sent
                        if r.msgs_sent else 0.0)
        p50 = _percentile(r.rtts_ms, 0.5)
        p95 = _percentile(r.rtts_ms, 0.95)
        effective_rate = (r.msgs_delivered * 1000.0 / r.wall_time_ms
                          if r.wall_time_ms > 0 else 0.0)
        # Max sustainable rate at this wire bandwidth = bw / wire_bytes_per_msg
        max_rate = (CONTESTED_RF.bandwidth_bps / r.wire_bytes_per_msg
                     if r.wire_bytes_per_msg > 0 else float("inf"))
        def fmt(x):
            return f"{x:.0f}" if x == x else "-"
        print(f"{r.name:<24} "
              f"{r.wire_bytes_per_msg:>7} {r.raw_payload_per_msg:>6} "
              f"{r.msgs_sent:>5} {r.msgs_delivered:>4} {r.msgs_lost:>5} "
              f"{delivery_pct:>5.1f}% "
              f"{fmt(p50):>5} {fmt(p95):>5} "
              f"{effective_rate:>8.1f}/s {max_rate:>8.1f}/s")
    print("-" * 115)
    print(f"max@wire = bandwidth ({CONTESTED_RF.bandwidth_bps:.0f} B/s) / wire_bytes_per_msg")
    print(f"           - the rate at which message-of-this-size can be pushed if the wire is "
          f"fully utilized for that traffic")


def _print_interpretation(results: List[RungResult]):
    print()
    print("INTERPRETATION - viability of each rung under the contested-RF profile above")
    print("-" * 100)
    required_rates = {
        "L1 video keyframe":    1.0,
        "L2 video delta":       14.0,
        "L3 audio active":      45.0,
        "L4 audio DTX":         45.0,
        "L5 JSON sensor diff":  0.2,
        "L6 status text":       0.1,
        "L7 REQ_REPEAT beacon": 0.05,
    }
    for r in results:
        delivery_pct = (100.0 * r.msgs_delivered / r.msgs_sent
                        if r.msgs_sent else 0.0)
        max_rate = (CONTESTED_RF.bandwidth_bps / r.wire_bytes_per_msg
                     if r.wire_bytes_per_msg > 0 else float("inf"))
        required = required_rates.get(r.name, 0.0)
        margin = max_rate / required if required > 0 else float("inf")

        if delivery_pct >= 90 and max_rate >= required:
            verdict = (f"VIABLE - wire has {margin:.0f}x headroom for the "
                       f"required {required:g}/s")
        elif max_rate < required and delivery_pct < 50:
            verdict = (f"COLLAPSED - required {required:g}/s exceeds "
                       f"wire max {max_rate:.1f}/s by {required/max_rate:.1f}x")
        elif max_rate < required:
            verdict = (f"DEGRADED - required {required:g}/s exceeds "
                       f"wire max {max_rate:.1f}/s by {required/max_rate:.1f}x")
        elif delivery_pct < 50:
            verdict = (f"INTERMITTENT - bandwidth OK ({margin:.0f}x headroom) "
                       f"but outages drop {100-delivery_pct:.0f}% of frames")
        else:
            verdict = f"MARGINAL - {delivery_pct:.0f}% delivery"
        print(f"  {r.name:<24} - {verdict}")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="SotF degradation ladder under the contested-RF profile.")
    ap.add_argument("--transport", choices=["sim", "loopback"], default="sim",
                     help="sim: in-process Python shaper applies CONTESTED_RF "
                          "(default). loopback: real TCP to an in-process "
                          "daemon; CONTESTED_RF shaping only bites with an "
                          "on-box --shape-iface (in-container = clean wire).")
    ap.add_argument("--shape-iface", type=str, default=None,
                     help="on-box: interface to apply CONTESTED_RF to via "
                          "tc-netem (root + CAP_NET_ADMIN; dry-run off-box).")
    args = ap.parse_args()

    shaper = None
    if args.shape_iface and args.transport == "loopback":
        shaper = NetemShaper(args.shape_iface, CONTESTED_RF, outage_thread=True)

    _print_header()
    print(f"transport: {args.transport}"
          + (f"  shape-iface: {args.shape_iface}" if args.shape_iface else ""))
    rungs = build_all_rungs()
    results: List[RungResult] = []

    for (name, description, factory, fragment, req_hash,
         count, timeout_s, target_rate, pipelined) in rungs:
        mode = "PIPELINED" if pipelined else "sequential"
        print(f"running {name}: {description}")
        print(f"  count={count}  timeout={timeout_s}s  target={target_rate} msg/s  mode={mode}")
        r = run_rung(name, description, factory, count, timeout_s,
                      target_rate, fragment=fragment, req_hash=req_hash,
                      pipelined=pipelined, transport=args.transport,
                      shaper=shaper)
        results.append(r)
        delivery = (100.0 * r.msgs_delivered / r.msgs_sent
                    if r.msgs_sent else 0.0)
        print(f"  -> {r.msgs_delivered}/{r.msgs_sent} ({delivery:.1f}%) "
              f"in {r.wall_time_ms:.0f}ms; "
              f"wire {r.wire_bytes_per_msg}B/msg\n")

    _print_results_table(results)
    _print_interpretation(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
