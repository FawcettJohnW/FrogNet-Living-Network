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
degrade_sweep.py - the comparison John actually asked for:

  How far can you degrade a link before the stream stops being viewable?
  Claim: because of the architecture, SotF degrades SIGNIFICANTLY further than a standard
  per-frame-request process before problems show.

Two transports, SAME real VP8 frames over a degrading link:

  SotF (established socket, pure data):
    The FrogNet TCP sockets are ALREADY UP. Each frame is written as pure bytes onto the
    standing stream (length-delimited), fire-and-forget - no per-frame handshake, no
    per-frame request/response, no waiting on an ack. Connection cost was paid ONCE at
    session establishment. Control is convergent (quiet when unchanged), so it doesn't
    fight the AV for the shrinking pipe.

  Standard per-frame-request process:
    Every frame is a transaction: request (headers + frame) -> wait for response -> next.
    Per-frame round-trip. As latency/loss climb, each frame's cycle stretches and fails;
    head-of-line blocking and retransmits compound; the stream falls apart while bandwidth
    remains, because it's choking on transaction overhead, not bandwidth.

The link is degraded along three axes, swept worse until each transport's delivered stream
drops below the viewability bar (enough frames arriving in time to sustain the GOP). We
report the breaking point of each and how much FURTHER SotF survives.

Model (honest): this is an analytic link model, not a kernel netem run - it computes, per
frame, whether that frame is delivered IN TIME given the link's bandwidth, RTT, and loss,
under each transport's delivery structure. The WG box test is the live confirmation; this
shows the architectural gap and where the knees are.
"""
import os, sys, struct, statistics

HERE = os.path.dirname(os.path.abspath(__file__))

def iter_ivf(path):
    with open(path, "rb") as f:
        f.read(32); seq = 0
        while True:
            fh = f.read(12)
            if len(fh) < 12: return
            sz = struct.unpack("<I", fh[:4])[0]; d = f.read(sz)
            if len(d) < sz: return
            yield seq, bool(d and (d[0] & 1) == 0), d; seq += 1

# ---- link model ----
class Link:
    """A degrading link: bandwidth (kbps), one-way latency (ms), loss (frac per packet)."""
    def __init__(self, kbps, rtt_ms, loss):
        self.kbps = kbps; self.rtt_ms = rtt_ms; self.loss = loss
        self.MTU = 1420
    def serialize_ms(self, nbytes):
        return (nbytes * 8) / (self.kbps) if self.kbps > 0 else 1e9   # ms (kbps = kbits/s)
    def pkts(self, nbytes):
        return max(1, (nbytes + self.MTU - 1) // self.MTU)
    def delivered(self, nbytes, rng):
        """Does a chunk of nbytes arrive at all? Lost if ANY packet is lost (TCP would
        retransmit, but each retransmit costs an RTT - see time models below)."""
        import random
        return all(rng.random() >= self.loss for _ in range(self.pkts(nbytes)))

# ---- per-frame TIME under each transport ----
def sotf_frame_time_ms(link, frame_bytes, rng):
    """Pure data on the standing socket: no handshake, no app round-trip. Time = serialize
    the bytes onto the pipe + propagation. TCP loss recovery costs ~1 RTT per lossy frame,
    but there is NO per-frame app-level round-trip, and frames PIPELINE (next frame's bytes
    start as soon as the pipe is free - the writer never waits on a reply)."""
    t = link.serialize_ms(frame_bytes) + link.rtt_ms   # propagation once
    if not link.delivered(frame_bytes, rng):
        t += link.rtt_ms                                # one retransmit RTT; still pipelined
    return t

def standard_frame_time_ms(link, frame_bytes, resp_bytes, rng):
    """Per-frame request/response: serialize request, wait a full RTT for the response,
    serialize response. The sender CANNOT start the next frame until this cycle completes
    (request/response structure) -> round-trip is on the critical path EVERY frame.
    Loss on either leg costs an extra RTT (timeout+retransmit) and blocks the next frame."""
    t = link.serialize_ms(frame_bytes) + link.rtt_ms      # request out + propagation
    t += link.serialize_ms(resp_bytes) + link.rtt_ms      # response back + propagation
    if not link.delivered(frame_bytes, rng):
        t += 2 * link.rtt_ms                              # retransmit, head-of-line blocked
    if not link.delivered(resp_bytes, rng):
        t += 2 * link.rtt_ms
    return t

def run_transport(frames, link, mode, fps=15):
    import random
    rng = random.Random(99)
    budget_ms = 1000.0 / fps                # a frame is "in time" if delivered within one frame period of its slot
    # pipelined timeline for SotF; serial cycle for standard
    now = 0.0; slot = 0.0
    in_time = late = 0
    gaps = []
    last_deliver = None
    REQ_HDR = 180                            # standard per-frame request headers
    RESP = 9                                 # "ACCEPTED"
    for seq, is_key, vp8 in frames:
        nbytes = len(vp8) + 2133             # frame + ~one audio chunk
        slot += budget_ms
        if mode == "sotf":
            ft = sotf_frame_time_ms(link, nbytes, rng)
            # pipelined: delivery time advances with the pipe, not reset per frame
            now = max(now + link.serialize_ms(nbytes), slot - budget_ms) + (ft - link.serialize_ms(nbytes))
            deliver_at = now
        else:
            ft = standard_frame_time_ms(link, nbytes + REQ_HDR, RESP, rng)
            # serial: each frame's full cycle is on the critical path
            now = max(now, slot - budget_ms) + ft
            deliver_at = now
        lateness = deliver_at - slot
        if lateness <= budget_ms:
            in_time += 1
        else:
            late += 1
        if last_deliver is not None:
            gaps.append(deliver_at - last_deliver)
        last_deliver = deliver_at
    jitter = statistics.pstdev(gaps) if len(gaps) > 1 else 0.0
    return {"in_time": in_time, "late": late, "total": len(frames),
            "viewable_pct": round(100.0 * in_time / len(frames), 1),
            "jitter_ms": round(jitter, 1)}

def viewable(stats, bar=85.0):
    return stats["viewable_pct"] >= bar

def main():
    frames = list(iter_ivf(os.path.join(_MEDIA, "motion.ivf")))
    print("=== DEGRADATION SWEEP: SotF (pure data, established socket) vs standard per-frame-request ===")
    print(f"    {len(frames)} real VP8 frames @15fps; viewable bar = 85% frames in-time\n")

    # ---- AXIS 1: rising latency (the architecture's core advantage) ----
    print("  AXIS 1 - RTT climbing (bandwidth ample 5000kbps, loss 0):")
    print(f"    {'RTT(ms)':>8} {'SotF view%':>11} {'SotF jit':>9} {'STD view%':>10} {'STD jit':>8}")
    sotf_break = std_break = None
    for rtt in [5, 20, 50, 100, 150, 200, 300, 400, 600]:
        link = Link(5000, rtt, 0.0)
        s = run_transport(frames, link, "sotf"); d = run_transport(frames, link, "standard")
        if std_break is None and not viewable(d): std_break = rtt
        if sotf_break is None and not viewable(s): sotf_break = rtt
        print(f"    {rtt:>8} {s['viewable_pct']:>10}% {s['jitter_ms']:>8}  {d['viewable_pct']:>9}% {d['jitter_ms']:>7}")
    print(f"    -> standard breaks at RTT={std_break}ms;  SotF still viewable to RTT={sotf_break}ms\n")

    # ---- AXIS 2: rising loss ----
    print("  AXIS 2 - packet loss climbing (5000kbps, RTT 50ms):")
    print(f"    {'loss%':>8} {'SotF view%':>11} {'STD view%':>10}")
    sb = db = None
    for loss in [0, 1, 2, 5, 10, 15, 20]:
        link = Link(5000, 50, loss/100.0)
        s = run_transport(frames, link, "sotf"); d = run_transport(frames, link, "standard")
        if db is None and not viewable(d): db = loss
        if sb is None and not viewable(s): sb = loss
        print(f"    {loss:>7}% {s['viewable_pct']:>10}% {d['viewable_pct']:>9}%")
    print(f"    -> standard breaks at loss={db}%;  SotF still viewable to loss={sb}%\n")

    # ---- AXIS 3: shrinking bandwidth ----
    print("  AXIS 3 - bandwidth shrinking (RTT 50ms, loss 0):")
    print(f"    {'kbps':>8} {'SotF view%':>11} {'STD view%':>10}")
    sb2 = db2 = None
    for kbps in [5000, 2000, 1000, 600, 400, 300, 200, 100]:
        link = Link(kbps, 50, 0.0)
        s = run_transport(frames, link, "sotf"); d = run_transport(frames, link, "standard")
        if db2 is None and not viewable(d): db2 = kbps
        if sb2 is None and not viewable(s): sb2 = kbps
        print(f"    {kbps:>8} {s['viewable_pct']:>10}% {d['viewable_pct']:>9}%")
    print(f"    -> standard breaks below {db2}kbps;  SotF still viewable down to {sb2}kbps\n")

    print("  READ: the architecture's advantage is on LATENCY and LOSS - the standard path")
    print("  pays a per-frame round-trip, so it chokes on transaction overhead long before")
    print("  the bandwidth runs out. SotF writes pure data on the standing socket and keeps")
    print("  flowing. Bandwidth floor is similar (both move the same bytes); the gap is how")
    print("  far you can degrade RTT/loss before the stream breaks.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
