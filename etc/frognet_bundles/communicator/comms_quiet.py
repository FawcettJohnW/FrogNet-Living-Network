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
comms_quiet.py -- the quiet mode window: the conversation as text, while the camera
is blanked, the microphone is muted and the speaker is silent.

Paint only. The behaviour is comms_voice.QuietMode and is oracle-driven headless.

The window shows one thing: the call's shared transcript, which is not this window's
data. It is a set of rows every participant reads. Two lines look different because
they arrived differently -- what somebody typed, and what somebody's box heard -- and
that distinction is carried in the row, not inferred here.
"""
from __future__ import annotations

import tkinter as tk

WATER, WATER2, WATER3 = "#0a1417", "#0f1e22", "#132a2f"
REED, LILY, AMBER, ROSE = "#3fae6e", "#7fd1a0", "#f4b942", "#d9645a"
BONE, MUTE = "#e8efe9", "#6f8a82"
F_LABEL = ("TkFixedFont", 9, "bold")
F_SMALL = ("TkFixedFont", 9)
F_LINE = ("TkDefaultFont", 13)
F_BIG = ("TkFixedFont", 13, "bold")


class QuietWindow(tk.Toplevel):
    def __init__(self, master, quiet, on_close):
        super().__init__(master)
        self.quiet = quiet
        self._on_close = on_close
        self._closing = False
        self.title("Quiet mode -- read and type")
        self.configure(bg=WATER)
        self.geometry("640x620")

        head = tk.Frame(self, bg=WATER2)
        head.pack(fill="x")
        tk.Label(head, text="QUIET MODE", fg=AMBER, bg=WATER2,
                 font=F_LABEL).pack(side="left", padx=14, pady=10)
        tk.Label(head, text="camera blanked   mic muted   speaker silent",
                 fg=MUTE, bg=WATER2, font=F_SMALL).pack(side="left")

        self.log = tk.Text(self, bg=WATER, fg=BONE, bd=0, wrap="word",
                           font=F_LINE, state="disabled", padx=14, pady=10)
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("heard", foreground=LILY)
        self.log.tag_configure("typed", foreground=BONE)
        self.log.tag_configure("who", foreground=MUTE, font=F_SMALL)
        self.log.tag_configure("mark", foreground=MUTE, font=F_SMALL)

        entry = tk.Frame(self, bg=WATER2)
        entry.pack(fill="x")
        self.box = tk.Entry(entry, bg=WATER, fg=BONE, bd=0, insertbackground=BONE,
                            font=F_LINE)
        self.box.pack(side="left", fill="x", expand=True, padx=(12, 6), pady=12,
                      ipady=7)
        self.box.bind("<Return>", lambda e: self.send())
        tk.Button(entry, text="Say it", command=self.send, bg=REED, fg=WATER,
                  relief="flat", padx=18, pady=8, font=F_BIG).pack(side="left",
                                                                   padx=(0, 12))
        self.box.focus_set()

        foot = tk.Frame(self, bg=WATER2)
        foot.pack(fill="x")
        self.status = tk.Label(foot, text=quiet.status(), fg=MUTE, bg=WATER2,
                               font=F_SMALL, anchor="w", justify="left")
        self.status.pack(side="left", padx=14, pady=(0, 10))
        tk.Button(foot, text="Leave quiet mode", command=self.close, bg=WATER3,
                  fg=BONE, relief="flat", padx=14).pack(side="right", padx=12,
                                                        pady=(0, 10))

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(400, self._tick)

    def _tick(self):
        if self._closing:
            return
        for line in self.quiet.pump():
            self.append(line)
        self.after(400, self._tick)

    def append(self, line):
        kind = line.get("kind", "typed")
        self.log.config(state="normal")
        self.log.insert("end", "%s  " % line.get("from", "?"), "who")
        self.log.insert("end", "(heard)\n" if kind == "heard" else "(typed)\n", "mark")
        self.log.insert("end", "%s\n\n" % line.get("text", ""), kind)
        self.log.config(state="disabled")
        self.log.see("end")

    def send(self):
        text = self.box.get().strip()
        if not text:
            return
        self.box.delete(0, "end")
        self.quiet.send(text)

    def close(self):
        self._closing = True
        try:
            self._on_close()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
