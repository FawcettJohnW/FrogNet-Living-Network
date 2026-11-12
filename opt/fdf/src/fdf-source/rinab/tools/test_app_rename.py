#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_app_rename.py -- [APP_RENAMED_V1] `fdf-app rename OLD NEW --admin USER:PASSWORD` (RangerTrak -> OpenEOC, John
# 2026-11-08), on the roles as nodes have them [ROLES_ON_API_V1]: one role host (this tree's api.php on 127.0.0.2:80, its
# own MariaDB database) for databasehost-control and the SystemRam, this machine's own .1 (10.99.99.1 on its veth fnt0)
# serving held_fire.php. Two machines, a and b, have the app installed as `rangertrak` (a by config, its Admin; b by
# install with the admin's password).
#   1 a's rename: openeoc's one configuration tuple is written, signed with the admin key derived for openeoc; a's copy
#     of openeoc is signed and carries rangertrak's RAMs, ports, roles, users' verifiers and the teams-join verifier;
#     rangertrak's copy is out of FDF_APPS (FDF_VAR/retired-rangertrak.json)
#   2 b's rename: b adopts openeoc's tuple (no second tuple written); its copy is the one configuration
#   3 a wrong admin password renames nothing: rangertrak stays installed, no openeoc copy
#   4 the admin app's key for openeoc (derived from the password) checks against the tuple's admin
# Needs root (port 80, the veth), php (mysqli, curl), MariaDB root by its socket.  usage: tools/test_app_rename.py
import hashlib, hmac, json, os, shutil, socket, subprocess, sys, tempfile, time

H = os.path.dirname(os.path.abspath(__file__))
F = os.path.normpath(os.path.join(H, "..", "..", "fdf"))
FN = os.path.normpath(os.path.join(H, "..", "..", "..", "..", "..", "frognet_semantic"))
CORE = os.path.join(FN, "core"); os.environ["FROGNET_CORE"] = CORE
sys.path.insert(0, os.path.join(F, "lib", "python")); sys.path.insert(0, CORE)
import fdfram  # noqa: E402
import frognet_tuples  # noqa: E402

FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what); ok or FAILS.append(what)

T = tempfile.mkdtemp(prefix="app-rename-"); procs = []
WWW = os.path.normpath(os.path.join(FN, "..", "..", "var", "www", "html")); PW = "rntest" + str(os.getpid())
def serve_www(ip, files, db=None):
    d = os.path.join(T, "www-" + ip); os.makedirs(d)
    for f in files: shutil.copy(os.path.join(WWW, f), d)
    if db:
        open(os.path.join(d, "config.php"), "w").write(open(os.path.join(WWW, "config.php")).read().replace("__FROGNET_DB_PASS__", PW)
            .replace("define('DB_NAME', 'FrogNet');", "define('DB_NAME', '%s');" % db).replace("define('DB_USER', 'FrogUser');", "define('DB_USER', 'RnTestUser');"))
        subprocess.run(["mysql", "-e", "DROP DATABASE IF EXISTS %s; CREATE DATABASE %s;" % (db, db) + "".join(
            "CREATE USER IF NOT EXISTS 'RnTestUser'@'%s' IDENTIFIED BY '%s'; ALTER USER 'RnTestUser'@'%s' IDENTIFIED BY '%s'; GRANT ALL ON %s.* TO 'RnTestUser'@'%s';"
            % (h, PW, h, PW, db, h) for h in ("localhost", "127.0.0.1"))], check=True)
        for f in ("Create_Database.sql", "schema_fixups.sql"):
            subprocess.run("sed 's/`FrogNet`/`%s`/g; s/^USE FrogNet;/USE %s;/' %s | mysql" % (db, db, os.path.join(WWW, f)), shell=True, check=True, stdout=subprocess.DEVNULL)
    procs.append(subprocess.Popen(["php", "-S", "%s:80" % ip, "-t", d], env=dict(os.environ, PHP_CLI_SERVER_WORKERS="4"), start_new_session=True,
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
serve_www("127.0.0.2", ["api.php", "config.php"], db="rntest_a")
serve_www(frognet_tuples.my_ip(), ["held_fire.php"])
HOSTS = T + "/hosts"
open(HOSTS, "w").write("127.0.0.1 localhost\n127.0.0.2 databasehost_control.frognet\n127.0.0.2 databasehost.frognet\n")
rm = fdfram.RoleRam("127.0.0.2")

def env(n):
    d = T + "/" + n; os.makedirs(d + "/apps", exist_ok=True)
    return dict(os.environ, FDF_HOSTS=HOSTS, FDF_APPS=d + "/apps", FDF_VAR=d + "/var", FDF_FACTS=d + "/facts.json",
                FDF_PY=os.path.join(F, "lib", "python"), FROGNET_CORE=CORE, FDF_APP_RESTART="true")
def app(n, *args):
    r = subprocess.run([os.path.join(F, "bin", "fdf-app")] + list(args), env=env(n), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.returncode, (r.stdout + r.stderr).strip()
def copy(n, a):
    try: return json.load(open(T + "/%s/apps/%s.json" % (n, a)))
    except OSError: return None
def configs(a):
    return [c for c in rm.read("fdf-app", "config", a) if not c.get("gone") and c.get("bag")]

try:
    rc, out = app("a", "config", "rangertrak", "general,alpha", "--role", "eoc", "--role", "alpha:default",
                  "--user", "EOCAdmin:eoc-pw=eoc", "--join", "teams-2026", "--admin", "boss:adm-2026")
    assert rc == 0, out
    rc, out = app("b", "install", "rangertrak", "--admin", "boss:adm-2026"); assert rc == 0, out
    old = copy("a", "rangertrak")

    rc3, out3 = app("a", "rename", "rangertrak", "openeoc", "--admin", "boss:wrong")
    check(rc3 != 0 and copy("a", "rangertrak") and not copy("a", "openeoc") and not configs("openeoc"),
          "3 a wrong admin password renames nothing: rangertrak stays, no openeoc copy, no tuple (%s)" % out3.splitlines()[-1][:120])

    rc, out = app("a", "rename", "rangertrak", "openeoc", "--admin", "boss:adm-2026")
    new, cs = copy("a", "openeoc"), configs("openeoc")
    carried = new and all(new.get(k) == old.get(k) for k in ("rams", "ports", "roles", "users", "join"))
    check(rc == 0 and new and new.get("signed") and len(cs) == 1 and carried and not copy("a", "rangertrak")
          and os.path.exists(T + "/a/var/retired-rangertrak.json"),
          "1 a's rename: openeoc's one tuple written (seq %s), a's copy signed, RAMs %s ports %s roles %s users %s join %s carried; "
          "rangertrak retired: %s" % (cs and cs[0]["bag"].get("seq"), new and new.get("rams"), new and new.get("ports"), new and sorted(new.get("roles") or {}),
                                     new and sorted(new.get("users") or {}), bool(new and new.get("join")), out.splitlines()[-1][:120]))

    seq = cs and cs[0]["bag"].get("seq")
    rc, out = app("b", "rename", "rangertrak", "openeoc", "--admin", "boss:adm-2026")
    nb, cs2 = copy("b", "openeoc"), configs("openeoc")
    check(rc == 0 and nb and nb.get("seq") == seq and len(cs2) == 1 and cs2[0]["bag"].get("seq") == seq and not copy("b", "rangertrak"),
          "2 b's rename adopts openeoc's tuple (seq %s, still one tuple): %s" % (nb and nb.get("seq"), out.splitlines()[-1][:120]))

    k = hashlib.pbkdf2_hmac("sha256", b"adm-2026", hashlib.sha256(b"fdf-app-admin:openeoc:boss").digest()[:16], 200000)
    pub = (cs2 and cs2[0]["bag"].get("admin")) or {}
    check(hmac.compare_digest(hmac.new(k, b"fdf-admin-check", hashlib.sha256).hexdigest(), pub.get("check", "")) and "key" not in pub
          and "adm-2026" not in json.dumps(cs2),
          "4 the admin app's key for openeoc checks against the tuple's admin (%s); no key and no password in the tuple" % pub.get("user"))
finally:
    for p in procs:
        try: os.killpg(p.pid, 9)
        except OSError: pass
print("RESULT", "FAIL" if FAILS else "PASS")
sys.exit(1 if FAILS else 0)
