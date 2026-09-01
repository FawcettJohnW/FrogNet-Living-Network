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
# FEATURE STATUS: standalone registeredUsers tuple service for the FrogNet
# Communicator app. It implements EXACTLY the three calls the app makes
# (GET/POST/DELETE /registeredUsers) over HTTP, JSON-file backed, stdlib only.
# It STANDS IN for the network tuple-space until you wire it to the real one:
# replace _read/_write with reads/writes against your Host's tuple store and the
# app needs no change. Verified in-container: GET/POST/DELETE round-trip.
"""
FrogNet registeredUsers service.

Run on the Host (a DIFFERENT port from the video stream server):
    python3 frognet_roster_server.py --port 8780

Then in the Communicator app set Host to:
    <host-ip>:8780        e.g. 10.250.250.1:8780

This is the roster, not the media. The stream server (frognet_communicator.py
--serve 9100) is a separate service on its own port.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

LOCK = threading.Lock()
STORE = os.path.join(os.path.expanduser("~"), ".frognet_communicator", "roster_server.json")
RESOURCE = "/registeredUsers"


def _read() -> dict:
    try:
        with open(STORE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _write(t: dict) -> None:
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(t, fh)
    os.replace(tmp, STORE)


class Handler(BaseHTTPRequestHandler):
    server_version = "FrogNetRoster/1.0"

    def _send(self, code, obj=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        if self.path.rstrip("/") == RESOURCE:
            with LOCK:
                users = sorted(_read().values(), key=lambda u: u.get("name", "").lower())
            self._send(200, {"users": users})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != RESOURCE:
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        uid = body.get("id") or body.get("name", "").strip().lower()
        if not uid:
            return self._send(400, {"error": "id or name required"})
        body["id"] = uid
        body.setdefault("name", uid)
        body.setdefault("status", "online")
        body.setdefault("ts", int(time.time() * 1000))
        with LOCK:
            d = _read()
            d[uid] = {**d.get(uid, {}), **body}
            _write(d)
            saved = d[uid]
        self._send(200, {"ok": True, "user": saved})

    def do_DELETE(self):
        prefix = RESOURCE + "/"
        if not self.path.startswith(prefix):
            return self._send(404, {"error": "not found"})
        uid = unquote(self.path[len(prefix):])
        with LOCK:
            d = _read()
            existed = d.pop(uid, None) is not None
            _write(d)
        self._send(200, {"ok": True, "removed": existed})

    def log_message(self, *args):
        # one tidy line per request instead of the default noisy format
        print(f"  {self.address_string()} {self.command} {self.path}", flush=True)


def main():
    ap = argparse.ArgumentParser(prog="frognet_roster_server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8780)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"FrogNet registeredUsers service on {args.host}:{args.port}")
    print(f"  store: {STORE}")
    print(f"  app Host field -> <host-ip>:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")


if __name__ == "__main__":
    main()
