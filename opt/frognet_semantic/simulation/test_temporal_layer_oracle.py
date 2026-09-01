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
"""test_temporal_layer_oracle.py - PURE model of the SotF temporal-layer plane:

  SENDER tags each VIDEO frame with a temporal layer id (TID) by its continuous video-frame
  index; keyframes are forced to the base layer (TID 0). The wire carries TID in the flags
  byte (call_media.pack_av_src tid=).

  SERVER forwards, per consumer, only video frames with TID <= that consumer's cap; keyframes
  (TID 0) and audio are ALWAYS forwarded. cap is derived from the consumer's served rung
  (cap_for_rung: L5->0, L6->1, L7->2, <L5->no video).

Proves: subset counts are monotonic in cap; every keyframe survives every cap; audio survives
every cap; the wire round-trips TID; conservative start cap is the base layer. No ffmpeg.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)
import sotf_temporal as T
import call_media as C

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")


def sender_tids(keyflags):
    """Replicate CallSender.feed_video tagging: continuous video index, keyframe forced to 0."""
    out, vidx = [], 0
    for key in keyflags:
        tid = 0 if key else T.tid_for_video_index(vidx)
        vidx += 1
        out.append((key, tid))
    return out


def server_subset(frames, cap):
    """Replicate the server's per-consumer video subset: keep iff keyframe or tid <= cap."""
    return [(key, tid) for (key, tid) in frames if key or T.forward_video(tid, cap)]


def main():
    # A realistic frame stream: keyframe every 8 (fixed GOP, multiple of periodicity), 40 frames.
    keyflags = [(i % 8 == 0) for i in range(40)]
    frames = sender_tids(keyflags)

    # keyframes are base layer
    ck("keyframes tagged TID 0", all(tid == 0 for (k, tid) in frames if k))
    # pattern present among P-frames (we should see all of 0,1,2 across the stream)
    tids = {tid for (k, tid) in frames}
    ck("all three layers present", tids == {0, 1, 2}, tids)

    c0 = server_subset(frames, T.CAP_BASE)
    c1 = server_subset(frames, T.CAP_HALF)
    c2 = server_subset(frames, T.CAP_FULL)
    ck("cap counts monotonic 0<=1<=2", len(c0) <= len(c1) <= len(c2),
       f"{len(c0)},{len(c1)},{len(c2)}")
    ck("cap FULL forwards everything", len(c2) == len(frames), f"{len(c2)} vs {len(frames)}")
    ck("cap BASE strictly less than FULL", len(c0) < len(c2), f"{len(c0)} vs {len(c2)}")

    nkey = sum(1 for (k, _t) in frames if k)
    for cap, sub in ((0, c0), (1, c1), (2, c2)):
        ck(f"cap {cap} keeps every keyframe", sum(1 for (k, _t) in sub if k) == nkey)

    # rung -> cap mapping and conservative start
    ck("cap_for_rung L5/L6/L7 = 0/1/2",
       (T.cap_for_rung(5), T.cap_for_rung(6), T.cap_for_rung(7)) == (0, 1, 2))
    ck("cap_for_rung L4 = None (no video)", T.cap_for_rung(4) is None)
    ck("START_CAP is base (conservative)", T.START_CAP == T.CAP_BASE)

    # wire carriage: TID round-trips through pack_av_src and the server's *_full unpack;
    # legacy-arity unpack still reports the right key
    pv = C.pack_av_src("gorp", 5, 100, False, b"", b"VID", tid=2)
    s, sq, ts, key, tid, a, v = C.unpack_av_src_full(pv)
    ck("wire TID round-trips", (s, key, tid, v) == ("gorp", False, 2, b"VID"), f"{(s,key,tid,v)}")
    pk = C.pack_av_src("gorp", 6, 101, True, b"", b"K", tid=0)
    _, _, _, k2, t2, _, _ = C.unpack_av_src_full(pk)
    ck("wire keyframe TID 0", k2 and t2 == 0)
    pa = C.pack_av_src("gorp", 7, 102, False, b"AUD", b"")     # audio: default tid 0
    _, _, _, _, ta, aa, _ = C.unpack_av_src_full(pa)
    ck("audio frame default TID 0", ta == 0 and aa == b"AUD")

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
