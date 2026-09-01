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
sotf_leg_adapt.py - PER-LEG stream adaptation for the FrogNet media host.

The media host serves each consumer leg INDEPENDENTLY. A LAN consumer and a WAN
consumer in the same call each get a stream tuned to THEIR link: the host encodes
one rung per (recipient, level), and each leg's level is driven by THAT leg's own
measured bearer - not one session-wide bearer. So a congested WAN peer degrades
its own downlink (and its own uplink) without dragging the LAN peer down.

Two responsibilities, both pure (no sockets, no ffmpeg) so the simulator drives them:

  1. bearer_to_rung(bearer_idx, ceiling_idx, allowed)  -> the rung to serve THIS leg
     (reuses sotf_ladder.send_level: never above ceiling, never above what the leg's
      bearer sustains, must be an allowed rung; walks DOWN past forbidden rungs).

  2. rung_video_treatment(level_idx) -> the REAL encode treatment for that rung:
     scale, fps, grayscale, kbps. This is where "degrade to 360p grayscale 10fps as
     the last video rung before audio-only" actually lives - L5 DUET is grayscale/10fps,
     L4 SOLO and below carry NO video at all (audio-only / text / presence).

Ladder rungs (from sotf_ladder, the protocol constant):
  L7 CHORUS   richest video+audio
  L6 ENSEMBLE video+audio
  L5 DUET     360p GRAYSCALE 10fps + audio   <- the video floor / transition to audio
  L4 SOLO     audio only (video shed)
  L3 VOICE    audio
  L2 WHISPER  text
  L1 BEACON   text
  L0 PULSE    presence
"""
from typing import Dict, Optional, Set

import sotf_ladder as L


# Opus mono audio bed carried by every audio/video rung (L3-L7). 32 kbps matches the
# host encoder (-b:a 32k). Text rungs carry only sparse messages; presence is heartbeat.
_AUDIO_KBPS = 32
_TEXT_KBPS = 4           # bounded message/beacon traffic
_PRESENCE_KBPS = 1       # heartbeat only
_OVERHEAD_KBPS = 8       # framing/control headroom per leg

# Sustainability bar (John's definition): a rung is sustainable on a leg iff, over the
# observation window, audio stays CLEAN (mono is fine) AND video holds >= MIN_FPS. Below
# the video rungs the video clause drops out (audio-only / text / presence).
MIN_FPS = 10
WINDOW_S = 10


# --- per-rung VIDEO treatment -------------------------------------------------
# The encode parameters for each rung that CARRIES video (L5-L7). Rungs L0-L4 carry
# no video, so they have no treatment here (the host emits audio/text/presence only).
# L5 is the deliberate floor: small, grayscale, low fps - the last thing seen before
# video is shed entirely at L4.
#   scale  : ffmpeg scale=  (W:H or W:-2)
#   fps    : output frame rate cap for THIS rung (None = source native, only the top rung)
#   gray   : apply format=gray (true grayscale) at the encoder
#   kbps   : target video bitrate for the rung
_VIDEO_TREATMENT: Dict[int, Dict] = {
    7: {"scale": "640:-2", "fps": None, "gray": False, "kbps": 2500},  # CHORUS  native fps, full color
    6: {"scale": "480:-2", "fps": 24,   "gray": False, "kbps": 1200},  # ENSEMBLE
    5: {"scale": "360:-2", "fps": 10,   "gray": True,  "kbps": 300},   # DUET    360p GRAY 10fps (floor)
}


def carries_video(level_idx: int) -> bool:
    """True only for rungs that actually transmit a video track (L5-L7)."""
    return level_idx in _VIDEO_TREATMENT


def rung_video_treatment(level_idx: int) -> Optional[Dict]:
    """The real encode treatment (scale/fps/gray/kbps) for a video rung, or None for
    audio-only/text/presence rungs (L4 and below carry no video)."""
    t = _VIDEO_TREATMENT.get(level_idx)
    return dict(t) if t is not None else None


def video_filter(level_idx: int) -> Optional[str]:
    """The ffmpeg -vf chain for this rung's video, or None if the rung carries no video.
    Combines scale, optional grayscale, and optional fps cap into one filter string -
    exactly what the encoder applies so the rung's wire cost matches its promise."""
    t = _VIDEO_TREATMENT.get(level_idx)
    if t is None:
        return None
    parts = [f"scale={t['scale']}"]
    if t["gray"]:
        parts.append("format=gray")        # TRUE grayscale - not a label, a real shed
    if t["fps"] is not None:
        parts.append(f"fps={t['fps']}")
    return ",".join(parts)


# --- per-leg rung selection ---------------------------------------------------
def bearer_to_rung(bearer_idx: int, ceiling_idx: int, allowed: Set[int]) -> int:
    """The rung to serve ONE leg: bounded by the leg's bearer (network) and the
    source ceiling (capability), restricted to allowed rungs. Thin wrapper over the
    canonical sotf_ladder.send_level so leg selection uses the SAME rule as the sender."""
    return L.send_level(ceiling_idx, bearer_idx, allowed)


class LegController:
    """Tracks one consumer leg's adaptive state. The host owns one of these per
    (session, recipient). Its bearer moves DOWN on congestion and UP on sustained
    health, INDEPENDENT of every other leg - this is what makes LAN and WAN legs
    adapt separately. Pure state machine; the host feeds it congestion/health signals
    and reads back the rung to encode for this leg."""

    def __init__(self, recipient: str, ceiling_idx: int, allowed: Set[int],
                 start_idx: Optional[int] = None,
                 recover_after: int = 3):
        self.recipient = recipient
        self.ceiling_idx = ceiling_idx
        self.allowed = set(allowed)
        # this leg's own bearer; starts at the ceiling (best the source can do) and is
        # pushed down by THIS leg's congestion only.
        self.bearer_idx = self.ceiling_idx if start_idx is None else start_idx
        self.recover_after = recover_after      # healthy ticks before stepping back up
        self._healthy_run = 0

    def on_congestion(self) -> int:
        """This leg dropped/backed-up: step its bearer DOWN one rung (never below L0).
        Resets the recovery streak. Returns the new served rung."""
        self.bearer_idx = max(L.MIN_IDX, self.bearer_idx - 1)
        self._healthy_run = 0
        return self.served_rung()

    def on_healthy(self) -> int:
        """This leg delivered cleanly this tick. After `recover_after` consecutive
        healthy ticks, step the bearer UP one rung (never above ceiling) - cautious
        recovery so we don't oscillate. Returns the new served rung."""
        self._healthy_run += 1
        if self._healthy_run >= self.recover_after and self.bearer_idx < self.ceiling_idx:
            self.bearer_idx += 1
            self._healthy_run = 0
        return self.served_rung()

    def served_rung(self) -> int:
        """The rung the host should ENCODE for this leg right now."""
        return bearer_to_rung(self.bearer_idx, self.ceiling_idx, self.allowed)


# --- per-rung wire cost + delivered quality -----------------------------------
def rung_required_kbps(level_idx: int) -> int:
    """Total wire rate a rung needs on a leg (video + audio bed + overhead). Monotonically
    non-increasing L7->L0, so every step down sheds load. Streaming framing/control overhead
    applies only to CONTINUOUS media rungs (video/audio); text is sparse messages and presence
    is a bare heartbeat, so those carry minimal overhead and stay reachable on a near-dead
    link. Used to decide whether a leg's bandwidth can sustain the rung."""
    kind = L.kind_of(level_idx)
    if kind == L.VIDEO_AUDIO:
        t = _VIDEO_TREATMENT[level_idx]
        return t["kbps"] + _AUDIO_KBPS + _OVERHEAD_KBPS
    if kind == L.AUDIO:
        return _AUDIO_KBPS + _OVERHEAD_KBPS
    if kind == L.TEXT:
        return _TEXT_KBPS                            # sparse messages, no streaming overhead
    return _PRESENCE_KBPS                            # PRESENCE - bare heartbeat


def rung_delivers(level_idx: int) -> Dict:
    """What a rung delivers when it fits: audio cleanliness (mono ok) and video fps.
    video_fps is None for rungs that carry no video (audio/text/presence)."""
    kind = L.kind_of(level_idx)
    if kind == L.VIDEO_AUDIO:
        t = _VIDEO_TREATMENT[level_idx]
        # native-fps top rung delivers well above the floor; capped rungs deliver their cap
        fps = t["fps"] if t["fps"] is not None else 30
        return {"audio": True, "audio_mono_ok": True, "video_fps": fps}
    if kind == L.AUDIO:
        return {"audio": True, "audio_mono_ok": True, "video_fps": None}
    # text / presence: no audio track, no video; "delivery" = messages/heartbeat get through
    return {"audio": False, "audio_mono_ok": True, "video_fps": None}


def is_sustainable(level_idx: int, leg_kbps: float) -> bool:
    """Is this rung sustainable on a leg with `leg_kbps` of usable bandwidth, by John's
    bar: the rung's required rate fits the link AND (if it carries video) it holds >= MIN_FPS
    AND (if it carries audio) audio is clean. For text/presence the bar is just that the rung
    fits - messages/heartbeat get through."""
    if rung_required_kbps(level_idx) > leg_kbps:
        return False
    d = rung_delivers(level_idx)
    if d["video_fps"] is not None and d["video_fps"] < MIN_FPS:
        return False
    return True


def sustainable_rung(leg_kbps: float, ceiling_idx: int, allowed: Set[int]) -> int:
    """The HIGHEST allowed rung (no higher than ceiling) sustainable on a leg with `leg_kbps`
    bandwidth. Walks DOWN from the ceiling: video rungs, then audio, then text, then presence
    - the degrade path, ending at text mode when nothing richer fits, presence (heartbeat) as
    the last reachable rung. Returns PULSE as the floor; callers can check is_sustainable on
    the result to know whether even the heartbeat fits a near-dead link."""
    for idx in range(min(ceiling_idx, L.MAX_IDX), L.MIN_IDX - 1, -1):
        if idx in allowed and is_sustainable(idx, leg_kbps):
            return idx
    return L.MIN_IDX        # PULSE floor - caller checks is_sustainable for a dead link
