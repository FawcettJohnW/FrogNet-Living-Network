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
comms_brand.py -- the FrogNet mark, and the frog door the Communicator opens on.

THE FROG DOOR

On fawcettinnovations.com, clicking the brand mark in the header drops a scrim over a
circular portal, and you fall through four FrogNet logos -- each a pair of leaves that
part, alternating vertical and horizontal, 250 ms apart, while the stack scales up --
into a dark pond room: a light beam, a slow radar sweep, a round table wired as a
seven-node mesh, THE FROGS ARE READY above it, and 7 to 16 frogs bobbing in the dark
with green eyes, placed from a per-open seed so it is never quite the same room twice.

It is the MST3K door sequence, and that is the thing to get right. What makes that
sequence work is that the camera never stops. A door is small and far; it grows as you
close on it; it parts; its leaves sweep past the edges of frame while the next one is
already growing behind. It is not a stack of images being zoomed -- it is travel, and
each door has its own distance from you.

So each door here has a z, the camera advances at a constant rate, and a door's size on
screen is a function of how far ahead of the camera it still is. Leaves part as their
door comes up on you and accelerate outward as it passes. The leaves are cut at build
time across fourteen depth steps from 0.34x to 1.90x, geometric so the growth reads
evenly. The first pass at this cut five steps from 1.00 to 1.18 -- the site's stack
scale -- which is a nudge, not a fall, and looked like exactly that.

Behind the last door is the room, which is where the splash earns its place. On the web
the door shuts again after a second and a half and there is nothing behind it. Here the
shell is reading its settings out of the tuple plane -- a round trip to the elected data
host that can be slow or can fail -- so the line under the table, which on the site
reads "Network Control - standing by", carries what the application is actually doing.

FAITHFUL: four doors, alternating split axis, the stagger, reverse-order close, the
seven-node table, the caption, 7-16 procedurally placed bobbing frogs with #7dffab eyes,
mulberry32 seeded per open -- the same generator the site uses, so a seed lays out the
same room in both.

APPROXIMATED, because Tk has neither gradients nor alpha on canvas items: the radar's
conic gradient is stepped wedges trailing a leading edge, and the light beam is banded
polygons. Perspective is a scale ladder, not a projection -- there is no transform.

REBUILT rather than ported: the frog and table drawings are inline SVG on the site and I
have not seen that source. These are Canvas primitives built to the description and will
not be pixel-identical.

FROGNET_REDUCED_MOTION=1 kills the bob, the sweep and the travel, as
prefers-reduced-motion does on the site. A missing asset degrades to a plain card:
decoration must never be able to stop somebody making a call.

ASCII only.
"""
from __future__ import annotations

import math
import os
import time
import tkinter as tk

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
DOOR_DIR = os.path.join(ASSET_DIR, "door")
SIZES = (512, 256, 96, 48, 32)

WATER, ROOM = "#0a1417", "#06100f"
REED, LILY, BONE, MUTE = "#3fae6e", "#7fd1a0", "#e8efe9", "#6f8a82"
EYE, BEAM, TABLE = "#7dffab", "#0f2a26", "#12312c"

W = H = 620
CX = CY = W // 2
PORTAL_R = 262

N_DOORS = 8
DOOR_GAP = 1.0          # z between doors
HOLD_S = 0.5            # held on each rung, with its count showing
STEP_S = 0.30           # the pass-through between one rung and the next
CAM_START = 0.0         # door zero is where you begin: full frame, shut, held
PART_SPAN = 0.55        # a door parts as the camera REACHES it and passes through,
                        # not before -- which is what lets door zero be held full
                        # frame and unparted as a title card
PART_TRAVEL = 0.62      # how far the leaves fly, as a fraction of the frame

_cache = {}
_scales = None


def door_scales():
    """The depth ladder the leaves were cut at."""
    global _scales
    if _scales is None:
        try:
            with open(os.path.join(DOOR_DIR, "scales.txt")) as f:
                _scales = [float(x) for x in f if x.strip()]
        except Exception:
            _scales = []
    return _scales


def mulberry32(seed: int):
    """The site's PRNG, reimplemented rather than replaced with random.Random so a
    given seed lays out the same room here as it does in the browser. The 32-bit
    wrap-around is explicit because Python integers do not overflow."""
    a = seed & 0xFFFFFFFF

    def rnd():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = a
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t = (t ^ (t + ((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    return rnd


def _mix(c1, c2, f):
    """Blend two hex colours. Canvas items have no alpha, so every fade here is a
    colour computed against the background it sits on."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))


def logo(size: int):
    """PhotoImage of the mark at one of the pre-cut sizes, or None if absent. The
    caller must keep the reference alive; Tk drops an image the moment Python does.
    Cached per interpreter -- a Tk image belongs to the one that made it."""
    size = min(SIZES, key=lambda s: abs(s - size))
    if size in _cache:
        return _cache[size]
    try:
        img = tk.PhotoImage(file=os.path.join(ASSET_DIR, "frognet_logo_%d.png" % size))
    except Exception:
        img = None
    _cache[size] = img
    return img


def have_assets() -> bool:
    if not all(os.path.exists(os.path.join(ASSET_DIR, "frognet_logo_%d.png" % s))
               for s in SIZES):
        return False
    n = len(door_scales())
    if n < 2:
        return False
    for d in range(N_DOORS):
        o = "v" if d % 2 == 0 else "h"
        for l in ("a", "b"):
            for i in range(n):
                if not os.path.exists(os.path.join(
                        DOOR_DIR, "door%d_%s_%s_%02d.png" % (d, o, l, i))):
                    return False
    return True


def reduced_motion() -> bool:
    return os.environ.get("FROGNET_REDUCED_MOTION", "") not in ("", "0", "false")


class FrogDoor(tk.Toplevel):
    ROOM_HOLD_S = 0.7

    def __init__(self, master, name=""):
        super().__init__(master)
        self.overrideredirect(True)
        self.configure(bg=WATER)
        self.name = name
        self.reduced = reduced_motion()
        self._closed = False
        self.skipped = False
        self._t0 = time.time()
        self._imgs = []
        self._frog_items = []
        self._sweep_items = []
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass

        self.canvas = tk.Canvas(self, width=W, height=H, bg=WATER,
                                highlightthickness=0, bd=0)
        self.canvas.pack()

        self._seed = int(time.time() * 1000) & 0xFFFFFFFF
        self._rnd = mulberry32(self._seed)

        self._build_room()
        self._build_doors()

        # Clicking anywhere shorts the whole thing out and hands the user the
        # application. A splash is never the point; it is what you look at while the
        # settings read finishes, and somebody who does not want to look at it should
        # not have to. Bound on the toplevel AND the canvas because an
        # overrideredirect window does not reliably take keyboard focus, so the key
        # bindings alone cannot be trusted to fire.
        for w in (self, self.canvas):
            w.bind("<Button-1>", lambda e: self.skip())
            w.bind("<Key>", lambda e: self.skip())
        self.focus_force()

        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry("%dx%d+%d+%d" % (W, H, (sw - W) // 2, (sh - H) // 3))

    # -- the room ----------------------------------------------------------
    def _build_room(self):
        """Build the room, then HIDE it.

        [ROOM_BEHIND_THE_LAST_DOOR_V1] The room used to be drawn first with the doors
        stacked over it, so every time a pair of leaves parted you saw straight
        through to the table, the frogs and the caption. By the third door there was
        nothing left to arrive at. The room is the destination: it is not revealed
        until the last door is out of the way.

        Everything except the portal shell and the countdown goes on the hold list;
        the frogs and the radar sweep are rebuilt every frame, so those are gated on
        the same flag rather than hidden once.
        """
        self._room_items = []
        self._room_shown = False
        c = self.canvas
        c.create_oval(CX - PORTAL_R, CY - PORTAL_R, CX + PORTAL_R, CY + PORTAL_R,
                      fill=ROOM, outline=_mix(ROOM, REED, 0.35), width=2)
        for i in range(7):
            f = i / 7.0
            spread = 44 + i * 28
            self._room_items.append(c.create_polygon(
                CX - 16, CY - PORTAL_R + 8, CX + 16, CY - PORTAL_R + 8,
                CX + spread, CY + 140, CX - spread, CY + 140,
                fill=_mix(BEAM, ROOM, f), outline=""))

        r = 104
        pts = [(CX + r * math.cos(-math.pi / 2 + i * 2 * math.pi / 7),
                CY + 30 + r * 0.52 * math.sin(-math.pi / 2 + i * 2 * math.pi / 7))
               for i in range(7)]
        self._room_items.append(c.create_oval(
            CX - r - 8, CY + 30 - r * 0.52 - 8, CX + r + 8,
            CY + 30 + r * 0.52 + 8, fill=TABLE, outline=_mix(TABLE, REED, 0.5)))
        for i in range(7):                       # a mesh: every pair, not a ring
            for j in range(i + 1, 7):
                self._room_items.append(c.create_line(
                    pts[i][0], pts[i][1], pts[j][0], pts[j][1],
                    fill=_mix(TABLE, REED, 0.30)))
        for x, y in pts:
            self._room_items.append(c.create_oval(
                x - 5, y - 5, x + 5, y + 5, fill=REED,
                outline=_mix(REED, EYE, 0.5)))

        self._room_items.append(c.create_text(
            CX, CY - 168, text="THE FROGS ARE READY", fill=LILY,
            font=("TkFixedFont", 16, "bold")))
        self._status = c.create_text(CX, CY + 166,
                                     text="Network Control - standing by", fill=MUTE,
                                     font=("TkFixedFont", 9))
        self._room_items.append(self._status)
        if self.name:
            self._room_items.append(c.create_text(
                CX, CY + 212, text=self.name, fill=REED,
                font=("TkFixedFont", 10, "bold")))
        # [SPLASH_COUNTDOWN_V1] The rung count, in the strip ABOVE the door. It was
        # first drawn on the room at centre, where the shut door covered it completely
        # during the hold -- the exact half second it exists to be read. The door is
        # 460px in a 620px frame, so this band is always clear of it, and it is raised
        # over the leaves so a passing door never crosses it.
        self._count = c.create_text(CX, CY - PORTAL_R + 34, text="",
                                    fill=LILY, font=("TkFixedFont", 62, "bold"))
        self._place_frogs()
        for item in self._room_items:
            c.itemconfig(item, state="hidden")

    def _reveal_room(self):
        """The last door is clear: the room arrives, all at once."""
        if self._room_shown or self._closed:
            return
        self._room_shown = True
        try:
            for item in self._room_items:
                self.canvas.itemconfig(item, state="normal")
        except Exception:
            self._closed = True

    def _place_frogs(self):
        n = 7 + int(self._rnd() * 10)
        self._frogs = []
        for _ in range(n):
            x = y = 0
            for _try in range(24):
                x = CX + (self._rnd() * 2 - 1) * (PORTAL_R - 44)
                y = CY + (self._rnd() * 2 - 1) * (PORTAL_R - 44)
                if (x - CX) ** 2 + (y - CY) ** 2 < (PORTAL_R - 50) ** 2:
                    break
            self._frogs.append({"x": x, "y": y, "s": 0.6 + self._rnd() * 0.9,
                                "phase": self._rnd() * math.tau,
                                "rate": 0.7 + self._rnd() * 0.8})
        self._draw_frogs(0.0)

    def _draw_frogs(self, t):
        if not self._room_shown:
            return                     # [ROOM_BEHIND_THE_LAST_DOOR_V1]
        c = self.canvas
        for i in self._frog_items:
            c.delete(i)
        self._frog_items = []
        for f in self._frogs:
            bob = 0.0 if self.reduced else math.sin(t * f["rate"] + f["phase"]) * 3.0
            x, y, s = f["x"], f["y"] + bob, f["s"]
            body = _mix(ROOM, REED, 0.28 + 0.24 * s / 1.5)
            add = self._frog_items.append
            add(c.create_oval(x - 13 * s, y - 8 * s, x + 13 * s, y + 9 * s,
                              fill=body, outline=""))
            add(c.create_oval(x - 9 * s, y - 15 * s, x + 9 * s, y - 1 * s,
                              fill=body, outline=""))
            for dx in (-5.2, 5.2):
                ex, ey = x + dx * s, y - 13 * s
                add(c.create_oval(ex - 4.2 * s, ey - 4.2 * s, ex + 4.2 * s,
                                  ey + 4.2 * s, fill=_mix(ROOM, EYE, 0.28),
                                  outline=""))
                add(c.create_oval(ex - 2.1 * s, ey - 2.1 * s, ex + 2.1 * s,
                                  ey + 2.1 * s, fill=EYE, outline=""))

    def _sweep(self, t):
        if self.reduced or not self._room_shown:
            return                     # [ROOM_BEHIND_THE_LAST_DOOR_V1]
        c = self.canvas
        for i in self._sweep_items:
            c.delete(i)
        self._sweep_items = []
        head = (t * 42.0) % 360.0
        for i in range(14):
            col = _mix(_mix(ROOM, REED, 0.22), ROOM, i / 14.0)
            self._sweep_items.append(
                c.create_arc(CX - PORTAL_R + 3, CY - PORTAL_R + 3,
                             CX + PORTAL_R - 3, CY + PORTAL_R - 3,
                             start=head - i * 4.2, extent=-4.4, style="pieslice",
                             fill=col, outline=""))
        for i in self._sweep_items:
            c.tag_lower(i)

    # -- the doors, at depth ----------------------------------------------
    RUNGS = ["L0 PULSE", "L1 BEACON", "L2 WHISPER", "L3 VOICE",
             "L4 SOLO", "L5 DUET", "L6 ENSEMBLE", "L7 CHORUS"]

    def _build_doors(self):
        """Eight doors, one per rung of the ladder, in the order the ladder runs.

        Nearest first is L0 -- somebody banging on a pipe -- and you come up through
        semaphore, telegraph, radio, boombox, black-and-white TV and home movies to a
        modern set, then out into the room. The ladder is what the whole application
        is about, so falling through it is not decoration attached to the product; it
        IS the product, stated before a word of UI appears.

        Flip the list to fall the other way, from a modern call down to the floor,
        which is the truer story of what happens under load. It ends on a pipe, which
        is a strange note to open an application on, so it is not the default."""
        self.doors = []
        scales = door_scales()
        if not scales:
            return
        for i in range(N_DOORS):
            orient = "v" if i % 2 == 0 else "h"
            leaves = {}
            for leaf in ("a", "b"):
                try:
                    imgs = [tk.PhotoImage(file=os.path.join(
                        DOOR_DIR, "door%d_%s_%s_%02d.png" % (i, orient, leaf, s)))
                        for s in range(len(scales))]
                except Exception:
                    self.doors = []
                    return
                self._imgs.extend(imgs)
                leaves[leaf] = imgs
            items = {leaf: self.canvas.create_image(CX, CY, image=leaves[leaf][0])
                     for leaf in ("a", "b")}
            self.doors.append({"orient": orient, "leaves": leaves, "items": items,
                               "z": DOOR_GAP * i})
        for d in reversed(self.doors):     # nearest door drawn last
            for leaf in ("a", "b"):
                self.canvas.tag_raise(d["items"][leaf])
        self.canvas.tag_raise(self._count)         # the count sits over everything

    def _place_door(self, d, camera, no_part=False):
        """Put one door where the camera says it is.

        `ahead` is how far in front of the camera the door still sits. Apparent size
        goes as 1/(ahead + 1) -- the same falloff a lens gives, which is what makes it
        read as approach rather than a growing picture. A door the camera has passed is
        hidden; a door still far enough back is shut."""
        scales = door_scales()
        ahead = d["z"] - camera
        if ahead < -1.05:
            for leaf in ("a", "b"):
                self.canvas.itemconfig(d["items"][leaf], state="hidden")
            return
        apparent = 1.0 / (ahead * 0.72 + 1.0)
        idx = min(range(len(scales)), key=lambda i: abs(scales[i] - apparent))
        # leaves start parting when the door is close, and accelerate as it passes
        part = 0.0
        if ahead < 0.0 and not no_part:
            part = min(1.5, (-ahead) / PART_SPAN) ** 1.2
        for leaf in ("a", "b"):
            img = d["leaves"][leaf][idx]
            self.canvas.itemconfig(d["items"][leaf], image=img, state="normal")
            w, h = img.width(), img.height()
            sign = -1 if leaf == "a" else 1
            travel = (W if d["orient"] == "v" else H) * PART_TRAVEL * part
            if d["orient"] == "v":
                self.canvas.coords(d["items"][leaf], CX + sign * (w / 2 + travel), CY)
            else:
                self.canvas.coords(d["items"][leaf], CX, CY + sign * (h / 2 + travel))

    # -- the sequence ------------------------------------------------------
    def open_doors(self):
        """Walk the ladder down, one rung at a time, and arrive in the room.

        Half a second held on each door with the count on it, then the leaves part
        and the camera passes through to the next. Eight doors, counted 8 down to 0 --
        the count IS the descent, so the viewer is told where they are rather than
        having to read eight small labels going past.

        Driven by an explicit pump, not `after`: this runs inside the main window's
        __init__, before the mainloop those callbacks depend on exists.
        """
        if self._closed or not self.doors:
            self._reveal_room()        # no doors to fall through: the room is all there is
            self._pump(self.ROOM_HOLD_S)
            return
        if self.reduced:
            for d in self.doors:
                self._safe_hide(d)
            self._reveal_room()        # reduced motion: no sequence, so no withholding
            self._pump(self.ROOM_HOLD_S)
            return

        n = len(self.doors)
        for i, d in enumerate(self.doors):
            if self._closed:
                return
            # hold: this door full frame, shut, with the count on it
            self._set_count(n - i)
            for dd in self.doors:
                self._place_door(dd, float(i) * DOOR_GAP, no_part=(dd is d))
            self._hold(HOLD_S)
            if self._closed:
                return
            # pass through it: camera advances one door, this one parts and goes by
            start = time.time()
            while not self._closed:
                t = time.time() - start
                if t > STEP_S:
                    break
                cam = (float(i) + (t / STEP_S)) * DOOR_GAP
                for dd in self.doors:
                    self._place_door(dd, cam)
                self._frame(time.time() - self._t0)
                time.sleep(0.016)
        if self._closed:
            return
        self._set_count(0)
        for d in self.doors:
            self._safe_hide(d)
        self._reveal_room()            # [ROOM_BEHIND_THE_LAST_DOOR_V1] now, not before
        self._pump(self.ROOM_HOLD_S)

    def _safe_hide(self, d):
        """Hide a door's leaves. Silent if the window is already gone -- a click on
        the splash destroys the canvas mid-sequence, and the code that ran after the
        pump loop went straight on to itemconfig() a dead widget:
            _tkinter.TclError: invalid command name ".!frogdoor.!canvas"
        """
        if self._closed:
            return
        try:
            for leaf in ("a", "b"):
                self.canvas.itemconfig(d["items"][leaf], state="hidden")
        except Exception:
            self._closed = True

    def _set_count(self, k):
        if self._closed:
            return
        try:
            self.canvas.itemconfig(self._count, text=str(k))
        except Exception:
            self._closed = True

    def _hold(self, seconds):
        end = time.time() + seconds
        while not self._closed and time.time() < end:
            self._frame(time.time() - self._t0)
            time.sleep(0.016)

    def _pump(self, seconds):
        end = time.time() + seconds
        while not self._closed and time.time() < end:
            self._frame(time.time() - self._t0)
            time.sleep(0.016)

    def _frame(self, t):
        try:
            self._sweep(t)
            self._draw_frogs(t)
            self.update_idletasks()
            self.update()
        except Exception:
            self._closed = True

    def step(self, text):
        """The line under the table. On the site it reads 'Network Control - standing
        by'; here it says what the application is doing, because here there is
        something behind the door."""
        if self._closed:
            return
        try:
            self.canvas.itemconfig(self._status, text=text)
            self._frame(time.time() - self._t0)
        except Exception:
            self._closed = True

    def _skip_to_room(self):
        """Clicking through jumps to the destination -- it does not cancel it. The
        room is what the splash is FOR; skipping the doors should land you in it."""
        for d in getattr(self, "doors", []):
            self._safe_hide(d)
        self._set_count(0)
        self._reveal_room()

    def skip(self):
        """User wants out. Every animation loop watches _closed, so setting it ends
        the sequence on the next frame; `skipped` tells the shell to get the rest of
        startup off the critical path rather than making them wait for a database
        round trip they just said they were not interested in."""
        self.skipped = True
        self.done()

    def done(self):
        self._closed = True
        try:
            self.destroy()
        except Exception:
            pass


class PlainSplash(tk.Toplevel):
    """Fallback when the door assets are absent."""

    def __init__(self, master, name=""):
        super().__init__(master)
        self.overrideredirect(True)
        self.configure(bg=WATER)
        self._img = logo(256)
        if self._img is not None:
            tk.Label(self, image=self._img, bg=WATER).pack(pady=(28, 8))
        tk.Label(self, text="FrogNet", fg=BONE, bg=WATER,
                 font=("TkDefaultFont", 26, "bold")).pack()
        tk.Label(self, text="C O M M U N I C A T O R", fg=LILY, bg=WATER,
                 font=("TkFixedFont", 12)).pack(pady=(2, 0))
        self.line = tk.Label(self, text="starting", fg=MUTE, bg=WATER,
                             font=("TkFixedFont", 9))
        self.line.pack(pady=(18, 24))
        self.skipped = False
        for w in (self,):
            w.bind("<Button-1>", lambda e: self.skip())
            w.bind("<Key>", lambda e: self.skip())
        self.update_idletasks()
        w, h = max(360, self.winfo_reqwidth()), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 3))

    def open_doors(self):
        pass

    def _skip_to_room(self):
        """Clicking through jumps to the destination -- it does not cancel it. The
        room is what the splash is FOR; skipping the doors should land you in it."""
        for d in getattr(self, "doors", []):
            self._safe_hide(d)
        self._set_count(0)
        self._reveal_room()

    def skip(self):
        self.skipped = True
        self.done()

    def step(self, text):
        try:
            self.line.config(text=text)
            self.update_idletasks()
            self.update()
        except Exception:
            pass

    def done(self):
        try:
            self.destroy()
        except Exception:
            pass


def splash(master, name=""):
    """The frog door if its assets are here, the plain card if not."""
    try:
        if have_assets():
            return FrogDoor(master, name)
    except Exception:
        pass
    try:
        return PlainSplash(master, name)
    except Exception:
        return None
