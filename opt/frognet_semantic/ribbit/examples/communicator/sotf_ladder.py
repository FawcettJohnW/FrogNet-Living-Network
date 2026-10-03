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
sotf_ladder.py -- the SotF-ACP degradation ladder, as policy (client-side, no core).

Canonical ladder (protocol constant). L8 is the richest rung, L0 the floor; the
controller degrades DOWN as the NETWORK's total throughput falls. Ordering is by
TOTAL wire cost of the rung (not any single track), so every step down sheds load:

  L8 BULLFROG  audio + 1080p video
  L7 CHORUS    audio + 720p video
  L6 ENSEMBLE  audio + 480p video
  L5 DUET      audio + 360p grayscale video
  L4 SOLO      audio only -- video shed
  L3 VOICE     audio only, reduced
  L2 WHISPER   plaintext text on the wire
  L1 BEACON    token vocabulary (contested-link floor)
  L0 PULSE     implicit presence

  The rungs name no channel count, because the ladder serves two applications
  with different audio formats and both are correct:

      fnphone           16 kHz mono   (fnphone_pa.py)  -- the FNWP voice phone
      Communicator A/V  48 kHz stereo (media_codex.py) -- the A/V call

  A rung is a promise about what tracks are carried and at what video size, not
  about sample rate or channels. The format belongs to the codex the call was
  opened with. Do not write a channel count into these names.

[STANDARD_SIZES_WIDESCREEN_V1] Every video rung is a named standard resolution
in 16:9 -- 1080p, 720p, 480p, 360p -- and the bottom walk continues in the same
aspect (240p, 144p, 160x90). A rung in a different aspect changes the SHAPE of the frame
as the ladder walks, which reads as a glitch rather than as degradation. 640x480
gets no rung: VGA is the 4:3 spelling of two rungs that already exist.

Invariant: total cost is monotonically non-increasing from L8 to L0. When the
network degrades, drop to the next-lower rung to reduce load; the controller
always sends the HIGHEST rung the current total throughput can sustain -- "the
highest level we are capable of" under the load ceiling.

Two things choose the rung:

  CEILING -- the highest rung this machine may source, from capability + flags.
    [VIDEO_DOES_NOT_NEED_A_MIC_V1] A video rung (L5-L8) needs a camera and ONLY
    a camera; needs_mic is False on every one of them. Audio rungs (L3-L4) need
    a mic. A camera-only machine sends video with no audio bed -- a stream with
    a note on it, not a disqualification -- and the receiver is told there is no
    audio and plays what there is. Gating video on a mic made a camera-only box
    compute a text-only ceiling: a working webcam produced text presence. So:
      camera + mic            -> L8 BULLFROG
      camera, no mic          -> L8 BULLFROG, video only
      --no-video / no camera  -> L4 SOLO    (top audio-only rung)
      no camera and no mic    -> L2 WHISPER
      both off                -> L2 WHISPER
    Text/beacon/pulse (L0-L2) need no media hardware.

  BEARER -- total network load pushes the rung DOWN (toward L0). The sender never
  rises above its ceiling and never above what the bearer sustains.

  send_level = highest ALLOWED rung that is no higher than the ceiling and no
  higher than the bearer permits. Walks DOWN to the next allowed rung; L0/L1 are
  always allowed, so a call never dies -- the medium changes instead.
"""
from __future__ import annotations

from typing import Set

# kinds, by what the rung carries
VIDEO_AUDIO, AUDIO, TEXT, PRESENCE = "video+audio", "audio", "text", "presence"

# idx, code, name, kind, and what the sender must have to SOURCE it.
# Higher idx = richer. needs_camera/needs_mic gate whether this machine can source it.
LEVELS = [
    {"idx": 0, "code": "L0", "name": "PULSE",    "kind": PRESENCE,    "needs_camera": False, "needs_mic": False},
    {"idx": 1, "code": "L1", "name": "BEACON",   "kind": TEXT,        "needs_camera": False, "needs_mic": False},
    {"idx": 2, "code": "L2", "name": "WHISPER",  "kind": TEXT,        "needs_camera": False, "needs_mic": False},
    {"idx": 3, "code": "L3", "name": "VOICE",    "kind": AUDIO,       "needs_camera": False, "needs_mic": True},
    {"idx": 4, "code": "L4", "name": "SOLO",     "kind": AUDIO,       "needs_camera": False, "needs_mic": True},
    # [VIDEO_DOES_NOT_NEED_A_MIC_V1] A video rung needs a CAMERA. It does not need a
    # microphone. A machine with a camera and no mic sends video with no audio bed --
    # that is a stream with a note on it, not a disqualification. The receiver is told
    # there is no audio and plays what there is.
    #
    # These were needs_mic=True, so a camera-only box computed allowed={L0,L1,L2} and
    # could not source video AT ALL: a working webcam produced text presence.
    {"idx": 5, "code": "L5", "name": "DUET",     "kind": VIDEO_AUDIO, "needs_camera": True,  "needs_mic": False},
    {"idx": 6, "code": "L6", "name": "ENSEMBLE", "kind": VIDEO_AUDIO, "needs_camera": True,  "needs_mic": False},
    {"idx": 7, "code": "L7", "name": "CHORUS",   "kind": VIDEO_AUDIO, "needs_camera": True,  "needs_mic": False},
    # [BULLFROG_V1] Added 2026-08-10. A rung above CHORUS for links that can
    # carry it -- and nothing decides whether they can except sending frames and
    # watching, same as every other rung.
    #
    # The index is NEW at the TOP, so no existing index changes meaning: a frame
    # labelled L7 still means what it meant. The number does travel on the wire
    # in pack_video(), and a peer built before this will not know what L8 is --
    # on receive the index is used for REPORTING only (the decoder reads
    # geometry from the bitstream), so an old peer mislabels the rung and
    # decodes the picture correctly.
    {"idx": 8, "code": "L8", "name": "BULLFROG", "kind": VIDEO_AUDIO, "needs_camera": True,  "needs_mic": False},
]
BY_IDX = {lv["idx"]: lv for lv in LEVELS}
MIN_IDX, MAX_IDX = 0, 8        # higher = richer
ALWAYS = {0, 1}                # PULSE/BEACON floor -- never removed


def code(idx: int) -> str:
    return BY_IDX[idx]["code"]


def name(idx: int) -> str:
    return BY_IDX[idx]["name"]


def kind_of(idx: int) -> str:
    return BY_IDX[idx]["kind"]


def allowed_levels(have_camera: bool, have_mic: bool,
                   no_video: bool = False, no_audio: bool = False) -> Set[int]:
    """Rungs this sender may source.

    [VIDEO_DOES_NOT_NEED_A_MIC_V1] A video rung (L5-L7) needs a CAMERA. An audio rung
    (L3-L4) needs a MIC. The two are independent: no mic means the video rungs carry
    no audio bed, which is noted on the stream, not a reason to withhold the picture.

    --no-video forbids video rungs. --no-audio forbids the audio rungs and silences
    the bed on the video rungs; it does NOT forbid video.

    PULSE/BEACON/WHISPER are always available."""
    cam_ok = have_camera and not no_video
    mic_ok = have_mic and not no_audio
    # no_audio no longer subtracts video: it silences the bed, it does not
    # withdraw the picture.
    allowed: Set[int] = set(ALWAYS)
    for lv in LEVELS:
        if lv["needs_camera"] and not cam_ok:
            continue
        if lv["needs_mic"] and not mic_ok:
            continue
        allowed.add(lv["idx"])
    return allowed


def ceiling(have_camera: bool, have_mic: bool,
            no_video: bool = False, no_audio: bool = False) -> int:
    """Highest rung the sender may source = 'switch to the highest supported level'.
    Just the max allowed idx."""
    return max(allowed_levels(have_camera, have_mic, no_video, no_audio))


def send_level(ceiling_idx: int, bearer_idx: int, allowed: Set[int]) -> int:
    """The rung to send now: no higher than the ceiling, no higher than the bearer
    sustains, and must be allowed. Walk DOWN to the next allowed rung; L0 always
    satisfies."""
    want = min(ceiling_idx, bearer_idx)            # highest we're permitted
    for idx in range(want, MIN_IDX - 1, -1):       # walk down to next allowed
        if idx in allowed:
            return idx
    return MIN_IDX


def describe_ceiling(have_camera: bool, have_mic: bool,
                     no_video: bool = False, no_audio: bool = False) -> str:
    c = ceiling(have_camera, have_mic, no_video, no_audio)
    return f"{BY_IDX[c]['code']} {BY_IDX[c]['name']} ({kind_of(c)})"


if __name__ == "__main__":
    cases = [
        ("camera+mic, no flags",   True,  True,  False, False),
        ("--no-video",             True,  True,  True,  False),
        ("--no-audio",             True,  True,  False, True),
        ("--no-video --no-audio",  True,  True,  True,  True),
        ("no camera (mic only)",   False, True,  False, False),
        ("no camera, no mic",      False, False, False, False),
    ]
    print("CEILING (highest level the sender switches to):")
    for label, cam, mic, nv, na in cases:
        print(f"  {label:24} -> {describe_ceiling(cam, mic, nv, na)}")
    print("\nDEGRADE DOWN as total network load rises (full-capability sender):")
    allowed = allowed_levels(True, True)
    ce = ceiling(True, True)
    for bearer in range(MAX_IDX, MIN_IDX - 1, -1):
        lv = send_level(ce, bearer, allowed)
        print(f"  bearer sustains L{bearer} -> send {code(lv)} {name(lv)}")
