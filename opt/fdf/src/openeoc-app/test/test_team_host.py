#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# test_team_host.py -- [TEAM_HOST_V1] a team's RAM on its lead's phone: the lead's phone runs the team RAM (team-ram,
# the binary the app ships as libteamram.so, built here for this machine) and says so; the EOC finds it there, a member
# on the hotspot reads the lead's card in it and connects to it; chat flows; the lead going takes the team's RAM with it.
# Three AppCores (CoreDriver) against real RAMs: fdf-ram for the general RAM and team Alpha's fdf-app RAM (which the
# lead's RAM replaces for its team), team-ram for the lead's, and a stand-in for the node's door (op=rams).
#   usage: test_team_host.py FDF_RAM TEAM_RAM
import json, os, queue, socket, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOST, TEAMRAM = sys.argv[1], sys.argv[2]
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE); SRC = os.path.dirname(APP)
API = "/FawcettInnovations.RINA.ram_interface.php"
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)
def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

T = tempfile.mkdtemp(prefix="rt-teamhost-"); procs = []
def ram(binary, r):
    p = free(); procs.append(subprocess.Popen([binary, "--listen", "127.0.0.1:%d" % p, "--api", API],
        env=dict(os.environ, RINA_REGION_FILE=T + "/%s.perm" % r, RINA_NET_CONFIG=T + "/none"), stdout=subprocess.DEVNULL, stderr=open(T + "/%s.log" % r, "w")))
    return "127.0.0.1:%d" % p
GEN, ALPHA = ram(HOST, "general"), ram(HOST, "alpha")
LEADPORT = free(); procs.append(subprocess.Popen([TEAMRAM, "--listen", "127.0.0.1:%d" % LEADPORT], stdout=subprocess.DEVNULL, stderr=open(T + "/lead-ram.log", "w")))
time.sleep(1)
def post(hp, body): return json.load(urllib.request.urlopen(urllib.request.Request("http://%s%s?op=write" % (hp, API), json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=5))
def get(hp, q): return json.load(urllib.request.urlopen("http://%s%s?%s" % (hp, API, q), timeout=5))
post(GEN, {"service": "rt-mission", "variable": "settings", "instance": "m1", "bag": {"name": "Exercise", "at": 1}})

class Door(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def answer(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if q.get("op") == ["rams"]: self.answer({"ok": True, "rams": {"general": GEN, "alpha": ALPHA}})
        else: self.answer({"ok": False, "error": "op"})
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.answer({"ok": True, "role": "eoc", "user": b.get("user")} if b.get("password") == "pw" else {"ok": False, "error": "no"})
dp = free(); srv = ThreadingHTTPServer(("127.0.0.1", dp), Door); threading.Thread(target=srv.serve_forever, daemon=True).start()
DOOR = "127.0.0.1:%d" % dp

CP = os.pathsep.join([os.path.join(SRC, "frognet-ram-java", "build", "frognet-ram.jar"), os.path.join(APP, "build", "core"), os.path.join(APP, "build", "test")])
class Dev:
    def __init__(self, name):
        self.name = name; self.q = queue.Queue(); self.lines = []
        self.p = subprocess.Popen(["java", "-cp", CP, "CoreDriver"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        procs.append(self.p)
        threading.Thread(target=self.read, daemon=True).start()
    def read(self):
        for l in self.p.stdout:
            try: o = json.loads(l)
            except ValueError: continue
            self.lines.append(o); self.q.put(o)
    def send(self, **o): self.p.stdin.write(json.dumps(o) + "\n"); self.p.stdin.flush()
    def wait(self, pred, t=20):
        end = time.time() + t
        for o in list(self.lines):
            if pred(o): return o
        while time.time() < end:
            try: o = self.q.get(timeout=max(0.05, end - time.time()))
            except queue.Empty: break
            if pred(o): return o
        return None
    def last(self, ev): return next((o for o in reversed(self.lines) if o.get("ev") == ev), None)

eoc, lead = Dev("eoc"), Dev("lead")
eoc.send(op="connect", door=DOOR, dev="deoc", name="EOC", role="eoc", user="boss", password="pw")
lead.send(op="connect", door=DOOR, dev="dlead", name="Lead", role="team", team="Alpha")
check(eoc.wait(lambda o: o.get("ev") == "connected") is not None and lead.wait(lambda o: o.get("ev") == "connected") is not None, "0 the EOC and the lead connect through the door (Alpha's RAM: fdf-app's, %s)" % ALPHA)
# 1 the lead hosts: its phone's RAM (team-ram) is Alpha's now
LEADADDR = "127.0.0.1:%d" % LEADPORT
lead.send(op="teamHost", port=LEADPORT, addr="127.0.0.1", card={"door": DOOR, "join": "fdfjoin1.test-line", "via": "lan"})
c1 = lead.wait(lambda o: o.get("ev") == "connected" and (o.get("rams") or {}).get("alpha") == "127.0.0.1:%d" % LEADPORT)
check(c1 is not None, "1 the lead's own Alpha RAM is its phone's: %s" % ((c1 or {}).get("rams")))
hc = None
for i in range(40):
    hc = [c for c in get(GEN, "op=read&service=rt-team-host").get("cells", []) if c["instance"] == "alpha"]
    if hc: break
    time.sleep(0.25)
check(bool(hc) and hc[0]["bag"].get("ram") == LEADADDR, "1 the lead's cell on the general RAM says where: %s" % (hc[0]["bag"] if hc else "none"))
# 2 the EOC follows Alpha to the lead's phone
c2 = eoc.wait(lambda o: o.get("ev") == "connected" and (o.get("rams") or {}).get("alpha") == LEADADDR)
check(c2 is not None, "2 the EOC's Alpha RAM is the lead's phone now: %s" % ((c2 or eoc.last("connected") or {}).get("rams")))
# 3 a member on the hotspot reads the lead's card at its gateway, then connects with the team RAM there
card = None
for i in range(40):
    cs = [c for c in get(LEADADDR, "op=read&service=rt-team&variable=card").get("cells", []) if c["instance"] == "alpha"]
    if cs: card = cs[0]["bag"]; break
    time.sleep(0.25)
check(card is not None and card.get("team") == "Alpha" and card.get("door") == DOOR and card.get("join") == "fdfjoin1.test-line", "3 the lead's card in its RAM: %s" % card)
mem = Dev("member")
mem.send(op="connect", door=card["door"] if card else DOOR, dev="dmem", name="Member", role="team", team="Alpha", teamRam=LEADADDR)
c3 = mem.wait(lambda o: o.get("ev") == "connected")
check(c3 is not None and (c3.get("rams") or {}).get("alpha") == LEADADDR, "3 the member's Alpha RAM is the lead's phone: %s" % ((c3 or {}).get("rams")))
tv = mem.wait(lambda o: o.get("ev") == "teamVia")
check(tv is not None and tv.get("via") == "lan" and tv.get("join") == "fdfjoin1.test-line", "3 the member is told its lead's way to the node (its phone's own side acts on it): %s" % tv)
lead.send(op="teamHost", port=LEADPORT, addr="127.0.0.1", card={"door": DOOR, "join": "fdfjoin1.test-line", "via": "tunnel"})
tv2 = mem.wait(lambda o: o.get("ev") == "teamVia" and o.get("via") == "tunnel")
check(tv2 is not None, "3 the lead on its tunnel: the member is told (it joins FrogNet itself)")
# 4 chat to the team: in the lead's RAM, seen by the lead and the EOC
time.sleep(1.5)
mem.send(op="say", to="team:Alpha", text="on scene at the ferry dock")
got_lead = lead.wait(lambda o: o.get("ev") == "msg" and o.get("text") == "on scene at the ferry dock")
got_eoc = eoc.wait(lambda o: o.get("ev") == "msg" and o.get("text") == "on scene at the ferry dock")
inram = any(c["bag"].get("text") == "on scene at the ferry dock" for c in get(LEADADDR, "op=read&service=rt-team").get("cells", []))
check(got_lead is not None and got_eoc is not None and inram, "4 a member's team message is in the lead's RAM and reaches the lead (%s) and the EOC (%s)" % (got_lead is not None, got_eoc is not None))
noalpha = not any(c["bag"].get("text") == "on scene at the ferry dock" for c in get(ALPHA, "op=read&service=rt-team").get("cells", []))
check(noalpha, "4 nothing went to fdf-app's Alpha RAM")
# 6 [TEAM_SPLIT_V1] the lead's phone joins another FrogNet (its own general RAM, its own node): the lead asks that node,
# its card says so, and its member follows it; the team's RAM stays on the lead's phone
GEN2 = ram(HOST, "general2"); time.sleep(0.5)
post(GEN2, {"service": "rt-mission", "variable": "settings", "instance": "m2", "bag": {"name": "Other site", "at": 2}})
class Door2(Door):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if q.get("op") == ["rams"]: self.answer({"ok": True, "rams": {"general": GEN2}})
        else: self.answer({"ok": False, "error": "op"})
dp2 = free(); srv2 = ThreadingHTTPServer(("127.0.0.1", dp2), Door2); threading.Thread(target=srv2.serve_forever, daemon=True).start()
DOOR2 = "127.0.0.1:%d" % dp2
lead.send(op="door", door=DOOR2)
c6 = lead.wait(lambda o: o.get("ev") == "connected" and (o.get("rams") or {}).get("general") == GEN2)
check(c6 is not None and (c6.get("rams") or {}).get("alpha") == LEADADDR, "6 the lead on the other network: %s" % ((c6 or lead.last("connected") or {}).get("rams")))
m6 = mem.wait(lambda o: o.get("ev") == "connected" and (o.get("rams") or {}).get("general") == GEN2, 30)
check(m6 is not None and (m6.get("rams") or {}).get("alpha") == LEADADDR, "6 the member followed its lead's card to the other network: %s" % ((m6 or mem.last("connected") or {}).get("rams")))
# 5 the lead goes: its cell goes with its session; the EOC's Alpha is fdf-app's again
lead.p.kill()
c5 = eoc.wait(lambda o: o.get("ev") == "connected" and (o.get("rams") or {}).get("alpha") == ALPHA, 30)
check(c5 is not None, "5 the lead gone: its cell is gone and the EOC's Alpha is where the door says again: %s" % ((c5 or eoc.last("connected") or {}).get("rams")))

for p in procs:
    try: p.kill()
    except Exception: pass
print("RESULT " + ("PASS" if not FAILS else "FAIL: %d" % len(FAILS)))
sys.exit(1 if FAILS else 0)
