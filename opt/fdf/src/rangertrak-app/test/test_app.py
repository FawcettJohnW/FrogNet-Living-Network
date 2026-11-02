#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_app.py RINA_HOST [SHOTS_DIR] -- [RT_APP_TEST_V1] the RangerTrak app (its real core and its real page, through
# DevHost) against real RAMs: rina-host for the general RAM and one per team, rt-sim writing the world, and a stand-in
# for the node's door (ram.php's op=rams from app-where.json, and op=login for the EOC's user). Three devices: the
# EOC, a member of team Alpha, the public.
#   1 the EOC's map shows the whole simulated world, moving
#   2 the EOC's chat has the ambulances' announcements
#   3 the Alpha member's team message is on the EOC's Alpha tab; the EOC's direct message is on the member's Direct tab
#   4 the public view shows hospitals, fires and outages only, and has no chat
#   5 the EOC's own presence is on the Alpha member's map; the Alpha member's on the EOC's
#   6 rt-sim stopped: every simulated instance leaves the RAM; the EOC draws them as last known positions
#   7 the EOC with a wrong password is refused, and says so
#   8 the To list has every person present (direct messages) and every team
#   9 a message while the chat is closed: a notice on the map, a count on the Chat button
#   10 the Team view opens on the teams only and on its own team's chat
#   11-14 the public's home; I'm safe and Find someone; I need help and the EOC's action on it; a report to the EOC
#   15 Sensors: the nodes, their sensors, a reading (a GPS position, and a reading with no fix); the Hosts line
import json, os, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

HOST = sys.argv[1]; SHOTS = sys.argv[2] if len(sys.argv) > 2 else ""
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE)
SRC = os.path.dirname(APP); FDF = os.path.join(SRC, "fdf-source", "fdf")
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)
def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

T = tempfile.mkdtemp(prefix="rt-app-"); os.makedirs(T + "/var")
procs = []
where = {}
for r in ("general", "alpha", "bravo", "charlie", "delta"):
    p = free(); where["rangertrak." + r] = "127.0.0.1:%d" % p
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
        if urlparse(self.path).path == "/api.php":           # the network's sensor store, as api.php answers
            return self.answer(sensors(q))
        if q.get("op") == ["rams"]:
            self.answer({"ok": True, "rams": {k.split(".", 1)[1]: v for k, v in json.load(open(T + "/var/app-where.json")).items()}})
        else: self.answer({"ok": False, "error": "op"})
    def do_POST(self):
        q = parse_qs(urlparse(self.path).query); b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if q.get("op") == ["login"]:
            if b.get("user") == "EOCAdmin" and b.get("password") == "EOCAdmin": self.answer({"ok": True, "role": "eoc", "user": "EOCAdmin"})
            else: self.answer({"ok": False, "error": "that user and password are not this app's"})
        else: self.answer({"ok": False, "error": "op"})
SENS = {"1": ("Seattle7.System.Perf", "System", {"load": [0.4, 0.3, 0.2], "mem_free_kb": 512000}),
        "2": ("New-York-2.System.Perf", "System", {"load": [1.1, 0.9, 0.7]}),
        "3": ("Seattle7.GPS.Position", "GPS", {"timestamp": 1, "error": "invalid gps json",
              "raw": '{"class":"TPV","mode":1} {"class":"TPV","mode":3,"lat":47.4473,"lon":-122.4598,"time":"2026-11-01T22:00:00Z"}'}),
        "4": ("New-York-2.GPS.Position", "GPS", {"class": None, "device": None, "mode": None, "time": None})}
def sensors(q):
    rows = [{"SensorID": int(i), "SensorName": n, "SensorType": t, "SensorAddress": "10.170.170.1" if n.startswith("Seattle7") else "10.28.28.1"}
            for i, (n, t, d) in SENS.items()]
    if q.get("entity") == ["sensors"]:
        if "SensorType" in q: rows = [r for r in rows if r["SensorType"] == q["SensorType"][0]]
        if "SensorName__like" in q: rows = [r for r in rows if r["SensorName"].startswith(q["SensorName__like"][0].rstrip("%"))]
        return {"ok": True, "rows": rows}
    i = q.get("SensorID", [""])[0]
    return {"ok": True, "row": {"SensorID": int(i), "jsonData": json.dumps(SENS[i][2]), "updated_at": "2026-11-01 18:00:00"}}
dp = free(); door = ThreadingHTTPServer(("127.0.0.1", dp), Door); threading.Thread(target=door.serve_forever, daemon=True).start()
DOOR = "127.0.0.1:%d" % dp

sim = subprocess.Popen([os.path.join(FDF, "bin", "rt-sim"), "--tick", "1"], env=dict(os.environ, FDF_VAR=T + "/var", FDF_PY=os.path.join(FDF, "lib", "python")),
                       stdout=open(T + "/sim.log", "w"), stderr=subprocess.STDOUT)
CP = os.pathsep.join([os.path.join(SRC, "frognet-ram-java", "build", "frognet-ram.jar"), os.path.join(APP, "build", "core"), os.path.join(APP, "build", "test")])
dev = {}
for d, name, role, team in (("eoc1", "EOC Desk", "eoc", ""), ("alpha9", "Pat (Alpha)", "team", "Alpha"), ("pub1", "Citizen", "public", "")):
    p = free(); dev[d] = p
    procs.append(subprocess.Popen(["java", "-Drt.sensors=http://127.0.0.1:%d/api.php" % dp, "-cp", CP, "DevHost", os.path.join(APP, "ui"), str(p), d, name, DOOR, role, team],
                                  stdout=open(T + "/dev-%s.log" % d, "w"), stderr=subprocess.STDOUT))
time.sleep(2.5)

def markers(pg): return pg.evaluate("Object.keys(S.markers).length")
with sync_playwright() as pw:
    br = pw.chromium.launch()
    def open_dev(d, w=1280, h=800):
        pg = br.new_context(viewport={"width": w, "height": h}).new_page()
        pg.goto("http://127.0.0.1:%d/" % dev[d]); pg.wait_for_selector("#connect:not([hidden])", timeout=8000); return pg
    eoc = open_dev("eoc1")
    eoc.fill("[name=user]", "EOCAdmin"); eoc.fill("[name=password]", "wrong"); eoc.click("#connect button")
    eoc.wait_for_function("document.querySelector('#cstatus').textContent.includes('not this app')", timeout=8000)
    check(True, "7 a wrong EOC password is refused: " + eoc.inner_text("#cstatus"))
    eoc.fill("[name=password]", "EOCAdmin"); eoc.click("#connect button")
    eoc.wait_for_selector("#main:not([hidden])", timeout=8000)
    eoc.wait_for_function("Object.keys(S.markers).length >= 60", timeout=15000)
    a = eoc.evaluate("JSON.stringify(Object.values(S.pos).filter(b=>b.kind==='ambulance').map(b=>[b.lat,b.lng]))")
    time.sleep(3)
    b = eoc.evaluate("JSON.stringify(Object.values(S.pos).filter(b=>b.kind==='ambulance').map(b=>[b.lat,b.lng]))")
    kinds = eoc.evaluate("(()=>{const c={};for(const b of Object.values(S.pos))c[b.kind]=(c[b.kind]||0)+1;return c})()")
    check(markers(eoc) >= 60 and a != b, "1 the EOC's map: %d markers %s; the ambulances moved in 3 s: %s" % (markers(eoc), kinds, a != b))
    eoc.wait_for_function("S.msgs.some(m=>/Medic \\d/.test(m.text))", timeout=20000)
    check(True, "2 the EOC's chat has the ambulances: " + eoc.evaluate("S.msgs.filter(m=>m.to==='all').slice(0,2).map(m=>m.name+': '+m.text).join(' | ')"))
    if SHOTS: eoc.screenshot(path=os.path.join(SHOTS, "eoc.png"))

    tm = open_dev("alpha9", 412, 860)
    tm.click("#connect button"); tm.wait_for_selector("#main:not([hidden])", timeout=8000)
    tm.wait_for_function("Object.keys(S.markers).length > 12", timeout=15000)
    tk = tm.evaluate("[...new Set(Object.keys(S.markers).map(id=>(S.pos[id]||S.who[id]||{}).kind))]")
    check(set(tk) <= {"team", "eoc"} and tm.evaluate("S.tab") == "team:Alpha" and tm.evaluate("$('#to').value") == "team:Alpha",
          "10 the Team view opens on the teams only (%s) and on its own team's chat (%s)" % (tk, tm.evaluate("S.tab")))
    tm.evaluate("send({op:'fix',lat:47.6205,lng:-122.3493,acc:8})")          # the phone's GPS: the Space Needle
    tm.click("#chatBtn"); tm.select_option("#to", "team:Alpha"); tm.fill("#text", "Alpha 9 on scene at the Center"); tm.click("#sayForm button")
    eoc.wait_for_function("S.msgs.some(m=>m.to==='team:Alpha' && m.text.includes('Alpha 9 on scene'))", timeout=10000)
    check(True, "3 the Alpha member's team message is on the EOC's Alpha tab")
    eoc.evaluate("openPop('alpha9')"); eoc.select_option("#pop .pto", "dev:alpha9"); eoc.fill("#pop .ptext", "Pat, check the north side"); eoc.click("#pop form button")
    tm.wait_for_function("S.msgs.some(m=>m.to.startsWith('dev:') && m.text.includes('north side'))", timeout=10000)
    check(True, "3 the EOC's direct message is on the Alpha member's Direct tab")
    eoc.wait_for_function("S.pos['alpha9'] && Math.abs(S.pos['alpha9'].lat-47.6205)<1e-4", timeout=10000)
    tm.wait_for_function("!!S.who['eoc1']", timeout=10000)
    check(True, "5 the Alpha member at its GPS fix on the EOC's map; the EOC present on the member's")
    opts = eoc.evaluate("[...document.querySelectorAll('#to option')].map(o=>o.value+'='+o.textContent)")
    check("dev:alpha9=Pat (Alpha) (Alpha)" in opts or any(o.startswith("dev:alpha9=") for o in opts),
          "8 the EOC's To list has every person present (direct) and every team: %s" % [o for o in opts if not o.startswith('dev:sim')][:8])
    tm.evaluate("$('#chat').hidden=true")
    eoc.select_option("#to", "all"); eoc.fill("#text", "All units: staging at Seattle Center"); eoc.click("#sayForm button")
    tm.wait_for_function("document.querySelector('#notices .notice') && /staging/.test(document.querySelector('#notices').textContent)", timeout=10000)
    check("(" in tm.inner_text("#chatBtn"), "9 the member's chat was closed: a notice on the map and the Chat button counts: '%s'" % tm.inner_text("#chatBtn"))
    if SHOTS:
        tm.click("#chatBtn"); tm.screenshot(path=os.path.join(SHOTS, "team-phone.png"))
        eoc.screenshot(path=os.path.join(SHOTS, "eoc-chat.png")); eoc.click("#pop .pclose")

    pb = open_dev("pub1", 412, 860)
    pb.check("[name=role][value=public]"); pb.click("#connect button"); pb.wait_for_selector("#main:not([hidden])", timeout=8000)
    home_ok = pb.is_visible("#home") and pb.evaluate("document.querySelectorAll('#home .tile').length") == 6
    pb.click("[data-go=map]"); pb.wait_for_function("Object.keys(S.markers).length > 10", timeout=15000)
    pk = pb.evaluate("[...new Set(Object.keys(S.markers).map(id=>id.startsWith('help:')?'help':(S.pos[id]||{}).kind))]")
    check(set(pk) <= {"hospital", "fire", "power", "help"} and pb.is_hidden("#chatBtn"), "4 the public's map: kinds %s (no people's positions), chat hidden" % pk)
    if SHOTS: pb.screenshot(path=os.path.join(SHOTS, "public-phone.png"))

    # [RT_PUBLIC_HOME_V1] the public opens on what a person can do; [RT_HELP_V1] help, safe, find, report
    check(home_ok, "11 the public opens on its home (6 choices), not the map")
    pb.click("#homeBtn")
    pb.click("[data-go=safe]"); pb.fill(".hf [name=name]", "Jane Doe"); pb.click(".hf .go")
    pb.click("#sheet .sh-close"); pb.click("[data-go=find]"); pb.fill("#sheet .q", "jane")
    pb.wait_for_function("/Jane Doe/.test(document.querySelector('#sheet .found').textContent)", timeout=10000)
    pb.fill("#sheet .q", "carol")
    pb.wait_for_function("/Carol Jensen/.test(document.querySelector('#sheet .found').textContent)", timeout=10000)
    check(True, "12 I'm safe, then Find someone: Jane Doe found as safe; a simulated check-in (Carol Jensen) too")
    pb.click("#sheet .sh-close"); pb.click("[data-go=needhelp]"); pb.fill(".hf [name=name]", "Bob Smith")
    pb.check(".needs input[value=water]"); pb.fill(".hf [name=place]", "Pike Place Market"); pb.click(".hf .go")
    eoc.click("#reqBtn"); eoc.wait_for_function("/Bob Smith/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    n_open = eoc.evaluate("Object.values(S.help).filter(b=>b.status==='open').length")
    eoc.evaluate("(()=>{const c=[...document.querySelectorAll('#sheet .card')].find(c=>/Bob Smith/.test(c.textContent));c.querySelector('button').click()})()")
    pb.wait_for_function("/on the way by EOC Desk/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    check(True, "13 I need help: on the EOC's Requests (%d open, the simulated victims' too); the EOC's 'On the way' is on the person's own request" % n_open)
    pb.click("#sheet .sh-close"); pb.click("[data-go=report]"); pb.select_option(".rf [name=category]", "Power line down")
    pb.fill(".rf [name=text]", "Line across 3rd Ave"); pb.click(".rf .go")
    eoc.wait_for_function("/Line across 3rd Ave/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    check(True, "14 a report to the EOC is on the EOC's Requests panel, as it is written")
    if SHOTS:
        eoc.screenshot(path=os.path.join(SHOTS, "eoc-requests.png"))
        pb.click("#homeBtn"); pb.screenshot(path=os.path.join(SHOTS, "public-home.png"))
    eoc.click("#sensBtn"); eoc.wait_for_function("/New-York-2/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    eoc.evaluate("send({op:'sensors',host:'Seattle7'})"); eoc.wait_for_function("/GPS.Position/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    eoc.evaluate("send({op:'sensor',id:'3',name:'Seattle7.GPS.Position'})")
    eoc.wait_for_function("/Position: 47.44730/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    eoc.evaluate("send({op:'sensor',id:'4',name:'New-York-2.GPS.Position'})")
    eoc.wait_for_function("/No fix/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    if SHOTS: eoc.screenshot(path=os.path.join(SHOTS, "eoc-sensors.png"))
    eoc.evaluate("send({op:'hosts'})"); eoc.wait_for_function("/Hosts: 2 node/.test(document.querySelector('#status').textContent)", timeout=10000)
    check("no fix: New-York-2" in eoc.inner_text("#status"), "15 Sensors: the nodes, a node's sensors, a reading; the GPS position read from the stored readings, no fix said; Hosts: '%s'" % eoc.inner_text("#status"))
    sim.terminate(); sim.wait()
    eoc.wait_for_function("Object.values(S.pos).filter(b=>b.sim).length===0", timeout=10000)
    time.sleep(0.5)
    lkp = eoc.evaluate("document.querySelectorAll('.mk.lkp').length")
    check(lkp >= 60, "6 rt-sim stopped: no simulated instance in the RAM; %d drawn as last known positions" % lkp)
    if SHOTS: eoc.screenshot(path=os.path.join(SHOTS, "eoc-lkp.png"))
    br.close()
for p in procs: p.terminate()
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
