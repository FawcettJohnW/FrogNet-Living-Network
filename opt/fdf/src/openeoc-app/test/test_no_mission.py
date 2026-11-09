#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_no_mission.py FDF_RAM -- [RT_STANDING_MISSION_V1] a network where no EOC has started a mission (and no rt-sim
# writes one), as John's was on 2026-11-05: two real cores (CoreDriver), a team member and a second one, on a real RAM
# (fdf-ram) behind a door:
#   1 the first core writes the standing mission and says so; both follow it
#   2 each one's position is in the positions RAM and each sees the other (presence and positions)
#   3 a message to the team from one comes to the other
#   4 a mission an EOC starts afterwards is the mission on both (newer than the standing one)
#   6 the admin's who is on now: Pat and Lee in the mission now, team Alpha
#   7 the sensor store where the door says it is (op=rams: sensors), no .frognet name needed
#   5 the EOC's login: a user whose role is in the EOC category (eochq) is the EOC; one in Ambulance is told where to put it
import json, os, queue, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
HOST = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE); SRC = os.path.dirname(APP)
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)
def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
T = tempfile.mkdtemp(prefix="rt-nomis-"); procs = []; where = {}
for r in ("general", "alpha"):
    p = free(); where[r] = "127.0.0.1:%d" % p
    procs.append(subprocess.Popen([HOST, "--listen", where[r], "--api", "/FawcettInnovations.RINA.ram_interface.php"],
                                  env=dict(os.environ, RINA_REGION_FILE=T + "/%s.perm" % r, RINA_NET_CONFIG=T + "/none"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
time.sleep(1)
class Door(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def answer(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if urlparse(self.path).path == "/api.php":            # [SENSORS_BY_THE_DOOR_V1] the sensor store, at the address the door says
            self.answer({"ok": True, "rows": [{"SensorID": 1, "SensorName": "Seattle7.GPS.Position", "SensorAddress": "10.188.188.7", "SensorType": "GPS"}] if q.get("SensorType") == ["GPS"] else []})
        elif q.get("entity") == ["sensor_data"]: self.answer({"ok": True, "row": {"jsonData": {"lat": 47.4528, "lon": -122.3325}}})
        elif q.get("op") == ["rams"]: self.answer({"ok": True, "rams": where, "node": "Seattle7", "sensors": "http://%s/api.php" % DOOR})
        elif q.get("op") == ["roles"]: self.answer({"ok": True, "login": False, "roles": {"alpha": {"default": True, "ram": True, "category": "Teams"},
                                                     "eochq": {"default": False, "ram": False, "category": "EOC"}, "medic1": {"default": False, "ram": True, "category": "Ambulance"}}})
        else: self.answer({"ok": False, "error": "op"})
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        role = {"EOCLead": "eochq", "Medic": "medic1"}.get(b.get("user"))
        self.answer({"ok": True, "role": role, "user": b.get("user")} if role and b.get("password") == "pw" else {"ok": False, "error": "that user and password are not this app's"})
dp = free(); srv = ThreadingHTTPServer(("127.0.0.1", dp), Door); threading.Thread(target=srv.serve_forever, daemon=True).start()
DOOR = "127.0.0.1:%d" % dp
CP = os.pathsep.join([os.path.join(SRC, "frognet-ram-java", "build", "frognet-ram.jar"), os.path.join(APP, "build", "core"), os.path.join(APP, "build", "test")])
class Dev:
    def __init__(self, sensors=True):
        self.q = queue.Queue(); self.lines = []
        self.p = subprocess.Popen(["java"] + (["-Drt.sensors=http://%s/api.php" % DOOR] if sensors else []) + ["-cp", CP, "CoreDriver"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        procs.append(self.p); threading.Thread(target=self.read, daemon=True).start()
    def read(self):
        for l in self.p.stdout:
            try: o = json.loads(l)
            except ValueError: continue
            self.lines.append(o); self.q.put(o)
    def send(self, **o): self.p.stdin.write(json.dumps(o) + "\n"); self.p.stdin.flush()
    def wait(self, pred, t=20):
        end = time.time() + t
        while time.time() < end:
            for o in list(self.lines):
                if pred(o): return o
            time.sleep(0.1)
        return None
try:
    a = Dev(); a.send(op="connect", door=DOOR, dev="pa", name="Pat", role="team", team="Alpha")
    said = a.wait(lambda o: "Standing operations" in str(o.get("text", "")))
    ma = a.wait(lambda o: o.get("ev") == "mission" and o.get("id") == "standing")
    b = Dev(); b.send(op="connect", door=DOOR, dev="pb", name="Lee", role="team", team="Alpha")
    mb = b.wait(lambda o: o.get("ev") == "mission" and o.get("id") == "standing")
    check(bool(said) and bool(ma) and bool(mb), "1 no mission on the network: %r; Pat and Lee follow the standing mission (%s, %s)" % ((said or {}).get("text"), bool(ma), bool(mb)))
    a.send(op="fix", lat=47.45, lng=-122.46, acc=5); b.send(op="fix", lat=47.46, lng=-122.47, acc=5)
    sees = lambda d, other: d.wait(lambda o: o.get("ev") == "world" and other in (o.get("pos") or {}) and other in (o.get("who") or {}))
    wa, wb = sees(a, "pb"), sees(b, "pa")
    a.send(op="diag"); dg = a.wait(lambda o: o.get("ev") == "diag")
    check(bool(wa) and bool(wb) and dg and dg["me"].get("inPositions") and dg["me"].get("inPresence"),
          "2 Pat sees Lee and Lee sees Pat (presence and positions); Pat's position is in the positions RAM: %s" % (dg or {}).get("me", {}).get("inPositions"))
    a.send(op="say", to="team:Alpha", text="Alpha on scene")
    got = b.wait(lambda o: o.get("ev") == "msg" and o.get("text") == "Alpha on scene")
    check(bool(got), "3 Pat's message to team Alpha comes to Lee: %s" % (got or {}).get("to"))
    a.send(op="missionSet", bag={"name": "Vashon windstorm", "event": "Vashon windstorm", "opPeriod": 1})
    na = a.wait(lambda o: o.get("ev") == "mission" and str(o.get("id", "")).startswith("vashon-windstorm"))
    nb = b.wait(lambda o: o.get("ev") == "mission" and str(o.get("id", "")).startswith("vashon-windstorm"))
    check(bool(na) and bool(nb), "4 a mission started afterwards is the mission on both: %s" % (na or {}).get("id"))
    # 7 [SENSORS_BY_THE_DOOR_V1] a core told nothing of the sensor store (no rt.sensors): the door says where it is
    t = Dev(sensors=False); t.send(op="connect", door=DOOR, dev="pt", name="Tess", role="team", team="Alpha")
    t.wait(lambda o: o.get("ev") == "connected"); t.send(op="hosts")
    h = t.wait(lambda o: o.get("ev") == "hosts" or (o.get("ev") == "error" and "GPS" in o.get("text", "")))
    check((h or {}).get("ev") == "hosts", "7 the sensor store where the door says: %s" % ((h or {}).get("list") if (h or {}).get("ev") == "hosts" else (h or {}).get("text")))
    # 6 [ADMIN_ON_NOW_V1] the admin's who-is-on-now: Pat and Lee, in the mission now (4's), team Alpha
    ad = subprocess.run(["java", "-cp", CP, "AdminOnNow", DOOR, "openeoc", "8000"], capture_output=True, text=True, timeout=60).stdout
    nows = [json.loads(l) for l in ad.splitlines() if l.startswith("{") and '"onNow"' in l]
    last = nows[-1] if nows else {}
    check(last.get("mission") == (na or {}).get("id") and {"pa", "pb"} <= set(last.get("who", {})) and last["who"]["pa"].get("team") == "Alpha",
          "6 the admin's on now: mission %s, %s" % (last.get("mission"), {d: (w.get("name"), w.get("team")) for d, w in last.get("who", {}).items()}))
    # 5 [RT_CATEGORY_IS_THE_VIEW_V1] the EOC's user is one whose role is in the EOC category (eochq), not only one named eoc
    e = Dev(); e.send(op="connect", door=DOOR, dev="pe", name="EOC Desk", role="eoc", user="EOCLead", password="pw")
    ce = e.wait(lambda o: o.get("ev") in ("connected", "error"))
    m = Dev(); m.send(op="connect", door=DOOR, dev="pm", name="Medic", role="eoc", user="Medic", password="pw")
    cm = m.wait(lambda o: o.get("ev") in ("connected", "error"))
    check((ce or {}).get("ev") == "connected" and (cm or {}).get("ev") == "error" and "Ambulance" in cm.get("text", "") and "EOC category" in cm.get("text", ""),
          "5 EOCLead (role eochq, category EOC) logs in as the EOC: %s; Medic (medic1, Ambulance) does not: %r" % ((ce or {}).get("ev"), (cm or {}).get("text")))
finally:
    for p in procs: p.terminate()
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
