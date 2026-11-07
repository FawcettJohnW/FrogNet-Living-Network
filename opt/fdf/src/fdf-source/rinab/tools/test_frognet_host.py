#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""test_frognet_host.py -- [FROGNETHOST_ADMIN_V1][ROLES_ON_API_V1] the FrogNetHost Admin against two real role hosts:
two api.php (this tree's var/www/html, each on its own MariaDB database) on port 80 of two loopback addresses, as
databasehost-control and the SystemRam, named by a hosts file the test rewrites. The node's own .1 (10.99.99.1 on its veth fnt0,
frognet_tuples.my_ip) serves held_fire.php on port 80, where the role hosts fire the held reads. The node's service (frognet-host
serve) runs with its steps stood in for (FROGNET_HOST_CMD_*: setup rewrites gateways.conf as setup_lillypad does, dump
and backup write their file, clear and reboot record that they ran); the console asks and waits for the answer.

  1  the service writes its state into the SystemRam at start
  2  setup NAME GW: applied (gateways.conf now says them), answered in the state, setup 1
  3  clear, dump, backup: each run once, answered with its number; dump and backup name their file
  4  a second clear is clear 2, and the clear step ran exactly twice
  5  the Admin's tuple removed from databasehost-control: the next clear is still 3 (numbered past the node's answer)
  6  the SystemRam moves (the hosts file names the other RAM host): the node's state is written there, at once
  7  status lists the node from the new SystemRam
  8  databasehost-control moves too: the Admin's tuple written in the new one is applied (clear 4)
  9  the Admin UI's page API: hosts, host-request, host-wait (the held read on the answer)
 10  rule 7: the node holds ONE read -- its config tuple at databasehost-control -- and none on the SystemRam
Needs: root (port 80, /run/frognet/held), MariaDB running with root access by socket, php with mysqli and curl.
usage: tools/test_frognet_host.py
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time

H = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F = os.path.join(H, "..", "fdf")
B = sys.argv[1] if len(sys.argv) > 1 else os.path.join(H, "build", "static")
sys.path.insert(0, os.path.join(F, "lib", "python"))
os.environ["FROGNET_CORE"] = os.path.normpath(os.path.join(H, "..", "..", "..", "..", "frognet_semantic", "core"))   # this tree's
import fdfram  # noqa: E402

FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)

def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

T = tempfile.mkdtemp(prefix="frognet-host-")
procs = []
WWW = os.path.normpath(os.path.join(H, "..", "..", "..", "..", "..", "var", "www", "html"))
PW = "fhtest" + str(os.getpid())
def serve_www(ip, files, db=None):
    """php -S on ip:80 serving `files` from this tree's var/www/html; api.php on its own database `db`"""
    d = os.path.join(T, "www-" + ip)
    os.makedirs(d)
    for f in files:
        shutil.copy(os.path.join(WWW, f), d)
    if db:
        cfg = (open(os.path.join(WWW, "config.php")).read().replace("__FROGNET_DB_PASS__", PW)
               .replace("define('DB_NAME', 'FrogNet');", "define('DB_NAME', '%s');" % db)
               .replace("define('DB_USER', 'FrogUser');", "define('DB_USER', 'FhTestUser');"))    # the test's own user
        open(os.path.join(d, "config.php"), "w").write(cfg)
        sql = ("DROP DATABASE IF EXISTS %s; CREATE DATABASE %s;" % (db, db) +
               "".join("CREATE USER IF NOT EXISTS 'FhTestUser'@'%s' IDENTIFIED BY '%s'; ALTER USER 'FhTestUser'@'%s' IDENTIFIED BY '%s'; "
                       "GRANT ALL ON %s.* TO 'FhTestUser'@'%s';" % (h, PW, h, PW, db, h) for h in ("localhost", "127.0.0.1")))
        subprocess.run(["mysql", "-e", sql], check=True)
        for f in ("Create_Database.sql", "schema_fixups.sql"):
            subprocess.run("sed 's/`FrogNet`/`%s`/g; s/^USE FrogNet;/USE %s;/' %s | mysql" % (db, db, os.path.join(WWW, f)), shell=True, check=True,
                           stdout=subprocess.DEVNULL)
    procs.append(subprocess.Popen(["php", "-S", "%s:80" % ip, "-t", d], env=dict(os.environ, PHP_CLI_SERVER_WORKERS="4"),
                                  stdout=subprocess.DEVNULL, stderr=open(T + "/php-%s.log" % ip, "w")))
    for _ in range(100):
        try: socket.create_connection((ip, 80), 0.2).close(); return
        except OSError: time.sleep(0.05)
subprocess.run(["ip", "addr", "add", "127.0.0.2/8", "dev", "lo"], capture_output=True)
subprocess.run(["ip", "addr", "add", "127.0.0.3/8", "dev", "lo"], capture_output=True)
serve_www("127.0.0.2", ["api.php", "config.php"], db="fhtest_a")       # a role host: api.php on port 80
serve_www("127.0.0.3", ["api.php", "config.php"], db="fhtest_b")
# this node's own .1 on its LAN (a veth pair: its FrogNet address, as frognet_tuples.my_ip finds it -- not lo)
if subprocess.run(["ip", "-o", "addr", "show", "fnt0"], capture_output=True, text=True).stdout.find("10.99.99.1/") < 0:
    subprocess.run(["ip", "link", "add", "fnt0", "type", "veth", "peer", "name", "fnt0p"], capture_output=True)
    subprocess.run(["ip", "addr", "add", "10.99.99.1/24", "dev", "fnt0"], capture_output=True)
    for d in ("fnt0p", "fnt0"): subprocess.run(["ip", "link", "set", d, "up"], capture_output=True)
sys.path.insert(0, os.environ["FROGNET_CORE"])
import frognet_tuples  # noqa: E402
ME = frognet_tuples.my_ip()                                           # the address holds fire to and tuples are written as
serve_www(ME, ["held_fire.php"])                                      # this node's port 80: where its holds fire

hosts = T + "/hosts"
def name_hosts(control_ip, system_ip):
    with open(hosts + ".tmp", "w") as f:
        f.write("127.0.0.1 localhost\n%s databasehost_control.frognet\n%s databasehost.frognet\n" % (control_ip, system_ip))
    os.replace(hosts + ".tmp", hosts)
name_hosts("127.0.0.2", "127.0.0.2")
open(T + "/fnid", "w").write("guid-node-one\n")
gw = T + "/gateways.conf"
open(gw, "w").write("NETWORK_NAME=OldName\nGATEWAY_IP=10.9.9.1\n")
os.makedirs(T + "/bin")
def stub(name, body):
    p = T + "/bin/" + name; open(p, "w").write("#!/bin/sh\n" + body + "\n"); os.chmod(p, 0o755); return p
setup = stub("setup", 'printf "NETWORK_NAME=%%s\\nGATEWAY_IP=%%s\\n" "$1" "$2" > %s; echo "lillypad $1 $2"' % gw)
clear = stub("clear", 'echo x >> %s/clears' % T)
dump = stub("dump", 'echo "-- dump" > "$1"')
backup = stub("backup", 'echo tgz > "$1"')
reboot = stub("reboot", 'echo x >> %s/reboots' % T)

env = dict(os.environ, FDF_HOSTS=hosts, FROGNET_HOSTS=hosts, FROGNET_GUID_FILE=T + "/fnid", FROGNET_GATEWAYS_CONF=gw,
           FROGNET_HOST_RECORD=T + "/record.json", FROGNET_HOST_DIR=T + "/out", FDF_VAR=T + "/var",
           FDF_PY=os.path.join(F, "lib", "python"), FROGNET_HOST_WAIT_S="20",
           FROGNET_HOST_CMD_SETUP=setup, FROGNET_HOST_CMD_CLEAR=clear, FROGNET_HOST_CMD_DUMP=dump,
           FROGNET_HOST_CMD_BACKUP=backup, FROGNET_HOST_CMD_REBOOT=reboot)
fh = os.path.join(F, "bin", "frognet-host")
def console(*a):
    p = subprocess.run([sys.executable, fh] + list(a), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    return p.returncode, (p.stdout + p.stderr).strip()
def state(ip):
    cs = [c for c in fdfram.RoleRam(ip).read("frognethost", "state", "guid-node-one") if not c.get("gone")]
    return cs[0]["bag"] if cs else {}                                  # the newest first
def wait_for(pred, limit=10.0):
    t0 = time.time()
    while time.time() - t0 < limit:
        v = pred()
        if v: return v, time.time() - t0
        time.sleep(0.05)
    return None, time.time() - t0

try:
    serve = subprocess.Popen([sys.executable, fh, "serve"], env=env, stdout=open(T + "/serve.log", "w"), stderr=subprocess.STDOUT)
    procs.append(serve)
    s, dt = wait_for(lambda: state("127.0.0.2"))
    check(bool(s) and s.get("name") == "OldName", "1 the service wrote its state into the SystemRam at start (%.0f ms): %s %s"
          % (dt * 1000, s and s.get("name"), s and s.get("gateway")))

    rc, out = console("setup", "NewName", "10.42.42.1")
    conf = open(gw).read()
    check(rc == 0 and "NETWORK_NAME=NewName" in conf and "GATEWAY_IP=10.42.42.1" in conf and state("127.0.0.2").get("done", {}).get("setup") == 1,
          "2 setup applied (gateways.conf: %s) and answered as setup 1: %s" % (conf.replace("\n", " ").strip(), out.splitlines()[-1:]))

    res = {}
    for op in ("clear", "dump", "backup"):
        res[op] = console(op)
    files = [l for l in res["dump"][1].splitlines() + res["backup"][1].splitlines() if T + "/out/" in l]
    check(all(r[0] == 0 for r in res.values()) and len(files) == 2 and all(os.path.exists(l.split(", ")[-1].split(" (")[0]) for l in files),
          "3 clear, dump, backup each answered: %s" % [r[1].splitlines()[-1] for r in res.values()])

    rc, out = console("clear")
    n = len(open(T + "/clears").read().split())
    check(rc == 0 and "clear 2" in out and n == 2, "4 a second clear is clear 2; the clear step ran %d times: %s" % (n, out.splitlines()[-1]))

    c = fdfram.RoleRam("127.0.0.2")
    for cell in c.read("frognethost", "config", "guid-node-one"):
        if not cell.get("gone"): c.remove(cell)
    rc, out = console("clear")
    n = len(open(T + "/clears").read().split())
    check(rc == 0 and "clear 3" in out and n == 3, "5 the Admin's tuple gone: the next clear is clear 3, run once (%d runs): %s" % (n, out.splitlines()[-1]))

    t0 = time.time(); name_hosts("127.0.0.2", "127.0.0.3")
    s, dt = wait_for(lambda: state("127.0.0.3"))
    check(bool(s) and s.get("name") == "NewName" and s.get("done", {}).get("clear") == 3,
          "6 the SystemRam moved to the other role host: the node's state written there %.0f ms after the hosts file named it" % (dt * 1000))

    rc, out = console("status")
    check(rc == 0 and "NewName" in out and "127.0.0.3" in out, "7 status from the new SystemRam: %s" % out.replace("\n", " | ")[:200])
    name_hosts("127.0.0.3", "127.0.0.3")
    rc, out = console("clear")
    n = len(open(T + "/clears").read().split())
    check(rc == 0 and "clear 4" in out and n == 4, "8 databasehost-control moved too: the Admin's tuple written there is applied, clear 4 (%d runs): %s"
          % (n, out.splitlines()[-1]))
    # 9  the Admin UI's page API (frognet_setup_v4_api.php under php -S): the list, a request, the held read on its answer
    www = WWW
    wport = free()
    procs.append(subprocess.Popen(["php", "-S", "127.0.0.1:%d" % wport, "-t", www], env=dict(env, FROGNET_HOST_BIN=fh),
                                  stdout=subprocess.DEVNULL, stderr=open(T + "/php.log", "w")))
    import urllib.request
    for _ in range(100):
        try: socket.create_connection(("127.0.0.1", wport), 0.2).close(); break
        except OSError: time.sleep(0.05)
    api = "http://127.0.0.1:%d/frognet_setup_v4_api.php?action=" % wport
    hl = json.loads(urllib.request.urlopen(api + "hosts", timeout=20).read())
    rq = json.loads(urllib.request.urlopen(urllib.request.Request(api + "host-request", data=json.dumps({"node": "guid-node-one", "op": "dump"}).encode(),
                                                                  headers={"Content-Type": "application/json"}, method="POST"), timeout=20).read())
    wt = json.loads(urllib.request.urlopen(api + "host-wait&node=guid-node-one&op=dump&seq=%d" % rq.get("seq", 0), timeout=30).read())
    check(hl.get("ok") and [h.get("name") for h in hl.get("hosts", [])] == ["NewName"] and rq.get("seq") == 2 and (wt.get("last") or {}).get("ok"),
          "9 the page API: hosts %s; host-request dump -> seq %s; host-wait -> %s" % ([h.get("name") for h in hl.get("hosts", [])], rq.get("seq"), wt.get("last")))
    # 10 rule 7: the node's service holds one read, on its config tuple at databasehost-control, and none on the
    #    SystemRam (the console's reads are the console's: they are not the node's)
    p = subprocess.run(["mysql", "-N", "-e", "SELECT SensorType, NameExact, NameLike, HolderID FROM fhtest_b.HeldRead"], capture_output=True, text=True)
    hb = [l.split("\t") for l in p.stdout.strip().splitlines() if l.strip()]
    mine = [h for h in hb if h[3] == "frognet-host.%d" % serve.pid]
    check(len(mine) == 1 and mine[0][:2] == ["frognethost", "config.guid-node-one"],
          "10 the node's service holds one read, (frognethost, config, guid-node-one), and none on its state: %s" % mine)
finally:
    for p in procs:
        if p.poll() is None: p.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS))
sys.exit(1 if FAILS else 0)
