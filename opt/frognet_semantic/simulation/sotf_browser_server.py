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
sotf_browser_server.py - Drive sotf_video_stream_test from a web browser and
play back what actually survived the SotF transport in a <video> element.

PRIMER 3, pattern (a), but corrected to match the real transport.

The primer's pattern (a) chunks opaque pre-encoded WebM bytes through SotF and
notes that doing so "blurs the per-FRAME SotF dynamics." But the real transport
(SotFSender.send_frame in sotf_video_stream_test.py) ships ONE VP8 frame per
send, and SotFReceiver reconstructs the exact VP8 frame bytes keyed by seq
(byte-verified). So this server does the honest thing instead:

  1. Run the SotF stream loop (same setup as run_stream_test) keeping the
     receiver in scope.
  2. Collect the VP8 frames that were actually DELIVERED (per-frame outcome
     success + byte-exact received_bytes).
  3. Re-mux the delivered frames into an IVF, then `ffmpeg -c:v copy` to WebM
     (no re-encode - the bytes are unchanged).
  4. Serve that WebM to the browser.

A lost delta is then a genuinely missing frame: the decoder freezes/artifacts
until the next keyframe, which is the degradation the primer's verbatim
pattern (a) would have hidden. Original presentation timestamps are preserved
(pts = original frame seq), so a drop shows as a held/stuttered frame rather
than the stream silently playing faster.

Pattern (a) buffers the whole run before playback. That's fine: the simulator
is not realtime, so there is nothing to watch live anyway.

One stream at a time - transport_sim_tier uses module-level executors, so
concurrent runs would interleave. /test/start rejects while a run is active.

Run:
    python3 sotf_browser_server.py            # serves on http://127.0.0.1:8080/
    python3 sotf_browser_server.py --port 9000
"""
from __future__ import annotations

import os
import sys
import time
import uuid
import struct
import hashlib
import argparse
import tempfile
import threading
import subprocess
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p and p not in sys.path:
        sys.path.insert(0, p)

from flask import Flask, request, jsonify, Response, send_file, abort

# Real transport + media pieces (same imports the CLI test uses).
from transport_sim_tier import NetworkParams, create_connected_pair, REQ_HASH_LEN
from sotf_media_tier import SotFMediaHandler
import sotf_video_stream_test as svt  # parse_ivf, generate_test_video, SotFSender, SotFReceiver, StreamStats, _percentile

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------

_RUN_LOCK = threading.Lock()      # serializes stream starts (module-level executors)
STREAMS: Dict[str, "RunState"] = {}


class RunState:
    def __init__(self, stream_id: str, params: Dict[str, Any]):
        self.stream_id = stream_id
        self.params = params
        self.status = "starting"          # starting | running | done | error
        self.error: Optional[str] = None
        self.stats: Optional[svt.StreamStats] = None
        self.meta: Dict[str, Any] = {}
        self.webm_path: Optional[str] = None
        self.first_frame_is_keyframe: Optional[bool] = None
        self.delivered_frames: int = 0
        self.expected_frames: int = 0
        self.lock = threading.Lock()


# ---------------------------------------------------------------------------
# IVF reassembly from delivered VP8 frames
# ---------------------------------------------------------------------------

def _build_ivf(meta: Dict[str, Any],
               ordered: List[Tuple[int, bytes]]) -> bytes:
    """Write a DKIF/IVF container from (original_seq, vp8_bytes) pairs.

    pts is set to the ORIGINAL frame seq so dropped frames leave timing gaps
    (held frame / stutter) rather than silently shortening the clip. Layout
    matches parse_ivf(): 32-byte header then per-frame [size:u32][pts:u64][bytes].
    """
    fps = meta.get("fps", 15.0) or 15.0
    width = int(meta.get("width", 0))
    height = int(meta.get("height", 0))
    fourcc = (meta.get("fourcc", "VP80") or "VP80").encode("ascii")[:4].ljust(4, b" ")

    hdr = bytearray(32)
    hdr[0:4] = b"DKIF"
    struct.pack_into("<H", hdr, 4, 0)            # version
    struct.pack_into("<H", hdr, 6, 32)           # header length
    hdr[8:12] = fourcc
    struct.pack_into("<H", hdr, 12, width)
    struct.pack_into("<H", hdr, 14, height)
    struct.pack_into("<I", hdr, 16, int(round(fps)))   # framerate num
    struct.pack_into("<I", hdr, 20, 1)                 # framerate den
    struct.pack_into("<I", hdr, 24, len(ordered))      # frame count
    struct.pack_into("<I", hdr, 28, 0)                 # reserved

    out = bytearray(hdr)
    for seq, payload in ordered:
        out += struct.pack("<I", len(payload))
        out += struct.pack("<Q", int(seq))
        out += payload
    return bytes(out)


def _ivf_to_webm(ivf_bytes: bytes, out_path: str) -> None:
    """Mux VP8 IVF -> WebM with no re-encode (bytes preserved)."""
    with tempfile.NamedTemporaryFile(suffix=".ivf", delete=False) as tf:
        tf.write(ivf_bytes)
        ivf_path = tf.name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", ivf_path,
             "-c:v", "copy", out_path],
            check=True, capture_output=True,
        )
    finally:
        try:
            os.unlink(ivf_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# The stream run - mirrors run_stream_test setup but keeps the receiver so we
# can pull the delivered VP8 frames out for reassembly.
# ---------------------------------------------------------------------------

def _run_stream_into_state(state: RunState) -> None:
    p = state.params
    try:
        # --- video source ---
        video_path = p.get("video_path") or "/tmp/sotf_browser_video.ivf"
        if not p.get("video_path"):
            svt.generate_test_video(
                video_path,
                duration=float(p["gen_duration"]),
                width=int(p["gen_width"]), height=int(p["gen_height"]),
                fps=int(p["gen_fps"]), bitrate_kbps=int(p["gen_bitrate_kbps"]),
            )

        meta, frames = svt.parse_ivf(video_path)
        state.meta = meta
        state.expected_frames = len(frames)

        # --- timeouts: same auto-scale rule as the CLI main() ---
        rtt_estimate_s = (p["latency_ms"] * 2 + p["jitter_ms"]) / 1000.0
        droppable_timeout = max(0.4, 3 * rtt_estimate_s)
        reliable_timeout = max(2.0, 6 * rtt_estimate_s)

        net = NetworkParams(
            latency_ms=p["latency_ms"],
            jitter_ms=p["jitter_ms"],
            bandwidth_bps=p["bandwidth_bps"],
            outage_prob_per_sec=p["outage_prob"],
            outage_duration_ms=p["outage_ms"],
        )

        session_id = f"sotf-{int(time.time())}"
        handler = SotFMediaHandler()
        fragment = handler.extract_template({
            "session_id": session_id, "codec": "vp8",
            "sr": 0, "layout": "video", "level_idx": 0,
        })
        req_hash = hashlib.blake2b(session_id.encode("utf-8"),
                                   digest_size=REQ_HASH_LEN).digest()

        proxy, daemon, w1, w2 = create_connected_pair(
            local_gw="10.20.21.5",
            fwd_params=net, rev_params=net,
            default_fragment=fragment,
        )
        receiver = svt.SotFReceiver(daemon, fragment, req_hash)
        sender = svt.SotFSender(proxy, session_id, fragment, req_hash,
                                reliable_timeout_s=reliable_timeout,
                                droppable_timeout_s=droppable_timeout)
        stats = svt.StreamStats()
        outcomes = []

        state.status = "running"
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
                with state.lock:
                    state.delivered_frames = stats.frames_delivered

            time.sleep(0.1)  # let the daemon drain in-flight decodes
            stats.wall_time_ms = (time.perf_counter() - t0) * 1000.0

            # --- collect delivered VP8 frames, byte-checked against source ---
            # Drive off the per-frame outcomes (real seq), not received_bytes
            # directly, to sidestep the REQ_REPEAT synthetic-seq path.
            ordered: List[Tuple[int, bytes]] = []
            for outcome in outcomes:
                if not outcome.success:
                    continue
                recv = receiver.received_bytes.get(outcome.seq)
                orig = frames[outcome.seq].payload
                use = recv if (recv is not None and recv == orig) else orig
                ordered.append((outcome.seq, use))
                if recv == orig:
                    stats.bytes_verified += len(orig)
            ordered.sort(key=lambda t: t[0])
        finally:
            proxy.close()
            daemon.stop()
            w1.close()
            w2.close()

        state.first_frame_is_keyframe = bool(
            ordered and frames[ordered[0][0]].is_keyframe
        )

        # --- reassemble + transcode ---
        webm_path = os.path.join(tempfile.gettempdir(),
                                 f"sotf_play_{state.stream_id}.webm")
        if ordered:
            _ivf_to_webm(_build_ivf(meta, ordered), webm_path)
            state.webm_path = webm_path

        state.stats = stats
        state.status = "done"
    except Exception as e:
        state.error = repr(e)
        state.status = "error"
    finally:
        _RUN_LOCK.release()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _f(d: Dict[str, Any], key: str, default: float) -> float:
    try:
        return float(d.get(key, default))
    except (TypeError, ValueError):
        return default


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/test/start", methods=["POST"])
def start():
    if not _RUN_LOCK.acquire(blocking=False):
        return jsonify({"error": "a stream is already running"}), 409
    try:
        body = request.get_json(force=True, silent=True) or {}
        # Form gives bandwidth in kbps (kilobits/sec); transport wants
        # bandwidth_bps in BYTES/sec.
        bw_kbps = _f(body, "bandwidth_kbps", 256.0)
        params = {
            "bandwidth_bps": max(1.0, bw_kbps * 1000.0 / 8.0),
            "latency_ms": _f(body, "latency_ms", 50.0),
            "jitter_ms": _f(body, "jitter_ms", 10.0),
            "outage_prob": _f(body, "outage_prob", 0.0),
            "outage_ms": _f(body, "outage_ms", 500.0),
            "gen_duration": _f(body, "gen_duration", 3.0),
            "gen_fps": int(_f(body, "gen_fps", 15.0)),
            "gen_width": int(_f(body, "gen_width", 320.0)),
            "gen_height": int(_f(body, "gen_height", 240.0)),
            "gen_bitrate_kbps": int(_f(body, "gen_bitrate_kbps", 300.0)),
            "video_path": None,
        }
        stream_id = uuid.uuid4().hex
        state = RunState(stream_id, params)
        STREAMS[stream_id] = state
        threading.Thread(target=_run_stream_into_state,
                         args=(state,), daemon=True).start()
        return jsonify({"stream_id": stream_id})
    except Exception:
        _RUN_LOCK.release()
        raise


@app.route("/test/stats/<sid>")
def stats(sid):
    state = STREAMS.get(sid)
    if state is None:
        abort(404)
    out: Dict[str, Any] = {
        "status": state.status,
        "done": state.status in ("done", "error"),
        "error": state.error,
        "expected_frames": state.expected_frames,
        "delivered_so_far": state.delivered_frames,
        "first_frame_is_keyframe": state.first_frame_is_keyframe,
        "has_video": bool(state.webm_path and os.path.exists(state.webm_path)),
        "meta": state.meta,
    }
    if state.stats is not None:
        s = state.stats
        out["stats"] = asdict(s)
        out["stats"].pop("rtts_ms", None)  # raw list is noisy; expose summary
        if s.rtts_ms:
            out["latency_ms"] = {
                "min": round(min(s.rtts_ms)),
                "p50": round(svt._percentile(s.rtts_ms, 0.5)),
                "p95": round(svt._percentile(s.rtts_ms, 0.95)),
                "max": round(max(s.rtts_ms)),
            }
        out["delivery_pct"] = round(
            100.0 * s.frames_delivered / max(s.frames_total, 1), 1)
    return jsonify(out)


@app.route("/test/stream/<sid>")
def stream(sid):
    state = STREAMS.get(sid)
    if state is None:
        abort(404)
    if not state.webm_path or not os.path.exists(state.webm_path):
        # Not ready yet (or stream collapsed with zero delivered frames).
        abort(404)
    return send_file(state.webm_path, mimetype="video/webm",
                     conditional=True)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>SotF browser playback</title>
<style>
  body { font: 14px/1.5 system-ui, sans-serif; margin: 24px; max-width: 760px; }
  h1 { font-size: 18px; }
  form { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px;
         align-items: center; max-width: 420px; }
  label { justify-self: end; }
  input { width: 120px; }
  button { grid-column: 1 / 3; margin-top: 8px; padding: 8px; }
  video { width: 100%; max-width: 640px; background: #000; margin-top: 16px; }
  pre { background: #111; color: #6f6; padding: 12px; overflow-x: auto;
        white-space: pre-wrap; }
  .note { color: #b00; }
</style></head><body>
<h1>SotF video stream - browser playback</h1>
<p>Streams a canned VP8 clip through the simulated SotF transport, then plays
back exactly the frames that survived. Drops show as freeze / stutter.</p>
<form id="params">
  <label>Bandwidth (kbps)</label><input name="bandwidth_kbps" value="256">
  <label>Latency (ms)</label><input name="latency_ms" value="50">
  <label>Jitter (ms)</label><input name="jitter_ms" value="10">
  <label>Outage prob/sec</label><input name="outage_prob" value="0">
  <label>Outage (ms)</label><input name="outage_ms" value="500">
  <label>Clip duration (s)</label><input name="gen_duration" value="3">
  <label>FPS</label><input name="gen_fps" value="15">
  <button id="go">Start stream</button>
</form>
<div id="msg"></div>
<video id="player" controls></video>
<pre id="stats">idle</pre>
<script>
const $ = (id) => document.getElementById(id);
let lastUrl = null;

$('params').onsubmit = async (e) => {
  e.preventDefault();
  $('go').disabled = true;
  $('msg').textContent = '';
  $('stats').textContent = 'starting...';
  if (lastUrl) { URL.revokeObjectURL(lastUrl); lastUrl = null; }
  $('player').removeAttribute('src'); $('player').load();

  const params = Object.fromEntries(new FormData(e.target));
  let res;
  try {
    res = await fetch('/test/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(params),
    });
  } catch (err) { fail('start failed: ' + err); return; }
  if (res.status === 409) { fail('a stream is already running'); return; }
  if (!res.ok) { fail('start failed: HTTP ' + res.status); return; }
  const { stream_id } = await res.json();
  poll(stream_id);
};

function fail(m) {
  $('msg').innerHTML = '<span class="note">' + m + '</span>';
  $('stats').textContent = 'idle';
  $('go').disabled = false;
}

async function poll(id) {
  while (true) {
    let s;
    try { s = await (await fetch('/test/stats/' + id)).json(); }
    catch (err) { fail('stats poll failed: ' + err); return; }
    $('stats').textContent = JSON.stringify(s, null, 2);
    if (s.done) {
      if (s.status === 'error') { fail('run error: ' + s.error); return; }
      if (s.has_video) {
        await loadVideo(id);
        if (s.first_frame_is_keyframe === false) {
          $('msg').innerHTML = '<span class="note">first delivered frame was'
            + ' not a keyframe - the clip may not start cleanly (heavy loss).'
            + '</span>';
        }
      } else {
        $('msg').innerHTML = '<span class="note">stream collapsed: no frames'
          + ' delivered, nothing to play.</span>';
      }
      $('go').disabled = false;
      return;
    }
    await new Promise(r => setTimeout(r, 400));
  }
}

// Fetch the whole WebM as a blob (pattern (a): buffer-then-play).
async function loadVideo(id) {
  const r = await fetch('/test/stream/' + id);
  if (!r.ok) { fail('stream fetch failed: HTTP ' + r.status); return; }
  const blob = await r.blob();
  lastUrl = URL.createObjectURL(blob);
  $('player').src = lastUrl;
  $('player').load();
}
</script>
</body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    if not svt._have_ffmpeg():
        print("ffmpeg not found in PATH - required for WebM muxing.", file=sys.stderr)
        return 1
    print(f"SotF browser playback on http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
