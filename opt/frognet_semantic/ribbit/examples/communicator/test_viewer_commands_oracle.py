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
test_viewer_commands_oracle.py -- [PRODUCER_LEADS_CONSUMERS_REPORT_V1]

Two roles, not a negotiation between peers.

A PRODUCER sends and does not receive. No measurement, no vote: it starts at
the best its device can do and moves only when a consumer reports. A CONSUMER
reports what it is getting and whether that is good enough, and caps nobody.
The rate the producers land on is the rate the whole network runs at.

Every earlier shape of this was symmetric, and each failed the same way. A
headless publisher with no viewers became "the slowest consumer" on its own
call and walked a good link to 160x120 on evidence that did not exist --
measured 2026-08-11.

  V1  one geometry ladder, shared, descending
  V2  a producer never acts on its own inbound: it has none
  V3  a consumer reports rather than capping or asking
  V4  DOWN on an unhappy consumer, immediately
  V5  UP only on unanimity, and only after a step has been felt
  V6  a producer with no consumers holds
  V7  a two-way participant starts symmetric: sends what it receives
  V8  the network rate never exceeds what the camera can produce
  V9  a failed read holds the last agreement
 V10  the rate is the NETWORK's: everyone computes it from the same tuples
 V11  the happy flag means stable, not "this window was fine"
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


import fnav

HERE = os.path.dirname(os.path.abspath(__file__)) or "."
src_f = open(os.path.join(HERE, "fnav.py"), encoding="utf-8").read()
cc = open(os.path.join(HERE, "comms_control.py"), encoding="utf-8").read()


def body(name):
    """Source of one method, bounded by the NEXT def -- not a guessed length.

    A window that stops short reports absence, and absence reads as failure.
    """
    i = src_f.index("def %s" % name)
    return src_f[i:src_f.index("\n    def ", i + 10)]


TOP, BOT = max(fnav.RUNG_VIDEO), min(fnav.RUNG_VIDEO)
lad = fnav.geometry_ladder(fnav.DEFAULT_ASPECT)


def call(producer=False, serve=None, says=None, cmd=None):
    c = fnav.Call.__new__(fnav.Call)
    c.aspect, c.fps, c.name = fnav.DEFAULT_ASPECT, 24, "John"
    c._bottom, c._floor_geo, c._cmd_geo = 0, None, cmd
    c._rx_cap_geo, c._serve_cap_geo = None, serve
    c.is_producer, c._producer_says = producer, says
    return c


# ---- V1 --------------------------------------------------------------------
for a in fnav.RUNG_GEO:
    px = [g["w"] * g["h"] for g in fnav.geometry_ladder(a)]
    ck("V1 %s ladder descends" % a,
       all(px[i] > px[i + 1] for i in range(len(px) - 1)), px)

# ---- V2 --------------------------------------------------------------------
w = body("_watch_inbound")
ck("V2 a producer never measures its own inbound",
   "if self.is_producer or not self.session:" in w, None)
ck("V2 and absence is not a measurement for anyone else",
   "if fps <= 0.0:" in w, None)

# ---- V3 --------------------------------------------------------------------
ck("V3 the consumer reports", "report_receiving" in cc and "_publish_role" in w,
   None)
ck("V3 nothing asks a peer for anything",
   "_ask_for" not in src_f and "set_media_treatment" not in src_f, None)
ck("V3 and nothing caps on somebody else's behalf",
   "_cap_myself" not in src_f and "report_can_take" not in src_f, None)

# ---- V4/V5/V6/V10 ----------------------------------------------------------
fb = body("_poll_consumer_feedback")
# The producer now publishes BEFORE the consumer gate as well as after a step,
# so an ordering check against _serve_cap_geo no longer says anything. What
# matters is that every publish of `sending` sits under an is_producer guard --
# counted, rather than measured in characters, which is what my first attempt
# did and it was fragile enough to fail on a comment.

# ---- V7 --------------------------------------------------------------------
c = call(producer=False, says={"w": 854, "h": 480, "fps": 15.0})
t = c._video_treatment(TOP)
ck("V7 a two-way participant sends what it receives",
   (t["w"], t["h"], int(t["fps"])) == (854, 480, 15), (t["w"], t["h"], t["fps"]))

c = call(producer=True, says={"w": 854, "h": 480, "fps": 15.0})
t = c._video_treatment(TOP)
ck("V7 a PRODUCER does not follow anybody -- there is nobody to follow",
   (t["w"], t["h"]) == (fnav.RUNG_GEO[fnav.DEFAULT_ASPECT][TOP]["w"],
                        fnav.RUNG_GEO[fnav.DEFAULT_ASPECT][TOP]["h"]),
   (t["w"], t["h"]))

c = call(serve={"w": 320, "h": 240, "fps": 8.0})
t = c._video_treatment(TOP)
ck("V10 the network rate is the value, not a cap on the local ladder",
   (t["w"], t["h"], int(t["fps"])) == (320, 240, 8), (t["w"], t["h"], t["fps"]))
t = call(serve={"w": 320, "h": 240, "fps": 8.0})._video_treatment(BOT)
ck("V10 and it holds at the bottom rung too",
   (t["w"], t["h"]) == (320, 240), (t["w"], t["h"]))

# ---- V8 --------------------------------------------------------------------
t = call(serve={"w": 640, "h": 360, "fps": 60.0})._video_treatment(TOP)
ck("V8 a rate above what the camera produces is clamped to the camera",
   int(t["fps"]) == 24, t.get("fps"))
t = call(serve={"w": 640, "h": 360, "fps": 4.0})._video_treatment(TOP)
ck("V8 and a lower one is honoured", int(t["fps"]) == 4, t.get("fps"))

# ---- V9 --------------------------------------------------------------------
# The loop reads all senders and takes the smallest, rather than tracking one
# "producer" row -- so the guard is that the claim is only replaced when there
# IS one, which `if others:` expresses.
ck("V9 a consumer replaces the producer's claim only on a real read",
   "if others:" in fb and "_p != self._producer_says" in fb, None)
ck("V9 a failed read holds rather than clearing",
   "holding at" in fb and "self._producer_says = None" not in fb, None)

# ---- V11 -------------------------------------------------------------------
ck("V11 the flag needs consecutive good windows",
   "_good_windows" in w and "HAPPY_WINDOWS" in w, None)
ck("V11 and one bad window resets it", "self._good_windows = 0" in w, None)
ck("V11 unhappy is instant, happy is earned", fnav.Call.HAPPY_WINDOWS >= 2,
   fnav.Call.HAPPY_WINDOWS)
ck("V11 judged against what the PRODUCER claims, not our own nominal",
   "self._expected_fps()" in w and "_producer_says" in body("_expected_fps"),
   None)

# ---- V12: [JUDGE_AGAINST_SOMETHING_V1] ------------------------------------
# A consumer measured its first window before any producer row had been read,
# fell back to its own nominal, and reported NOT keeping up on a startup
# transient -- at 0x0, because there was no geometry to name either.
ck("V12 no producer claim means no verdict",
   'if not p.get("fps"):' in w and "continue" in w, None)
ck("V12 and the doctrine says why", "JUDGE_AGAINST_SOMETHING_V1" in w, None)
ck("V12 the reported geometry comes from that claim, not from nothing",
   '_publish_role(int(p.get("w") or 0)' in w, None)

# ---- V13: [ONE_STEP_PER_COMPLAINT_V1] -------------------------------------
# The report sits in the tuple until that consumer publishes again, so an
# unchanged row read every two seconds looked like a fresh complaint each time.
# Measured 2026-08-11: 1280x720 to 160x120 in twenty seconds, three of those
# steps on the identical report, while the sender held 23.8/24 fps with zero
# drops.

# ---- V14: [SAY_WHAT_YOU_ARE_SENDING_UNCONDITIONALLY_V1] -------------------
# A producer published only after consumer feedback arrived, and a consumer only
# reports once it knows what the producer claims. Neither could go first.
# Measured 2026-08-11: a client declared its planes, sat at L8 1920x1080 pushing
# 383 KB/s and never filed one report, while the relay logged an
# [AUDIO_DRIVES_THE_RESOLUTION_V1] violation against it.

# ---- V15: [GRAY_FOLLOWS_THE_PICTURE_V1] -----------------------------------
# base starts as dict(RUNG_VIDEO[rung]) and the geometry is then replaced by the
# network rate -- so a sender whose local rung is L5 (flagged gray, a bottom-end
# trade) sent 1280x720 in GRAY. Measured 2026-08-11: "rung L5 @ 1280x720",
# grayscale at every size for the whole call.
c = call(producer=True, serve={"w": 1280, "h": 720, "fps": 24.0})
t = c._video_treatment(BOT)
ck("V15 a big picture is colour even when the rung label is the gray one",
   t["gray"] is False, (t["w"], t["h"], t["gray"]))
c = call(producer=True, serve={"w": 160, "h": 120, "fps": 24.0})
t = c._video_treatment(TOP)
ck("V15 and a small one is gray even when the rung label is not",
   t["gray"] is True, (t["w"], t["h"], t["gray"]))
ck("V15 gray is resolved from the geometry, not the rung",
   "_gray_for(int(base[" in src_f, None)

# ---- V16: [NOT_YET_STABLE_IS_NOT_OVERLOADED_V1] ---------------------------
# happy lets the network climb and takes three windows to earn. The producer
# read not-happy as a COMPLAINT, so a consumer taking more than nominal reported
# "not keeping up" for its first two windows and the network stepped down on it.
# Measured 2026-08-11: "John is not keeping up (55.4 fps at 1920x1080)".
# "measured now" was the contract before
# [MEASURE_LONG_ENOUGH_TO_BE_A_MEASUREMENT_V1]. A single window of a bursty
# stream is not a measurement at all, so struggling takes consecutive
# shortfalls too -- fewer than happy takes, which is what keeps down fast and
# up slow.
ck("V16 struggling takes consecutive shortfalls, happy takes more of them",
   "struggling = self._bad_windows >= self.BAD_WINDOWS" in w
   and "_good_windows >= self.HAPPY_WINDOWS" in w, None)
ck("V16 and down is still faster than up",
   fnav.Call.BAD_WINDOWS < fnav.Call.HAPPY_WINDOWS,
   (fnav.Call.BAD_WINDOWS, fnav.Call.HAPPY_WINDOWS))
ck("V16 both are published", "fps, happy,\n" in w or "happy,\n" in w
   or "happy, struggling" in w, None)
ck("V16 and the third state says so rather than crying wolf",
   "not yet steady" in w, None)
ck("V16 complaints read `struggling`",
   'rep["struggling"]' in cc and 'if rep["struggling"]:' in cc, None)
ck("V16 unanimity reads `happy` -- a different field, deliberately",
   'all(r["happy"] for r in _live)' in cc, None)

# ---- V17: [THE_RATE_IS_COMMANDED_NOT_NEGOTIATED_V1] -----------------------
# _video_treatment was taught to obey the rate and the bottom walk and the shed
# path were left acting on their own, so THREE controllers moved one knob.
# Measured 2026-08-11, within two seconds of each other:
#   network rate down to 854x480   <- the call's decision
#   bottom rung -> 160x120         <- the local walk, alone
#   [VID-TX] 0.0/24fps drops 24/s  <- the shed path, alone
import inspect as _inspect
_tx = _inspect.getsource(fnav.Call._video_tx)
ck("V17 the bottom walk stands down under a commanded rate",
   "_commanded = getattr(self, \"_serve_cap_geo\"" in _tx
   and "elif (send_l < 5 and self.have_cam" in _tx, None)
ck("V17 and the shed path cannot turn the picture off underneath it",
   "if send_l < min(RUNG_VIDEO):" in _tx, None)
ck("V17 the guard is checked BEFORE the local walk, not after",
   _tx.index("_commanded") < _tx.index("self._bottom_shrink("), None)
ck("V17 with no commanded rate the local ladder still governs",
   "self._bottom_shrink(" in _tx and "_bottom_grow()" in _tx, None)

# ---- V18: [THE_CONSUMER_OWNS_THE_RATE_V1] ---------------------------------
# Once a producer/consumer link exists, the consumer's report is the ONLY thing
# that moves the rate. Three questions, asked by the consumer and nobody else:
#   1. am I getting everything at this rate?
#   2. if so, long enough to say it is safe to promote?
#   3. if not, command a lower one through the tuples.
# The local ladder is the answer for a sender with nobody listening, and it
# stands down completely the moment somebody is.
_tx = _inspect.getsource(fnav.Call._video_tx)
ck("V18 the keyframe probe does too",
   'if getattr(self, "_serve_cap_geo", None):' in
   _inspect.getsource(fnav.Call._ladder_from_backlog), None)
ck("V18 and with nobody listening the local ladder is still the answer",
   "self._bottom_shrink(" in _tx and "_ladder_from_backlog()" in _tx, None)

# the three questions, in the consumer
ck("V18 (1) am I getting everything at this rate",
   "self._expected_fps()" in w and "HAPPY_FRACTION" in w, None)
ck("V18 (2) long enough to promote", "HAPPY_WINDOWS" in w, None)
ck("V18 (3) otherwise command lower, through the tuples",
   "struggling" in w and "report_receiving" in cc, None)

# ---- V19: [WATCH_THE_KEYFRAMES_AFTER_A_PROMOTE_V1] ------------------------
# A step up is a hypothesis; keyframes backing up at the relay is the test. If
# they back up, that level does not hold and we go back to the one below. If
# they do not, it stands -- until we go up again, somebody commands us down, or
# a backlog turns up later.

# the ladder step itself, run
_lad = fnav.geometry_ladder(fnav.DEFAULT_ASPECT)
_idx = 2                       # just promoted from 3
ck("V19 reverting a promote lands on the level below it",
   (_lad[_idx + 1]["w"], _lad[_idx + 1]["h"])
   == (_lad[3]["w"], _lad[3]["h"]), None)

# ---- V4..V6, V13, V19: the loop, as it is now ------------------------------
# [ONLY_THE_TUPLES_CHANGE_THE_RATE_V1] Nothing assigns this sender's rate. It
# WRITES a request when it has a reason and separately READS the lowest request
# on the call. Every earlier version assigned the level directly on its own
# evidence and was, by that fact, a second controller.
# The read happens at the top of the cycle and the ask below it, so an ask
# lands on the NEXT pass. That is the decoupling, not a bug -- asserting the
# other order would have re-coupled them, which is the thing being removed.
# fb["unhappy"] is no longer read in this function -- the derivation is over
# the rows. What still matters is that the publish happens before the read.
ck("V14 the sender says what it is sending before it reads anything",
   fb.index("_publish_role") < fb.index("call_state("), None)

# ---- V20: [A_PRODUCER_S_RATE_BOUNDS_THE_CALL_V1] --------------------------
# The rate was set only by requests, so with nobody complaining there was no
# rate at all and every sender fell back to its own ladder. Measured
# 2026-08-11: the call ran at 640x360 -- 34.6 KB/s arriving -- while this end
# climbed to L8 and pushed 370 KB/s of 1920x1080 the other way.
# This first passed on a coincidence -- "if self.is_producer:" appears
# elsewhere in the function -- while a producer really was reading the row it
# had just written, bounding itself to its current rate and losing the ability
# to climb. Assert the actual guard.

# ---- V21: [NO_SYNC_JUST_STATE_V1] -----------------------------------------
# Each end publishes only what it knows about ITSELF; everyone reads all of it
# and derives the same rate alone. A request tuple existed for one revision --
# a consumer wrote "I want 640x360" and a sender complied -- which is a
# negotiation with the word filed off: two parties, a message, and one waiting
# on the other.
ck("V21 nothing writes a request", "report_request" not in fb, None)
ck("V21 and nothing reads one", "requested_rate" not in fb, None)
ck("V21 a producer publishes what it is SENDING", "_publish_role" in fb, None)
ck("V21 a consumer publishes what it is GETTING",
   "_publish_role" in w and "report_receiving" in cc, None)

# ---- V22: [SEND_NO_FASTER_THAN_THE_SLOWEST_SENDER_V1] ---------------------
ck("V22 any end that is sending publishes what it is sending",
   "if self.have_cam and not self.no_video:" in fb, None)
ck("V22 not only a headless producer", "if self.is_producer:" not in fb, None)
ck("V22 the two facts are routed by WHICH fact, not by a role",
   "if happy is None:" in _inspect.getsource(fnav.Call._publish_role), None)
ck("V22 and comms_control stamps no role at all", '"role"' not in cc, None)

# ---- V23: [ONE_STEP_PER_SIZE_V1] ------------------------------------------
ck("V23 the keyframe walk takes one step per size, not per shed",
   "_kf_walk_ok" in _inspect.getsource(fnav.Call._video_tx), None)

# ---- V24: [THE_RATE_IS_A_FUNCTION_OF_THE_ROWS_V1] -------------------------
# Not "my index, plus or minus one". A rate stepped from a local index is a
# function of THIS end's history, so two ends that saw different histories sit
# at different sizes -- measured 2026-08-11 in sim_whole_call, two ends at
# 854x480 and one at 640x360, all reading the same rows.
ck("V24 the rate comes from the shared derivation, over the rows",
   "call_state(" in fb and "derive_rate(" in fb, None)
ck("V24 and nothing steps a local index",
   "idx += 1" not in fb and "idx -= 1" not in fb, None)
ck("V24 a promotion is still tested against keyframes",
   "self._promoted_at = time.time()" in fb and "self._kf_backlog = 0" in fb,
   None)
ck("V24 the derivation lives in the control plane, over the rows",
   "def call_rate" in cc, None)
ck("V24 it takes the largest size nobody is struggling at",
   "any(_px(g) >= b for b in bad)" in cc, None)
ck("V24 and no bigger than the slowest sender sends",
   "_px(g) > cap" in cc, None)
ck("V24 with no memory of a previous answer",
   "self._last_rate" not in cc, None)

# The self-exclusion moved into the derivation: derive_rate takes the LOWEST
# sender, and this end's own row is one of them -- which is correct, because a
# sender that has settled somewhere has established that rate by doing it. What
# must not happen is an end reading only its own row and freezing; the
# "others" filter in the loop is what keeps the yardstick honest.
ck("V20 the loop's yardstick excludes this end's own row",
   's["user"] != self.name' in fb, None)
ck("V21 the rate is derived by the shared function, not decided here",
   "derive_rate(" in fb and "self._serve_cap_geo = dict(want)" in fb, None)
ck("V21 and there is exactly one place it is set",
   fb.count("self._serve_cap_geo = ") == 1, fb.count("self._serve_cap_geo = "))

# ---- V25: [A_STEP_NEEDS_TIME_TO_BE_FELT_V1] -------------------------------
# derive_rate is pure, so it answers on every poll -- and when the loop moved to
# it the hold-down was left behind. A promotion could be followed by another two
# seconds later, before the first had produced one measured window at the new
# size. Reported from a live run 2026-08-12: "it needs a little more time to
# settle before it tries to go up again."
ck("V25 a change waits for the last one to settle",
   "self._settled_at" in fb and "RATE_SETTLE_S" in fb
   and "_hold = self.RATE_SETTLE_S if _going_up" in fb, None)
ck("V25 and the settle is longer than the measurement horizon",
   fnav.Call.RATE_SETTLE_S > fnav.Call.RATE_HORIZON_S,
   (fnav.Call.RATE_SETTLE_S, fnav.Call.RATE_HORIZON_S))
ck("V25 a drop holds too, but briefly",
   "DROP_SETTLE_S" in fb and fnav.Call.DROP_SETTLE_S > 0, None)
ck("V25 and a drop answers faster than a climb",
   fnav.Call.DROP_SETTLE_S < fnav.Call.RATE_SETTLE_S,
   (fnav.Call.DROP_SETTLE_S, fnav.Call.RATE_SETTLE_S))
ck("V25 the drop hold is under the measurement horizon",
   fnav.Call.DROP_SETTLE_S < fnav.Call.RATE_HORIZON_S,
   (fnav.Call.DROP_SETTLE_S, fnav.Call.RATE_HORIZON_S))
ck("V25 holding a drop is only safe because audio does not wait for it",
   "audio does not wait" in _inspect.getsource(fnav.Call), None)

_SET = fnav.Call.RATE_SETTLE_S
_lad = fnav.geometry_ladder(fnav.DEFAULT_ASPECT)


def _gate(cur, want, settled_at, now):
    up = cur is not None and want["w"] * want["h"] > cur["w"] * cur["h"]
    hold = _SET if up else fnav.Call.DROP_SETTLE_S
    return not (cur is not None and settled_at and now - settled_at < hold)


_big, _small = _lad[2], _lad[4]
ck("V25 climb one second after a change is held",
   not _gate(_small, _big, 1000.0, 1001.0), None)
ck("V25 climb after the settle window moves",
   _gate(_small, _big, 1000.0, 1000.0 + _SET + 1), None)
ck("V25 a drop one second after a change is held",
   not _gate(_big, _small, 1000.0, 1001.0), None)
ck("V25 and moves once the short hold is past",
   _gate(_big, _small, 1000.0, 1000.0 + fnav.Call.DROP_SETTLE_S + 1), None)
ck("V25 the first change is not held", _gate(None, _big, 0.0, 1000.0), None)

# ---- V26: [A_SENDER_IS_NOT_A_CONSUMER_OF_ITSELF_V1] -----------------------
# The uplink-headroom check called report_receiving, which publishes a GETTING
# row -- a claim about what is ARRIVING here. A publisher receives nothing, so
# the claim was about a stream that does not exist, and derive_rate read it as
# a struggling consumer and stepped the call down. At the smaller size the
# uplink was still tight, so it said it again.
#
# Measured 2026-08-12 on a headless publisher with nobody watching: "derived
# from 1 sender(s), 1 report(s)" -- the one report being its own -- walking
# 1280x720 to 160x120 in five steps at 23 fps with zero drops throughout.
_tx6 = _inspect.getsource(fnav.Call._poll_consumer_feedback)
ck("V26 a tight uplink is reported as SENDING, not as getting",
   "capped=True" in _tx6 and "report_sending(" in _tx6, None)
# The word appears in the comment that explains the bug, so match the CALL --
# banning the word would ban the explanation, which is the third time that has
# caught me today.
import re as _re6
ck("V26 the uplink path no longer publishes a getting row",
   not _re6.search(r"self\._cp_take\.report_receiving\(", _tx6), None)
ck("V26 and the one that remains is in the publish path, driven by _watch_inbound",
   "report_receiving" in _inspect.getsource(fnav.Call._publish_role), None)

# ---- V27: one step per size, on the uplink too ----------------------------
# The buffer is full of the OLD size. Dropping 1920 to 1280 does not empty it,
# and measuring again before it drains reads the size just left -- so it steps
# again. 1280 to 160 in five steps at 23 fps with zero drops, 2026-08-12.
ck("V27 a step is followed by a wait for the buffer to turn over",
   "_uplink_step_at" in _tx6 and "UPLINK_SETTLE_S" in _tx6, None)
ck("V27 and the wait is seconds, not one poll",
   fnav.Call.UPLINK_SETTLE_S >= 4.0, fnav.Call.UPLINK_SETTLE_S)

# where it lands on a real link, from the ladder's own bitrates
_c7 = fnav.Call.__new__(fnav.Call)
_c7.aspect = fnav.DEFAULT_ASPECT
_lad7 = fnav.geometry_ladder(fnav.DEFAULT_ASPECT)


def _stops_at(link_bps):
    for g in _lad7:
        free = max(0.0, 1.0 - _c7._bitrate_for(g["w"], g["h"]) / float(link_bps))
        if free >= fnav.Call.UPLINK_TIGHT:
            return (g["w"], g["h"])
    return _lad7[-1]["w"], _lad7[-1]["h"]


ck("V27 a 1 Mbps uplink stops well above the floor",
   _stops_at(1_000_000)[0] >= 640, _stops_at(1_000_000))
ck("V27 a 5 Mbps uplink barely steps at all",
   _stops_at(5_000_000)[0] >= 1280, _stops_at(5_000_000))
ck("V27 and a genuinely tiny one still reaches the floor",
   _stops_at(70_000)[0] <= 320, _stops_at(70_000))

_L6 = [{"w": 1920, "h": 1080}, {"w": 1280, "h": 720}, {"w": 854, "h": 480},
       {"w": 640, "h": 360}, {"w": 480, "h": 360}, {"w": 320, "h": 240},
       {"w": 160, "h": 120}]
_D6 = __import__("comms_control").ControlPlane.derive_rate


def _spin(capped, n=4, start=(854, 480)):
    w, h = start
    out = []
    for _ in range(n):
        st = {"senders": [{"user": "Dave", "w": w, "h": h, "fps": 24.0,
                           "capped": capped}], "getters": []}
        r = _D6(st, _L6)
        w, h = r["w"], r["h"]
        out.append((w, h))
    return out


ck("V26 a capped sender holds where its uplink put it",
   len(set(_spin(True))) == 1, _spin(True))
ck("V26 and is not climbed on the grounds that nobody complained",
   _spin(True)[-1] == (854, 480), _spin(True)[-1])
ck("V26 an uncapped sender alone is still free to climb",
   _spin(False)[-1][0] > 854, _spin(False))

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
