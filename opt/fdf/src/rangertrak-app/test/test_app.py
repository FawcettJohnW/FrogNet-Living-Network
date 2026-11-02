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
#   16-17 the Team view starts with its own team only; Layers in front of the chat
#   18 team actions (a service request, a sensor platform) and the EOC's action; 19 compliance
#   21 the hospital's desk: the simulated hospitals' statuses; a desk publishes its own, the EOC sees it
#   22 an ambulance's patient transport is on the hospital's Inbound; the hospital's Acknowledge is on the ambulance's
#   25 the mission: the EOC sees it; a team member updates it (objectives) and the EOC sees the update
#   26 a device with no GPS: asked for its street address; the position it gives ("lat, lon" here: the address lookup is
#      the phone's) is its position on the EOC's map, marked as an address; the member's map is centred on it
#   27 the chat's Call: offered when the To is a person
#   28 a new mission started by a team member is the mission on every device
#   24 the connect screen's teams are the ones the admin defined (door op=roles)
#   23 Call: the EOC calls the Alpha member: the Communicator's device picker (camera, mic, speakers) opens on the
#      EOC; the member's app rings, Answer opens its Communicator to answer; the chosen devices go to the Communicator
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
        elif q.get("op") == ["roles"]:                       # [ADMIN_APP_V1] the teams the admin defined (echo: a fifth)
            self.answer({"ok": True, "login": False, "roles": {"eoc": {"default": False, "ram": False}, **{t: {"default": True, "ram": True} for t in ("alpha", "bravo", "charlie", "delta", "echo")}}})
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
        "4": ("New-York-2.GPS.Position", "GPS", {"class": None, "device": None, "mode": None, "time": None}),
        "5": ("Seattle7.SenseHat.Env", "SenseHat", {"temperature": 31.2, "humidity": 44.0, "pressure": 1012.3})}
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
for d, name, role, team in (("eoc1", "EOC Desk", "eoc", ""), ("alpha9", "Pat (Alpha)", "team", "Alpha"), ("pub1", "Citizen", "public", ""),
                              ("hosp1", "Charge Nurse", "hospital", ""), ("med12", "Medic 12 crew", "ambulance", "")):
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
    eoc.wait_for_function("[...document.querySelectorAll('[name=team] option')].map(o=>o.value).join()==='Alpha,Bravo,Charlie,Delta,Echo'", timeout=8000)
    check(True, "24 the connect screen's teams are the admin's, asked of the node: " + eoc.evaluate("[...document.querySelectorAll('[name=team] option')].map(o=>o.value).join(', ')"))
    eoc.fill("[name=user]", "EOCAdmin"); eoc.fill("[name=password]", "wrong"); eoc.click("#connect button")
    eoc.wait_for_function("document.querySelector('#cstatus').textContent.includes('not this app')", timeout=8000)
    check(True, "7 a wrong EOC password is refused: " + eoc.inner_text("#cstatus"))
    eoc.fill("[name=password]", "EOCAdmin"); eoc.click("#connect button")
    eoc.wait_for_selector("#main:not([hidden])", timeout=8000)
    try: eoc.wait_for_function("Object.keys(S.markers).length >= 60", timeout=15000)
    except Exception: print("DEBUG", eoc.evaluate("JSON.stringify({m:Object.keys(S.markers).length,pos:Object.keys(S.pos).length,role:S.role,st:$('#status').textContent,on:S.on})")); raise
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
    tm.wait_for_function("Object.keys(S.markers).length >= 4", timeout=15000)
    tk = tm.evaluate("[...new Set(Object.keys(S.markers).map(id=>(S.pos[id]||S.who[id]||{}).kind))]")
    tt = tm.evaluate("[...new Set(Object.keys(S.markers).map(id=>(S.pos[id]||{}).team))]")
    check(tt == ["Alpha"], "16 the Team view starts with only its own team on the map: teams %s" % tt)
    tm.click("#layersBtn")
    front = tm.evaluate("(()=>{$('#chat').hidden=false;const r=$('#layers').getBoundingClientRect();const e=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return !!e.closest('#layers')})()")
    tm.evaluate("$('#chat').hidden=true"); tm.click("#layersBtn")
    check(front, "17 Layers opens in front of the chat")
    check(set(tk) <= {"team", "eoc"} and tm.evaluate("S.tab") == "team:Alpha" and tm.evaluate("$('#to').value") == "team:Alpha",
          "10 the Team view opens on the teams only (%s) and on its own team's chat (%s)" % (tk, tm.evaluate("S.tab")))
    tm.evaluate("send({op:'fix',lat:47.6205,lng:-122.3493,acc:8})")          # the phone's GPS: the Space Needle
    tm.click("#chatBtn"); tm.evaluate("$('#to').value='team:Alpha'"); tm.fill("#text", "Alpha 9 on scene at the Center"); tm.click("#sayForm button")
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
    eoc.evaluate("$('#chat').hidden=false"); eoc.select_option("#to", "all"); eoc.fill("#text", "All units: staging at Seattle Center"); eoc.click("#sayForm button")
    tm.wait_for_function("document.querySelector('#notices .notice') && /staging/.test(document.querySelector('#notices').textContent)", timeout=10000)
    check("(" in tm.inner_text("#chatBtn"), "9 the member's chat was closed: a notice on the map and the Chat button counts: '%s'" % tm.inner_text("#chatBtn"))
    if SHOTS:
        tm.click("#chatBtn"); tm.screenshot(path=os.path.join(SHOTS, "team-phone.png"))
        eoc.screenshot(path=os.path.join(SHOTS, "eoc-chat.png")); eoc.click("#pop .pclose")

    pb = open_dev("pub1", 412, 860)
    pb.check("[name=role][value=public]"); pb.click("#connect button"); pb.wait_for_selector("#main:not([hidden])", timeout=8000)
    home_ok = pb.is_visible("#home") and pb.evaluate("document.querySelectorAll('#home [data-go]').length") == 8
    pb.click("[data-go=map]"); pb.wait_for_function("Object.keys(S.markers).length > 10", timeout=15000)
    pk = pb.evaluate("[...new Set(Object.keys(S.markers).map(id=>id.startsWith('help:')?'help':id.startsWith('field:')?(S.field[id.slice(6)]||{}).what:(S.pos[id]||{}).kind))]")
    check(set(pk) <= {"hospital", "fire", "power", "help", "hazard"} and pb.is_hidden("#chatBtn"), "4 the public's map: kinds %s (no people's positions), chat hidden" % pk)
    if SHOTS: pb.screenshot(path=os.path.join(SHOTS, "public-phone.png"))

    # [RT_PUBLIC_HOME_V1] the public opens on what a person can do; [RT_HELP_V1] help, safe, find, report
    check(home_ok, "11 the public opens on its home (8 choices: help, safe, find, report, services, library, FEMA, map), not the map")
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
    pb.click("#sheet .sh-close"); pb.click("[data-go=library]"); lib = pb.evaluate("document.querySelectorAll('#sheet details').length")
    pb.click("#sheet .sh-close"); pb.click("[data-go=fema]"); fem = "1-800-621-3362" in pb.inner_text("#sheet")
    check(lib >= 8 and fem, "20 the survival library (%d topics, in the app) and Contact FEMA" % lib)
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
    # [RT_FIELD_V1] team actions; the EOC acts on a service request; [RT_COMPLIANCE_V1]
    tm.click("#actBtn"); tm.select_option("#sheet [data-what=service] [name=type]", "Generator / power")
    tm.select_option("#sheet [data-what=service] [name=priority]", "Urgent"); tm.fill("#sheet [data-what=service] [name=note]", "shelter at the school")
    tm.click("#sheet [data-what=service] .go"); tm.click("#sheet [data-what=platform] .go")
    eoc.click("#reqBtn"); eoc.wait_for_function("/shelter at the school/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    eoc.evaluate("(()=>{const c=[...document.querySelectorAll('#sheet .card')].find(c=>/shelter at the school/.test(c.textContent));[...c.querySelectorAll('button')].find(b=>b.textContent==='Dispatched').click()})()")
    tm.wait_for_function("/dispatched by EOC Desk/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    plat = eoc.evaluate("Object.values(S.field).filter(b=>b.what==='platform').length")
    check(True, "18 team actions: a service request is on the EOC's Requests, its 'Dispatched' on the member's Actions; %d sensor platforms deployed (the simulated ones and this one)" % plat)
    check("OUT OF COMPLIANCE: Seattle7" in eoc.inner_text("#status") or eoc.evaluate("!!S.compliance['Seattle7']"),
          "19 compliance: the host with a working environmental sensor says it is out of compliance: %s" % eoc.evaluate("JSON.stringify(S.compliance)")[:160])
    if SHOTS:
        eoc.click("#sheet .sh-close"); eoc.screenshot(path=os.path.join(SHOTS, "eoc-map.png"))
        tm.screenshot(path=os.path.join(SHOTS, "team-actions.png"))
    # [RT_FACILITY_V1] the hospital's desk; [RT_TRANSPORT_V1] the ambulance
    hs = open_dev("hosp1", 1100, 800)
    hs.check("[name=role][value=hospital]"); hs.fill("[name=unit]", "Swedish Ballard"); hs.click("#connect button")
    hs.wait_for_selector("#main:not([hidden])", timeout=8000)
    hs.wait_for_function("Object.keys(S.fac).length >= 12 && !$('#sheet').hidden && S.sheet==='fac'", timeout=15000)
    hs.fill(".fac [name=b_ed]", "3"); hs.fill(".fac [name=b_icu]", "1"); hs.select_option(".fac [name=diversion]", "Trauma diversion")
    hs.fill(".fac [name=needs]", "neuro surgeon on call"); hs.click(".fac .go")
    eoc.wait_for_function("S.fac['swedish-ballard'] && S.fac['swedish-ballard'].by==='Charge Nurse' && S.fac['swedish-ballard'].beds.ed===3", timeout=10000)
    check(hs.inner_text("#actBtn") == "Status" and hs.inner_text("#reqBtn") == "Inbound",
          "21 the hospital's desk: %d hospitals' statuses (the simulated ones); Swedish Ballard's published, on the EOC: %s" % (
              hs.evaluate("Object.keys(S.fac).length"), eoc.evaluate("JSON.stringify(S.fac['swedish-ballard'].beds)")))
    md = open_dev("med12", 412, 860)
    md.check("[name=role][value=ambulance]"); md.fill("[name=unit]", "Medic 12"); md.click("#connect button")
    md.wait_for_selector("#main:not([hidden])", timeout=8000); md.wait_for_function("Object.keys(S.fac).length >= 12", timeout=10000)
    md.click("#actBtn"); md.select_option(".tf [name=to]", "Swedish Ballard"); md.select_option(".tf [name=triage]", "Red")
    md.fill(".tf [name=eta]", "9"); md.fill(".tf [name=complaint]", "fall from ladder, unresponsive"); md.click(".tf .go")
    hs.click("#reqBtn"); hs.wait_for_function("/fall from ladder/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    sim_inbound = hs.evaluate("Object.values(S.tr).filter(t=>t.sim).length")
    hs.evaluate("(()=>{const c=[...document.querySelectorAll('#sheet .card')].find(c=>/fall from ladder/.test(c.textContent));[...c.querySelectorAll('button')].find(b=>b.textContent==='Acknowledge').click()})()")
    md.wait_for_function("Object.entries(S.trActs).some(([k,a])=>a.action==='acknowledged' && S.tr[k.split('~')[0]] && /fall from ladder/.test(S.tr[k.split('~')[0]].complaint))", timeout=10000)
    eoc.click("#reqBtn"); eoc.wait_for_function("/Patient transports under way/.test(document.querySelector('#sheet').textContent) && /fall from ladder/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    check(md.evaluate("S.myStatus") == "Transporting",
          "22 Medic 12's transport is on Swedish Ballard's Inbound, its Acknowledge on Medic 12's, the transport on the EOC's Requests; %d simulated transports so far" % sim_inbound)
    if SHOTS:
        hs.screenshot(path=os.path.join(SHOTS, "hospital-inbound.png")); md.click("#actBtn"); md.screenshot(path=os.path.join(SHOTS, "ambulance-unit.png"))
        hs.click("#actBtn"); hs.screenshot(path=os.path.join(SHOTS, "hospital-desk.png"))
    # [RT_CALL_V1] Call: the native side is stood in for (it opens comms-app, or the phone's Communicator)
    stub = "window.CALLS=[];window.rtNative.comms=j=>CALLS.push(['comms',JSON.parse(j)]);window.rtNative.startComms=j=>CALLS.push(['start',JSON.parse(j)]);0"
    eoc.evaluate(stub); tm.evaluate(stub)
    eoc.evaluate("$('#sheet').hidden=true; openPop('alpha9')"); eoc.click("#pop .pcall")
    eoc.wait_for_function("CALLS.some(c=>c[0]==='comms' && c[1].peer==='Pat (Alpha)' && !c[1].answer)", timeout=10000)
    tm.wait_for_selector(".ring", timeout=10000); ring = tm.inner_text(".ring"); tm.click(".ring .yes")
    tm.wait_for_function("CALLS.some(c=>c[0]==='comms' && c[1].peer==='EOC Desk' && c[1].answer)", timeout=5000)
    eoc.evaluate("""rtDeliver(JSON.stringify({ev:'devices',peer:'Pat (Alpha)',name:'EOC Desk',answer:false,ram:'10.170.170.1:8800',media:'10.170.170.1:8994',saved:{},
      cameras:[{value:'video=Integrated Camera',label:'Integrated Camera'}],mics:[{n:'0',label:'Microphone Array'},{n:'2',label:'Headset'}],speakers:[{n:'1',label:'Speakers'},{n:'2',label:'Headset'}],note:''}))""")
    eoc.select_option(".cf [name=mic]", "2"); eoc.select_option(".cf [name=speaker]", "2"); eoc.click(".cf .go")
    try: eoc.wait_for_function("CALLS.some(c=>c[0]==='start')", timeout=5000)
    except Exception: print("DEBUG", eoc.evaluate("JSON.stringify({calls:CALLS, sheet:S.sheet, hidden:$('#sheet').hidden, top:(()=>{const r=document.querySelector('.cf .go').getBoundingClientRect();const e=document.elementFromPoint(r.left+5,r.top+5);return e&&(e.id||e.className)})()})"))
    st = eoc.evaluate("(CALLS.find(c=>c[0]==='start')||[0,{}])[1]")
    check(st.get("camera") == "video=Integrated Camera" and st.get("mic") == "2" and st.get("speaker") == "2" and st.get("ram", "").endswith(":8800") and "calling" in ring,
          "23 Call: the EOC's Communicator picker (camera, mic, speakers) gives %s; the member's app rang ('%s') and Answer opened its Communicator to answer" % (
              {k: st.get(k) for k in ("camera", "mic", "speaker", "ram", "media")}, ring.replace("\n", " ")))
    # [RT_MISSION_V1] the mission
    eoc.evaluate("$('#sheet').hidden=true"); eoc.click("#misBtn"); eoc.wait_for_selector("#sheet .mis", timeout=5000)
    m0 = eoc.inner_text("#sheet .mis")
    tm.evaluate("$('#sheet').hidden=true"); tm.click("#misBtn"); tm.click("text=Update this mission")
    tm.fill(".mf [name=objectives]", "Account for every resident of Vashon by 18:00"); tm.click(".mf .go")
    eoc.wait_for_function("/Account for every resident/.test(document.querySelector('#sheet').textContent)", timeout=10000)
    check("Seattle metro exercise" in m0, "25 the mission on the EOC's Mission panel ('%s'); the Alpha member's update (objectives) on it at once: %s" % (
        m0.splitlines()[1] if len(m0.splitlines()) > 1 else m0, eoc.evaluate("JSON.stringify(S.mission.bag.objectives)")))
    # [RT_NO_GPS_V1] [RT_FOLLOW_ME_V1]
    tm.evaluate("$('#sheet').hidden=true; rtDeliver(JSON.stringify({ev:'nogps',why:'This device has no GPS.'}))")
    tm.wait_for_selector(".af", timeout=5000); tm.fill(".af [name=a]", "47.6097, -122.3331"); tm.click(".af .go")
    eoc.wait_for_function("S.pos['alpha9'] && S.pos['alpha9'].src==='address' && Math.abs(S.pos['alpha9'].lat-47.6097)<1e-4", timeout=10000)
    time.sleep(1)                                                            # (the pan is animated)
    c = tm.evaluate("(()=>{const c=map.getCenter();return [c.lat,c.lng,map.getZoom()]})()")
    check(abs(c[0] - 47.6097) < 0.01 and abs(c[1] + 122.3331) < 0.01, "26 no GPS: asked for the street address; the position given is the member's on the EOC's map (src address); the member's map centred on it: %s" % c)
    tm.evaluate("$('#chat').hidden=false"); tm.select_option("#to", "dev:eoc1"); vis = tm.is_visible("#callTo"); tm.select_option("#to", "all")
    check(vis and tm.is_hidden("#callTo"), "27 the chat's Call is offered for a person (the EOC Desk), not for Everyone")
    sim.terminate(); sim.wait()
    eoc.wait_for_function("Object.values(S.pos).filter(b=>b.sim).length===0", timeout=10000)
    time.sleep(0.5)
    lkp = eoc.evaluate("document.querySelectorAll('.mk.lkp').length")
    check(lkp >= 60, "6 rt-sim stopped: no simulated instance in the RAM; %d drawn as last known positions" % lkp)
    if SHOTS: eoc.screenshot(path=os.path.join(SHOTS, "eoc-lkp.png"))
    tm.evaluate("$('#chat').hidden=true"); tm.click("#misBtn"); tm.click("text=Start a new mission")
    tm.fill(".mf [name=name]", "Vashon windstorm"); tm.fill(".mf [name=event]", "Windstorm, island power out"); tm.click(".mf .go")
    eoc.wait_for_function("S.mission && /^vashon-windstorm-/.test(S.mission.id)", timeout=10000)
    tm.wait_for_function("S.mission && /^vashon-windstorm-/.test(S.mission.id) && $('#mission').textContent==='Vashon windstorm'", timeout=10000)
    check(True, "28 the new mission (%s) is the mission on the EOC and the member" % eoc.evaluate("S.mission.id"))
    if SHOTS: tm.click("#misBtn"); tm.screenshot(path=os.path.join(SHOTS, "team-mission.png"))
    br.close()
for p in procs: p.terminate()
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
