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
"""[READ_YOUR_OWN_WRITE_V1] John 2026-09-27: put() is complete when THIS participant's own consequences of the write
are observable. So a put followed at once by a get, in the same participant, must see the put -- every time, including
the first put at a new prefix length and a withdrawal followed at once by a get.
usage: tools/test_read_your_write.py RAM_HOST RAM_PORT [ROUNDS]"""
import json, subprocess, sys, time
host, port = sys.argv[1], sys.argv[2]; rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 40
p = subprocess.Popen(["./ribbit_cpp/ribbit-lisp", "--ram", host, port], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open("/tmp/ryw_client_err.txt", "w"), text=True)
def call(op, **a):
    p.stdin.write(json.dumps({"operation": op, "args": a}) + "\n"); p.stdin.flush(); r = json.loads(p.stdout.readline())
    if not r.get("ok"): raise RuntimeError("%s: %s" % (op, r.get("error")))
    return r["result"]
def locs(x):
    x = json.loads(x) if isinstance(x, str) else x
    return sorted(r["address"] for r in x.get("rlocs", [])) if isinstance(x, dict) else []
call("site.add", iid="0", prefix="198.18.0.0/16", group="", accept_more_specifics=True)
assert call("resolver.wait_site", iid="0", prefix="198.18.0.0/16", group="", active=True) == "good"
misses = []
for i in range(rounds):
    mask = 17 + i % 12                                     # new prefix lengths as it goes: the first put at a length
    pfx = "198.18.%d.0/%d" % ((i * 7) % 256 & (0xff << (24 - mask) if mask < 24 else 0xff), mask)
    eid = pfx.split("/")[0].rsplit(".", 1)[0] + ".9"
    r = "192.0.2.%d" % (1 + i % 200)
    call("registration.put", iid="0", prefix=pfx, group="", ttl=60, rloc_set=[r])
    got = locs(call("resolution.get", iid="0", prefix=eid + "/32", group=""))
    if r not in got: misses.append(("put", pfx, r, got))
    call("registration.delete", iid="0", prefix=pfx, group="")
    got = locs(call("resolution.get", iid="0", prefix=eid + "/32", group=""))
    if r in got: misses.append(("delete", pfx, r, got))
p.stdin.close(); p.wait(timeout=10)
for m in misses[:6]: print("  MISS", m)
print("%s: %d of %d put->get / delete->get pairs did not see their own write" % ("READ-YOUR-WRITE PASS" if not misses else "READ-YOUR-WRITE FAIL", len(misses), 2 * rounds))
sys.exit(0 if not misses else 1)
