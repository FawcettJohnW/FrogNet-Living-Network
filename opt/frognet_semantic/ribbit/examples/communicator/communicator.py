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
communicator.py - FrogNet Communicator, the phone-shaped desktop shell, LIVE.

Same surface designed with John (Home / Call / Text / Games / Calendar), but the
presence grid, Call/Hang Up, and Text are now driven by the REAL backend that already
exists next to this file - communicator_app.py - not local state:

  presence / roster   -> roster_list/register/deregister  (frognet_roster_server :8780)
  chat                -> chat_send/chat_read through the transient DB (frognet_tuples)
  call signaling      -> call tuples in the transient DB (ringing/accepted/ended)
  A/V media           -> spawns frognet_communicator.py --connect <host>:9000 (ffmpeg)

Run:
    python3 communicator.py --host 10.250.250.1 --name john
    python3 communicator.py                       # prompts for name + host, persists

What is REAL vs LOCAL in this file:
  REAL  : the People grid (live roster), Call -> ring -> media, incoming ring dialog,
          Text (converges through the same session tuples the proven app uses).
  LOCAL : the self-view / uplink / rung / drop-link / audio-video presentation toggles.
          communicator_app exposes no backend wire for those; they stay local UI state,
          marked, exactly as the original shell had them. Wire them to av_caps/ladder
          when that surface lands; the UI above does not change.

stdlib only (tkinter + urllib + subprocess via the backend). A/V Call needs ffmpeg on
PATH; presence/text/launchers do not.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

import tkinter as tk
from tkinter import font as tkfont

# the proven, Tk-free backend (Tk only loads inside its run_shell, not at import)
import communicator_app as B

# ---- canonical media stack: calls ride sotf_media_backing/media_stream via call_media,
# adaptive over the server's bearer tuple (drop-don't-stall). The capture half of the
# legacy engine (UNPINNED-framerate live capture, mic buffer, IVF iter) is reused; its
# blocking send path is retired from the call path. Tolerant import so a node missing the
# stack still loads the app (it just can't place SotF calls). ----
try:
    import call_media as CMED
    from frognet_communicator import (
        probe_caps as _cap_probe,
        _live_cmd as _cap_live_cmd,
        _iter_ivf_stream as _cap_iter_ivf,
        _MicBuffer as _CapMicBuffer,
        AUDIO_BYTES_PER_SEC as _CAP_AUDIO_BPS,
    )
    _SOTF_CALLS = True
except Exception as _e:                       # pragma: no cover
    CMED = None
    _SOTF_CALLS = False

# ---- palette: deep slate-green, high contrast. Surfaces are grey-green (NOT black);
# text is near-white; borders are visible. Video panes stay darker by design. ----
BG      = "#1b2623"   # app background - clearly not black
PANEL   = "#283733"   # cards lift off BG
PANELHI = "#35433e"
LINE    = "#5e7d70"   # borders you can actually see
INK     = "#f4f7f2"   # primary text, near white
INKDIM  = "#c6d3cb"   # secondary text, high readability
PAD     = "#54e08a"   # vivid lily-pad accent
PADDIM  = "#90c8a4"   # readable secondary green
WARN    = "#f1c463"
BAD     = "#ec8475"
VIDBG   = "#13211d"   # video / self-view surface (kept darker; video renders here)
CELL    = "#223530"   # a peer cell on the call grid
BADGE   = "#19271f"   # small overlay badges over video

PHONE_W, PHONE_H = 390, 780

SERVICES = [("Phone", "Call", True), ("Text", "Text", True),
            ("Games", "Games", False), ("Calendar", "Calendar", False)]
TABS = ["Home", "Call", "Text", "Games", "Calendar"]

LOBBY_SID = "lobby"        # standalone Text room when no call is active (session-scoped chat)


def _spawn(cmd):
    return subprocess.Popen(cmd, start_new_session=(os.name != "nt"))


def _media_proc_count():
    """Count live ffmpeg/ffplay processes - to expose orphans that survive a call
    teardown (a stuck capture ffmpeg holds the camera; the next call then captures
    only a few frames and stalls). Logged before each call and after each _kill."""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe",
                                  "/FI", "IMAGENAME eq ffplay.exe"],
                                 capture_output=True, text=True, timeout=5).stdout
            return out.lower().count("ffmpeg.exe") + out.lower().count("ffplay.exe")
        out = subprocess.run(["pgrep", "-c", "-x", "ffmpeg"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out or 0)
    except Exception:
        return -1


def _spawn_logged(cmd):
    """Spawn an engine subprocess with its stdout+stderr redirected into the shared
    log, so call failures are captured on every OS instead of vanishing into a
    console that may not even be visible (e.g. a Windows GUI launch). POSIX procs
    start a new session so _kill can take down the whole tree (incl. ffplay)."""
    try:
        with open(_PLOG, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] SPAWN: "
                    + " ".join(str(c) for c in cmd) + "\n")
            f.flush()
            return subprocess.Popen(cmd, stdout=f, stderr=f,
                                   start_new_session=(os.name != "nt"))
    except Exception:
        return subprocess.Popen(cmd, start_new_session=(os.name != "nt"))


def _kill(p):
    """Kill the engine subprocess AND its ffmpeg/ffplay children so devices (camera,
    ALSA audio) are released. Windows: taskkill /T. POSIX: kill the whole process
    group - otherwise orphaned ffplay keeps the audio device and the next call's
    playback fails with 'Device or resource busy'."""
    pid = getattr(p, "pid", "?")
    try:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, text=True)
            _plog(f"[KILL] taskkill /T pid={pid} rc={r.returncode} "
                  f"{(r.stdout or r.stderr).strip()[:160]}")
        else:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                _plog(f"[KILL] killpg pid={pid} ok")
            except Exception as e:
                p.terminate()
                _plog(f"[KILL] killpg pid={pid} failed ({e}); terminate() instead")
    except Exception as e:
        _plog(f"[KILL] pid={pid} error {e}; terminate()")
        try:
            p.terminate()
        except Exception:
            pass


def _close_call_entry(entry):
    """Tear down one element of an App.calls[peer] list. Two shapes:
      - a SotF tuple: ("sotf", CallSender, CallReceiver_in, stop_event[, capproc,
        micbuf]) - signal stop, close sender/receiver, kill capture ffmpeg, close mic.
      - a legacy subprocess handle - kill its process group (the engine path).
    Idempotent and exception-safe so hangup never wedges on a half-built call."""
    if isinstance(entry, tuple) and entry and entry[0] == "sotf":
        # ("sotf", CallSender, CallReceiver_in, stop_event[, capproc, micbuf])
        _, cs, rx_in, stop = entry[:4]
        try: stop.set()
        except Exception: pass
        for obj in (cs, rx_in):
            try:
                if obj is not None: obj.close()
            except Exception: pass
        for extra in entry[4:]:                     # capproc, micbuf
            try:
                if hasattr(extra, "terminate"): extra.terminate()
                elif hasattr(extra, "close"):    extra.close()
            except Exception: pass
        _plog("[KILL] sotf call entry closed")
    else:
        _kill(entry)


# --- pre-call A/V self-check: preview the SAME device inputs the engine captures
# (dshow on Windows, avfoundation on macOS, v4l2/alsa on Linux), via ffplay. No host,
# no call - what you see/hear here is what the call would send. ---
def _ffplay_path():
    ff = shutil.which("ffmpeg")
    if ff:
        cand = (ff[:-6] + "ffplay") if ff.lower().endswith("ffmpeg") else None
        if cand and (os.path.exists(cand) or os.path.exists(cand + ".exe")):
            return cand
    return shutil.which("ffplay")


_DEV_CACHE = None


def _list_dshow_devices():
    """(video_names, audio_names) from ffmpeg's dshow enumeration (Windows). Cached."""
    global _DEV_CACHE
    if _DEV_CACHE is not None:
        return _DEV_CACHE
    vids, auds = [], []
    ff = shutil.which("ffmpeg")
    if ff:
        try:
            p = subprocess.run([ff, "-hide_banner", "-list_devices", "true",
                                "-f", "dshow", "-i", "dummy"],
                               capture_output=True, text=True, timeout=10)
            out = (p.stderr or "") + (p.stdout or "")
            section = None
            for line in out.splitlines():
                low = line.lower()
                m = re.search(r'"([^"]+)"\s*\((video|audio)\)', line)   # ffmpeg 4.4+ form
                if m:
                    (vids if m.group(2) == "video" else auds).append(m.group(1))
                    continue
                if "video devices" in low:
                    section = "video"; continue
                if "audio devices" in low:
                    section = "audio"; continue
                m2 = re.search(r'"([^"]+)"', line)                       # older grouped form
                if m2 and "alternative name" not in low and section:
                    (vids if section == "video" else auds).append(m2.group(1))
        except Exception:
            pass
    dedup = lambda xs: list(dict.fromkeys(xs))
    _DEV_CACHE = (dedup(vids), dedup(auds))
    return _DEV_CACHE


def _os_default_camera():
    osn = platform.system()
    if osn == "Windows":
        vids, _ = _list_dshow_devices()
        return f"video={vids[0]}" if vids else None
    if osn == "Darwin":
        return "0"
    for p in ("/dev/video0", "/dev/video1"):
        if os.path.exists(p):
            return p
    return None


def _os_default_mic():
    """The system default microphone per OS. Windows has no 'default' pseudo-device,
    so take the first enumerated dshow audio device."""
    osn = platform.system()
    if osn == "Windows":
        _, auds = _list_dshow_devices()
        return f"audio={auds[0]}" if auds else None
    if osn == "Darwin":
        return ":0"            # avfoundation: audio device 0, no video
    return "default"           # alsa


def _list_avfoundation_devices():
    """(video, audio) as [(label, devstr)] from avfoundation enumeration (macOS)."""
    v, a = [], []
    ff = shutil.which("ffmpeg")
    if not ff:
        return v, a
    try:
        p = subprocess.run([ff, "-hide_banner", "-f", "avfoundation",
                            "-list_devices", "true", "-i", ""],
                           capture_output=True, text=True, timeout=10)
        out = (p.stderr or "") + (p.stdout or "")
        section = None
        for line in out.splitlines():
            low = line.lower()
            if "video devices" in low:
                section = "v"; continue
            if "audio devices" in low:
                section = "a"; continue
            m = re.search(r"\[(\d+)\]\s+(.*\S)", line)
            if m and section:
                idx, name = m.group(1), m.group(2)
                (v if section == "v" else a).append(
                    (name, idx if section == "v" else f":{idx}"))
    except Exception:
        pass
    return v, a


def _alsa_mics():
    """Real ALSA capture devices via `arecord -l`, offered as the tolerant 'default'
    and 'plughw:card,dev' forms (plughw converts rate/format; raw 'hw:' often can't
    open at 16k/mono and fails with 'No such file or directory')."""
    out = [("default", "default")]
    try:
        r = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=5)
        for line in (r.stdout or "").splitlines():
            m = re.match(r"card (\d+):\s*\S+\s*\[([^\]]*)\].*device (\d+):", line)
            if m:
                card, name, dev = m.group(1), m.group(2).strip(), m.group(3)
                out.append((f"{name} (plughw:{card},{dev})", f"plughw:{card},{dev}"))
    except Exception:
        pass
    return out


def _enumerate_devices(refresh=False):
    """(cameras, microphones), each [(label, device_string)] for the current OS."""
    global _DEV_CACHE
    if refresh:
        _DEV_CACHE = None
    osn = platform.system()
    if osn == "Windows":
        v, a = _list_dshow_devices()
        return ([(n, f"video={n}") for n in v], [(n, f"audio={n}") for n in a])
    if osn == "Darwin":
        return _list_avfoundation_devices()
    cams = [(p, p) for p in ("/dev/video0", "/dev/video1", "/dev/video2") if os.path.exists(p)]
    mics = [("default", "default")] + _alsa_mics()
    return cams, mics


def _camera_preview_cmd(camera, fps):
    ff = _ffplay_path()
    if not ff or not camera:
        return None
    osn = platform.system()
    base = [ff, "-hide_banner", "-loglevel", "error", "-window_title", "FrogNet - camera check"]
    fr = ["-framerate", str(fps)] if fps else []
    if osn == "Windows":
        return base + ["-f", "dshow", *fr, "-i", camera]
    if osn == "Darwin":
        return base + ["-f", "avfoundation", *fr, "-i", camera]
    return base + ["-f", "v4l2", *fr, "-i", camera]


def _mic_meter_cmd(mic):
    """ffmpeg command that prints the mic's live RMS level (dB) to stderr, using the
    SAME per-OS input the call captures from. We read those lines to drive a meter."""
    ff = shutil.which("ffmpeg")
    if not ff or not mic:
        return None
    fmt = {"Windows": "dshow", "Darwin": "avfoundation"}.get(platform.system(), "alsa")
    return [ff, "-hide_banner", "-loglevel", "info",
            "-f", fmt, "-i", mic,
            "-af", "astats=metadata=1:reset=1,"
                   "ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f", "null", "-"]


# --- embedded preview: ffmpeg pipes scaled PPM frames; the app renders them into the
# center tile via tk.PhotoImage (stdlib only, no PIL). Low fps/size keeps the dshow
# real-time buffer from overflowing. ---
def _cam_ppm_cmd(camera, fps=12, width=480):
    ff = shutil.which("ffmpeg")
    if not ff or not camera:
        return None
    osn = platform.system()
    pre = [ff, "-hide_banner", "-loglevel", "warning"]
    # NOTE: do NOT pin the input -framerate - dshow aborts if the device doesn't list
    # that exact rate ("Could not set video options"), which is why one cam opens and
    # another won't. Let the device pick its native mode; cap the rate on OUTPUT (-r).
    if osn == "Windows":
        inp = ["-f", "dshow", "-rtbufsize", "256M", "-i", camera]
    elif osn == "Darwin":
        inp = ["-f", "avfoundation", "-i", camera]
    else:
        inp = ["-f", "v4l2", "-i", camera]
    out = ["-vf", f"scale={width}:-2", "-r", str(fps), "-f", "image2pipe", "-vcodec", "ppm", "-"]
    return pre + inp + out


def _read_token(pipe):
    c = pipe.read(1)
    while c and c in b" \t\n\r":
        c = pipe.read(1)
    out = b""
    while c and c not in b" \t\n\r":
        out += c
        c = pipe.read(1)
    return out


def _read_exact(pipe, n):
    buf = b""
    while len(buf) < n:
        chunk = pipe.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _read_ppm_stream(pipe):
    """Yield canonical P6 PPM byte-blocks parsed from an ffmpeg image2pipe stdout."""
    while True:
        magic = _read_token(pipe)
        if magic != b"P6":
            return
        try:
            w = int(_read_token(pipe)); h = int(_read_token(pipe)); mx = int(_read_token(pipe))
        except ValueError:
            return
        data = _read_exact(pipe, w * h * 3)
        if data is None:
            return
        yield b"P6\n%d %d\n%d\n" % (w, h, mx) + data


_RMS_RX = re.compile(r"RMS_level=(-?\d+(?:\.\d+)?|-?inf|nan)")


_PLOG = os.path.join(tempfile.gettempdir(), "frognet_preview.log")


def _plog(s):
    try:
        with open(_PLOG, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {s}\n")
    except Exception:
        pass


def _rms_to_db(token: str) -> float:
    return -90.0 if ("inf" in token or token == "nan") else float(token)


def _mic_meter_console(mic) -> None:
    """Terminal level meter: a moving bar + dB. Talk and watch it move. Ctrl-C stops."""
    cmd = _mic_meter_cmd(mic)
    if not cmd:
        print("no mic / ffmpeg not found"); return
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         text=True, bufsize=1)
    print("mic level - speak; the bar should jump (Ctrl-C to stop):")
    try:
        for line in p.stderr:
            m = _RMS_RX.search(line)
            if not m:
                continue
            db = _rms_to_db(m.group(1))
            d = max(-60.0, min(0.0, db))
            n = int((d + 60.0) / 60.0 * 40)
            print("\r[" + "#" * n + "-" * (40 - n) + f"] {db:6.1f} dB ", end="", flush=True)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        try:
            _kill(p)
        except Exception:
            pass
        print()


def _hhmm(ts_ms: int) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(int(ts_ms) / 1000.0))
    except Exception:
        return ""


class Communicator(tk.Tk):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.me_id = cfg["id"]
        self.me_name = cfg["name"]
        self.host = cfg["host"]
        self.dbhost = cfg.get("dbhost", B.DBHOST)          # _control: coordination ONLY
        # game state / metrics are DATA, not discovery -> the elected data host, never _control
        self.gamehost = cfg.get("gamehost") or "databasehost.frognet"
        # call settings: RUNTIME-ONLY (never persisted, so two identities on one box
        # don't inherit each other's camera). Absent a flag, none is sent and the
        # engine picks its default.
        self.cam = cfg.get("camera")
        self.mic = cfg.get("mic") or _os_default_mic()   # default mic when none specified
        self.fps = cfg.get("fps")
        self.bitrate = cfg.get("bitrate_kbps")

        self.title("FrogNet Communicator")
        self.configure(bg="#101a17")
        self.geometry(f"{PHONE_W}x{PHONE_H}")
        self.minsize(340, 560)          # phone-sized start, but grow as large as you like

        # fonts
        self.f_mono = tkfont.Font(family="Consolas", size=10)
        self.f_mono_sm = tkfont.Font(family="Consolas", size=8)
        self.f_sans = tkfont.Font(family="Segoe UI", size=11)
        self.f_big = tkfont.Font(family="Segoe UI", size=15, weight="bold")
        self.f_huge = tkfont.Font(family="Consolas", size=20)

        # ---- live, backend-fed state (pumped from the poll thread) ----
        self.tab = tk.StringVar(value="Home")
        self.users: list = []                 # roster_list() -> [{id,name,status,...}]
        self.conversation: list = []          # merged chat_read() -> [(who, text, hhmm)]
        self.active_peer = None               # peer id of the call whose grid we show
        self.calls: dict = {}                 # peer_id -> [subprocess] live media
        self.sessions: dict = {}              # peer_id -> session_id
        self.seen_calls: dict = {}            # sid -> last state acted on
        self._group_sel: set = set()          # originator's selected group-call invitees
        self.stats_on: bool = False           # call stats overlay visible?
        self.chat_sid = LOBBY_SID             # which session the Text tab reads/writes

        # ---- embedded A/V preview state (camera frames into the center tile) ----
        self.preview_active = False
        self._frame = None                    # latest PPM bytes from the camera pipe
        self._photo = None                    # keep a ref so Tk doesn't GC the image
        self._mic_db = None                   # latest mic RMS level (dB)
        self._prev_stop = None
        self._prev_procs: list = []
        self.preview_lbl = None
        self.mic_bar = None
        self.mic_lbl = None
        self._tmpimg = os.path.join(tempfile.gettempdir(), f"frognet_preview_{os.getpid()}.ppm")
        self._cam_err = ""                    # last line of the camera ffmpeg's stderr
        self._prev_t0 = 0.0
        # ---- embedded call video (frames decoded into the Call tiles) ----
        self.call_embedded = False
        self.call_frames = {}                 # {"self": ppm, "peer": ppm}
        self._call_fifos = []
        self.metrics = {"sent": 0, "recv": 0, "disp_peer": 0, "disp_self": 0,
                        "rx": 0, "vdrop": 0, "viewers": 0, "wire": 0,
                        "converged": 0, "full": 0, "diff": 0, "repeat": 0}
        self.metrics_lbl = None
        self.peer_lbl = None
        self.self_lbl = None
        self._peer_img = None
        self._self_img = None

        # ---- LOCAL presentation state (no backend wire; marked) ----
        self.video_possible = True
        self.audio_possible = True
        self.video_enabled  = True
        self.audio_enabled  = True
        self.display_on     = True
        self.video_rung     = 2          # 0..3
        self.audio_rung     = 1          # 0=mono 1=stereo
        self.self_view      = "Half"     # Full/Half/Thumbnail/Off
        self.uplink         = "av"       # watch/audio/av
        self.ptt            = False
        self.speak_in       = False
        self.RUNGS = ["150k 160x120", "300k 320x240", "600k 320x240", "1200k 640x480"]

        # ---- background poll thread -> Tk queue ----
        self.q: "queue.Queue" = queue.Queue()
        self.stop = threading.Event()

        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True)
        self._build_header()
        self.content = tk.Frame(self.body, bg=BG)
        self.content.pack(fill="both", expand=True)
        self._build_tabbar()
        self.render()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<KeyPress-o>", lambda e: self.toggle_stats())   # 'o' toggles stats overlay
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self.after(400, self._drain)

    # ============================================================ backend I/O
    def _poll_loop(self):
        # Presence is a TRANSIENT tuple: re-assert it EVERY cycle so it stays fresh (it ages
        # out by PRESENCE_FRESH_S if we stop). Then read everyone's fresh presence. "Online"
        # is a continuously refreshed fact; stop refreshing -> you age out -> offline.
        last_chat = 0.0
        while not self.stop.is_set():
            try:
                B.roster_register(self.host, self.me_id, self.me_name)   # re-assert my presence
            except Exception as e:
                self.q.put(("error", str(e)))
            try:
                self.q.put(("users", B.roster_list(self.host)))         # read fresh roster
            except Exception as e:
                self.q.put(("error", str(e)))
            try:
                rows = B.T.get(B.SERVICE, "call", dbhost=self.dbhost, fresh_s=180)
                self.q.put(("calls", rows))
            except Exception:
                pass
            now = time.monotonic()
            if now - last_chat >= 2.0:
                last_chat = now
                try:
                    self.q.put(("chat", B.chat_read(self.chat_sid, self.dbhost)))
                except Exception:
                    pass
            self.stop.wait(2.0)

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "users":
                    sig = tuple((u.get("id"), u.get("name")) for u in payload)
                    self.users = payload
                    if sig != getattr(self, "_users_sig", None):
                        self._users_sig = sig          # only repaint on a real change
                        if (self.tab.get() in ("Call", "Home")
                                and not self.preview_active and not self.call_embedded):
                            self.render()
                elif kind == "calls":
                    self._handle_calls(payload)
                elif kind == "chat":
                    conv = [
                        ("you" if m.get("from") == self.me_id else m.get("from", "?"),
                         m.get("text", ""), _hhmm(m.get("ts", 0)))
                        for m in payload]
                    sig = tuple(conv)
                    self.conversation = conv
                    if sig != getattr(self, "_chat_sig", None):
                        self._chat_sig = sig
                        if (self.tab.get() in ("Text", "Call")
                                and not self.preview_active and not self.call_embedded):
                            self.render()
                elif kind == "error":
                    self._status(f"network: {payload}", BAD)
        except queue.Empty:
            pass
        self.after(500, self._drain)

    def _others(self):
        return [u for u in self.users if u.get("id") != self.me_id]

    def _name_of(self, pid):
        for u in self.users:
            if u.get("id") == pid:
                return u.get("name") or pid
        return pid

    # -- call signaling (memory, not messages - same tuples as the proven app) --
    def _new_session(self, peer):
        return f"{self.me_id}-{peer}-{int(time.time())}"

    def start_call(self, peer):
        """1:1 call - a group call of one invitee."""
        return self.start_group_call([peer])

    def _toggle_group(self, pid):
        """Toggle a person into/out of the next group call's invitee set (originator UI)."""
        if pid in self._group_sel:
            self._group_sel.discard(pid)
        else:
            self._group_sel.add(pid)
        self.render()

    def _start_selected_group(self):
        """Originator starts a multi-person call with the currently selected invitees."""
        peers = [p for p in self._group_sel if p not in self.calls and p not in self.sessions]
        self._group_sel = set()
        if peers:
            self.start_group_call(peers)

    def toggle_stats(self):
        """Show/hide the call stats overlay (key 'o' or the (i) button)."""
        self.stats_on = not self.stats_on
        lbl = getattr(self, "stats_lbl", None)
        btn = getattr(self, "stats_btn", None)
        try:
            if lbl is not None and lbl.winfo_exists():
                if self.stats_on:
                    lbl.place(x=10, y=44)
                else:
                    lbl.place_forget()
            if btn is not None and btn.winfo_exists():
                btn.config(fg=(PAD if self.stats_on else INKDIM))
        except Exception:
            pass

    def _stats_text(self) -> str:
        """Format the live call metrics for the overlay - per-sender, large + readable.
        Includes frame RATES (fps) and the Did/Would comparison: real adaptive bytes on the
        wire vs what a conventional fixed-rate stream would have sent."""
        m = self.metrics or {}
        sent = m.get("sent", 0); txd = m.get("tx_drop", 0)
        tot = sent + txd
        drop_pct = (100.0 * txd / tot) if tot else 0.0
        rung = m.get("rung", "-")
        depth = m.get("depth", 0)
        # frame rates over elapsed call time
        import time as _t
        elapsed = max(0.001, _t.time() - m.get("t0", _t.time()))
        up_fps = sent / elapsed
        shown = m.get("disp_peer", 0)
        down_fps = shown / elapsed
        self_shown = m.get("disp_self", 0)
        self_fps = self_shown / elapsed
        rung_name = ""
        try:
            import sotf_ladder as _L
            lv = _L.BY_IDX.get(int(rung)) if str(rung).isdigit() else None
            if lv:
                rung_name = f" {lv.get('name','')}"
        except Exception:
            pass
        # Did vs Would (wire efficiency vs conventional fixed-rate networking)
        did = m.get("did_bytes", 0)
        would = m.get("would_bytes", 0)
        did_kb = did / 1024.0; would_kb = would / 1024.0
        if would > 0:
            saved_pct = 100.0 * (would - did) / would
            ratio = (would / did) if did else 0.0
            didwould = (f"  did   : {did_kb:,.0f} KB  ({did_kb*8/elapsed:,.0f} kbps)\n"
                        f"  would : {would_kb:,.0f} KB  (conventional)\n"
                        f"  saved : {saved_pct:.0f}%   ({ratio:.1f}x lighter)")
        else:
            didwould = "  did/would : (warming up)"
        return (
            "CALL STATS  (o to hide)\n"
            f"-- UP (me) -------------\n"
            f"  camera->sent : {sent}  ({up_fps:.1f} fps)\n"
            f"  wire drop   : {txd}  ({drop_pct:.1f}%)\n"
            f"  send rung   : L{rung}{rung_name}\n"
            f"  queue depth : {depth}\n"
            f"-- DOWN (mixed) --------\n"
            f"  shown       : {shown}  ({down_fps:.1f} fps)\n"
            f"  self (local): {self_shown}  ({self_fps:.1f} fps)\n"
            f"-- WIRE vs CONVENTIONAL -\n"
            f"{didwould}"
        )

    def start_group_call(self, peers):
        """Originator-only multi-person call. The originator fixes the participant set at
        creation; there is NO join-in-progress. One session/sid for everyone; the call
        tuple carries the full participant list so each invitee rings and all join the
        SAME mediahost session. Cost stays one-up/one-down per client - the host mixes."""
        peers = [p for p in dict.fromkeys(peers) if p and p != self.me_id]
        if not peers:
            return
        # don't start if any invitee is already in a call/session with us
        if any(p in self.calls or p in self.sessions for p in peers):
            return
        sid = f"{self.me_id}-grp-{int(time.time())}"
        participants = [self.me_id] + peers          # FIXED set; originator first
        for p in peers:
            self.sessions[p] = sid
        primary = peers[0]
        self.active_peer = primary
        self.chat_sid = sid
        # one ringing tuple per invitee. SCOPE MUST be distinct per invitee
        # (session:sid:<to>) - a shared session:sid scope would have each put overwrite
        # the last, so only one invitee would ring. The shared SID still binds them to one
        # mediahost session; the per-invitee scope only separates the ring signals.
        for p in peers:
            B.T.put(B.SERVICE, "call", f"{B.T.session_scope(sid)}:{p}",
                    {"from": self.me_id, "to": p, "state": "ringing",
                     "name": self.me_name, "sid": sid, "participants": participants,
                     "group": len(participants) > 2},
                    dbhost=self.dbhost)
        self.seen_calls[sid] = "ringing"
        self.tab.set("Call")
        self.render()

    def _end_embedded(self):
        """Stop the embedded-call render loop, let reader threads exit, drop FIFOs."""
        self.call_embedded = False
        self.call_frames = {}                 # readers check membership and exit
        for f in self._call_fifos:
            try:
                os.remove(f)
            except OSError:
                pass
        self._call_fifos = []
        self.peer_lbl = None; self.self_lbl = None
        self.metrics_lbl = None
        self._peer_img = None; self._self_img = None

    def hang_up(self, peer):
        for p in self.calls.pop(peer, []):
            _close_call_entry(p)
        self._end_embedded()
        sid = self.sessions.pop(peer, None)
        if sid:
            B.T.put(B.SERVICE, "call", B.T.session_scope(sid),
                    {"from": self.me_id, "to": peer, "state": "ended"}, dbhost=self.dbhost)
            self.seen_calls[sid] = "ended"
        if self.chat_sid == sid:
            self.chat_sid = LOBBY_SID
        if self.active_peer == peer:
            self.active_peer = None
        self.render()

    def _spawn_media(self, peer, sid):
        """Place a two-way A/V call. Default path is the canonical SotF media stack
        (adaptive, drop-don't-stall, bearer-tuple backpressure). The legacy blocking
        engine remains as an explicit fallback (cfg['transport'] == 'engine', or the
        stack not importable on this node)."""
        if peer in self.calls:
            return
        use_sotf = _SOTF_CALLS and self.cfg.get("transport", "sotf") != "engine"
        if use_sotf:
            try:
                return self._spawn_media_sotf(peer, sid)
            except Exception as e:
                _plog(f"[SOTF] call setup failed, falling back to engine: {e!r}")
                self.q.put(("error", f"SotF A/V failed, using engine: {e}"))
                # fall through to the engine path
        return self._spawn_media_engine(peer, sid)

    def _spawn_media_engine(self, peer, sid):
        """LEGACY fallback: open camera HERE only on accept. Two-way: publish me, view
        self, view peer. Linux renders both feeds INSIDE the tiles (viewer --save FIFO ->
        ffmpeg -> PPM); Windows / any embed failure falls back to --display windows.
        Retained for nodes without the SotF stack; not the default call path."""
        if peer in self.calls:
            return
        self.stop_preview()                       # free the camera for the call engine
        avhost = self.host.split(":")[0]
        eng = B.ENGINE
        my_stream = f"{sid}:{self.me_id}"
        peer_stream = f"{sid}:{peer}"
        sender = [sys.executable, eng, "--connect", f"{avhost}:{B.AV_PORT}",
                  "--role", "sender", "--stream", my_stream, "--source", "live"]
        if self.fps:     sender += ["--fps", str(self.fps)]
        if self.bitrate: sender += ["--bitrate-kbps", str(self.bitrate)]
        if self.cam:     sender += ["--camera", self.cam]
        if self.mic:     sender += ["--mic", self.mic]
        try:
            _plog(f"[ORPHANS] live ffmpeg/ffplay before call = {_media_proc_count()}")
            _plog("SPAWN: " + " ".join(str(c) for c in sender))
            sp = subprocess.Popen(sender, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, bufsize=0,
                                  start_new_session=(os.name != "nt"))
            threading.Thread(target=self._meter_drain, args=(sp.stdout, "sender"),
                             daemon=True).start()
            procs = [sp]
        except Exception as e:
            self.q.put(("error", f"media: {e}")); return

        self.call_frames = {"self": None, "peer": None}
        self._call_fifos = []
        self.metrics = {"sent": 0, "recv": 0, "disp_peer": 0, "disp_self": 0,
                        "rx": 0, "vdrop": 0, "viewers": 0, "wire": 0,
                        "converged": 0, "full": 0, "diff": 0, "repeat": 0}
        embedded = bool(shutil.which("ffmpeg"))     # method chosen per-OS in _start_embedded_view
        _plog(f"call spawn: embedded_capable={embedded} "
              f"mkfifo={hasattr(os, 'mkfifo')} ffmpeg={bool(shutil.which('ffmpeg'))} "
              f"streams self={my_stream} peer={peer_stream}")
        if embedded:
            try:
                self._start_embedded_view("self", my_stream, avhost, eng, procs)
                self._start_embedded_view("peer", peer_stream, avhost, eng, procs)
                self.call_embedded = True
            except Exception as e:
                _plog(f"EMBED FAILED -> falling back to windows: {e!r}")
                for p in procs[1:]:
                    try: _kill(p)
                    except Exception: pass
                procs = procs[:1]
                for f in self._call_fifos:
                    try: os.remove(f)
                    except OSError: pass
                self._call_fifos = []
                embedded = False
                self.q.put(("error", f"embed failed, using windows: {e}"))
        if not embedded:
            self.call_embedded = False
            sp = [sys.executable, eng, "--connect", f"{avhost}:{B.AV_PORT}",
                  "--role", "viewer", "--stream", my_stream, "--display"]
            pv = [sys.executable, eng, "--connect", f"{avhost}:{B.AV_PORT}",
                  "--role", "viewer", "--stream", peer_stream, "--display"]
            if self.fps:
                sp += ["--fps", str(self.fps)]; pv += ["--fps", str(self.fps)]
            procs += [_spawn_logged(sp), _spawn_logged(pv)]

        self.calls[peer] = procs
        self.sessions[peer] = sid
        self.active_peer = peer
        self.chat_sid = sid
        _plog(f"call mode = {'EMBEDDED (in-app tiles)' if self.call_embedded else 'WINDOWS (ffplay)'}")
        self._status(f"in call . {self._name_of(peer)} . "
                     + ("video in-app" if self.call_embedded else "video in windows"), PAD)
        if self.call_embedded:
            self.after(150, self._tick_call)

    # -- SotF call path: capture -> CallSender (bearer-adaptive) -> mediahost;
    #    mediahost -> CallReceiver -> VP8->PPM -> the SAME call_frames tiles. ------
    def _media_control(self, sid, role="producer"):
        """A media_stream.TupleControl bound to this call's session, backed by the
        app's tuple space (B.T). B.T's surface differs from the TupleControl backend
        contract (no get_one; put/get take dbhost= not addr=), so adapt it here."""
        app_T = B.T
        svc = "mediastream"          # media_stream.SERVICE namespace (NOT B.SERVICE)
        dbhost = self.dbhost

        class _Backend:
            def put(self, service, var, scope, value, addr=None):
                return app_T.put(service, var, scope, value, dbhost=dbhost)
            def get(self, service, var, fresh_s=0):
                return app_T.get(service, var, dbhost=dbhost, fresh_s=fresh_s)
            def get_one(self, service, var, scope, fresh_s=0):
                rows = app_T.get(service, var, dbhost=dbhost, fresh_s=fresh_s)
                for r in rows:
                    if r.get("scope") == scope:
                        return r.get("value")
                return None

        from media_stream import TupleControl
        ctrl = TupleControl(_Backend(), sid, addr=self.host.split(":")[0])
        return ctrl

    def _play_audio(self, pcm):
        """Peer-leg audio out. Lazily opens an ffplay PCM sink and writes to it.
        Gated: only the peer's audio is played (self is muted)."""
        if not pcm:
            return
        sink = getattr(self, "_audio_sink", None)
        if sink is None:
            ff = shutil.which("ffplay")
            if not ff:
                return
            cmd = [ff, "-hide_banner", "-loglevel", "quiet", "-nodisp", "-autoexit",
                   "-f", "s16le", "-ar", "48000", "-ch_layout", "mono", "-i", "pipe:0"]
            try:
                sink = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
                self._audio_sink = sink
            except Exception:
                self._audio_sink = None
                return
        try:
            sink.stdin.write(pcm); sink.stdin.flush()
        except Exception:
            pass

    @staticmethod
    def _ivf_header(w=320, h=240, fps=12):
        return (b"DKIF" + struct.pack("<HH", 0, 32) + b"VP80"
                + struct.pack("<HHIIII", w, h, fps, 1, 0, 0))

    def _feed_webm_downlink(self, chunk: bytes):
        """Feed raw muxed-webm downlink bytes to TWO consumers of the same stream: an
        ffmpeg that decodes video -> PPM -> the 'peer' tile, and an ffplay that plays the
        audio. The host emits ONE webm program (VP8+Opus); teeing keeps each consumer a
        clean single-purpose process (robust vs a single multi-output ffmpeg w/ fd plumbing)."""
        v = getattr(self, "_dl_decoder", None)
        if v is None:
            ff = shutil.which("ffmpeg")
            fp = shutil.which("ffplay")
            # video decoder: webm in -> scaled PPM out
            if ff:
                vcmd = [ff, "-hide_banner", "-loglevel", "quiet", "-f", "webm", "-i", "pipe:0",
                        "-an", "-vf", "scale=360:-2", "-r", "12",
                        "-f", "image2pipe", "-vcodec", "ppm", "pipe:1"]
                try:
                    v = subprocess.Popen(vcmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, bufsize=0)
                    def _vr():
                        for ppm in _read_ppm_stream(v.stdout):
                            if "peer" in self.call_frames:
                                self.call_frames["peer"] = ppm
                                self.metrics["disp_peer"] = self.metrics.get("disp_peer", 0) + 1
                    threading.Thread(target=_vr, daemon=True).start()
                except Exception:
                    v = False
            else:
                v = False
            self._dl_decoder = v
            # audio player: webm in -> speakers (ffplay demuxes Opus itself)
            a = None
            if fp:
                acmd = [fp, "-hide_banner", "-loglevel", "quiet", "-nodisp", "-autoexit",
                        "-f", "webm", "-i", "pipe:0"]
                try:
                    a = subprocess.Popen(acmd, stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         bufsize=0)
                except Exception:
                    a = None
            self._dl_audio = a
        # write the SAME chunk to both consumers
        for proc in (self._dl_decoder, getattr(self, "_dl_audio", None)):
            if proc and proc is not False:
                try:
                    proc.stdin.write(chunk); proc.stdin.flush()
                except Exception:
                    pass

    def _av_decoder(self, which, on_ppm):
        """ffmpeg: IVF(VP8) on stdin -> scaled PPM frames; a reader calls on_ppm per
        frame. Same scale/rate as the legacy embedded view (self=152px, peer=360px,
        12fps) so the tile bytes are render-identical."""
        ff = shutil.which("ffmpeg")
        sw = 152 if which == "self" else 360
        dec = [ff, "-hide_banner", "-loglevel", "warning", "-f", "ivf", "-i", "pipe:0",
               "-vf", f"scale={sw}:-2", "-r", "12", "-f", "image2pipe",
               "-vcodec", "ppm", "-"]
        proc = subprocess.Popen(dec, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, bufsize=10 ** 7,
                                start_new_session=(os.name != "nt"))

        def reader():
            n = 0
            for ppm in _read_ppm_stream(proc.stdout):
                on_ppm(ppm); n += 1
                if n == 1:
                    _plog(f"[{which}] FIRST FRAME ok ({len(ppm)} bytes)")
            _plog(f"[{which}] decode stream ended after {n} frames")

        threading.Thread(target=reader, daemon=True).start()
        return proc

    def _feed_decode(self, which, key, vp8):
        """Feed one VP8 frame into the per-tile decoder (creating it + writing the IVF
        header on first frame). Fills call_frames[which] with PPM via the reader."""
        decoders = getattr(self, "_call_decoders", None)
        if decoders is None:
            decoders = self._call_decoders = {}
        dec = decoders.get(which)
        if dec is None:
            def on_ppm(ppm, w=which):
                if w in self.call_frames:
                    self.call_frames[w] = ppm
                    self.metrics[f"disp_{w}"] = self.metrics.get(f"disp_{w}", 0) + 1
            dec = self._av_decoder(which, on_ppm)
            try:
                dec.stdin.write(self._ivf_header()); dec.stdin.flush()
            except Exception:
                pass
            decoders[which] = dec
        try:
            rec = struct.pack("<IQ", len(vp8), 0) + vp8     # IVF frame: size + ts + payload
            dec.stdin.write(rec); dec.stdin.flush()
        except Exception:
            pass

    def _spawn_media_sotf(self, peer, sid):
        """Two-way call on the canonical SotF media stack. ONE stream UP (CallSender,
        rung-capped by the server's bearer, shed-don't-stall) + ONE stream DOWN
        (CallReceiver: the host's pre-mixed all-feeds canvas). Cost is constant per
        client regardless of party count - the host bears the N-source mixing. The SELF
        tile renders from LOCAL capture (we already have our own frames pre-encode), not
        from the downlink. No engine subprocess, no blocking socket."""
        if peer in self.calls:
            return
        self.stop_preview()                       # free the camera for the call capture
        cap = _cap_probe()
        control = self._media_control(sid, role="producer")

        self.call_frames = {"self": None, "peer": None}
        self._call_decoders = {}
        self.metrics = {"sent": 0, "recv": 0, "disp_peer": 0, "disp_self": 0,
                        "rx": 0, "vdrop": 0, "viewers": 0, "wire": 0,
                        "rung": 7, "tx_drop": 0, "depth": 0,
                        "converged": 0, "full": 0, "diff": 0, "repeat": 0}

        # OUT - ONE uplink: publish my camera+mic; rung capped by the server's bearer.
        cs = CMED.CallSender(control, node=self.me_id,
                             have_camera=True, have_mic=bool(self.mic),
                             on_drop=lambda: self.metrics.__setitem__(
                                 "tx_drop", self.metrics["tx_drop"] + 1))
        cs.connect(codec="vp8")

        # IN - ONE downlink: the host's pre-mixed all-feeds program is a MUXED WEBM
        # stream (VP8 video + Opus audio in one container). Feed the RAW webm bytes into a
        # single ffmpeg that demuxes BOTH tracks: video -> PPM -> the tile, audio -> PCM ->
        # the audio sink. (This is the webm-aware downlink: the host is a mixer now, so the
        # client consumes one container stream, not per-frame _pack_raw.)
        self._dl_decoder = None
        self._dl_audio = None

        def _on_webm_bytes(chunk: bytes):
            self._feed_webm_downlink(chunk)
            self.metrics["recv"] = self.metrics.get("recv", 0) + len(chunk)

        rx_in = CMED.CallReceiver(lambda *a: None)
        rx_in._on_frame_bytes = _on_webm_bytes      # raw stream bytes, not _pack_raw
        ci = cs.producer.conn_info or {}
        rx_addr, rx_port = ci.get("addr"), ci.get("rx")
        if rx_addr and rx_port:
            rx_in.open(rx_addr, rx_port)

        stop = threading.Event()
        self.sessions[peer] = sid
        self.active_peer = peer
        self.chat_sid = sid
        self.call_embedded = True

        # capture: reuse the UNPINNED-framerate live capture; pump to CallSender (up) AND
        # render the SELF tile locally from the SAME frames (no self downlink).
        fps = self.fps or 10
        cmd = _cap_live_cmd(cap, fps, self.bitrate or 120, self.cam or "")
        capproc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        micbuf = _CapMicBuffer(cap, self.mic) if self.mic else None
        aud_per_frame = int(_CAP_AUDIO_BPS / max(fps, 1))
        aud_per_frame -= aud_per_frame % 2          # 16-bit sample alignment
        self.calls[peer] = [("sotf", cs, rx_in, stop, capproc, micbuf)]

        def _capture_pump():
            ts0 = time.time()
            self.metrics["t0"] = ts0
            try:
                for seq, is_key, vp8 in _cap_iter_ivf(capproc.stdout):
                    if stop.is_set():
                        break
                    ts = int((time.time() - ts0) * 1000)
                    if micbuf:
                        a = micbuf.take(aud_per_frame)
                        if a:
                            cs.feed_audio(ts, a)
                            self.metrics["did_bytes"] = self.metrics.get("did_bytes", 0) + len(a)
                    cs.feed_video(ts, is_key, vp8)
                    self._feed_decode("self", is_key, vp8)   # self tile = local capture
                    self.metrics["sent"] = self.metrics.get("sent", 0) + 1
                    # Did = real bytes we put on the wire (rung-shed, adaptive, compressed).
                    self.metrics["did_bytes"] = self.metrics.get("did_bytes", 0) + len(vp8)
                    # Would = a conventional stream: full frame EVERY tick at the ceiling
                    # rung, no shed/no convergence. Approximate as the ceiling video budget
                    # per frame (bitrate/fps) - what a fixed-rate encoder would emit.
                    would_per_frame = int((self.bitrate or 120) * 1000 / 8 / max(fps, 1))
                    self.metrics["would_bytes"] = self.metrics.get("would_bytes", 0) + would_per_frame
                    self.metrics["rung"] = cs.send_level()
                    self.metrics["depth"] = cs.depth()
            finally:
                try: capproc.terminate()
                except Exception: pass
                if micbuf:
                    try: micbuf.close()
                    except Exception: pass

        threading.Thread(target=_capture_pump, daemon=True).start()

        _plog(f"call mode = SOTF (adaptive A/V) session={sid} peer={peer}")
        self._status(f"in call . {self._name_of(peer)} . SotF adaptive A/V", PAD)
        self.after(150, self._tick_call)

    def _meter_drain(self, pipe, kind):
        """Read an engine process's output line by line. 'sender' -> frames sent;
        'viewer' -> the server's K_STAT telemetry JSON ('STAT {...}'). Audio/mic/error
        lines from either are mirrored into the log so failures are visible."""
        KEYW = ("MIC", "AUDIO", "error", "Error", "cannot", "Cannot",
                "Failed", "could not", "Invalid", "Could not")
        buf = b""
        try:
            while True:
                chunk = pipe.read(256)
                if not chunk:
                    break
                buf += chunk
                s = buf.decode("utf-8", "replace")
                parts = re.split(r"[\r\n]", s)
                buf = parts.pop().encode("utf-8", "replace")    # keep trailing partial
                for line in parts:
                    line = line.strip()
                    if not line:
                        continue
                    if kind == "sender":
                        m = re.search(r"sent (\d+)", line)
                        if m:
                            self.metrics["sent"] = int(m.group(1))
                    elif line.startswith("STAT "):
                        try:
                            d = json.loads(line[5:])
                            self.metrics["recv"] = int(d.get("video", self.metrics["recv"]))
                            self.metrics["rx"] = int(d.get("rx", 0))
                            self.metrics["vdrop"] = int(d.get("vdrop", 0))
                            self.metrics["viewers"] = int(d.get("viewers", 0))
                            self.metrics["wire"] = int(d.get("wire_bytes", 0))
                            self.metrics["converged"] = int(d.get("converged", 0))
                            self.metrics["full"] = int(d.get("full", 0))
                            self.metrics["diff"] = int(d.get("diff", 0))
                            self.metrics["repeat"] = int(d.get("repeat", 0))
                        except Exception:
                            pass
                    if any(k in line for k in KEYW):
                        _plog(f"{kind}: {line}")
        except Exception:
            pass

    def _start_embedded_view(self, which, stream, avhost, eng, procs):
        """Get reconstructed IVF from an engine viewer into the app, decode to PPM, and
        render in the tile. Linux uses a FIFO (proven). Windows (no os.mkfifo) pipes the
        engine's stdout straight into the decoder: `engine --save - | ffmpeg -f ivf -i -`."""
        ff = shutil.which("ffmpeg")
        sw = 152 if which == "self" else 360       # self fits the inset; peer fills the tile
        dec = [ff, "-hide_banner", "-loglevel", "warning", "-f", "ivf", "-i", "pipe:0",
               "-vf", f"scale={sw}:-2", "-r", "12", "-f", "image2pipe", "-vcodec", "ppm", "-"]

        if hasattr(os, "mkfifo"):
            fifo = os.path.join(tempfile.gettempdir(), f"frognet_{which}_{os.getpid()}.ivf")
            try:
                os.remove(fifo)
            except OSError:
                pass
            os.mkfifo(fifo)
            self._call_fifos.append(fifo)
            dec[dec.index("pipe:0")] = fifo          # decoder reads the FIFO
            _plog(f"[{which}] decode: {' '.join(dec)}")
            decp = subprocess.Popen(dec, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, bufsize=10 ** 7,
                                   start_new_session=(os.name != "nt"))
            viewer = [sys.executable, eng, "--connect", f"{avhost}:{B.AV_PORT}",
                      "--role", "viewer", "--stream", stream, "--save", fifo]
            if which == "peer":
                viewer += ["--audio"]            # hear the peer; self stays muted (no echo)
            if self.fps:
                viewer += ["--fps", str(self.fps)]
            _plog(f"[{which}] viewer(fifo): {' '.join(viewer)}")
            if which == "peer":
                vproc = subprocess.Popen(viewer, stdout=open(_PLOG, "a"),
                                        stderr=subprocess.PIPE, bufsize=0,
                                        start_new_session=(os.name != "nt"))
                threading.Thread(target=self._meter_drain, args=(vproc.stderr, "viewer"),
                                 daemon=True).start()
            else:
                vproc = _spawn_logged(viewer)
        else:
            # Windows: engine writes IVF to its stdout; pipe it into the decoder's stdin.
            viewer = [sys.executable, eng, "--connect", f"{avhost}:{B.AV_PORT}",
                      "--role", "viewer", "--stream", stream, "--save", "-"]
            if which == "peer":
                viewer += ["--audio"]            # hear the peer; self stays muted (no echo)
            if self.fps:
                viewer += ["--fps", str(self.fps)]
            _plog(f"[{which}] viewer(stdout): {' '.join(viewer)}")
            if which == "peer":
                vproc = subprocess.Popen(viewer, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, bufsize=0,
                                        start_new_session=(os.name != "nt"))
                threading.Thread(target=self._meter_drain, args=(vproc.stderr, "viewer"),
                                 daemon=True).start()
            else:
                vproc = subprocess.Popen(viewer, stdout=subprocess.PIPE, stderr=open(_PLOG, "a"),
                                     start_new_session=(os.name != "nt"))
            _plog(f"[{which}] decode: {' '.join(dec)}")
            decp = subprocess.Popen(dec, stdin=vproc.stdout, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, bufsize=10 ** 7,
                                   start_new_session=(os.name != "nt"))
            vproc.stdout.close()        # decoder owns the read end; engine sees EPIPE on exit

        procs.append(vproc); procs.append(decp)

        def drain_err():
            try:
                for line in iter(decp.stderr.readline, b""):
                    s = line.decode("utf-8", "replace").rstrip()
                    if s:
                        _plog(f"[{which}] decode stderr: {s}")
            except Exception:
                pass

        def reader():
            n = 0
            for ppm in _read_ppm_stream(decp.stdout):
                if which not in self.call_frames:
                    break
                self.call_frames[which] = ppm
                n += 1
                self.metrics[f"disp_{which}"] = n
                if n == 1:
                    _plog(f"[{which}] FIRST FRAME ok ({len(ppm)} bytes)")
            _plog(f"[{which}] decode stream ended after {n} frames; rc={decp.poll()}")

        threading.Thread(target=drain_err, daemon=True).start()
        threading.Thread(target=reader, daemon=True).start()

    def _tick_call(self):
        if not self.call_embedded or self.active_peer is None:
            return
        # On other tabs the call tiles don't exist; keep looping cheaply until we're
        # back on Call (the labels get rebuilt by render()).
        if self.tab.get() != "Call":
            self.after(120, self._tick_call)
            return
        for which, lbl_attr, img_attr in (("peer", "peer_lbl", "_peer_img"),
                                          ("self", "self_lbl", "_self_img")):
            lbl = getattr(self, lbl_attr, None)
            frame = self.call_frames.get(which)
            if lbl is None or frame is None:
                continue
            try:
                if not lbl.winfo_exists():        # destroyed mid-render - skip this tick
                    continue
                tmp = os.path.join(tempfile.gettempdir(),
                                   f"frognet_call_{which}_{os.getpid()}.ppm")
                with open(tmp, "wb") as f:
                    f.write(frame)
                photo = tk.PhotoImage(file=tmp)
                lbl.config(image=photo, text="")
                setattr(self, img_attr, photo)
            except Exception:
                pass                               # do NOT re-config a possibly-dead widget
        s = getattr(self, "stats_lbl", None)
        try:
            if s is not None and self.stats_on and s.winfo_exists():
                s.config(text=self._stats_text())
        except Exception:
            pass
        self.after(70, self._tick_call)

    def accept_call(self, sid, frm, participants=None):
        # accept on a per-acceptor scope so the originator sees each accept distinctly.
        B.T.put(B.SERVICE, "call", f"{B.T.session_scope(sid)}:{self.me_id}",
                {"from": frm, "to": self.me_id, "state": "accepted",
                 "name": self.me_name, "sid": sid,
                 "participants": participants or [frm, self.me_id]},
                dbhost=self.dbhost)
        self.seen_calls[f"{sid}:{self.me_id}:accepted"] = "accepted"
        # join the shared mediahost session. For a group the active_peer is the originator;
        # the host mixes all feeds so this client still does one-up/one-down.
        if self.seen_calls.get(f"spawned:{sid}") != "yes":
            self.seen_calls[f"spawned:{sid}"] = "yes"
            self.active_peer = frm
            self.sessions[frm] = sid
            self._spawn_media(frm, sid)
        self.tab.set("Call")
        self.render()

    def decline_call(self, sid, frm):
        B.T.put(B.SERVICE, "call", f"{B.T.session_scope(sid)}:{self.me_id}",
                {"from": frm, "to": self.me_id, "state": "declined", "sid": sid},
                dbhost=self.dbhost)
        self.seen_calls[f"{sid}:{self.me_id}:declined"] = "declined"

    def _handle_calls(self, rows):
        for r in rows or []:
            v = r.get("value", {})
            # group ring scopes are session:<sid>:<to>; sid is carried in the value too.
            sid = v.get("sid") or r.get("scope", "").replace("session:", "")
            to = v.get("to"); frm = v.get("from"); state = v.get("state")
            seen_key = f"{sid}:{to}:{state}"
            if not sid or not state or self.seen_calls.get(seen_key) == state:
                continue
            if to == self.me_id and state == "ringing" and frm != self.me_id:
                self.seen_calls[seen_key] = "ringing"
                parts = v.get("participants") or [frm, self.me_id]
                self._ring_dialog(sid, frm, v.get("name"), participants=parts)
            elif frm == self.me_id and state == "accepted":
                # an invitee accepted; join the SHARED session once (idempotent on sid).
                if self.seen_calls.get(f"spawned:{sid}") != "yes":
                    self.seen_calls[f"spawned:{sid}"] = "yes"
                    self._spawn_media(self.active_peer or to, sid)
                self.seen_calls[seen_key] = "accepted"
                self.render()
            elif state in ("declined", "ended"):
                self.seen_calls[seen_key] = state
                peer = to if frm == self.me_id else frm
                for p in self.calls.pop(peer, []):
                    _close_call_entry(p)
                self._end_embedded()
                self.sessions.pop(peer, None)
                if self.active_peer == peer:
                    self.active_peer = None
                if self.chat_sid == sid:
                    self.chat_sid = LOBBY_SID
                self.render()

    def _ring_dialog(self, sid, frm, frm_name, participants=None):
        win = tk.Toplevel(self); win.title("Incoming call"); win.configure(bg=BG)
        win.geometry("300x150")
        tk.Label(win, text=(frm_name or frm), fg=INK, bg=BG, font=self.f_big).pack(pady=(22, 2))
        n = len(participants or [])
        sub = "is calling..." if n <= 2 else f"group call . {n} people..."
        tk.Label(win, text=sub, fg=INKDIM, bg=BG, font=self.f_mono_sm).pack()
        row = tk.Frame(win, bg=BG); row.pack(pady=18)
        tk.Button(row, text="Decline", command=lambda: (win.destroy(), self.decline_call(sid, frm)),
                  fg="#fff", bg=BAD, bd=0, padx=14, pady=6, cursor="hand2").pack(side="left", padx=6)
        tk.Button(row, text="Accept",
                  command=lambda: (win.destroy(), self.accept_call(sid, frm, participants)),
                  fg="#06140c", bg=PAD, bd=0, padx=14, pady=6, cursor="hand2").pack(side="left", padx=6)
        win.protocol("WM_DELETE_WINDOW", lambda: (win.destroy(), self.decline_call(sid, frm)))

    def _on_close(self):
        self.stop.set()
        try:
            B.roster_deregister(self.host, self.me_id)
        except Exception:
            pass
        for plist in self.calls.values():
            for p in plist:
                _close_call_entry(p)
        self.destroy()

    # ----------------------------------------------------------------- header
    def _build_header(self):
        h = tk.Frame(self.body, bg=BG, height=44)
        h.pack(fill="x"); h.pack_propagate(False)
        tk.Label(h, text="*", fg=PAD, bg=BG, font=self.f_mono).pack(side="left", padx=(14, 4))
        tk.Label(h, text="FrogNet", fg=INK, bg=BG, font=self.f_mono).pack(side="left")
        gear = tk.Label(h, text="Settings", fg="#06140c", bg=PAD,
                        font=self.f_mono_sm, cursor="hand2", padx=12, pady=4)
        gear.pack(side="right", padx=(0, 12), pady=8)
        gear.bind("<Button-1>", lambda e: self.open_settings())
        tk.Label(h, text=f"{self.me_name} . {self.host}", fg=INKDIM, bg=BG,
                 font=self.f_mono_sm).pack(side="right", padx=12)
        tk.Frame(self.body, bg=LINE, height=1).pack(fill="x")
        self.status = tk.Label(self.body, text="", fg=INKDIM, bg=BG,
                               font=self.f_mono_sm, anchor="w")
        self.status.pack(fill="x", padx=14, pady=(3, 0))

    def _status(self, text, color=INKDIM):
        try:
            self.status.config(text=text, fg=color)
        except Exception:
            pass

    # ---------------------------------------------------------------- tab bar
    def _build_tabbar(self):
        bar = tk.Frame(self.body, bg=PANEL, height=46)
        bar.pack(fill="x", side="bottom"); bar.pack_propagate(False)
        tk.Frame(self.body, bg=LINE, height=1).pack(fill="x", side="bottom")
        self._tabbtns = {}
        for t in TABS:
            b = tk.Label(bar, text=t, fg=INKDIM, bg=PANEL, font=self.f_mono_sm, cursor="hand2")
            b.pack(side="left", expand=True, fill="both")
            b.bind("<Button-1>", lambda e, tt=t: self._switch(tt))
            self._tabbtns[t] = b

    def _switch(self, t):
        if self.preview_active and t != "Call":
            self.stop_preview()
        self.tab.set(t)
        self.chat_sid = self.sessions.get(self.active_peer, LOBBY_SID) if t == "Text" and self.active_peer else (
            self.chat_sid if t == "Text" else self.chat_sid)
        self.render()

    # ------------------------------------------------------------- rendering
    def render(self):
        for w in self.content.winfo_children():
            w.destroy()
        for t, b in self._tabbtns.items():
            b.configure(fg=(PAD if t == self.tab.get() else INKDIM))
        {"Home": self._home, "Call": self._call, "Text": self._text,
         "Games": self._games,
         "Calendar": lambda: self._bundle("Calendar", "Shared calendar bundle.")}[self.tab.get()]()

    # ------------------------------------------------------------------- Home
    def _home(self):
        tk.Label(self.content, text="SERVICES AVAILABLE", fg=INKDIM, bg=BG,
                 font=self.f_mono_sm).pack(anchor="w", padx=16, pady=(14, 8))
        for name, to, live in SERVICES:
            card = tk.Frame(self.content, bg=PANEL, cursor="hand2",
                            highlightbackground=LINE, highlightthickness=1)
            card.pack(fill="x", padx=14, pady=5)
            card.bind("<Button-1>", lambda e, d=to: self._switch(d))
            icon = tk.Label(card, text=name[0], fg=(PAD if live else INKDIM), bg=PANELHI,
                            font=self.f_big, width=3, height=1)
            icon.pack(side="left", padx=10, pady=10)
            txt = tk.Frame(card, bg=PANEL); txt.pack(side="left", anchor="w", pady=10)
            tk.Label(txt, text=name, fg=INK, bg=PANEL, font=self.f_big).pack(anchor="w")
            tk.Label(txt, text=("ready" if live else "bundle"), fg=INKDIM, bg=PANEL,
                     font=self.f_mono_sm).pack(anchor="w")
            for w in (card, icon, txt):
                w.bind("<Button-1>", lambda e, d=to: self._switch(d))
            if live:
                tk.Label(card, text="*", fg=PAD, bg=PANEL, font=self.f_mono).pack(side="right", padx=12)
        n = len(self._others())
        tk.Label(self.content, text=f"{n} on the pond" if n else "no one else here yet",
                 fg=INKDIM, bg=BG, font=self.f_mono_sm).pack(anchor="w", padx=16, pady=(12, 0))

    # ------------------------------------------------------------------- Call
    def _video_state(self):
        if not self.display_on: return "black"
        if not self.video_possible and not self.audio_possible: return "textonly"
        if not self.video_enabled: return "videooff"
        if not self.video_possible: return "novideo"
        return "grid"

    def _audio_label(self):
        if not self.audio_enabled: return ("Off", INKDIM)
        if not self.audio_possible: return ("None", BAD)
        return ("Stereo", PAD) if self.audio_rung == 1 else ("Mono", WARN)

    def _call(self):
        others = self._others()
        vh = {"Full": 300, "Half": 360, "Thumbnail": 420, "Off": 470}[self.self_view]
        vid = tk.Frame(self.content, bg=VIDBG, height=vh)
        vid.pack(fill="both", expand=True); vid.pack_propagate(False)
        st = self._video_state()
        alabel, acolor = self._audio_label()

        if self.preview_active:
            self.preview_lbl = tk.Label(vid, bg=VIDBG, fg=INKDIM, font=self.f_mono_sm,
                                        text="starting camera...", cursor="hand2")
            self.preview_lbl.pack(fill="both", expand=True)
            self.preview_lbl.bind("<Button-1>", lambda e: self.stop_preview())
            # mic level bar across the bottom of the tile
            track = tk.Frame(vid, bg=PANEL, height=8)
            track.place(relx=0, rely=1.0, y=-8, relwidth=1.0, height=8)
            self.mic_bar = tk.Frame(track, bg=PAD)
            self.mic_bar.place(x=0, y=0, relheight=1.0, relwidth=0.0)
            self.mic_lbl = tk.Label(vid, text=("mic ..." if self.mic else "no mic found"),
                                    fg=INKDIM, bg=BADGE, font=self.f_mono_sm)
            self.mic_lbl.place(x=10, rely=1.0, y=-30)
            tk.Label(vid, text="LIVE CHECK . click to stop", fg=PAD, bg=BADGE,
                     font=self.f_mono_sm).place(relx=1.0, x=-150, y=10)
            self._call_controls()
            return
        else:
            self.preview_lbl = None; self.mic_bar = None; self.mic_lbl = None

        if self.active_peer and self.call_embedded:
            self.peer_lbl = tk.Label(vid, bg=VIDBG, fg=INKDIM, font=self.f_mono_sm,
                                     text="waiting for peer video...")
            self.peer_lbl.pack(fill="both", expand=True)
            self.self_lbl = tk.Label(vid, bg=CELL, fg=INKDIM, font=self.f_mono_sm, text="you")
            self.self_lbl.place(relx=1.0, rely=1.0, x=-162, y=-124, width=152, height=114)
            tk.Label(vid, text=f"in call . {self._name_of(self.active_peer)}", fg=PAD,
                     bg=BADGE, font=self.f_mono_sm).place(x=10, y=10)
            # --- stats overlay: translucent, toggled by the 'o' key or the (i) button,
            #     LARGE readable text, per-sender. Reads the live self.metrics dict. ---
            self.stats_lbl = tk.Label(
                vid, text="", fg=INK, bg=BADGE, font=self.f_mono, justify="left",
                anchor="nw", padx=12, pady=10, bd=0)
            if self.stats_on:
                self.stats_lbl.place(x=10, y=44)
            self.stats_btn = tk.Label(vid, text="(i) stats", fg=(PAD if self.stats_on else INKDIM),
                                      bg=BADGE, font=self.f_mono_sm, cursor="hand2",
                                      padx=8, pady=4)
            self.stats_btn.place(relx=1.0, x=-86, y=10)
            self.stats_btn.bind("<Button-1>", lambda e: self.toggle_stats())
            self._call_controls()
            return

        if (self.active_peer and self.active_peer in self.sessions
                and self.active_peer not in self.calls):
            box = tk.Frame(vid, bg=VIDBG); box.pack(expand=True)
            tk.Label(box, text="Calling...", fg=WARN, bg=VIDBG, font=self.f_huge).pack(pady=(0, 6))
            tk.Label(box, text=self._name_of(self.active_peer), fg=INK, bg=VIDBG,
                     font=self.f_mono).pack()
            tk.Label(box, text="waiting for them to answer", fg=INKDIM, bg=VIDBG,
                     font=self.f_mono_sm).pack(pady=(4, 0))
            self._call_controls()
            return

        if st == "grid":
            grid = tk.Frame(vid, bg=VIDBG); grid.pack(expand=True, fill="both", padx=8, pady=8)
            if not others:
                tk.Label(grid, text="no one else on the pond", fg=INKDIM, bg=VIDBG,
                         font=self.f_mono_sm).pack(expand=True)
            for i, u in enumerate(others):
                pid = u.get("id", u.get("name", "")).strip().lower()
                in_call = pid in self.calls
                calling = (pid in self.sessions) and not in_call
                r, c = divmod(i, 2)
                edge = PAD if in_call else WARN if calling else LINE
                cell = tk.Frame(grid, bg=CELL, highlightbackground=edge,
                                highlightthickness=(2 if (in_call or calling) else 1))
                cell.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
                tk.Label(cell, text=(u.get("name", "?")[:1]).upper(), fg=PAD, bg=PANELHI,
                         font=self.f_huge, width=2).pack(expand=True)
                tag = "  . in call" if in_call else "  . calling..." if calling else ""
                tk.Label(cell, text=u.get("name", pid) + tag,
                         fg=INKDIM, bg=CELL, font=self.f_mono_sm).pack(side="bottom",
                                                                            anchor="w", padx=4)
                if in_call:
                    act, bbg, bfg, fn = "Hang Up", BAD, "#fff", (lambda p=pid: self.hang_up(p))
                elif calling:
                    act, bbg, bfg, fn = "Calling... (cancel)", WARN, "#06140c", (lambda p=pid: self.hang_up(p))
                else:
                    act, bbg, bfg, fn = "Call", PAD, "#06140c", (lambda p=pid: self.start_call(p))
                tk.Button(cell, text=act, command=fn, fg=bfg, bg=bbg, bd=0, cursor="hand2",
                          activebackground=bbg, font=self.f_mono, padx=12, pady=7).pack(
                          side="bottom", fill="x", padx=6, pady=(4, 8))
                # group-call multi-select: a checkbox to add this person to the next group
                # call. Only offered when not already in/placing a call.
                if not in_call and not calling:
                    selected = pid in self._group_sel
                    tk.Button(cell, text=("[x] in group" if selected else "[ ] add to group"),
                              command=(lambda p=pid: self._toggle_group(p)),
                              fg=(PAD if selected else INKDIM), bg=CELL, bd=0, cursor="hand2",
                              activebackground=CELL, font=self.f_mono_sm).pack(
                              side="bottom", fill="x", padx=6)
            for c in range(2): grid.columnconfigure(c, weight=1)
            for r in range((max(len(others), 1) + 1) // 2): grid.rowconfigure(r, weight=1)
            # originator-only Group Call action when 2+ people are selected
            if len(self._group_sel) >= 2:
                tk.Button(vid, text=f"Start Group Call . {len(self._group_sel)} people",
                          command=self._start_selected_group, fg="#06140c", bg=PAD, bd=0,
                          cursor="hand2", font=self.f_mono, padx=12, pady=9).pack(
                          side="bottom", fill="x", padx=8, pady=(0, 8))
        elif st == "black":
            pass
        else:
            big, small, tone = {
                "textonly": ("Text Only", "audio and video can't be carried - call continues as text", WARN),
                "novideo":  ("No Video", "video can't be carried on this link", BAD),
                "videooff": ("Video Off", "you turned video off", INKDIM),
            }[st]
            box = tk.Frame(vid, bg=VIDBG); box.pack(expand=True)
            tk.Label(box, text=big, fg=tone, bg=VIDBG, font=self.f_huge).pack(pady=(0, 6))
            tk.Label(box, text=small, fg=INKDIM, bg=VIDBG, font=self.f_mono_sm,
                     wraplength=240).pack()

        tk.Label(vid, text=f"? {alabel}", fg=acolor, bg=BADGE, font=self.f_mono_sm).place(x=10, y=10)
        if st == "grid":
            tk.Label(vid, text=self.RUNGS[self.video_rung].split()[0], fg=INKDIM, bg=BADGE,
                     font=self.f_mono_sm).place(relx=1.0, x=-46, y=10)

        if self.self_view != "Off":
            sh = {"Full": 130, "Half": 84, "Thumbnail": 50}[self.self_view]
            sv = tk.Frame(self.content, bg=VIDBG, height=sh)
            sv.pack(fill="x"); sv.pack_propagate(False)
            tk.Frame(self.content, bg=LINE, height=1).pack(fill="x")
            lbl = {"watch": "you . watching (no uplink)", "audio": "you . audio only on uplink",
                   "av": "you . self-view"}[self.uplink]
            tk.Label(sv, text=lbl, fg=INKDIM, bg=VIDBG, font=self.f_mono_sm).pack(expand=True)
            tk.Label(sv, text=self.self_view, fg=PADDIM, bg=VIDBG,
                     font=self.f_mono_sm).place(relx=1.0, x=-52, rely=1.0, y=-18)

        chat = tk.Frame(self.content, bg=BG, height=66)
        chat.pack(fill="x"); chat.pack_propagate(False)
        tk.Frame(self.content, bg=LINE, height=1).pack(fill="x")
        for who, msg, _ in self.conversation[-3:]:
            row = tk.Frame(chat, bg=BG); row.pack(anchor="w", padx=10, pady=1, fill="x")
            tk.Label(row, text=f"{who}:", fg=(PAD if who == "you" else INKDIM), bg=BG,
                     font=self.f_mono_sm).pack(side="left")
            tk.Label(row, text=msg, fg=INK, bg=BG, font=self.f_mono_sm).pack(side="left", padx=4)

        self._call_controls()

    def _call_controls(self):
        c = tk.Frame(self.content, bg=PANEL); c.pack(fill="x", side="bottom")
        tk.Frame(self.content, bg=LINE, height=1).pack(fill="x", side="bottom")

        if self.active_peer and self.active_peer in self.calls:
            peer = self.active_peer
            tk.Button(c, text=f"?  Hang Up - {self._name_of(peer)}",
                      command=lambda p=peer: self.hang_up(p),
                      fg="#fff", bg=BAD, bd=0, cursor="hand2", activebackground=BAD,
                      font=self.f_mono, pady=8).pack(fill="x", padx=10, pady=(8, 4))
        elif (self.active_peer and self.active_peer in self.sessions
              and self.active_peer not in self.calls):
            peer = self.active_peer
            tk.Button(c, text=f"Calling {self._name_of(peer)}...  - Cancel",
                      command=lambda p=peer: self.hang_up(p),
                      fg="#06140c", bg=WARN, bd=0, cursor="hand2", activebackground=WARN,
                      font=self.f_mono, pady=8).pack(fill="x", padx=10, pady=(8, 4))

        def btn(parent, text, on, cmd, accent=PAD):
            b = tk.Label(parent, text=text, fg=(accent if on else INKDIM),
                         bg=(PANELHI if on else PANEL), font=self.f_mono_sm, cursor="hand2",
                         highlightbackground=(accent if on else LINE), highlightthickness=1,
                         pady=6)
            b.bind("<Button-1>", lambda e: cmd())
            return b

        # LOCAL presentation toggles (no backend wire - marked)
        def toggle(attr):
            setattr(self, attr, not getattr(self, attr)); self.render()
        def cycle_self():
            order = ["Full", "Half", "Thumbnail", "Off"]
            self.self_view = order[(order.index(self.self_view) + 1) % 4]; self.render()
        def cycle_up():
            order = ["av", "audio", "watch"]
            self.uplink = order[(order.index(self.uplink) + 1) % 3]; self.render()
        def rung(d):
            self.video_rung = max(0, min(3, self.video_rung + d)); self.render()

        r1 = tk.Frame(c, bg=PANEL); r1.pack(fill="x", padx=8, pady=(8, 3))
        chk = tk.Label(c, text=("stop check" if self.preview_active else "check camera + mic"),
                       fg=PAD, bg=PANELHI, font=self.f_mono_sm,
                       cursor="hand2", highlightbackground=PAD, highlightthickness=1, pady=6)
        chk.pack(fill="x", padx=10, pady=(8, 4), before=r1)
        chk.bind("<Button-1>", lambda e: self.check_av())
        btn(r1, f"video {'on' if self.video_enabled else 'off'}", self.video_enabled,
            lambda: toggle("video_enabled")).pack(side="left", expand=True, fill="x", padx=2)
        btn(r1, f"audio {'on' if self.audio_enabled else 'off'}", self.audio_enabled,
            lambda: toggle("audio_enabled")).pack(side="left", expand=True, fill="x", padx=2)
        btn(r1, f"display {'on' if self.display_on else 'off'}", self.display_on,
            lambda: toggle("display_on")).pack(side="left", expand=True, fill="x", padx=2)

        r2 = tk.Frame(c, bg=PANEL); r2.pack(fill="x", padx=8, pady=3)
        btn(r2, f"self . {self.self_view}", False, cycle_self).pack(side="left", expand=True, fill="x", padx=2)
        upl = {"av": "A/V", "audio": "audio", "watch": "watch"}[self.uplink]
        btn(r2, f"uplink . {upl}", self.uplink != "watch", cycle_up).pack(side="left", expand=True, fill="x", padx=2)
        btn(r2, "PTT", self.ptt, lambda: toggle("ptt"), accent=WARN).pack(side="left", expand=True, fill="x", padx=2)

        r3 = tk.Frame(c, bg=PANEL); r3.pack(fill="x", padx=8, pady=3)
        btn(r3, "drop video link" if self.video_possible else "video impossible",
            not self.video_possible, lambda: toggle("video_possible"), accent=BAD).pack(side="left", expand=True, fill="x", padx=2)
        btn(r3, "drop audio link" if self.audio_possible else "audio impossible",
            not self.audio_possible, lambda: toggle("audio_possible"), accent=BAD).pack(side="left", expand=True, fill="x", padx=2)

        r4 = tk.Frame(c, bg=PANEL); r4.pack(fill="x", padx=8, pady=(3, 8))
        btn(r4, "rung -", False, lambda: rung(-1)).pack(side="left", expand=True, fill="x", padx=2)
        tk.Label(r4, text=self.RUNGS[self.video_rung], fg=INKDIM, bg=PANEL,
                 font=self.f_mono_sm).pack(side="left", expand=True, fill="x")
        btn(r4, "rung +", False, lambda: rung(1)).pack(side="left", expand=True, fill="x", padx=2)

    # ------------------------------------------------------------------- Text
    def _text(self):
        room = ("call" if self.chat_sid != LOBBY_SID else "lobby")
        tk.Label(self.content, text=f"room: {room} . converges through the session tuple",
                 fg=INKDIM, bg=BG, font=self.f_mono_sm).pack(anchor="w", padx=12, pady=(10, 6))
        msgs = tk.Frame(self.content, bg=BG); msgs.pack(fill="both", expand=True, padx=10)
        if not self.conversation:
            tk.Label(msgs, text="no messages yet", fg=INKDIM, bg=BG,
                     font=self.f_mono_sm).pack(anchor="w", pady=6)
        for who, msg, at in self.conversation:
            anchor = "e" if who == "you" else "w"
            bubble = tk.Frame(msgs, bg=(PADDIM if who == "you" else PANEL),
                              highlightbackground=LINE, highlightthickness=1)
            bubble.pack(anchor=anchor, pady=4, padx=2)
            if who != "you":
                tk.Label(bubble, text=who, fg=PAD, bg=PANEL, font=self.f_mono_sm).pack(anchor="w", padx=8, pady=(5, 0))
            tk.Label(bubble, text=msg, fg=INK, bg=(PADDIM if who == "you" else PANEL),
                     font=self.f_sans, wraplength=250, justify="left").pack(anchor="w", padx=8, pady=(2, 5))

        # speak-incoming (local TTS affordance - no backend wire)
        sp = tk.Frame(self.content, bg=BG); sp.pack(fill="x", padx=12, pady=4)
        spk = tk.Label(sp, text=f"speak incoming {'on' if self.speak_in else 'off'}",
                       fg=(PAD if self.speak_in else INKDIM), bg=(PANELHI if self.speak_in else PANEL),
                       font=self.f_mono_sm, cursor="hand2", padx=8, pady=4,
                       highlightbackground=(PAD if self.speak_in else LINE), highlightthickness=1)
        spk.pack(side="left"); spk.bind("<Button-1>", lambda e: self._toggle_speak())
        tk.Label(sp, text="local . TTS", fg=INKDIM, bg=BG, font=self.f_mono_sm).pack(side="left", padx=8)

        row = tk.Frame(self.content, bg=PANEL); row.pack(fill="x", side="bottom")
        tk.Frame(self.content, bg=LINE, height=1).pack(fill="x", side="bottom")
        ptt = tk.Label(row, text="PTT", fg=(WARN if self.ptt else INKDIM),
                       bg=(PANELHI if self.ptt else PANEL), font=self.f_mono_sm, cursor="hand2",
                       width=5, pady=10, highlightbackground=(WARN if self.ptt else LINE),
                       highlightthickness=1)
        ptt.pack(side="left", padx=8, pady=8)
        ptt.bind("<ButtonPress-1>", lambda e: self._ptt(True))
        ptt.bind("<ButtonRelease-1>", lambda e: self._ptt(False))
        self.entry = tk.Entry(row, bg=BG, fg=INK, insertbackground=INK, font=self.f_sans,
                              relief="flat", highlightbackground=LINE, highlightthickness=1)
        self.entry.pack(side="left", fill="x", expand=True, pady=8, ipady=6)
        self.entry.bind("<Return>", lambda e: self._send())
        snd = tk.Label(row, text="send", fg="#06140c", bg=PAD, font=self.f_mono, cursor="hand2",
                       padx=14, pady=8)
        snd.pack(side="left", padx=8); snd.bind("<Button-1>", lambda e: self._send())

    def _toggle_speak(self): self.speak_in = not self.speak_in; self.render()
    def _ptt(self, on): self.ptt = on

    def check_av(self):
        """Toggle an in-app A/V check: live camera in the center tile + a mic level bar
        across its bottom. Same device inputs a call uses. No host, no separate window."""
        if self.preview_active:
            self.stop_preview()
        else:
            self.start_preview()

    def open_settings(self, refresh=False):
        cams, mics = _enumerate_devices(refresh=refresh)
        win = tk.Toplevel(self); win.title("Settings - devices"); win.configure(bg=BG)
        win.geometry("480x340"); win.minsize(400, 280)
        tk.Label(win, text="Devices", fg=INK, bg=BG, font=self.f_big).pack(
            anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, text="Camera and microphone for calls and the A/V check.",
                 fg=INKDIM, bg=BG, font=self.f_mono_sm).pack(anchor="w", padx=16, pady=(0, 10))

        cam_map = {lbl: dev for lbl, dev in cams}; cam_map["(none)"] = None
        mic_map = {lbl: dev for lbl, dev in mics}; mic_map["(none)"] = None

        def _current_label(dmap, cur):
            for lbl, dev in dmap.items():
                if dev == cur:
                    return lbl
            return "(none)"

        def picker(title, dmap, current):
            tk.Label(win, text=title, fg=PADDIM, bg=BG, font=self.f_mono_sm).pack(
                anchor="w", padx=16, pady=(8, 2))
            var = tk.StringVar(value=_current_label(dmap, current))
            opts = list(dmap.keys())
            om = tk.OptionMenu(win, var, *opts)
            om.configure(bg=PANEL, fg=INK, activebackground=PANELHI, activeforeground=INK,
                         highlightthickness=1, highlightbackground=LINE, bd=0,
                         font=self.f_mono_sm, anchor="w")
            om["menu"].configure(bg=PANEL, fg=INK, activebackground=PANELHI,
                                 activeforeground=INK, font=self.f_mono_sm)
            om.pack(fill="x", padx=16)
            return var

        cam_var = picker(f"Camera ({len(cams)} found)", cam_map, self.cam)
        mic_var = picker(f"Microphone ({len(mics)} found)", mic_map, self.mic)

        row = tk.Frame(win, bg=BG); row.pack(fill="x", padx=16, pady=16)

        def save():
            self.cam = cam_map.get(cam_var.get())
            self.mic = mic_map.get(mic_var.get())
            try:
                cfg = B.load_config()
                cfg["pref_camera"] = self.cam
                cfg["pref_mic"] = self.mic
                B.save_config(cfg)
            except Exception:
                pass
            self._status(f"devices: cam {self.cam or '-'} . mic {self.mic or '-'}", PAD)
            was_live = self.preview_active
            if was_live:
                self.stop_preview()
            win.destroy()
            if was_live:
                self.start_preview()              # restart check with the new devices

        tk.Button(row, text="Refresh", command=lambda: (win.destroy(), self.open_settings(refresh=True)),
                  fg=INK, bg=PANEL, bd=0, padx=14, pady=6, cursor="hand2",
                  activebackground=PANELHI, activeforeground=INK).pack(side="left")
        tk.Button(row, text="Save", command=save, fg="#06140c", bg=PAD, bd=0,
                  padx=20, pady=6, cursor="hand2", activebackground=PAD).pack(side="right")
        tk.Button(row, text="Cancel", command=win.destroy, fg=INKDIM, bg=PANEL, bd=0,
                  padx=14, pady=6, cursor="hand2", activebackground=PANELHI,
                  activeforeground=INK).pack(side="right", padx=8)

    def start_preview(self):
        if self.preview_active:
            return
        if not shutil.which("ffmpeg"):
            self._status("ffmpeg not on PATH", BAD); return
        self.preview_active = True
        self._frame = None; self._mic_db = None
        self._cam_err = ""; self._prev_t0 = time.monotonic()
        self._prev_stop = threading.Event(); self._prev_procs = []
        self.tab.set("Call"); self.render()        # builds the preview tile + mic bar
        cam = self.cam or _os_default_camera()
        ccmd = _cam_ppm_cmd(cam, fps=(self.fps or 12))
        if ccmd:
            threading.Thread(target=self._cam_reader, args=(ccmd, self._prev_stop),
                             daemon=True).start()
        else:
            self._status("no camera (pass --camera)", BAD)
        if self.mic:
            mcmd = _mic_meter_cmd(self.mic)
            if mcmd:
                threading.Thread(target=self._mic_reader, args=(mcmd, self._prev_stop),
                                 daemon=True).start()
        self.after(120, self._tick_preview)
        self._status("live A/V check - click the tile (or 'stop check') to end", PAD)

    def stop_preview(self):
        if not self.preview_active:
            return
        self.preview_active = False
        if self._prev_stop:
            self._prev_stop.set()
        for p in self._prev_procs:
            try:
                _kill(p)
            except Exception:
                pass
        self._prev_procs = []
        self._frame = None; self._photo = None; self._mic_db = None
        self.preview_lbl = None; self.mic_bar = None; self.mic_lbl = None
        try:
            os.remove(self._tmpimg)
        except OSError:
            pass
        self.render()

    def _cam_reader(self, cmd, stop):
        logf = os.path.join(tempfile.gettempdir(), "frognet_preview.log")

        def log(s):
            try:
                with open(logf, "a") as f:
                    f.write(f"[{time.strftime('%H:%M:%S')}] {s}\n")
            except Exception:
                pass

        log("CAM CMD: " + " ".join(cmd))
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, bufsize=10 ** 7)
        except Exception as e:
            self._cam_err = f"spawn failed: {e}"
            log(self._cam_err)
            self.q.put(("error", f"camera: {e}")); return
        self._prev_procs.append(p)

        def drain_err():
            try:
                for line in iter(p.stderr.readline, b""):
                    if stop.is_set():
                        break
                    s = line.decode("utf-8", "replace").rstrip()
                    if s:
                        self._cam_err = s          # latest line, also shown on the tile
                        log("stderr: " + s)
            except Exception:
                pass

        threading.Thread(target=drain_err, daemon=True).start()
        n = 0
        try:
            for ppm in _read_ppm_stream(p.stdout):
                if stop.is_set():
                    break
                self._frame = ppm
                n += 1
                if n == 1:
                    log(f"FIRST FRAME ok ({len(ppm)} bytes)")
        except Exception as e:
            log(f"reader exception: {e}")
        rc = p.poll()
        log(f"stream ended after {n} frames; ffmpeg rc={rc}")
        if n == 0 and not stop.is_set():
            self._cam_err = self._cam_err or f"no frames; ffmpeg rc={rc}"
            self._cam_err += f"  (log: {logf})"

    def _mic_reader(self, cmd, stop):
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, text=True, bufsize=1)
        except Exception:
            return
        self._prev_procs.append(p)
        for line in p.stderr:
            if stop.is_set():
                break
            m = _RMS_RX.search(line)
            if m:
                self._mic_db = _rms_to_db(m.group(1))

    def _tick_preview(self):
        if not self.preview_active:
            return
        lbl = self.preview_lbl
        try:
            if lbl is not None and lbl.winfo_exists():
                if self._frame is not None:
                    with open(self._tmpimg, "wb") as fh:
                        fh.write(self._frame)
                    photo = tk.PhotoImage(file=self._tmpimg)
                    lbl.config(image=photo, text="")
                    self._photo = photo                 # hold the ref
                elif (self._cam_err and (time.monotonic() - self._prev_t0) > 3.0):
                    lbl.config(text=f"camera didn't start:\n{self._cam_err}\n\n"
                                    f"try Settings -> pick another camera",
                               fg=BAD, image="")
        except Exception:
            pass
        try:
            if (self.mic_bar is not None and self.mic_bar.winfo_exists()
                    and self._mic_db is not None):
                d = max(-60.0, min(0.0, self._mic_db))
                self.mic_bar.place_configure(relwidth=(d + 60.0) / 60.0)
                self.mic_bar.config(bg=(BAD if self._mic_db > -3 else
                                        PAD if self._mic_db > -40 else PADDIM))
                if self.mic_lbl is not None and self.mic_lbl.winfo_exists():
                    self.mic_lbl.config(text=f"mic {self._mic_db:5.1f} dB")
        except Exception:
            pass
        self.after(70, self._tick_preview)

    def _send(self):
        txt = self.entry.get().strip()
        if not txt:
            return
        self.entry.delete(0, "end")
        msg = {"from": self.me_id, "text": txt, "ts": int(time.time() * 1000)}
        # local echo immediately
        self.conversation.append(("you", txt, _hhmm(msg["ts"])))
        self.render()
        # persist the SAME msg through the session tuple (lossless, retries, no dup)
        threading.Thread(target=B.chat_send,
                         args=(self.chat_sid, self.me_id, msg, self.dbhost),
                         daemon=True).start()

    # ===================================================================== Games
    def _games(self):
        """The Games HUB - a live lobby driven by tuples. Games in progress are tuples in the
        shared DB (select across each game\'s dimension); this shows them as JOIN (open seats),
        WATCH (in play), or REPLAY (finished), plus the installed games you can START. Lobby
        chat is a tuple strip at the bottom. Reading a game tuple IS watching it."""
        import games_lobby as GL
        tk.Label(self.content, text="GAMES ON THE POND", fg=INKDIM, bg=BG,
                 font=self.f_mono_sm).pack(anchor="w", padx=16, pady=(12, 4))

        wrap = tk.Frame(self.content, bg=BG)
        wrap.pack(fill="both", expand=True, padx=12)

        # ---- SELECT * across all game tuples, classify ----
        try:
            rows = GL.list_tables(B.T, self.gamehost)
            sec = GL.classify(rows)
        except Exception as e:
            sec = {"joinable": [], "watching": [], "over": []}
            self._status(f"lobby: {e}", BAD)

        def table_row(parent, r, kind):
            card = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
            card.pack(fill="x", pady=3)
            # action button FIRST (right) so it paints on first show
            if kind == "join":
                tk.Button(card, text=f"Join ({r['open_seats']} open)",
                          command=lambda rr=r: self._game_open(rr, "player"),
                          fg="#06140c", bg=PAD, bd=0, padx=12, pady=5, cursor="hand2",
                          font=self.f_mono).pack(side="right", padx=(4, 10))
            elif kind == "watch":
                tk.Button(card, text="Watch",
                          command=lambda rr=r: self._game_open(rr, "watch"),
                          fg="#06140c", bg=PADDIM, bd=0, padx=12, pady=5, cursor="hand2",
                          font=self.f_mono).pack(side="right", padx=(4, 10))
            else:
                tk.Button(card, text="Replay",
                          command=lambda rr=r: self._game_open(rr, "watch"),
                          fg=INK, bg=PANELHI, bd=0, padx=12, pady=5, cursor="hand2",
                          font=self.f_mono).pack(side="right", padx=(4, 10))
            txt = tk.Frame(card, bg=PANEL); txt.pack(side="left", anchor="w", pady=6, padx=8)
            tk.Label(txt, text=f"{r['title']} . {r['table']}", fg=INK, bg=PANEL,
                     font=self.f_big).pack(anchor="w")
            who = ", ".join(r["seats"]) or "no one yet"
            extra = (f"ply {r['ply']}" if r["ply"] else "new")
            here = len(r.get("here") or [])
            tk.Label(txt, text=f"{who} . {extra}" + (f" . {here} here" if here else ""),
                     fg=INKDIM, bg=PANEL, font=self.f_mono_sm).pack(anchor="w")

        # ---- JOIN: open lobby seats ----
        if sec["joinable"]:
            tk.Label(wrap, text="JOIN A GAME", fg=PAD, bg=BG, font=self.f_mono_sm).pack(anchor="w", pady=(6, 2))
            for r in sec["joinable"]: table_row(wrap, r, "join")
        # ---- WATCH: in progress ----
        if sec["watching"]:
            tk.Label(wrap, text="WATCH IN PROGRESS", fg=PADDIM, bg=BG, font=self.f_mono_sm).pack(anchor="w", pady=(8, 2))
            for r in sec["watching"]: table_row(wrap, r, "watch")
        # ---- REPLAY: finished ----
        if sec["over"]:
            tk.Label(wrap, text="FINISHED", fg=INKDIM, bg=BG, font=self.f_mono_sm).pack(anchor="w", pady=(8, 2))
            for r in sec["over"][:4]: table_row(wrap, r, "over")

        # ---- START a new game (installed games) ----
        tk.Label(wrap, text="START A NEW GAME", fg=PAD, bg=BG, font=self.f_mono_sm).pack(anchor="w", pady=(10, 2))
        try:
            beacons = B.ApiBeacons(self.dbhost).beacons()
        except Exception:
            beacons = []
        seen = set()
        startable = False
        for b in beacons:
            if b.get("hub") != "games":
                continue
            bid = b.get("id", "?")
            if bid in seen:
                continue
            seen.add(bid)
            if B.bundle_local_app(b) is None:
                continue
            startable = True
            row = tk.Frame(wrap, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
            row.pack(fill="x", pady=3)
            tk.Button(row, text="Start",
                      command=lambda bb=b: self._game_start(bb),
                      fg="#06140c", bg=PAD, bd=0, padx=14, pady=5, cursor="hand2",
                      font=self.f_mono).pack(side="right", padx=(4, 10))
            tk.Label(row, text=b.get("title", bid), fg=INK, bg=PANEL,
                     font=self.f_big).pack(side="left", anchor="w", padx=10, pady=6)
        if not startable:
            tk.Label(wrap, text="no games installed here", fg=INKDIM, bg=BG,
                     font=self.f_mono_sm).pack(anchor="w", pady=4)

        # ---- lobby chat (a tuple) ----
        self._games_chat(wrap)
        try:
            self.content.update_idletasks()
        except Exception:
            pass

    def _game_meta(self, beacon):
        """(game_name, app_path) for a beacon: game name from id last segment; app from the
        local bundle. boardgame -> its own bg_play; the card games -> the shared game_app."""
        gid = beacon.get("id", "")
        name = gid.split(".")[-1] if gid else beacon.get("title", "game").lower()
        return name

    def _game_start(self, beacon):
        """Matchmaker Start: if there\'s already an OPEN lobby for this game that I\'m not
        already seated at, JOIN it instead of minting another room (prevents duplicate
        matchmaker rooms). Only create a fresh table when none is joinable. One room per
        click is the bug we\'re avoiding here."""
        import games_lobby as GL
        name = self._game_meta(beacon)
        try:
            rows = GL.list_tables(B.T, self.gamehost)
        except Exception:
            rows = []
        # an existing open lobby for THIS game with a seat free and me not already in it
        openrooms = [r for r in rows
                     if r["service"] == name and r["phase"] == "lobby"
                     and r["open_seats"] > 0 and self.me_id not in r["seats"]]
        if openrooms:
            # join the oldest open room (stable: smallest table id) -> matchmaking, no dup
            target = sorted(openrooms, key=lambda r: r["table"])[0]
            self._launch_game_app(name, target["table"], role="player", beacon=beacon)
            return
        # none joinable -> create exactly one new table
        table = f"{self.me_id}-{int(time.time())}"
        self._launch_game_app(name, table, role="start", beacon=beacon)

    def _game_open(self, row, role):
        """Join or watch an existing table."""
        self._launch_game_app(row["service"], row["table"], role=role)

    # which games are the shared games-common codices (run via game_app over tuples)
    CARD_GAMES = ("connectfour", "hearts", "liarsdice", "reversi", "backgammon")

    def _launch_game_app(self, game, table, role="player", beacon=None):
        """Launch the correct app for the game's FAMILY:
          - boardgame  -> its own windowed bg_play.py (elected-host board)
          - backgammon -> its own bg_app.py over the tuple space (--space)
          - card games -> the shared game_app.py (codex run locally over tuples)
        Routing by family is essential: game_app only knows the card-game codices, so
        backgammon/boardgame must NOT be sent to it."""
        import os as _os
        bundles = B.BUNDLES_ROOT
        bid = beacon.get("id") if beacon else None
        watch = (role == "watch")

        if game == "boardgame" or bid == "net.frognet.boardgame":
            app = _os.path.join(bundles, "boardgame", "app", "bg_play.py")
            cmd = [sys.executable, app, "--connect", self.host.split(":")[0],
                   "--who", self.me_name, "--gid", table]
        elif game in self.CARD_GAMES:
            app = _os.path.join(bundles, "games-common", "game_app.py")
            cmd = [sys.executable, app, "--game", game, "--table", table,
                   "--who", self.me_id, "--dbhost", self.gamehost,
                   "--bundles-root", bundles]
            if watch:
                cmd.append("--watch")
            elif role == "start":
                cmd.append("--start")
        else:
            self._status(f"don't know how to launch '{game}'", BAD)
            return

        if not _os.path.isfile(app):
            self._status(f"{game}: app not installed here ({_os.path.basename(app)})", BAD)
            return
        try:
            env = dict(_os.environ, FROGNET_BUNDLES_ROOT=bundles)
            subprocess.Popen(cmd, env=env, start_new_session=(os.name != "nt"))
            self._status(f"{'watching' if watch else 'opening'} {game} . {table}", PAD)
            self.after(800, self.render)
        except Exception as e:
            self._status(f"launch failed: {e}", BAD)

    def _games_chat(self, parent):
        """A lobby-scoped chat right in the Games tab - converges through the same session
        tuple machinery the Text tab uses (B.chat_send/chat_read on LOBBY_SID)."""
        wrap = tk.Frame(parent, bg=BG); wrap.pack(fill="x", side="bottom", pady=(8, 4))
        tk.Frame(parent, bg=LINE, height=1).pack(fill="x", side="bottom")
        tk.Label(wrap, text="lobby chat", fg=INKDIM, bg=BG,
                 font=self.f_mono_sm).pack(anchor="w", pady=(0, 4))
        log = tk.Frame(wrap, bg=BG); log.pack(fill="x")
        for who, msg, _ in self.conversation[-4:]:
            row = tk.Frame(log, bg=BG); row.pack(anchor="w", fill="x")
            tk.Label(row, text=f"{who}:", fg=(PAD if who == "you" else INKDIM), bg=BG,
                     font=self.f_mono_sm).pack(side="left")
            tk.Label(row, text=msg, fg=INK, bg=BG, font=self.f_mono_sm).pack(side="left", padx=4)
        row = tk.Frame(wrap, bg=PANEL); row.pack(fill="x", pady=(4, 0))
        self.games_entry = tk.Entry(row, bg=BG, fg=INK, insertbackground=INK,
                                    font=self.f_sans, relief="flat",
                                    highlightbackground=LINE, highlightthickness=1)
        self.games_entry.pack(side="left", fill="x", expand=True, pady=6, ipady=5, padx=(0, 6))
        self.games_entry.bind("<Return>", lambda e: self._games_send())
        snd = tk.Label(row, text="send", fg="#06140c", bg=PAD, font=self.f_mono, cursor="hand2",
                       padx=14, pady=6)
        snd.pack(side="left"); snd.bind("<Button-1>", lambda e: self._games_send())

    def _games_send(self):
        txt = self.games_entry.get().strip()
        if not txt:
            return
        self.games_entry.delete(0, "end")
        msg = {"from": self.me_id, "text": txt, "ts": int(time.time() * 1000)}
        self.conversation.append(("you", txt, _hhmm(msg["ts"])))
        self.render()
        threading.Thread(target=B.chat_send,
                         args=(LOBBY_SID, self.me_id, msg, self.dbhost),
                         daemon=True).start()

    # ----------------------------------------------------------------- bundle
    def _bundle(self, name, note):
        f = tk.Frame(self.content, bg=BG); f.pack(expand=True, fill="both")
        tk.Label(f, text=f"{name.upper()} HUB", fg=INKDIM, bg=BG, font=self.f_mono_sm).pack(pady=(40, 14))
        tk.Label(f, text=name[0], fg=PADDIM, bg=PANEL, font=self.f_huge, width=3, height=1,
                 highlightbackground=LINE, highlightthickness=1).pack(pady=8)
        tk.Label(f, text=note, fg=INKDIM, bg=BG, font=self.f_sans, wraplength=260,
                 justify="center").pack(pady=12)
        tk.Label(f, text="tab present because the bundle is installed", fg=PADDIM, bg=BG,
                 font=self.f_mono_sm).pack(pady=8)


def _build_args():
    ap = argparse.ArgumentParser(prog="communicator")
    ap.add_argument("--host", help="FrogNet Host (roster :8780 / A/V :9000)")
    ap.add_argument("--name", help="your display name")
    ap.add_argument("--dbhost", help="transient DB host (default databasehost_control.frognet)")
    ap.add_argument("--camera", help="capture device for calls, e.g. 'video=USB Video Device'")
    ap.add_argument("--mic", help="microphone; default = system default mic if omitted")
    ap.add_argument("--fps", type=int, help="call frame rate (default: engine's)")
    ap.add_argument("--bitrate-kbps", type=int, help="call video bitrate")
    ap.add_argument("--check", action="store_true",
                    help="preview camera + mic with ffplay, then exit (no host needed)")
    return ap.parse_args()


def _run_check(a) -> int:
    """Headless A/V check: camera preview window + a live console mic meter."""
    if not _ffplay_path() and not shutil.which("ffmpeg"):
        print("ffmpeg/ffplay not found on PATH"); return 2
    cam = a.camera or _os_default_camera()
    cam_proc = None
    cc = _camera_preview_cmd(cam, a.fps)
    if cc:
        cam_proc = _spawn(cc); print(f"camera preview opened: {cam}")
    else:
        print("no camera - pass --camera 'video=...'")
    try:
        mic = a.mic or _os_default_mic()
        if mic:
            print(f"mic: {mic}")
            _mic_meter_console(mic)                 # blocks; Ctrl-C to stop
        else:
            print("no mic found")
            input("press Enter to stop the camera preview... ")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        if cam_proc:
            try:
                _kill(cam_proc)
            except Exception:
                pass
    return 0


def _resolve_identity(a) -> dict:
    """Host is ALWAYS derived (.1 of this network; shared DB resolves by name) - never asked.
    Name: --name wins; else a saved name; else ASK (prompt prefilled with the derived machine
    name). With --name supplied, jump straight into the session. Persists per-folder."""
    cfg = B.load_config()
    if a.host:
        cfg["host"] = a.host
    cfg.setdefault("host", B._derive_host())        # derived, never prompted
    if a.dbhost:
        cfg["dbhost"] = a.dbhost

    if a.name:                                      # explicit name -> no prompt, just go
        cfg["name"] = a.name
    elif not cfg.get("name"):                       # no flag, none saved -> ask name only
        cfg["name"] = B.prompt_name(B._derive_name())
    cfg["id"] = cfg.get("id") or cfg["name"].lower()

    B.save_config(cfg)                              # persist identity/host (per-folder)
    for k in ("camera", "mic", "fps", "bitrate_kbps"):
        cfg.pop(k, None)
    if a.camera:
        cfg["camera"] = a.camera
    elif cfg.get("pref_camera"):
        cfg["camera"] = cfg["pref_camera"]
    if a.mic:
        cfg["mic"] = a.mic
    elif cfg.get("pref_mic"):
        cfg["mic"] = cfg["pref_mic"]
    if a.fps:
        cfg["fps"] = a.fps
    if a.bitrate_kbps:
        cfg["bitrate_kbps"] = a.bitrate_kbps
    return cfg


if __name__ == "__main__":
    args = _build_args()
    if args.check:
        sys.exit(_run_check(args))
    Communicator(_resolve_identity(args)).mainloop()
