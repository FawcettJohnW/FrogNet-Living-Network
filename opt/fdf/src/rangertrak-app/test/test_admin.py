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
import hashlib, hmac, json, os, shutil, socket, subprocess, sys, tempfile, threading, time
from urllib.request import urlopen, Request
from playwright.sync_api import sync_playwright

SHOTS = sys.argv[1] if len(sys.argv) > 1 else ""
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE); SRC = os.path.dirname(APP)
F = os.path.join(SRC, "fdf-source", "fdf"); B = os.path.join(SRC, "fdf-source", "rinab", "build", "static")
sys.path.insert(0, os.path.join(F, "lib", "python"))
import fdfram  # noqa: E402

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
procs.append(subprocess.Popen(["java", "-Drt.core=admin", "-cp", CP, "DevHost", os.path.join(APP, "ui-admin"), str(HP), "x", "x", DOOR],
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
        br.close()
finally:
    for p in procs: p.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
