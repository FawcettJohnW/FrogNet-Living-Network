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
"""[FRONT_RECONNECTS_V1] oracle: the RAM host goes away and comes back; the front does not stay dead.
Usage: run_reconnect.py <build-dir> <redis-cli> <base-port>"""
import subprocess, sys, time, socket, os
B, CLI, P = sys.argv[1], sys.argv[2], int(sys.argv[3]); API = "/Fawcett.Redis.ram_interface.php"
env = dict(os.environ, FROGNET_BLOB_ROOT="/tmp/reconnect-blobs"); os.makedirs(env["FROGNET_BLOB_ROOT"], exist_ok=True); env.pop("RIBBIT_REDIS_ADMIN", None)
def up(port):
    t0 = time.time()
    while time.time() - t0 < 10:
        try: socket.create_connection(("127.0.0.1", port), timeout=1).close(); return True
        except OSError: pass
    return False
def host(): h = subprocess.Popen([B + "/ribbit-redis-host", "--listen", "127.0.0.1:%d" % P, "--api", API, "--quiet"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); up(P); return h
def cli(*a): return subprocess.run([CLI, "-p", str(P + 1)] + list(a), capture_output=True, text=True, timeout=30).stdout.strip()
fails = 0
def check(n, ok, why=""):
    global fails; print(("PASS " if ok else "FAIL ") + n + (" -- " + why if why else "")); fails += 0 if ok else 1
h = host()
f = subprocess.Popen([B + "/ribbit-redis-front", "--port", str(P + 1), "--save", ""], env=dict(env, RIBBIT_RAM="127.0.0.1:%d" % P), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
up(P + 1)
check("R1 before: the front serves", cli("set", "k", "v") == "OK" and cli("get", "k") == "v")
h.kill(); h.wait()
t0 = time.time(); out = cli("get", "k"); dt = time.time() - t0
check("R2 host gone: the front answers at once with an error naming the region (no hang, no stale answer)",
      "region could not be reached" in out and dt < 5, "%.1fs %r" % (dt, out))
check("R3 the front process is still running", f.poll() is None)
h = host()
check("R4 host back (a new region): a new connection is served", cli("set", "k2", "v2") == "OK" and cli("get", "k2") == "v2")
check("R5 the new region is new: what the old one held is gone (no persistence, by construction)", cli("get", "k") == "")
for p in (f, h): p.kill()
print("RESULT", "GREEN" if fails == 0 else "RED", fails); sys.exit(1 if fails else 0)
