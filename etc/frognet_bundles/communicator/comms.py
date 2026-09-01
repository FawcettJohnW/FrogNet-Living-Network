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
comms.py - FrogNet Communicator (CLI).

The runnable front end for the control plane. It drives comms_control (the BLDC/UnREST
layer: presence, call setup, chat) and hands off to fnav for the actual audio/video call.
This is the command-line driver, not the graphical app - the GUI is a later layer on the
SAME control plane.

What it does:
  - announces your presence on a background heartbeat (LATEST_ONLY: stale == offline)
  - lets you see who's online, start or join a call, and chat
  - on `call`/`join`, resolves the elected media host via the control plane and launches
    fnav.Call pointed at it - the data plane (raw A/V on FNWP-1) is all fnav's

Run:
  python3 comms.py --name Gorp
  python3 comms.py --name Alice --in 23 --out 5 --codec h264
Then type commands at the prompt:  roster | call | join <session> | chat <text> | who | quit

Flags after --name pass through to fnav for the call leg:
  --in/--out <dev>  --cam N  --fps N  --codec vp8|h264|h264hw
  --no-video  --no-audio  --no-display  --no-duck
"""
from __future__ import annotations

import argparse
import os
import threading
import time
import uuid

import comms_control as CC
import fnav


HEARTBEAT_S = 10          # re-announce presence this often (well under PRESENCE_FRESH_S=30)


class CommsCLI:
    def __init__(self, args):
        self.args = args
        self.me_id = args.id or f"{args.name.lower()}-{uuid.uuid4().hex[:4]}"
        caps = {"cam": not args.no_video, "mic": not args.no_audio,
                "no_video": args.no_video, "no_audio": args.no_audio}
        relay = None
        if args.relay:
            rh, _, rp = args.relay.partition(":")
            relay = (rh, int(rp or 9000))
        self.cp = CC.ControlPlane(self.me_id, args.name, caps=caps, relay=relay)
        self._stop = threading.Event()
        self._in_call = None          # active fnav.Call thread, if any
        self._relay = None            # in-process relay, if we're serving

    def cmd_serve(self, bind="0.0.0.0:9000"):
        """Start the fnav relay in-process so this box hosts the call - no separate terminal."""
        if self._relay:
            print("  (already serving)")
            return
        h, p = fnav.A._hostport(bind)
        r = fnav.Relay(h, p)
        threading.Thread(target=r.serve, daemon=True).start()
        self._relay = r
        print(f"[serve] relay listening on {bind} - others can call/join through this host")

    # ---- presence heartbeat ----
    def _heartbeat(self):
        while not self._stop.is_set():
            try:
                self.cp.announce()
                self.cp.refresh_calls()          # keep active calls joinable (don't age out)
            except Exception as e:
                print(f"[presence] announce failed: {type(e).__name__}: {e}")
            self._stop.wait(HEARTBEAT_S)

    # ---- the fnav hand-off ----
    def _launch_fnav(self, host, port):
        """Start an fnav.Call leg pointed at the resolved media host, in its own thread so the
        CLI stays responsive. fnav owns the data plane from here."""
        if self._in_call:                          # stop any prior leg first (no stacking)
            try: self._in_call[0]._stop.set()
            except Exception: pass
            self._in_call[1].join(timeout=2.0)
            self._in_call = None
        a = self.args
        call = fnav.Call(
            host, port, a.name,
            in_dev=fnav.A._resolve_device(a.in_dev),
            out_dev=fnav.A._resolve_device(a.out_dev),
            cam=a.cam, no_video=a.no_video, no_audio=a.no_audio,
            duck=not a.no_duck, fps=a.fps, no_display=a.no_display,
            codec_id={"auto": None, "vp8": fnav.CODEC_VP8, "h264": fnav.CODEC_H264_SW,
                      "h264hw": fnav.CODEC_H264_HW}[a.codec])
        t = threading.Thread(target=call.run, daemon=True)
        t.start()
        self._in_call = (call, t)
        print(f"[call] connected to media host {host}:{port} - fnav running. "
              f"(Esc/q in the video window, or 'hangup' here)")

    # ---- commands ----
    def cmd_roster(self):
        people = self.cp.roster()
        if not people:
            print("  (nobody else online)")
            return
        for p in people:
            cam = "video" if p["caps"].get("cam") else "audio-only"
            mark = " (you)" if p["id"] == self.me_id else ""
            print(f"  {p['name']:<16} {p['status']:<8} {cam}{mark}")

    def cmd_call(self):
        info = self.cp.start_call(members=[self.me_id])
        print(f"[call] started session {info['session']}  (share this id so others can: join {info['session']})")
        self._launch_fnav(info["host"], info["port"])

    def cmd_join(self, session):
        info = self.cp.join_call(session)
        if not info:
            visible = self.cp.list_calls()
            if visible:
                print(f"[call] no call '{session}'. Visible sessions: "
                      + ", ".join(v["session"] for v in visible))
            else:
                print(f"[call] no call '{session}' - and no call tuples are visible at all "
                      f"(the originator's control host may not have reached yours yet, "
                      f"or the call aged out). Have them re-run 'call' and join promptly.")
            return
        print(f"[call] joining session {session}")
        self._launch_fnav(info["host"], info["port"])

    def cmd_chat(self, session, text):
        self.cp.send_chat(session, text)

    def cmd_readchat(self, session):
        for m in self.cp.read_chat(session):
            print(f"  {m['from']}: {m['text']}")

    def cmd_hangup(self):
        if not self._in_call:
            print("  (not in a call)")
            return
        call, t = self._in_call
        call._stop.set()
        t.join(timeout=3.0)
        self._in_call = None
        print("[call] hung up.")

    # ---- REPL ----
    def _acquire_single_instance(self):
        """Refuse to start if another comms.py for this name is already running - that's how
        a dozen instances pile up (each its own encoder + presence announcer). Cross-platform:
        fcntl on Unix, msvcrt on Windows. Lock releases when this process dies."""
        import tempfile
        path = os.path.join(tempfile.gettempdir(), f"comms-{self.args.name.lower()}.lock")
        self._lock_path = path
        try:
            self._lock_fh = open(path, "a+")
        except Exception:
            return True                          # can't open lockfile -> don't block startup
        locked = False
        try:
            try:
                import fcntl                      # Unix
                fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except ModuleNotFoundError:
                import msvcrt                     # Windows
                self._lock_fh.seek(0)
                msvcrt.locking(self._lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
        except OSError:
            locked = False                        # someone else holds it
        except Exception:
            return True                           # unknown locking issue -> don't block startup
        if locked:
            try:
                self._lock_fh.seek(0); self._lock_fh.truncate()
                self._lock_fh.write(str(os.getpid())); self._lock_fh.flush()
            except Exception:
                pass
            return True
        other = ""
        try:
            with open(path) as f: other = f.read().strip()
        except Exception:
            pass
        print(f"Another Communicator for '{self.args.name}' is already running"
              + (f" (pid {other})" if other else "") + ".")
        print(f"  Stop that one first, or use a different --name.")
        return False

    def run(self):
        if not self._acquire_single_instance():
            return
        threading.Thread(target=self._heartbeat, daemon=True).start()
        print(f"FrogNet Communicator - you are {self.args.name} ({self.me_id})")
        print("commands: roster | serve [bind] | call | join <session> | chat <session> <text> | "
              "readchat <session> | hangup | quit")
        try:
            while not self._stop.is_set():
                try:
                    line = input("> ").strip()
                except EOFError:
                    break
                if not line:
                    continue
                parts = line.split(maxsplit=2)
                cmd = parts[0].lower()
                if cmd in ("quit", "exit"):
                    break
                elif cmd in ("roster", "who"):
                    self.cmd_roster()
                elif cmd == "call":
                    self.cmd_call()
                elif cmd == "serve":
                    self.cmd_serve(parts[1] if len(parts) > 1 else "0.0.0.0:9000")
                elif cmd == "join" and len(parts) > 1:
                    self.cmd_join(parts[1])
                elif cmd == "chat" and len(parts) > 2:
                    self.cmd_chat(parts[1], parts[2])
                elif cmd == "readchat" and len(parts) > 1:
                    self.cmd_readchat(parts[1])
                elif cmd == "hangup":
                    self.cmd_hangup()
                else:
                    print("  ?  roster | call | join <session> | chat <session> <text> | "
                          "readchat <session> | hangup | quit")
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            if self._in_call:
                self._in_call[0]._stop.set()
            try:
                self.cp.go_offline()
            except Exception:
                pass
            print("bye.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="FrogNet Communicator (CLI driver).")
    ap.add_argument("--name", required=True, help="your display name (e.g. Gorp, Alice)")
    ap.add_argument("--id", default=None, help="stable id (default: name + random suffix)")
    ap.add_argument("--relay", default=None,
                    help="media relay HOST:PORT to use when the mesh election can't resolve one "
                         "(e.g. 10.250.250.1:9000)")
    # fnav passthrough for the call leg
    ap.add_argument("--in", dest="in_dev", default=None)
    ap.add_argument("--out", dest="out_dev", default=None)
    ap.add_argument("--cam", type=int, default=0)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--codec", choices=["auto", "vp8", "h264", "h264hw"], default="auto")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-display", action="store_true")
    ap.add_argument("--no-duck", action="store_true")
    CommsCLI(ap.parse_args(argv)).run()


if __name__ == "__main__":
    main()
