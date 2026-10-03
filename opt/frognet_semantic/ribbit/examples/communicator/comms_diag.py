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
comms_diag.py -- the diagnostics window: what the bearer is doing, and why it is
taking its time about it.

The call window shows the rung you are on. This window shows the CONTROLLER: drops
arriving, the counters filling, the rung stepping, and the lag between those three.
That lag is the point. fnav.Bearer never reacts to a single sample -- it steps down
after 3 consecutive stressed samples, back up after 5 clean ones, and never more
than once a second in either direction. The 3-versus-5 asymmetry is deliberate
hysteresis: a flapping link settles low instead of pumping the picture up and down.
Without a plot that is invisible, and the honest question "why is it still at 480p
when the link came back" has no answer on screen.

Three stacked panels over one shared time axis:

  RUNG        the step line, with the ceiling drawn as the roof it cannot exceed
  THROUGHPUT  KB/s actually on the wire, video and audio, against the throttle budget
  REFUSED     frames the wire refused, VIDEO and AUDIO -- the only congestion signal
              SotF has, since send-or-drop keeps no queue to measure depth on. Below
              L5 video is shed by design, so audio sheds are the only signal left;
              a panel that drew video alone went blank exactly when it mattered

[DIAG_IS_UNGATED_V1] Nothing here filters, substitutes or suppresses a sample. A
measured zero is drawn as a zero; an absent counter is drawn as a gap; the two are
never collapsed onto each other. `or 0.0` and `if d <= 0: continue` did exactly that
collapsing and made a working audio-only call look like a dead screen.

All arithmetic lives in comms_ui.LinkHistory / plot_points and is oracle-driven
headless; this file is paint.
"""
from __future__ import annotations

import time

import tkinter as tk

import comms_ui as UI
import sotf_ladder as L

WATER, WATER2, WATER3 = "#0a1417", "#0f1e22", "#132a2f"
REED, LILY, AMBER, ROSE = "#3fae6e", "#7fd1a0", "#f4b942", "#d9645a"
BONE, MUTE, GRID = "#e8efe9", "#6f8a82", "#1c3438"
F_LABEL = ("TkFixedFont", 9, "bold")
F_SMALL = ("TkFixedFont", 9)
F_MONO = ("TkFixedFont", 11)
# [WIRE_ON_THE_DIAG_SCREEN_V1] Deliberately larger than F_MONO. These are the
# figures you read while something is wrong; on the in-call strip they were
# F_SMALL at the bottom edge of the window.
F_WIRE = ("TkFixedFont", 14)

W, H = 820, 700
PAD_L, PAD_R = 54, 16
PANEL_H = 118
GAP = 30


class DiagWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.title("FrogNet Communicator -- diagnostics")
        self.configure(bg=WATER)
        self.geometry("%dx%d" % (W, H))
        self._closing = False
        self.span = tk.DoubleVar(value=60.0)

        head = tk.Frame(self, bg=WATER2)
        head.pack(fill="x")
        tk.Label(head, text="BEARER", fg=LILY, bg=WATER2,
                 font=F_LABEL).pack(side="left", padx=14, pady=10)
        self.summary = tk.Label(head, text="", fg=BONE, bg=WATER2, font=F_MONO)
        self.summary.pack(side="left")
        for secs, label in ((30.0, "30s"), (60.0, "60s"), (120.0, "2m")):
            tk.Button(head, text=label, relief="flat", bg=WATER3, fg=BONE, padx=10,
                      command=lambda s=secs: self.span.set(s)).pack(side="right", padx=3,
                                                                    pady=6)

        self.canvas = tk.Canvas(self, width=W, height=H - 150, bg=WATER,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        foot = tk.Frame(self, bg=WATER2)
        foot.pack(fill="x")
        self.law = tk.Label(
            foot, bg=WATER2, fg=MUTE, font=F_SMALL, justify="left", anchor="w",
            text=("step DOWN after %d consecutive stressed samples   "
                  "step UP after %d consecutive clean ones   "
                  "at most one step per %.1f s"
                  % (UI.BEARER_BAD_STEPS, UI.BEARER_GOOD_STEPS,
                     UI.BEARER_STEP_CAP_S)))
        self.law.pack(anchor="w", padx=14, pady=(8, 2))
        self.lag_lbl = tk.Label(foot, bg=WATER2, fg=BONE, font=F_MONO, justify="left",
                                anchor="w", text="")
        self.lag_lbl.pack(anchor="w", padx=14, pady=(0, 10))

        # [WIRE_ON_THE_DIAG_SCREEN_V1] The wire figures -- send/recv rates,
        # incoming frame size, drops, backlog -- lived only in the in-call
        # control strip, in F_SMALL, at the bottom edge of a window whose middle
        # is video. They are the numbers you actually read while something is
        # wrong, and they were the least legible thing on screen.
        #
        # Same values, same source (app._last_stats, the snapshot the legend
        # already uses), rendered large at the top of the screen that exists for
        # reading numbers. Not a copy of the formatting: three lines with room,
        # so send, receive and loss can be compared at a glance instead of being
        # scanned out of one run-on string.
        wire = tk.Frame(self, bg=WATER3)
        wire.pack(fill="x", after=head)
        tk.Label(wire, text="WIRE", fg=LILY, bg=WATER3,
                 font=F_LABEL).pack(side="left", padx=14, pady=(10, 10))
        col = tk.Frame(wire, bg=WATER3)
        col.pack(side="left", fill="x", expand=True, pady=8)
        self.wire_send = tk.Label(col, text="", fg=BONE, bg=WATER3,
                                  font=F_WIRE, anchor="w", justify="left")
        self.wire_send.pack(anchor="w")
        self.wire_recv = tk.Label(col, text="", fg=BONE, bg=WATER3,
                                  font=F_WIRE, anchor="w", justify="left")
        self.wire_recv.pack(anchor="w")
        self.wire_loss = tk.Label(col, text="", fg=MUTE, bg=WATER3,
                                  font=F_WIRE, anchor="w", justify="left")
        self.wire_loss.pack(anchor="w")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(120, self._tick)

    # ------------------------------------------------------------------
    def _panel(self, y, title, unit):
        c = self.canvas
        x0, x1 = PAD_L, W - PAD_R
        c.create_text(x0, y - 12, text=title, fill=MUTE, font=F_LABEL, anchor="w")
        c.create_text(x1, y - 12, text=unit, fill=MUTE, font=F_SMALL, anchor="e")
        c.create_rectangle(x0, y, x1, y + PANEL_H, outline=GRID, fill=WATER2)
        return x0, x1

    def _tick(self):
        if self._closing:
            return
        self._draw_wire()
        self._draw()
        self.after(120, self._tick)

    def _draw_wire(self):
        """[WIRE_ON_THE_DIAG_SCREEN_V1] The wire panel, from the same snapshot
        the in-call legend reads.

        No substitution: an absent counter prints as the legend's MISSING
        marker, never as 0. A measured zero and an unreported one are different
        facts and this screen exists to tell them apart.
        """
        s = getattr(self.app, "_last_stats", None)
        if not s:
            for lbl in (self.wire_send, self.wire_recv, self.wire_loss):
                lbl.config(text="")
            self.wire_send.config(text="no call running")
            return
        self.wire_send.config(
            text="send    %6s fps   %8s KB/s video   %8s KB/s audio"
                 % (UI.num(s.get("v_fps_sent")), UI.num(s.get("v_kbps")),
                    UI.num(s.get("a_kbps"))))
        self.wire_recv.config(
            text="recv    %6s fps   %8s KB/s video   %8s KB/s audio  %s"
                 % (UI.num(s.get("v_fps_recv")), UI.num(s.get("v_rx_kbps")),
                    UI.num(s.get("a_rx_kbps")),
                    UI.rx_size_legend(s.get("rx_dims")).strip()))
        _holds = getattr(self.app, "_holds", None)
        _backlog = UI.hold_legend(_holds)
        self.wire_loss.config(
            text="drops   %6s /s video   %8s /s audio        %s"
                 % (UI.num(s.get("v_drop_fps")),
                    UI.num(getattr(self.app, "_a_shed_delta", None)),
                    _backlog or "backlog --"),
            fg=(ROSE if (_holds and any(h.get("awaiting_keyframe")
                                        for h in _holds)) else MUTE))

    def _draw(self):
        c = self.canvas
        c.delete("all")
        hist = self.app.history
        span = float(self.span.get())
        ts, rows = hist.window(span)
        x0, x1 = PAD_L, W - PAD_R
        width = x1 - x0

        if not ts:
            c.create_text(W / 2, 140, text="no call running -- nothing to plot",
                          fill=MUTE, font=F_MONO)
            self.summary.config(text="idle")
            self.lag_lbl.config(text="")
            return

        t0, t1 = ts[0], ts[-1]
        call = self.app.call
        ceiling = getattr(call, "ceiling", L.MAX_IDX) if call is not None else L.MAX_IDX

        # -- panel 1: the rung --------------------------------------------
        y = 30
        self._panel(y, "RUNG", "L0 - L7")
        for idx in range(L.MIN_IDX, L.MAX_IDX + 1):
            yy = y + PANEL_H - (idx / L.MAX_IDX) * PANEL_H
            c.create_line(x0, yy, x1, yy, fill=GRID)
            if idx in (0, 3, 5, 7):
                c.create_text(x0 - 8, yy, text=L.code(idx), fill=MUTE, font=F_SMALL,
                              anchor="e")
        yc = y + PANEL_H - (ceiling / L.MAX_IDX) * PANEL_H
        c.create_line(x0, yc, x1, yc, fill=AMBER, dash=(3, 3))
        c.create_text(x1 - 4, yc - 8, text="ceiling %s" % L.code(ceiling), fill=AMBER,
                      font=F_SMALL, anchor="e")
        for seg in UI.plot_points(ts, [r.get("rung") for r in rows], x0, y, width,
                                  PANEL_H, 0, L.MAX_IDX, t0, t1):
            if len(seg) > 1:
                flat = []
                prev = None
                for px, py in seg:                 # step line, not a slope: a rung
                    if prev is not None:           # change is instantaneous
                        flat += [px, prev]
                    flat += [px, py]
                    prev = py
                c.create_line(*flat, fill=REED, width=2)

        # step markers, so the eye lands on the moments that matter
        for ev in hist.step_events(span):
            px = x0 + (ev["t"] - t0) / ((t1 - t0) or 1.0) * width
            col = ROSE if ev["dir"] == "down" else LILY
            c.create_line(px, y, px, y + PANEL_H, fill=col, dash=(2, 4))
            c.create_text(px, y + 6, text=L.code(ev["to"]), fill=col, font=F_SMALL)

        # -- panel 2: throughput vs the budget -----------------------------
        # [DIAG_IS_UNGATED_V1] Video AND audio, both drawn, neither substituted.
        # `or 0.0` was a gate: it turned a MISSING sample into a reading of zero, so
        # an absent counter and a genuinely idle wire drew the same line. None stays
        # None and plot_points breaks the trace there; a real zero is drawn AT zero.
        y += PANEL_H + GAP
        self._panel(y, "THROUGHPUT", "KB/s on the wire   video / audio")
        vkbps = [r.get("kbps") for r in rows]
        akbps = [r.get("a_kbps") for r in rows]
        budget = [None if r.get("throttle_kbps") is None
                  else r["throttle_kbps"] / 8.0 for r in rows]       # kbit -> KB/s
        _seen = [v for v in list(vkbps) + list(akbps) + list(budget) if v is not None]
        vmax = (max(_seen) if _seen else 10.0) * 1.15 or 10.0
        for seg in UI.plot_points(ts, budget, x0, y, width, PANEL_H, 0, vmax, t0, t1):
            if len(seg) > 1:
                c.create_line(*[v for p in seg for v in p], fill=AMBER, dash=(4, 3))
        for seg in UI.plot_points(ts, vkbps, x0, y, width, PANEL_H, 0, vmax, t0, t1):
            if len(seg) > 1:
                c.create_line(*[v for p in seg for v in p], fill=LILY, width=2)
        for seg in UI.plot_points(ts, akbps, x0, y, width, PANEL_H, 0, vmax, t0, t1):
            if len(seg) > 1:
                c.create_line(*[v for p in seg for v in p], fill=REED, width=2)
        c.create_text(x0 - 8, y, text="%.0f" % vmax, fill=MUTE, font=F_SMALL, anchor="e")
        c.create_text(x0 - 8, y + PANEL_H, text="0", fill=MUTE, font=F_SMALL, anchor="e")
        c.create_text(x1 - 4, y + PANEL_H + 12, text="video", fill=LILY,
                      font=F_SMALL, anchor="e")
        c.create_text(x1 - 44, y + PANEL_H + 12, text="audio", fill=REED,
                      font=F_SMALL, anchor="e")

        # -- panel 3: what the wire refused ---------------------------------
        # [DIAG_IS_UNGATED_V1] Video drops AND audio sheds. `if d <= 0: continue`
        # skipped every zero, so a clean second and a second with no data looked
        # identical -- and below L5, where video is shed by design, the panel drew
        # nothing at all while audio sheds were the only congestion signal left.
        # Every sample is now marked: a zero draws a baseline tick, absent data draws
        # nothing, and the two are distinguishable on the glass.
        y += PANEL_H + GAP
        self._panel(y, "REFUSED", "frames/s the wire refused   video / audio")
        drops = [r.get("drops") for r in rows]
        asheds = [r.get("a_sheds") for r in rows]
        _seen = [v for v in list(drops) + list(asheds) if v is not None]
        dmax = ((max(_seen) if _seen else 1.0) or 1.0) * 1.2
        for t, d, a in zip(ts, drops, asheds):
            px = x0 + (t - t0) / ((t1 - t0) or 1.0) * width
            if d is not None:
                ph = (d / dmax) * PANEL_H
                if d > 0:
                    c.create_line(px, y + PANEL_H, px, y + PANEL_H - ph, fill=ROSE)
                else:
                    # a measured zero: one pixel on the floor, so "clean" is visible
                    # and is not the same picture as "no sample"
                    c.create_line(px, y + PANEL_H, px, y + PANEL_H - 1, fill=GRID)
            if a:
                ph = (a / dmax) * PANEL_H
                c.create_line(px + 1, y + PANEL_H, px + 1, y + PANEL_H - ph,
                              fill=AMBER)
        c.create_text(x0 - 8, y, text="%.0f" % dmax, fill=MUTE, font=F_SMALL, anchor="e")
        c.create_text(x0 - 8, y + PANEL_H, text="0", fill=MUTE, font=F_SMALL, anchor="e")
        c.create_text(x1 - 4, y + PANEL_H + 12, text="video", fill=ROSE,
                      font=F_SMALL, anchor="e")
        c.create_text(x1 - 44, y + PANEL_H + 12, text="audio", fill=AMBER,
                      font=F_SMALL, anchor="e")

        # -- readouts -------------------------------------------------------
        last = rows[-1]
        bad, good = last.get("bad"), last.get("good")
        self.summary.config(
            text="rung %s   ceiling %s   stressed %s/%d   clean %s/%d"
                 % (L.code(last.get("rung")) if last.get("rung") is not None else "--",
                    L.code(ceiling),
                    bad if bad is not None else "-", UI.BEARER_BAD_STEPS,
                    good if good is not None else "-", UI.BEARER_GOOD_STEPS))
        lag = hist.hysteresis_lag(span)
        parts = []
        if lag["down_mean"] is not None:
            parts.append("mean lag before stepping DOWN  %.2f s (%d step%s)"
                         % (lag["down_mean"], len(lag["down_lags"]),
                            "" if len(lag["down_lags"]) == 1 else "s"))
        if lag["up_mean"] is not None:
            parts.append("mean lag before stepping UP  %.2f s (%d step%s)"
                         % (lag["up_mean"], len(lag["up_lags"]),
                            "" if len(lag["up_lags"]) == 1 else "s"))
        if not parts:
            parts.append("no rung changes in this window")
        self.lag_lbl.config(text="    ".join(parts))

    def close(self):
        self._closing = True
        try:
            self.app.diag = None
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
