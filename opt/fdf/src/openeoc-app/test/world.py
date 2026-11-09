#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# world.py FDF_RAM OUTFILE -- [RT_APP_TEST_V1] the test world on its own, for driving an app by hand or from a shell:
# the general RAM and one per team (fdf-ram), the door stand-in (op=rams, op=login), rt-sim. Writes the door's
# host:port to OUTFILE, then runs until killed.
import json, os, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
HOST = sys.argv[1]; OUT = sys.argv[2]
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE)
SRC = os.path.dirname(APP); FDF = os.path.join(SRC, "fdf-source", "fdf")
def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
T = tempfile.mkdtemp(prefix="rt-app-"); os.makedirs(T + "/var")
procs = []
where = {}
for r in ("general", "alpha", "bravo", "charlie", "delta"):
    p = free(); where["openeoc." + r] = "127.0.0.1:%d" % p
    procs.append(subprocess.Popen([HOST, "--listen", "127.0.0.1:%d" % p, "--api", "/FawcettInnovations.RINA.ram_interface.php"],
                                  env=dict(os.environ, RINA_REGION_FILE=T + "/%s.perm" % r, RINA_NET_CONFIG=T + "/none"),
                                  stdout=subprocess.DEVNULL, stderr=open(T + "/host-%s.log" % r, "w")))
json.dump(where, open(T + "/var/app-where.json", "w"))
time.sleep(1)

class Door(BaseHTTPRequestHandler):      # ram.php's op=rams and op=login, as the node's door answers them
    def log_message(self, *a): pass
    def answer(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if q.get("op") == ["rams"]:
            self.answer({"ok": True, "rams": {k.split(".", 1)[1]: v for k, v in json.load(open(T + "/var/app-where.json")).items()}})
        else: self.answer({"ok": False, "error": "op"})
    def do_POST(self):
        q = parse_qs(urlparse(self.path).query); b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if q.get("op") == ["login"]:
            if b.get("user") == "EOCAdmin" and b.get("password") == "EOCAdmin": self.answer({"ok": True, "role": "eoc", "user": "EOCAdmin"})
            else: self.answer({"ok": False, "error": "that user and password are not this app's"})
        else: self.answer({"ok": False, "error": "op"})
dp = free(); door = ThreadingHTTPServer(("127.0.0.1", dp), Door); threading.Thread(target=door.serve_forever, daemon=True).start()
DOOR = "127.0.0.1:%d" % dp

# [APP_SERVICES_V1] rt-sim finds the general AppRam as a node does: its AppHost in /etc/hosts (FDF_HOSTS) and its port
# in the app's configuration (FDF_APPS/openeoc.json)
os.makedirs(T + "/apps", exist_ok=True)
open(T + "/hosts", "w").write("127.0.0.1 openeocHost.frognet\n")
json.dump({"rams": ["general"], "ports": {"general": int(where["openeoc.general"].split(":")[1])}}, open(T + "/apps/openeoc.json", "w"))
sim = subprocess.Popen([os.path.join(FDF, "bin", "rt-sim"), "--tick", "1"], env=dict(os.environ, FDF_VAR=T + "/var", FDF_HOSTS=T + "/hosts", FDF_APPS=T + "/apps", FDF_PY=os.path.join(FDF, "lib", "python")),
                       stdout=open(T + "/sim.log", "w"), stderr=subprocess.STDOUT)
open(OUT, "w").write(DOOR)
print("door", DOOR, "world", T, flush=True)
try:
    sim.wait()
finally:
    for p in procs: p.terminate()
