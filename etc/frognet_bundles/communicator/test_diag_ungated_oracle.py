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
test_diag_ungated_oracle.py -- the diagnostics record what happened, ungated.

At L4 and below video is shed BY DESIGN. The diagnostics carried video counters only
(v_kbps, v_drop_fps), so every series the screen knew how to draw went to zero and
stayed there while the call ran perfectly on audio. The screen read as "stopped".

Two separate defects, both gates:

  MISSING WAS RENDERED AS ZERO. `kbps = [r.get("kbps") or 0.0 ...]` turned an absent
  counter into a reading of nought, so "no sample" and "an idle wire" drew the same
  line. plot_points already breaks a trace on None precisely so those stay
  distinguishable -- the `or 0.0` threw that away before it ever got there.

  ZERO WAS NOT DRAWN AT ALL. `if d <= 0: continue` skipped every clean sample, so a
  clean second and a second with no data looked identical. Below L5, where video
  drops are structurally zero, the panel drew nothing whatsoever.

And audio was never recorded. WireStats has computed a_kbps all along and nothing
read it; SotFDataPlane.audio_sheds counts the frames the wire refused and nothing
read that either -- while below L5 audio sheds are the ONLY congestion signal there
is.

WHAT THIS PROVES

  1. AUDIO IS RECORDED        a_kbps and a_sheds are LinkHistory fields.
  2. ZERO IS A READING        a measured 0.0 is stored as 0.0, not dropped.
  3. MISSING IS NOT ZERO      an absent counter is stored as None and stays None.
  4. PLOT KEEPS THEM APART    plot_points draws a zero on the axis and BREAKS the
                              trace on None -- they must not produce the same
                              geometry.
  5. L4 IS NOT A FLATLINE     with video shed and audio flowing, the recorded row
                              still carries a non-zero audio reading.
  6. SHEDS READ NON-DESTRUCTIVELY  the diagnostics must never call take_audio_sheds():
                              it clears the pending count the BEARER steers on.
  10. THE RX REPORT SAYS WHERE  the receive loop names its PLANE, what it had
                              received, when the last frame arrived, and whether it
                              was already stopping -- and a close mid-frame reads
                              differently from a clean hang-up. Two identical
                              one-line reports cost days.

  8. RECEIVE IS MEASURED       the legend says how fast things are ARRIVING, not
                              only how fast they are being sent. Send-side bytes
                              were counted; the receive side was counted only in
                              FRAMES, so no screen could answer "is the far end
                              getting through".
  9. BACKLOG COMES FROM MEMORY the relay publishes MediaHold per viewer -- awaiting
                              keyframe, bytes held, inters shed, cap, and a NAMED
                              reason -- and the Communicator reads it. No relay
                              publishing is reported as UNKNOWN, never as "no
                              backlog".

  7. THE CONSOLE LEGEND TOO    the fps/KB-s/drops line on the call window is a
                              readout under the same rule: audio is on it, a
                              measured zero prints as 0, and an absent counter
                              prints as MISSING rather than as zero.

Run:  python3 test_diag_ungated_oracle.py
Exit: 0 all checks pass, 1 any check fails.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# comms_control imports frognet_tuples, which is a shim onto core.frognet_tuples --
# the semantic tree must be importable exactly as it is on a node.
for _p in ("/opt/frognet_semantic",
           os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "..", "..", "opt", "frognet_semantic")):
    if os.path.isdir(_p) and os.path.abspath(_p) not in sys.path:
        sys.path.insert(0, os.path.abspath(_p))

import comms_ui as UI                               # noqa: E402

FAILURES = []


def check(name, ok, detail=""):
    print("  %-5s %s%s" % ("PASS" if ok else "FAIL", name,
                           ("\n        " + detail) if (detail and not ok) else ""))
    if not ok:
        FAILURES.append(name)


def main():
    print(__doc__.strip().splitlines()[0])
    print()

    # 1 -- the fields exist at all
    print("check 1 -- audio is recorded")
    check("a_kbps_field", "a_kbps" in UI.LinkHistory.FIELDS,
          "LinkHistory.FIELDS = %r" % (UI.LinkHistory.FIELDS,))
    check("a_sheds_field", "a_sheds" in UI.LinkHistory.FIELDS,
          "LinkHistory.FIELDS = %r" % (UI.LinkHistory.FIELDS,))
    print()

    # 2 + 3 -- zero survives, missing survives, and they differ
    print("checks 2,3 -- a zero is a reading; a missing counter is not a zero")
    h = UI.LinkHistory()
    h.add(100.0, rung=4, kbps=0.0, drops=0.0, a_kbps=12.5, a_sheds=0)
    h.add(100.1, rung=4, kbps=None, drops=None, a_kbps=None, a_sheds=None)
    ts, rows = h.window(60.0)
    check("zero_stored_as_zero",
          rows[0]["kbps"] == 0.0 and rows[0]["drops"] == 0.0,
          "a measured zero did not survive: %r" % (rows[0],))
    check("missing_stored_as_none",
          rows[1]["kbps"] is None and rows[1].get("a_kbps", None) is None,
          "an absent counter was coerced: %r" % (rows[1],))
    check("zero_is_not_missing",
          rows[0]["kbps"] is not None and rows[1]["kbps"] is None,
          "zero and missing are indistinguishable in the history")
    print()

    # 4 -- and the plot keeps them apart
    print("check 4 -- plot_points draws a zero and breaks on missing")
    segs_zero = UI.plot_points([1.0, 2.0, 3.0], [0.0, 0.0, 0.0],
                               0, 0, 100, 100, 0, 10, 1.0, 3.0)
    segs_gap = UI.plot_points([1.0, 2.0, 3.0], [0.0, None, 0.0],
                              0, 0, 100, 100, 0, 10, 1.0, 3.0)
    check("zero_is_drawn",
          len(segs_zero) == 1 and len(segs_zero[0]) == 3,
          "a run of zeros did not draw as one continuous trace: %r" % (segs_zero,))
    check("missing_breaks_trace",
          len(segs_gap) == 2,
          "a None did not break the trace: %r" % (segs_gap,))
    print()

    # 5 -- the L4 shape
    print("check 5 -- at L4 the row is not empty: audio is still on the wire")
    h = UI.LinkHistory()
    # video shed by design, audio flowing, wire refusing two audio frames
    h.add(200.0, rung=4, kbps=0.0, drops=0.0, a_kbps=11.0, a_sheds=2)
    _ts, rows = h.window(60.0)
    r = rows[-1]
    check("l4_audio_present",
          r.get("a_kbps") == 11.0 and r.get("a_sheds") == 2,
          "the L4 sample carries no audio signal: %r" % (r,))
    check("l4_video_zero_not_missing",
          r["kbps"] == 0.0 and r["drops"] == 0.0,
          "video at L4 should read a MEASURED zero: %r" % (r,))
    print()

    # 6 -- the feed must not consume the bearer's signal
    print("check 6 -- the diagnostics do not call take_audio_sheds()")
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "communicator_live.py")).read()
    # Look at CODE, not comments: the fix documents the trap in a comment right
    # above the read, and a naive substring search matches its own warning.
    _at = src.index("self.history.add(")
    _feed = [l.split("#", 1)[0] for l in src[_at - 2000:_at].splitlines()]
    check("no_take_audio_sheds",
          not any("take_audio_sheds" in l for l in _feed),
          "the history feed calls take_audio_sheds(), which CLEARS the pending "
          "count Bearer.sample() reads to arm a downgrade -- sampling it for a "
          "graph steals the controller's congestion signal")
    print()

    # 7 -- the console legend obeys the same rule
    print("check 7 -- the console legend is ungated too")
    if not hasattr(UI, "num") or not hasattr(UI, "wire_legend"):
        # Old build: the legend is formatted inline with .get(k, "-") / .get(k, 0),
        # so there is nothing to interrogate and no way to tell a measured zero
        # from an absent counter. That IS the finding.
        for _n in ("num_zero_is_zero", "num_missing_is_not_zero",
                   "legend_shows_audio", "legend_missing_not_zero"):
            check(_n, False, "comms_ui has no num()/wire_legend(): the console "
                             "legend is formatted inline from video-only fields "
                             "with a default that hides absent counters")
        print()
        print("FAIL (%d): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    check("num_zero_is_zero", UI.num(0.0) == "0",
          "a measured zero rendered as %r" % (UI.num(0.0),))
    check("num_missing_is_not_zero", UI.num(None) == UI.MISSING,
          "an absent counter rendered as %r, not %r"
          % (UI.num(None), UI.MISSING))
    _l4 = UI.wire_legend({"v_fps_sent": 0.0, "v_kbps": 0.0,
                          "a_kbps": 11.2, "v_drop_fps": 0.0})
    check("legend_shows_audio", "11.2" in _l4 and "audio" in _l4,
          "at L4 the legend shows no audio at all: %r" % (_l4,))
    _empty = UI.wire_legend({})
    check("legend_missing_not_zero",
          UI.MISSING in _empty and " 0 " not in _empty,
          "an empty snapshot rendered as zeros: %r" % (_empty,))
    print()

    # 8 -- receive side
    print("check 8 -- the legend reports what is ARRIVING")
    _l = UI.wire_legend({"v_fps_sent": 23.7, "v_kbps": 131.0, "a_kbps": 11.2,
                         "v_fps_recv": 19.2, "v_rx_kbps": 97.0,
                         "a_rx_kbps": 10.8, "v_drop_fps": 0.0})
    check("legend_has_receive", "recv" in _l and "97" in _l and "10.8" in _l,
          "no receive figures on the legend: %r" % (_l,))
    import fnav as _F
    try:
        _w = _F.WireStats()
        _w.on_video_recv(5000)
        _w.on_audio_recv(400)
        _w._reset_t = 0.0                  # force a roll
        _w.on_audio_recv(400)
        _snap = _w.snapshot()
    except (TypeError, AttributeError) as _e:
        _snap = {}
        print("        (WireStats has no receive accounting: %r)" % (_e,))
    check("wirestats_counts_rx_bytes",
          _snap.get("v_rx_kbps") is not None and _snap.get("a_rx_kbps") is not None,
          "WireStats does not publish receive rates: %r" % (sorted(_snap),))
    print()

    # 9 -- backlog from memory
    print("check 9 -- the keyframe backlog is memory, and absence is not 'none'")
    check("relay_publishes_holds", hasattr(_F.Relay, "_publish_holds"),
          "the relay does not publish MediaHold -- nothing outside it can see a "
          "viewer that is stuck")
    try:
        import comms_control as _CC
        _has_read = hasattr(_CC.ControlPlane, "media_holds")
    except Exception as _e:
        _has_read = False
        print("        (comms_control not importable here: %r)" % (_e,))
    check("controlplane_reads_holds", _has_read,
          "the Communicator cannot read MediaHold")
    if not hasattr(UI, "hold_legend"):
        check("no_relay_is_not_no_backlog", False,
              "comms_ui has no hold_legend(): nothing renders the backlog")
        check("backlog_names_who_and_why", False, "no hold_legend()")
        print()
        print("FAIL (%d): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    _none = UI.hold_legend(None)
    _empty = UI.hold_legend([])
    check("no_relay_is_not_no_backlog",
          UI.MISSING in _none and _none != _empty,
          "a relay publishing NOTHING reads the same as a relay reporting a "
          "clear link: %r vs %r" % (_none, _empty))
    _busy = UI.hold_legend([{"addr": "10.102.60.1", "name": "John",
                             "awaiting_keyframe": True,
                             "held_keyframe_bytes": 67131,
                             "inter_frames_shed": 412,
                             "reason": "mediaspeed_cap 93000 bps"}])
    check("backlog_names_who_and_why",
          "John" in _busy and "67131" in _busy and "mediaspeed_cap" in _busy,
          "the backlog line does not say who is stuck or why: %r" % (_busy,))
    print()

    # 10 -- the RX report
    print("check 10 -- the receive-loop report identifies plane and state")
    import socket as _s, threading as _th, time as _t2, io as _io
    from contextlib import redirect_stdout as _rso

    def _drive(kill_midframe):
        left, right = _s.socketpair()
        class _C:
            name = "Probe"; host = "127.0.0.1"; port = 9000
        c = _C()
        c.sock = left; c.vsock = None
        c._stop = _th.Event()
        c.stats = _F.WireStats()
        c.mixer = type("M", (), {"feed": lambda *a, **k: None})()
        c.vdec = None
        c._kf_backlog = 0; c._kf_clear_since = 0.0
        c._force_key = False; c._force_key_for = 0
        left.setblocking(False)
        buf = _io.StringIO()
        th = _th.Thread(target=_F.Call._recv_loop, args=(c,), daemon=True)
        with _rso(buf):
            th.start()
            _t2.sleep(0.15)
            _F.send_frame(right, _F.pack_typed(_F.KIND_AUDIO, "Far", b"pcm"))
            _t2.sleep(0.15)
            if kill_midframe:
                right.sendall(_F._LEN.pack(64) + b"\x00" * 8)   # short of 64
                _t2.sleep(0.1)
            right.close()
            th.join(timeout=3)
        return buf.getvalue()

    _mid = _drive(True)
    _clean = _drive(False)
    check("rx_names_the_plane", "[RX/audio]" in _mid,
          "the report does not say which plane ended: %r" % (_mid[:200],))
    check("rx_reports_frames", "frames=1" in _mid,
          "the report does not say what had been received: %r" % (_mid[:300],))
    check("rx_reports_stopping", "stopping_already=False" in _mid,
          "the report cannot distinguish a socket taken away from a hang-up")
    check("rx_names_first_to_end", "FIRST to end" in _mid,
          "nothing marks which plane failed first, so the second plane's report "
          "reads as an independent fault")
    check("midframe_is_not_clean_close",
          "MID-FRAME" in _mid and "MID-FRAME" not in _clean,
          "a peer vanishing mid-frame reads the same as a clean hang-up.\n"
          "        mid:   %r\n        clean: %r" % (_mid[:160], _clean[:160]))
    check("clean_close_says_so", "cleanly between frames" in _clean,
          "a clean close is not named as one: %r" % (_clean[:200],))
    print()

    if FAILURES:
        print("FAIL (%d): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("PASS -- ungated: zero is zero, missing is missing, audio is recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
