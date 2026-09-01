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
"""test_temporal_decode_clean_oracle.py - the load-bearing proof that the SERVER can subset
temporal layers per consumer with NO re-encode and NO corruption, using the LIVE encoder
config (3 temporal layers, fixed GOP a multiple of the periodicity, PERIODIC keyframes).

It encodes a multi-GOP clip with the exact ts-parameters sotf_temporal builds, then replicates
the SENDER's index->TID tagging (continuous video index, keyframe forced to base) and the
SERVER's per-consumer subset (keep iff keyframe or tid <= cap), and DECODES each cap subset.
Every subset must decode with zero decoder errors across all the keyframes - that is what
proves the sender's index pattern stays aligned with libvpx's own layer assignment even with
periodic keyframes (not just the rare-keyframe case).

Gated: skips cleanly if ffmpeg is absent.
"""
import os, sys, struct, subprocess, shutil, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)
import sotf_temporal as T

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")


def _read_ivf(path):
    b = open(path, "rb").read()
    assert b[:4] == b"DKIF"
    hdr, off, frames = b[:32], 32, []
    while off + 12 <= len(b):
        sz = struct.unpack("<I", b[off:off + 4])[0]; off += 12
        frames.append(b[off:off + sz]); off += sz
    return hdr, frames


def _write_ivf(path, hdr, frames):
    out = bytearray(hdr)
    for i, d in enumerate(frames):
        out += struct.pack("<IQ", len(d), i) + d
    open(path, "wb").write(out)


def _is_key(vp8):                          # VP8 keyframe: bit0 of first byte == 0
    return len(vp8) > 0 and (vp8[0] & 0x01) == 0


def _decode_clean(ffmpeg, path):
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path, "-f", "null", "-"],
                       stderr=subprocess.PIPE)
    return r.returncode == 0 and not r.stderr.decode("latin1").strip(), r.stderr.decode("latin1")[:200]


def main():
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("  [SKIP] ffmpeg not present - temporal decode-clean proof needs the real codec")
        print("\n0 passed, 0 failed"); return 0

    d = tempfile.mkdtemp(prefix="tltest_")
    full = os.path.join(d, "full.ivf")
    # LIVE config: short FIXED GOP (multiple of periodicity), periodic keyframes, multi-GOP clip.
    gop = T.PERIODICITY * 2                 # 8: frequent keyframes => many alignment checkpoints
    enc = [ffmpeg, "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=5",
           "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "400k",
           "-g", str(gop), "-keyint_min", str(gop),
           "-ts-parameters", T.ts_parameters(400),
           "-f", "ivf", full]
    r = subprocess.run(enc, stderr=subprocess.PIPE)
    if r.returncode != 0:
        ck("encode 3-layer multi-GOP clip", False, r.stderr.decode("latin1")[:200])
        print(f"\n{_p} passed, {_f} failed"); return 1
    ck("encode 3-layer multi-GOP clip", True)

    hdr, frames = _read_ivf(full)
    nkey = sum(1 for v in frames if _is_key(v))
    ck("clip has multiple keyframes (periodic)", nkey >= 4, f"{nkey} keyframes")

    # SENDER tagging: continuous video index, keyframe forced to base.
    tagged, vidx = [], 0
    for v in frames:
        key = _is_key(v)
        tid = 0 if key else T.tid_for_video_index(vidx)
        vidx += 1
        tagged.append((key, tid, v))

    # SERVER subset per cap, then DECODE each subset and require it clean.
    counts = {}
    for cap in (T.CAP_BASE, T.CAP_HALF, T.CAP_FULL):
        sub = [v for (key, tid, v) in tagged if key or T.forward_video(tid, cap)]
        counts[cap] = len(sub)
        # every subset must still begin on a keyframe and contain all keyframes
        ck(f"cap {cap} keeps all keyframes", sum(1 for v in sub if _is_key(v)) == nkey)
        p = os.path.join(d, f"cap{cap}.ivf"); _write_ivf(p, hdr, sub)
        clean, tail = _decode_clean(ffmpeg, p)
        ck(f"cap {cap} subset decodes CLEAN ({len(sub)} frames)", clean, tail)

    ck("counts monotonic in cap", counts[0] <= counts[1] <= counts[2], str(counts))
    ck("cap FULL == all frames", counts[T.CAP_FULL] == len(frames), f"{counts[T.CAP_FULL]} vs {len(frames)}")
    ck("cap BASE is roughly a quarter", counts[0] <= len(frames) * 0.6, f"{counts[0]}/{len(frames)}")

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
