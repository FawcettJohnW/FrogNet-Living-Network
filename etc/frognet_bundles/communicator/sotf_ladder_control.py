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
sotf_ladder_control.py - the SotF degrade/recover controller, codex-side.

This is decision logic only. It consumes telemetry and emits a Decision; it does
NOT touch sockets, encoders, or level_idx itself. The media codex feeds it samples
and APPLIES what it returns:
  - TUNE     -> adjust an in-rung encoder parameter (cheap, reversible)
  - DOWNGRADE-> write a lower level_idx into the resident envelope (the far side
                reads it and adapts decode; no handshake - memory, not messages)
  - UPGRADE  -> write a higher level_idx after sustained health
  - HOLD     -> do nothing

Why telemetry, not bandwidth probing: ffmpeg reports frames dropped at encode and
the decoder reports how far behind real-time it is; the media socket reports its
EWOULDBLOCK drop rate (non-blocking, drop-don't-stall). Together those three say
"this rung is too high" earlier and more precisely than any link estimator, and the
GROWING lag - not its instantaneous value - is the unambiguous downgrade trigger,
because no in-rung tweak recovers a backlog that is still growing.

The discipline is the same one that stabilized the route-metric winners: react to a
sustained trend over a window, not a single noisy sample, and cap how often a step
may fire so one bad half-second cannot cascade to the floor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from enum import Enum
from typing import Deque, List, Optional, Tuple


# -- The ladder --------------------------------------------------------------
# level_idx is the one mutable RESIDENT_ONCE envelope field. Higher = richer.
# Each rung declares whether it carries video/audio and a coarse target so the
# codex knows what to emit; the in-rung tunables live in TUNABLES.
@dataclass(frozen=True)
class Level:
    idx: int
    name: str
    video: bool
    audio: bool
    note: str


LEVELS: Tuple[Level, ...] = (
    Level(0, "beacon",        False, False, "REQ_REPEAT 'I am here, unchanged' - the floor"),
    Level(1, "text",          False, False, "text status only"),
    Level(2, "audio_dtx",     False, True,  "DTX silence-gated voice"),
    Level(3, "audio_active",  False, True,  "full active voice"),
    Level(4, "video_delta",   True,  True,  "voice + delta video"),
    Level(5, "video_keyframe",True,  True,  "voice + periodic keyframes"),
    Level(6, "video_full",    True,  True,  "full-rate video + voice"),
)
MIN_LEVEL = LEVELS[0].idx
MAX_LEVEL = LEVELS[-1].idx

# In-rung knobs the controller will pull BEFORE stepping the rung. Ordered cheapest
# (most reversible) first. The codex maps these names to real ffmpeg/Opus params.
TUNABLES: Tuple[str, ...] = ("video_bitrate_down", "video_fps_down", "video_q_up")


class Action(Enum):
    HOLD = "hold"
    TUNE = "tune"
    DOWNGRADE = "downgrade"
    UPGRADE = "upgrade"


@dataclass
class Decision:
    action: Action
    level_idx: int                      # the level to be at after this decision
    tunable: Optional[str] = None       # set when action == TUNE
    reason: str = ""


@dataclass
class TelemetrySample:
    """One observation. The codex fills this each tick from the real sources.
      t              monotonic seconds
      encode_drops   ffmpeg frames dropped since the last sample (count)
      decode_lag_ms  how far behind real-time the far decoder reports being
      wire_drop_rate fraction of frames the non-blocking socket shed on EWOULDBLOCK
                     this tick (0.0-1.0)
    """
    t: float
    encode_drops: int = 0
    decode_lag_ms: float = 0.0
    wire_drop_rate: float = 0.0


@dataclass
class LadderControllerConfig:
    window_sec: float = 3.0             # trend window - judge sustained, not instant
    min_step_interval_sec: float = 2.0  # step-rate cap (down or in-rung tune)
    recover_hold_sec: float = 8.0       # sustained health required before an upgrade
    upgrade_cooldown_sec: float = 6.0   # don't upgrade right after a downgrade
    # "degrading" thresholds (any one, sustained over the window):
    lag_growing_ms_per_sec: float = 40.0   # decode lag rising faster than this
    lag_abs_ms: float = 250.0              # or absolute lag above this
    wire_drop_high: float = 0.15           # or shedding >15% of frames on the wire
    # "healthy" thresholds (all, sustained over recover_hold_sec):
    lag_ok_ms: float = 60.0
    wire_drop_ok: float = 0.02


class LadderController:
    def __init__(self, start_level: int = 4, config: Optional[LadderControllerConfig] = None):
        self.cfg = config or LadderControllerConfig()
        self.level = int(start_level)
        self._win: Deque[TelemetrySample] = deque()
        self._last_step_t: float = -1e9
        self._last_downgrade_t: float = -1e9
        self._healthy_since: Optional[float] = None
        self._tune_used: List[str] = []     # in-rung knobs already pulled at this level

    # -- public: feed one sample, get a Decision ------------------------------
    def observe(self, s: TelemetrySample) -> Decision:
        self._win.append(s)
        cutoff = s.t - self.cfg.window_sec
        while len(self._win) > 1 and self._win[0].t < cutoff:
            self._win.popleft()

        degrading, why = self._degrading()
        if degrading:
            self._healthy_since = None
            return self._respond_to_degradation(s, why)

        # not degrading - track health for possible recovery
        if self._healthy_now():
            if self._healthy_since is None:
                self._healthy_since = s.t
            return self._maybe_upgrade(s)
        else:
            # neither degrading nor clearly healthy: steady state, reset health timer
            self._healthy_since = None
            return Decision(Action.HOLD, self.level, reason="steady")

    # -- degradation response: tune within rung, then downgrade ---------------
    def _respond_to_degradation(self, s: TelemetrySample, why: str) -> Decision:
        if (s.t - self._last_step_t) < self.cfg.min_step_interval_sec:
            return Decision(Action.HOLD, self.level, reason=f"degrading({why}); step-rate capped")

        lag_growing = self._lag_slope() > self.cfg.lag_growing_ms_per_sec
        knob = self._next_tunable()

        # Stage 1: if there is an in-rung knob left AND the backlog isn't runaway,
        # pull the cheap lever first.
        if knob is not None and not lag_growing:
            self._tune_used.append(knob)
            self._last_step_t = s.t
            return Decision(Action.TUNE, self.level, tunable=knob,
                            reason=f"degrading({why}); in-rung tune before stepping")

        # Stage 2: no headroom left, or lag still growing -> step the rung down.
        if self.level > MIN_LEVEL:
            self.level -= 1
            self._last_step_t = s.t
            self._last_downgrade_t = s.t
            self._tune_used.clear()         # fresh in-rung headroom at the new level
            return Decision(Action.DOWNGRADE, self.level,
                            reason=f"degrading({why}); "
                                   f"{'lag still growing' if lag_growing else 'in-rung exhausted'}")
        return Decision(Action.HOLD, self.level, reason=f"degrading({why}); already at floor")

    # -- recovery: only after sustained health and a cooldown -----------------
    def _maybe_upgrade(self, s: TelemetrySample) -> Decision:
        if self.level >= MAX_LEVEL:
            return Decision(Action.HOLD, self.level, reason="healthy; already at top")
        if (s.t - self._last_downgrade_t) < self.cfg.upgrade_cooldown_sec:
            return Decision(Action.HOLD, self.level, reason="healthy; upgrade cooldown")
        if self._healthy_since is None or (s.t - self._healthy_since) < self.cfg.recover_hold_sec:
            return Decision(Action.HOLD, self.level, reason="healthy; building confidence")
        if (s.t - self._last_step_t) < self.cfg.min_step_interval_sec:
            return Decision(Action.HOLD, self.level, reason="healthy; step-rate capped")
        self.level += 1
        self._last_step_t = s.t
        self._healthy_since = s.t          # require a fresh hold before the next step up
        self._tune_used.clear()
        return Decision(Action.UPGRADE, self.level, reason="sustained health; stepping up")

    # -- signal helpers -------------------------------------------------------
    def _degrading(self) -> Tuple[bool, str]:
        if not self._win:
            return False, ""
        last = self._win[-1]
        if last.decode_lag_ms > self.cfg.lag_abs_ms:
            return True, "lag-abs"
        if self._lag_slope() > self.cfg.lag_growing_ms_per_sec:
            return True, "lag-growing"
        if self._mean_wire_drop() > self.cfg.wire_drop_high:
            return True, "wire-drop"
        return False, ""

    def _healthy_now(self) -> bool:
        if not self._win:
            return False
        return (self._win[-1].decode_lag_ms <= self.cfg.lag_ok_ms
                and self._mean_wire_drop() <= self.cfg.wire_drop_ok
                and self._lag_slope() <= 0.0)

    def _lag_slope(self) -> float:
        """ms of decode lag gained per second across the window (least-effort 2-point)."""
        if len(self._win) < 2:
            return 0.0
        a, b = self._win[0], self._win[-1]
        dt = b.t - a.t
        if dt <= 0:
            return 0.0
        return (b.decode_lag_ms - a.decode_lag_ms) / dt

    def _mean_wire_drop(self) -> float:
        if not self._win:
            return 0.0
        return sum(s.wire_drop_rate for s in self._win) / len(self._win)

    def _next_tunable(self) -> Optional[str]:
        for k in TUNABLES:
            if k not in self._tune_used:
                return k
        return None

    # introspection for tests / the monitor
    @property
    def level_name(self) -> str:
        return LEVELS[self.level].name
