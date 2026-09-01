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
"""serve_web_test.py -- DEV ONLY. UI + backgammon codex on one origin so
web/index.html works unmodified. python3 dev/serve_web_test.py [port]; open the URL."""
import os, sys, json
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT,"codex"))
from backgammon_codex import BackgammonCodex, InMemoryTransient, InMemoryPerm
CODEX=BackgammonCodex(InMemoryTransient(), InMemoryPerm())
WEB=os.path.join(ROOT,"web"); PREFIX="/unrest/family-backgammon/"

class H(SimpleHTTPRequestHandler):
    def __init__(self,*a,**k): super().__init__(*a, directory=WEB, **k)
    def _j(self,o,c=200):
        b=json.dumps(o).encode(); self.send_response(c)
        self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(b)
    def _gid(self):
        from urllib.parse import urlparse, parse_qs
        return parse_qs(urlparse(self.path).query).get("g",["table-1"])[0]
    def _verb(self):
        p=self.path.split("?")[0]; return p[len(PREFIX):] if p.startswith(PREFIX) else None
    def do_GET(self):
        v=self._verb(); g=self._gid()
        if v=="get":
            st=CODEX.get(g) or CODEX.new_game(gid=g); return self._j(st)
        return super().do_GET()
    def do_POST(self):
        v=self._verb(); g=self._gid()
        n=int(self.headers.get("Content-Length",0)); body=json.loads(self.rfile.read(n) or b"{}")
        if v=="new_game": return self._j(CODEX.new_game(gid=g))
        if v=="roll":     return self._j(CODEX.roll(g))
        if v=="move":     return self._j(CODEX.move(g, body["from"], body["die"]))
        if v=="offer_double":   return self._j(CODEX.offer_double(g))
        if v=="accept_double":  return self._j(CODEX.accept_double(g))
        if v=="decline_double": return self._j(CODEX.decline_double(g))
        if v=="presence":
            CODEX.touch_presence(g, body.get("who","dev"))
            return self._j({"who_is_here":CODEX.who_is_here(g)})
        self._j({"error":"unknown verb"},404)
    def log_message(self,*a): pass

if __name__=="__main__":
    port=int(sys.argv[1]) if len(sys.argv)>1 else 8888
    print(f"open  http://127.0.0.1:{port}/   (board + codex, same origin)  Ctrl-C to stop")
    ThreadingHTTPServer(("127.0.0.1",port), H).serve_forever()
