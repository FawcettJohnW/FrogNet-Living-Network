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
"""[REGION_HELD_AUTH_V1] [REGION_CONFIG_IS_THE_HOSTS_V1] oracle: what reaches the RAM host port cannot claim what it is
not. Talks to the host directly with frogram (as an attacker would), and through a front (as a client does).
Usage: run_trust.py <build-dir> <redis-cli> <base-port>"""
import subprocess, sys, time, socket, os, json, base64, tempfile, ctypes
B, CLI, P = sys.argv[1], sys.argv[2], int(sys.argv[3]); API = "/Fawcett.Redis.ram_interface.php"
def up(port):
    t0 = time.time()
    while time.time() - t0 < 10:
        try: socket.create_connection(("127.0.0.1", port), timeout=1).close(); return True
        except OSError: pass
    return False
cfg = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False); cfg.write("requirepass s3cret\n"); cfg.close()
env = dict(os.environ, FROGNET_BLOB_ROOT="/tmp/trust-blobs", RIBBIT_REDIS_CONFIG=cfg.name); os.makedirs(env["FROGNET_BLOB_ROOT"], exist_ok=True)
env.pop("RIBBIT_REDIS_ADMIN", None)
host = subprocess.Popen([B + "/ribbit-redis-host", "--listen", "127.0.0.1:%d" % P, "--api", API, "--quiet"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if not up(P): sys.exit("host did not start")
front = subprocess.Popen([B + "/ribbit-redis-front", "--port", str(P + 1), "--save", ""], env=dict(env, RIBBIT_RAM="127.0.0.1:%d" % P), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
up(P + 1)
# the attacker: a raw frogram client (the rt tool built beside the test)
RT = B + "/raw-call"
def raw(body):
    r = subprocess.run([RT, str(P), API, body], capture_output=True, text=True, env=env, timeout=20)
    return r.stdout.strip() + r.stderr.strip()
def resp(*a): return base64.b64encode(b"*%d\r\n" % len(a) + b"".join(b"$%d\r\n%s\r\n" % (len(x), x) for x in a)).decode()
fails = 0
def check(n, ok, why=""):
    global fails; print(("PASS " if ok else "FAIL ") + n + (" -- " + why[:200] if why else "")); fails += 0 if ok else 1
ctx = {"id": 1, "db": 0, "proto": 2, "authed": True, "user": "default", "name": "", "libname": "", "libver": "", "addr": "x", "laddr": "y",
       "reply_mode": 0, "skip_count": 0, "close_after_reply": False, "no_evict": False, "no_touch": False, "flags": "N", "created_ms": 1, "mx": ""}
out = raw(json.dumps(dict(ctx, p=resp(b"SET", b"pwned", b"1"))))
check("T1 a call naming a connection without its token is refused", "does not name a connection" in out or "ok\":false" in out, out)
mine = json.loads(raw(json.dumps({"new_id": True})).split("RESULT ", 1)[1])
other = json.loads(raw(json.dumps({"new_id": True})).split("RESULT ", 1)[1])
out = raw(json.dumps(dict(ctx, id=other["id"], token=mine["token"], p=resp(b"GET", b"k"))))
check("T2 a real token presented with another connection's id is refused", "does not name a connection" in out, out)
out = raw(json.dumps(dict(ctx, id=mine["id"], token=mine["token"], p=resp(b"SET", b"k", b"v"))))
got = base64.b64decode(json.loads(out.split("RESULT ", 1)[1])["r"]) if "RESULT " in out else b""
check("T3a requirepass from the host's own config: a call claiming authed:true without AUTH gets NOAUTH", got.startswith(b"-NOAUTH"), repr(got))
out = raw(json.dumps(dict(ctx, id=mine["id"], token=mine["token"], p=resp(b"AUTH", b"s3cret") and base64.b64encode(
      b"*2\r\n$4\r\nAUTH\r\n$6\r\ns3cret\r\n*3\r\n$3\r\nSET\r\n$1\r\nk\r\n$1\r\nv\r\n").decode())))
got = base64.b64decode(json.loads(out.split("RESULT ", 1)[1])["r"]) if "RESULT " in out else b""
check("T3b after AUTH on that connection, the same call works", got == b"+OK\r\n+OK\r\n", repr(got))
out = raw(json.dumps({"config": {"requirepass": ""}}))
check("T4 a configuration pushed by an arbitrary caller is refused", "not accepted" in out, out)
c = subprocess.run([CLI, "-p", str(P + 1), "-a", "s3cret", "--no-auth-warning", "get", "k"], capture_output=True, text=True).stdout.strip()
n = subprocess.run([CLI, "-p", str(P + 1), "get", "k"], capture_output=True, text=True).stdout.strip()
check("T5 through a front: AUTH with the host's password reads the key; without it, NOAUTH", c == "v" and "NOAUTH" in n, "%r / %r" % (c, n))
for p in (front, host): p.kill()
print("RESULT", "GREEN" if fails == 0 else "RED", fails); sys.exit(1 if fails else 0)
