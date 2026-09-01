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
serve_web_test.py - DEV ONLY. Serves the web UI AND the codex verbs from one
origin so web/index.html works UNMODIFIED (its relative /unrest/family-calendar
path resolves to this same server). Open the printed URL in a browser.

    python3 dev/serve_web_test.py        # then open http://127.0.0.1:8888/

Ctrl-C to stop.
"""
import os, sys, json
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # bundle root
sys.path.insert(0, os.path.join(ROOT, "dev"))
sys.path.insert(0, os.path.join(ROOT, "codex"))
from calendar_codex import (CalendarCodex, InMemoryTransientStore,
                            InMemoryPermStore, make_event)

CODEX = CalendarCodex(InMemoryTransientStore(), InMemoryPermStore())
WEB = os.path.join(ROOT, "web")
PREFIX = "/unrest/family-calendar/"


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=WEB, **k)   # static files from web/

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b)

    def _verb(self):
        p = self.path.split("?")[0]
        return p[len(PREFIX):] if p.startswith(PREFIX) else None

    def do_GET(self):
        if self._verb() == "list":
            return self._json(CODEX.t.get("family-calendar.events") or CODEX.refault())
        return super().do_GET()        # serve index.html etc.

    def do_POST(self):
        v = self._verb()
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if v == "create":
            return self._json(CODEX.create_event(make_event(
                body.get("summary", ""), body.get("start", ""), body.get("end", ""),
                location=body.get("location", ""), notes=body.get("notes", ""),
                all_day=body.get("all_day", False))))
        if v == "update":
            return self._json(CODEX.update_event(body.pop("uid"), **body) or {"error": "no uid"})
        if v == "delete":
            return self._json({"ok": CODEX.delete_event(body.get("uid", ""))})
        if v == "presence":
            CODEX.touch_presence(body.get("who", "dev"))
            return self._json({"who_is_here": CODEX.who_is_here()})
        self._json({"error": "unknown verb"}, 404)

    def log_message(self, *a): pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    print(f"open  http://127.0.0.1:{port}/   (UI + codex, same origin)  Ctrl-C to stop")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
