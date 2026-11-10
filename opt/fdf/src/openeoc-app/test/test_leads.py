#!/usr/bin/env python3
"""test_leads.py -- [EOC_LEADS_V1] the EOC's Leads against r52's api.php and fdf-app, with the door from the tree (DOOR_PHP):
the EOC app real (AppCore + its page, DevHost) adds a lead with roles; the lead creates its team at the door.
usage: test_leads.py NODE_ROOT (a node package tree: its api.php, fdf-app; root, MariaDB running)
  OPENEOC_APP=<this app> DOOR_PHP=<the door to test: fdf-source/fdf/www/dc/ram.php> FDF_RAM=<rinab build>/fdf-ram"""
import json, os, shutil, subprocess, sys, tempfile, time, socket, urllib.request

R = os.path.abspath(sys.argv[1]); WWW = R + "/var/www/html"; FDF = R + "/opt/fdf"
SCR = os.path.dirname(os.path.abspath(__file__)); T = tempfile.mkdtemp(prefix="repro-")
PW = "repro" + str(os.getpid()); procs = []
def sh(*a, **k): return subprocess.run(list(a), capture_output=True, text=True, **k)

# ---- databasehost-control and the SystemRam: r52's api.php on 127.0.0.2:80, its own MariaDB database
for ip in ("127.0.0.2",): sh("ip", "addr", "add", ip + "/8", "dev", "lo")
# the node's own FrogNet address (its .1: the writer of its tuples, the holder its held reads fire to, through its port 80)
sh("ip", "addr", "add", "10.99.99.1/32", "dev", "lo")
d = T + "/www-ctl"; shutil.copytree(WWW, d)
cfg = (open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "..", "var", "www", "html", "config.php")).read().replace("__FROGNET_DB_PASS__", PW)
       .replace("define('DB_NAME', 'FrogNet');", "define('DB_NAME', 'repro_ctl');").replace("define('DB_USER', 'FrogUser');", "define('DB_USER', 'ReproUser');"))
open(d + "/config.php", "w").write(cfg)
sql = "DROP DATABASE IF EXISTS repro_ctl; CREATE DATABASE repro_ctl;" + "".join(
    "CREATE USER IF NOT EXISTS 'ReproUser'@'%s' IDENTIFIED BY '%s'; ALTER USER 'ReproUser'@'%s' IDENTIFIED BY '%s'; GRANT ALL ON repro_ctl.* TO 'ReproUser'@'%s';"
    % (h, PW, h, PW, h) for h in ("localhost", "127.0.0.1"))
subprocess.run(["mysql", "-e", sql], check=True)
for f in ("Create_Database.sql", "schema_fixups.sql"):
    subprocess.run("sed 's/`FrogNet`/`repro_ctl`/g; s/^USE FrogNet;/USE repro_ctl;/' %s | mysql" % os.path.join(WWW, f), shell=True, check=True, stdout=subprocess.DEVNULL)
procs.append(subprocess.Popen(["php", "-S", "127.0.0.2:80", "-t", d], env=dict(os.environ, PHP_CLI_SERVER_WORKERS="8"),
                              stdout=subprocess.DEVNULL, stderr=open(T + "/php-ctl.log", "w")))

# ---- the node: its hosts file (as the election writes it), its facts, its fdf-app; its door (r52's ram.php)
os.makedirs(T + "/etc"); HOSTS = T + "/etc/hosts"
open(HOSTS, "w").write("127.0.0.1 localhost\n127.0.1.1 FrogNetHost.node-a\n127.0.0.2 databasehost_control.frognet\n127.0.0.2 databasehost.frognet\n")
os.makedirs(T + "/a/apps")
json.dump({"name": "node-a", "guid": "guid-a", "role": "node", "addrs": [{"ip": "10.99.99.1", "kind": "own"}, {"ip": "127.0.1.1", "kind": "own"}],
           "capability": {"mem_total_kb": 8000000, "cores": 4}}, open(T + "/a/facts.json", "w"))
ENV = dict(os.environ, FDF_HOSTS=HOSTS, FDF_FACTS=T + "/a/facts.json", FDF_VAR=T + "/a", FDF_APPS=T + "/a/apps", FDF_BIN=FDF + "/bin",
           FDF_PY=FDF + "/lib/python", FDF_APP_RESTART="true", FROGNET_CORE=R + "/opt/frognet_semantic/core")
www = T + "/door/opt/fdf"; os.makedirs(www + "/www"); shutil.copytree(FDF + "/www/dc", www + "/www/dc"); shutil.copy(os.environ["DOOR_PHP"], www + "/www/dc/ram.php")
open(www + "/fdf.env", "w").write("export FDF_BIN=%s/bin FDF_HOSTS=%s FDF_VAR=%s/a\ntrue\n" % (FDF, HOSTS, T))
procs.append(subprocess.Popen(["php", "-S", "127.0.1.1:8088", "-t", T + "/door/opt/fdf/www"], env=dict(ENV, PHP_CLI_SERVER_WORKERS="8"),   # as Apache: a held read does not block the rest stdout=subprocess.DEVNULL,
                              stderr=open(T + "/php-door.log", "w")))
procs.append(subprocess.Popen(["php", "-S", "10.99.99.1:80", "-t", d], env=dict(os.environ, PHP_CLI_SERVER_WORKERS="8"),   # this node's port 80: held_fire.php
                              stdout=subprocess.DEVNULL, stderr=open(T + "/php-node.log", "w")))
for hp in (("127.0.0.2", 80), ("127.0.1.1", 8088), ("10.99.99.1", 80)):
    for _ in range(100):
        try: socket.create_connection(hp, 0.2).close(); break
        except OSError: time.sleep(0.05)
def fa(*a):
    p = subprocess.run([sys.executable, FDF + "/bin/fdf-app"] + list(a), env=ENV, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    return p.returncode, (p.stdout + p.stderr).strip()
def roles(app):
    try: return json.loads(urllib.request.urlopen("http://127.0.1.1:8088/dc/ram.php?op=roles&app=" + app, timeout=20).read())
    except urllib.error.HTTPError as e: return {"http": e.code, "body": e.read().decode()[:300]}
def show(t, j): print("%s:\n  %s" % (t, json.dumps(j, sort_keys=True)[:900]), flush=True)
def door(q, body=None):
    r = urllib.request.Request("http://127.0.1.1:8088/dc/ram.php?" + q, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    try: return json.loads(urllib.request.urlopen(r, timeout=60).read())
    except urllib.error.HTTPError as e:
        t = e.read().decode()
        try: return json.loads(t)
        except ValueError: return {"http": e.code, "body": t[:300]}
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)

import re
try:
    rc, out = fa("config", "openeoc", "general,alpha", "--role", "eoc@EOC", "--role", "alpha", "--user", "EOCAdmin:eoc-2026=eoc", "--user", "kim:kim-2026=alpha",
                 "--admin", "boss:adm-2026")
    print("fdf-app config openeoc: rc=%d %s" % (rc, out.replace("\n", " | ")[:200]))
    cfgf = json.load(open(T + "/a/apps/openeoc.json")); gp = int(cfgf["ports"]["general"])
    open(HOSTS, "a").write("127.0.0.1 openeocHost.frognet\n")
    procs.append(subprocess.Popen([os.environ["FDF_RAM"], "--listen", "127.0.0.1:%d" % gp, "--api", "/FawcettInnovations.RINA.ram_interface.php"],
                                  env=dict(os.environ, RINA_REGION_FILE=T + "/g.perm", RINA_NET_CONFIG=T + "/none"), stdout=subprocess.DEVNULL, stderr=open(T + "/ram.log", "w")))
    APP = os.environ["OPENEOC_APP"]; SRC = os.path.dirname(APP)
    CP = os.pathsep.join([SRC + "/frognet-ram-java/build/frognet-ram.jar", APP + "/build/core", APP + "/build/test"])
    from playwright.sync_api import sync_playwright
    def free():
        s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
    pe, pt, pl, p2 = free(), free(), free(), free()
    os.makedirs("/run/frognet/held", exist_ok=True)
    procs.append(subprocess.Popen(["java", "-Drt.layers=help,field,eoc,medical,ics,fema", "-Duser.home=" + T, "-cp", CP, "DevHost", APP + "/ui", str(pe), "eoc1", "EOC Desk", "127.0.1.1:8088", "eoc", "", "eoc"],
                                  stdout=open(T + "/dev-eoc.log", "w"), stderr=subprocess.STDOUT))
    procs.append(subprocess.Popen(["java", "-Drt.layers=help,field,team,ics,fema", "-Duser.home=" + T, "-cp", CP, "DevHost", APP + "/ui", str(pt), "lee1", "Lee", "127.0.1.1:8088", "team", "", "team"],
                                  stdout=open(T + "/dev-team.log", "w"), stderr=subprocess.STDOUT))
    procs.append(subprocess.Popen(["java", "-Drt.layers=help,field,team,ics,fema", "-Duser.home=" + T, "-cp", CP, "DevHost", APP + "/ui", str(pl), "lr1", "Lee", "127.0.1.1:8088", "team", "Lee Rescue", "team"],
                                  stdout=open(T + "/dev-lr.log", "w"), stderr=subprocess.STDOUT))
    procs.append(subprocess.Popen(["java", "-Drt.layers=help,field,team,ics,fema", "-Duser.home=" + T, "-cp", CP, "DevHost", APP + "/ui", str(p2), "lr2", "Pat", "127.0.1.1:8088", "team", "Lee Rescue", "team"],
                                  stdout=open(T + "/dev-lr2.log", "w"), stderr=subprocess.STDOUT))
    time.sleep(2.5)
    with sync_playwright() as pw:
        br = pw.chromium.launch(); errs = []
        pg = br.new_page(viewport={"width": 1100, "height": 900}); pg.on("pageerror", lambda e: errs.append("eoc: " + str(e)))
        pg.goto("http://127.0.0.1:%d/" % pe); pg.wait_for_selector("#connect:not([hidden])", timeout=8000)
        pg.fill("[name=user]", "EOCAdmin"); pg.fill("[name=password]", "eoc-2026"); pg.click("#connect button[type=submit]")
        pg.wait_for_selector("#main:not([hidden])", timeout=20000)
        check(pg.evaluate("!$('#leadsBtn').hidden"), "1 the EOC has Leads")
        pg.evaluate("$('#leadsBtn').click()"); pg.wait_for_function("S.leads !== null && document.querySelector('#sheet .leadform')", timeout=15000)
        pg.fill(".leadform [name=lead]", "lee"); pg.fill(".leadform [name=password]", "lee-2026"); pg.fill(".leadform [name=roles]", "team, hospital"); pg.click(".leadform .go")
        pg.wait_for_selector("#sheet .leadqr svg", timeout=15000)
        ls = door("op=leads&app=openeoc")
        line = pg.evaluate("FrogQR.enrollLine(" + json.dumps("http://127.0.1.1:8088/dc/ram.php") + ",'openeoc','lee')")
        ok_said = pg.inner_text("#sheet .leadsay")
        check("lead lee created" in ok_said, "2b the Leads screen says the lead was created: %r" % ok_said)
        check((ls.get("leads") or {}).get("lee", {}).get("roles") == ["team", "hospital"] and "lee-2026" not in json.dumps(ls),
              "2 Create the lead: lee, roles team and hospital, at the door (op=leads: %s); its QR on the EOC's screen; no password at the door's answer" % json.dumps(ls.get("leads")))
        pg.fill(".leadform [name=lead]", "sam"); pg.fill(".leadform [name=password]", "sam-2026"); pg.fill(".leadform [name=roles]", "*"); pg.click(".leadform .go")
        pg.wait_for_function("S.leads && S.leads.sam", timeout=15000)
        pg.fill(".leadform [name=lead]", "zed"); pg.fill(".leadform [name=password]", "zed-2026"); pg.fill(".leadform [name=roles]", "team, plumber"); pg.click(".leadform .go")
        check("not a role: plumber" in pg.inner_text("#status"), "3 a role that is not one is refused on the screen: " + pg.inner_text("#status"))
        pg.fill(".leadform [name=lead]", "nopw"); pg.fill(".leadform [name=password]", ""); pg.click(".leadform .go")
        said = pg.inner_text("#sheet .leadsay")
        check("password of at least 4" in said, "3b a lead with no password: said on the Leads screen itself: %r" % said)
        nk = door("op=lead&app=openeoc", {"user": "kim", "password": "kim-2026", "lead": "eve", "roles": ["*"], "verifier": {"salt": "00" * 16, "n": 200000, "hash": "0" * 64}})
        check("not a Command Center user" in str(nk.get("why")), "4 a team's user (kim) cannot add a lead at the door: " + str(nk.get("why")))

        # the lead's phone: the EOC's QR, scanned: the name filled in, the team's name blank
        tm = br.new_page(viewport={"width": 412, "height": 860}); tm.on("pageerror", lambda e: errs.append("team: " + str(e)))
        tm.goto("http://127.0.0.1:%d/" % pt); tm.wait_for_selector("#connect:not([hidden])", timeout=8000)
        tm.evaluate("() => { window.__enroll = []; window.rtNative.enroll = (j) => window.__enroll.push(JSON.parse(j)); document.querySelector('#qrSec').hidden = false }")   # a phone's: the QR section
        tm.evaluate("(l) => rtDeliver({ev:'enrollScan', line:l, url:'http://127.0.1.1:8088/dc/ram.php', app:'openeoc', user:'lee', team:false, key:''})", line)
        st = tm.evaluate("({u:$('#enrollUser').value, ro:$('#enrollUser').readOnly, uh:$('#enrollUserL').hidden, t:$('#enrollCall').value, th:$('#enrollCallL').hidden, lab:$('#enrollCallL .ecl').textContent, go:$('#enrollGo').textContent})")
        check(st == {"u": "lee", "ro": True, "uh": False, "t": "", "th": False, "lab": "Team name", "go": "Create the Team"}, "5 the lead scans the EOC's QR: %s" % st)
        tm.fill("#enrollCall", "Lee Rescue"); tm.fill("#enrollPw", "lee-2026"); tm.click("#enrollGo")
        sent = tm.evaluate("window.__enroll")
        check(len(sent) == 1 and sent[0]["team"] == "Lee Rescue" and sent[0]["kind"] == "team" and sent[0]["line"].split()[3] == "lee",
              "6 Create the Team asks the phone to enroll lee, team Lee Rescue, as a team: %s" % [dict(x, password="***") for x in sent])
        # what the phone then posts (Enroll.enroll(line, password, team, kind)), at the door
        t0 = time.time(); en = door("op=enroll&app=openeoc", {"user": "lee", "password": "lee-2026", "team": "Lee Rescue", "kind": "team"})
        ro = door("op=roles&app=openeoc"); lr = (ro.get("roles") or {}).get("Lee Rescue")
        check("did not give openeoc-lee_rescue an identity" in str(en.get("why")) and lr and lr.get("child"),
              "7 the door creates team Lee Rescue (op=roles: %s) and asks the region for its identity (none here, %.1f s): %s" % (lr, time.time() - t0, str(en.get("why"))[:90]))
        amb = door("op=enroll&app=openeoc", {"user": "lee", "password": "lee-2026", "team": "Lee Medic", "kind": "ambulance"})
        check("not ambulance" in str(amb.get("why")), "8 lee cannot run an ambulance: " + str(amb.get("why")))
        tk = door("op=enroll&app=openeoc", {"user": "sam", "password": "sam-2026", "team": "Lee Rescue", "kind": "team"})
        check("no replacement" in str(tk.get("why")), "9 sam cannot take Lee Rescue: " + str(tk.get("why")))
        wp = door("op=enroll&app=openeoc", {"user": "lee", "password": "nope", "team": "Lee Rescue", "kind": "team"})
        nt = door("op=enroll&app=openeoc", {"user": "lee", "password": "lee-2026", "kind": "team"})
        check("not lee's password" in str(wp.get("why")) and "team's name" in str(nt.get("why")), "10 a wrong password: %s; no team's name: %s" % (wp.get("why"), nt.get("why")))
        lg = door("op=login&app=openeoc", {"user": "lee", "password": "lee-2026"}); lg2 = door("op=login&app=openeoc", {"user": "sam", "password": "sam-2026"})
        check(lg.get("role") == "Lee Rescue" and "not created a team" in str(lg2.get("why")), "11 lee logs in as Lee Rescue; sam, with no team yet: %s" % lg2.get("why"))
        pg.evaluate("$('#leadsBtn').click()"); pg.wait_for_function("S.leads && S.leads.lee && S.leads.lee.team === 'Lee Rescue'", timeout=15000)
        check("Lee Rescue" in pg.inner_text("#sheet"), "12 the EOC's Leads shows lee's team: " + " | ".join(l for l in pg.inner_text("#sheet").splitlines() if "lee" in l.lower())[:160])
        pg.click("#sheet tr:has-text('sam') button:has-text('Remove')"); pg.wait_for_function("S.leads && !S.leads.sam", timeout=15000)
        gone = door("op=login&app=openeoc", {"user": "sam", "password": "sam-2026"})
        check("not this app" in str(gone.get("why")), "13 Remove: sam is gone at the door: " + str(gone.get("why")))
        # [TEAM_ON_SYSTEMRAM_V1] the lead says Use SystemRam: the team has no RAM of its own; its messages go through api.php
        lr = br.new_page(viewport={"width": 412, "height": 860}); lr.on("pageerror", lambda e: errs.append("lr: " + str(e)))
        lr.goto("http://127.0.0.1:%d/" % pl); lr.wait_for_selector("#connect:not([hidden])", timeout=8000)
        lr.evaluate("() => { const f = $('#connect'); if (![...f.team.options].some(o => o.value === 'Lee Rescue')) f.team.insertAdjacentHTML('beforeend', '<option>Lee Rescue</option>'); f.team.value = 'Lee Rescue' }")
        lr.click("#connect button[type=submit]"); lr.wait_for_selector("#main:not([hidden])", timeout=20000)
        lr.evaluate("send({op: 'teamPlace', at: 'systemram', user: 'lee', password: 'lee-2026'})")
        for _ in range(100):
            rm = door("op=rams&app=openeoc").get("rams", {})
            if rm.get("Lee Rescue") == "systemram": break
            time.sleep(0.2)
        title = lr.evaluate("document.title")
        check(rm.get("Lee Rescue") == "systemram" and title == "OpenEOC Team · Lee Rescue", "15 Use SystemRam: the door says team Lee Rescue is %r; the lead's window title %r" % (rm.get("Lee Rescue"), title))
        pg.wait_for_function("() => true"); time.sleep(3)
        lr.evaluate("send({op: 'say', to: 'team:Lee Rescue', text: 'Lee Rescue on the SystemRam, over'})")
        try: pg.wait_for_function("S.msgs.some(m => m.text === 'Lee Rescue on the SystemRam, over')", timeout=30000); got = True
        except Exception: got = False
        rows = door("op=team&app=openeoc&s=openeoc-team&team=Lee%20Rescue").get("rows", [])
        check(got and any((r.get("bag") or {}).get("text") == "Lee Rescue on the SystemRam, over" for r in rows),
              "16 the team's message reaches the EOC (%s), through the SystemRam's api.php (op=team: %d tuple(s) for openeoc-team@lee rescue)" % (got, len(rows)))
        pg.evaluate("send({op: 'say', to: 'team:Lee Rescue', text: 'EOC copies, Lee Rescue'})")
        try: lr.wait_for_function("S.msgs.some(m => m.text === 'EOC copies, Lee Rescue')", timeout=30000); back = True
        except Exception: back = False
        check(back, "17 the EOC's message to team Lee Rescue reaches the team's device through the SystemRam")
        # [TEAM_DELETE_V1] the Command Center deletes team Lee Rescue: Delete, then Delete Lee Rescue: sure?
        pg.evaluate("$('#leadsBtn').click()"); pg.wait_for_selector("#sheet .tdel[data-t='Lee Rescue']", timeout=15000)
        pg.click("#sheet .tdel[data-t='Lee Rescue']"); asked = "sure?" in pg.inner_text("#sheet .tdel[data-t='Lee Rescue']")
        pg.click("#sheet .tdel[data-t='Lee Rescue']")
        pg.wait_for_function("S.leadTeams && !S.leadTeams['Lee Rescue']", timeout=15000)
        ro2 = door("op=roles&app=openeoc").get("roles") or {}; rm2 = door("op=rams&app=openeoc").get("rams") or {}; ld2 = (door("op=leads&app=openeoc").get("leads") or {}).get("lee", {})
        check(asked and "Lee Rescue" not in ro2 and "Lee Rescue" not in rm2 and ld2.get("team") == "" and "team Lee Rescue deleted" in pg.inner_text("#sheet .leadsay"),
              "18 Delete (asked first: %s): Lee Rescue gone from the door's roles and RAMs, lee has no team (%r); the screen: %r" % (asked, ld2.get("team"), pg.inner_text("#sheet .leadsay")))
        again = door("op=enroll&app=openeoc", {"user": "lee", "password": "lee-2026", "team": "Lee Rescue", "kind": "team"})
        adm = door("op=teamdel&app=openeoc", {"user": "EOCAdmin", "password": "eoc-2026", "team": "alpha"})
        check("did not give openeoc-lee_rescue an identity" in str(again.get("why")) and "admin configured" in str(adm.get("why")),
              "19 lee creates Lee Rescue again (to the region step: %s); the admin's Team alpha is the admin tool's to remove: %s" % (str(again.get("why"))[:60], adm.get("why")))
        # [TEAM_DELETE_V1] removing lead lee takes its team with it; a device connecting as Lee Rescue is refused, said
        pg.evaluate("$('#leadsBtn').click()"); pg.wait_for_selector("#sheet tr:has-text('lee') button:has-text('Remove')", timeout=15000)
        pg.click("#sheet tr:has-text('lee') button:has-text('Remove')")
        pg.wait_for_function("S.leads && !S.leads.lee && S.leadTeams && !S.leadTeams['Lee Rescue']", timeout=15000)
        p2g = br.new_page(viewport={"width": 412, "height": 860}); p2g.on("pageerror", lambda e: errs.append("lr2: " + str(e)))
        p2g.goto("http://127.0.0.1:%d/" % p2); p2g.wait_for_selector("#connect:not([hidden])", timeout=8000)
        p2g.evaluate("() => { const f = $('#connect'); if (![...f.team.options].some(o => o.value === 'Lee Rescue')) f.team.insertAdjacentHTML('beforeend', '<option>Lee Rescue</option>'); f.team.value = 'Lee Rescue' }")
        p2g.click("#connect button[type=submit]")
        p2g.wait_for_function("document.querySelector('#cstatus').textContent.includes('not on this network') || document.querySelector('#status').textContent.includes('not on this network')", timeout=20000)
        why = p2g.inner_text("#cstatus") + " | " + p2g.inner_text("#status")
        check(p2g.is_hidden("#main") and "the Command Center deleted it" in why, "20 lead lee removed takes Lee Rescue with it; a device connecting as Lee Rescue is refused: %r" % why[:200])
        logs = open(T + "/php-door.log").read() + open(T + "/dev-eoc.log").read()
        check(not errs and "lee-2026" not in logs and "sam-2026" not in logs, "14 no page errors %s; no password in the door's or the core's log" % errs)
        br.close()
except Exception as e:
    FAILS.append("stopped: %s" % str(e)[:300]); raise
finally:
    for p in procs: p.terminate()
    print("work dir:", T); print("RESULT", "PASS" if not FAILS else "FAIL: %s" % FAILS)
