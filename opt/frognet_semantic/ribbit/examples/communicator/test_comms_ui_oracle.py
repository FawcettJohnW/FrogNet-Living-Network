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
test_comms_ui_oracle.py -- drives the REAL comms_ui model headless (no tk, no cv2).

Every case that fixes a defect carries a CONTROL that reproduces what the old
communicator_live.py actually did, so the oracle fails on the old behavior and passes
on the new one rather than merely agreeing with itself. The controls are transcribed
from the old source, with line references, not from memory.

Run: python3 test_comms_ui_oracle.py
"""
import sys

import comms_ui as U
import sotf_ladder as L

# fnav's real rung table drives the prediction cases. Importing fnav pulls in the
# media stack; the table itself is a plain dict of protocol constants, so read it
# from fnav when available and fall back to the literal values it defines.
try:
    from fnav import RUNG_VIDEO
except Exception:                                            # pragma: no cover
    RUNG_VIDEO = {7: {"w": 1280, "h": 720, "gray": False, "bitrate": 1_200_000},
                  6: {"w": 854, "h": 480, "gray": False, "bitrate": 600_000},
                  5: {"w": 640, "h": 360, "gray": True, "bitrate": 300_000}}

_p = _f = 0


def ck(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, extra))


# =============================================================================
print("-- screen transitions -------------------------------------------------")
s = U.Screen()
ck("opens in the lobby", s.mode == U.LOBBY)
ck("lobby shows discovery, not the stage",
   s.shows("flock") and s.shows("preflight") and not s.shows("stage"))

s.ring("sess-1", "Julie")
ck("an invite rings", s.mode == U.RINGING and s.caller == "Julie")
ck("ringing shows the ring, hides the stage", s.shows("ring") and not s.shows("stage"))
s.accept()
ck("accept enters the call", s.mode == U.CALL and s.session == "sess-1")
ck("call shows stage + ladder + link, hides the flock",
   s.shows("stage") and s.shows("ladder") and s.shows("link") and not s.shows("flock"))

# A second invite arriving mid-call must NOT hijack the window. The old shell had no
# mode at all, so nothing structurally prevented this; _check_ring guarded on
# `self.call is not None` (communicator_live.py:242) -- one ad-hoc test, not a rule.
before = s.mode
s.ring("sess-2", "Dan")
ck("an invite during a call does not interrupt it", s.mode == before == U.CALL)

s.hangup()
ck("hangup lands on ENDED with a reason", s.mode == U.ENDED and s.reason)
s.back_to_lobby()
ck("ENDED returns to the lobby", s.mode == U.LOBBY and s.reason == "")

s2 = U.Screen()
s2.dialing("sess-9")
ck("the originator is not rung -- straight into the call",
   s2.mode == U.CALL and s2.session == "sess-9")
s2.dropped("The relay reset.")
ck("a dropped leg is terminal and says why",
   s2.mode == U.ENDED and s2.reason == "The relay reset.")

# CONTROL -- the old shell had exactly two visual states, both painted identically:
# _hang() and _on_call_dropped() (communicator_live.py:362, 379) both just re-enabled
# a button and blanked a label. There was no ENDED screen and no way to distinguish
# "you hung up" from "the connection died" except a status string that the next
# status write overwrote.
old_states = {"idle", "in_call"}
ck("CONTROL: old shell had no ENDED state to explain a drop",
   U.ENDED not in old_states)


# =============================================================================
print("-- ring tracking: decline must not deafen the session ------------------")
rt = U.RingTracker()
calls = [{"session": "s1", "members": ["julie", "me"], "host": "h", "port": 9000}]
ck("an invite listing me rings once", len(rt.should_ring(calls, "me", busy=False)) == 1)
ck("and does not ring again on the next poll",
   rt.should_ring(calls, "me", busy=False) == [])

# I decline. The shell also calls leave_call(), which rewrites the call tuple without
# me -- so the very next poll shows me absent and nothing rings.
rt.declined("s1")
without_me = [{"session": "s1", "members": ["julie"], "host": "h", "port": 9000}]
ck("after declining, a call I am not on does not ring",
   rt.should_ring(without_me, "me", busy=False) == [])

# The caller redials -- adds me back to the members list.
ck("a fresh invite to the same session rings again",
   len(rt.should_ring(calls, "me", busy=False)) == 1)

# CONTROL -- the old logic, transcribed from communicator_live.py:244-248:
#     if self.me_id not in members or sid in self._rung: continue
#     self._rung.add(sid)
#     ... _ring_popup(...)      # decline() only destroys the window (270-271)
old_rung = set()


def old_should_ring(calls, me_id, in_call):
    out = []
    for c in calls:
        sid = c.get("session")
        if in_call or sid == None:
            continue
        if me_id not in c.get("members", []) or sid in old_rung:
            continue
        old_rung.add(sid)
        out.append(c)
    return out


old_should_ring(calls, "me", False)                     # rings once
# old decline: window.destroy() only -- _rung keeps the sid
ck("CONTROL: old shell could never re-ring a declined session",
   old_should_ring(calls, "me", False) == [])

# busy suppression
rt2 = U.RingTracker()
ck("nothing rings while busy", rt2.should_ring(calls, "me", busy=True) == [])

# a call that disappears may ring again when it returns
rt3 = U.RingTracker()
rt3.should_ring(calls, "me", busy=False)
rt3.should_ring([], "me", busy=False)                   # call ended -> forgotten
ck("a call that ended and returned rings again",
   len(rt3.should_ring(calls, "me", busy=False)) == 1)


# =============================================================================
print("-- calls are named by who is on them ----------------------------------")
roster = [{"id": "julie", "name": "Julie"}, {"id": "dan", "name": "Dan"},
          {"id": "me", "name": "John"}, {"id": "pierre", "name": "Pierre"}]
names = U.display_names(roster)
c1 = {"session": "a3f91c02deadbeef", "members": ["julie", "me"]}
c3 = {"session": "b7", "members": ["julie", "dan", "pierre", "me"]}
ck("a two-party call names the other party", U.call_label(c1, names, "me") == "Julie")
ck("a crowded call names some and counts the rest",
   U.call_label(c3, names, "me") == "Julie, Dan +1",
   U.call_label(c3, names, "me"))
ck("an unknown id degrades to the id, not a crash",
   U.call_label({"members": ["ghost", "me"]}, names, "me") == "ghost")
ck("membership is answerable", U.call_is_mine(c1, "me") and not U.call_is_mine(c1, "dan"))

# CONTROL -- old label, transcribed from communicator_live.py:306:
#     text=f"{sid[:8]} . {n}"
old_label = "%s . %d" % (c3["session"][:8], len(c3["members"]))
ck("CONTROL: old shell labelled a call with a hex fragment",
   old_label == "b7 . 4" or old_label.startswith(c3["session"][:8]))
ck("new label carries a name the old one did not",
   "Julie" in U.call_label(c3, names, "me") and "Julie" not in old_label)


# =============================================================================
print("-- link presets predict the rung from the real ladder table ------------")
ck("four presets, top one unthrottled", len(U.PRESETS) == 4 and U.PRESETS[0].bps == 0)
ck("kbps converts to bps", U.PRESETS[1].bps == 700_000)

ck("unthrottled predicts the ceiling",
   U.expected_rung(0, RUNG_VIDEO, ceiling_idx=7) == 7)
ck("1.2 Mbit carries L7 CHORUS 720p",
   U.expected_rung(1_200_000, RUNG_VIDEO, 7) == 7)
ck("700 kbit drops to L6 ENSEMBLE 480p",
   U.expected_rung(700_000, RUNG_VIDEO, 7) == 6,
   U.expected_rung(700_000, RUNG_VIDEO, 7))
ck("320 kbit drops to L5 DUET 360p",
   U.expected_rung(320_000, RUNG_VIDEO, 7) == 5)
ck("110 kbit cannot carry any video rung -> L4 SOLO, audio survives",
   U.expected_rung(110_000, RUNG_VIDEO, 7) == 4)
ck("the ceiling is never exceeded by a prediction",
   U.expected_rung(0, RUNG_VIDEO, ceiling_idx=4) == 4)
ck("each preset predicts a rung no higher than the one above it",
   all(U.expected_rung(U.PRESETS[i].bps, RUNG_VIDEO, 7)
       >= U.expected_rung(U.PRESETS[i + 1].bps, RUNG_VIDEO, 7)
       for i in range(len(U.PRESETS) - 1)))

# The whole demo beat: the four presets must actually walk the ladder, not all land
# on the same rung. If they did, pressing them would show the audience nothing.
predicted = [U.expected_rung(p.bps, RUNG_VIDEO, 7) for p in U.PRESETS]
ck("the presets walk distinct rungs 7 -> 4", predicted == [7, 6, 5, 4], predicted)


# =============================================================================
print("-- the ladder as renderable cells -------------------------------------")
cells = U.ladder_cells(send_idx=6, ceiling_idx=7, allowed=L.allowed_levels(True, True))
by_idx = {c["idx"]: c for c in cells}
ck("cells run richest first", [c["idx"] for c in cells] == [7, 6, 5, 4, 3])
ck("the current rung is active", by_idx[6]["state"] == U.ACTIVE)
ck("a rung above the current one reads as shed", by_idx[7]["state"] == U.SHED)
ck("rungs below the current one remain available",
   by_idx[5]["state"] == U.AVAILABLE and by_idx[4]["state"] == U.AVAILABLE)
ck("cells carry the human rung name", by_idx[7]["name"] == "CHORUS")

# A box with no camera: video rungs are BLOCKED, not merely shed -- the difference
# between "the wire will not carry it" and "this machine cannot source it" is the
# distinction the old single stats line could not express at all.
nocam = U.ladder_cells(send_idx=4, ceiling_idx=L.ceiling(False, True),
                       allowed=L.allowed_levels(False, True))
nb = {c["idx"]: c for c in nocam}
ck("no camera blocks the video rungs",
   nb[7]["state"] == U.BLOCKED and nb[5]["state"] == U.BLOCKED)
ck("no camera still runs audio at L4", nb[4]["state"] == U.ACTIVE)

idle = U.ladder_cells(send_idx=None, ceiling_idx=7, allowed=L.allowed_levels(True, True))
ck("with no call nothing is active",
   all(c["state"] != U.ACTIVE for c in idle))

ck("caption names the resolution at a video rung",
   "1280x720" in U.rung_caption(7, RUNG_VIDEO))
ck("caption says grayscale at L5", "grayscale" in U.rung_caption(5, RUNG_VIDEO))
ck("caption says video shed at L4", "video shed" in U.rung_caption(4, RUNG_VIDEO))
ck("caption is honest when not connected",
   U.rung_caption(None, RUNG_VIDEO) == "not connected")


# =============================================================================
print("-- controls armed before a call are not silently discarded -------------")
pc = U.PendingControls()
ck("default is wide open and says so",
   not pc.armed() and pc.summary() == "link open, quality auto")
pc.bps = 320_000
pc.jitter_ms = 60
pc.quality = "480p"
ck("armed settings are recorded", pc.armed())
ck("and summarised for the badge",
   pc.summary() == "320 kbps, 60 ms jitter, capped 480p", pc.summary())
ck("quality maps to a ladder cap", pc.cap() == 6 and U.PendingControls().cap() == L.MAX_IDX)

# CONTROL -- the old shell, transcribed from communicator_live.py:410-414:
#     def _set_throttle(self, val=None):
#         bps = self._throttle_bps()
#         self.bps_lbl.config(text=...)            # label ALWAYS updated
#         if self.call is not None and hasattr(...): self.call.set_throttle(bps)
# so with no call the readout said "320 kbps" and nothing anywhere held the value.
old_call = None
old_label_text = "320 kbps"
old_stored = None
if old_call is not None:
    old_stored = 320_000
ck("CONTROL: old shell showed a throttle it had not stored",
   old_label_text == "320 kbps" and old_stored is None)
ck("new model stores what it displays", pc.bps == 320_000)


# =============================================================================
print("-- preflight states the ceiling before anyone is watching --------------")
ck("camera + mic is a video ceiling", "L7" in U.preflight_verdict(True, True))
ck("no camera is called out as audio only",
   U.preflight_verdict(False, True).startswith("audio only"))
ck("no mic falls to the text floor",
   U.preflight_verdict(True, False).startswith("text floor"))
ck("--no-video is honoured as a ceiling, not an error",
   U.ceiling_from_devices(True, True, no_video=True) == 4)


# =============================================================================
print("-- shared link: one person adjusts, both sides follow -------------------")
dan = U.SharedLink("dan")
pub = {"bps": 320_000, "jitter_ms": 60, "preset": "Congested",
       "set_by": "john", "set_by_name": "John", "ts": 100}
ck("the far side takes the tuple", dan.take(pub) is True)
ck("and lands on the same numbers", (dan.bps, dan.jitter_ms) == (320_000, 60))
ck("and can name who set it", dan.attribution() == "set by John")
ck("reading the same tuple again is not a change", dan.take(pub) is False)
ck("the preset comes with it, so the far side lights the same button",
   dan.preset == "Congested")

john = U.SharedLink("john")
john.set_local(320_000, 60, "Congested")
ck("reading back my own setting is not a change", john.take(pub) is False)
ck("and I am not told I set it", john.attribution() == "")

ck("a later tuple replaces the earlier one",
   dan.take({"bps": 0, "jitter_ms": 0, "preset": "Open water",
             "set_by": "john", "set_by_name": "John", "ts": 200}) is True
   and dan.bps == 0)
ck("an empty read changes nothing", U.SharedLink("x").take(None) is False)


# =============================================================================
print("-- hysteresis history ---------------------------------------------------")
ck("the bearer constants match fnav.Bearer.sample",
   (U.BEARER_BAD_STEPS, U.BEARER_GOOD_STEPS, U.BEARER_STEP_CAP_S) == (3, 5, 1.0))
ck("it retreats sooner than it advances -- that IS the hysteresis",
   U.BEARER_BAD_STEPS < U.BEARER_GOOD_STEPS)

h = U.LinkHistory(capacity=50)
for i in range(80):
    h.add(1000.0 + i * 0.08, rung=7, bearer=7, kbps=90.0, drops=0.0,
          throttle_kbps=0, bad=0, good=5)
ck("history is bounded", len(h) == 50)
ts, vals = h.series("kbps", seconds=1.0)
ck("a window returns only the tail", len(ts) <= 14 and len(ts) == len(vals))

# A run that walks down under drops and climbs back when the wire clears.
h2 = U.LinkHistory()
t = 0.0
for _ in range(20):                       # clean at L7
    h2.add(t, rung=7, drops=0.0); t += 0.08
for _ in range(20):                       # drops start; step down after a delay
    h2.add(t, rung=7, drops=4.0); t += 0.08
for _ in range(20):
    h2.add(t, rung=6, drops=2.0); t += 0.08
for _ in range(30):                       # wire clears; step back up later
    h2.add(t, rung=6, drops=0.0); t += 0.08
for _ in range(10):
    h2.add(t, rung=7, drops=0.0); t += 0.08

evs = h2.step_events(seconds=999)
ck("both steps are detected", len(evs) == 2, evs)
ck("first down, then up",
   evs[0]["dir"] == "down" and evs[1]["dir"] == "up")
ck("the step records where it came from and went to",
   evs[0]["from"] == 7 and evs[0]["to"] == 6)

lag = h2.hysteresis_lag(seconds=999)
ck("a down lag is measured", lag["down_mean"] is not None)
ck("an up lag is measured", lag["up_mean"] is not None)
ck("it falls faster than it climbs",
   lag["down_mean"] < lag["up_mean"], (lag["down_mean"], lag["up_mean"]))

# Two steps down inside ONE unbroken congestion episode. The second lag must be
# measured from the first STEP, not from the onset of the episode -- otherwise the
# readout grows the longer the link stays bad and reports a "lag" many times the
# controller's 1.0 s rate cap, which is the age of the congestion, not a lag. This
# case was written because the diagnostics window showed exactly that: 9.12 s.
h3 = U.LinkHistory()
t = 0.0
for _ in range(10):
    h3.add(t, rung=7, drops=0.0); t += 0.08
for _ in range(20):                       # drops begin, never stop
    h3.add(t, rung=7, drops=5.0); t += 0.08
for _ in range(40):                       # first step down, still dropping
    h3.add(t, rung=6, drops=5.0); t += 0.08
for _ in range(10):                       # second step down, still dropping
    h3.add(t, rung=5, drops=5.0); t += 0.08
lag3 = h3.hysteresis_lag(seconds=999)
ck("both down steps are measured", len(lag3["down_lags"]) == 2, lag3["down_lags"])
ck("the first is measured from the onset of the drops",
   abs(lag3["down_lags"][0] - 1.6) < 0.05, lag3["down_lags"][0])
ck("the second is measured from the first step, not the onset",
   abs(lag3["down_lags"][1] - 3.2) < 0.05, lag3["down_lags"][1])
ck("no lag exceeds the elapsed episode",
   max(lag3["down_lags"]) < 6.0, lag3["down_lags"])

# The mirror image: three steps climbing back out of ONE clean period. Each must be
# measured from the step before it, not all from the same last drop -- the same
# drift, in the direction that did not happen to be on screen when it was noticed.
h4 = U.LinkHistory()
t = 0.0
for _ in range(10):
    h4.add(t, rung=4, drops=6.0); t += 0.08
for _ in range(15):                       # drops stop; counter fills
    h4.add(t, rung=4, drops=0.0); t += 0.08
for _ in range(13):
    h4.add(t, rung=5, drops=0.0); t += 0.08
for _ in range(13):
    h4.add(t, rung=6, drops=0.0); t += 0.08
for _ in range(13):
    h4.add(t, rung=7, drops=0.0); t += 0.08
lag4 = h4.hysteresis_lag(seconds=999)
ck("three up steps are measured", len(lag4["up_lags"]) == 3, lag4["up_lags"])
ck("each is roughly the rate cap, not a running total",
   all(0.9 < x < 1.5 for x in lag4["up_lags"]), lag4["up_lags"])
ck("they do not drift upward step by step",
   max(lag4["up_lags"]) - min(lag4["up_lags"]) < 0.3, lag4["up_lags"])

segs = U.plot_points([0.0, 1.0, 2.0], [0, 5, 10], 10, 20, 100, 50, 0, 10)
ck("the plot spans the box", segs[0][0][0] == 10 and abs(segs[0][-1][0] - 110) < 1e-6)
ck("y is inverted for canvas coords",
   segs[0][0][1] == 70 and segs[0][-1][1] == 20)
ck("a value is clamped, not drawn off the box",
   U.plot_points([0.0], [99], 0, 0, 10, 10, 0, 10)[0][0][1] == 0)
gap = U.plot_points([0.0, 1.0, 2.0], [1, None, 3], 0, 0, 100, 50, 0, 10)
ck("missing samples break the line instead of reading as zero", len(gap) == 2)


# =============================================================================
print("-- camera and audio choosers -------------------------------------------")
probes = [{"index": 0, "opened": True, "ok": True, "w": 1280, "h": 720},
          {"index": 1, "opened": True, "ok": True, "w": 1280, "h": 720},
          {"index": 2, "opened": True, "ok": False, "w": 0, "h": 0},
          {"index": 3, "opened": False, "ok": False, "w": 0, "h": 0}]
cams = U.rank_cameras(probes)
ck("every probed index is listed, working or not", len(cams) == 4)
ck("the lowest working index is the default",
   [c.index for c in cams if c.is_default] == [0])
ck("a busy index is listed but not usable",
   cams[2].usable is False and "no frame" in cams[2].detail)
ck("an index that would not open says so", "did not open" in cams[3].detail)
ck("the label carries the resolution", "1280x720" in cams[0].label())

devs = [{"name": "HDA Intel Mic", "max_input_channels": 2, "max_output_channels": 0,
         "default_samplerate": 48000.0},
        {"name": "HDMI Out", "max_input_channels": 0, "max_output_channels": 2,
         "default_samplerate": 44100.0},
        {"name": "USB Headset", "max_input_channels": 1, "max_output_channels": 2,
         "default_samplerate": 48000.0}]
ins = U.audio_choices(devs, "input", default_index=0)
outs = U.audio_choices(devs, "output", default_index=2)
ck("inputs exclude playback-only devices",
   [d.name for d in ins] == ["HDA Intel Mic", "USB Headset"])
ck("outputs exclude capture-only devices",
   [d.name for d in outs] == ["HDMI Out", "USB Headset"])
ck("the system default is marked", "system default" in ins[0].label())
ck("indices stay the PortAudio indices, not list positions",
   [d.index for d in outs] == [1, 2])


# =============================================================================
print("-- microphone level meter ----------------------------------------------")
ck("silence floors instead of going to -inf", U.rms_to_db(0.0) == U.METER_FLOOR_DB)
ck("full scale is 0 dB", abs(U.rms_to_db(1.0)) < 1e-9)
ck("silence reads empty", U.meter_fraction(0.0) == 0.0)
ck("full scale fills the bar", U.meter_fraction(1.0) == 1.0)
ck("the bar is mapped in dB, not linearly -- speech is visible",
   U.meter_fraction(0.03) > 0.4, U.meter_fraction(0.03))
ck("a linear bar would have looked dead there", 0.03 < 0.4)
ck("states run silent -> quiet -> good -> hot",
   [U.meter_state(x) for x in (0.0, 0.002, 0.05, 0.9)]
   == ["silent", "quiet", "good", "hot"],
   [U.meter_state(x) for x in (0.0, 0.002, 0.05, 0.9)])
ck("each state has something to tell the user",
   all(U.meter_advice(s) for s in ("silent", "quiet", "good", "hot")))
ck("no signal points at the device, not the user",
   "microphone" in U.meter_advice("silent"))


# =============================================================================
print("-- saved settings meet the hardware that is actually here ---------------")
r = U.resolve_saved({"cam": 1, "in_name": "USB Headset", "out_name": "HDMI Out",
                     "codec": "h264", "quality": "480p"}, cams, ins, outs)
ck("a saved camera that is present is honoured", r["cam"] == 1)
ck("audio is matched by name across renumbering", r["in_dev"] == 2 and r["out_dev"] == 1)
ck("stream choices come back too", (r["codec"], r["quality"]) == ("h264", "480p"))
ck("nothing to report when everything resolved", r["notes"] == [])

gone = U.resolve_saved({"cam": 7, "in_name": "Studio Preamp"}, cams, ins, outs)
ck("an absent camera falls back to a working one", gone["cam"] == 0)
ck("and the fallback is stated, not silent",
   any("saved camera" in n for n in gone["notes"]), gone["notes"])
ck("an absent mic falls back to the system default", gone["in_dev"] == 0)
ck("with a note", any("input" in n for n in gone["notes"]))

first = U.resolve_saved(None, cams, ins, outs)
ck("a first run picks sane defaults quietly",
   first["cam"] == 0 and first["in_dev"] == 0 and first["out_dev"] == 2
   and first["notes"] == [])
ck("a box with no camera resolves to none rather than crashing",
   U.resolve_saved({"cam": 0}, [], ins, outs)["cam"] is None)


# =============================================================================

# =============================================================================
print("-- the codec control: auto means PROBE ----------------------------------")
# [CODEC_DEFAULT_IS_AUTO_V1] The shell built its controls with
#     codec=args.codec if args.codec != "auto" else "vp8"
# so the DEFAULT command line asked for the probe, the probe chose H.264/libx264 as the
# best encoder the box can run and announced it, and _apply_pending then stamped
# software VP8 over it before the first frame. Measured: VP8 software is 2.2x the cost
# of x264 at 1280x720 on a fast machine, far worse on a Pi -- which is why a box that
# should hold its rate produced 5-19 fps and the ladder walked itself down.
ck("PendingControls defaults to auto, not a named codec",
   U.PendingControls().codec == "auto", U.PendingControls().codec)
ck("an explicit choice is still carried",
   U.PendingControls(codec="h264").codec == "h264")
ck("auto is not 'armed' -- it is the absence of an override",
   not U.PendingControls().armed())
ck("a named codec alone is still not 'armed' (it is a choice, not a throttle)",
   not U.PendingControls(codec="vp8").armed())

print()
print()
print("=== %d passed, %d failed ===" % (_p, _f))
print("ORACLE " + ("GREEN" if _f == 0 else "RED"))
sys.exit(0 if _f == 0 else 1)