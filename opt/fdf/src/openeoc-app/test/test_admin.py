#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_admin.py [SHOTS_DIR] -- [ADMIN_APP_V1] the OpenEOC admin app (its real core, AdminCore, and its real page,
# through DevHost) against a real network RAM (fdf-ram), three real members (fdf-client, fdf-app serve) and the real
# door (/dc/ram.php under PHP, its `ram` CLI the real one). [ROLES_ON_API_V1] databasehost-control and the SystemRam are
# one role host's api.php on port 80 (127.0.0.2, this tree's api.php on its own MariaDB database), which the members'
# hosts file names; this machine's own .1 (10.99.99.1 on its veth fnt0) serves held_fire.php, where its holds fire.
# The members were installed with
#   fdf-app config openeoc general,alpha,bravo --role eoc@EOC --role alpha:default --role bravo:default
#       --user EOCAdmin:EOCAdmin=eoc --admin boss:adm-2026
# [TEAMS_AUTHORIZED_V1] (John 2026-11-09: "we are authorizing Teams, and that's the trust boundary. EOC allocates Team
# login and PW, lead connects to the EOC LAN and claims the Team") the admin's screen is Teams and EOC users:
#   1 a wrong admin password is refused, on this device (no request carries it)
#   2 the admin's screen: Teams alpha and bravo, EOC user EOCAdmin, every member and its configuration as current
#   3 the admin adds Team echo with its password and applies: every member says adopted, on the screen; the door's
#     op=roles has echo (its own RAM); echo's login and password log in at the door as echo; the echo RAM is served; no
#     password and no admin key in the network RAM
#   4 a member installed without --admin says why it did not adopt, on the screen
#   5 [ENROLL_V1] echo's login leads echo: the members' configurations say so; at the door, op=enroll with a wrong
#     password is refused, op=identity by a user who leads no team (EOCAdmin) is refused, and echo's op=identity gets
#     past the check to the region (none here, so the door says the region did not give the identity)
#   6 [FROG_QR_V1] echo's QR on the admin's screen reads, by the phone scanner's zxing, as echo's enrollment line
#   7 [ASSIGNMENTS_SAVE_V1] Save writes the assignments (verifiers, no password) to a file; Team echo is removed and
#     applied; Restore loads the file as an edit, Apply sends it: echo is back, led by its login, which logs in with the
#     old password
#   8 the admin adds Team crew1 (no category: its app's, at its claim) and EOC user dana: every member with the admin
#     key adopts them; the configuration has no category for crew1 and dana in the EOC's role; crew1 and dana log in
#     at the door as crew1 and eoc
#  10 [APP_SELECTION_V1] the admin's "Where each RAM is served": * ranked -mem_total_kb, applied: the general RAM moves to
#     Vashon1 (the least memory; every member decides by the newest configuration, Vashon1 too, which cannot adopt it);
#     the row removed and applied: back on Seattle5 (the most memory)
#  11 [GROUPS_NOW_V1] the admin's "Groups now": every group, its category, where it lives and where it is served; echo
#     places echo on its device at the door: the table says so with no reload (the held read wakes on the place and on
#     the where cell), echo served by nobody on the members; echo places it on the SystemRam: served again, in the table
#   9 [GROUP_PLACE_V1] echo's lead, through the real AppCore (CoreDriver, as the lead's phone runs it): Host team
#     places echo here, on the lead's phone, at the door -- the members adopt it, none serves echo, op=rams has no echo
#     and its places say here; Team on databasehost places it there -- a member serves echo again, and the core hears
#     it from the door's held op=rams (until=echo) in the time printed
import hashlib, hmac, json, os, shutil, socket, subprocess, sys, tempfile, threading, time
from urllib.request import urlopen, Request
from playwright.sync_api import sync_playwright

SHOTS = sys.argv[1] if len(sys.argv) > 1 else ""
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE); SRC = os.path.dirname(APP)
F = os.path.join(SRC, "fdf-source", "fdf"); B = os.path.join(SRC, "fdf-source", "rinab", "build", "static")
sys.path.insert(0, os.path.join(F, "lib", "python"))
CORE = os.path.normpath(os.path.join(SRC, "..", "..", "frognet_semantic", "core")); os.environ["FROGNET_CORE"] = CORE
import fdfram  # noqa: E402
sys.path.insert(0, HERE)
import zxing_read  # noqa: E402
zxing_read.ready()

FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)
def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
def wait_for(pred, limit=15.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        v = pred()
        if v: return v
        time.sleep(0.1)
    return None

T = tempfile.mkdtemp(prefix="openeoc-admin-"); os.makedirs(T + "/bin")
for b in ("fdf-ram", "fdf-client", "fdf-where", "fdf-events", "ram"): shutil.copy(os.path.join(B, b), T + "/bin/")
procs = []
NET = "127.0.0.1:%d" % free()
procs.append(subprocess.Popen([T + "/bin/fdf-ram", "--listen", NET, "--api", fdfram.API],
                              env=dict(os.environ, RINA_REGION_FILE=T + "/net.perm", RINA_NET_CONFIG=T + "/none"),
                              stdout=subprocess.DEVNULL, stderr=open(T + "/net.log", "w")))
time.sleep(0.5)
# [ROLES_ON_API_V1] the role host (both roles) and this node's held_fire.php
WWW = os.path.normpath(os.path.join(SRC, "..", "..", "..", "var", "www", "html")); PW = "adtest" + str(os.getpid())
def serve_www(ip, files, db=None):
    d = os.path.join(T, "www-" + ip); os.makedirs(d)
    for f in files: shutil.copy(os.path.join(WWW, f), d)
    if db:
        open(os.path.join(d, "config.php"), "w").write(open(os.path.join(WWW, "config.php")).read().replace("__FROGNET_DB_PASS__", PW)
            .replace("define('DB_NAME', 'FrogNet');", "define('DB_NAME', '%s');" % db).replace("define('DB_USER', 'FrogUser');", "define('DB_USER', 'AdTestUser');"))
        subprocess.run(["mysql", "-e", "DROP DATABASE IF EXISTS %s; CREATE DATABASE %s;" % (db, db) + "".join(
            "CREATE USER IF NOT EXISTS 'AdTestUser'@'%s' IDENTIFIED BY '%s'; ALTER USER 'AdTestUser'@'%s' IDENTIFIED BY '%s'; GRANT ALL ON %s.* TO 'AdTestUser'@'%s';"
            % (h, PW, h, PW, db, h) for h in ("localhost", "127.0.0.1"))], check=True)
        for f in ("Create_Database.sql", "schema_fixups.sql"):
            subprocess.run("sed 's/`FrogNet`/`%s`/g; s/^USE FrogNet;/USE %s;/' %s | mysql" % (db, db, os.path.join(WWW, f)), shell=True, check=True, stdout=subprocess.DEVNULL)
    procs.append(subprocess.Popen(["php", "-S", "%s:80" % ip, "-t", d], env=dict(os.environ, PHP_CLI_SERVER_WORKERS="8"), start_new_session=True,
                                  stdout=subprocess.DEVNULL, stderr=open(T + "/php-%s.log" % ip, "w")))
    for _ in range(100):
        try: socket.create_connection((ip, 80), 0.2).close(); return
        except OSError: time.sleep(0.05)
subprocess.run(["ip", "addr", "add", "127.0.0.2/8", "dev", "lo"], capture_output=True)
if subprocess.run(["ip", "-o", "addr", "show", "fnt0"], capture_output=True, text=True).stdout.find("10.99.99.1/") < 0:
    subprocess.run(["ip", "link", "add", "fnt0", "type", "veth", "peer", "name", "fnt0p"], capture_output=True)
    subprocess.run(["ip", "addr", "add", "10.99.99.1/24", "dev", "fnt0"], capture_output=True)
    for d_ in ("fnt0p", "fnt0"): subprocess.run(["ip", "link", "set", d_, "up"], capture_output=True)
os.makedirs("/run/frognet/held", exist_ok=True); os.chmod("/run/frognet/held", 0o1777)
serve_www("127.0.0.2", ["api.php", "config.php"], db="adtest_a")
sys.path.insert(0, CORE)
import frognet_tuples  # noqa: E402
ME = frognet_tuples.my_ip(); serve_www(ME, ["held_fire.php"])
HOSTS = T + "/hosts"
open(HOSTS, "w").write("127.0.0.1 localhost\n127.0.1.1 FrogNetHost.Seattle7\n127.0.2.1 FrogNetHost.Seattle5\n127.0.3.1 FrogNetHost.Vashon1\n"
                       "127.0.0.2 databasehost_control.frognet\n127.0.0.2 databasehost.frognet\n")
rm = fdfram.RoleRam("127.0.0.2")
MEM = {"a": 2_000_000, "b": 4_000_000, "c": 1_000_000}
def env(n):
    return dict(os.environ, RINA_RAM=NET, FDF_FACTS=T + "/%s/facts.json" % n, FDF_VAR=T + "/" + n, FDF_APPS=T + "/%s/apps" % n,
                FDF_BIN=T + "/bin", FDF_PY=os.path.join(F, "lib", "python"), FDF_NAME="Seattle" + n, FDF_HOSTS=HOSTS, FROGNET_CORE=CORE)
for i, n in enumerate("abc", start=1):
    d = T + "/" + n; os.makedirs(d + "/apps")
    json.dump({"name": {"a": "Seattle7", "b": "Seattle5", "c": "Vashon1"}[n], "guid": "guid-" + n, "role": "node", "subnet": "127.0.%d.0/24" % i,
               "addrs": [{"ip": "127.0.%d.1" % i, "kind": "own"}, {"ip": ME, "kind": "own"}], "capability": {"mem_total_kb": MEM[n], "cores": 4}}, open(d + "/facts.json", "w"))
    procs.append(subprocess.Popen([T + "/bin/fdf-client"], env=env(n), stdout=open(d + "/client.log", "w"), stderr=subprocess.STDOUT))
time.sleep(1.5)
# [APP_SERVICES_V1] the app has one configuration tuple: the Admin's first install on a writes it; b installs it with the
# admin's password; c keeps a copy from before r47 with no admin key (an older install line), which it cannot adopt
CFG = ["openeoc", "general,alpha,bravo", "--role", "eoc@EOC", "--role", "alpha:default", "--role", "bravo:default", "--user", "EOCAdmin:EOCAdmin=eoc"]
for n in "abc":
    if n == "a":
        subprocess.run([os.path.join(F, "bin", "fdf-app"), "config"] + CFG + ["--admin", "boss:adm-2026"], env=env(n), check=True, stdout=subprocess.DEVNULL)
    elif n == "b":
        subprocess.run([os.path.join(F, "bin", "fdf-app"), "install", "openeoc", "--admin", "boss:adm-2026"], env=dict(env(n), FDF_APP_RESTART="true"),
                       check=True, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    else:
        mine = json.load(open(T + "/a/apps/openeoc.json"))
        json.dump({k: mine[k] for k in ("app", "rams", "ports", "partition", "login", "roles", "categories", "users", "select") if k in mine},
                  open(T + "/c/apps/openeoc.json", "w"))
    procs.append(subprocess.Popen([os.path.join(F, "bin", "fdf-app"), "serve"], env=env(n), stdout=open(T + "/%s/app.log" % n, "w"), stderr=subprocess.STDOUT))
# the door, as a node serves it: /dc/ram.php, fdf.env beside www (member a's: its RAM, its app-where.json)
os.makedirs(T + "/door/www/dc")
shutil.copy(os.path.join(F, "www", "dc", "ram.php"), T + "/door/www/dc/ram.php")
json.dump({"apps": []}, open(T + "/door/www/dc/apps.json", "w"))
open(T + "/door/fdf.env", "w").write("export RINA_RAM=%s FDF_BIN=%s FDF_VAR=%s FDF_HOSTS=%s FDF_FACTS=%s FROGNET_CORE=%s\n" % (NET, T + "/bin", T + "/a", HOSTS, T + "/a/facts.json", CORE))
DP = free(); DOOR = "127.0.0.1:%d" % DP
procs.append(subprocess.Popen(["php", "-S", DOOR, "-t", T + "/door/www"], stdout=open(T + "/door.log", "w"), stderr=subprocess.STDOUT,
                              env=dict(os.environ, PHP_CLI_SERVER_WORKERS="8")))
def door(q, body=None):
    r = Request("http://%s/dc/ram.php?%s" % (DOOR, q), data=json.dumps(body).encode() if body is not None else None,
                headers={"Content-Type": "application/json"})
    try: return json.loads(urlopen(r, timeout=30).read())
    except Exception as e: return {"ok": False, "why": str(e) + " " + getattr(e, "read", lambda: b"")().decode()[:200]}
ok = wait_for(lambda: door("op=roles&app=openeoc").get("ok") and json.load(open(T + "/a/app-where.json")) if os.path.exists(T + "/a/app-where.json") else None, 20)
CP = os.pathsep.join([os.path.join(SRC, "frognet-ram-java", "build", "frognet-ram.jar"), os.path.join(APP, "build", "core"), os.path.join(APP, "build", "test")])
HP = free()
procs.append(subprocess.Popen(["java", "-Drt.core=admin", "-Duser.home=" + T, "-cp", CP, "DevHost", os.path.join(APP, "ui-admin"), str(HP), "x", "x", DOOR],
                              stdout=open(T + "/devhost.log", "w"), stderr=subprocess.STDOUT))
time.sleep(2)
try:
    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_context(viewport={"width": 1280, "height": 900}).new_page()
        PAGE_ERRORS = []; pg.on("pageerror", lambda e: PAGE_ERRORS.append(str(e)))   # a page error is said, in the check that meets it
        pg.goto("http://127.0.0.1:%d/" % HP); pg.wait_for_selector("#connect:not([hidden])", timeout=8000)
        pg.fill("[name=door]", DOOR); pg.fill("[name=user]", "boss"); pg.fill("[name=password]", "wrong"); pg.click("#connect .go")
        pg.wait_for_function("/not openeoc's admin/.test(document.querySelector('#cstatus').textContent)", timeout=15000)
        check(pg.is_visible("#connect"), "1 a wrong admin password is refused on this device: " + pg.inner_text("#cstatus"))
        pg.fill("[name=password]", "adm-2026"); pg.click("#connect .go")
        pg.wait_for_selector("#main:not([hidden])", timeout=15000)
        pg.wait_for_function("document.querySelectorAll('#members tr').length === 3", timeout=10000)
        teams = pg.evaluate("[...document.querySelectorAll('#teams tr td:first-child b')].map(b=>b.textContent)")
        eocu = pg.evaluate("[...document.querySelectorAll('#eocUsers tr td:first-child b')].map(b=>b.textContent)")
        mem = pg.inner_text("#members")
        check(sorted(teams) == ["alpha", "bravo"] and eocu == ["EOCAdmin"] and mem.count("current") == 3 and "no admin key" in mem and not PAGE_ERRORS,
              "2 the admin's screen: Teams %s, EOC users %s, 3 members current (Vashon1 without an admin key): %s; page errors %s" % (teams, eocu, " | ".join(l for l in mem.splitlines() if l.strip())[:220], PAGE_ERRORS))
        if SHOTS: pg.screenshot(path=os.path.join(SHOTS, "admin-before.png"), full_page=True)
        pg.fill("#newTeam", "echo"); pg.fill("#newTeamPw", "echo-2026"); pg.click("#addTeam")
        chg = pg.inner_text("#changes")
        pg.click("#apply")
        pg.wait_for_function("(document.querySelector('#members').textContent.match(/adopted/g)||[]).length === 2 && /refused/.test(document.querySelector('#members').textContent)", timeout=20000)
        mem2 = pg.inner_text("#members")
        if SHOTS: pg.screenshot(path=os.path.join(SHOTS, "admin-applied.png"), full_page=True)
        ro = door("op=roles&app=openeoc")
        lg = door("op=login&app=openeoc", {"user": "echo", "password": "echo-2026"})
        echo = wait_for(lambda: door("op=rams&app=openeoc").get("rams", {}).get("echo"), 15)
        txt = json.dumps([c["bag"] for c in rm.read("fdf-app", "config")] + [c["bag"] for c in rm.read("fdf-app", "admin")])
        k = hashlib.pbkdf2_hmac("sha256", b"adm-2026", hashlib.sha256(b"fdf-app-admin:openeoc:boss").digest()[:16], 200000).hex()
        check("echo" in ro.get("roles", {}) and ro["roles"]["echo"]["ram"] and lg.get("role") == "echo" and bool(echo) and "echo-2026" not in txt and k not in txt,
              "3 '%s' applied: two members adopted it, on the screen; op=roles has echo (%s); echo logs in at the door as %s; echo served at %s; "
              "no password, no admin key in the network RAM" % (chg[:90], ro.get("roles", {}).get("echo"), lg.get("role") or lg.get("why"), echo))
        check("no admin key on this member" in mem2, "4 the member installed without --admin says why it did not adopt: " +
              [l for l in mem2.splitlines() if "refused" in l or "no admin key" in l][:2].__repr__()[:200])

        # 5 echo's login leads echo
        def leads_kim():   # both members with the admin key say echo's login leads it
            l = [c for c in rm.read("fdf-app", "config") if c["bag"].get("users", {}).get("echo", {}).get("lead")]
            return l if len(l) == 2 else None
        chg5 = "Team echo"
        lead = wait_for(leads_kim, 20)
        bad = door("op=enroll&app=openeoc", {"user": "echo", "password": "nope"})
        notlead = door("op=identity&app=openeoc", {"user": "EOCAdmin", "password": "EOCAdmin", "name": "x1"})
        kid = door("op=identity&app=openeoc", {"user": "echo", "password": "echo-2026", "name": "x1"})
        check(lead and len(lead) == 2 and "not this app" in bad.get("why", "") and "does not lead" in notlead.get("why", "")
              and "did not give openeoc-echo-x1 an identity" in kid.get("why", ""),
              "5 '%s': %d members say echo's login leads it; a wrong password: %s; a non-lead: %s; echo gets to the region: %s"
              % (chg5[:60], len(lead or []), bad.get("why", "")[:50], notlead.get("why", "")[:60], kid.get("why", "")[:80]))
        # 6 kim's QR
        pg.wait_for_function("document.querySelector('#changes').textContent.indexOf('adopted by 2') >= 0", timeout=20000)
        pg.click("[data-qr=echo]"); pg.wait_for_selector("#qr:not([hidden]) #qrImg svg", timeout=5000)
        shot = os.path.join(T, "kim-qr.png"); pg.locator("#qrImg svg").screenshot(path=shot)
        if SHOTS: pg.screenshot(path=os.path.join(SHOTS, "admin-qr.png"))
        got = zxing_read.read([shot]).get(shot)
        want = "fdfenroll1 http://%s/dc/ram.php openeoc echo" % DOOR
        check(got == want, "6 echo's QR reads as %r" % got)
        pg.click("#qrClose")
        # 7 save, remove, restore
        pg.click("#save")
        sv = wait_for(lambda: [f for f in os.listdir(T + "/FrogNet-assignments")] if os.path.isdir(T + "/FrogNet-assignments") else None, 10)
        saved = open(os.path.join(T, "FrogNet-assignments", sv[0])).read() if sv else ""
        pg.wait_for_function("document.querySelector('#saves').value", timeout=5000)
        pg.click("[data-del-team=echo]"); pg.click("#apply")
        gone = wait_for(lambda: all("echo" not in c["bag"].get("users", {}) and "echo" not in c["bag"].get("roles", {}) for c in rm.read("fdf-app", "config") if c["bag"].get("admin", {}).get("check")), 20)
        pg.wait_for_function("document.querySelector('#changes').textContent.indexOf('adopted by 2') >= 0", timeout=20000)
        pg.click("#restore")
        pg.wait_for_function("/Team echo/.test(document.querySelector('#changes').textContent)", timeout=5000)
        chg7 = pg.inner_text("#changes"); pg.click("#apply")
        back = wait_for(leads_kim, 20)
        lg7 = door("op=login&app=openeoc", {"user": "echo", "password": "echo-2026"})
        check(bool(sv) and '"echo"' in saved and "verifier" in saved and "echo-2026" not in saved and gone and back and len(back) == 2 and lg7.get("role") == "echo",
              "7 saved %s (echo's verifier, no password); Team echo removed on every member: %s; restored ('%s') and applied: %d members say echo's login leads it; echo logs in as %s"
              % (sv, bool(gone), chg7[:70], len(back or []), lg7.get("role") or lg7.get("why")))
        # 8 [TEAMS_AUTHORIZED_V1] Team crew1 (no category) and EOC user dana
        pg.wait_for_function("document.querySelector('#changes').textContent.indexOf('adopted by 2') >= 0", timeout=20000)
        pg.fill("#newTeam", "crew1"); pg.fill("#newTeamPw", "crew1-2026"); pg.click("#addTeam")
        pg.fill("#newEoc", "dana"); pg.fill("#newEocPw", "dana-2026"); pg.click("#addEoc")
        chg8 = pg.inner_text("#changes"); pg.click("#apply")
        def crew1_everywhere():
            l = [c for c in rm.read("fdf-app", "config") if (c["bag"].get("roles", {}).get("crew1") or {}).get("category") == ""
                 and (c["bag"].get("users", {}).get("crew1") or {}).get("lead") and (c["bag"].get("users", {}).get("dana") or {}).get("role") == "eoc"]
            return l if len(l) == 2 else None
        adopted8 = wait_for(crew1_everywhere, 20)
        lg8 = door("op=login&app=openeoc", {"user": "crew1", "password": "crew1-2026"}); lg8d = door("op=login&app=openeoc", {"user": "dana", "password": "dana-2026"})
        if SHOTS: pg.screenshot(path=os.path.join(SHOTS, "admin-teams.png"), full_page=True)
        check(bool(adopted8) and lg8.get("role") == "crew1" and lg8d.get("role") == "eoc" and not PAGE_ERRORS,
              "8 '%s'; %d members adopted it; crew1 logs in as %s, dana as %s; page errors %s"
              % (chg8[:90], len(adopted8 or []), lg8.get("role") or lg8.get("why"), lg8d.get("role") or lg8d.get("why"), PAGE_ERRORS))
        # 10 the app's selection, from the admin's screen
        pg.wait_for_function("document.querySelector('#changes').textContent.indexOf('No changes') >= 0 || document.querySelector('#changes').textContent.indexOf('adopted') >= 0", timeout=20000)
        pg.fill("#newSel", "*"); pg.click("#addSel")
        pg.fill("[data-sel='*'][data-k=rank]", "-mem_total_kb"); pg.press("[data-sel='*'][data-k=rank]", "Tab")
        chg10 = pg.inner_text("#changes"); t0 = time.time(); pg.click("#apply")
        def on(prefix):
            return lambda: (lambda g: g if g.startswith(prefix) else None)(door("op=rams&app=openeoc").get("rams", {}).get("general", ""))
        low = wait_for(on("127.0.3.1:"), 30); dt10 = time.time() - t0
        cfgsel = [c["bag"].get("select") for c in rm.read("fdf-app", "config") if c["bag"].get("admin", {}).get("check")]
        pg.wait_for_function("document.querySelector('#changes').textContent.indexOf('No changes') >= 0 || document.querySelector('#changes').textContent.indexOf('adopted') >= 0", timeout=20000)
        pg.click("[data-del-sel='*']"); chg10b = pg.inner_text("#changes"); t1 = time.time(); pg.click("#apply")
        back = wait_for(on("127.0.2.1:"), 30); dt10b = time.time() - t1
        check(bool(low) and bool(back) and all(x == {"*": {"need": [], "rank": ["-mem_total_kb"]}} for x in cfgsel) and len(cfgsel) == 2,
              "10 '%s': general on Vashon1 %.1f s after (%s; the adopting members' select %s); '%s': back on Seattle5 %.1f s after (%s)"
              % (chg10[:80], dt10, low, cfgsel[:1], chg10b[:60], dt10b, back))
        # 11 Groups now
        row = lambda g: pg.evaluate("[...document.querySelectorAll('#groups tr')].map(r=>r.innerText).find(t=>t.startsWith(%s)) || ''" % json.dumps(g))
        g0 = {g: row(g) for g in ("echo", "crew1", "eoc")}
        door("op=place&app=openeoc", {"user": "echo", "password": "echo-2026", "at": "here", "guid": "phone-kim"})
        try: pg.wait_for_function("[...document.querySelectorAll('#groups tr')].some(r=>r.innerText.startsWith('echo') && r.innerText.indexOf(\"lead's device\")>=0 && r.innerText.indexOf('not a member')>=0)", timeout=20000); live1 = row("echo")
        except Exception: live1 = ""
        door("op=place&app=openeoc", {"user": "echo", "password": "echo-2026", "at": "databasehost"})
        try: pg.wait_for_function("[...document.querySelectorAll('#groups tr')].some(r=>r.innerText.startsWith('echo') && /SystemRam[\\s\\S]*127\\.0\\./.test(r.innerText))", timeout=20000); live2 = row("echo")
        except Exception: live2 = ""
        if SHOTS: pg.screenshot(path=os.path.join(SHOTS, "admin-groups.png"), full_page=True)
        check("its app's, at its claim" in g0["echo"] and "echo" in g0["echo"].split("\t")[4] and "crew1" in g0["crew1"].split("\t")[4] and "EOC" in g0["eoc"] and "no RAM of its own" in g0["eoc"] and bool(live1) and bool(live2),
              "11 Groups now: %s; echo places echo on its device: %s; on the SystemRam: %s" % (" | ".join(v.replace("\t", " / ") for v in g0.values())[:260], live1.replace("\t", " / ")[:120], live2.replace("\t", " / ")[:120]))
        br.close()
    # 9 the lead says where its team lives
    import queue
    class Dev:
        def __init__(self):
            self.q = queue.Queue(); self.lines = []
            self.p = subprocess.Popen(["java", "-cp", CP, "CoreDriver"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            procs.append(self.p); threading.Thread(target=self.read, daemon=True).start()
        def read(self):
            for l in self.p.stdout:
                try: o = json.loads(l)
                except ValueError: continue
                self.lines.append(o); self.q.put(o)
        def send(self, **o): self.p.stdin.write(json.dumps(o) + "\n"); self.p.stdin.flush()
        def wait(self, pred, t=30):
            end = time.time() + t
            for o in list(self.lines):
                if pred(o): return o
            while time.time() < end:
                try: o = self.q.get(timeout=max(0.05, end - time.time()))
                except queue.Empty: break
                if pred(o): return o
            return None
    LP = free(); procs.append(subprocess.Popen([T + "/bin/fdf-ram", "--listen", "127.0.0.1:%d" % LP, "--api", fdfram.API],   # the lead phone's RAM (as libteamram)
        env=dict(os.environ, RINA_REGION_FILE=T + "/lead.perm", RINA_NET_CONFIG=T + "/none"), stdout=subprocess.DEVNULL, stderr=open(T + "/lead-ram.log", "w")))
    kim = Dev(); kim.send(op="connect", door=DOOR, dev="kimphone", name="Kim", role="team", team="Echo")
    con = kim.wait(lambda o: o.get("ev") == "connected")
    kim.send(op="teamHost", port=LP, addr="127.0.0.1", card={}, user="echo", password="echo-2026")
    said = kim.wait(lambda o: "lives on this phone" in str(o.get("text", "")))
    def unserved():
        r = door("op=rams&app=openeoc")
        return r if "echo" not in r.get("rams", {}) and (r.get("places", {}).get("echo") or {}).get("at") == "here" else None
    here = wait_for(unserved, 20)
    t0 = time.time(); kim.send(op="teamPlace", at="databasehost", user="echo", password="echo-2026")
    served = kim.wait(lambda o: "served on databasehost now" in str(o.get("text", "")) or "not served yet" in str(o.get("text", "")), 40)
    dt = time.time() - t0
    after = door("op=rams&app=openeoc")
    check(con is not None and said is not None and bool(here) and served is not None and "served on databasehost now" in served.get("text", "")
          and "echo" in after.get("rams", {}) and (after.get("places", {}).get("echo") or {}).get("at") == "databasehost",
          "9 Host team: '%s'; op=rams then: echo %s, places %s; Team on databasehost: '%s' %.1f s after; op=rams: echo at %s, places %s"
          % ((said or {}).get("text", "")[:90], "unserved" if here else "still served", (here or {}).get("places"), (served or {}).get("text", "")[:110], dt,
             after.get("rams", {}).get("echo"), after.get("places")))
finally:
    for p in procs: p.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
