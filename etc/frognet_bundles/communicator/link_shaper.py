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
link_shaper.py - constrain THIS communicator's own link, locally.

A single process-global LinkShaper that the media writer consults before putting a frame on
the wire. It shapes ONLY this instance's egress - other communicators on the network are
unaffected and run at full speed (or under their own shaper). This is a demo/test lever, not
a network policy: it lets one node simulate a constrained or jammed link while the rest of the
call runs normally, so you can watch per-leg adaptation kick in for real.

Two independent constraints, both off by default:
  bandwidth_kbps  - egress ceiling. Frames are PACED so the running byte-rate stays at/below
                    this cap; excess waits (the sender's bounded queue then sheds, exactly as
                    under real congestion - which is what drives the rung down).
  jam_loss        - fraction [0..1] of DROPPABLE frames to drop outright (simulated jamming).
                    Keyframes and audio (is_key=True) are PROTECTED and never dropped here, so
                    jamming degrades smoothness/quality but does not kill the call outright -
                    matching how the real droppable class behaves under loss.

The shaper reports what it did so the UI can show "link: 256 kbps, 8% jam" and the metrics
can distinguish shaper-induced shedding from genuine network backpressure.
"""
import threading
import time
import random


class LinkShaper:
    def __init__(self):
        self._lock = threading.Lock()
        self.bandwidth_kbps = None        # None = unthrottled
        self.jam_loss = 0.0               # 0 = no jamming
        # pacing state
        self._window_start = time.monotonic()
        self._window_bytes = 0
        # counters for the UI / metrics
        self.paced_delay_s = 0.0
        self.jammed_frames = 0
        self.passed_frames = 0
        self._rng = random.Random()

    # ---- configuration (called from the UI) -------------------------------
    def set_bandwidth(self, kbps):
        """Cap egress at kbps (None or 0 => unthrottled)."""
        with self._lock:
            self.bandwidth_kbps = int(kbps) if kbps else None
            self._window_start = time.monotonic()
            self._window_bytes = 0

    def set_jam(self, loss_fraction):
        """Drop this fraction of droppable frames (0..1). 0 => no jamming."""
        with self._lock:
            self.jam_loss = max(0.0, min(1.0, float(loss_fraction)))

    def clear(self):
        with self._lock:
            self.bandwidth_kbps = None
            self.jam_loss = 0.0

    def active(self) -> bool:
        return self.bandwidth_kbps is not None or self.jam_loss > 0.0

    def describe(self) -> str:
        if not self.active():
            return "link: unconstrained"
        bw = f"{self.bandwidth_kbps} kbps" if self.bandwidth_kbps else "full"
        jam = f"{self.jam_loss*100:.0f}% jam" if self.jam_loss else "no jam"
        return f"link: {bw}, {jam}"

    # ---- the writer consults these per frame ------------------------------
    def should_drop(self, frame_len: int, is_key: bool) -> bool:
        """Jamming: drop a droppable frame with probability jam_loss. Keyframes and audio
        (is_key) are protected and never dropped here."""
        with self._lock:
            jam = self.jam_loss
        if jam <= 0.0 or is_key:
            self.passed_frames += 1
            return False
        if self._rng.random() < jam:
            self.jammed_frames += 1
            return True
        self.passed_frames += 1
        return False

    def pace(self, frame_len: int) -> None:
        """Bandwidth ceiling: block just long enough that the running egress rate stays at or
        below bandwidth_kbps. Token-bucket-ish over a 1s window. No-op when unthrottled."""
        with self._lock:
            cap = self.bandwidth_kbps
        if not cap:
            return
        cap_bytes_per_s = cap * 1000 / 8.0
        now = time.monotonic()
        with self._lock:
            elapsed = now - self._window_start
            if elapsed >= 1.0:
                # new window
                self._window_start = now
                self._window_bytes = frame_len
                return
            # would this frame exceed the window's byte budget?
            budget = cap_bytes_per_s * 1.0          # bytes allowed per 1s window
            if self._window_bytes + frame_len <= budget:
                self._window_bytes += frame_len
                return
            # over budget: sleep until the window rolls over
            sleep_for = max(0.0, 1.0 - elapsed)
        if sleep_for > 0:
            time.sleep(sleep_for)
            self.paced_delay_s += sleep_for
        with self._lock:
            self._window_start = time.monotonic()
            self._window_bytes = frame_len


# process-global shaper for THIS communicator instance (other instances have their own)
_SHAPER = LinkShaper()

def shaper() -> LinkShaper:
    return _SHAPER
