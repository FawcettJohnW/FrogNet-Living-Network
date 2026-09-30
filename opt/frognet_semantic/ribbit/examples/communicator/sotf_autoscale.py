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
sotf_autoscale.py - the closed control loop: backlog -> ffmpeg options, both directions.

A consumer (or the server) that observes congestion translates it into ffmpeg options
and writes them into the PRODUCER's control tuple + trips the producer's flag. The
producer reads the flag JIT every loop and, on trip, applies. Nobody messages anyone;
the desired encoder state simply IS in the shared space.

The signal is the media vector's backlog DEPTH (queued frames not yet on the wire):
  depth rising past HIGH  -> link can't keep up -> step DOWN a rung (lower bitrate)
  depth falling below LOW -> link has headroom  -> step UP a rung  (higher bitrate)
Hysteresis (HIGH != LOW) stops rung flapping. The rungs are a fixed ladder of real
ffmpeg option bags. This is the thing that makes "scale to fit" automatic.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

# Real ffmpeg option ladder, worst (0) -> best (N). Each is a full bag the producer runs.
RUNGS: List[Dict[str, Any]] = [
    {"bitrate_kbps": 150, "cpu_used": 8, "g": 30, "scale": "160x120"},
    {"bitrate_kbps": 300, "cpu_used": 8, "g": 30, "scale": "320x240"},
    {"bitrate_kbps": 600, "cpu_used": 8, "g": 30, "scale": "320x240"},
    {"bitrate_kbps": 1200, "cpu_used": 6, "g": 30, "scale": "640x480"},
    {"bitrate_kbps": 2500, "cpu_used": 4, "g": 30, "scale": "640x480"},
]


class AutoScaler:
    """Observes a depth source and drives a producer's rung via the codex control plane.
    Lives on the OBSERVER side (consumer/server). It calls set_ffmpeg_options(for_who=
    producer) - the producer never knows the scaler exists."""
    def __init__(self, codex, producer_who: str,
                 depth_fn, maxq: int,
                 start_rung: int = 2,
                 high_frac: float = 0.50, low_frac: float = 0.10):
        self.codex = codex
        self.producer = producer_who
        self.depth_fn = depth_fn                    # callable -> current backlog depth
        self.maxq = max(1, maxq)
        self.rung = start_rung
        self.high = high_frac
        self.low = low_frac
        self.history: List[Dict[str, Any]] = []
        # publish the producer's starting rung once
        self.codex.set_ffmpeg_options(dict(RUNGS[self.rung]), for_who=self.producer)

    def tick(self) -> Optional[str]:
        """Read backlog; step the rung if past a threshold; on change, write the new
        options + trip the producer's flag. Returns 'down'/'up'/None."""
        depth = self.depth_fn()
        frac = depth / self.maxq
        moved = None
        if frac >= self.high and self.rung > 0:
            self.rung -= 1; moved = "down"
        elif frac <= self.low and self.rung < len(RUNGS) - 1:
            self.rung += 1; moved = "up"
        if moved:
            self.codex.set_ffmpeg_options(dict(RUNGS[self.rung]), for_who=self.producer)
        self.history.append({"depth": depth, "frac": round(frac, 3),
                             "rung": self.rung, "moved": moved})
        return moved
