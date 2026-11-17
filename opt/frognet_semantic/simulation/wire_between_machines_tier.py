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
import json
import os
import shutil
import subprocess
import sys
import tempfile
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

    def run(self, argv, env=None, **kw):
        """A process in this machine's namespace."""
        p = subprocess.Popen(["ip", "netns", "exec", self.ns] + argv, env=env, **kw)
        self.procs.append(p)
        return p

    def exec(self, argv, env=None, inp=None):
        return subprocess.run(["ip", "netns", "exec", self.ns] + argv, env=env, input=inp, capture_output=True, text=True)


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
        m.run(["php", "-S", "0.0.0.0:80", "-t", web], env=dict(m.env, PHP_CLI_SERVER_WORKERS="8"),
              stdout=open(os.path.join(m.dir, "php.log"), "w"), stderr=subprocess.STDOUT)


def main():
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
            api = m.exec(["php", "-r", "echo @file_get_contents('http://127.0.0.1/api.php?entity=sensors&action=values&limit=1');"]).stdout
            check(all(pings) and me == m.ip and role == machines[0].ip and api.strip().startswith(("{", "[")),
                  "%s: reaches the others (%s), FrogNet's own call gives its address as %s, its hosts file names the "
                  "ControlRam %s, and its own api.php answers on its port 80 (%s)" % (m.name, pings, me, role, api.strip()[:60]))
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
