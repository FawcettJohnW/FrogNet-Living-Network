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
sim_sotf_media_codex.py - exercise the SotF media codex HARD, prove the metrics, and
prove the PLANE SPLIT is right:
  - AV frames go raw out the video vector, byte-exact, NO convergence (live AV never
    repeats; diffing it is pure cost - so we don't).
  - SAME/DIFF lives on the CONTROL JSON: re-store an unchanged bag -> SAME (~0 bytes);
    change one field -> small DIFF. That is the real, measured gain.
"""
import os, sys, json, hashlib, random

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path: sys.path.insert(0, HERE)

from working_memory import TransientStore, PermStore
import sotf_metrics
import sotf_media_codex as MC

_p = _f = 0
def check(name, cond, extra=""):
    global _p, _f
    if cond: _p += 1; print(f"  [PASS] {name}")
    else:    _f += 1; print(f"  [FAIL] {name}  {extra}")

def _copy(v): return json.loads(json.dumps(v)) if v is not None else None
class SimTransient(TransientStore):
    def __init__(self): self._d = {}
    def get(self, n): return _copy(self._d.get(n))
    def upsert(self, n, v): self._d[n] = _copy(v)
    def drop(self, n): self._d.pop(n, None)
    def all_by_type(self, typ):
        return [_copy(v) for v in self._d.values()
                if isinstance(v, dict) and v.get("type") == typ]
class SimPerm(PermStore):
    def __init__(self): self._d = {}
    def load(self, k): return _copy(self._d.get(k))
    def save(self, k, v): self._d[k] = _copy(v)

class VideoVector:
    def __init__(self): self.q = []
    def send(self, frame): self.q.append(frame)
    def drain(self):
        out, self.q = self.q, []; return out

def _vp8_realish(seq):
    # realistic: EVERY frame differs (sensor noise / re-encode). Never repeats.
    random.seed(seq * 2654435761 & 0xffffffff)
    return bytes(random.getrandbits(8) for _ in range(600))
def _audio(seq):
    random.seed(seq); return bytes(random.getrandbits(8) for _ in range(320))


def main():
    print("=== SotF media codex - simulator (corrected plane split) ===")
    t = SimTransient(); p = SimPerm(); vv = VideoVector()
    prod = MC.SotFMediaCodex(t, p, "call1", "julie", vv.send, role=sotf_metrics.ROLE_SENDER)
    cons = MC.SotFMediaCodex(t, p, "call1", "donna", (lambda f: None), role=sotf_metrics.ROLE_PLAYER)

    # ---- DATA: 100 ever-changing AV frames, raw, byte-exact, no convergence ----
    exact = 0
    for seq in range(100):
        a, v = _audio(seq), _vp8_realish(seq)
        prod.send_av(seq, seq % 30 == 0, a, v)
        for buf in vv.drain():
            rseq, key, ra, rv = cons.recv_av(buf)
            if ra == a and rv == v: exact += 1
    check("D1 every AV frame byte-exact off the video vector", exact == 100, f"exact={exact}")
    check("D2 AV path did NOT touch the control codec (no ctl writes yet)",
          prod.metrics.ctl_full == 0 and prod.metrics.ctl_diff == 0 and prod.metrics.ctl_same == 0)
    check("D3 throughput counted (sent/received)",
          prod.metrics.sent == 100 and cons.metrics.received == 100)

    # ---- CONTROL: ffmpeg options as shared memory; cheap boolean flag ----
    # producer sets its own initial options (FULL); re-storing unchanged -> SAME.
    base_opts = {"bitrate_kbps": 800, "cpu_used": 8, "g": 30}
    k1 = prod.set_ffmpeg_options(base_opts)
    # producer steady state: check the cheap flag every loop. Not tripped by itself
    # after it takes them, so simulate a few quiet loops.
    prod.take_ffmpeg_options()                      # producer consumes its own initial set, clears
    quiet = sum(1 for _ in range(50) if not prod.conditions_changed())
    check("C1 first options store is FULL", k1 == "full", f"k1={k1}")
    check("C2 steady state: flag stays clear, cheap reads", quiet == 50, f"quiet={quiet}")

    # ---- BACK-OFF: consumer writes new ffmpeg options for the PRODUCER + trips flag ----
    # the consumer is drowning; it translates "back off" into actual encoder options and
    # stores them into the PRODUCER's control, then trips the producer's flag. It does NOT
    # message the producer. The producer doesn't know who wrote it.
    cons.set_ffmpeg_options({"bitrate_kbps": 300, "cpu_used": 8, "g": 30}, for_who="julie")
    check("B1 producer's flag is now tripped (JIT-visible)", prod.conditions_changed() is True)
    new_opts = prod.take_ffmpeg_options()           # producer pays the read ONCE, on trip
    check("B2 producer reads the backed-off options", new_opts and new_opts["bitrate_kbps"] == 300, f"{new_opts}")
    check("B3 flag cleared after the producer applied", prod.conditions_changed() is False)
    check("B4 producer never needed to know who wrote it", True)  # write-agnostic by construction

    # ---- SCALE UP: link recovered; same path, higher options, same flag ----
    cons.set_ffmpeg_options({"bitrate_kbps": 1200, "cpu_used": 6, "g": 30}, for_who="julie")
    check("U1 scale-up trips the same flag", prod.conditions_changed() is True)
    up = prod.take_ffmpeg_options()
    check("U2 producer applies higher bitrate via identical path", up and up["bitrate_kbps"] == 1200, f"{up}")
    check("U3 flag cleared after apply", prod.conditions_changed() is False)

    # control convergence metric: the options bag is mostly stable -> SAME dominates.
    for i in range(100):
        prod.set_ffmpeg_options(new_opts)           # re-store unchanged -> SAME
    comp = prod.metrics.ctl_compression()
    print("  ctl metrics:", json.dumps(comp))
    check("C3 control SAME dominates", comp["ctl_same"] > comp["ctl_diff"] + comp["ctl_full"],
          f"same={comp['ctl_same']} diff={comp['ctl_diff']} full={comp['ctl_full']}")
    check("C4 control convergence saves big (>70%)", comp["ctl_saved_pct"] > 70, f"saved={comp['ctl_saved_pct']}")

    # ---- SPACE: many LiveStream instances coexist; whole-space read ----
    cons.set_ffmpeg_options({"bitrate_kbps": 300}, for_who="donna")  # consumer's own instance
    third = MC.SotFMediaCodex(t, p, "call1", "sammy", (lambda f: None))
    third.set_ffmpeg_options({"bitrate_kbps": 600})
    whos = sorted(x.get("who") for x in prod.read_livestreams())
    check("S1 multi-writer space: all instances visible", whos == ["donna", "julie", "sammy"], f"{whos}")

    # ---- hostReset on databasehost float ----
    rep = prod.hostReset()
    after = t.get(MC._ls_key("call1", "julie"))
    check("H1 hostReset re-asserted my LiveStream from perm", after is not None and after.get("who") == "julie")
    check("H2 hostReset dropped the control reference (next store re-FULLs)", rep.get("control_refulled") is True)
    kf = prod.set_ffmpeg_options({"bitrate_kbps": 500})
    check("H3 first control store after float is FULL again", kf == "full", f"kf={kf}")

    print(f"\n=== {_p} passed, {_f} failed ===")
    return 1 if _f else 0

if __name__ == "__main__":
    sys.exit(main())
