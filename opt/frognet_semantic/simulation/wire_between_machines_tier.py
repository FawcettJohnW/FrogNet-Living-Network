#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
wire_between_machines_tier.py -- [WIRE_BETWEEN_MACHINES_V1] the semantic wire between MACHINES on a topology, on a
transport the tier is told, with a battery of tests run from every machine to every other.

John, 2026-11-17, of the Homa driver: "The Homa tests in the simulator need to activate the driver between machines
on the topology and use that as the transport for a full battery of tests. That is passing." Before this tier the
simulator ran the wire only between ONE proxy and ONE daemon (ribbit/examples/homa/EXPLORATION-LOG.md, "what the
simulator has, and lacks").

A machine here is a network namespace, so nothing about a node is stood in for:
  its address          its own 10.x.y.1 on its own interface: frognet_tuples.my_ip() and the daemon's local_ips() are
                       the real calls
  its hosts file       /etc/netns/<ns>/hosts, which `ip netns exec` puts at /etc/hosts for everything run in it; it is
                       also FROGNET_HOSTS, the file the drivers' watcher watches and the role's address is read from
  its database         its own (one MariaDB serves them all; each machine reaches its own database at 127.0.0.1:3306,
                       as a node does, through a relay in its namespace to the server's socket)
  its api.php          the tree's own, on its port 80: the origin a request to that machine ends at, and on the machine
                       that holds the roles, the ControlRam and SystemRam
  its daemon           daemon/daemon_main.py, the process a node runs, through the seam, on port 9009
  its proxy            the proxy's engine (proxy.transport_semantic.handle_request) in a process of its own in that
                       namespace, driven by this tier over a pipe. (The proxy's HTTP front door, proxy_main with the
                       node's port-80 redirect, is NOT run: a request is handed to the engine with its target, as
                       door_semantic_tier hands it.)

The transport is the word given (--transport tcp | softhoma | auto). The tier is run on tcp first: the TCP driver
must pass the battery before anything is concluded about another transport, and that run is the control.

Needs root, network namespaces, php (mysqli, curl), socat, MariaDB answering root on its socket, the simulator's
DB_CONFIG (setup.sh), and for softhoma the Homa API interface built. Says what is missing and FAILS.

  python3 simulation/wire_between_machines_tier.py [--transport tcp] [--topology trial3] [--keep]
"""
import argparse
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SEM = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(SEM))
WEB = os.path.join(ROOT, "var", "www", "html")
sys.path.insert(0, HERE)
import sim_env  # noqa: E402,F401  [SIM_OWNS_ITS_ENV_V1]

BRIDGE = "fnwbr"
PY = sys.executable
SOFT_BUILT = os.path.join(SEM, "ribbit", "examples", "homa", "build")

# The topologies. A machine: (name, its .1, the roles it holds). The first increment is the shape of the hardware
# trial of 2026-11-17 (ribbit/examples/homa/REPORT-soft-homa-against-tcp-20261117.md).
TOPOLOGIES = {
    "trial3": [("Seattle5", "10.250.250.1", ("databasehost", "databasehost_control")),
               ("Seattle3B", "10.251.251.1", ()),
               ("New-York-1", "10.123.123.1", ())],
}


def sh(cmd, inp=None, check=False):
    r = subprocess.run(cmd, input=inp, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("%s: rc=%d %s" % (" ".join(cmd), r.returncode, (r.stderr or r.stdout)[-400:]))
    return r


class Machine:
    def __init__(self, n, name, ip, roles, tmp):
        self.n, self.name, self.ip, self.roles = n, name, ip, roles
        self.ns = "fnw%d" % n
        self.db = "FrogNetWire%d" % n
        self.dir = os.path.join(tmp, name)
        self.hosts = "/etc/netns/%s/hosts" % self.ns
        self.procs = []

    def _in(self, argv):
        """argv as run ON this machine: in its network namespace, with the node's tools where a node has them. `ip
        netns exec` gives the process a mount namespace of its own; in it the checkout's usr/local/bin is put at
        /usr/local/bin, so the node's own scripts (getEth0Address, which reads /usr/local/bin/mapInterfaces) run as
        on a node and find THIS machine's LAN device and address. Nothing is installed on the machine running the
        simulator ([SIM_OWNS_ITS_ENV_V1])."""
        return ["ip", "netns", "exec", self.ns, "bash", "-c", 'mount --bind "$0" /usr/local/bin && exec "$@"',
                os.environ["FROGNET_BIN_ROOT"]] + argv

    def run(self, argv, env=None, **kw):
        """A process on this machine."""
        p = subprocess.Popen(self._in(argv), env=env, **kw)
        self.procs.append(p)
        return p

    def exec(self, argv, env=None, inp=None):
        return subprocess.run(self._in(argv), env=env, input=inp, capture_output=True, text=True)

    def start_wire(self, word):
        """This machine's daemon and proxy (the processes a node runs, as its units start them) told the transport
        `word`, and a client on the machine."""
        env = dict(self.env, FROGNET_SEM_TRANSPORT=word)
        if word != "tcp":
            env["FROGNET_SOFTHOMA_LIB"] = os.path.join(SOFT_BUILT, "libsofthoma.so")   # the driver opens it; nothing preloaded
        self.daemon_log = os.path.join(self.dir, "daemon.log")
        self.daemon = self.run([PY, os.path.join(SEM, "daemon", "daemon_main.py"), "--listen", "0.0.0.0:9009"], env=env, cwd=SEM,
                               stdout=open(self.daemon_log, "w"), stderr=subprocess.STDOUT)
        self.proxy_log = os.path.join(self.dir, "proxy.log")
        self.proxy_main = self.run([PY, "-m", "proxy.proxy_main", "--listen", "0.0.0.0:80"], env=env, cwd=SEM,
                                   stdout=open(self.proxy_log, "w"), stderr=subprocess.STDOUT)
        self.proxy = self.run([PY, os.path.abspath(__file__), "--agent"], env=env, cwd=SEM, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.answers = queue.Queue()

        def read():
            for line in self.proxy.stdout:
                if line.startswith(MARK):
                    self.answers.put(json.loads(line[len(MARK):]))
            self.answers.put({"error": "the client on this machine ended"})
        threading.Thread(target=read, daemon=True).start()

    def ask(self, op, wait_s=120, **kw):
        if op is not None:
            self.proxy.stdin.write(json.dumps(dict(kw, op=op)) + "\n"); self.proxy.stdin.flush()
        try:
            return self.answers.get(timeout=wait_s)
        except queue.Empty:
            return {"error": "no answer from the client on %s in %ds" % (self.name, wait_s)}


# ----------------------------------------------------------------------------------------- a machine's proxy (child)
MARK = "@@WIRE-TIER "


def agent():
    """Run in a machine's namespace: a client on that machine. It asks for http://<another machine>/..., as a program
    on a node does, and the node's own port-80 rule hands the request to the node's proxy. One JSON line in, one line
    out."""
    import urllib.error
    import urllib.request

    def fetch(url, method, body, wait_s=90):
        r = urllib.request.Request(url, data=body.encode() if body else None, method=method,
                                   headers={"Content-Type": "application/json"} if body else {})
        t0 = time.time()
        try:
            with urllib.request.urlopen(r, timeout=wait_s) as f:
                return {"status": f.status, "body": f.read().decode(errors="replace"),
                        "cache": f.headers.get("X-FrogNet-Cache", ""), "ms": (time.time() - t0) * 1000}
        except urllib.error.HTTPError as e:
            return {"status": e.code, "body": e.read().decode(errors="replace"), "cache": e.headers.get("X-FrogNet-Cache", ""),
                    "ms": (time.time() - t0) * 1000}
        except Exception as e:
            return {"status": -1, "body": "%s: %s" % (type(e).__name__, e), "cache": "", "ms": (time.time() - t0) * 1000}

    def req(target, method, path, body):
        return fetch("http://%s%s" % (target, path), method, body)

    def local(method, path, body):
        return fetch("http://127.0.0.1:8080" + path, method, body, 30)      # this machine's own api.php, directly

    def many(reqs, threads):
        out, lock, it = [None] * len(reqs), threading.Lock(), iter(enumerate(reqs))

        def work():
            while True:
                with lock:
                    nxt = next(it, None)
                if nxt is None:
                    return
                i, r = nxt
                out[i] = req(*r)
        ts = [threading.Thread(target=work) for _ in range(threads)]
        for t in ts: t.start()
        for t in ts: t.join()
        return out

    print(MARK + json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        c = json.loads(line)
        try:
            if c["op"] == "req":
                r = req(c["target"], c["method"], c["path"], c.get("body"))
            elif c["op"] == "many":
                r = many([(x["target"], x["method"], x["path"], x.get("body")) for x in c["reqs"]], c["threads"])
            elif c["op"] == "local":
                r = local(c["method"], c["path"], c.get("body"))
            else:
                r = {"error": "no such op %r" % c["op"]}
        except Exception as e:
            r = {"error": "%s: %s" % (type(e).__name__, e)}
        print(MARK + json.dumps(r), flush=True)


def hosts_text(machines):
    """A node's hosts file after the merge: every machine in the block, and the role lines."""
    out = ["127.0.0.1 localhost"]
    for m in machines:
        out.append("%s FrogNetHost.%s %s" % (m.ip, m.name, m.name))
    for m in machines:
        for r in m.roles:
            out.append("%s %s.frognet" % (m.ip, r))
    return "\n".join(out) + "\n"


def write_hosts(m, text):
    """In place, as one write: the file a namespace sees at /etc/hosts is this file itself, and the watcher takes the
    close of a write as its event."""
    with open(m.hosts, "w") as f:
        f.write(text)


def missing():
    need = []
    if os.geteuid() != 0:
        need.append("root")
    for tool in ("ip", "php", "socat", "mariadb"):
        if not shutil.which(tool):
            need.append(tool)
    if shutil.which("php"):
        mods = sh(["php", "-m"]).stdout.lower().split()
        need += ["php-" + x for x in ("mysqli", "curl") if x not in mods]
    if shutil.which("mariadb") and sh(["mariadb", "-uroot", "-e", "SELECT 1"]).returncode != 0:
        need.append("a MariaDB server answering root on its socket")
    if not os.path.exists(os.environ.get("FROGNET_DB_CONFIG", "")):
        need.append("the simulator's DB_CONFIG (simulation/setup.sh)")
    if shutil.which("ip") and os.geteuid() == 0:
        r = sh(["ip", "netns", "add", "fnwprobe"])
        if r.returncode != 0:
            need.append("network namespaces (%s)" % r.stderr.strip()[:80])
        sh(["ip", "netns", "del", "fnwprobe"])
    return need


def tear_down(machines):
    for m in machines:
        for p in m.procs:
            if p.poll() is None:
                p.terminate()
        for p in m.procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        pids = sh(["ip", "netns", "pids", m.ns]).stdout.split()
        if pids:
            sh(["kill", "-9"] + pids)
        sh(["ip", "netns", "del", m.ns])
        sh(["ip", "link", "del", "fnwh%d" % m.n])            # its end of the link, if the namespace's going left it
        shutil.rmtree("/etc/netns/%s" % m.ns, ignore_errors=True)
        sh(["mariadb", "-uroot", "-e", "DROP DATABASE IF EXISTS %s" % m.db])
    sh(["ip", "link", "del", BRIDGE])


def bring_up(machines, dbc):
    """The network, each machine's database, its relay to the database server, and its api.php."""
    sh(["ip", "link", "add", BRIDGE, "type", "bridge"], check=True)
    sh(["ip", "link", "set", BRIDGE, "up"], check=True)
    sock = sh(["mariadb", "-uroot", "-N", "-e", "SELECT @@socket"]).stdout.strip()
    users = sh(["mariadb", "-uroot", "-N", "-e",
                "SELECT CONCAT(\"'\",user,\"'@'\",host,\"'\") FROM mysql.user WHERE user='%s'" % dbc["user"]]).stdout.split()
    for m in machines:
        os.makedirs(m.dir)
        os.makedirs("/etc/netns/%s" % m.ns, exist_ok=True)
        write_hosts(m, hosts_text(machines))
        sh(["ip", "netns", "add", m.ns], check=True)
        host_if, ns_if = "fnwh%d" % m.n, "eth0"
        sh(["ip", "link", "add", host_if, "type", "veth", "peer", "name", ns_if, "netns", m.ns], check=True)
        sh(["ip", "link", "set", host_if, "master", BRIDGE, "up"], check=True)
        for c in (["ip", "link", "set", "lo", "up"], ["ip", "addr", "add", m.ip + "/8", "dev", ns_if],
                  ["ip", "link", "set", ns_if, "up"]):
            r = m.exec(c)
            if r.returncode != 0:
                raise RuntimeError("%s in %s: %s" % (" ".join(c), m.ns, r.stderr.strip()))
        # its database
        sql = open(os.path.join(WEB, "Create_Database.sql")).read().replace("`FrogNet`", "`%s`" % m.db)
        fix = open(os.path.join(WEB, "schema_fixups.sql")).read().replace("USE FrogNet;", "USE %s;" % m.db)
        r1, r2 = sh(["mariadb", "-uroot"], sql), sh(["mariadb", "-uroot"], fix)
        if r1.returncode or r2.returncode or not users:
            raise RuntimeError("database %s: %s %s (users %s)" % (m.db, r1.stderr[-200:], r2.stderr[-200:], users))
        for u in users:
            sh(["mariadb", "-uroot", "-e", "GRANT ALL ON %s.* TO %s" % (m.db, u)], check=True)
        cfg = os.path.join(m.dir, "DB_CONFIG.json")
        json.dump(dict(dbc, host="127.0.0.1", port=3306, database=m.db), open(cfg, "w"))
        # 127.0.0.1:3306 in this namespace is this machine's database, as on a node
        m.run(["socat", "TCP-LISTEN:3306,bind=127.0.0.1,fork,reuseaddr", "UNIX-CONNECT:" + sock],
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # its api.php, on its port 80
        web = os.path.join(m.dir, "html"); os.mkdir(web)
        for f in os.listdir(WEB):
            if f.endswith(".php"):
                shutil.copy(os.path.join(WEB, f), web)
        c = open(os.path.join(web, "config.php")).read()
        c = c.replace("__FROGNET_DB_PASS__", dbc["password"]).replace("define('DB_NAME', 'FrogNet')", "define('DB_NAME', '%s')" % m.db)
        c = c.replace("define('DB_USER', 'FrogUser')", "define('DB_USER', '%s')" % dbc["user"])
        open(os.path.join(web, "config.php"), "w").write(c)
        held = os.path.join(m.dir, "held"); os.mkdir(held)
        m.env = dict(os.environ, FROGNET_DB_CONFIG=cfg, FROGNET_HOSTS=m.hosts, FROGNET_HELD_DIR=held,
                     FROGNET_BLOB_ROOT=os.path.join(m.dir, "blobs"), PYTHONDONTWRITEBYTECODE="1")
        m.env.pop("LD_PRELOAD", None)
        # its api.php where a node's is, on port 8080; port 80 is the node's proxy
        m.run(["php", "-S", "0.0.0.0:8080", "-t", web], env=dict(m.env, PHP_CLI_SERVER_WORKERS="16"),
              stdout=open(os.path.join(m.dir, "php.log"), "w"), stderr=subprocess.STDOUT)
        # the node's HTTP interception, as etc/setup_iptables writes it: what a program on the node sends to port 80
        # of a FrogNet address goes to the node's own proxy; the daemon's own requests (mark 1) do not
        for rule in (["-A", "OUTPUT", "-m", "mark", "--mark", "1", "-p", "tcp", "--dport", "80", "-j", "RETURN"],
                     ["-A", "OUTPUT", "-p", "tcp", "-d", "127.0.0.1/32", "--dport", "80", "-j", "ACCEPT"],
                     ["-A", "OUTPUT", "-p", "tcp", "-d", "10.0.0.0/8", "--dport", "80", "-j", "REDIRECT", "--to-ports", "80"]):
            r = m.exec(["iptables", "-t", "nat"] + rule)
            if r.returncode != 0:
                raise RuntimeError("iptables %s in %s: %s" % (" ".join(rule), m.ns, r.stderr.strip()))


RPATH = "/api.php?entity=sensors&action=values&SensorType=%s&limit=%d&order=SensorID&parse=1"
WPATH = "/api.php?entity=sensor_data&action=upsert_by_name"


def battery_fanout(machines, check):
    """(a) From every machine's proxy to every other machine's daemon: concurrent reads and writes through the wire,
    each compared with what the target machine's own api.php says. The workload of test_fanout_wire_order_oracle,
    with the target's real api.php and database in place of that oracle's stand-in origin."""
    good = True
    R, W, N, TYPES = 6, 3, 30, ("WA", "WB")
    for b in machines:                                    # what each machine holds: 3 rows of each type, written on it
        for t in TYPES:
            for i in range(3):
                r = b.ask("local", method="POST", path=WPATH, body=json.dumps(
                    {"SensorName": "%s.%s.s%d" % (t, b.name, i), "SensorType": t, "SensorAddress": b.ip, "SensorNetwork": b.name,
                     "jsonData": {"v": i, "at": b.name}}))
                if r.get("status") != 200:
                    check(False, "seeding %s: its own api.php refused a row: %s" % (b.name, r)); return False
    for a in machines:
        for b in machines:
            if a is b:
                continue
            warm = [a.ask("req", target=b.ip, method="GET", path=RPATH % (t, k)) for t in TYPES for k in (1, 2)]
            warm.append(a.ask("req", target=b.ip, method="POST", path=WPATH, body=json.dumps(
                {"SensorName": "w0.%s.from.%s" % (b.name, a.name), "SensorType": "WW", "SensorAddress": a.ip, "SensorNetwork": a.name,
                 "jsonData": {"v": 0}})))
            reqs = []
            for i in range(N):
                for r in range(R):
                    reqs.append({"target": b.ip, "method": "GET", "path": RPATH % (TYPES[r % 2], 1 + (i + r) % 3)})
                for w in range(W):
                    reqs.append({"target": b.ip, "method": "POST", "path": WPATH, "body": json.dumps(
                        {"SensorName": "w%d.%s.from.%s" % (w, b.name, a.name), "SensorType": "WW", "SensorAddress": a.ip,
                         "SensorNetwork": a.name, "jsonData": {"v": i}})})
            t0 = time.time()
            got = a.ask("many", reqs=reqs, threads=R + W, wait_s=300)
            dt = time.time() - t0
            if not isinstance(got, list):
                check(False, "%s -> %s: %s" % (a.name, b.name, got)); good = False; continue
            bad = []
            for q, g in zip(reqs, got):
                if g["status"] != 200:
                    bad.append((q["path"][-40:], g["status"], g["body"][:120])); continue
                d = json.loads(g["body"])
                if q["method"] == "GET":
                    t, k = q["path"].split("SensorType=")[1].split("&")[0], int(q["path"].split("limit=")[1].split("&")[0])
                    rows = d.get("rows", [])
                    if len(rows) != k or any(x.get("SensorType") != t for x in rows):
                        bad.append((q["path"][-40:], "rows", [(x.get("SensorName"), x.get("SensorType")) for x in rows][:4]))
                elif not d.get("ok"):
                    bad.append(("write", g["body"][:120]))
            ms = sorted(g["ms"] for g in got if g["status"] == 200)
            # the same read through the wire and from the target's own api.php
            path = RPATH % ("WW", 10)
            wire, own = a.ask("req", target=b.ip, method="GET", path=path), b.ask("local", method="GET", path=path)
            same = wire.get("status") == 200 and own.get("status") == 200 and json.loads(wire["body"]) == json.loads(own["body"])
            last = {r["SensorName"]: (r.get("data") or r.get("jsonData") or {}).get("v") for r in json.loads(own["body"]).get("rows", [])} if own.get("status") == 200 else {}
            mine = {"w%d.%s.from.%s" % (w, b.name, a.name): N - 1 for w in range(W)}
            held = all(last.get(n) == v for n, v in mine.items())
            fine = all(w.get("status") == 200 for w in warm) and not bad and same and held
            good = good and fine
            check(fine, "%s -> %s: %d concurrent reads and %d writes in %.1fs (median %.1f ms): every read answered its own "
                        "rows, every write acknowledged, %s holds each writer's last value, and a read through the wire "
                        "equals %s's own api.php%s" % (a.name, b.name, R * N, W * N, dt, ms[len(ms) // 2] if ms else -1, b.name, b.name,
                                                       "" if fine else " -- WRONG: warm %s; %d bad %s; same=%s; last=%s" % (
                                                           [w.get("status") for w in warm], len(bad), bad[:3], same, {n: last.get(n) for n in mine})))
    return good


def battery_carried(machines, word, check):
    """What carried it, read from each machine's own proxy log: its sessions, and on which driver."""
    good = True
    for m in machines:
        log = open(m.proxy_log, errors="replace").read()
        homa = sorted({l.split(" to ")[1].split(":")[0] for l in log.split("\n") if l.startswith("[SEM-HOMA] session ") and " opened" in l})
        tcp = sorted({l.split("host=")[1].split()[0] for l in log.split("\n") if l.startswith("[RPC-TRACE] event=connect_ok host=")})
        unknown = "LOCAL GW UNKNOWN" in log or "LOCAL GW UNKNOWN" in open(m.daemon_log, errors="replace").read()
        others = sorted(o.ip for o in machines if o is not m)
        if word == "tcp":
            fine = tcp == others and not homa
        elif word == "softhoma":
            fine = homa == others and not tcp and "LD_PRELOAD" not in log
        else:
            fine = sorted(set(homa) | set(tcp)) == others
        fine = fine and not unknown
        good = good and fine
        check(fine, "%s: told %s; its proxy's sessions on the Homa driver: %s; on the TCP driver: %s; each end named itself in "
                    "its HELLO by the node's own getEth0Address: %s" % (m.name, word, homa or "none", tcp or "none", not unknown))
    return good


def main():
    if "--agent" in sys.argv:
        return agent()
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default="tcp", choices=("tcp", "softhoma", "auto"))
    ap.add_argument("--topology", default="trial3", choices=sorted(TOPOLOGIES))
    ap.add_argument("--keep", action="store_true", help="leave the namespaces and their files up at the end")
    a = ap.parse_args()
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg), flush=True)

    need = missing()
    if a.transport != "tcp" and not os.path.exists(os.path.join(SOFT_BUILT, "libsofthoma.so")):
        need.append("soft Homa built (make -C ribbit/examples/homa HOMA=<HomaModule checkout>)")
    if need:
        print("RESULT FAIL: this tier needs " + "; ".join(need))
        return 1
    dbc = json.load(open(os.environ["FROGNET_DB_CONFIG"]))
    tmp = tempfile.mkdtemp(prefix="wire-machines-")
    machines = [Machine(i, name, ip, roles, tmp) for i, (name, ip, roles) in enumerate(TOPOLOGIES[a.topology])]
    tear_down(machines)                                   # what an earlier run that was killed left behind
    print("== %s, transport %s: %s" % (a.topology, a.transport, ", ".join("%s %s" % (m.name, m.ip) for m in machines)), flush=True)
    try:
        bring_up(machines, dbc)
        time.sleep(1.5)
        # ---- 0. the machines are machines
        for m in machines:
            others = [o for o in machines if o is not m]
            pings = [m.exec(["ping", "-c", "1", "-W", "2", o.ip]).returncode == 0 for o in others]
            me = m.exec([PY, "-c", "import sys; sys.path.insert(0, %r); from core import frognet_tuples as T; print(T.my_ip())" % SEM],
                        env=m.env).stdout.strip().split("\n")[-1]
            role = m.exec([PY, "-c", "import socket; print(socket.gethostbyname('databasehost_control.frognet'))"], env=m.env).stdout.strip()
            api = m.exec(["php", "-r", "echo @file_get_contents('http://127.0.0.1:8080/api.php?entity=sensors&action=values&limit=1');"]).stdout
            check(all(pings) and me == m.ip and role == machines[0].ip and api.strip().startswith(("{", "[")),
                  "%s: reaches the others (%s), FrogNet's own call gives its address as %s, its hosts file names the "
                  "ControlRam %s, and its own api.php answers where a node's does, on port 8080 (%s)" % (m.name, pings, me, role, api.strip()[:60]))

        # ---- the wire: every machine's daemon and proxy, on the transport given
        for m in machines:
            m.start_wire(a.transport)
        up = [m.ask(None, wait_s=60) for m in machines]
        def listening():
            return [m.exec(["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/80"]).returncode == 0 and
                    (a.transport == "softhoma" or m.exec(["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/9009"]).returncode == 0) for m in machines]
        t_end = time.time() + 40
        while not all(listening()) and time.time() < t_end:
            time.sleep(0.5)
        check(all(u.get("ready") for u in up) and all(m.daemon.poll() is None and m.proxy_main.poll() is None for m in machines) and all(listening()),
              "every machine's daemon (daemon_main.py) and proxy (proxy_main) are up, told %s, with the node's port-80 rule (%s)"
              % (a.transport, listening()))

        ok = battery_fanout(machines, check) and ok
        ok = battery_carried(machines, a.transport, check) and ok
    finally:
        if a.keep:
            print("kept: %s; namespaces %s" % (tmp, ", ".join(m.ns for m in machines)))
        else:
            tear_down(machines)
            shutil.rmtree(tmp, ignore_errors=True)
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
