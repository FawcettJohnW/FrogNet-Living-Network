#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""test_frognet_host.py -- [FROGNETHOST_ADMIN_V1] the FrogNetHost Admin against two real RAM hosts (fdf-ram):
databasehost-control and the SystemRam, named by a hosts file the test rewrites. The node's service (frognet-host
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
 10  [RAM_PORT_CONFIGURABLE_V1] setup with a new RAM host port: written to the node's ram_port file, the services started
     again (stood in for), and the node on the new port: its state, with the port, in the SystemRam on that port
usage: tools/test_frognet_host.py [BUILD_DIR_WITH_fdf-ram]
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time

H = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F = os.path.join(H, "..", "fdf")
B = sys.argv[1] if len(sys.argv) > 1 else os.path.join(H, "build", "static")
sys.path.insert(0, os.path.join(F, "lib", "python"))
import fdfram  # noqa: E402

FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)

def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

T = tempfile.mkdtemp(prefix="frognet-host-")
procs = []
# two RAM hosts at one port (the RAM host port is the same on every node), on two loopback addresses for two nodes
PORT = free()
def ram_at(ip, name):
    procs.append(subprocess.Popen([os.path.join(B, "fdf-ram"), "--listen", "%s:%d" % (ip, PORT), "--api", fdfram.API],
                                  env=dict(os.environ, RINA_REGION_FILE=T + "/%s.perm" % name, RINA_NET_CONFIG=T + "/none", FDF_VAR=T + "/var"),
                                  stdout=subprocess.DEVNULL, stderr=open(T + "/%s.log" % name, "w")))
    for _ in range(100):
        try: socket.create_connection((ip, PORT), 0.2).close(); return
        except OSError: time.sleep(0.05)
ram_at("127.0.0.2", "a"); ram_at("127.0.0.3", "b")

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

open(T + "/ram_port", "w").write("%d\n" % PORT)
os.environ.pop("FDF_LAN_RAM_PORT", None)
env = dict(os.environ, FDF_HOSTS=hosts, FDF_RAM_PORT_FILE=T + "/ram_port", FROGNET_HOST_CMD_PORT=stub("port", 'echo x >> %s/ports' % T), FROGNET_GUID_FILE=T + "/fnid", FROGNET_GATEWAYS_CONF=gw,
           FROGNET_HOST_RECORD=T + "/record.json", FROGNET_HOST_DIR=T + "/out", FDF_VAR=T + "/var",
           FDF_PY=os.path.join(F, "lib", "python"), FROGNET_HOST_WAIT_S="20",
           FROGNET_HOST_CMD_SETUP=setup, FROGNET_HOST_CMD_CLEAR=clear, FROGNET_HOST_CMD_DUMP=dump,
           FROGNET_HOST_CMD_BACKUP=backup, FROGNET_HOST_CMD_REBOOT=reboot)
fh = os.path.join(F, "bin", "frognet-host")
def console(*a):
    p = subprocess.run([sys.executable, fh] + list(a), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    return p.returncode, (p.stdout + p.stderr).strip()
def state(ip):
    cs = [c for c in fdfram.Ram("%s:%d" % (ip, PORT)).read("frognethost", "state", "guid-node-one") if not c.get("gone")]
    return cs[-1]["bag"] if cs else {}
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

    c = fdfram.Ram("127.0.0.2:%d" % PORT)
    for cell in c.read("frognethost", "config", "guid-node-one"):
        if not cell.get("gone"): c.remove(cell)
    rc, out = console("clear")
    n = len(open(T + "/clears").read().split())
    check(rc == 0 and "clear 3" in out and n == 3, "5 the Admin's tuple gone: the next clear is clear 3, run once (%d runs): %s" % (n, out.splitlines()[-1]))

    t0 = time.time(); name_hosts("127.0.0.2", "127.0.0.3")
    s, dt = wait_for(lambda: state("127.0.0.3"))
    check(bool(s) and s.get("name") == "NewName" and s.get("done", {}).get("clear") == 3,
          "6 the SystemRam moved to the other RAM host: the node's state written there %.0f ms after the hosts file named it" % (dt * 1000))

    rc, out = console("status")
    check(rc == 0 and "NewName" in out and "127.0.0.3" in out, "7 status from the new SystemRam: %s" % out.replace("\n", " | ")[:200])
    name_hosts("127.0.0.3", "127.0.0.3")
    rc, out = console("clear")
    n = len(open(T + "/clears").read().split())
    check(rc == 0 and "clear 4" in out and n == 4, "8 databasehost-control moved too: the Admin's tuple written there is applied, clear 4 (%d runs): %s"
          % (n, out.splitlines()[-1]))
    # 9  the Admin UI's page API (frognet_setup_v4_api.php under php -S): the list, a request, the held read on its answer
    www = os.path.join(H, "..", "..", "..", "..", "..", "var", "www", "html")
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
    # 10 a new RAM host port
    P2 = free()
    for ip, nm in (("127.0.0.3", "c"),):
        procs.append(subprocess.Popen([os.path.join(B, "fdf-ram"), "--listen", "%s:%d" % (ip, P2), "--api", fdfram.API],
                                      env=dict(os.environ, RINA_REGION_FILE=T + "/%s.perm" % nm, RINA_NET_CONFIG=T + "/none", FDF_VAR=T + "/var"),
                                      stdout=subprocess.DEVNULL, stderr=open(T + "/%s.log" % nm, "w")))
    time.sleep(0.5)
    rc, out = console("setup", "NewName", "10.42.42.1", str(P2))
    def on_p2():
        try:
            cs = [c for c in fdfram.Ram("127.0.0.3:%d" % P2).read("frognethost", "state", "guid-node-one") if not c.get("gone")]
            return cs[-1]["bag"] if cs and cs[-1]["bag"].get("ram_port") == P2 else None
        except fdfram.RamError:
            return None
    s, dt = wait_for(on_p2, 15)
    check(rc == 0 and open(T + "/ram_port").read().strip() == str(P2) and os.path.exists(T + "/ports") and bool(s),
          "10 setup with RAM host port %d: the file says %s, the services started again, and the node's state on the new port %.0f ms after: %s"
          % (P2, open(T + "/ram_port").read().strip(), dt * 1000, out.splitlines()[-1:]))
finally:
    for p in procs:
        if p.poll() is None: p.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS))
sys.exit(1 if FAILS else 0)
