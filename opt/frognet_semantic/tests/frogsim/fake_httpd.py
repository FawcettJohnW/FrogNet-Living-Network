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
"""fake_httpd.py - a node's :80 responder: getHosts.php (its /etc/hosts as JSON)
and frognet_echo.php (its identity CSV). Stands in for the PHP the real proxy
fronts. One per node; the fabric delivers peer :80 sockets here (route-gated)."""
import os, sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
ROOT="/home/claude/frogsim_proc"
NODE=os.environ["FROGSIM_NODE"]; IDENT=os.environ.get("FROGSIM_IDENT","")
def hosts():
    p=f"{ROOT}/nodes/{NODE}/etc_hosts"; out=[]
    if os.path.exists(p):
        for ln in open(p):
            f=ln.split()
            if len(f)>=2 and f[0].startswith("10."): out.append({"ip":f[0],"name":f[1]})
    return out
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        if "getHosts.php" in self.path:
            body=json.dumps(hosts()).encode()
        elif "frognet_echo.php" in self.path:
            body=f"FrogNetHost.{NODE},{IDENT},1,1".encode()
        else:
            body=b""
        self.send_response(200); self.send_header("Content-Length",str(len(body)))
        self.end_headers(); self.wfile.write(body)
if __name__=="__main__":
    port=int(sys.argv[1]); HTTPServer(("127.0.0.1",port),H).serve_forever()
