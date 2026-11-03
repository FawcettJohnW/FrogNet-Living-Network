#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_admin.py [SHOTS_DIR] -- [ADMIN_APP_V1] the RangerTrak admin app (its real core, AdminCore, and its real page,
# through DevHost) against a real network RAM (rina-host), three real members (fdf-client, fdf-app serve) and the real
# door (/dc/ram.php under PHP, its `ram` CLI the real one). The members were installed with
#   fdf-app config rangertrak general,alpha,bravo --role eoc --role alpha:default --role bravo:default
#       --user EOCAdmin:EOCAdmin=eoc --admin boss:adm-2026
#   1 a wrong admin password is refused, on this device (no request carries it)
#   2 the admin's screen: the roles, the users, every member and its configuration as current
#   3 the admin adds team echo (its own RAM, no login) and user kim (role echo, a password) and applies: every member
#     says adopted, on the screen; the door's op=roles has echo; kim's password logs in at the door as echo; the echo
#     RAM is served; no password and no admin key in the network RAM
#   4 a member installed without --admin says why it did not adopt, on the screen
#   5 [ENROLL_V1] the admin makes kim echo's lead: the members' configurations say so; at the door, op=enroll with a
#     wrong password is refused, op=identity by a user who leads no team is refused, and kim's op=identity gets past
#     the check to the region (none here, so the door says the region did not give the identity)
#   6 [FROG_QR_V1] kim's QR on the admin's screen reads, by the phone scanner's zxing, as kim's enrollment line
#   7 [ASSIGNMENTS_SAVE_V1] Save writes the assignments (verifiers, no password) to a file; kim is removed and applied;
#     Restore loads the file as an edit, Apply sends it: kim is back as echo's lead and logs in with the old password
#   8 [CATEGORIES_V1] the roles of a configuration made before categories are on the Teams tab; the admin adds the
#     category Power, its group crew1 (own RAM) and its lead pat, and applies: the Power tab shows crew1 and pat, Teams
#     does not; every member with the admin key adopts it; the door's op=roles says crew1 is in Power and the tabs'
#     order (Teams, Power); pat logs in at the door as crew1
import hashlib, hmac, json, os, shutil, socket, subprocess, sys, tempfile, threading, time
from urllib.request import urlopen, Request
from playwright.sync_api import sync_playwright

SHOTS = sys.argv[1] if len(sys.argv) > 1 else ""
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE); SRC = os.path.dirname(APP)
F = os.path.join(SRC, "fdf-source", "fdf"); B = os.path.join(SRC, "fdf-source", "rinab", "build", "static")
sys.path.insert(0, os.path.join(F, "lib", "python"))
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

T = tempfile.mkdtemp(prefix="rt-admin-"); os.makedirs(T + "/bin")
for b in ("rina-host", "fdf-client", "fdf-where", "fdf-events", "ram"): shutil.copy(os.path.join(B, b), T + "/bin/")
procs = []
NET = "127.0.0.1:%d" % free()
procs.append(subprocess.Popen([T + "/bin/rina-host", "--listen", NET, "--api", fdfram.API],
                              env=dict(os.environ, RINA_REGION_FILE=T + "/net.perm", RINA_NET_CONFIG=T + "/none"),
                              stdout=subprocess.DEVNULL, stderr=open(T + "/net.log", "w")))
time.sleep(0.5); rm = fdfram.Ram(NET)
MEM = {"a": 2_000_000, "b": 4_000_000, "c": 1_000_000}
def env(n):
    return dict(os.environ, RINA_RAM=NET, FDF_FACTS=T + "/%s/facts.json" % n, FDF_VAR=T + "/" + n, FDF_APPS=T + "/%s/apps" % n,
                FDF_BIN=T + "/bin", FDF_PY=os.path.join(F, "lib", "python"), FDF_NAME="Seattle" + n)
for i, n in enumerate("abc", start=1):
    d = T + "/" + n; os.makedirs(d + "/apps")
    json.dump({"name": {"a": "Seattle7", "b": "Seattle5", "c": "Vashon1"}[n], "guid": "guid-" + n, "role": "node", "subnet": "127.0.%d.0/24" % i,
               "addrs": [{"ip": "127.0.%d.1" % i, "kind": "own"}], "capability": {"mem_total_kb": MEM[n], "cores": 4}}, open(d + "/facts.json", "w"))
    procs.append(subprocess.Popen([T + "/bin/fdf-client"], env=env(n), stdout=open(d + "/client.log", "w"), stderr=subprocess.STDOUT))
time.sleep(1.5)
for n in "abc":   # c: installed with no admin (an older install line)
    subprocess.run([os.path.join(F, "bin", "fdf-app"), "config", "rangertrak", "general,alpha,bravo", "--role", "eoc", "--role", "alpha:default",
                    "--role", "bravo:default", "--user", "EOCAdmin:EOCAdmin=eoc"] + (["--admin", "boss:adm-2026"] if n != "c" else []),
                   env=env(n), check=True, stdout=subprocess.DEVNULL)
    procs.append(subprocess.Popen([os.path.join(F, "bin", "fdf-app"), "serve"], env=env(n), stdout=open(T + "/%s/app.log" % n, "w"), stderr=subprocess.STDOUT))
# the door, as a node serves it: /dc/ram.php, fdf.env beside www (member a's: its RAM, its app-where.json)
os.makedirs(T + "/door/www/dc")
shutil.copy(os.path.join(F, "www", "dc", "ram.php"), T + "/door/www/dc/ram.php")
json.dump({"apps": []}, open(T + "/door/www/dc/apps.json", "w"))
open(T + "/door/fdf.env", "w").write("export RINA_RAM=%s FDF_BIN=%s FDF_VAR=%s\n" % (NET, T + "/bin", T + "/a"))
DP = free(); DOOR = "127.0.0.1:%d" % DP
procs.append(subprocess.Popen(["php", "-S", DOOR, "-t", T + "/door/www"], stdout=open(T + "/door.log", "w"), stderr=subprocess.STDOUT,
                              env=dict(os.environ, PHP_CLI_SERVER_WORKERS="8")))
def door(q, body=None):
    r = Request("http://%s/dc/ram.php?%s" % (DOOR, q), data=json.dumps(body).encode() if body is not None else None,
                headers={"Content-Type": "application/json"})
    try: return json.loads(urlopen(r, timeout=30).read())
    except Exception as e: return {"ok": False, "why": str(e) + " " + getattr(e, "read", lambda: b"")().decode()[:200]}
ok = wait_for(lambda: door("op=roles&app=rangertrak").get("ok") and json.load(open(T + "/a/app-where.json")) if os.path.exists(T + "/a/app-where.json") else None, 20)
CP = os.pathsep.join([os.path.join(SRC, "frognet-ram-java", "build", "frognet-ram.jar"), os.path.join(APP, "build", "core"), os.path.join(APP, "build", "test")])
HP = free()
procs.append(subprocess.Popen(["java", "-Drt.core=admin", "-Duser.home=" + T, "-cp", CP, "DevHost", os.path.join(APP, "ui-admin"), str(HP), "x", "x", DOOR],
                              stdout=open(T + "/devhost.log", "w"), stderr=subprocess.STDOUT))
time.sleep(2)
try:
    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_context(viewport={"width": 1280, "height": 900}).new_page()
        pg.goto("http://127.0.0.1:%d/" % HP); pg.wait_for_selector("#connect:not([hidden])", timeout=8000)
        pg.fill("[name=door]", DOOR); pg.fill("[name=user]", "boss"); pg.fill("[name=password]", "wrong"); pg.click("#connect .go")
        pg.wait_for_function("/not rangertrak's admin/.test(document.querySelector('#cstatus').textContent)", timeout=15000)
        check(pg.is_visible("#connect"), "1 a wrong admin password is refused on this device: " + pg.inner_text("#cstatus"))
        pg.fill("[name=password]", "adm-2026"); pg.click("#connect .go")
        pg.wait_for_selector("#main:not([hidden])", timeout=15000)
        pg.wait_for_function("document.querySelectorAll('#members tr').length === 3", timeout=10000)
        roles = pg.evaluate("[...document.querySelectorAll('#roles tr td:first-child b')].map(b=>b.textContent)")
        mem = pg.inner_text("#members")
        check(sorted(roles) == ["alpha", "bravo", "eoc"] and "EOCAdmin" in pg.inner_text("#users") and mem.count("current") == 3 and "no admin key" in mem,
              "2 the admin's screen: roles %s, users EOCAdmin, 3 members current (Vashon1 without an admin key): %s" % (roles, " | ".join(l for l in mem.splitlines() if l.strip())[:220]))
        if SHOTS: pg.screenshot(path=os.path.join(SHOTS, "admin-before.png"), full_page=True)
        pg.fill("#newRole", "echo"); pg.click("#addRole")
        pg.fill("#newUser", "kim"); pg.click("#addUser"); pg.select_option("[data-u=kim]", "echo"); pg.fill("[data-pw=kim]", "kim-2026")
        chg = pg.inner_text("#changes")
        pg.click("#apply")
        pg.wait_for_function("(document.querySelector('#members').textContent.match(/adopted/g)||[]).length === 2 && /refused/.test(document.querySelector('#members').textContent)", timeout=20000)
        mem2 = pg.inner_text("#members")
        if SHOTS: pg.screenshot(path=os.path.join(SHOTS, "admin-applied.png"), full_page=True)
        ro = door("op=roles&app=rangertrak")
        lg = door("op=login&app=rangertrak", {"user": "kim", "password": "kim-2026"})
        echo = wait_for(lambda: door("op=rams&app=rangertrak").get("rams", {}).get("echo"), 15)
        txt = json.dumps([c["bag"] for c in rm.read("fdf-app", "config")] + [c["bag"] for c in rm.read("fdf-app", "admin")])
        k = hashlib.pbkdf2_hmac("sha256", b"adm-2026", hashlib.sha256(b"fdf-app-admin:rangertrak:boss").digest()[:16], 200000).hex()
        check("echo" in ro.get("roles", {}) and ro["roles"]["echo"]["ram"] and lg.get("role") == "echo" and bool(echo) and "kim-2026" not in txt and k not in txt,
              "3 '%s' applied: two members adopted it, on the screen; op=roles has echo (%s); kim logs in at the door as %s; echo served at %s; "
              "no password, no admin key in the network RAM" % (chg[:90], ro.get("roles", {}).get("echo"), lg.get("role") or lg.get("why"), echo))
        check("no admin key on this member" in mem2, "4 the member installed without --admin says why it did not adopt: " +
              [l for l in mem2.splitlines() if "refused" in l or "no admin key" in l][:2].__repr__()[:200])

        # 5 kim leads echo
        def leads_kim():   # both members with the admin key say kim leads
            l = [c for c in rm.read("fdf-app", "config") if c["bag"].get("users", {}).get("kim", {}).get("lead")]
            return l if len(l) == 2 else None
        pg.check("[data-lead=kim]"); chg5 = pg.inner_text("#changes"); pg.click("#apply")
        lead = wait_for(leads_kim, 20)
        bad = door("op=enroll&app=rangertrak", {"user": "kim", "password": "nope"})
        notlead = door("op=identity&app=rangertrak", {"user": "EOCAdmin", "password": "EOCAdmin", "name": "x1"})
        kid = door("op=identity&app=rangertrak", {"user": "kim", "password": "kim-2026", "name": "x1"})
        check(lead and len(lead) == 2 and "not this app" in bad.get("why", "") and "does not lead" in notlead.get("why", "")
              and "did not give rangertrak-echo-x1 an identity" in kid.get("why", ""),
              "5 '%s': %d members say kim leads; a wrong password: %s; a non-lead: %s; kim gets to the region: %s"
              % (chg5[:60], len(lead or []), bad.get("why", "")[:50], notlead.get("why", "")[:60], kid.get("why", "")[:80]))
        # 6 kim's QR
        pg.wait_for_function("document.querySelector('#changes').textContent.indexOf('adopted by 2') >= 0", timeout=20000)
        pg.click("[data-qr=kim]"); pg.wait_for_selector("#qr:not([hidden]) #qrImg svg", timeout=5000)
        shot = os.path.join(T, "kim-qr.png"); pg.locator("#qrImg svg").screenshot(path=shot)
        if SHOTS: pg.screenshot(path=os.path.join(SHOTS, "admin-qr.png"))
        got = zxing_read.read([shot]).get(shot)
        want = "fdfenroll1 http://%s/dc/ram.php rangertrak kim" % DOOR
        check(got == want, "6 kim's QR reads as %r" % got)
        pg.click("#qrClose")
        # 7 save, remove, restore
        pg.click("#save")
        sv = wait_for(lambda: [f for f in os.listdir(T + "/FrogNet-assignments")] if os.path.isdir(T + "/FrogNet-assignments") else None, 10)
        saved = open(os.path.join(T, "FrogNet-assignments", sv[0])).read() if sv else ""
        pg.wait_for_function("document.querySelector('#saves').value", timeout=5000)
        pg.click("[data-del-user=kim]"); pg.click("#apply")
        gone = wait_for(lambda: all("kim" not in c["bag"].get("users", {}) for c in rm.read("fdf-app", "config") if c["bag"].get("admin", {}).get("check")), 20)
        pg.wait_for_function("document.querySelector('#changes').textContent.indexOf('adopted by 2') >= 0", timeout=20000)
        pg.click("#restore")
        pg.wait_for_function("/user kim/.test(document.querySelector('#changes').textContent)", timeout=5000)
        chg7 = pg.inner_text("#changes"); pg.click("#apply")
        back = wait_for(leads_kim, 20)
        lg7 = door("op=login&app=rangertrak", {"user": "kim", "password": "kim-2026"})
        check(bool(sv) and '"kim"' in saved and "verifier" in saved and "kim-2026" not in saved and gone and back and len(back) == 2 and lg7.get("role") == "echo",
              "7 saved %s (kim's verifier, no password); kim removed on every member: %s; restored ('%s') and applied: %d members say kim leads; kim logs in as %s"
              % (sv, bool(gone), chg7[:70], len(back or []), lg7.get("role") or lg7.get("why")))
        # 8 categories: Power, its group crew1 and its lead pat
        pg.wait_for_function("document.querySelector('#changes').textContent.indexOf('adopted by 2') >= 0", timeout=20000)
        tabs0 = pg.evaluate("[...document.querySelectorAll('#tabs button[data-tab]')].map(b=>b.dataset.tab)")
        pg.fill("#newCat", "Power"); pg.click("#addCat")
        pg.fill("#newRole", "crew1"); pg.click("#addRole")
        pg.fill("#newUser", "pat"); pg.click("#addUser"); pg.fill("[data-pw=pat]", "pat-2026"); pg.check("[data-lead=pat]")
        power_roles = pg.evaluate("[...document.querySelectorAll('#roles tr td:first-child b')].map(b=>b.textContent)")
        power_users = pg.evaluate("[...document.querySelectorAll('#users tr td:first-child b')].map(b=>b.textContent)")
        chg8 = pg.inner_text("#changes"); pg.click("#apply")
        def crew1_everywhere():
            l = [c for c in rm.read("fdf-app", "config") if (c["bag"].get("roles", {}).get("crew1") or {}).get("category") == "Power"
                 and (c["bag"].get("users", {}).get("pat") or {}).get("lead")]
            return l if len(l) == 2 else None
        adopted8 = wait_for(crew1_everywhere, 20)
        pg.click("[data-tab=Teams]")
        teams_roles = pg.evaluate("[...document.querySelectorAll('#roles tr td:first-child b')].map(b=>b.textContent)")
        ro8 = door("op=roles&app=rangertrak"); lg8 = door("op=login&app=rangertrak", {"user": "pat", "password": "pat-2026"})
        if SHOTS: pg.click("[data-tab=Power]"); pg.screenshot(path=os.path.join(SHOTS, "admin-power.png"), full_page=True) if SHOTS else None
        check(tabs0 == ["Teams"] and power_roles == ["crew1"] and power_users == ["pat"] and "crew1" not in teams_roles and bool(adopted8)
              and (ro8.get("roles", {}).get("crew1") or {}).get("category") == "Power" and ro8.get("categories") == ["Teams", "Power"]
              and (ro8.get("roles", {}).get("echo") or {}).get("category") == "Teams" and lg8.get("role") == "crew1",
              "8 tabs before %s; '%s'; the Power tab: groups %s, users %s; Teams: %s; %d members adopted; op=roles crew1 %s, categories %s; pat logs in as %s"
              % (tabs0, chg8[:90], power_roles, power_users, teams_roles, len(adopted8 or []), ro8.get("roles", {}).get("crew1"), ro8.get("categories"), lg8.get("role") or lg8.get("why")))
        br.close()
finally:
    for p in procs: p.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
