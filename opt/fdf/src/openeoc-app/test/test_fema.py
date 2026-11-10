#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_fema.py FDF_RAM [SHOTS_DIR] -- [FEMA_EOC_V1] the FEMA EOC function and EOCAdmin in the real apps (their real core
# and page, through DevHost) against real RAMs (fdf-ram: the general RAM and the teams'), with a stand-in for the node's
# door (op=rams, op=roles, op=login). The devices, each with the layers its app is built with:
#   the EOC (eoc), Team Alpha's member (team), a hospital (hospital), an ambulance (ambulance), EOCAdmin (eocadmin, locked)
#   1 the organizations on the network, by the app they joined with: Teams (Alpha), Hospitals, Ambulances
#   2 the hospital publishes Trauma diversion: Health and Medical is Stabilizing from the reports on the EOC's lifelines
#   3 the EOC's call on Energy (Unstable): every device's lifelines say so
#   4 the incident (ICS or ICS-like, op period 1); staffing: EOC Director filled; ICS-202 objective filed
#   5 the EOC assigns Alpha (ICS-204) from Organizations: Alpha's app has it under Assignments and a notice; Alpha
#     acknowledges it; the EOC's board says Acknowledged
#   6 EOCAdmin: opens as EOCAdmin only, on its Forms; ICS 209 filled from the boards and the network (lifelines,
#     Alpha, the hospital, the incident); kept as a draft, made final (read only); every EOCAdmin and the EOC see it on the forms board
#   7 ICS 204 for Alpha names its assignment; the IAP for op period 1 carries 202, 203, 204, 205, 206, 208
#   8 Print: the native print is asked, with the sheet only on the page's print rules
#   9 an EOC user's role in the EOC category opens EOCAdmin; a team's role does not
#   10 no page errors on any device
import json, os, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

HOST = sys.argv[1]; SHOTS = sys.argv[2] if len(sys.argv) > 2 else ""
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE); SRC = os.path.dirname(APP)
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)
def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

T = tempfile.mkdtemp(prefix="openeoc-fema-"); procs, where = [], {}
for r in ("general", "alpha", "bravo"):
    p = free(); where[r] = "127.0.0.1:%d" % p
    procs.append(subprocess.Popen([HOST, "--listen", "127.0.0.1:%d" % p, "--api", "/FawcettInnovations.RINA.ram_interface.php"],
                                  env=dict(os.environ, RINA_REGION_FILE=T + "/%s.perm" % r, RINA_NET_CONFIG=T + "/none"),
                                  stdout=subprocess.DEVNULL, stderr=open(T + "/host-%s.log" % r, "w")))
time.sleep(1)
USERS = {"EOCAdmin": ("EOCAdmin", "eoc"), "planner": ("planner1", "eochq"), "alphalead": ("alpha1", "alpha")}
class Door(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def answer(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if urlparse(self.path).path == "/api.php": return self.answer({"ok": True, "rows": []})
        if q.get("op") == ["rams"]: self.answer({"ok": True, "rams": where})
        elif q.get("op") == ["roles"]:
            self.answer({"ok": True, "login": False, "roles": {"eoc": {"default": False, "ram": False, "category": "EOC"}, "eochq": {"default": False, "ram": False, "category": "EOC"},
                                                               "alpha": {"default": False, "ram": True, "category": "Teams"}, "bravo": {"default": False, "ram": True, "category": "Teams"}}})
        else: self.answer({"ok": False, "error": "op"})
    def do_POST(self):
        q = parse_qs(urlparse(self.path).query); b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        u = USERS.get(b.get("user"))
        if q.get("op") == ["login"]:
            if u and b.get("password") == u[0]: self.answer({"ok": True, "role": u[1], "user": b["user"]})
            else: self.answer({"ok": False, "error": "that user and password are not this app's"})
        else: self.answer({"ok": False, "error": "op"})
dp = free(); door = ThreadingHTTPServer(("127.0.0.1", dp), Door); threading.Thread(target=door.serve_forever, daemon=True).start()
DOOR = "127.0.0.1:%d" % dp
CP = os.pathsep.join([os.path.join(SRC, "frognet-ram-java", "build", "frognet-ram.jar"), os.path.join(APP, "build", "core"), os.path.join(APP, "build", "test")])
# each device with its app's layers, as the Android modules and the Windows zips list them
DEVS = (("eoc1", "EOC Desk", "eoc", "", "help,field,eoc,medical,ics,fema", "eoc"),
        ("alpha9", "Pat (Alpha)", "team", "Alpha", "help,field,team,ics,fema", "team"),
        ("hosp1", "Charge Nurse", "hospital", "", "help,medical,ics,fema", "hospital"),
        ("med12", "Medic 12 crew", "ambulance", "", "help,field,medical,ics,fema", "ambulance"),
        ("adm1", "Lee (Planning)", "eocadmin", "", "help,medical,ics,fema,paper", "eocadmin"),
        ("adm2", "Kim (Docs)", "eocadmin", "", "help,medical,ics,fema,paper", "eocadmin"),
        ("adm3", "Sam (Alpha lead)", "eocadmin", "", "help,medical,ics,fema,paper", "eocadmin"))
dev = {}
for d, name, role, team, lay, lock in DEVS:
    p = free(); dev[d] = p
    procs.append(subprocess.Popen(["java", "-Drt.layers=" + lay, "-cp", CP, "DevHost", os.path.join(APP, "ui"), str(p), d, name, DOOR, role, team, lock],
                                  stdout=open(T + "/dev-%s.log" % d, "w"), stderr=subprocess.STDOUT))
time.sleep(2.5)
ERRS = []
try:
  with sync_playwright() as pw:
    br = pw.chromium.launch()
    def open_dev(d, w=1280, h=860):
        pg = br.new_context(viewport={"width": w, "height": h}).new_page()
        pg.on("pageerror", lambda e, d=d: ERRS.append(d + ": " + str(e)))
        pg.add_init_script("window.__printed=[]")
        pg.goto("http://127.0.0.1:%d/" % dev[d]); pg.wait_for_selector("#connect:not([hidden])", timeout=8000); return pg
    def up(pg): pg.wait_for_selector("#main:not([hidden])", timeout=10000)

    eoc = open_dev("eoc1"); eoc.fill("[name=user]", "EOCAdmin"); eoc.fill("[name=password]", "EOCAdmin"); eoc.click("#connect button[type=submit]"); up(eoc)
    tm = open_dev("alpha9", 412, 860); tm.click("#connect button[type=submit]"); up(tm)
    hs = open_dev("hosp1"); hs.fill("[name=unit]", "Swedish Ballard"); hs.click("#connect button[type=submit]"); up(hs)
    md = open_dev("med12", 412, 860); md.fill("[name=unit]", "Medic 12"); md.click("#connect button[type=submit]"); up(md)

    # 1 the organizations
    eoc.wait_for_function("RT.fema && (()=>{const o=RT.fema.orgs();return o.team&&o.team.Alpha&&o.staff&&o.staff['Swedish Ballard']&&o.ambulance&&o.ambulance['Medic 12']})()", timeout=15000)
    eoc.click("#femaBtn"); eoc.click("text=Organizations")
    body = eoc.inner_text("#sheet")
    check(all(x in body for x in ("Teams", "Alpha", "Hospitals", "Swedish Ballard", "Ambulances", "Medic 12")), "1 Organizations, by the app they joined with: " + " | ".join(l for l in body.splitlines() if l.strip())[:220])

    # 2 the hospital's diversion into Health and Medical
    hs.wait_for_selector(".fac"); hs.select_option(".fac [name=diversion]", "Trauma diversion"); hs.fill(".fac [name=b_ed]", "2"); hs.click(".fac .go")
    eoc.wait_for_function("RT.fema.reports().health.some(r=>r[0]==='Swedish Ballard'&&r[1]==='Stabilizing')", timeout=10000)
    eoc.click("#femaBtn"); eoc.click("text=Community lifelines")
    ll = eoc.inner_text("#sheet")
    check("from the reports: Stabilizing" in ll.split("Health and Medical")[1].split("\n")[0], "2 Health and Medical from the reports: " + ll.split("Health and Medical")[1].split("\n")[0].strip())

    # 3 the EOC's call on Energy
    eoc.click(".card.ll:has-text('Energy') button")
    eoc.select_option(".icsform [name=status]", "Unstable"); eoc.fill(".icsform [name=why]", "Feeder 7 down, 18,400 customers out"); eoc.click(".icsform .go")
    eoc.wait_for_function("S.sheet==='fema-ll' && document.querySelector('#sheet').innerText.includes('Command Center: Unstable')", timeout=10000)
    tm.wait_for_function("RT.fema.call('Energy (Power and Fuel)') && RT.fema.call('Energy (Power and Fuel)').status==='Unstable'", timeout=10000)
    check(True, "3 the EOC's call on Energy, Unstable, on the EOC and on Alpha's app")

    # 4 the incident, staffing, an objective
    eoc.evaluate("RT.open('ics-new','incident')")
    eoc.fill(".icsform [name=incName]", "Cascade County Windstorm"); eoc.select_option(".icsform [name=level]", "II: Limited activation")
    eoc.fill(".icsform [name=opPeriod]", "1"); eoc.select_option(".icsform [name=structure]", "ICS or ICS-like"); eoc.select_option(".icsform [name=ictype]", "Type 3"); eoc.click(".icsform .go")
    try: eoc.wait_for_function("RT.icsNow('incident','incident').incName==='Cascade County Windstorm'", timeout=10000)
    except Exception: print("DEBUG", eoc.evaluate("JSON.stringify({st:$('#status').textContent,sheet:S.sheet,inc:S.ics.incident,html:document.querySelector('#sheet .sh-body').innerText.slice(0,300)})")); raise
    eoc.click("#femaBtn"); eoc.click("text=Staffing"); eoc.click("tr:has(th:text-is('EOC Director')) button:has-text('Fill')")
    eoc.fill(".icsform [name=person]", "Dana Ruiz"); eoc.fill(".icsform [name=agency]", "County EM"); eoc.click(".icsform .go")
    eoc.wait_for_function("S.sheet==='fema-staff' && document.querySelector('#sheet').innerText.includes('Dana Ruiz')", timeout=10000)
    eoc.evaluate("RT.open('ics-new','objectives',{opPeriod:1})"); eoc.fill(".icsform [name=objective]", "Account for all residents in the north district by 18:00"); eoc.click(".icsform .go")
    eoc.wait_for_function("Object.keys(S.ics.objectives||{}).length===1", timeout=10000)
    check(True, "4 the incident (ICS or ICS-like, Type 3, op period 1); EOC Director filled by Dana Ruiz; an ICS-202 objective")

    # 5 the EOC assigns Alpha
    eoc.click("#femaBtn"); eoc.click("text=Organizations"); eoc.click(".card:has-text('Alpha') button:has-text('Assign')")
    check(eoc.input_value(".icsform [name=to]") == "Alpha", "5a Assign from Organizations names Alpha: " + eoc.input_value(".icsform [name=to]"))
    eoc.fill(".icsform [name=work]", "Welfare checks, Maple St to Elm St"); eoc.fill(".icsform [name=comms]", "Tac 1, 146.520 simplex"); eoc.click(".icsform .go")
    tm.wait_for_function("document.querySelector('#status').textContent.includes('the Command Center assigned Alpha')", timeout=10000)
    tm.evaluate("RT.open('fema-mine')"); tm.wait_for_selector("#sheet td:has-text('Welfare checks')")
    tm.click("#sheet tr:has-text('Welfare checks')"); tm.select_option(".icsupd [name=status]", "Acknowledged"); tm.fill(".icsupd [name=text]", "on our way"); tm.click(".icsupd .go")
    eoc.wait_for_function("Object.values(S.ics.assign||{}).length===1 && RT.icsNow('assign',Object.keys(S.ics.assign)[0]).status==='Acknowledged'", timeout=10000)
    check(tm.evaluate("!$('#mineBtn').hidden") and eoc.evaluate("$('#mineBtn').hidden"), "5 Alpha has it under Assignments (with a notice), acknowledged it; the EOC's board says Acknowledged")

    # 6 EOCAdmin
    ad = open_dev("adm1"); locked = ad.inner_text("#connect")
    ad.fill("[name=user]", "planner"); ad.fill("[name=password]", "planner1"); ad.click("#connect button[type=submit]"); up(ad)
    ad.wait_for_function("S.sheet==='paper'", timeout=10000)
    check("EOCAdmin only" in locked and ad.evaluate("S.role") == "eocadmin", "6a EOCAdmin opens as EOCAdmin only, on its Forms; planner (role eochq, category EOC) logs in")
    ad.wait_for_function("RT.icsNow('incident','incident').incName && Object.keys(S.ics.assign||{}).length && Object.keys(S.fac||{}).length", timeout=10000)
    ad.click("#sheet button:has-text('ICS 209')")
    v = {k: ad.input_value(".paperform [name=%s]" % k) for k in ("incident", "lifelines", "orgs", "level", "ictype")}
    check(v["incident"] == "Cascade County Windstorm" and "Energy (Power and Fuel): Unstable" in v["lifelines"] and "Health and Medical: Stabilizing" in v["lifelines"]
          and "Alpha (Teams)" in v["orgs"] and "Swedish Ballard (Hospitals)" in v["orgs"] and v["ictype"] == "Type 3",
          "6b ICS 209 filled from the boards and the network: " + json.dumps(v)[:300])
    ad.fill(".paperform [name=outlook]", "Winds easing after 22:00"); ad.click("#pfDraft")
    ad.wait_for_function("Object.keys(S.ics.forms||{}).length===1", timeout=10000)
    ad.click("#pfFinal"); ad.click("#pfFinal")
    ad.wait_for_function("(()=>{const id=Object.keys(S.ics.forms||{})[0];return id&&RT.icsNow('forms',id).status==='Final'})()", timeout=10000)
    a2 = open_dev("adm2"); a2.fill("[name=user]", "EOCAdmin"); a2.fill("[name=password]", "EOCAdmin"); a2.click("#connect button[type=submit]"); up(a2)
    a2.wait_for_function("Object.keys(S.ics.forms||{}).length===1", timeout=10000)
    a2.click("#sheet tr:has-text('ICS 209')"); a2.wait_for_selector(".paperform")
    fin = a2.evaluate("[...document.querySelectorAll('.paperform [name]')].every(x=>x.disabled)") and a2.input_value(".paperform [name=outlook]") == "Winds easing after 22:00"
    eoc.wait_for_function("Object.keys(S.ics.forms||{}).length===1", timeout=10000)
    check(fin, "6c the 209 kept as a draft, made final: another EOCAdmin opens it read only, as kept; the EOC has it on the forms board")

    # 7 the 204 and the IAP
    ad.evaluate("RT.open('paper-form','204')")
    w = ad.input_value(".paperform [name=work]")
    check(ad.input_value(".paperform [name=to]") == "Alpha" and "Welfare checks" in w and "Acknowledged" in w and "Tac 1" in ad.input_value(".paperform [name=comms]"), "7a ICS 204 for Alpha: " + w)
    ad.evaluate("RT.open('paper-iap')"); iap = ad.inner_text("#sheet")
    check(all(("ICS " + n) in iap for n in ("202", "203", "204", "205", "206", "208")) and "north district" in iap and "Dana Ruiz" in iap and "Swedish Ballard" in iap,
          "7 the IAP for op period 1: 202 (the objective), 203 (Dana Ruiz), 204, 205, 206 (Swedish Ballard), 208")

    # 8 Print: the native side's, the sheet only
    ad.evaluate("() => { window.rtNative.print = (t) => { window.__printed.push(t) } }")
    ad.click("#iapPrint")
    pr = ad.evaluate("window.__printed"); ad.emulate_media(media="print")
    vis = ad.evaluate("({sheet: getComputedStyle(document.querySelector('#sheet')).display, map: getComputedStyle(document.querySelector('#map')).display, head: getComputedStyle(document.querySelector('header')).display, tool: getComputedStyle(document.querySelector('#sheet .noprint')).display})")
    ad.emulate_media(media="screen")
    check(pr == ["OpenEOC · Incident Action Plan, op period 1"] and vis["sheet"] != "none" and vis["map"] == "none" and vis["head"] == "none" and vis["tool"] == "none",
          "8 Print asks the native print (%s); printing shows the sheet only %s" % (pr, vis))
    if SHOTS:
        ad.evaluate("RT.open('paper-form','209')"); ad.screenshot(path=os.path.join(SHOTS, "eocadmin-209.png"))
        eoc.click("#femaBtn"); eoc.screenshot(path=os.path.join(SHOTS, "eoc-fema.png"))
        eoc.click("text=Community lifelines"); eoc.screenshot(path=os.path.join(SHOTS, "eoc-lifelines.png"))
        tm.evaluate("RT.open('fema-mine')"); tm.screenshot(path=os.path.join(SHOTS, "team-assignments.png"))

    # 9 a team's login does not open EOCAdmin
    a3 = open_dev("adm3"); a3.fill("[name=user]", "alphalead"); a3.fill("[name=password]", "alpha1"); a3.click("#connect button[type=submit]")
    a3.wait_for_function("document.querySelector('#cstatus').textContent.includes('alpha')", timeout=10000)
    check(a3.is_hidden("#main"), "9 a team's role (alpha, category Teams) does not open EOCAdmin: " + a3.inner_text("#cstatus")[:160])
    check(not ERRS, "10 no page errors %s" % ERRS)
    br.close()
finally:
    for p in procs: p.kill()
print("RESULT", "PASS" if not FAILS else "FAIL %s" % FAILS)
sys.exit(1 if FAILS else 0)
