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
"""communicator_live.py -- FrogNet Communicator, native desktop, on the PROVEN call method.

Same call path as before -- this rebuild is the SHELL only, not the media stack:

  presence / roster / call setup / chat -> comms_control.ControlPlane  (BLDC/UnREST)
  audio + video over the relay          -> fnav.Call  (FNWP-1 data plane)
  screens / ladder / link instrument    -> comms_ui  (pure model, oracle-driven)

WHAT CHANGED AND WHY

The previous shell had one fixed three-column layout that never changed. Roster,
open calls, stats, the video stage, the demo sliders and chat were all on screen at
once whether you were idle or mid-call, so there was nothing to navigate: no lobby,
no ringing screen, no end-of-call. The controls that make the demonstration -- the
uplink throttle and jitter -- silently did nothing when there was no call, while
their labels confidently reported the value they had not applied. And the ladder
walk from 720p down to voice, which is the entire point of the demonstration, was a
13px monospace block in a 240px sidebar that nobody past the second row could read.

This version is four screens, each a different instrument:

  LOBBY    the flock, the calls in progress, preflight (prove your camera and mic
           work while nobody is watching), lobby chat, and the link armed ahead of
           time. This is the front door for a group who set this up for themselves.
  RINGING  a real screen, not a stray Toplevel. Declining leaves the call tuple,
           which is how the caller finds out -- shared memory, not a message.
  CALL     the ladder is the headline: full-width rung readout and an L7..L3 track
           a room can read, over the tiles, with the link instrument beneath it.
  ENDED    says WHY the call ended and offers the way back. The old shell dropped
           a hangup and a dead relay into the same silent idle state.

Run:
    python3 communicator_live.py --name Gorp --cam 1 --no-audio
    python3 communicator_live.py --name Alice --in 0 --codec h264 --serve
Flags mirror comms.py:  --in/--out <dev>  --cam N  --fps N  --codec vp8|h264|h264hw
  --relay HOST:PORT  --serve (host the relay in-process)  --no-video  --no-audio
"""
from __future__ import annotations
import argparse, logging, logging.handlers, os, queue, subprocess, sys
import tempfile, threading, time, uuid

import tkinter as tk
from tkinter import ttk

import comms_brand
import comms_control as CC
import comms_diag
import comms_quiet
import comms_setup
import comms_voice
import comms_ui as UI
import fnav
import sotf_ladder as L

try:
    import cv2
except Exception:
    cv2 = None

HEARTBEAT_S = 5
MAX_TILES = 4

# -- palette -----------------------------------------------------------------
WATER, WATER2, WATER3 = "#0a1417", "#0f1e22", "#132a2f"
REED, LILY, AMBER, ROSE = "#3fae6e", "#7fd1a0", "#f4b942", "#d9645a"
BONE, MUTE, LINE, STAGE = "#e8efe9", "#6f8a82", "#22383d", "#06100f"

# -- type scale: the ladder readout is meant to be legible across a room ------
F_HERO = ("TkFixedFont", 30, "bold")     # the rung, in a call
F_BIG = ("TkFixedFont", 15, "bold")
F_MED = ("TkDefaultFont", 11)
F_SMALL = ("TkFixedFont", 9)
F_LABEL = ("TkFixedFont", 9, "bold")
F_MONO = ("TkFixedFont", 11)

LOBBY_SCOPE = "lobby"                     # chat that exists without a call


def _log_path():
    """Where the log goes, per platform. %LOCALAPPDATA% on Windows, XDG state dir
    otherwise, falling back to the temp dir if neither is writable."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        d = os.path.join(base, "FrogNetCommunicator")
    else:
        base = (os.environ.get("XDG_STATE_HOME")
                or os.path.join(os.path.expanduser("~"), ".local", "state"))
        d = os.path.join(base, "frognet")
    try:
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "communicator.log")
    except Exception:
        return os.path.join(tempfile.gettempdir(), "frognet-communicator.log")


def setup_logging(name, verbose=False):
    """[COMMUNICATOR_LOG_V1] A log file, because there wasn't one.

    Everything this client knew went to stdout and stderr, which on Windows means a
    console window that closes with the app -- so every question of the form "what did
    it say when it did that" needed the failure reproduced with a terminal open. The
    interesting events here are exactly the ones that are hard to catch live: who was
    in the flock at the moment you pressed call, what the ladder did under a squeeze,
    which device the camera probe opened, why a write was refused.

    Rotating, 1 MB x 3, so it cannot fill a Pi's card. Warnings and above ALSO go to
    stderr so a terminal still shows trouble.
    """
    path = _log_path()
    log = logging.getLogger("frognet.communicator")
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.handlers[:] = []
    try:
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=1 << 20,
                                                  backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%dT%H:%M:%S"))
        log.addHandler(fh)
    except Exception as e:
        print("warning: cannot open log file %s: %s" % (path, e))
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.addHandler(sh)
    log.info("=" * 62)
    log.info("communicator start name=%s pid=%d python=%s platform=%s",
             name, os.getpid(), sys.version.split()[0], sys.platform)
    log.info("log file: %s", path)
    return log


LOG = logging.getLogger("frognet.communicator")


def _ppm_bytes(bgr, w, h):
    """Encode a decoded frame as P6 PPM. Tk reads PPM natively, so no PIL needed.

    [TILE_TAKES_WHAT_IT_IS_GIVEN_V1] The array is whatever the decoder produced,
    not whatever this function hoped for. It assumed three channels and called
    cvtColor(BGR2RGB) unconditionally; a single-channel frame raises there, the
    exception is caught by _fill's try, and the tile silently keeps the previous
    picture -- which reads as "the display stopped updating" rather than as an
    error, and was reported exactly that way for the two grayscale geometries.

    Two channels' worth of shape is one line to handle and removes a whole class
    of silent blank. A renderer must not have opinions about the shape of what
    it is handed.
    """
    t = cv2.resize(bgr, (max(1, w), max(1, h)))
    if t.ndim == 2:                                   # single-channel (gray)
        rgb = cv2.cvtColor(t, cv2.COLOR_GRAY2RGB)
    elif t.shape[2] == 4:                             # BGRA
        rgb = cv2.cvtColor(t, cv2.COLOR_BGRA2RGB)
    else:
        rgb = cv2.cvtColor(t, cv2.COLOR_BGR2RGB)
    return b"P6\n%d %d\n255\n" % (t.shape[1], t.shape[0]) + rgb.tobytes()


def _photo(bgr, w, h, path):
    """Render one tile.

    MEASURED, not assumed. This rebuild originally replaced the previous shell's
    write-a-.ppm-and-reload-it path with an in-memory tk.PhotoImage(data=...),
    on the reasoning that a disk round trip per tile per frame is obviously worse.
    Both halves of that were wrong. On Tk 8.6:

      tk.PhotoImage(data=base64(ppm))  raises "couldn't recognize image data"
                                       (base64 PPM is not accepted; raw bytes are)
      tk.PhotoImage(data=<raw ppm>)    3.13 ms per 480x270 tile
      write .ppm + PhotoImage(file=)   0.79 ms per 480x270 tile

    The file path is ~4x FASTER, because data= pushes the whole byte string through
    the Tcl interpreter while file= is read directly by Tk. At 4 tiles on an 80ms
    tick that is 3ms of the budget instead of 13ms. So the disk path stays, with
    one reused path per tile slot. Re-measure if the target box has a slow /tmp.
    """
    with open(path, "wb") as f:
        f.write(_ppm_bytes(bgr, w, h))
    return tk.PhotoImage(file=path)


class App(tk.Tk):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.me_id = args.id or "%s-%s" % (args.name.lower(), uuid.uuid4().hex[:4])
        # [FOUR_TUPLES_V1] The call tuples are keyed on the NAME. me_id is a
        # per-process handle; passing it where a name is expected matches
        # nothing and silently fails to exclude ME from a member list.
        self.me_name = args.name
        caps = {"cam": not args.no_video, "mic": not args.no_audio,
                "no_video": args.no_video, "no_audio": args.no_audio}
        relay = None
        if args.relay:
            rh, _, rp = args.relay.partition(":")
            relay = (rh, int(rp or 9000))
        self.cp = CC.ControlPlane(self.me_id, args.name, caps=caps, relay=relay)
        # [MEDIASPEED_V1] Previous runs of this client left MediaSpeed rows under
        # their own me_id. The media host keys caps by ADDRESS, so those collapse
        # onto one key and a stale one can win -- the slider then appears dead.
        # Clear ours before publishing anything new.
        self.cp.clear_stale_media_speeds()

        # -- the model: everything the chrome is derived from -----------------
        self.screen = UI.Screen()
        self.rings = UI.RingTracker()
        # [CODEC_DEFAULT_IS_AUTO_V1] This translated "auto" INTO "vp8" -- so the
        # default command line asked for the probe, the probe chose H.264/libx264 and
        # announced it, and _apply_pending then stamped software VP8 over it before the
        # first frame. VP8 software at 720p is several times the CPU of x264: measured
        # on hardware, 5-19 fps where the box should manage its full rate. auto means
        # auto.
        self.pending = UI.PendingControls(codec=args.codec)
        # The link condition is one fact about the CALL, not a private knob: whoever
        # adjusts it adjusts the call, and every participant applies it to its own
        # uplink. self.link is this node's converged view of that fact.
        self.link = UI.SharedLink(self.me_id)
        self.history = UI.LinkHistory()
        self.diag = None
        self.quiet = None
        self.quiet_win = None

        self.call = None
        self.call_thread = None
        self._stop = threading.Event()
        self._q = queue.Queue()
        self._photos = {}
        self._names = {}
        self._calls = []
        self._preview_photo = None
        self._tmp = {i: os.path.join(tempfile.gettempdir(),
                     "fncomm_%d_%d.ppm" % (os.getpid(), i))
                     for i in range(MAX_TILES + 1)}
        self._preflight = {"cam": not args.no_video, "mic": not args.no_audio,
                           "tested": False, "note": ""}
        self._devices = {"cam": args.cam, "in_dev": args.in_dev,
                         "out_dev": args.out_dev, "in_name": None, "out_name": None}
        self.setup_win = None
        self._pv_cap = None          # lobby self-preview capture, released before a call
        # [CAMERA_CLAIM_V1] One owner at a time. _start_call released the preview and
        # THEN did a network round trip (cp.start_call) before the mode changed --
        # _tick_preview fires every 80ms, saw mode==LOBBY and call is None, and
        # reopened the camera in that window. fnav._probe then found it busy, set
        # have_cam=False, and _video_tx was never spawned: the call connected and no
        # video was ever sent. The claim is taken BEFORE the release and held until
        # the call is over.
        self._cam_claim = False
        self._pv_opening = False     # a preview open is in flight on a worker
        self._pv_failed = None       # index that failed; retried only on a change
        self._ask_after = None       # scheduled "who are you calling?"
        self._ask_win = None

        if args.serve:
            self._serve_relay()

        self.withdraw()                       # keep the empty shell off screen
        splash = None if args.no_splash else comms_brand.splash(self, args.name)
        if splash is not None:
            splash.open_doors()
            splash.step("finding the pond")

        self.title("FrogNet Communicator -- %s" % args.name)
        self.configure(bg=WATER)
        self.geometry("1180x760")
        self.minsize(940, 620)
        if splash is not None:
            splash.step("laying out the lillypad")
        self._build_ui()
        self._apply_mode(force=True)
        if splash is not None:
            splash.step("reading your settings from the pond")

        # The settings read is a round trip to the elected data host. Do it while the
        # splash is still up and say so, rather than opening a window that silently
        # changes its own camera a second later.
        # If the user clicked through the splash they asked for the application, not
        # for a wait on the elected data host. Read the settings on a thread in that
        # case; the window opens now and adopts them when they land.
        if splash is not None and getattr(splash, "skipped", False):
            threading.Thread(target=self._load_settings, daemon=True).start()
        else:
            self._load_settings()
        if splash is not None:
            splash.done()
        self.deiconify()
        threading.Thread(target=self._load_settings_later, daemon=True).start()
        threading.Thread(target=self._heartbeat, daemon=True).start()
        threading.Thread(target=self._poll, daemon=True).start()
        self.after(100, self._drain_q)
        self.after(80, self._tick_video)
        self.after(300, self._tick_preview)
        self.protocol("WM_DELETE_WINDOW", self._quit)
        # [SAY_WHICH_BYTES_ARE_RUNNING_V1] Three runs were argued about on the
        # basis of "that must be the old code". Nobody should ever have to infer
        # which build is running from the shape of its log lines. This prints a
        # content hash of the files that actually got imported -- not a version
        # constant, which is a claim, but a fingerprint of the bytes on disk.
        # [A_SILENT_EXIT_IS_A_BUG_REPORT_V1] Tk routes callback exceptions here.
        # The default implementation prints, but anything that overrides or
        # wraps it can swallow them -- and an exception inside an after() or a
        # bind() is exactly where a window dies without a word.
        self.report_callback_exception = self._callback_failed
        self._exit_reason = None
        _print_build_id()
        print("[communicator] window up -- %s (pid %d)" % (args.name, os.getpid()),
              flush=True)

    def _callback_failed(self, exc, val, tb):
        """[A_SILENT_EXIT_IS_A_BUG_REPORT_V1] Name the callback that raised.

        Printed, not logged: the log file is not where somebody watching a
        console is looking, and a window that vanishes needs its reason on the
        screen it vanished from.
        """
        import traceback as _tb
        print("[communicator] CALLBACK RAISED %s: %s" % (exc.__name__, val),
              flush=True)
        _tb.print_exception(exc, val, tb)

    def _serve_relay(self):
        try:
            h, p = fnav.A._hostport(self.args.serve if isinstance(self.args.serve, str)
                                    else "0.0.0.0:9000")
            r = fnav.Relay(h, p)
            threading.Thread(target=r.serve, daemon=True).start()
            print("[serve] relay on %s:%d" % (h, p))
        except Exception as e:
            print("[serve] failed: %s" % e)

    # =====================================================================
    # chrome
    # =====================================================================
    def _build_ui(self):
        self._build_header()
        body = tk.Frame(self, bg=LINE)
        body.pack(fill="both", expand=True)
        self.left = self._build_left_rail(body)
        self.center = tk.Frame(body, bg=WATER)
        self.center.pack(side="left", fill="both", expand=True)
        self.right = self._build_right_rail(body)
        self._build_ladder(self.center)
        self._build_center_stack(self.center)
        self._build_link_bar(self.center)

    def _build_header(self):
        bar = tk.Frame(self, bg=WATER2)
        bar.pack(fill="x")
        self._mark = comms_brand.logo(32)
        if self._mark is not None:
            tk.Label(bar, image=self._mark, bg=WATER2).pack(side="left", padx=(12, 8),
                                                            pady=6)
        tk.Label(bar, text="frognet", fg=LILY, bg=WATER2,
                 font=("TkFixedFont", 15, "bold")).pack(side="left", padx=(0, 4), pady=12)
        tk.Label(bar, text="communicator", fg=MUTE, bg=WATER2,
                 font=("TkFixedFont", 15)).pack(side="left")
        self.mode_badge = tk.Label(bar, text="", fg=WATER, bg=REED, font=F_LABEL,
                                   padx=8, pady=2)
        self.mode_badge.pack(side="left", padx=16)
        self.status = tk.Label(bar, text="", fg=MUTE, bg=WATER2, font=F_SMALL)
        self.status.pack(side="right", padx=14)
        self.link_badge = tk.Label(bar, text="", fg=WATER, bg=AMBER, font=F_LABEL,
                                   padx=8, pady=2)
        # packed/forgotten by _refresh_link_badge -- only visible when armed
        self.who_lbl = tk.Label(bar, text="you are %s" % self.args.name, fg=BONE,
                                bg=WATER2, font=F_SMALL)
        self.who_lbl.pack(side="right", padx=8)

    # -- left rail: the flock and the calls -------------------------------
    def _build_left_rail(self, parent):
        rail = tk.Frame(parent, bg=WATER2, width=260)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)

        tk.Label(rail, text="THE FLOCK", fg=MUTE, bg=WATER2,
                 font=F_LABEL).pack(anchor="w", padx=14, pady=(14, 2))
        self.flock_count = tk.Label(rail, text="looking...", fg=MUTE, bg=WATER2,
                                    font=F_SMALL)
        self.flock_count.pack(anchor="w", padx=14)

        # The old rail packed roster, calls and stats into one 260px strip with no
        # scrolling, so a real flock pushed everything below it off the window with
        # no indication anything was there. The roster scrolls now.
        wrap = tk.Frame(rail, bg=WATER2)
        wrap.pack(fill="both", expand=True, padx=(8, 0), pady=(6, 0))
        self.flock_canvas = tk.Canvas(wrap, bg=WATER2, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.flock_canvas.yview)
        self.flock_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.flock_canvas.pack(side="left", fill="both", expand=True)
        self.roster_box = tk.Frame(self.flock_canvas, bg=WATER2)
        self._flock_win = self.flock_canvas.create_window(
            (0, 0), window=self.roster_box, anchor="nw")
        self.roster_box.bind(
            "<Configure>",
            lambda e: self.flock_canvas.configure(
                scrollregion=self.flock_canvas.bbox("all")))
        self.flock_canvas.bind(
            "<Configure>",
            lambda e: self.flock_canvas.itemconfig(self._flock_win, width=e.width))

        tk.Label(rail, text="CALLS IN PROGRESS", fg=MUTE, bg=WATER2,
                 font=F_LABEL).pack(anchor="w", padx=14, pady=(12, 4))
        self.calls_box = tk.Frame(rail, bg=WATER2)
        self.calls_box.pack(fill="x", pady=(0, 12))
        return rail

    # -- right rail: chat, and stats behind a disclosure ------------------
    def _build_right_rail(self, parent):
        rail = tk.Frame(parent, bg=WATER2, width=280)
        rail.pack(side="right", fill="y")
        rail.pack_propagate(False)

        self.chat_title = tk.Label(rail, text="LOBBY CHAT", fg=MUTE, bg=WATER2,
                                   font=F_LABEL)
        self.chat_title.pack(anchor="w", padx=14, pady=(14, 4))
        self.chat_log = tk.Text(rail, bg=WATER, fg=BONE, bd=0, wrap="word",
                                font=F_MED, state="disabled", height=10)
        self.chat_log.pack(fill="both", expand=True, padx=10)
        cf = tk.Frame(rail, bg=WATER2)
        cf.pack(fill="x", pady=8, padx=10)
        self.chat_in = tk.Entry(cf, bg=WATER, fg=BONE, bd=0, insertbackground=BONE)
        self.chat_in.pack(side="left", fill="x", expand=True, ipady=5)
        self.chat_in.bind("<Return>", lambda e: self._send_chat())
        tk.Button(cf, text="Send", command=self._send_chat, bg=REED, fg=WATER,
                  relief="flat").pack(side="left", padx=(6, 0))

        # Stats are diagnostics, not the headline -- collapsed by default so the
        # ladder is what the eye lands on.
        self.stats_open = tk.BooleanVar(value=False)
        self.stats_btn = tk.Button(rail, text="+ wire detail", command=self._toggle_stats,
                                   bg=WATER2, fg=MUTE, relief="flat", anchor="w")
        self.stats_btn.pack(fill="x", padx=10, pady=(0, 2))
        self.stats_panel = tk.Label(rail, text="", fg=BONE, bg=WATER, justify="left",
                                    anchor="nw", font=F_SMALL)
        return rail

    def _toggle_stats(self):
        self.stats_open.set(not self.stats_open.get())
        if self.stats_open.get():
            self.stats_panel.pack(fill="x", padx=10, pady=(0, 10))
            self.stats_btn.config(text="- wire detail")
        else:
            self.stats_panel.pack_forget()
            self.stats_btn.config(text="+ wire detail")

    # -- the ladder headline (call screen) --------------------------------
    def _build_ladder(self, parent):
        self.ladder_bar = tk.Frame(parent, bg=WATER3)
        head = tk.Frame(self.ladder_bar, bg=WATER3)
        head.pack(fill="x", padx=16, pady=(12, 2))
        self.rung_lbl = tk.Label(head, text="--", fg=LILY, bg=WATER3, font=F_HERO)
        self.rung_lbl.pack(side="left")
        cap = tk.Frame(head, bg=WATER3)
        cap.pack(side="left", padx=14)
        self.rung_caption = tk.Label(cap, text="", fg=BONE, bg=WATER3, font=F_BIG,
                                     anchor="w")
        self.rung_caption.pack(anchor="w")
        self.rung_sub = tk.Label(cap, text="", fg=MUTE, bg=WATER3, font=F_SMALL,
                                 anchor="w")
        self.rung_sub.pack(anchor="w")
        self.hang_btn = tk.Button(head, text="Hang up", command=self._hang, bg=ROSE,
                                  fg="#fff", relief="flat", padx=16, pady=8)
        self.hang_btn.pack(side="right")

        # The track: one cell per rung, richest first. Colour carries the state, so
        # the walk down under load is visible from across a room.
        track = tk.Frame(self.ladder_bar, bg=WATER3)
        track.pack(fill="x", padx=16, pady=(6, 12))
        self.rung_cells = {}
        for idx in range(L.MAX_IDX, 2, -1):
            cell = tk.Frame(track, bg=WATER2, bd=0)
            cell.pack(side="left", fill="x", expand=True, padx=2)
            code = tk.Label(cell, text=L.code(idx), bg=WATER2, fg=MUTE, font=F_BIG)
            code.pack(fill="x", pady=(6, 0))
            nm = tk.Label(cell, text=L.name(idx), bg=WATER2, fg=MUTE, font=F_SMALL)
            nm.pack(fill="x", pady=(0, 6))
            self.rung_cells[idx] = (cell, code, nm)

    def _paint_ladder(self, send_idx, ceiling_idx, allowed):
        cells = UI.ladder_cells(send_idx, ceiling_idx, allowed)
        for c in cells:
            widget = self.rung_cells.get(c["idx"])
            if not widget:
                continue
            frame, code, nm = widget
            if c["state"] == UI.ACTIVE:
                bg, fg, sub = REED, WATER, WATER
            elif c["state"] == UI.SHED:
                bg, fg, sub = WATER2, AMBER, MUTE
            elif c["state"] == UI.BLOCKED:
                bg, fg, sub = WATER2, "#33484c", "#33484c"
            else:
                bg, fg, sub = WATER2, LILY, MUTE
            frame.config(bg=bg)
            code.config(bg=bg, fg=fg)
            nm.config(bg=bg, fg=sub)
        self.rung_lbl.config(
            text=L.code(send_idx) if send_idx is not None else "--",
            fg=REED if send_idx is not None and send_idx >= 5 else AMBER)
        self.rung_caption.config(text=UI.rung_caption(send_idx, fnav.RUNG_VIDEO))

    # -- centre stack: one screen visible at a time -----------------------
    def _build_center_stack(self, parent):
        self.stack = tk.Frame(parent, bg=WATER)
        self.stack.pack(fill="both", expand=True)
        self._build_lobby_card(self.stack)
        self._build_stage(self.stack)
        self._build_ring_card(self.stack)
        self._build_ended_card(self.stack)

    def _build_lobby_card(self, parent):
        self.lobby_card = tk.Frame(parent, bg=WATER)
        pad = tk.Frame(self.lobby_card, bg=WATER)
        pad.pack(expand=True, fill="both", padx=28, pady=20)

        tk.Label(pad, text="Ready when you are", fg=BONE, bg=WATER,
                 font=("TkDefaultFont", 20, "bold")).pack(anchor="w")
        self.lobby_sub = tk.Label(pad, text="", fg=MUTE, bg=WATER, font=F_MED,
                                  justify="left", anchor="w")
        self.lobby_sub.pack(anchor="w", pady=(2, 16))

        # PREFLIGHT. The likeliest demo failure is a wrong camera index, and the old
        # shell's only remedy was to quit and relaunch from a terminal with a
        # different --cam. Test it here, before anyone is watching.
        card = tk.Frame(pad, bg=WATER2)
        card.pack(fill="x", pady=(0, 16))
        tk.Label(card, text="PREFLIGHT", fg=MUTE, bg=WATER2,
                 font=F_LABEL).pack(anchor="w", padx=14, pady=(12, 6))
        row = tk.Frame(card, bg=WATER2)
        row.pack(fill="x", padx=14, pady=(0, 12))
        self.preview_lbl = tk.Label(row, bg=STAGE, fg=MUTE, width=30, height=8,
                                    text="starting camera...", font=F_SMALL)
        self.preview_lbl.pack(side="left")
        info = tk.Frame(row, bg=WATER2)
        info.pack(side="left", fill="both", expand=True, padx=16)
        cam_row = tk.Frame(info, bg=WATER2)
        cam_row.pack(anchor="w", pady=(0, 8))
        tk.Button(cam_row, text="Camera and sound...", command=self._open_setup,
                  bg=WATER3, fg=BONE, relief="flat", padx=14,
                  pady=7).pack(side="left")
        tk.Button(cam_row, text="Diagnostics", command=self._open_diag, bg=WATER3,
                  fg=MUTE, relief="flat", padx=12, pady=7).pack(side="left", padx=8)
        self.verdict_lbl = tk.Label(info, text="", fg=LILY, bg=WATER2, font=F_MONO,
                                    justify="left", anchor="w")
        self.verdict_lbl.pack(anchor="w")
        self.devices_lbl = tk.Label(info, text="", fg=MUTE, bg=WATER2, font=F_SMALL,
                                    justify="left", anchor="w")
        self.devices_lbl.pack(anchor="w", pady=(4, 0))

        self._lobby_mark = comms_brand.logo(256)
        if self._lobby_mark is not None:
            tk.Label(pad, image=self._lobby_mark, bg=WATER).pack(side="bottom",
                                                                 anchor="e",
                                                                 pady=(12, 0))

        act = tk.Frame(pad, bg=WATER)
        act.pack(fill="x")
        self.call_btn = tk.Button(act, text="Start a call", command=self._begin_call,
                                  bg=REED, fg=WATER, relief="flat", padx=22, pady=12,
                                  font=F_BIG)
        self.call_btn.pack(side="left")
        tk.Button(act, text="Games", command=self._games_popup, bg=WATER2, fg=LILY,
                  relief="flat", padx=16, pady=12).pack(side="left", padx=10)
        self.relay_lbl = tk.Label(act, text="", fg=MUTE, bg=WATER, font=F_SMALL)
        self.relay_lbl.pack(side="left", padx=14)

    def _build_stage(self, parent):
        self.stage = tk.Frame(parent, bg=STAGE)
        self.tiles = []
        for _ in range(MAX_TILES):
            self.tiles.append(tk.Label(self.stage, bg=STAGE, fg=MUTE, text=""))
        ctl = tk.Frame(self.stage, bg=WATER2)
        self.incall_ctl = ctl
        tk.Label(ctl, text="codec", fg=MUTE, bg=WATER2,
                 font=F_SMALL).pack(side="left", padx=(12, 4), pady=8)
        self.codec_var = tk.StringVar(value=self.pending.codec)
        # [CODEC_DEFAULT_IS_AUTO_V1] "auto" was not in the menu at all, so the probe's
        # choice could never be asked for -- only overridden.
        ttk.OptionMenu(ctl, self.codec_var, self.pending.codec, "auto", "vp8", "h264",
                       "h264hw", command=self._set_codec).pack(side="left")
        tk.Label(ctl, text="quality", fg=MUTE, bg=WATER2,
                 font=F_SMALL).pack(side="left", padx=(14, 4))
        self.qual_var = tk.StringVar(value=self.pending.quality)
        # [BULLFROG_V1] The menu is the second place a rung has to be named --
        # QUALITY_CAP maps the label to an index, and this lists the labels. A
        # rung added to only one of them is either unreachable or a KeyError.
        ttk.OptionMenu(ctl, self.qual_var, self.pending.quality, "Auto",
                       "1080p", "720p", "480p", "360p",
                       command=self._set_quality).pack(side="left")
        # [ASPECT_IS_CHOSEN_V1] Frame shape above L5. Below L5 the ladder goes
        # 4:3 either way -- see [CONSTRAINED_GOES_4_3_V1] -- so this is not a
        # promise about the whole ladder and is labelled for what it governs.
        tk.Label(ctl, text="shape", bg=WATER2, fg=LILY,
                 font=F_SMALL).pack(side="left", padx=(14, 4))
        self.aspect_var = tk.StringVar(value=self.pending.aspect)
        ttk.OptionMenu(ctl, self.aspect_var, self.pending.aspect,
                       "16:9", "4:3",
                       command=self._set_aspect).pack(side="left")
        tk.Button(ctl, text="Invite", command=self._ask_who, bg=REED, fg=WATER,
                  relief="flat", padx=12).pack(side="left", padx=(14, 6))
        tk.Button(ctl, text="Games", command=self._games_popup, bg=WATER3, fg=LILY,
                  relief="flat", padx=12).pack(side="left")
        tk.Button(ctl, text="Diagnostics", command=self._open_diag, bg=WATER3,
                  fg=MUTE, relief="flat", padx=12).pack(side="left")
        self.quiet_btn = tk.Button(ctl, text="Quiet mode", command=self._toggle_quiet,
                                   bg=AMBER, fg=WATER, relief="flat", padx=12)
        self.quiet_btn.pack(side="left", padx=8)
        self.wire_lbl = tk.Label(ctl, text="", fg=MUTE, bg=WATER2, font=F_MONO)
        self.wire_lbl.pack(side="right", padx=14)
        # [MEDIAHOLD_IS_MEMORY_V1] What the media host is holding, read from the
        # MediaHold tuples. This is the only place a sender can learn that the far
        # end is stuck: its own uplink is clean while the viewer watches stills.
        self.hold_lbl = tk.Label(ctl, text="", fg=MUTE, bg=WATER2, font=F_SMALL)
        self.hold_lbl.pack(side="right", padx=14)

    def _build_ring_card(self, parent):
        self.ring_card = tk.Frame(parent, bg=WATER)
        box = tk.Frame(self.ring_card, bg=WATER2)
        box.place(relx=0.5, rely=0.42, anchor="center")
        tk.Label(box, text="incoming call", fg=MUTE, bg=WATER2,
                 font=F_LABEL).pack(pady=(26, 6), padx=60)
        self.ring_who = tk.Label(box, text="", fg=LILY, bg=WATER2,
                                 font=("TkDefaultFont", 24, "bold"))
        self.ring_who.pack(padx=60)
        self.ring_sub = tk.Label(box, text="", fg=MUTE, bg=WATER2, font=F_SMALL)
        self.ring_sub.pack(pady=(4, 18))
        btns = tk.Frame(box, bg=WATER2)
        btns.pack(pady=(0, 26))
        tk.Button(btns, text="Answer", command=self._accept, bg=REED, fg=WATER,
                  relief="flat", padx=26, pady=10, font=F_BIG).pack(side="left", padx=8)
        tk.Button(btns, text="Decline", command=self._decline, bg=ROSE, fg="#fff",
                  relief="flat", padx=26, pady=10, font=F_BIG).pack(side="left", padx=8)

    def _build_ended_card(self, parent):
        self.ended_card = tk.Frame(parent, bg=WATER)
        box = tk.Frame(self.ended_card, bg=WATER2)
        box.place(relx=0.5, rely=0.42, anchor="center")
        self._ended_mark = comms_brand.logo(48)
        if self._ended_mark is not None:
            tk.Label(box, image=self._ended_mark, bg=WATER2).pack(pady=(22, 0))
        tk.Label(box, text="call ended", fg=MUTE, bg=WATER2,
                 font=F_LABEL).pack(pady=(8, 8), padx=54)
        self.ended_why = tk.Label(box, text="", fg=BONE, bg=WATER2,
                                  font=("TkDefaultFont", 15))
        self.ended_why.pack(padx=54)
        self.ended_stats = tk.Label(box, text="", fg=MUTE, bg=WATER2, font=F_SMALL,
                                    justify="center")
        self.ended_stats.pack(pady=(8, 18))
        b = tk.Frame(box, bg=WATER2)
        b.pack(pady=(0, 26))
        tk.Button(b, text="Back to the flock", command=self._back_to_lobby, bg=REED,
                  fg=WATER, relief="flat", padx=20, pady=9).pack(side="left", padx=6)

    # -- link instrument ---------------------------------------------------
    def _build_link_bar(self, parent):
        self.link_bar = tk.Frame(parent, bg=WATER2)
        head = tk.Frame(self.link_bar, bg=WATER2)
        head.pack(fill="x", padx=14, pady=(8, 0))
        tk.Label(head, text="LINK CONDITIONS", fg=AMBER, bg=WATER2,
                 font=F_LABEL).pack(side="left")
        self.link_state = tk.Label(head, text="", fg=BONE, bg=WATER2, font=F_SMALL)
        self.link_state.pack(side="left", padx=12)
        self.predict_lbl = tk.Label(head, text="", fg=LILY, bg=WATER2, font=F_MONO)
        self.predict_lbl.pack(side="right")

        row = tk.Frame(self.link_bar, bg=WATER2)
        row.pack(fill="x", padx=14, pady=(6, 10))
        # Named presets, because a slider is unusable in front of a room: you cannot
        # hit a number while talking and the audience cannot read where you put it.
        self.preset_btns = []
        for p in UI.PRESETS:
            b = tk.Button(row, text=p.name, relief="flat", padx=14, pady=7,
                          bg=WATER3, fg=BONE,
                          command=lambda pp=p: self._apply_preset(pp))
            b.pack(side="left", padx=(0, 8))
            self.preset_btns.append((b, p))
        tk.Label(row, text="custom", fg=MUTE, bg=WATER2,
                 font=F_SMALL).pack(side="left", padx=(14, 6))
        self.bps_var = tk.DoubleVar(value=2000)
        ttk.Scale(row, from_=50, to=2000, variable=self.bps_var, length=150,
                  command=self._set_custom).pack(side="left")
        tk.Label(row, text="jitter", fg=MUTE, bg=WATER2,
                 font=F_SMALL).pack(side="left", padx=(12, 6))
        self.jit_var = tk.DoubleVar(value=0)
        ttk.Scale(row, from_=0, to=500, variable=self.jit_var, length=110,
                  command=self._set_custom).pack(side="left")

    # =====================================================================
    # navigation: the chrome follows the mode
    # =====================================================================
    def _apply_mode(self, force=False):
        m = self.screen.mode
        if not force and m == getattr(self, "_painted_mode", None):
            return
        self._painted_mode = m
        s = self.screen

        for w in (self.lobby_card, self.stage, self.ring_card, self.ended_card):
            w.pack_forget()
        for w in (self.ladder_bar, self.link_bar):
            w.pack_forget()
        self.incall_ctl.pack_forget()

        if s.shows("ladder"):
            self.ladder_bar.pack(fill="x", before=self.stack)
        if m == UI.LOBBY:
            self.lobby_card.pack(fill="both", expand=True)
        elif m == UI.CALL:
            self.stage.pack(fill="both", expand=True)
            self.incall_ctl.pack(fill="x", side="bottom")
        elif m == UI.RINGING:
            self.ring_card.pack(fill="both", expand=True)
        elif m == UI.ENDED:
            self.ended_card.pack(fill="both", expand=True)
        if s.shows("link"):
            self.link_bar.pack(fill="x", side="bottom")

        # rails: the flock is for finding people, so it is hidden in a call where
        # the tiles ARE the people.
        if s.shows("flock"):
            self.left.pack(side="left", fill="y", before=self.center)
        else:
            self.left.pack_forget()

        badge = {UI.LOBBY: ("LOBBY", REED), UI.RINGING: ("RINGING", AMBER),
                 UI.CALL: ("IN CALL", REED), UI.ENDED: ("ENDED", ROSE)}[m]
        self.mode_badge.config(text=badge[0], bg=badge[1])
        self.chat_title.config(text="CALL CHAT" if m == UI.CALL else "LOBBY CHAT")
        self._last_chat_sig = None          # rescope: repaint chat for the new scope
        self._refresh_lobby_text()
        self._refresh_link_badge()

    def _refresh_lobby_text(self):
        n = max(0, len(self._names) - 1)
        others = "no one else is here yet" if n == 0 else (
            "%d other%s on the pond" % (n, "" if n == 1 else "s"))
        self.lobby_sub.config(
            text="%s. Pick someone from the flock to call them, or start a call and\n"
                 "let them join from their own Communicator." % others)
        # The lobby SHOWS the media host; it does not dial it. _resolve_media raises
        # NoMediaHost when nothing is elected, which is right for starting a call and
        # wrong here -- unhandled it took the whole window down before it opened.
        try:
            host, port = self.cp._resolve_media()
            self.relay_lbl.config(text="media host %s:%s" % (host, port), fg=MUTE)
        except CC.NoMediaHost:
            self.relay_lbl.config(text="no media host elected for this LAN", fg=ROSE)
        _mc = fnav.media_capability()
        if _mc["missing"]:
            self.devices_lbl.config(
                text="missing: %s\n%s" % (", ".join(_mc["missing"]),
                                          fnav.media_install_hint(_mc["missing"])),
                fg=ROSE)
        self._preflight["mic"] = self._probe_mic() and _mc["audio"]
        verdict = UI.preflight_verdict(self._preflight["cam"], self._preflight["mic"],
                                       self.args.no_video, self.args.no_audio)
        ceil = UI.ceiling_from_devices(self._preflight["cam"], self._preflight["mic"],
                                       self.args.no_video, self.args.no_audio)
        self.verdict_lbl.config(text=verdict, fg=(LILY if ceil >= 5 else ROSE))
        if ceil < 5 and not self.args.no_video:
            # say the consequence, not just the ceiling
            self.verdict_lbl.config(
                text=verdict + "\nno microphone -- video cannot be sent at all "
                               "(every video rung rides the audio bed)")
        self.devices_lbl.config(text=self._device_note())

    def _probe_mic(self):
        """Is there a usable input device? [MIC_GATES_VIDEO_V1]

        _preflight["mic"] used to be `not --no-audio`, i.e. an assertion that a
        microphone exists because nobody said it didn't. On a box with no input
        device the lobby then read "camera + mic ready -- can source L7 CHORUS" and
        the call sat at L2 WHISPER with no video and no explanation: every video rung
        rides the audio bed, so no microphone means no video, ever, at any bandwidth.
        Ask the device layer instead of assuming.
        """
        if self.args.no_audio:
            return False
        try:
            import sounddevice as sd
            dev = (fnav.A._resolve_device(self.args.in_dev)
                   if self.args.in_dev is not None else None)
            info = sd.query_devices(dev, "input") if dev is not None \
                else sd.query_devices(kind="input")
            return int(info.get("max_input_channels", 0)) > 0
        except Exception:
            return False

    def _device_note(self):
        d = self._devices
        if d.get("in_name") or d.get("out_name"):
            return ("camera %s\nmic: %s\nout: %s"
                    % (d.get("cam"), (d.get("in_name") or "default")[:34],
                       (d.get("out_name") or "default")[:34]))
        try:
            import sounddevice as sd
            i = sd.query_devices(fnav.A._resolve_device(self.args.in_dev), "input") \
                if self.args.in_dev is not None else sd.query_devices(kind="input")
            o = sd.query_devices(fnav.A._resolve_device(self.args.out_dev), "output") \
                if self.args.out_dev is not None else sd.query_devices(kind="output")
            return "mic: %s\nout: %s" % (i["name"][:34], o["name"][:34])
        except Exception as e:
            return "audio devices unavailable (%s)" % type(e).__name__

    # =====================================================================
    # background
    # =====================================================================
    def _load_settings_later(self):
        """Re-read once shortly after start, in case the data host was slow or was
        elected after we asked."""
        self._stop.wait(6.0)
        if not self._stop.is_set():
            self._load_settings()

    def _load_settings(self):
        """Read this user's saved devices off the tuple plane at startup.

        Off the UI thread: it is a database read, and a slow or absent data host must
        not hold the window closed. Whatever comes back is reconciled against the
        hardware actually present before it is applied."""
        try:
            saved = self.cp.load_settings()
        except Exception:
            saved = None
        if saved:
            self._q.put(("settings", saved))

    def _heartbeat(self):
        while not self._stop.is_set():
            try:
                self.cp.announce()
                self.cp.refresh_calls()
                self.cp.refresh_settings()
            except Exception as e:
                self._q.put(("status", "presence: %s" % type(e).__name__))
            self._stop.wait(HEARTBEAT_S)

    def _poll(self):
        while not self._stop.is_set():
            try:
                roster = self.cp.roster()
            except Exception as e:
                roster = []
                LOG.exception("roster failed: %s: %s", type(e).__name__, e)
            try:
                calls = self.cp.list_calls()
            except Exception as e:
                # [AN_EMPTY_LIST_IS_NOT_AN_ANSWER_V1] This swallowed everything.
                # A raise in list_calls became "no calls", indistinguishable
                # from a pond with no calls in it, and nothing said a word in
                # the log or on the console. The lobby read "none right now"
                # either way.
                calls = []
                LOG.exception("list_calls failed -- lobby will show none: "
                              "%s: %s", type(e).__name__, e)
            try:
                chat = self.cp.read_chat(self.screen.session or LOBBY_SCOPE)
            except Exception:
                chat = []
            link = None
            if self.screen.session:
                try:
                    link = self.cp.read_link(self.screen.session)
                except Exception:
                    link = None
            self._q.put(("data", roster, calls, chat, link))
            self._stop.wait(1.0)

    def _drain_q(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg[0] == "data":
                    _, roster, calls, chat, link = msg
                    self._reconcile_link(link)
                    self._names = UI.display_names(roster)
                    self._calls = calls
                    self._render_roster(roster)
                    self._render_calls(calls)
                    self._render_chat(chat)
                    self._check_ring(calls)
                elif msg[0] == "settings":
                    self._adopt_settings(msg[1], from_store=True)
                elif msg[0] == "status":
                    self.status.config(text=msg[1])
        except queue.Empty:
            pass
        self.after(500, self._drain_q)

    def _adopt_settings(self, settings, from_store=False):
        """Apply chosen devices. Explicit command-line flags still win: someone who
        typed --cam 3 meant it, and having stored settings quietly override the flag
        would make the flag untrustworthy."""
        if settings.get("cam") is not None and "--cam" not in sys.argv:
            self.args.cam = int(settings["cam"])
        if settings.get("in_dev") is not None and "--in" not in sys.argv:
            self.args.in_dev = settings["in_dev"]
        if settings.get("out_dev") is not None and "--out" not in sys.argv:
            self.args.out_dev = settings["out_dev"]
        if settings.get("codec") and self.args.codec == "auto":
            self.pending.codec = settings["codec"]
        if settings.get("quality"):
            self.pending.quality = settings["quality"]
        self._preview_retry()        # a different camera may have been chosen
        self._devices = {"cam": self.args.cam, "in_dev": self.args.in_dev,
                         "out_dev": self.args.out_dev,
                         "in_name": settings.get("in_name"),
                         "out_name": settings.get("out_name")}
        self._preflight["cam"] = self.args.cam is not None and not self.args.no_video
        if from_store:
            self.status.config(text="settings restored from the pond")
        self._refresh_lobby_text()

    def _claim_camera(self):
        self._cam_claim = True
        self._preview_off()

    def _release_camera(self):
        self._cam_claim = False
        self._preview_retry()        # fnav has handed the camera back

    def _preview_on(self):
        """Ask for a live self-preview. Opens the camera on a WORKER, never here.

        [PREVIEW_OPEN_OFF_UI_THREAD_V1] This used to call cv2.VideoCapture directly,
        on the Tk main thread, and _tick_preview called it every 80ms whenever there
        was no capture yet. A DirectShow open can take seconds, and right after Camera
        and sound released the device it fails -- so the app re-attempted a blocking
        open sixteen times a second and the mainloop never got back to redrawing. It
        looked like a hang because it was one: closing the device chooser froze the
        window until it was killed.

        One attempt at a time, off the UI thread, and a device that failed is not
        hammered -- it is retried when something CHANGES: a different camera chosen,
        the chooser closed, a call ended.
        """
        if (cv2 is None or self.args.no_video or self._cam_claim
                or self._pv_cap is not None or self._pv_opening):
            return
        idx = int(self.args.cam or 0)
        if self._pv_failed == idx:
            return                      # already tried this one; wait for a change
        self._pv_opening = True
        threading.Thread(target=self._preview_open_worker, args=(idx,),
                         daemon=True).start()

    def _preview_open_worker(self, idx):
        cap = None
        try:
            with fnav._silence_cv2_stderr():
                cap = fnav.open_camera(idx)
            if not cap.isOpened():
                cap.release()
                cap = None
        except Exception as e:
            LOG.warning("preview: camera %s open failed: %s", idx, e)
            cap = None
        # hand the result back to the UI thread; it owns the widgets
        try:
            self.after(0, self._preview_opened, idx, cap)
        except Exception:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    def _preview_opened(self, idx, cap):
        self._pv_opening = False
        if self._cam_claim or self.args.no_video:
            if cap is not None:         # somebody took the camera while we were opening
                try:
                    cap.release()
                except Exception:
                    pass
            return
        if cap is None:
            self._pv_failed = idx
            LOG.info("preview: camera %s unavailable", idx)
            try:
                self.preview_lbl.config(
                    text="camera %d did not open\nuse Camera and sound to pick "
                         "another" % idx)
            except Exception:
                pass
            return
        self._pv_failed = None
        self._pv_cap = cap
        LOG.info("preview: camera %s open", idx)

    def _preview_off(self):
        self._pv_opening = False
        if self._pv_cap is not None:
            try:
                self._pv_cap.release()
            except Exception:
                pass
            self._pv_cap = None

    def _preview_retry(self):
        """Something changed -- a camera was chosen, the chooser closed, a call ended.
        Allow one more attempt at a device that previously failed."""
        self._pv_failed = None

    def _tick_preview(self):
        if self._stop.is_set():
            return
        want = (self.screen.mode == UI.LOBBY and self.call is None
                and self.setup_win is None and not self._cam_claim)
        if want and self._pv_cap is None:
            self._preview_on()
        elif not want and self._pv_cap is not None:
            self._preview_off()
        if self._pv_cap is not None:
            try:
                ok, frame = self._pv_cap.read()
                if ok and frame is not None:
                    ih, iw = frame.shape[:2]
                    w = 240
                    photo = _photo(frame, w, max(1, int(ih * w / iw)),
                                   self._tmp[MAX_TILES])
                    self._preview_photo = photo
                    self.preview_lbl.config(image=photo, text="")
                    self._preflight["cam"] = True
            except Exception as e:
                LOG.warning("preview tick: %s: %s", type(e).__name__, e)
        self.after(80, self._tick_preview)      # [TICK_ALWAYS_REARMS_V1]

    def _open_setup(self):
        self._claim_camera()         # Camera and sound opens the device itself
        if getattr(self, "setup_win", None) is not None:
            try:
                self.setup_win.lift()
                return
            except Exception:
                self.setup_win = None
        self._pv_cap = None          # lobby self-preview capture, released before a call
        # [CAMERA_CLAIM_V1] One owner at a time. _start_call released the preview and
        # THEN did a network round trip (cp.start_call) before the mode changed --
        # _tick_preview fires every 80ms, saw mode==LOBBY and call is None, and
        # reopened the camera in that window. fnav._probe then found it busy, set
        # have_cam=False, and _video_tx was never spawned: the call connected and no
        # video was ever sent. The claim is taken BEFORE the release and held until
        # the call is over.
        self._cam_claim = False
        self._pv_opening = False     # a preview open is in flight on a worker
        self._pv_failed = None       # index that failed; retried only on a change
        self._ask_after = None       # scheduled "who are you calling?"
        self._ask_win = None
        self.setup_win = comms_setup.SetupWindow(
            self, self.cp, dict(self._devices), self._setup_saved,
            self._tmp[MAX_TILES], on_close=self._setup_closed)

    def _setup_closed(self):
        self.setup_win = None
        self._preview_retry()        # the chooser just released the device
        self._release_camera()

    def _setup_saved(self, settings):
        self._adopt_settings(settings)
        self.status.config(text="devices saved")

    def _toggle_quiet(self):
        """Quiet mode: blank the camera, mute the mic, silence the speaker, and hold
        the conversation as text through the call's shared transcript."""
        if self.quiet is not None:
            self._leave_quiet()
            return
        if self.call is None or not self.screen.session:
            self.status.config(text="quiet mode needs a call")
            return
        self.quiet = comms_voice.QuietMode(self.cp, self.screen.session, self.me_id)
        self.quiet.engage(self.call)
        self.quiet_win = comms_quiet.QuietWindow(self, self.quiet, self._leave_quiet)
        self.quiet_btn.config(text="Leave quiet", bg=ROSE, fg="#fff")
        self.status.config(text="quiet mode -- %s" % self.quiet.status())

    def _leave_quiet(self):
        if self.quiet is None:
            return
        self.quiet.release(self.call)
        self.quiet = None
        if self.quiet_win is not None:
            win, self.quiet_win = self.quiet_win, None
            try:
                win._closing = True
                win.destroy()
            except Exception:
                pass
        try:
            self.quiet_btn.config(text="Quiet mode", bg=AMBER, fg=WATER)
        except Exception:
            pass
        self.status.config(text="")

    def _open_diag(self):
        if self.diag is not None:
            try:
                self.diag.lift()
                return
            except Exception:
                self.diag = None
        self.quiet = None
        self.quiet_win = None
        self.diag = comms_diag.DiagWindow(self, self)

    def _reconcile_link(self, remote):
        """The call's link tuple says what the link is. Apply it to MY uplink.

        Only the setting crosses the wire; the shaping is local, because a sender can
        only throttle the uplink it owns. Reading back my own setting changes
        nothing, so it costs nothing to apply it again."""
        if not self.link.take(remote):
            return
        self.pending.bps = self.link.bps
        self.pending.jitter_ms = self.link.jitter_ms
        # [SLIDER_PUSHES_ON_SETTLE_V1] Moving the widget to follow the tuple is
        # not the user moving it. Suppress the callback across both .set() calls
        # so adopting a remote setting does not republish it as a local one.
        self._slider_suppress = True
        try:
            self.bps_var.set(2000 if self.link.bps == 0 else self.link.bps // 1000)
            self.jit_var.set(self.link.jitter_ms)
        finally:
            self._slider_suppress = False
        if self.call is not None:
            try:
                self.call.set_throttle(self.link.bps)
                self.call.set_jitter(self.link.jitter_ms)
                # [MEDIASPEED_V1] the downlink half of the same setting.
                _s = getattr(self.screen, "session", None)
                if not _s:
                    raise RuntimeError(
                        "adopted a link setting but screen.session is empty: the "
                        "uplink was throttled, the downlink was NOT.")
                self.cp.set_media_control(_s, speed_bps=self.link.bps)
            except Exception as e:
                LOG.error("link: could not apply: %r", e)
        match = next((p for p in UI.PRESETS if p.name == self.link.preset), None)
        for b, p in self.preset_btns:
            on = match is not None and p.name == match.name
            b.config(bg=AMBER if on else WATER3, fg=WATER if on else BONE)
        self._refresh_link_badge()
        self.status.config(text="link changed by %s"
                                % (self.link.set_by_name or self.link.set_by))

    def _check_ring(self, calls):
        busy = self.screen.mode in (UI.CALL, UI.RINGING)
        # [MEMBERSHIP_IS_SELF_ASSERTED_V1] Ring on INVITATIONS, not on finding
        # my own id in somebody's remembered members list. `calls` is still
        # passed for the "N on the call" line, which is a fact about the call
        # and not about whether to ring.
        try:
            invites = self.cp.invitations_for_me()
        except Exception as e:
            LOG.warning("could not read invitations: %s: %s", type(e).__name__, e)
            return
        _size = {c.get("session"): len(c.get("members") or []) for c in calls}
        # [FOUR_TUPLES_V1] invitations are keyed and addressed by NAME
        for c in self.rings.should_ring(invites, self.me_name, busy=busy):
            caller = c.get("from") or "someone"
            LOG.info("ringing: session=%s from=%s", c.get("session"), caller)
            self.screen.ring(c["session"], self._names.get(caller, caller))
            self.ring_who.config(text="%s is calling" % self.screen.caller)
            self.ring_sub.config(text="%d on the call"
                                      % _size.get(c.get("session"), 0))
            self._apply_mode()
            try:
                self.bell()
            except Exception:
                pass
            break

    # -- render ------------------------------------------------------------
    def _render_roster(self, roster):
        sig = tuple((p.get("id"), p.get("name"), p.get("status")) for p in roster)
        if sig == getattr(self, "_last_roster_sig", None):
            return
        self._last_roster_sig = sig
        LOG.info("flock: %s", ", ".join(
            "%s(%s)" % (p.get("name"), p.get("id")) for p in roster) or "empty")
        for w in self.roster_box.winfo_children():
            w.destroy()
        # [ONE_ROW_PER_PERSON_V1] Every launch mints a new me_id, so one person who
        # has started the client a few times inside the freshness window appears as
        # several rows -- observed: nine george-* tuples for one George. The tuples
        # are all legitimate; the ROSTER is a list of people, not of tuples. Keep the
        # freshest row per name and drop the rest.
        _by_name = {}
        for p in roster:
            if p.get("id") == self.me_id:
                continue
            _k = (p.get("name") or p.get("id") or "?").strip().lower()
            _prev = _by_name.get(_k)
            if _prev is None or (p.get("ts") or 0) >= (_prev.get("ts") or 0):
                _by_name[_k] = p
        others = sorted(_by_name.values(),
                        key=lambda q: (q.get("name") or "").lower())
        self.flock_count.config(
            text="just you so far" if not others else
                 "%d online" % len(others))
        for p in others:
            row = tk.Frame(self.roster_box, bg=WATER2)
            row.pack(fill="x", padx=6, pady=3)
            caps = p.get("caps") or {}
            mark = "video" if caps.get("cam") else ("audio" if caps.get("mic") else "text")
            box = tk.Frame(row, bg=WATER2)
            box.pack(side="left", fill="x", expand=True)
            tk.Label(box, text=p.get("name", "?"), fg=BONE, bg=WATER2, anchor="w",
                     font=F_MED).pack(anchor="w")
            tk.Label(box, text=mark, fg=MUTE, bg=WATER2, anchor="w",
                     font=F_SMALL).pack(anchor="w")
            tk.Button(row, text="Call",
                      command=lambda pid=p.get("id"): self._start_call([pid]),
                      bg=REED, fg=WATER, relief="flat", padx=10).pack(side="right")
        self._refresh_lobby_text()

    def _render_calls(self, calls):
        sig = (self.screen.session,
               tuple((c.get("session"), tuple(c.get("members") or [])) for c in calls))
        if sig == getattr(self, "_last_calls_sig", None):
            return
        self._last_calls_sig = sig
        LOG.info("calls in progress: %s", [
            {"session": c.get("session"), "members": c.get("members"),
             "host": c.get("host")} for c in calls] or "none")
        for w in self.calls_box.winfo_children():
            w.destroy()
        # [AN_ANNOUNCED_CALL_IS_SOMEWHERE_TO_GO_V1] Show it whether or not
        # anybody has joined yet.
        #
        # [ONLY_LIVE_CALLS_V1] hid every call with an empty members list, on the
        # reasoning that a call everybody has left is a stale row rather than
        # somewhere to go. That reasoning belonged to the old shape, where the
        # only evidence a call existed WAS its members list.
        #
        # It is wrong now and it is what empties this lobby. CallsAnnounce is a
        # host saying "I am offering this call, connect here" -- a fact in its
        # own right, from a live writer, that ages out when the offer stops.
        # Membership comes from a DIFFERENT tuple written by DIFFERENT nodes, so
        # a call is routinely announced before anyone has joined it, and the
        # announcer's own CallsJoined row may not have landed yet when the
        # lobby first reads. Requiring members meant an offer nobody had taken
        # up was invisible -- so nobody could take it up.
        #
        # A call that is genuinely over stops being announced, and the row ages
        # out under fresh_s. That is what makes the offer trustworthy, not the
        # presence of members in it.
        if not calls:
            tk.Label(self.calls_box, text="none right now", fg=MUTE, bg=WATER2,
                     font=F_SMALL).pack(anchor="w", padx=14)
            return
        for c in calls:
            sid = c.get("session")
            row = tk.Frame(self.calls_box, bg=WATER2)
            row.pack(fill="x", padx=8, pady=3)
            label = UI.call_label(c, self._names, self.me_name)
            mine = sid == self.screen.session
            tk.Label(row, text=label, fg=LILY if mine else BONE, bg=WATER2,
                     anchor="w", font=F_MED).pack(side="left")
            if not mine and self.screen.mode == UI.LOBBY:
                tk.Button(row, text="Join", command=lambda s=sid: self._join(s),
                          bg=REED, fg=WATER, relief="flat", padx=10).pack(side="right")

    def _render_chat(self, chat):
        sig = tuple((m.get("from"), m.get("text")) for m in chat)
        if sig == getattr(self, "_last_chat_sig", None):
            return
        self._last_chat_sig = sig
        self.chat_log.config(state="normal")
        self.chat_log.delete("1.0", "end")
        for m in chat:
            self.chat_log.insert("end", "%s: %s\n" % (m.get("from", "?"),
                                                      m.get("text", "")))
        self.chat_log.config(state="disabled")
        self.chat_log.see("end")

    def _send_chat(self):
        t = self.chat_in.get().strip()
        if not t:
            return
        self.chat_in.delete(0, "end")
        try:
            self.cp.send_chat(self.screen.session or LOBBY_SCOPE, t)
        except Exception:
            pass

    # =====================================================================
    # call lifecycle
    # =====================================================================
    def _cancel_ask(self):
        """Drop a scheduled 'who are you calling?'.

        [NO_ASK_AFTER_HANGUP_V1] The picker is scheduled a moment after the call
        starts so the camera warms up while you choose. Hang up inside that window
        and it used to arrive anyway -- an invitation dialog appearing over the lobby
        for a call that no longer exists. Hanging up cancels it, and _ask_who refuses
        to open unless a call is actually live.
        """
        if self._ask_after is not None:
            try:
                self.after_cancel(self._ask_after)
            except Exception:
                pass
            self._ask_after = None

    def _begin_call(self):
        """Start a call button: open the call NOW -- camera up, you are in it -- and
        ask who to invite over the top of it.

        The choosing is not on the critical path. fnav opening the camera and
        connecting to the relay is the slow part, so it starts first and you pick
        while it warms up.
        """
        if self.call is not None:
            self._ask_who()          # already in a call: this is "invite more"
            return
        self._start_call([])
        if self.screen.session:
            self._cancel_ask()
            self._ask_after = self.after(120, self._ask_who)

    def _ask_who(self):
        """Who are you calling? ALWAYS asked, and the open invitation is always on
        offer -- even with an empty flock, where the answer is 'nobody yet, leave it
        open' and you should be told that rather than left guessing.

        Ticking somebody adds them to the call's member list, which is what makes
        their Communicator ring. There is no invite message to send or to lose.
        """
        self._ask_after = None
        if self.call is None or not self.screen.session:
            return                        # [NO_ASK_AFTER_HANGUP_V1]
        if getattr(self, "_ask_win", None) is not None:
            try:
                self._ask_win.lift()
                return
            except Exception:
                self._ask_win = None

        others = [(pid, nm) for pid, nm in sorted(self._names.items(),
                                                  key=lambda kv: (kv[1] or "").lower())
                  if pid != self.me_id]
        already = set()
        try:
            info = self.cp.call_info(self.screen.session) or {}
            already = set(info.get("members") or [])
        except Exception:
            pass

        win = tk.Toplevel(self)
        self._ask_win = win
        win.title("Who are you calling?")
        win.configure(bg=WATER2)
        win.attributes("-topmost", True)

        def close():
            self._ask_win = None
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", close)
        tk.Label(win, text="Who are you calling?", fg=BONE, bg=WATER2,
                 font=F_BIG).pack(padx=28, pady=(20, 4))
        tk.Label(win, text="they ring the moment you pick them", fg=MUTE, bg=WATER2,
                 font=F_SMALL).pack(padx=28, pady=(0, 12))

        picked = {}
        box = tk.Frame(win, bg=WATER2)
        box.pack(fill="x", padx=28)
        for pid, nm in others:
            here = pid in already
            v = tk.BooleanVar(value=False)
            picked[pid] = v
            tk.Checkbutton(box, text=nm + ("   (on the call)" if here else ""),
                           variable=v, bg=WATER2, fg=(MUTE if here else BONE),
                           selectcolor=WATER, activebackground=WATER2,
                           activeforeground=LILY, anchor="w",
                           state=("disabled" if here else "normal"),
                           font=F_MED).pack(fill="x", pady=1)
        if not others:
            tk.Label(box, text="nobody else is on the pond yet", fg=MUTE, bg=WATER2,
                     font=F_MED).pack(fill="x", pady=4)

        # the open invitation, always offered
        tk.Frame(win, bg=LINE, height=1).pack(fill="x", padx=28, pady=(14, 10))
        tk.Label(win, text="OPEN INVITATION", fg=AMBER, bg=WATER2,
                 font=F_LABEL).pack(anchor="w", padx=28)
        tk.Label(win, text="the call is listed under CALLS IN PROGRESS for everyone\n"
                           "on the pond -- anybody can join it without being rung.",
                 fg=MUTE, bg=WATER2, font=F_SMALL, justify="left").pack(anchor="w",
                                                                        padx=28)

        btns = tk.Frame(win, bg=WATER2)
        btns.pack(pady=(16, 20))

        def go():
            # [FOUR_TUPLES_V1] Invite by NAME: CallsInvitations is scoped
            # user:<name>, and the picker's keys are per-process ids. Sending an
            # id would write a row nobody ever reads, because the invitee looks
            # up its own name.
            who = [self._names.get(pid, pid)
                   for pid, v in picked.items() if v.get()]
            close()
            self._invite(who)

        tk.Button(btns, text="Ring them", command=go, bg=REED, fg=WATER, relief="flat",
                  padx=22, pady=8, font=F_BIG,
                  state=("normal" if others else "disabled")).pack(side="left", padx=6)
        tk.Button(btns, text="Leave it open", command=close, bg=WATER3, fg=BONE,
                  relief="flat", padx=16, pady=8).pack(side="left")

    def _missing_media_dialog(self, cap, hint):
        """[MEDIA_DEPS_ARE_CHECKED_V1] cv2/av/sounddevice/samplerate are pip installs,
        not part of the bundle. Nothing checked for them, so the client started, the
        lobby looked normal, and Start a call died inside a Tk callback with
        ModuleNotFoundError. Say it plainly instead."""
        win = tk.Toplevel(self)
        win.title("Missing media libraries")
        win.configure(bg=WATER2)
        win.attributes("-topmost", True)
        tk.Label(win, text="This machine cannot carry a call", fg=ROSE, bg=WATER2,
                 font=F_BIG).pack(padx=28, pady=(20, 6))
        tk.Label(win, text=cap["why"], fg=BONE, bg=WATER2, font=F_MED,
                 wraplength=440, justify="left").pack(padx=28)
        tk.Label(win, text="These are pip installs, not part of the bundle:",
                 fg=MUTE, bg=WATER2, font=F_SMALL).pack(padx=28, pady=(12, 2))
        e = tk.Entry(win, bg=WATER, fg=LILY, bd=0, font=F_MONO, justify="center")
        e.insert(0, hint)
        e.config(state="readonly")
        e.pack(fill="x", padx=28, ipady=6)
        tk.Label(win, text="Presence, chat and the transcript work without them --\n"
                           "that is the text floor, and it is a real rung.",
                 fg=MUTE, bg=WATER2, font=F_SMALL, justify="left").pack(padx=28,
                                                                        pady=(12, 4))
        tk.Button(win, text="OK", command=win.destroy, bg=REED, fg=WATER,
                  relief="flat", padx=22, pady=8).pack(pady=(10, 20))

    def _no_media_dialog(self, msg):
        """No media host elected for this LAN. Called from _start_call's NoMediaHost
        branch -- which referenced this method before it existed, so that path raised
        AttributeError instead of explaining itself."""
        win = tk.Toplevel(self)
        win.title("No media host")
        win.configure(bg=WATER2)
        win.attributes("-topmost", True)
        tk.Label(win, text="No media host on this LAN", fg=ROSE, bg=WATER2,
                 font=F_BIG).pack(padx=28, pady=(20, 6))
        tk.Label(win, text=msg, fg=BONE, bg=WATER2, font=F_MED, wraplength=440,
                 justify="left").pack(padx=28)
        tk.Label(win, text="A call gathers at the media host elected for your LAN.\n"
                           "Until one is elected and running the relay there is\n"
                           "nowhere for the call to form.\n\n"
                           "  getent hosts mediahost.frognet\n"
                           "  systemctl status frognet-mediahost   (on that host)",
                 fg=MUTE, bg=WATER2, font=F_SMALL, justify="left").pack(padx=28,
                                                                        pady=(12, 4))
        tk.Button(win, text="OK", command=win.destroy, bg=REED, fg=WATER,
                  relief="flat", padx=22, pady=8).pack(pady=(10, 20))

    def _invite(self, who):
        if not who or not self.screen.session:
            return
        try:
            members = self.cp.invite(self.screen.session, who)
        except Exception as e:
            self.status.config(text="invite failed: %s" % type(e).__name__)
            return
        if members is None:
            self.status.config(text="that call is no longer there")
            return
        names = ", ".join(self._names.get(p, p) for p in who)
        LOG.info("invited %s -> members now %s", who, members)
        self.status.config(text="ringing %s" % names)

    def _start_call(self, members=None):
        if self.call is not None:
            return
        # [MEDIA_DEPS_ARE_CHECKED_V1] A machine with neither encoder nor audio cannot
        # carry a call at all. fnav used to discover that inside its constructor and
        # raise ModuleNotFoundError straight out of a Tk callback. Ask first.
        cap = fnav.media_capability()
        if not cap["video"] and not cap["audio"]:
            self._release_camera()
            hint = fnav.media_install_hint(cap["missing"])
            self.status.config(text="this machine has no media: %s" % cap["why"])
            self._missing_media_dialog(cap, hint)
            return
        self._claim_camera()         # held until the call ends: fnav owns it now

        # [JOIN_BEFORE_YOU_CREATE_V1] An open call on this relay is the call.
        #
        # This unconditionally minted a NEW session. Two people pressing Call
        # therefore sat in two different sessions, and before the relay checked
        # sessions the shared port made those interoperate BY ACCIDENT. That
        # accident was the bug; it was also the only thing bringing the two ends
        # together. Once [FAN_IS_PER_SESSION_V1] closed it, pressing Call at both
        # ends produced two people transmitting into black holes -- measured
        # 2026-08-10, 374 KB/s out and frames=0 back, on both sides at once.
        #
        # Creating is the exception now. If a call is already open on the relay
        # this node uses, that session IS the call and we join it.
        #
        # ONLY with nobody selected. `Call <person>` is a request for a
        # conversation with that person and must not silently drop the caller
        # into somebody else's room; that path still creates.
        info = None
        if not members:
            try:
                _open = [c for c in self.cp.list_calls() if c.get("session")]
            except Exception as e:
                _open = []
                LOG.warning("could not list open calls: %s: %s",
                            type(e).__name__, e)
            if not _open:
                # [SAY_WHY_THE_LIST_IS_EMPTY_V1] Creating is the exception, so
                # say when it happens and why. Silently minting a session is how
                # two people end up alone in two rooms.
                LOG.info("no open call to join -- creating one")
            if _open:
                # Most-populated first, then by session id so the choice is
                # deterministic when two are equally full -- an arbitrary pick
                # here would put two simultaneous callers in different rooms
                # again, which is the whole defect.
                _open.sort(key=lambda c: (-len(c.get("members") or []),
                                          c["session"]))
                _pick = _open[0]["session"]
                try:
                    info = self.cp.join_call(_pick)
                except Exception as e:
                    LOG.warning("join of open call %s failed: %s: %s -- "
                                "starting a new one", _pick, type(e).__name__, e)
                    info = None
                if info:
                    LOG.info("joined open call session=%s host=%s:%s "
                             "members=%s (did not create)", info.get("session"),
                             info.get("host"), info.get("port"),
                             _open[0].get("members"))
                    self.screen.dialing(info["session"])
                    self._enter_call(info)
                    return

        try:
            # [FOUR_TUPLES_V1] invite by NAME. me_id is a per-process handle and
            # is no longer an identity in any call tuple; start_call invites the
            # others rather than asserting them onto the call.
            info = self.cp.start_call(members=members or [])
            LOG.info("announced call session=%s host=%s:%s inviting=%s",
                     info.get("session"), info.get("host"), info.get("port"),
                     members or "nobody")
        except CC.NoMediaHost as e:
            # the message names the actual problem; a class name would not
            self._release_camera()
            self.status.config(text=str(e))
            self._no_media_dialog(str(e))
            return
        except Exception as e:
            self._release_camera()
            self.status.config(text="call failed: %s: %s" % (type(e).__name__, e))
            return
        LOG.info("start_call session=%s host=%s:%s members=%s",
                 info.get("session"), info.get("host"), info.get("port"),
                 members or [])
        self.screen.dialing(info["session"])
        self._enter_call(info)

    def _join(self, session):
        if self.call is not None:
            return
        self._claim_camera()
        try:
            info = self.cp.join_call(session)
        except Exception as e:
            self.status.config(text="join failed: %s" % type(e).__name__)
            return
        if not info:
            self.status.config(text="that call is no longer there")
            return
        self.screen.dialing(session)
        self._enter_call(info)

    def _accept(self):
        self._claim_camera()
        session = self.screen.session
        self.screen.accept()
        self.rings.answered(session)
        try:
            info = self.cp.join_call(session)
        except Exception as e:
            self.screen.dropped("Could not join: %s" % type(e).__name__)
            self._apply_mode()
            return
        if not info:
            self.screen.dropped("That call ended before you answered.")
            self._apply_mode()
            return
        self._enter_call(info)

    def _decline(self):
        """Declining retires the invitation, so the caller sees it stop.

        UnREST's way of saying no: change the shared memory, do not send a
        message about it. The old shell just destroyed the popup and told nobody
        -- and never rang for that session again, so a redial was silent
        forever."""
        session = self.screen.session
        self.screen.decline()
        self.rings.declined(session)
        try:
            # [MEMBERSHIP_IS_SELF_ASSERTED_V1] Declining retires the INVITATION,
            # which is the fact that was addressed to me. leave_call was the old
            # way of saying no -- it removed me from a members list I had never
            # been on, since I never answered. Nothing to leave; the thing to
            # withdraw is the invite.
            self.cp.decline(session)
        except Exception as e:
            LOG.warning("could not decline %s: %s: %s", session,
                        type(e).__name__, e)
        self.status.config(text="declined")
        self._apply_mode()

    def _enter_call(self, info):
        a = self.args
        codec_id = {"auto": None, "vp8": fnav.CODEC_VP8, "h264": fnav.CODEC_H264_SW,
                    "h264hw": fnav.CODEC_H264_HW}[a.codec]
        if self.pending.codec and a.codec != "auto":
            codec_id = {"vp8": fnav.CODEC_VP8, "h264": fnav.CODEC_H264_SW,
                        "h264hw": fnav.CODEC_H264_HW}.get(self.pending.codec, codec_id)
        call = fnav.Call(
            info["host"], info["port"], a.name,
            in_dev=fnav.A._resolve_device(a.in_dev),
            out_dev=fnav.A._resolve_device(a.out_dev),
            cam=int(self.args.cam or 0),
            no_video=a.no_video, no_audio=a.no_audio,
            duck=not a.no_duck, fps=a.fps, no_display=True, codec_id=codec_id,
            # [SENDER_READS_MEDIASPEED_V1] Give the Call the session so it can
            # read the OTHER viewers' MediaSpeed rows and lower its own rung to
            # the slowest of them. Without this the sender is uncapped no matter
            # what any viewer publishes.
            session=self.screen.session)
        self.call = call

        # [CALL_RUN_IS_OBSERVED_V1] Nothing watched this thread. When run()
        # raised, Python printed to stderr -- a console that closes on Windows --
        # and the app carried on holding a half-constructed Call forever: no
        # decoder, no sockets, no video, and no error anywhere the user could
        # see. A media plane failing to open is fatal to the call and has to
        # look like it.
        def _run_call():
            try:
                call.run()
            except Exception as e:
                LOG.exception("call.run() failed")
                try:
                    self.status.config(text="CALL FAILED: %s" % (e,))
                except Exception:
                    pass
                self.call = None

        self.call_thread = threading.Thread(target=_run_call, daemon=True)
        self.call_thread.start()
        # Everything armed in the lobby lands NOW. The old shell discarded these
        # unless a call already existed, while still displaying them as applied.
        self._apply_pending()
        # A call already in progress may already have a link condition set. Adopt it
        # rather than quietly imposing whatever this window happened to be showing.
        try:
            self._reconcile_link(self.cp.read_link(info["session"]))
        except Exception:
            pass
        self.history = UI.LinkHistory()
        self.screen.connected(info["host"], info["port"])
        # not "connected": the socket is opened on the worker thread and may be
        # refused. Say what we are attempting; _tick_video corrects it either way.
        self.status.config(text="calling %s:%s..." % (info["host"], info["port"]))
        self._apply_mode()

    def _apply_pending(self):
        c = self.call
        if c is None:
            return
        try:
            c.set_throttle(self.pending.bps)
            c.set_jitter(self.pending.jitter_ms)
            c.set_level_cap(self.pending.cap())
            # [MEDIASPEED_V1] set_throttle shapes only the leg this client owns
            # (camera -> host). The same number must reach the media host so it
            # caps the other leg (host -> this client). A client's link is
            # symmetric; one slider, both directions.
            #
            # NOT guarded on "is there a session". A guard here was written and
            # removed: with no session it skipped the publish and said nothing,
            # so the slider moved, the label updated, the uplink throttled, and
            # the downlink stayed wide open with no indication. That is precisely
            # the failure this file's header describes -- controls that "silently
            # did nothing while their labels confidently reported the value they
            # had not applied". _apply_pending already returns early when there
            # is no call, so reaching here means there IS one; a missing session
            # is a fault and it says so.
            _sess = getattr(self.screen, "session", None)
            if not _sess:
                raise RuntimeError(
                    "link setting cannot be published: in a call but "
                    "screen.session is empty, so the media host cannot be told "
                    "which viewer to cap. The uplink was throttled; the downlink "
                    "was NOT.")
            self.cp.set_media_control(_sess, speed_bps=self.pending.bps)
            # [CODEC_DEFAULT_IS_AUTO_V1] Only a codec the user actually PICKED
            # overrides the probe. "auto" (and anything unrecognised) leaves the
            # encoder the box chose for itself.
            _forced = {"vp8": fnav.CODEC_VP8, "h264": fnav.CODEC_H264_SW,
                       "h264hw": fnav.CODEC_H264_HW}.get(self.pending.codec)
            if _forced is not None:
                c.codec_id = _forced
        except Exception as e:
            # Name the failure, not just its class. "control apply failed:
            # RuntimeError" tells nobody which control failed or why, and the
            # setting the user just chose is not in effect.
            self.status.config(text="control apply FAILED: %s" % (e,))
            LOG.error("link: apply failed: %r", e)

    def _hang(self):
        self._cancel_ask()
        self._release_camera()
        self._leave_quiet()
        c = self.call
        self.call = None
        if c is not None:
            try:
                c._stop.set()
            except Exception:
                pass
        if self.call_thread:
            self.call_thread.join(timeout=2.0)
        session = self.screen.session
        if session:
            try:
                self.cp.leave_call(session)
            except Exception:
                pass
        self._last_stats = getattr(self, "_last_stats", {})
        self.screen.hangup()
        self.call_thread = None
        self._clear_tiles()
        self._paint_ended()
        self._apply_mode()

    def _on_call_dropped(self):
        self._cancel_ask()
        self._release_camera()
        self._leave_quiet()
        self.call = None
        self.call_thread = None
        session = self.screen.session
        if session:
            try:
                self.cp.leave_call(session)
            except Exception:
                pass
        host = self.screen.host or "the media host"
        port = self.screen.port or 9000
        LOG.warning("call dropped: host=%s port=%s", self.screen.host, self.screen.port)
        self.screen.dropped("No answer from %s:%s -- the media host is not accepting "
                            "connections, or the call ended." % (host, port))
        self._clear_tiles()
        self._paint_ended()
        self._apply_mode()

    def _paint_ended(self):
        self.ended_why.config(text=self.screen.reason)
        s = getattr(self, "_last_stats", {}) or {}
        did, would = s.get("did_kb", 0), s.get("would_kb", 0)
        if would > 0 and did > 0:
            saved = 100.0 * (would - did) / would
            self.ended_stats.config(
                text="sent %.0f KB where a full-frame stream would have sent %.0f KB "
                     "-- %.0f%% lighter" % (did, would, saved))
        else:
            self.ended_stats.config(text="")

    def _back_to_lobby(self):
        self.screen.back_to_lobby()
        self._apply_mode()

    def _clear_tiles(self):
        for lbl in self.tiles:
            lbl.place_forget()
        self._photos.clear()

    # =====================================================================
    # controls -- always stored, applied now or on call entry
    # =====================================================================
    def _set_codec(self, val):
        """[CODEC_DEFAULT_IS_AUTO_V1] "auto" means PROBE, not VP8.

        This read `.get(val, fnav.CODEC_VP8)` -- so choosing "auto" in the UI forced
        software VP8, the exact opposite of what the control says. Combined with the
        pending default being "vp8", a client that had never touched the control was
        running VP8 at 720p no matter what the probe found.
        """
        self.pending.codec = val
        if self.call is None:
            return
        if val == "auto":
            try:
                self.call.codec_id = fnav.VideoEncoder.probe_best_codec(
                    fps=int(self.args.fps))
                LOG.info("codec: auto -> %s",
                         fnav.CODEC_NAME.get(self.call.codec_id, "?"))
            except Exception as e:
                self.status.config(text="codec probe failed: %s" % type(e).__name__)
            return
        forced = {"vp8": fnav.CODEC_VP8, "h264": fnav.CODEC_H264_SW,
                  "h264hw": fnav.CODEC_H264_HW}.get(val)
        if forced is not None:
            self.call.codec_id = forced
            LOG.info("codec: forced %s", fnav.CODEC_NAME.get(forced, "?"))

    def _set_aspect(self, *_a):
        """[ASPECT_IS_CHOSEN_V1] Takes effect on the NEXT call.

        Changing it mid-call would rebuild the encoder at a new shape and every
        viewer's tile would jump; the ladder already changes shape once, on the
        way out of L5, and that is as much as one call should do.
        """
        self.pending.aspect = self.aspect_var.get()
        LOG.info("aspect %s (applies to the next call)", self.pending.aspect)

    def _set_quality(self, val):
        self.pending.quality = val
        if self.call is not None:
            self.call.set_level_cap(self.pending.cap())
        self._refresh_link_badge()

    def _apply_preset(self, preset):
        self.pending.bps = preset.bps
        self.pending.jitter_ms = preset.jitter_ms
        # [SLIDER_PUSHES_ON_SETTLE_V1] Same reason: the preset publishes itself
        # below. Without this the widget move fired _set_custom too and a second,
        # preset-less push landed on top of it, erasing the attribution.
        self._slider_suppress = True
        try:
            self.bps_var.set(2000 if preset.kbps == 0 else preset.kbps)
            self.jit_var.set(preset.jitter_ms)
        finally:
            self._slider_suppress = False
        self._push_link(preset)

    # [SLIDER_PUSHES_ON_SETTLE_V1] ttk.Scale fires command= on EVERY value
    # change: once per motion event while dragging, and again on a programmatic
    # .set(). Each one was a throttle change, an encoder-affecting ceiling
    # recalculation, and TWO tuple writes. A single drag produced dozens --
    # 1899000, 1096000, 1039000, 652000 in one gesture, all of them intermediate
    # positions nobody chose.
    #
    # Worse, _reconcile_link() sets bps_var to follow the tuple, which re-enters
    # this callback and republishes -- so adopting a remote setting immediately
    # re-published it as a local "custom" one, overwriting the preset
    # attribution with nothing.
    SLIDER_SETTLE_MS = 250

    def _set_custom(self, _val=None):
        """Coalesce a drag into one push, after the control settles."""
        if getattr(self, "_slider_suppress", False):
            return                     # programmatic .set(): not a user gesture
        after_id = getattr(self, "_slider_after", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception as e:
                LOG.error("link: after_cancel failed: %r", e)
        self._slider_after = self.after(self.SLIDER_SETTLE_MS,
                                        self._commit_custom)

    def _commit_custom(self):
        self._slider_after = None
        kbps = int(float(self.bps_var.get()))
        self.pending.bps = 0 if kbps >= 2000 else kbps * 1000
        self.pending.jitter_ms = int(float(self.jit_var.get()))
        self._push_link(None)

    def _push_link(self, preset):
        if self.call is not None:
            # No bare pass: a throttle that did not apply is the control lying
            # about itself, which is the exact failure this shell was built to
            # end.
            try:
                self.call.set_throttle(self.pending.bps)
                self.call.set_jitter(self.pending.jitter_ms)
            except Exception as e:
                self.status.config(text="uplink shaping FAILED: %s" % (e,))
                LOG.error("link: set_throttle/set_jitter failed: %r", e)
        # Publish so the far side follows. One person adjusts the link; nobody else
        # has to touch anything.
        #
        # [PUBLISH_RETURN_IS_NOT_DECORATION_V1] set_local() records "this value is
        # published, so reading it back is not a change". It ran BEFORE the put,
        # unconditionally -- so a put that failed still left SharedLink claiming
        # the value was live, take() then saw no change on the read-back, and the
        # push was never retried. Record it only once the put has actually landed;
        # a failed publish deliberately leaves the OLD value in SharedLink so the
        # next poll sees a difference and this is pushed again.
        _prev = (self.link.bps, self.link.jitter_ms, self.link.preset)
        self.link.set_local(self.pending.bps, self.pending.jitter_ms,
                            preset.name if preset else "")
        if self.screen.session:
            try:
                # [PUBLISH_RETURN_IS_NOT_DECORATION_V1] set_link/set_media_speed
                # return a bool and both returns were discarded, so a put that
                # timed out was indistinguishable from one that landed. Worse,
                # self.link.set_local() above has ALREADY recorded the value as
                # published, so there is no retry -- the next read_link poll
                # returns whatever is really in the store and the slider reverts
                # to it. One transient PUT_FAILED was enough to make the control
                # look like it was ignoring the user.
                _ok_link = self.cp.set_link(self.screen.session, self.pending.bps,
                                            self.pending.jitter_ms,
                                            preset.name if preset else "")
                # [MEDIASPEED_V1] THIS is the path the slider takes -- _set_custom
                # and every preset land here, not in _apply_pending. Publishing
                # only there meant moving the slider throttled the uplink and
                # never told the media host, so the downlink stayed wide open.
                #
                # A client's link is symmetric: one number, both legs.
                # [MEDIACONTROL_BELONGS_TO_THE_CALL_V1] one row, the call's own
                _ok_speed = self.cp.set_media_control(self.screen.session,
                                                     speed_bps=self.pending.bps)
                if not _ok_link or not _ok_speed:
                    _which = ", ".join(n for n, ok in
                                       (("link", _ok_link), ("media control", _ok_speed))
                                       if not ok)
                    self.status.config(
                        text="link publish FAILED (%s did not write) -- uplink "
                             "shaped locally, far side NOT told" % _which)
                    LOG.error("link: put returned False for %s "
                              "(bps=%s jitter=%s session=%s) -- local setting is "
                              "NOT published and will be overwritten by the next "
                              "poll", _which, self.pending.bps,
                              self.pending.jitter_ms, self.screen.session)
                    # The local record must not claim a publish that did not
                    # happen: leave SharedLink holding the OLD value so the next
                    # poll sees a difference and this push is retried.
                    # Roll the local record back: it must not claim a publish
                    # that did not happen.
                    self.link.set_local(_prev[0], _prev[1], _prev[2])
                    self._link_publish_failed = True
                else:
                    self._link_publish_failed = False
            except Exception as e:
                # Same rollback on a raise as on a False return.
                self.link.set_local(_prev[0], _prev[1], _prev[2])
                self._link_publish_failed = True
                self.status.config(text="link publish FAILED: %s" % (e,))
                LOG.error("link: publish failed: %r (bps=%s jitter=%s) -- local "
                          "setting NOT published", e, self.pending.bps,
                          self.pending.jitter_ms)
        for b, p in self.preset_btns:
            on = preset is not None and p.name == preset.name
            b.config(bg=AMBER if on else WATER3, fg=WATER if on else BONE)
        self._refresh_link_badge()

    def _refresh_link_badge(self):
        who = self.link.attribution()
        self.link_state.config(text=self.pending.summary()
                                    + (("   (%s)" % who) if who else ""))
        # Say what is ABOUT to happen, from the real per-rung bitrates, so the room
        # watches a prediction come true instead of being told afterwards.
        # [CEILING_NOT_YET_PROBED_V1] fnav sets Call.ceiling in _probe(), which runs
        # inside Call.run() on the worker thread -- so it does not exist at the moment
        # _enter_call paints, and if the connect is refused it never exists at all.
        # Reading it bare crashed the Tk callback the instant a media host was not
        # accepting on :9000. The device ceiling is the right answer until the probe
        # has spoken; _paint_ladder and _tick_video already read it this way and this
        # one spot did not.
        ceil_idx = UI.ceiling_from_devices(
            self._preflight["cam"], self._preflight["mic"],
            self.args.no_video, self.args.no_audio)
        if self.call is not None:
            ceil_idx = getattr(self.call, "ceiling", ceil_idx)
        want = UI.expected_rung(self.pending.bps, fnav.RUNG_VIDEO, ceil_idx)
        self.predict_lbl.config(
            text="this link should carry %s %s" % (L.code(want), L.name(want)))
        if self.pending.armed():
            self.link_badge.config(text="LINK %s" % self.pending.summary().upper())
            self.link_badge.pack(side="right", padx=8)
        else:
            self.link_badge.pack_forget()

    # =====================================================================
    # preflight
    # =====================================================================
    # =====================================================================
    # games
    # =====================================================================
    GAME_CATALOG = [
        ("Card Games", [
            ("hearts", "Hearts", "card"),
            ("liarsdice", "Liar's Dice", "card"),
        ]),
        ("Board Games", [
            ("backgammon", "Backgammon", "board"),
            ("connectfour", "Connect Four", "card"),
            ("reversi", "Reversi", "card"),
        ]),
    ]

    def _game_app_path(self, game, family):
        """Where a game's entry point lives. One place, so the chooser and the
        launcher cannot disagree about what is installed."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if family == "board":
            return os.path.join(root, "boardgame", "app", "bg_play.py")
        if family == "bg":
            return os.path.join(root, "net.frognet.backgammon", "app", "bg_app.py")
        if game in ("connectfour", "reversi"):
            return os.path.join(root, "games-common",
                                {"connectfour": "connect4_play.py",
                                 "reversi": "reversi_play.py"}[game])
        return os.path.join(root, "games-common", "game_app.py")

    def _games_popup(self):
        """[OFFER_ONLY_WHAT_EXISTS_V1] Only games whose app is actually on this box.

        GAME_CATALOG is a fixed list of five and every one of them was offered
        unconditionally; the existence check lived in _launch_game, so you picked a
        game, the window closed, and a status line said the app was missing. A chooser
        that lists things that are not there is worse than no chooser.
        """
        installed, absent = [], []
        for category, games in self.GAME_CATALOG:
            here = [(g, t, f) for g, t, f in games
                    if os.path.exists(self._game_app_path(g, f))]
            gone = [t for g, t, f in games
                    if not os.path.exists(self._game_app_path(g, f))]
            if here:
                installed.append((category, here))
            absent.extend(gone)

        win = tk.Toplevel(self)
        win.title("Games")
        win.configure(bg=WATER2)
        win.attributes("-topmost", True)
        tk.Label(win, text="FrogNet Games", fg=LILY, bg=WATER2,
                 font=F_BIG).pack(pady=(16, 2), padx=24)
        if not installed:
            tk.Label(win, text="no game bundles are installed on this machine",
                     fg=ROSE, bg=WATER2, font=F_MED).pack(padx=24, pady=(6, 4))
            tk.Label(win, text="they live beside the communicator in\n%s"
                               % os.path.dirname(os.path.dirname(
                                   os.path.abspath(__file__))),
                     fg=MUTE, bg=WATER2, font=F_SMALL,
                     justify="left").pack(padx=24, pady=(0, 12))
        else:
            tk.Label(win, text="each opens in its own window, over the tuple space",
                     fg=MUTE, bg=WATER2, font=F_SMALL).pack(pady=(0, 12), padx=24)
        for category, games in installed:
            tk.Label(win, text=category.upper(), fg=MUTE, bg=WATER2,
                     font=F_LABEL).pack(anchor="w", padx=24, pady=(8, 4))
            for game, title, family in games:
                tk.Button(win, text=title,
                          command=lambda g=game, f=family, w=win: (
                              self._launch_game(g, f), w.destroy()),
                          bg=REED, fg=WATER, relief="flat", width=22,
                          pady=6).pack(padx=24, pady=2)
        if absent:
            tk.Label(win, text="not installed: " + ", ".join(sorted(absent)),
                     fg=MUTE, bg=WATER2, font=F_SMALL,
                     wraplength=300).pack(padx=24, pady=(12, 0))
        tk.Label(win, text="", bg=WATER2).pack(pady=6)

    def _launch_game(self, game, family, table="table-1"):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        host = getattr(self.cp, "dbhost", "databasehost.frognet")
        if family == "board":
            app = os.path.join(root, "boardgame", "app", "bg_play.py")
            cmd = [sys.executable, app, "--connect", host, "--who", self.args.name,
                   "--gid", table]
        elif family == "bg":
            app = os.path.join(root, "net.frognet.backgammon", "app", "bg_app.py")
            cmd = [sys.executable, app, "--space", host, "--table", table,
                   "--who", self.args.name]
        elif game in ("connectfour", "reversi"):
            play = {"connectfour": "connect4_play.py", "reversi": "reversi_play.py"}[game]
            app = os.path.join(root, "games-common", play)
            cmd = [sys.executable, app, "--who", self.me_id, "--table", table,
                   "--dbhost", host, "--bundles-root", root]
        else:
            app = os.path.join(root, "games-common", "game_app.py")
            cmd = [sys.executable, app, "--game", game, "--table", table,
                   "--who", self.me_id, "--dbhost", host, "--bundles-root", root]
        if not os.path.exists(app):
            self.status.config(text="game app missing: %s"
                                    % os.path.relpath(app, root))
            return
        try:
            subprocess.Popen(cmd)
            self.status.config(text="launched %s (table %s)" % (game, table))
        except Exception as e:
            self.status.config(text="game launch failed: %s" % type(e).__name__)

    # =====================================================================
    # video
    # =====================================================================
    def _fmt_stats(self, s):
        # [DIAG_IS_UNGATED_V1] Every `.get(k, 0)` here turned an absent counter into
        # a reading of zero. A panel that cannot tell "nothing was measured" from
        # "the measurement was nothing" is not a diagnostic. Audio is shown too --
        # it is the whole of the traffic below L5.
        return ("up    %s fps  %s KB/s video  %s KB/s audio\n"
                "drop  %s fps\n"
                "down  %s fps (ref %s)\n"
                "key   %s KB x%s\n"
                "delta %s KB x%s\n"
                "%s" % (
                    UI.num(s.get("v_fps_sent")), UI.num(s.get("v_kbps")),
                    UI.num(s.get("a_kbps")),
                    UI.num(s.get("v_drop_fps")),
                    UI.num(s.get("v_fps_recv")), UI.num(s.get("ref_fps")),
                    UI.num(s.get("key_kb")), UI.num(s.get("key_n")),
                    UI.num(s.get("delta_kb")), UI.num(s.get("delta_n")),
                    self._fmt_transfer(s)))

    def _fmt_transfer(self, s):
        # A ratio needs both terms; absent ones are said so rather than shown as 0.
        did, would = s.get("did_kb"), s.get("would_kb")
        if did is None or would is None:
            return "transfer: no sample"
        if would <= 0:
            return "transfer: warming up"
        saved = 100.0 * (would - did) / would
        return "did %.0f KB / would %.0f KB\nsaved %.0f%%" % (did, would, saved)

    def _tick_video(self):
        if self.call is not None and self.call_thread is not None \
                and not self.call_thread.is_alive():
            self._on_call_dropped()
        c = self.call
        if c is not None:
            send_l = getattr(c, "_send_l", None)
            self._paint_ladder(send_l, getattr(c, "ceiling", L.MAX_IDX),
                               getattr(c, "allowed", None))
            try:
                s = c.stats.snapshot()
                # [RX_DIMS_ARE_MEASURED_V1] The decoder's per-sender frame sizes,
                # recorded by _paint_tiles on the previous pass. Folded into the
                # SAME snapshot every readout already consumes, so the wire
                # legend, the stats panel and the diagnostics window all show it
                # without three separate plumbings.
                s["rx_dims"] = getattr(self, "_rx_dims", None)
                self._last_stats = s
                # [DIAG_IS_UNGATED_V1] The legend is a readout, not a summary. It
                # shows what the sample says, with nothing substituted and nothing
                # left out.
                #
                # It read VIDEO only, so below L5 -- where video is shed by design --
                # every number on it went to zero and stayed there while audio
                # carried the call. And "-" for an absent counter is not the same
                # thing as 0.0 for an idle wire; both were being shown as whatever
                # the default happened to be. Audio is on the line now, and a
                # missing counter reads "--" while a measured zero reads 0.
                self.wire_lbl.config(text=UI.wire_legend(s))
                if self.stats_open.get():
                    self.stats_panel.config(text=self._fmt_stats(s))
                # [WINDOW_KNOWS_ITS_RUNG_V1] One line per COMPLETED measurement
                # window, labelled with the rung that window was measured at.
                #
                # This used to fire on `send_l != self._logged_rung` -- a rung
                # CHANGE -- and print s.get(...), which is snapshot(): the last
                # window, closed BEFORE the change. So the single line emitted
                # per transition described the rung the sender had just left
                # while naming the one it had just entered. Every step down to L4
                # printed L7's 24fps and ~150KB/s against "rung L4"; every
                # promotion out of L4 printed L4's zeros against "rung L5".
                #
                # It also meant a call that settled emitted NOTHING further, so
                # the last line printed was the only thing to read and it was
                # guaranteed to be about the wrong rung. Steady state was never
                # sampled at all.
                #
                # Gate on the window's own sequence number instead. `at` is the
                # window's rung, `now` is the live one -- when they differ, the
                # measurement is simply older than the current rung, which is a
                # fact about the sample and not a discrepancy to reconcile.
                _seq = s.get("seq")
                if _seq is not None and _seq != getattr(self, "_logged_seq", None):
                    self._logged_seq = _seq
                    _lv = s.get("levels") or []
                    _at = "-" if not _lv else (
                        L.code(_lv[0]) if len(_lv) == 1
                        else "%s..%s" % (L.code(_lv[0]), L.code(_lv[-1])))
                    LOG.info("at %s  now %s  ceiling=%s  %ss window  "
                             "%s fps  %s KB/s video  %s KB/s audio  drops %s/s  %s",
                             _at,
                             L.code(send_l) if send_l is not None else "-",
                             L.code(getattr(c, "ceiling", L.MAX_IDX)),
                             s.get("win_s"),
                             s.get("v_fps_sent"), s.get("v_kbps"),
                             s.get("a_kbps"),
                             s.get("v_drop_fps"), self.pending.summary())
                b = getattr(c, "bearer", None)
                # [DIAG_IS_UNGATED_V1] Record what the sample says. A zero is a
                # reading and goes in as a zero; a missing counter goes in as None
                # (absent data), and the two are NOT collapsed onto each other.
                #
                # Audio is recorded because below L5 it is the only traffic there is.
                # The panels used to carry v_kbps and v_drop_fps alone, so at L4 --
                # where video is shed by design -- every series the screen knew how to
                # draw went to zero and stayed there while the call ran fine.
                #
                # a_sheds is a DELTA off SotFDataPlane.audio_sheds, which is
                # cumulative and non-destructive. take_audio_sheds() must not be
                # called here: it clears the pending count the bearer reads to arm a
                # downgrade, so sampling it for a graph would steal the controller's
                # congestion signal.
                _aq = getattr(c, "sendq", None)
                _acum = getattr(_aq, "audio_sheds", None)
                if _acum is None:
                    _ashed = None
                else:
                    _prev = getattr(self, "_a_sheds_prev", None)
                    _ashed = 0 if _prev is None else max(0, _acum - _prev)
                    self._a_sheds_prev = _acum
                self.history.add(time.time(),
                                 rung=send_l,
                                 bearer=getattr(b, "idx", None),
                                 kbps=s.get("v_kbps"),
                                 drops=s.get("v_drop_fps"),
                                 a_kbps=s.get("a_kbps"),
                                 a_sheds=_ashed,
                                 throttle_kbps=(self.pending.bps // 1000),
                                 bad=getattr(b, "_bad", None),
                                 good=getattr(b, "_good", None))
                _ceil = getattr(c, "ceiling", L.MAX_IDX)
                _why = ""
                if _ceil < 5 and not self.args.no_video:
                    # [MIC_GATES_VIDEO_V1] a ceiling below L5 is a CAPABILITY limit,
                    # not congestion -- the link is innocent and the user should not
                    # be left staring at a pinned ladder wondering which it is.
                    _why = ("   no microphone on this box: video cannot be sent"
                            if not getattr(c, "have_mic", True)
                            else "   this box cannot source video")
                # [DIAG_IS_UNGATED_V1] `s.get("v_drop_fps", 0)` reported an absent
                # counter as a clean wire. Video drops AND audio sheds, each shown
                # as measured or as "--" when there is no sample.
                _adrop = getattr(getattr(c, "sendq", None), "audio_sheds", None)
                _aprev = getattr(self, "_legend_asheds_prev", None)
                self._legend_asheds_prev = _adrop
                _adelta = (None if _adrop is None or _aprev is None
                           else max(0, _adrop - _aprev))
                # [WIRE_ON_THE_DIAG_SCREEN_V1] The diagnostics window shows the
                # same audio-shed delta this line does. Published rather than
                # recomputed: two readouts deriving the same figure from the same
                # cumulative counter would each consume it on their own beat and
                # disagree.
                self._a_shed_delta = _adelta
                # [MEDIAHOLD_IS_MEMORY_V1] Read on a slow beat: this is state that
                # stays true, not an event that must be caught. A read per 80ms UI
                # tick would be a tuple read per frame for no more information.
                _now = time.time()
                if _now - getattr(self, "_holds_at", 0.0) >= 2.0:
                    self._holds_at = _now
                    try:
                        self._holds = self.cp.media_holds()
                    except Exception as e:
                        # Absence of an answer is not an answer of "no backlog".
                        self._holds = None
                        LOG.debug("MediaHold read failed: %r", e)
                    self.hold_lbl.config(
                        text=UI.hold_legend(getattr(self, "_holds", None)),
                        fg=ROSE if (self._holds and any(
                            h.get("awaiting_keyframe") for h in self._holds))
                        else MUTE)
                self.rung_sub.config(
                    text="ceiling %s   drops %s/s video  %s/s audio   %s%s"
                         % (L.code(_ceil),
                            UI.num(s.get("v_drop_fps")),
                            UI.num(_adelta),
                            self.pending.summary(), _why))
            except Exception:
                pass
            try:
                self._paint_tiles(c)
            except Exception as e:
                # [TICK_ALWAYS_REARMS_V1] see below
                LOG.warning("paint_tiles: %s: %s", type(e).__name__, e)
        # [TICK_ALWAYS_REARMS_V1] The re-arm is the LAST thing and nothing above it may
        # skip it. _paint_tiles used to sit outside the try, so one paint exception
        # killed this loop for good: the window stayed alive and the video simply
        # stopped updating, with nothing in any log.
        #
        # And this used to ALSO schedule _tick_preview, which reschedules itself -- so
        # every video tick spawned another self-perpetuating preview chain. Minutes in,
        # hundreds of chains are each re-arming every 80ms and Tk's callback queue is
        # saturated: the video "starts out fine and stops updating". The preview loop is
        # started ONCE, in __init__.
        self.after(80, self._tick_video)

    def _paint_tiles(self, c):
        # [TILE_DIAG_V1] Painter tracing at DEBUG (--verbose), rate-limited to one
        # pass per second so it cannot drown the log at 24 fps.
        #
        # Every branch the painter can take is named: what the decoder holds and
        # how old each frame is, the raw stage geometry BEFORE the max() floors
        # hide a not-yet-laid-out window, which frame matched as self, each
        # tile's placement rectangle, and the reason any tile ends up blank.
        # Two display changes were shipped on a theory and both took the video
        # away, because none of this was visible.
        _dg = (LOG.isEnabledFor(logging.DEBUG)
               and (time.time() - getattr(self, "_tdiag_at", 0.0) >= 1.0))
        if _dg:
            self._tdiag_at = time.time()
        if cv2 is None:
            if _dg:
                LOG.debug("tile: cv2 is None -- painter returns, no tile drawn")
            return
        frames = []
        sl = getattr(c, "_send_l", None)
        audio_only = sl is not None and sl < 5
        if getattr(c, "have_cam", False) or not self.args.no_video:
            if audio_only:
                frames.append(("you", None, "video shed -- audio is holding the call"))
            else:
                frames.append(("you", getattr(c, "_self_frame", None),
                               "camera starting..."))
        if _dg:
            _sf = getattr(c, "_self_frame", None)
            LOG.debug("tile: send_l=%s audio_only=%s have_cam=%s no_video=%s "
                      "self_frame=%s", sl, audio_only,
                      getattr(c, "have_cam", None),
                      getattr(self.args, "no_video", None),
                      None if _sf is None else "%dx%d" % (_sf.shape[1], _sf.shape[0]))
        try:
            _t = c.vdec.tiles()
            if _dg:
                # A tile that is present but never updating is a frozen picture,
                # and at this layer that is indistinguishable from a live one.
                # The age is the only thing that separates them.
                try:
                    _ages = {k: (lambda a: None if a is None else round(a, 1))(
                                 c.vdec.frame_age(k)) for k in _t}
                except Exception as _e:
                    _ages = "frame_age unavailable: %r" % (_e,)
                LOG.debug("tile: vdec keys=%s shapes=%s age_s=%s",
                          sorted(_t.keys()),
                          {k: (None if v is None else "%dx%d" % (v.shape[1], v.shape[0]))
                           for k, v in _t.items()}, _ages)
            # [RX_DIMS_ARE_MEASURED_V1] The decoder is the only place the size of
            # an ARRIVING picture is known -- the wire legend carries rates and
            # drops but never said how big the incoming frame actually is, so a
            # far end that had quietly dropped to 360p looked identical to one
            # holding 720p. Recorded here because this is where the frames are
            # already in hand; no extra decode, no extra read.
            _dims = {}
            for src, img in sorted(_t.items()):
                if img is not None:
                    frames.append((src, img, None))
                    _dims[src] = (img.shape[1], img.shape[0])
            self._rx_dims = _dims
        except Exception as _e:
            # Was a bare pass: no remote tiles, no reason, forever.
            # [RX_IS_PER_SOURCE_V1] Distinguish "not up yet" from "failed".
            # Call.__init__ sets vdec = None and run() builds it, so between
            # constructing a Call and run() reaching that line -- every redial,
            # on the worker thread, while this painter is already ticking on the
            # main thread -- vdec is legitimately None. Reporting that as a
            # decoder failure sent an afternoon chasing a decoder that was fine.
            if getattr(c, "vdec", None) is None:
                LOG.debug("tile: decoder not built yet (call starting up)")
            else:
                LOG.warning("tile: vdec.tiles() failed: %r", _e)
            # [RX_DIMS_ARE_MEASURED_V1] Do NOT leave the last good sizes on
            # screen when the decoder stopped answering -- a stale dimension
            # reads as a live one.
            self._rx_dims = None
        # [SELF_IS_AN_INSET_V1] The people you are talking TO get the stage; you get
        # a thumbnail. Equal horizontal bands gave half the display to your own
        # camera in a two-way call, which is the one picture you least need to see.
        #
        # Self is frames[0] by construction above. Split it out, lay the remote
        # tiles across the full stage, then place self last in a corner -- Tk stacks
        # by placement order, and lift() makes it explicit -- so it floats ABOVE the
        # remote video and both play at once.
        _self_fr = frames[0] if frames and frames[0][0] == "you" else None
        _remote = [f for f in frames if f is not _self_fr]
        if _dg:
            # Self is matched on the literal label "you". If that ever changes,
            # _self_fr is None, everything lands in _remote and the inset is
            # never placed.
            LOG.debug("tile: frames=%s self_matched=%s remote=%d",
                      [(f[0], f[1] is not None, f[2]) for f in frames],
                      _self_fr is not None, len(_remote))

        _raw_w, _raw_h = self.stage.winfo_width(), self.stage.winfo_height()
        sw = max(160, self.stage.winfo_width())
        sh = max(120, self.stage.winfo_height() - 44)      # minus the control row

        n_rem = min(len(_remote), MAX_TILES - (1 if _self_fr else 0))
        band_h = max(1, sh // max(1, n_rem))
        if _dg:
            # Raw geometry before the floors: winfo_* returns 1 until the stage
            # is laid out, so sw/sh silently become 160/120 and every rectangle
            # is computed against a window that does not exist. Tk places a
            # negative y without complaint and the tile is simply off-screen --
            # which looks exactly like "no video".
            LOG.debug("tile: stage raw=%dx%d -> sw=%d sh=%d n_rem=%d band_h=%d "
                      "MAX_TILES=%d tiles=%d photos=%d tmp=%d",
                      _raw_w, _raw_h, sw, sh, n_rem, band_h, MAX_TILES,
                      len(self.tiles), len(getattr(self, "_photos", [])),
                      len(getattr(self, "_tmp", [])))

        def _fill(lbl, slot, label, img, placeholder, x, y, w, h, rel=False):
            # Every reason a tile can end up blank, named.
            if _dg and (w <= 0 or h <= 0 or (not rel and (x < 0 or y < 0))):
                LOG.debug("tile:   slot=%d %r OFF-SCREEN/ZERO x=%d y=%d w=%d h=%d "
                          "rel=%s -- nothing visible", slot, label, x, y, w, h, rel)
            try:
                if rel:
                    lbl.place(relx=0, y=y, relwidth=1, height=h)
                else:
                    lbl.place(x=x, y=y, width=w, height=h)
            except Exception as e:
                LOG.warning("tile:   slot=%d %r place() failed: %r", slot, label, e)
                return
            if img is not None:
                try:
                    ih, iw = img.shape[:2]
                    scale = min(w / iw, h / ih)
                    _pw, _ph = max(1, int(iw * scale)), max(1, int(ih * scale))
                    photo = _photo(img, _pw, _ph, self._tmp[slot])
                    self._photos[slot] = photo
                    lbl.config(image=photo, text="", compound="center")
                    if _dg:
                        LOG.debug("tile:   slot=%d %r IMAGE %dx%d -> %dx%d at "
                                  "x=%d y=%d w=%d h=%d rel=%s",
                                  slot, label, iw, ih, _pw, _ph, x, y, w, h, rel)
                except Exception as e:
                    # Falls back to the label text -- silently, until now. On
                    # screen that looks like "no signal".
                    lbl.config(image="", text=label, fg=BONE)
                    LOG.warning("tile:   slot=%d %r image failed (%r) -- showing "
                                "text", slot, label, e)
            else:
                lbl.config(image="", text=placeholder or label, fg=AMBER,
                           font=F_BIG)
                if _dg:
                    LOG.debug("tile:   slot=%d %r PLACEHOLDER %r",
                              slot, label, placeholder or label)

        _slot = 0
        for i in range(n_rem):
            label, img, placeholder = _remote[i]
            _fill(self.tiles[_slot], _slot, label, img, placeholder,
                  0, i * band_h, sw, band_h, rel=True)
            _slot += 1

        if _self_fr is not None and _slot < MAX_TILES:
            label, img, placeholder = _self_fr
            # 20% of the stage width, 16:9. The remote caller is what you are
            # looking at; your own camera is a confidence monitor, not content.
            iw_ = max(96, int(sw * 0.20))
            ih_ = max(90, int(iw_ * 9 / 16))
            pad = 12
            _fill(self.tiles[_slot], _slot, label, img, placeholder,
                  sw - iw_ - pad, sh - ih_ - pad, iw_, ih_)
            self.tiles[_slot].lift()                # float above the remote video
            if _dg:
                LOG.debug("tile:   inset slot=%d at x=%d y=%d %dx%d (stage %dx%d)",
                          _slot, sw - iw_ - pad, sh - ih_ - pad, iw_, ih_, sw, sh)
            _slot += 1

        for i in range(_slot, MAX_TILES):
            self.tiles[i].place_forget()
        if _dg:
            LOG.debug("tile: placed=%d hidden=%d..%d", _slot, _slot, MAX_TILES - 1)
        n = n_rem
        if n == 0:
            self.tiles[0].config(image="", text="connected -- waiting for video...",
                                 fg=AMBER, font=F_BIG)
            self.tiles[0].place(relx=0.5, rely=0.45, anchor="center")

    def _quit(self, why="the window was closed"):
        # [A_SILENT_EXIT_IS_A_BUG_REPORT_V1] record it before tearing anything
        # down, so the reason survives whatever happens next.
        self._exit_reason = why
        print("[communicator] shutting down: %s" % why, flush=True)
        self._stop.set()
        self._preview_off()
        threading.Timer(2.5, lambda: os._exit(0)).start()
        try:
            self._hang()
        except Exception:
            pass
        try:
            self.cp.announce("offline")
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)


def _build_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--id", default=None)
    ap.add_argument("--relay", default=None, help="media relay HOST:PORT override")
    ap.add_argument("--serve", nargs="?", const="0.0.0.0:9000", default=None,
                    help="host the relay in-process (optionally BIND:PORT)")
    ap.add_argument("--in", dest="in_dev", default=None)
    ap.add_argument("--out", dest="out_dev", default=None)
    ap.add_argument("--cam", type=int, default=0)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--codec", choices=["auto", "vp8", "h264", "h264hw"], default="auto")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-duck", action="store_true")
    ap.add_argument("--no-splash", action="store_true",
                    help="skip the frog door and open straight into the lobby")
    ap.add_argument("--verbose", action="store_true",
                    help="debug-level logging (every roster poll, every tick)")
    return ap.parse_args(argv)


def _print_build_id():
    """Fingerprint the modules that matter, from the files actually imported.

    sys.modules, not a directory listing: if an old copy shadows the new one on
    sys.path, this reports the one Python loaded, which is the only one that
    matters.
    """
    import hashlib
    import sys as _sys
    parts = []
    for name in ("communicator_live", "comms_control", "comms_ui", "fnav"):
        mod = _sys.modules.get(name)
        if mod is None and name == "communicator_live":
            # Run as a script, this module is __main__ -- not under its own
            # name. Reported MISSING while the other three hashed fine, which
            # made the one file most likely to be stale the one file the stamp
            # could not vouch for.
            _m = _sys.modules.get("__main__")
            if getattr(_m, "__file__", "").endswith("communicator_live.py"):
                mod = _m
        path = getattr(mod, "__file__", None) if mod else None
        if not path:
            parts.append("%s=MISSING" % name)
            continue
        try:
            # [A_FINGERPRINT_IS_OF_THE_CODE_NOT_THE_BYTES_V1] Normalise line
            # endings before hashing. Opening a file in Notepad rewrites it
            # CRLF: identical code, different bytes, different hash -- and the
            # stamp then reports a stale file when nothing changed but the
            # newlines, which is precisely when it must not lie.
            raw = open(path, "rb").read().replace(b"\r\n", b"\n")
            parts.append("%s=%s" % (name, hashlib.sha256(raw).hexdigest()[:8]))
        except OSError as e:
            parts.append("%s=UNREADABLE(%s)" % (name, type(e).__name__))
    print("[communicator] build %s" % "  ".join(parts), flush=True)
    _cl = _sys.modules.get("communicator_live") or _sys.modules.get("__main__")
    if _cl and getattr(_cl, "__file__", None):
        print("[communicator] running from %s" % os.path.dirname(
            os.path.abspath(_cl.__file__)), flush=True)


def main(argv=None):
    args = _build_args(argv)
    setup_logging(args.name, args.verbose)
    # [BEARER_DIAG_OPT_IN_V1] --verbose also turns on the per-sample ladder
    # trace. FROGNET_BEARER_DIAG=1 turns it on by itself.
    if args.verbose:
        fnav.BEARER_DIAG = True
    cap = fnav.media_capability()
    LOG.info("media capability: video=%s audio=%s missing=%s",
             cap["video"], cap["audio"], cap["missing"] or "none")
    if cv2 is None:
        print("warning: cv2 not available -- video tiles will not render")
    # [A_SILENT_EXIT_IS_A_BUG_REPORT_V1] mainloop() returning is not an event
    # anybody sees. Observed 2026-08-11: the window printed "window up" and the
    # process went straight back to the prompt -- no window, no traceback, no
    # codec probe, nothing. A Tk callback that raises goes through
    # report_callback_exception, and anything that calls quit() or destroy()
    # ends the loop with no trace at all. Both were silent.
    app = App(args)
    try:
        app.mainloop()
    except BaseException as e:
        print("[communicator] mainloop raised %s: %s"
              % (type(e).__name__, e), flush=True)
        raise
    finally:
        _why = getattr(app, "_exit_reason", None)
        if _why:
            print("[communicator] window closed: %s" % _why, flush=True)
        else:
            print("[communicator] mainloop returned with no reason recorded -- "
                  "something called quit() or destroy() without saying why",
                  flush=True)


if __name__ == "__main__":
    main()
