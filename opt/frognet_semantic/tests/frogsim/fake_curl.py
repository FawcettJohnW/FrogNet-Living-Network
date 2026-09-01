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
"""fake_curl.py - route-gated HTTP boundary for discovery probes. echo/getHosts go
via curl to a peer's :80; here we gate by the caller's faked kernel (forward route +
return-path to the caller's src) and serve the OWNER node's real state."""
import os, sys, json
sys.path.insert(0, "/home/claude/frogsim_proc")
import fabric, frogsim_transport as ft
ROOT="/home/claude/frogsim_proc"; NODE=os.environ.get("FROGSIM_NODE","")

def reachable(pip):
    via,dev,src = fabric._route_get(NODE, pip)
    if dev is None: return False, "no-route"
    owner = ft.owner_of_ip(pip)
    if not owner: return False, "no-owner"
    if src:  # return-path
        _,rdev,_ = fabric._route_get(owner, src)
        if rdev is None: return False, "return-path-lost"
    return owner, "ok"

def node_hosts(owner):
    p=f"{ROOT}/nodes/{owner}/etc_hosts"
    out=[]
    if os.path.exists(p):
        for ln in open(p):
            f=ln.split()
            if len(f)>=2 and f[0].startswith("10."):
                out.append({"ip":f[0],"name":f[1]})
    return out

argv=sys.argv[1:]
url=next((a for a in argv if a.startswith("http://")), "")
want_code = "%{http_code}" in " ".join(argv)
pip = url.split("://",1)[1].split("/",1)[0] if url else ""
owner, why = reachable(pip)
body=""; code="200"
if not owner:
    code="000"
    if "-f" in argv or "-fsS" in argv or "-fsSk" in argv:
        sys.stderr.write(f"curl: (7) fabric gate: {why} to {pip}\n"); 
        sys.stdout.write(("" if not want_code else code)); sys.exit(7)
else:
    if "frognet_echo.php" in url:
        # peer identity CSV: name,dot_one,rtt,rtt  (dot_one = pip)
        name=f"FrogNetHost.{owner}"
        body=f"{name},{pip},1,1"
    elif "getHosts.php" in url:
        body=json.dumps(node_hosts(owner))
    else:
        body=""
sys.stdout.write(body + (code if want_code else ""))
sys.exit(0)
