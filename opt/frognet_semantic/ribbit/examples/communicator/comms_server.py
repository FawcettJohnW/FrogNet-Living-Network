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
comms_server.py -- Communicator UI backend (shared by the web and Tk front ends).

One headless backend, two thin front ends. It owns the control plane (comms_control:
presence/call/chat over BLDC/UnREST on the data host) and, while in a call, an fnav.Call
running with no_display=True -- fnav still captures, encodes, sends, receives, and decodes;
it just doesn't open its own window, because the UI is the display.

It exposes a small local HTTP API that any front end drives:
  GET  /                  -> the web UI (comms_web.html, if present beside this file)
  GET  /api/state         -> {me, roster[], call{session,host,port,members}|null,
                             codec, stats{}, chat[]}
  GET  /api/calls         -> {calls:[{session,host,port,members}]}  every open call
  GET  /api/games         -> {games:[...]}  open game tables (games_lobby.list_tables)
  POST /api/call          -> start a call           {session,host,port}
  POST /api/join  {session}
  POST /api/hangup
  POST /api/chat  {text}
  POST /api/codec {codec:"vp8"|"h264"|"h264hw"}
  POST /api/quality {cap:int}  -> live video quality cap (5=360p,6=480p,7=Auto)
  GET  /api/video.jpg     -> latest composited video tiles as JPEG (self + peers)

Zero new dependencies: stdlib http.server/json/threading + cv2.imencode (headless JPEG).

Run:  python3 comms_server.py --name Gorp [--port 8800] [--relay 10.250.250.1:9000]
Then open http://localhost:8800  (or point the Tk client at the same port).
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import cv2

import comms_control as CC
import fnav

HEARTBEAT_S = 10
HERE = os.path.dirname(os.path.abspath(__file__))


class Backend:
    """Owns the control plane and the active call. Thread-safe enough for the HTTP handlers:
    control-plane reads/writes are independent calls; the call object is swapped under a lock."""
    def __init__(self, args):
        self.args = args
        self.me_id = args.id or f"{args.name.lower()}-{os.urandom(2).hex()}"
        caps = {"cam": not args.no_video, "mic": not args.no_audio,
                "no_video": args.no_video, "no_audio": args.no_audio}
        relay = None
        if args.relay:
            h, _, p = args.relay.partition(":")
            relay = (h, int(p or 9000))
        self.cp = CC.ControlPlane(self.me_id, args.name, caps=caps, relay=relay)
        self.lock = threading.Lock()
        self.call = None             # fnav.Call while in a call
        self.session = None
        self._stop = threading.Event()
        threading.Thread(target=self._heartbeat, daemon=True).start()

    def _heartbeat(self):
        while not self._stop.is_set():
            try:
                self.cp.announce()
                self.cp.refresh_calls()
            except Exception as e:
                print(f"[presence] {type(e).__name__}: {e}")
            self._stop.wait(HEARTBEAT_S)

    # ---- call lifecycle ----
    def _launch(self, host, port):
        # stop any existing call FIRST -- otherwise the old fnav.Call's capture/encode/audio
        # threads keep running and a new set stacks on top (leaked encoders = the CPU pile).
        with self.lock:
            old = self.call; self.call = None
        if old is not None:
            try: old._stop.set()
            except Exception: pass
            time.sleep(0.2)                       # let its threads observe _stop and exit
        a = self.args
        call = fnav.Call(
            host, port, a.name,
            in_dev=fnav.A._resolve_device(a.in_dev),
            out_dev=fnav.A._resolve_device(a.out_dev),
            cam=a.cam, no_video=a.no_video, no_audio=a.no_audio,
            duck=not a.no_duck, fps=a.fps, no_display=True,      # UI is the display
            codec_id={"auto": None, "vp8": fnav.CODEC_VP8, "h264": fnav.CODEC_H264_SW,
                      "h264hw": fnav.CODEC_H264_HW}[a.codec],
            jitter_ms=a.jitter)
        threading.Thread(target=call.run, daemon=True).start()
        with self.lock:
            self.call = call

    def start_call(self):
        info = self.cp.start_call(members=[self.me_id])
        self.session = info["session"]
        self._launch(info["host"], info["port"])
        return info

    def join_call(self, session):
        info = self.cp.join_call(session)
        if not info:
            return None
        self.session = session
        self._launch(info["host"], info["port"])
        return info

    def hangup(self):
        with self.lock:
            c = self.call; self.call = None
        if c:
            c._stop.set()
        if self.session:
            try: self.cp.leave_call(self.session)
            except Exception: pass
        self.session = None

    def set_codec(self, codec):
        cid = {"vp8": fnav.CODEC_VP8, "h264": fnav.CODEC_H264_SW,
               "h264hw": fnav.CODEC_H264_HW}.get(codec)
        with self.lock:
            if self.call is not None and cid is not None:
                self.call.codec_id = cid          # TX loop applies + falls back if unusable

    # ---- "on the pond": every open call/stream and game table, joinable from the UI ----
    def list_calls(self):
        try:
            return self.cp.list_calls()
        except Exception:
            return []

    def list_games(self):
        try:
            import games_lobby
            import frognet_tuples as T
            return games_lobby.list_tables(T, self.cp.dbhost)
        except Exception:
            return []

    # ---- live stream parameter: video quality cap (resolution). codec is set_codec. ----
    def set_level_cap(self, cap):
        with self.lock:
            if self.call is not None and hasattr(self.call, "set_level_cap"):
                self.call.set_level_cap(cap)

    # ---- state for the UI ----
    def state(self):
        with self.lock:
            c = self.call
        call_info = None
        if self.session:
            call_info = self.cp.call_info(self.session)
        codec = "vp8"
        stats = {}
        diag = {"have_cam": None, "self_frame": False, "peer_tiles": 0, "connected": False}
        if c is not None:
            codec = {fnav.CODEC_VP8: "vp8", fnav.CODEC_H264_SW: "h264",
                     fnav.CODEC_H264_HW: "h264hw"}.get(c.codec_id, "vp8")
            try: stats = c.stats.snapshot()
            except Exception: stats = {}
            diag["have_cam"] = getattr(c, "have_cam", None)
            diag["self_frame"] = getattr(c, "_self_frame", None) is not None
            diag["connected"] = getattr(c, "sock", None) is not None and not c._stop.is_set()
            try: diag["peer_tiles"] = len(c.vdec.tiles()) if c.vdec else 0
            except Exception: pass
        return {
            "me": {"id": self.me_id, "name": self.args.name},
            "roster": self.cp.roster(),
            "in_call": c is not None,
            "call": call_info,
            "session": self.session,
            "codec": codec,
            "stats": stats,
            "diag": diag,
            "chat": self.cp.read_chat(self.session) if self.session else [],
        }

    # ---- the video surface: composite self + peer tiles into one JPEG ----
    def video_jpeg(self):
        with self.lock:
            c = self.call
        tiles = []
        if c is not None:
            mine = getattr(c, "_self_frame", None)
            if mine is not None:
                tiles.append(("you", mine))
            if c.vdec is not None:
                for src, img in sorted(c.vdec.tiles().items()):
                    tiles.append((src, img))
        if not tiles:
            # idle placeholder
            blank = np.zeros((180, 320, 3), dtype=np.uint8)
            cv2.putText(blank, "no video", (90, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (90, 90, 90), 1)
            tiles = [("", blank)]
        TW, TH = 320, 180
        cells = []
        for label, img in tiles:
            t = cv2.resize(img, (TW, TH))
            if label:
                color = (0, 220, 255) if label == "you" else (0, 230, 0)
                cv2.putText(t, label, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1)
            cells.append(t)
        grid = np.hstack(cells) if len(cells) > 1 else cells[0]
        ok, buf = cv2.imencode(".jpg", grid, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return bytes(buf) if ok else b""

    def shutdown(self):
        self._stop.set()
        self.hangup()
        try: self.cp.go_offline()
        except Exception: pass


def make_handler(backend: Backend):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return {}

        def do_GET(self):
            path = self.path.split("?", 1)[0]      # drop query (browser adds ?t= cache-buster)
            if path == "/" or path == "/index.html":
                p = os.path.join(HERE, "comms_web.html")
                if os.path.exists(p):
                    body = open(p, "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._json({"error": "comms_web.html not found beside comms_server.py"}, 404)
            elif path == "/api/state":
                self._json(backend.state())
            elif path == "/api/calls":
                self._json({"calls": backend.list_calls()})
            elif path == "/api/games":
                self._json({"games": backend.list_games()})
            elif path == "/api/video.jpg":
                jpg = backend.video_jpeg()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(jpg)))
                self.end_headers()
                self.wfile.write(jpg)
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            b = self._body()
            if path == "/api/call":
                self._json(backend.start_call())
            elif path == "/api/join":
                info = backend.join_call(b.get("session", ""))
                self._json(info or {"error": "no such call"}, 200 if info else 404)
            elif path == "/api/hangup":
                backend.hangup(); self._json({"ok": True})
            elif path == "/api/chat":
                if backend.session and b.get("text"):
                    backend.cp.send_chat(backend.session, b["text"])
                self._json({"ok": True})
            elif path == "/api/codec":
                backend.set_codec(b.get("codec", "vp8")); self._json({"ok": True})
            elif path == "/api/quality":
                backend.set_level_cap(b.get("cap", 7)); self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description="FrogNet Communicator UI backend (web + Tk).")
    ap.add_argument("--name", required=True)
    ap.add_argument("--id", default=None)
    ap.add_argument("--port", type=int, default=8800, help="UI port (default 8800)")
    ap.add_argument("--bind", default="127.0.0.1",
                    help="address to serve on. 127.0.0.1 = this box only (default); "
                         "0.0.0.0 = reachable from other boxes on the LAN/mesh")
    ap.add_argument("--relay", default=None, help="media relay HOST:PORT override")
    ap.add_argument("--in", dest="in_dev", default=None)
    ap.add_argument("--out", dest="out_dev", default=None)
    ap.add_argument("--cam", type=int, default=0)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--jitter", type=int, default=120, help="audio jitter cushion ms")
    ap.add_argument("--codec", choices=["auto", "vp8", "h264", "h264hw"], default="auto")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-duck", action="store_true")
    args = ap.parse_args(argv)

    backend = Backend(args)
    httpd = ThreadingHTTPServer((args.bind, args.port), make_handler(backend))
    print(f"FrogNet Communicator -- {args.name}")
    if args.bind in ("0.0.0.0", "::"):
        # show every address this box answers on, so you can browse from another machine
        import socket as _s
        addrs = set()
        # [HOSTS_ONLY_V1] cosmetic listing of this box's addresses -- read them from
        # the interfaces, not from a resolver.
        try:
            import subprocess as _sp
            _out = _sp.run(["ip", "-4", "-o", "addr", "show"],
                           capture_output=True, text=True, timeout=5).stdout
            for _line in _out.splitlines():
                _p = _line.split()
                if len(_p) >= 4 and "/" in _p[3]:
                    ip = _p[3].split("/")[0]
                    if not ip.startswith("127."):
                        addrs.add(ip)
        except Exception:
            pass
        print(f"  serving on all interfaces, port {args.port}")
        print(f"  open:  http://localhost:{args.port}   (from this box)")
        for ip in sorted(addrs):
            print(f"         http://{ip}:{args.port}   (from another box)")
        if not addrs:
            print(f"         http://<this-box-ip>:{args.port}   (from another box)")
    else:
        print(f"  open:  http://{args.bind}:{args.port}")
        if args.bind in ("127.0.0.1", "localhost"):
            print("  (this box only -- use --bind 0.0.0.0 to reach it from another machine)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        backend.shutdown()
        print("\nbye.")


if __name__ == "__main__":
    main()
