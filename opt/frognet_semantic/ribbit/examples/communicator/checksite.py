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
"""checksite.py -- prove this extracted bundle is the adaptive one.

Deploy discipline: verify by extracting back out and RUNNING, not by trusting
the archive. Run this from the bundle root after extracting:

    cd /etc/frognet_bundles/communicator && python3 checksite.py

Needs core on the path for the frognet_tuples shim:

    PYTHONPATH=/opt/frognet_semantic python3 checksite.py

Exits non-zero on the first failure. No network, no camera, no libav required --
the oracles stub what they need.
"""
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Every file the adaptive media path needs, present and importable.
REQUIRED = (
    "fnav.py",
    "communicator_live.py",
    "comms_control.py",
    "comms_ui.py",
    "sotf_ladder.py",
    "DOCTRINE.txt",
)

# The oracles. Each one is red on the pre-adaptive tree by construction.
ORACLES = (
    "test_video_floor_oracle.py",
    "test_rung_measured_oracle.py",
    "test_window_rung_oracle.py",
    "test_shed_signal_oracle.py",
    "test_names_oracle.py",
    "test_bottom_rung_oracle.py",
    "test_bullfrog_oracle.py",
    "test_viewer_commands_oracle.py",
    "test_fan_session_oracle.py",
    "sim_tuple_roundtrip.py",
    "sim_calls.py",
    "sim_converge.py",
    "sim_whole_call.py",
    "test_lobby_oracle.py",
    "test_five_tuples_oracle.py",
    "test_shim_surface_oracle.py",
    "test_mic_vs_link_oracle.py",
    "test_clean_hangup_oracle.py",
    "test_drop_frame_oracle.py",
    "test_rate_horizon_oracle.py",
    "test_no_burst_oracle.py",
)

# Markers that must exist in the source. A bundle that carries the files but
# not these is an older tree with the right names.
MARKERS = (
    ("fnav.py", "SETTLE_BEFORE_YOU_CLIMB_V1"),

    ("comms_control.py", "A_CEILING_MUST_BE_ABLE_TO_LIFT_V1"),

    ("fnav.py", "ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1"),

    ("fnav.py", "THE_TWO_NUMBERS_ARE_NOT_THE_SAME_UNITS_V1"),

    ("fnav.py", "A_SHRINKING_READ_IS_NOT_NEWS_V1"),

    ("fnav.py", "SAY_WHICH_GATE_STOPPED_THE_REPORT_V1"),

    ("fnav.py", "A_NEW_SIZE_NEEDS_A_NEW_KEYFRAME_V1"),

    ("comms_control.py", "A_SENDER_BOUND_ALONE_CANNOT_CLIMB_V1"),

    ("fnav.py", "THE_BITRATE_BELONGS_TO_THE_PICTURE_V1"),

    ("fnav.py", "THE_GUARD_MUST_WORK_WHERE_THE_CLIENT_RUNS_V1"),

    ("fnav.py", "THE_BUFFER_IS_THE_LATENCY_V1"),

    ("fnav.py", "SHRINKING_A_BLOCKED_SOCKET_IS_NOT_A_FIX_V1"),

    ("fnav.py", "THE_SOCKET_KNOWS_BEFORE_THE_DROP_V1"),

    ("comms_control.py", "A_MEMBER_IS_PROOF_THE_CALL_EXISTS_V1"),

    ("comms_control.py", "A_RATE_THAT_FAILED_IS_NOT_A_CANDIDATE_V1"),

    ("fnav.py", "ONE_DERIVATION_V1"),

    ("comms_control.py", "ONE_DERIVATION_V1"),

    ("comms_control.py", "A_REPORT_IS_ABOUT_A_SIZE_V1"),

    ("fnav.py", "THE_RATE_IS_A_FUNCTION_OF_THE_ROWS_V1"),

    ("comms_control.py", "THE_RATE_IS_A_FUNCTION_OF_THE_ROWS_V1"),

    ("fnav.py", "SEND_NO_FASTER_THAN_THE_SLOWEST_SENDER_V1"),

    ("comms_control.py", "SEND_NO_FASTER_THAN_THE_SLOWEST_SENDER_V1"),

    ("fnav.py", "ONE_STEP_PER_SIZE_V1"),

    ("fnav.py", "WALK_THE_KEYFRAME_DOWN_UNTIL_IT_FITS_V1"),

    ("fnav.py", "A_HOLD_IS_NOT_FOREVER_V1"),

    ("fnav.py", "ONE_STEP_PER_HOLD_V1"),

    ("fnav.py", "A_PRODUCER_S_RATE_BOUNDS_THE_CALL_V1"),

    ("fnav.py", "READ_A_COUNTER_NOBODY_ELSE_DRAINS_V1"),

    ("fnav.py", "REFUSED_AND_TIMED_OUT_ARE_DIFFERENT_FAULTS_V1"),

    ("fnav.py", "ASK_THE_CAMERA_FOR_MJPEG_V1"),

    ("fnav.py", "SHRINKING_ONLY_HELPS_IF_THE_WIRE_IS_THE_LIMIT_V1"),

    ("fnav.py", "DO_NOT_COMMIT_TO_A_FRAME_THAT_WILL_NOT_FIT_V1"),

    ("fnav.py", "A_FAILED_READ_IS_NOT_NOTHING_V1"),

    ("fnav.py", "NO_CATCH_UP_BURST_V1"),

    ("fnav.py", "SAY_WHY_IT_ABORTED_V1"),

    ("fnav.py", "MEASURE_LONG_ENOUGH_TO_BE_A_MEASUREMENT_V1"),

    ("comms_control.py", "NO_SYNC_JUST_STATE_V1"),

    ("fnav.py", "NO_SYNC_JUST_STATE_V1"),

    ("fnav.py", "DROP_THE_FRAME_NOT_THE_CALL_V1"),

    ("fnav.py", "HANG_UP_IS_NOT_A_FAULT_V1"),

    ("fnav.py", "SAY_WHAT_YOU_ARE_SENDING_UNCONDITIONALLY_V1"),

    ("fnav.py", "NOT_YET_STABLE_IS_NOT_OVERLOADED_V1"),

    ("fnav.py", "GRAY_FOLLOWS_THE_PICTURE_V1"),

    ("fnav.py", "THE_MIC_IS_NOT_THE_LINK_V1"),

    ("frognet_tuples.py", "A_SHIM_MUST_BE_USABLE_BEFORE_IT_FINISHES_V1"),

    ("comms_control.py", "PRODUCER_LEADS_CONSUMERS_REPORT_V1"),

    ("fnav.py", "THE_FLAG_MEANS_STABLE_V1"),

    ("fnav.py", "ONE_RATE_FOR_THE_NETWORK_V1"),

    ("fnav.py", "PRODUCER_LEADS_CONSUMERS_REPORT_V1"),

    ("fnav.py", "VIDEO_FLOOR_IS_MEASURED_V1"),
    ("fnav.py", "FLOOR_IS_PROVEN_NOT_GLIMPSED_V1"),
    ("fnav.py", "RUNG_IS_MEASURED_V1"),
    ("fnav.py", "GEOMETRY_IS_NOT_A_RUNG_V1"),
    ("fnav.py", "PROBE_IS_GRADED_ON_FRAMES_V1"),
    ("fnav.py", "WINDOW_KNOWS_ITS_RUNG_V1"),
    ("communicator_live.py", "WINDOW_KNOWS_ITS_RUNG_V1"),
    ("fnav.py", "SHED_IS_READ_FROM_THE_QUEUE_THAT_SHED_IT_V1"),
    ("fnav.py", "take_video_sheds"),
    ("fnav.py", "BOTTOM_RUNG_SHRINKS_V1"),
    ("sotf_ladder.py", "BULLFROG"),
    ("fnav.py", "ASPECT_IS_CHOSEN_V1"),
    ("fnav.py", "CONSTRAINED_GOES_4_3_V1"),
    ("fnav.py", "GROWTH_IS_EARNED_V1"),
    ("fnav.py", "VBV_OR_THE_BUDGET_IS_A_WISH_V1"),
    ("fnav.py", "SLOWEST_VIEWER_COMMANDS_V1"),
    ("comms_control.py", "SLOWEST_VIEWER_COMMANDS_V1"),
    ("fnav.py", "FAN_IS_PER_SESSION_V1"),
    ("fnav.py", "ALONE_IS_NOT_SILENT_V1"),
    ("communicator_live.py", "SAY_WHICH_BYTES_ARE_RUNNING_V1"),
    ("comms_control.py", "FOUR_TUPLES_V1"),
    ("communicator_live.py", "AN_ANNOUNCED_CALL_IS_SOMEWHERE_TO_GO_V1"),
    ("comms_control.py", "ANNOUNCE_IS_THE_HOSTS_ROW_V1"),
    ("comms_control.py", "MEDIACONTROL_BELONGS_TO_THE_CALL_V1"),
    ("fnav.py", "MEDIACONTROL_BELONGS_TO_THE_CALL_V1"),
    ("fnav.py", "SMALLEST_WIRE_WINS_V1"),
    ("communicator_live.py", "TILE_TAKES_WHAT_IT_IS_GIVEN_V1"),
)

fails = []


def ck(name, ok, detail=""):
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          "" if ok else "   -- %s" % detail))
    if not ok:
        fails.append(name)


print("checksite: %s" % HERE)

# 1. no bytecode shipped
stale = []
for root, dirs, files in os.walk(HERE):
    if "__pycache__" in dirs:
        stale.append(os.path.join(root, "__pycache__"))
    stale += [os.path.join(root, f) for f in files if f.endswith((".pyc", ".pyo"))]
ck("no bytecode in the bundle", not stale, "%d found" % len(stale))

# 2. files present
for f in REQUIRED + ORACLES:
    ck("present: %s" % f, os.path.isfile(os.path.join(HERE, f)))

# 3. markers present
for f, marker in MARKERS:
    p = os.path.join(HERE, f)
    try:
        ok = marker in open(p, encoding="utf-8", errors="replace").read()
    except OSError as e:
        ok = False
    ck("%s carries [%s]" % (f, marker), ok, "marker absent")

# 4. everything parses. compile() in memory rather than py_compile: a bundle
# that must ship no bytecode should not have its own checker emit any, and
# py_compile(cfile=os.devnull) refuses on a non-regular file.
for f in REQUIRED:
    if not f.endswith(".py"):
        continue
    p = os.path.join(HERE, f)
    try:
        compile(open(p, encoding="utf-8").read(), p, "exec")
        ck("compiles: %s" % f, True)
    except Exception as e:
        ck("compiles: %s" % f, False, "%s: %s" % (type(e).__name__, e))

# 5. the oracles actually pass, here, now
env = dict(os.environ)
env["PYTHONPATH"] = os.pathsep.join(
    [p for p in (HERE, "/opt/frognet_semantic", env.get("PYTHONPATH", "")) if p])
for t in ORACLES:
    p = os.path.join(HERE, t)
    if not os.path.isfile(p):
        continue
    r = subprocess.run([sys.executable, p], cwd=HERE, env=env,
                       capture_output=True, text=True)
    tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
    ck("oracle: %s" % t, r.returncode == 0, tail)

print()
if fails:
    print("checksite FAILED (%d): %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("checksite OK -- this is the adaptive communicator")
