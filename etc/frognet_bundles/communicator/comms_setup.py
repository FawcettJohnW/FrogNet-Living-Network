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
comms_setup.py -- the device chooser: pick a camera while watching it, pick a
microphone while watching it move, pick a speaker and hear it.

Before this, choosing devices meant quitting, guessing a different `--cam N` on the
command line, and relaunching to find out. Audio was worse: there was no way to
learn that the selected input was the dead front-panel jack short of making a call
and having someone tell you they could not hear you.

Two rules this window lives by:

  RELEASE WHAT YOU OPEN. The preview holds the camera and the meter holds a
  PortAudio input stream. fnav opens both again when a call starts, and a device
  still held here is exactly the "opened but read() returned no frame
  (busy/permission)" path in fnav._probe. Every open is paired with a release, on
  switching device, on Save, and on close.

  SETTINGS ARE SHARED MEMORY. Nothing is written to a dotfile. Choices converge
  into the transient store through ControlPlane.save_settings, under a scope keyed
  to (this node, this user), and are re-asserted by the heartbeat like presence.

All model logic -- ranking camera probes, filtering the PortAudio table, mapping RMS
to a bar -- lives in comms_ui and is oracle-driven headless. This file is paint and
device handling.
"""
from __future__ import annotations

import math
import threading

import tkinter as tk
from tkinter import ttk

import comms_ui as UI

try:
    import cv2
except Exception:
    cv2 = None

try:
    import numpy as np
except Exception:
    np = None

WATER, WATER2, WATER3 = "#0a1417", "#0f1e22", "#132a2f"
REED, LILY, AMBER, ROSE = "#3fae6e", "#7fd1a0", "#f4b942", "#d9645a"
BONE, MUTE, STAGE = "#e8efe9", "#6f8a82", "#06100f"
F_LABEL = ("TkFixedFont", 9, "bold")
F_SMALL = ("TkFixedFont", 9)
F_MED = ("TkDefaultFont", 11)
F_BIG = ("TkFixedFont", 13, "bold")

PREVIEW_W, PREVIEW_H = 320, 180
METER_W, METER_H = 260, 18


class SetupWindow(tk.Toplevel):
    """Modeless device chooser. `on_save(settings)` is called with the chosen
    values so the shell can adopt them for the next call."""

    def __init__(self, master, cp, current, on_save, tmp_path, on_close=None):
        super().__init__(master)
        self.cp = cp
        self.on_save = on_save
        # [CAMERA_CLAIM_V1] the shell holds the camera claim while this window is up;
        # tell it when we are done so the lobby preview can take the device back.
        self.on_closed = on_close
        self.tmp = tmp_path
        self.title("Camera and sound")
        self.configure(bg=WATER)
        self.geometry("760x560")

        self.cameras = []
        self.inputs = []
        self.outputs = []
        self.cam_idx = current.get("cam")
        self.in_dev = current.get("in_dev")
        self.out_dev = current.get("out_dev")

        self._cap = None                 # held camera, released on switch/close
        self._stream = None              # held PortAudio input, ditto
        self._rms = 0.0
        self._peak = 0.0
        self._photo = None
        self._closing = False

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(50, self._scan)
        self.after(66, self._tick_preview)
        self.after(60, self._tick_meter)

    # -- chrome ------------------------------------------------------------
    def _build(self):
        tk.Label(self, text="Camera and sound", fg=BONE, bg=WATER,
                 font=("TkDefaultFont", 17, "bold")).pack(anchor="w", padx=20, pady=(16, 2))
        self.note = tk.Label(self, text="looking for devices...", fg=MUTE, bg=WATER,
                             font=F_SMALL, justify="left", anchor="w")
        self.note.pack(anchor="w", padx=20, pady=(0, 12))

        body = tk.Frame(self, bg=WATER)
        body.pack(fill="both", expand=True, padx=20)

        # -- camera --------------------------------------------------------
        cam = tk.Frame(body, bg=WATER2)
        cam.pack(fill="x", pady=(0, 12))
        tk.Label(cam, text="CAMERA", fg=MUTE, bg=WATER2,
                 font=F_LABEL).pack(anchor="w", padx=14, pady=(12, 6))
        crow = tk.Frame(cam, bg=WATER2)
        crow.pack(fill="x", padx=14, pady=(0, 12))
        self.preview = tk.Label(crow, bg=STAGE, fg=MUTE, width=44, height=10,
                                text="starting preview...", font=F_SMALL)
        self.preview.pack(side="left")
        cside = tk.Frame(crow, bg=WATER2)
        cside.pack(side="left", fill="both", expand=True, padx=14)
        self.cam_list = tk.Listbox(cside, bg=WATER, fg=BONE, bd=0, height=5,
                                   highlightthickness=0, selectbackground=REED,
                                   selectforeground=WATER, font=F_SMALL,
                                   exportselection=False)
        self.cam_list.pack(fill="x")
        self.cam_list.bind("<<ListboxSelect>>", self._pick_camera)
        self.cam_note = tk.Label(cside, text="", fg=MUTE, bg=WATER2, font=F_SMALL,
                                 anchor="w", justify="left")
        self.cam_note.pack(anchor="w", pady=(6, 0))
        tk.Button(cside, text="Rescan", command=self._scan, bg=WATER3, fg=BONE,
                  relief="flat", padx=10).pack(anchor="w", pady=(8, 0))

        # -- microphone ----------------------------------------------------
        mic = tk.Frame(body, bg=WATER2)
        mic.pack(fill="x", pady=(0, 12))
        tk.Label(mic, text="MICROPHONE", fg=MUTE, bg=WATER2,
                 font=F_LABEL).pack(anchor="w", padx=14, pady=(12, 6))
        mrow = tk.Frame(mic, bg=WATER2)
        mrow.pack(fill="x", padx=14, pady=(0, 12))
        self.in_list = tk.Listbox(mrow, bg=WATER, fg=BONE, bd=0, height=4,
                                  highlightthickness=0, selectbackground=REED,
                                  selectforeground=WATER, font=F_SMALL,
                                  exportselection=False)
        self.in_list.pack(side="left", fill="x", expand=True)
        self.in_list.bind("<<ListboxSelect>>", self._pick_input)
        mside = tk.Frame(mrow, bg=WATER2)
        mside.pack(side="left", padx=16)
        tk.Label(mside, text="say something", fg=MUTE, bg=WATER2,
                 font=F_SMALL).pack(anchor="w")
        self.meter = tk.Canvas(mside, width=METER_W, height=METER_H, bg=WATER,
                               highlightthickness=0, bd=0)
        self.meter.pack(anchor="w", pady=4)
        self.meter_note = tk.Label(mside, text="", fg=MUTE, bg=WATER2, font=F_SMALL,
                                   anchor="w")
        self.meter_note.pack(anchor="w")

        # -- speaker -------------------------------------------------------
        out = tk.Frame(body, bg=WATER2)
        out.pack(fill="x")
        tk.Label(out, text="SPEAKER", fg=MUTE, bg=WATER2,
                 font=F_LABEL).pack(anchor="w", padx=14, pady=(12, 6))
        orow = tk.Frame(out, bg=WATER2)
        orow.pack(fill="x", padx=14, pady=(0, 12))
        self.out_list = tk.Listbox(orow, bg=WATER, fg=BONE, bd=0, height=4,
                                   highlightthickness=0, selectbackground=REED,
                                   selectforeground=WATER, font=F_SMALL,
                                   exportselection=False)
        self.out_list.pack(side="left", fill="x", expand=True)
        oside = tk.Frame(orow, bg=WATER2)
        oside.pack(side="left", padx=16)
        tk.Button(oside, text="Play a test tone", command=self._test_tone, bg=WATER3,
                  fg=BONE, relief="flat", padx=12, pady=6).pack()
        self.out_note = tk.Label(oside, text="", fg=MUTE, bg=WATER2, font=F_SMALL)
        self.out_note.pack(pady=(6, 0))
        self.out_list.bind("<<ListboxSelect>>", self._pick_output)

        foot = tk.Frame(self, bg=WATER)
        foot.pack(fill="x", padx=20, pady=14)
        tk.Button(foot, text="Save", command=self._save, bg=REED, fg=WATER,
                  relief="flat", padx=22, pady=9, font=F_BIG).pack(side="left")
        tk.Button(foot, text="Close", command=self.close, bg=WATER2, fg=BONE,
                  relief="flat", padx=18, pady=9).pack(side="left", padx=8)
        self.saved_note = tk.Label(foot, text="", fg=LILY, bg=WATER, font=F_SMALL)
        self.saved_note.pack(side="left", padx=14)

    # -- discovery ---------------------------------------------------------
    def _scan(self):
        """Probe cameras off the UI thread -- opening ten cv2 indices takes seconds
        and must not freeze the window."""
        self.note.config(text="looking for devices...")
        threading.Thread(target=self._scan_worker, daemon=True).start()
        self._load_audio()

    def _scan_worker(self):
        probes = []
        if cv2 is not None:
            import fnav
            with fnav._silence_cv2_stderr():
                for i in range(10):
                    cap = fnav.open_camera(i)
                    try:
                        opened = bool(cap.isOpened())
                        ok, w, h = False, 0, 0
                        if opened:
                            ok = bool(cap.read()[0])
                            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                        if opened or ok:
                            probes.append({"index": i, "opened": opened, "ok": ok,
                                           "w": w, "h": h})
                    finally:
                        cap.release()          # released before the preview opens one
        try:
            self.after(0, lambda: self._cameras_found(probes))
        except Exception:
            pass

    def _cameras_found(self, probes):
        if self._closing:
            return
        self.cameras = UI.rank_cameras(probes)
        self.cam_list.delete(0, "end")
        for c in self.cameras:
            self.cam_list.insert("end", c.label())
            if not c.usable:
                self.cam_list.itemconfig("end", fg="#445b5f")
        if not self.cameras:
            self.cam_note.config(text="no camera found on indices 0-9")
            self.preview.config(text="no camera", image="")
        else:
            want = self.cam_idx
            usable = [c for c in self.cameras if c.usable]
            if want is None or not any(c.index == want and c.usable for c in usable):
                want = usable[0].index if usable else None
            for row, c in enumerate(self.cameras):
                if c.index == want:
                    self.cam_list.selection_clear(0, "end")
                    self.cam_list.selection_set(row)
            self._open_camera(want)
        self._note()

    def _load_audio(self):
        try:
            import sounddevice as sd
            devs = list(sd.query_devices())
            din, dout = sd.default.device
        except Exception as e:
            self.in_list.delete(0, "end")
            self.in_list.insert("end", "audio unavailable (%s)" % type(e).__name__)
            self.out_list.delete(0, "end")
            self.out_list.insert("end", "audio unavailable (%s)" % type(e).__name__)
            return
        self.inputs = UI.audio_choices(devs, "input", din)
        self.outputs = UI.audio_choices(devs, "output", dout)
        for box, rows, want in ((self.in_list, self.inputs, self.in_dev),
                                (self.out_list, self.outputs, self.out_dev)):
            box.delete(0, "end")
            for row, d in enumerate(rows):
                box.insert("end", d.label())
                if d.index == want or (want is None and d.is_default):
                    box.selection_clear(0, "end")
                    box.selection_set(row)
        if self.in_list.curselection():
            self._pick_input()

    def _note(self):
        bits = []
        if self.cam_idx is not None:
            bits.append("camera %s" % self.cam_idx)
        if self.in_dev is not None:
            bits.append("mic %s" % self.in_dev)
        if self.out_dev is not None:
            bits.append("speaker %s" % self.out_dev)
        self.note.config(text="   ".join(bits) or "nothing selected yet")

    # -- camera preview ----------------------------------------------------
    def _pick_camera(self, _e=None):
        sel = self.cam_list.curselection()
        if not sel:
            return
        c = self.cameras[sel[0]]
        if not c.usable:
            self.cam_note.config(text="%s -- %s" % (c.name, c.detail), fg=AMBER)
            self._release_camera()
            self.preview.config(image="", text="no picture from %s" % c.name)
            return
        self._open_camera(c.index)

    def _open_camera(self, index):
        self._release_camera()
        if index is None or cv2 is None:
            return
        self.cam_idx = index
        try:
            import fnav
            with fnav._silence_cv2_stderr():
                cap = fnav.open_camera(index)
            if cap.isOpened():
                self._cap = cap
                self.cam_note.config(text="live preview -- this is what they will see",
                                     fg=LILY)
            else:
                cap.release()
                self.cam_note.config(text="camera %s did not open" % index, fg=AMBER)
        except Exception as e:
            self.cam_note.config(text="camera error: %s" % type(e).__name__, fg=ROSE)
        self._note()

    def _release_camera(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def _tick_preview(self):
        if self._closing:
            return
        if self._cap is not None and cv2 is not None:
            try:
                ok, frame = self._cap.read()
                if ok and frame is not None:
                    t = cv2.resize(frame, (PREVIEW_W, PREVIEW_H))
                    rgb = cv2.cvtColor(t, cv2.COLOR_BGR2RGB)
                    with open(self.tmp, "wb") as f:
                        f.write(b"P6\n%d %d\n255\n" % (PREVIEW_W, PREVIEW_H))
                        f.write(rgb.tobytes())
                    self._photo = tk.PhotoImage(file=self.tmp)
                    self.preview.config(image=self._photo, text="")
            except Exception:
                pass
        self.after(66, self._tick_preview)

    # -- microphone meter --------------------------------------------------
    def _pick_input(self, _e=None):
        sel = self.in_list.curselection()
        if not sel or not self.inputs:
            return
        self._open_input(self.inputs[sel[0]].index)

    def _open_input(self, index):
        self._release_input()
        self.in_dev = index
        if np is None:
            self.meter_note.config(text="numpy unavailable -- no meter")
            return
        try:
            import sounddevice as sd

            def cb(indata, frames, t, status):
                try:
                    x = np.asarray(indata, dtype="float32")
                    self._rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
                    self._peak = max(self._peak * 0.92, self._rms)
                except Exception:
                    self._rms = 0.0

            self._stream = sd.InputStream(device=index, channels=1, callback=cb,
                                          blocksize=1024)
            self._stream.start()
            self.meter_note.config(text="listening...", fg=MUTE)
        except Exception as e:
            self.meter_note.config(text="cannot open: %s" % type(e).__name__, fg=ROSE)
        self._note()

    def _release_input(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._rms = self._peak = 0.0

    def _tick_meter(self):
        if self._closing:
            return
        frac = UI.meter_fraction(self._rms)
        peak = UI.meter_fraction(self._peak)
        state = UI.meter_state(self._rms)
        colour = {"silent": MUTE, "quiet": AMBER, "good": REED, "hot": ROSE}[state]
        self.meter.delete("all")
        self.meter.create_rectangle(0, 0, METER_W, METER_H, fill=WATER, outline="")
        # segmented bar: a continuous bar reads as a progress bar, segments read as
        # a level meter
        segs = 28
        on = int(frac * segs)
        for i in range(segs):
            x0 = i * (METER_W / segs) + 1
            x1 = x0 + (METER_W / segs) - 2
            self.meter.create_rectangle(x0, 2, x1, METER_H - 2,
                                        fill=colour if i < on else "#16292d",
                                        outline="")
        px = peak * METER_W
        self.meter.create_line(px, 0, px, METER_H, fill=BONE)
        if self._stream is not None:
            self.meter_note.config(text=UI.meter_advice(state), fg=colour)
        self.after(60, self._tick_meter)

    # -- speaker -----------------------------------------------------------
    def _pick_output(self, _e=None):
        sel = self.out_list.curselection()
        if sel and self.outputs:
            self.out_dev = self.outputs[sel[0]].index
            self._note()

    def _test_tone(self):
        if np is None:
            self.out_note.config(text="numpy unavailable")
            return
        try:
            import sounddevice as sd
            rate = 44100
            t = np.arange(int(rate * 0.5)) / rate
            tone = 0.2 * np.sin(2 * math.pi * 440 * t).astype("float32")
            tone[:400] *= np.linspace(0, 1, 400)          # avoid a click
            tone[-400:] *= np.linspace(1, 0, 400)
            sd.play(tone, rate, device=self.out_dev)
            self.out_note.config(text="playing 440 Hz", fg=LILY)
        except Exception as e:
            self.out_note.config(text="cannot play: %s" % type(e).__name__, fg=ROSE)

    # -- save / close ------------------------------------------------------
    def _save(self):
        in_name = next((d.name for d in self.inputs if d.index == self.in_dev), None)
        out_name = next((d.name for d in self.outputs if d.index == self.out_dev), None)
        settings = {"cam": self.cam_idx, "in_dev": self.in_dev,
                    "out_dev": self.out_dev, "in_name": in_name, "out_name": out_name}
        try:
            # ControlPlane holds the bag this client loaded at startup, so naming
            # only the device fields here still writes a whole bag. Nothing merges
            # behind our back; the client simply knows its own settings.
            self.cp.save_settings(**settings)
            self.saved_note.config(text="saved to the pond", fg=LILY)
        except Exception as e:
            self.saved_note.config(text="save failed: %s" % type(e).__name__, fg=ROSE)
        try:
            self.on_save(settings)
        except Exception:
            pass

    def close(self):
        self._closing = True
        self._release_camera()
        self._release_input()
        try:
            self.destroy()
        except Exception:
            pass
        if self.on_closed:
            try:
                self.on_closed()
            except Exception:
                pass
