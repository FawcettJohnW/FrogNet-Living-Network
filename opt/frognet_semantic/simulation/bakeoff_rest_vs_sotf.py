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
import os, sys
# installed layout: communicator bundle modules + media assets next to this sim
_HERE = os.path.dirname(os.path.abspath(__file__))
for _c in ["/etc/frognet_bundles/communicator", os.path.join(_HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator")]:
    if os.path.isdir(_c):
        sys.path.insert(0, os.path.abspath(_c)); break
_MEDIA = os.path.join(_HERE, "media_assets")
_RUNGDIR = os.path.join(_MEDIA, "rungs")
"""
bakeoff_rest_vs_sotf.py - the data-plane win as a NUMBER, not an argument.

Pushes the SAME real VP8 movie frames (+ real PCM audio) two ways and counts wire bytes
and round-trips, using FrogNet's OWN wire-byte estimator (copied verbatim from
proxy/transport_semantic.py) so the REST baseline is FrogNet's accounting, not a strawman:

  REST path (what moving AV over HTTP costs):
    - one HTTP request PER FRAME: method+path+headers (Host, Content-Type,
      Content-Length, ...) + the frame as body
    - a response per frame (ack the sender doesn't need)
    - TCP handshake + teardown + WG encap + per-packet ACKs, per FrogNet's model
    - this is the per-frame transport TAX REST imposes and SotF deletes

  SotF path (the raw media vector):
    - one length-prefixed frame on a persistent socket the handler owns
    - fire-and-forget: NO response, NO per-frame handshake/teardown, NO HTTP envelope
    - WG encap still applies to the bytes (same physical wire), counted the same way

Also reports the ROUND-TRIP count (REST = 1 per frame; SotF = 0), because on a
latency-bound bearer that, not bytes, is what caps frame rate.

Run: python3 bakeoff_rest_vs_sotf.py
"""
import os, sys, struct, json

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- FrogNet's OWN estimators (verbatim from proxy/transport_semantic.py) ----
def est_http_request_wire(method, path, headers, body):
    WG_PER_PKT = 60; INNER_MTU = 1420; TCP_HANDSHAKE_WG = 360; TCP_ACK_WG = 120
    n = len(method) + 1 + len(path) + len(" HTTP/1.1\r\n")
    for k, v in (headers or {}).items():
        n += len(str(k)) + 2 + len(str(v)) + 2
    n += 2
    n += len(body) if body else 0
    pkts = max(1, (n + INNER_MTU - 1) // INNER_MTU)
    return TCP_HANDSHAKE_WG + n + (pkts * WG_PER_PKT) + (pkts * TCP_ACK_WG)

def est_http_response_wire(body_size, content_type="text/plain"):
    WG_PER_PKT = 60; INNER_MTU = 1420; TCP_TEARDOWN_WG = 480; TCP_ACK_WG = 120
    RESP_HEADERS = 194
    total = RESP_HEADERS + body_size
    pkts = max(1, (total + INNER_MTU - 1) // INNER_MTU)
    return TCP_TEARDOWN_WG + total + (pkts * WG_PER_PKT) + (pkts * TCP_ACK_WG)

# SotF raw vector: persistent socket, length-prefixed frame, fire-and-forget. The bytes
# still cross WireGuard, so charge WG encap per packet the SAME way - but NO handshake,
# NO teardown, NO response, NO HTTP envelope. (A 4-byte length prefix instead of headers.)
def est_sotf_frame_wire(frame_len):
    WG_PER_PKT = 60; INNER_MTU = 1420
    n = 4 + frame_len                              # length prefix + raw frame
    pkts = max(1, (n + INNER_MTU - 1) // INNER_MTU)
    # persistent stream: receiver still ACKs data at TCP level; charge the same per-pkt ACK
    # the REST model charges for data packets (fairness), but nothing else.
    TCP_ACK_WG = 120
    return n + (pkts * WG_PER_PKT) + (pkts * TCP_ACK_WG)


def iter_ivf(path):
    with open(path, "rb") as f:
        if f.read(32)[:4] != b"DKIF": raise ValueError("not IVF")
        seq = 0
        while True:
            fh = f.read(12)
            if len(fh) < 12: return
            sz = struct.unpack("<I", fh[:4])[0]; d = f.read(sz)
            if len(d) < sz: return
            yield seq, bool(d and (d[0] & 1) == 0), d; seq += 1


def bakeoff(name, path, pcm, fps=15):
    apf = int(16000 * 2 / fps)
    rest_wire = sotf_wire = 0
    rest_rt = sotf_rt = 0
    frames = payload = 0
    for seq, is_key, vp8 in iter_ivf(path):
        a = pcm[(seq * apf) % max(len(pcm), 1):][:apf] if pcm else b"\x00" * apf
        if len(a) < apf: a += b"\x00" * (apf - len(a))
        # one AV unit = audio + video, the same bytes both paths must move
        body = a + vp8
        payload += len(body); frames += 1

        # REST: HTTP POST per frame with realistic FrogNet-ish headers + a response
        headers = {
            "Host": "mediahost.frognet",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(body)),
            "X-FrogNet-Async": "1",
            "Connection": "keep-alive",
        }
        rest_wire += est_http_request_wire("POST", "/sotf/av-call", headers, body)
        rest_wire += est_http_response_wire(9)     # "ACCEPTED\n"
        rest_rt += 1                                # one request/response round-trip

        # SotF: one length-prefixed frame on the persistent vector, no reply
        sotf_wire += est_sotf_frame_wire(len(body))
        sotf_rt += 0

    return {
        "movie": name, "frames": frames, "payload_bytes": payload,
        "rest_wire": rest_wire, "sotf_wire": sotf_wire,
        "rest_overhead": rest_wire - payload, "sotf_overhead": sotf_wire - payload,
        "rest_roundtrips": rest_rt, "sotf_roundtrips": sotf_rt,
        "ratio": rest_wire / sotf_wire if sotf_wire else 0,
        "saved_pct": round((1 - sotf_wire / rest_wire) * 100, 1) if rest_wire else 0,
    }


def main():
    pcm_path = os.path.join(HERE, "movies", "audio.pcm")
    pcm = open(pcm_path, "rb").read() if os.path.exists(pcm_path) else b""
    movies = [("motion", "movies/motion.ivf"), ("zoom", "movies/zoom.ivf"),
              ("static", "movies/static.ivf")]
    print("=== DATA-PLANE BAKE-OFF: REST/HTTP-per-frame vs SotF raw vector ===")
    print("    (wire bytes by FrogNet's own estimator; real VP8 frames + real PCM)\n")
    tot_rest = tot_sotf = tot_frames = 0
    for nm, rel in movies:
        path = os.path.join(HERE, rel)
        if not os.path.exists(path):
            print(f"  {nm}: MISSING"); continue
        r = bakeoff(nm, path, pcm)
        tot_rest += r["rest_wire"]; tot_sotf += r["sotf_wire"]; tot_frames += r["frames"]
        print(f"  {nm:7s} {r['frames']:3d} frames, payload {r['payload_bytes']//1024:4d}KB")
        print(f"          REST wire {r['rest_wire']//1024:5d}KB  (overhead {r['rest_overhead']//1024:4d}KB, {r['rest_roundtrips']:3d} round-trips)")
        print(f"          SotF wire {r['sotf_wire']//1024:5d}KB  (overhead {r['sotf_overhead']//1024:4d}KB, {r['sotf_roundtrips']:3d} round-trips)")
        print(f"          -> SotF moves the SAME frames in {r['saved_pct']}% fewer wire bytes "
              f"({r['ratio']:.2f}x), and pays 0 round-trips vs {r['rest_roundtrips']}\n")
    if tot_rest:
        print(f"  TOTAL  {tot_frames} frames:  REST {tot_rest//1024}KB  vs  SotF {tot_sotf//1024}KB  "
              f"=> {round((1-tot_sotf/tot_rest)*100,1)}% fewer wire bytes, {tot_rest//tot_sotf}x lighter")
        print(f"  ROUND-TRIPS:  REST {tot_frames}  vs  SotF 0  "
              f"(on a latency-bound bearer this, not bytes, caps frame rate)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
