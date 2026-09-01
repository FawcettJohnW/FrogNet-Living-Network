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
test_stats_overlay_oracle.py - the call stats overlay (toggle + formatting).

Gates the overlay logic in communicator.py (Tkinter rendering can't run in-container, so
this proves the parts that don't need a display): the toggle state machine and the
_stats_text formatter against a realistic live metrics dict.

  1. toggle_stats flips stats_on and is idempotent across presses.
  2. _stats_text reflects the live metrics: sent, wire-drop count + %, rung (+ canonical
     sotf_ladder rung name), queue depth, recv/shown/self.
  3. drop% math: tx_drop / (sent + tx_drop).
  4. rung name comes from the CANONICAL ladder (sotf_ladder.BY_IDX).
"""
from __future__ import annotations
import os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)

FAILS = []
def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  - {detail}" if not ok else ""))
    if not ok: FAILS.append(label)


def run():
    for m in ("tkinter", "tkinter.font", "tkinter.ttk", "tkinter.scrolledtext"):
        mod = types.ModuleType(m)
        if m == "tkinter":
            for n in ("Tk","Toplevel","Frame","Label","Button","Canvas","StringVar","Entry"):
                setattr(mod, n, object)
        sys.modules[m] = mod
    sys.modules["tkinter"].font = sys.modules["tkinter.font"]
    import communicator as C
    App = [v for v in vars(C).values() if isinstance(v, type) and hasattr(v, "_spawn_media_sotf")][0]
    app = App.__new__(App)
    app.stats_on = False
    # no real widgets - toggle should not raise when widgets absent
    app.toggle_stats()
    check("toggle_stats turns the overlay ON", app.stats_on is True)
    app.toggle_stats()
    check("toggle_stats turns the overlay OFF (idempotent flip)", app.stats_on is False)

    # realistic live metrics at a video rung, with timing + wire byte counters
    import time as _t
    app.bitrate = 120
    app.metrics = {"sent": 300, "recv": 999, "disp_peer": 280, "disp_self": 300,
                   "rung": 6, "tx_drop": 12, "depth": 3,
                   "t0": _t.time() - 10.0, "did_bytes": 150000, "would_bytes": 1800000}
    txt = app._stats_text()
    check("stats text shows camera->sent", "camera->sent : 300" in txt, txt)
    check("stats text shows UP fps rate", "fps)" in txt and "30.0 fps" in txt, txt)
    check("stats text shows wire drop count", "wire drop   : 12" in txt, txt)
    check("drop% math correct (tx_drop/(sent+tx_drop))", "(3.8%)" in txt, txt)
    check("send rung shown with canonical ladder name (L6 ENSEMBLE)",
          "L6" in txt and "ENSEMBLE" in txt, txt)
    check("queue depth shown", "queue depth : 3" in txt, txt)
    check("DOWN shows shown-fps + self-fps", "shown" in txt and "self (local)" in txt, txt)
    check("WIRE vs CONVENTIONAL section present (did/would/saved)",
          "did" in txt and "would" in txt and "saved" in txt, txt)
    # 150000 did vs 1800000 would -> saved ~91.7% -> "92%", ratio 12.0x
    check("Did/Would savings computed (~92%, 12x lighter)",
          "92%" in txt and "12.0x" in txt, txt)

    # audio-only rung (L3 VOICE) - name resolves, no crash
    app.metrics["rung"] = 3
    t2 = app._stats_text()
    check("audio rung name resolves (L3 VOICE)", "L3" in t2 and "VOICE" in t2, t2)

    # zero-traffic guard: no division by zero
    import time as _t2
    app.metrics = {"sent": 0, "recv": 0, "disp_peer": 0, "disp_self": 0,
                   "rung": 7, "tx_drop": 0, "depth": 0, "t0": _t2.time(),
                   "did_bytes": 0, "would_bytes": 0}
    t3 = app._stats_text()
    check("zero-traffic shows 0.0% with no error", "(0.0%)" in t3, t3)


def main():
    print("=== call stats overlay: toggle + formatting ===")
    try:
        run()
    except Exception as e:
        import traceback; traceback.print_exc()
        check("oracle ran without exception", False, repr(e))
    print("\n" + ("ALL STATS-OVERLAY CHECKS PASS" if not FAILS
                  else f"STATS-OVERLAY CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
