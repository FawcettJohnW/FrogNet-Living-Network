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
mock_codex_server.py - DEV ONLY. Serves the five Family Calendar codex verbs over
HTTP on 127.0.0.1:8800 using CalendarCodex with in-memory stores, so the web UI and
Android client can be exercised without a FrogNet box. Not for production.
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codex"))
from calendar_codex import (CalendarCodex, InMemoryTransientStore,
                            InMemoryPermStore, make_event)

CODEX = CalendarCodex(InMemoryTransientStore(), InMemoryPermStore())
PREFIX = "/unrest/family-calendar/"


class H(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self): self._send({}, 204)

    def _verb(self):
        p = self.path.split("?")[0]
        return p[len(PREFIX):] if p.startswith(PREFIX) else None

    def do_GET(self):
        v = self._verb()
        if v == "list":
            el = CODEX.t.get("family-calendar.events") or CODEX.refault()
            return self._send(el)
        self._send({"error": "unknown verb"}, 404)

    def do_POST(self):
        v = self._verb()
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if v == "create":
            ev = make_event(body.get("summary", ""), body.get("start", ""),
                            body.get("end", ""), location=body.get("location", ""),
                            notes=body.get("notes", ""), all_day=body.get("all_day", False))
            return self._send(CODEX.create_event(ev))
        if v == "update":
            return self._send(CODEX.update_event(body.pop("uid"), **body) or {"error": "no such uid"})
        if v == "delete":
            return self._send({"ok": CODEX.delete_event(body.get("uid", ""))})
        if v == "presence":
            CODEX.touch_presence(body.get("who", "dev"))
            return self._send({"who_is_here": CODEX.who_is_here()})
        self._send({"error": "unknown verb"}, 404)

    def log_message(self, *a): pass


if __name__ == "__main__":
    print("mock codex on http://127.0.0.1:8800" + PREFIX + "<verb>  (Ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", 8800), H).serve_forever()
