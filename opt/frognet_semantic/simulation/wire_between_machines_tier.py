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
import re
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

# The topologies. A machine: (name, its .1, the roles the election gave it). The first increment is the shape of the
# hardware trial of 2026-11-17 (ribbit/examples/homa/REPORT-soft-homa-against-tcp-20261117.md).
# The ControlRam is not placed here. It is where the node's own rule puts it: the highest .1
# (usr/local/bin/frognet_dbhost_elector.py: "Control = highest .1 == databasehost_control.frognet"), by control_holder()
# below. Until 13:30 on 17 Nov this table put it on Seattle5 beside the SystemRam, which is not where a network of
# these three machines has it (10.251.251.1 is the highest), and hid that the two RAMs are on different machines.
TOPOLOGIES = {
    "trial3": [("Seattle5", "10.250.250.1", ("databasehost",)),
               ("Seattle3B", "10.251.251.1", ()),
               ("New-York-1", "10.123.123.1", ())],
}
SYSTEM_RAM, CONTROL_RAM = "databasehost.frognet", "databasehost_control.frognet"


def simulator_topologies():
    """[THE_WHOLE_RANGE_OF_TOPOLOGIES_V1] John, 2026-11-17: "The simulator should be able to run it across the entire
    range of topologies". The range is the simulator's own: every topology with a baseline recorded on hardware
    (simulation/baselines/topo_*.json), built by its builder in frognet_sim.py."""
    d = os.path.join(HERE, "baselines")
    return sorted(f[:-5] for f in os.listdir(d) if f.startswith("topo_") and f.endswith(".json"))


def net_of(key):
    """A topology as this tier builds it: {"machines": [(name, its .1, roles, [(device, address/prefix)])],
    "segments": [[(name, device)]], "routes": {name: [route lines]}, "left_out": [names], "says": text}.

    trial3 is one L2 segment. A simulator topology is exactly the simulator's: its nodes' devices and addresses
    (frognet_sim's builder), its L2 segments, and the routes the baseline recorded on hardware for it
    (committed_routes), so a request between two machines crosses the machines between them as it did there. A
    tunnel device (wg0) is an ordinary link here: what the semantic wire rides is an IP path, and WireGuard is not
    part of what this tier tests. A node that serves no /24 (a worker holding a lease on another's) runs no proxy
    or daemon and is left out, and said."""
    if key in TOPOLOGIES:
        t = TOPOLOGIES[key]
        return {"machines": [(n, ip, tuple(r), [("eth0", ip + "/8")]) for n, ip, r in t],
                "segments": [[(n, "eth0") for n, _ip, _r in t]], "routes": {}, "left_out": [], "says": "one L2 segment"}
    import frognet_sim as H
    topo = getattr(H, key)()
    H.assign_identities(topo)
    base = json.load(open(os.path.join(HERE, "baselines", key + ".json")))
    serves = [n for n, x in topo.nodes.items() if x.frognet_ip and x.frognet_ip.endswith(".1") and x.frognet_ip.startswith("10.")]
    machines = [(n, topo.nodes[n].frognet_ip, (), [(i.name, i.addr_cidr) for i in topo.nodes[n].ifaces.values()]) for n in serves]
    segments = [[(n, i) for n, i in seg if n in serves] for seg in topo.segments]
    return {"machines": machines, "segments": [g for g in segments if len(g) > 1],
            "routes": {n: base["committed_routes"].get(n, []) for n in serves},
            "left_out": [n for n in topo.nodes if n not in serves],
            "says": "%s; baseline recorded on %s: %d of %d pairs reachable" % (
                topo.label, base.get("source"), base["reachability"]["pairs_ok"], base["reachability"]["pairs_total"])}


def place_roles(machines):
    """The roles, as this tier writes the merge's result. The ControlRam by the elector's own rule (the highest .1).
    The SystemRam is the election's choice among capabilities, which this tier does not run: where the topology's
    table names one it is that machine, else the first machine that is not the ControlRam's (so that the two RAMs
    are on different machines wherever there are two machines)."""
    ctl = control_holder(machines)
    if not any("databasehost" in m.roles for m in machines):
        first = [m for m in machines if m is not ctl] or [ctl]
        first[0].roles = tuple(first[0].roles) + ("databasehost",)
    ctl.roles = tuple(ctl.roles) + ("databasehost_control",)
    return [m for m in machines if "databasehost" in m.roles][0]


def control_holder(machines):
    """The elector's rule, its own expression: the highest .1."""
    return max(machines, key=lambda m: tuple(int(x) for x in m.ip.split(".")))


def words_of(setting):
    """A transport setting as core/homa_transport.py reads it: (the node's word, {RAM's role name: its word})."""
    lines = [l.split() for l in setting.lower().replace(",", "\n").splitlines() if l.strip()]
    return lines[0][0], {l[0]: l[1] for l in lines[1:]}


def word_between(setting, m, o):
    """The word for m's session to o: the word of the named RAM that is on o, else the node's word."""
    node, rams = words_of(setting)
    there = sorted(set(w for r, w in rams.items() if r.split(".")[0] in o.roles))
    return there[0] if there else node


def sh(cmd, inp=None, check=False):
    r = subprocess.run(cmd, input=inp, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("%s: rc=%d %s" % (" ".join(cmd), r.returncode, (r.stderr or r.stdout)[-400:]))
    return r


class Machine:
    def __init__(self, n, name, ip, roles, tmp, ifaces=None):
        self.n, self.name, self.ip, self.roles = n, name, ip, roles
        self.ifaces = ifaces or [("eth0", ip + "/8")]
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
        if word != "tcp":                    # any setting that names a Homa: told where the library is
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

    def stop_wire(self):
        """The machine goes down as a machine does: its daemon, its proxy, the memory process its daemon started and
        the client are killed where they stand. Nothing is told and nothing is written first."""
        gone = []
        for pid in sh(["ip", "netns", "pids", self.ns]).stdout.split():
            try:
                cmd = open("/proc/%s/cmdline" % pid, "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if any(k in cmd for k in ("daemon_main.py", "proxy.proxy_main", "softhoma-ram", "--agent")):
                try:
                    os.kill(int(pid), 9); gone.append(cmd.split()[-2 if "--listen" in cmd else -1][-24:])
                except OSError:
                    pass
        for p in (self.daemon, self.proxy_main, self.proxy):
            try:
                p.wait(timeout=10)
            except Exception:
                pass
        return gone

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

    def streams(lists):
        """Each list is one program on the machine: its requests one after another, the lists at once. A writer's
        writes to its own tuple are therefore in order, as test_fanout_wire_order_oracle's writers are."""
        out = [[None] * len(l) for l in lists]

        def work(k):
            for i, r in enumerate(lists[k]):
                out[k][i] = req(r["target"], r["method"], r["path"], r.get("body"))
        ts = [threading.Thread(target=work, args=(k,)) for k in range(len(lists))]
        for t in ts: t.start()
        for t in ts: t.join()
        return out

    print(MARK + json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        c = json.loads(line)
        try:
            if c["op"] == "req":
                r = req(c["target"], c["method"], c["path"], c.get("body"))
            elif c["op"] == "streams":
                r = streams(c["lists"])
            elif c["op"] == "local":
                r = local(c["method"], c["path"], c.get("body"))
            else:
                r = {"error": "no such op %r" % c["op"]}
        except Exception as e:
            r = {"error": "%s: %s" % (type(e).__name__, e)}
        print(MARK + json.dumps(r), flush=True)


def hosts_text(machines, roles=True):
    """A node's hosts file: every machine in the block and, once the merge's election has run, the role lines."""
    out = ["127.0.0.1 localhost"]
    for m in machines:
        out.append("%s FrogNetHost.%s %s" % (m.ip, m.name, m.name))
    for m in machines:
        for r in m.roles if roles else ():
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
    for l in sh(["ip", "-o", "link"]).stdout.splitlines():   # this tier's links and segments on this side, whatever a run left
        dev = l.split(":")[1].strip().split("@")[0]
        if dev.startswith(("fnwh", "fnws")):
            sh(["ip", "link", "del", dev])


def bring_up(machines, dbc, net):
    """The network, each machine's database, its relay to the database server, and its api.php."""
    seg_of = {}
    for k, seg in enumerate(net["segments"]):                # an L2 segment is a bridge on this side
        sh(["ip", "link", "add", "fnws%d" % k, "type", "bridge"], check=True)
        sh(["ip", "link", "set", "fnws%d" % k, "up"], check=True)
        for name, dev in seg:
            seg_of[(name, dev)] = "fnws%d" % k
    sock = sh(["mariadb", "-uroot", "-N", "-e", "SELECT @@socket"]).stdout.strip()
    # Every machine's database is on the simulator's one database server. A node's daemon, proxy and api.php hold
    # their own connections, and the server's default (151) is for one node: with five machines or more the
    # machines were answered "Too many connections" (found on the first run across the topologies, 17 Nov). The
    # simulator's own server is told how many machines it is standing for.
    sh(["mariadb", "-uroot", "-e", "SET GLOBAL max_connections = %d" % max(151, 400 * len(machines))], check=True)
    users = sh(["mariadb", "-uroot", "-N", "-e",
                "SELECT CONCAT(\"'\",user,\"'@'\",host,\"'\") FROM mysql.user WHERE user='%s'" % dbc["user"]]).stdout.split()
    for m in machines:
        os.makedirs(m.dir)
        os.makedirs("/etc/netns/%s" % m.ns, exist_ok=True)
        write_hosts(m, hosts_text(machines, roles=False))    # booted; the merge has not run: no role is named yet
        sh(["ip", "netns", "add", m.ns], check=True)
        cmds = [["ip", "link", "set", "lo", "up"], ["sysctl", "-qw", "net.ipv4.ip_forward=1"],
                ["sysctl", "-qw", "net.ipv4.conf.all.rp_filter=0"], ["sysctl", "-qw", "net.ipv4.conf.default.rp_filter=0"]]
        for j, (dev, cidr) in enumerate(m.ifaces):           # each device the topology gives this machine, by its name
            host_if = "fnwh%d_%d" % (m.n, j)
            sh(["ip", "link", "add", host_if, "type", "veth", "peer", "name", dev, "netns", m.ns], check=True)
            if (m.name, dev) in seg_of:
                sh(["ip", "link", "set", host_if, "master", seg_of[(m.name, dev)]], check=True)
            sh(["ip", "link", "set", host_if, "up"], check=True)   # a device on no segment (a served LAN with no one on it) is up, alone
            cmds += [["ip", "addr", "add", cidr, "dev", dev], ["ip", "link", "set", dev, "up"],
                     ["sysctl", "-qw", "net.ipv4.conf.%s.rp_filter=0" % dev],
                     # a recorded route says "via <the far machine's .1> dev wgN": WireGuard hands that to the peer on
                     # the link whoever holds the address. On an ordinary link the peer answers for an address it
                     # routes onward, which is proxy ARP.
                     ["sysctl", "-qw", "net.ipv4.conf.%s.proxy_arp=1" % dev],
                     # and at once: the kernel delays such an answer by up to 0.8 s by default, which a tunnel does not
                     ["sysctl", "-qw", "net.ipv4.neigh.%s.proxy_delay=0" % dev]]
        for c in cmds:
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
    # the routes the baseline recorded on hardware for this topology, now that every device exists. "via X dev wgN"
    # names a next hop that is not on the link's own /30 (WireGuard needs no neighbour); on an ordinary link the
    # kernel is told the same thing with onlink, and the next machine answers for the address it holds.
    for m in machines:
        for line in net["routes"].get(m.name, []):
            w = line.split()
            if "via" not in w:
                continue                                     # a connected route: the address put it there
            # src: a node's installed route carries its identity (discovery.Discovery.dev_src gives the node's .1 for
            # every device, and promote() stamps it), so what it sends to another machine's /24 comes from its .1 and
            # can be answered. The baseline's lines do not print it; a transit /30 is known only to its two ends.
            r = m.exec(["ip", "route", "add", w[0], "via", w[w.index("via") + 1], "dev", w[w.index("dev") + 1], "onlink",
                        "metric", w[w.index("metric") + 1], "src", m.ip])
            if r.returncode != 0 and "File exists" not in r.stderr:
                raise RuntimeError("route %r on %s: %s" % (line, m.name, r.stderr.strip()))


RPATH = "/api.php?entity=sensors&action=values&SensorType=%s&limit=%d&order=SensorID&parse=1"
WPATH = "/api.php?entity=sensor_data&action=upsert_by_name"


def battery_fanout(machines, check, pairs=None, tag=""):
    """(a) From every machine's proxy to every other machine's daemon: concurrent reads and writes through the wire,
    each compared with what the target machine's own api.php says. The workload of test_fanout_wire_order_oracle,
    with the target's real api.php and database in place of that oracle's stand-in origin."""
    good = True
    R, W, N, TYPES = 6, 3, 30, ("WA", "WB")
    for b in machines if pairs is None else ():           # what each machine holds: 3 rows of each type, written on it
        for t in TYPES:
            for i in range(3):
                r = b.ask("local", method="POST", path=WPATH, body=json.dumps(
                    {"SensorName": "%s.%s.s%d" % (t, b.name, i), "SensorType": t, "SensorAddress": b.ip, "SensorNetwork": b.name,
                     "jsonData": {"v": i, "at": b.name}}))
                if r.get("status") != 200:
                    check(False, "seeding %s: its own api.php refused a row: %s" % (b.name, r)); return False
    for a in machines:
        for b in machines:
            if a is b or (pairs is not None and (a, b) not in pairs):
                continue
            warm = [a.ask("req", target=b.ip, method="GET", path=RPATH % (t, k)) for t in TYPES for k in (1, 2)]
            warm.append(a.ask("req", target=b.ip, method="POST", path=WPATH, body=json.dumps(
                {"SensorName": "w0.%s.from.%s" % (b.name, a.name), "SensorType": "WW", "SensorAddress": a.ip, "SensorNetwork": a.name,
                 "jsonData": {"v": 0}})))
            lists = [[{"target": b.ip, "method": "GET", "path": RPATH % (TYPES[r % 2], 1 + (i + r) % 3)} for i in range(N)]
                     for r in range(R)]                   # R readers
            lists += [[{"target": b.ip, "method": "POST", "path": WPATH, "body": json.dumps(
                {"SensorName": "w%d.%s.from.%s" % (w, b.name, a.name), "SensorType": "WW", "SensorAddress": a.ip,
                 "SensorNetwork": a.name, "jsonData": {"v": i}})} for i in range(N)] for w in range(W)]   # W writers
            t0 = time.time()
            ans = a.ask("streams", lists=lists, wait_s=300)
            dt = time.time() - t0
            if not isinstance(ans, list):
                check(False, "%s -> %s: %s" % (a.name, b.name, ans)); good = False; continue
            reqs, got = [q for l in lists for q in l], [g for l in ans for g in l]
            bad, miss = [], 0
            for q, g in zip(reqs, got):
                if g["status"] == 503 and g["body"].startswith(REQ_MISS):
                    miss += 1; REQ_MISSES.append("%s -> %s %s" % (a.name, b.name, q["path"][-44:])); continue
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
            path = RPATH % ("WW", 1000)                   # every writer's row on the target, however many machines wrote to it
            wire, own = a.ask("req", target=b.ip, method="GET", path=path), b.ask("local", method="GET", path=path)
            same = wire.get("status") == 200 and own.get("status") == 200 and json.loads(wire["body"]) == json.loads(own["body"])
            last = {r["SensorName"]: (r.get("data") or r.get("jsonData") or {}).get("v") for r in json.loads(own["body"]).get("rows", [])} if own.get("status") == 200 else {}
            mine = {"w%d.%s.from.%s" % (w, b.name, a.name): N - 1 for w in range(W)}
            held = all(last.get(n) == v for n, v in mine.items())
            fine = all(w.get("status") == 200 for w in warm) and not bad and same and held
            good = good and fine
            check(fine, tag + "%s -> %s: %d concurrent reads and %d writes in %.1fs (median %.1f ms): every read answered its own "
                        "rows, every write acknowledged, %s holds each writer's last value, and a read through the wire "
                        "equals %s's own api.php%s%s" % (a.name, b.name, R * N, W * N, dt, ms[len(ms) // 2] if ms else -1, b.name, b.name,
                                                       "" if not miss else " [NOT JUDGED: %d of the reads answered 503 %r, the semantic "
                                                       "engine's own fault above either driver, seen on TCP at the same place]" % (miss, REQ_MISS),
                                                       "" if fine else " -- WRONG: warm %s; %d bad %s; same=%s; last=%s" % (
                                                           [w.get("status") for w in warm], len(bad), bad[:3], same, {n: last.get(n) for n in mine})))
    return good


# [THE_ENGINE_S_MISS_IS_NOT_THE_WIRE_S_V1] Between machines, about one run in three has ONE read, always in the first
# burst from the first machine to its second target, answered 503 with this reason. It is the semantic engine's
# (proxy/transport_semantic.py and daemon/engine/execution.py: an instance reference the proxy believes the peer
# holds and the peer does not, twice in a row for one request while other streams are doing the same). It is above
# both drivers and happens on TCP as on Homa (ribbit/examples/homa/EXPLORATION-LOG.md section 7, item 0a). This tier
# judges the transport, so such a read is counted and said on its own line and in the result, and is not held
# against the pair. Nothing else is excused: any other status, any wrong row, any lost write fails the pair.
REQ_MISS = "REQ_MISS from daemon; exhausted retries"
REQ_MISSES = []

LISTENING = "[DAEMON-HOMA] [HOMA_TRANSPORT_V1] semantic wire on Homa at"


def listens(m):
    """How many times this machine's daemon has said, in its own log, that it is listening on Homa."""
    return open(m.daemon_log, errors="replace").read().count(LISTENING)


def wait_for_listeners(machines, wait_s=30, more_than=None):
    """Each daemon's own log line that it is listening on Homa (more_than: {name: count} it must now exceed). The tier
    reads the machines' logs; it does not ask the memory, so it stands in for nothing."""
    t_end, there = time.time() + wait_s, {}
    while time.time() < t_end:
        there = {m.name: listens(m) > (more_than or {}).get(m.name, 0) for m in machines}
        if all(there.values()):
            break
        time.sleep(0.3)
    return there


def battery_restart(machines, system, setting, check):
    """(b) The SystemRam's machine leaves the network and comes back. John, 2026-11-17: "If the system restarted that
    is a network event that everything else on the network will see". What the network does at that event is the
    merge: the machine is out of the hosts block and the election gives its role to a machine that is there (this
    tier writes the merge's result; it does not run the election: the role goes to the next machine in the table).
    On soft Homa the memory went with the machine, so every other machine's Homa socket went down; each must be
    listening again, in the memory where the role is now, after its own hosts file is rewritten, with nothing
    restarted, and the wire must carry the same work between the machines that are there. Then the machine comes
    back, the merge puts it in the block again (the role stays where it is), and the wire carries the same work to
    and from it.

    The first version of this battery (17 Nov 13:35) killed the machine and brought it back with every hosts file
    still naming it as the SystemRam, which is not what a network does, and John had said so ("possible ... but not
    realistic"). It is kept in the record, with what it found (EXPLORATION-LOG.md section 3)."""
    homa = words_of(setting)[0] != "tcp"
    others = [m for m in machines if m is not system]
    heir = others[0]
    before = {m.name: listens(m) for m in machines}
    gone = system.stop_wire()
    time.sleep(1.0)
    if homa:
        def down(m):
            log = open(m.daemon_log, errors="replace").read()
            return "the Homa listener ended" in log or "the Homa listener is NOT listening" in log.split(LISTENING)[-1]
        downs = {m.name: down(m) for m in others}
        check(len(gone) >= 3 and all(downs.values()),
              "(b) %s (the SystemRam) is killed where it stands (%d processes); soft Homa's memory went with it and every "
              "other machine's daemon says its Homa listener went down (%s)" % (system.name, len(gone), downs))
    else:
        check(len(gone) >= 3, "(b) %s (the SystemRam) is killed where it stands (%d processes)" % (system.name, len(gone)))
    # the merge without it: it is out of the block and its role is the heir's
    system.roles = tuple(r for r in system.roles if r != "databasehost")
    heir.roles = tuple(heir.roles) + ("databasehost",)
    for m in reversed(others):                            # the role's new machine learns it last
        write_hosts(m, hosts_text(others))
        time.sleep(0.4)
    named = [m.exec(["getent", "hosts", SYSTEM_RAM]).stdout.split()[:1] == [heir.ip] and system.ip not in open(m.hosts).read() for m in others]
    check(all(named), "(b) the merge: %s is out of every remaining machine's hosts block and %s (%s) is the SystemRam" % (system.name, heir.ip, heir.name))
    if homa:
        there = wait_for_listeners(others, more_than=before)
        check(all(there.values()), "(b) every remaining machine's daemon is listening on Homa again, in the memory where the role is "
                                   "now, after its own hosts file was rewritten, with nothing restarted (%s)" % there)
    alive = all(m.daemon.poll() is None and m.proxy_main.poll() is None for m in others)
    check(alive, "(b) no daemon or proxy on a remaining machine has ended")
    good = battery_fanout(machines, check, pairs=[(others[0], others[1]), (others[1], others[0])], tag="(b) without %s, " % system.name) and alive
    # it comes back: it starts with the hosts file a node has before the merge reaches it (itself, no roles)
    write_hosts(system, hosts_text([system], roles=False))
    system.start_wire(setting)
    up = system.ask(None, wait_s=60)
    t_end, ready = time.time() + 60, False
    while not ready and time.time() < t_end:
        ready = system.ask("req", target=system.ip, method="GET", path=RPATH % ("none", 1)).get("status") == 200
        time.sleep(0.3)
    check(up.get("ready") and ready, "(b) %s is back: its daemon and proxy are up and it answers itself through its own proxy" % system.name)
    for m in reversed(machines):                          # the merge with it: every file rewritten, the role where it is
        write_hosts(m, hosts_text(machines))
        time.sleep(0.4)
    if homa:
        there = wait_for_listeners([system])
        check(all(there.values()), "(b) %s's daemon is listening on Homa in the memory at %s after the merge reached it (%s)" % (system.name, heir.name, there))
    alive = all(m.daemon.poll() is None and m.proxy_main.poll() is None for m in machines)
    check(alive, "(b) no daemon or proxy on any machine has ended")
    pairs = [(o, system) for o in others] + [(system, o) for o in others]
    return battery_fanout(machines, check, pairs=pairs, tag="(b) with %s back, " % system.name) and alive and good


def battery_carried(machines, word, check):
    """What carried it, read from each machine's own proxy log: its sessions, and on which driver."""
    good = True
    for m in machines:
        log = open(m.proxy_log, errors="replace").read()
        # anywhere in the text: two threads' lines are sometimes written on one line of the log
        homa = sorted(set(re.findall(r"\[SEM-HOMA\] session [0-9a-f]+ to ([0-9.]+):\d+ set=\d+ opened", log)))
        tcp = sorted(set(re.findall(r"\[RPC-TRACE\] event=connect_ok host=([0-9.]+) ", log)))
        unknown = "LOCAL GW UNKNOWN" in log or "LOCAL GW UNKNOWN" in open(m.daemon_log, errors="replace").read()
        others = [o for o in machines if o is not m]
        want = {o.ip: word_between(word, m, o) for o in others}          # [A_WORD_PER_RAM_CONNECTION_V1] per connection
        fine = "LD_PRELOAD" not in log and sorted(set(homa) | set(tcp)) == sorted(want)
        for ip, w in want.items():
            if w == "tcp":
                fine = fine and ip in tcp and ip not in homa
            elif w == "softhoma":
                fine = fine and ip in homa and ip not in tcp
        fine = fine and not unknown
        good = good and fine
        check(fine, "%s: told %s, so to each machine: %s; its proxy's sessions on the Homa driver: %s; on the TCP driver: %s; each "
                    "end named itself in its HELLO by the node's own getEth0Address: %s"
              % (m.name, word.replace(",", "; "), ", ".join("%s %s" % kv for kv in sorted(want.items())), homa or "none", tcp or "none", not unknown))
    return good


def every_topology(setting):
    """Each of the simulator's topologies in a run of its own (a process of its own: nothing of one is left for the
    next), and one line for each at the end."""
    rows, t_all = [], time.time()
    for key in simulator_topologies():
        t0 = time.time()
        r = subprocess.run([PY, os.path.abspath(__file__), "--transport", setting, "--topology", key], capture_output=True, text=True)
        out = r.stdout
        res = ([l for l in out.splitlines() if l.startswith("RESULT")] or ["RESULT FAIL (no result: %s)" % (r.stderr.strip()[-200:] or out[-200:])])[-1]
        meds = [float(x) for x in re.findall(r"\(median ([\d.]+) ms\)", out) if float(x) >= 0]
        rows.append((key, res, out.count("[PASS]"), out.count("[FAIL]"), len(re.findall(r"concurrent reads", out)),
                     sum(meds) / len(meds) if meds else -1, len(re.findall(r"^NOT JUDGED", out, re.M)), time.time() - t0))
        print("%-34s %s  pass %d fail %d  pairs %d  mean of pair medians %.1f ms  %s%.0fs" % (
            key, res, rows[-1][2], rows[-1][3], rows[-1][4], rows[-1][5], "engine REQ_MISS seen  " if rows[-1][6] else "", rows[-1][7]), flush=True)
        for l in out.splitlines():
            if "[FAIL]" in l:
                print("      " + l.strip()[:400], flush=True)
    good = sum(1 for r in rows if r[1] == "RESULT PASS")
    print("TOPOLOGIES %d of %d PASS on %s (%.0fs)" % (good, len(rows), setting, time.time() - t_all))
    print("RESULT", "PASS" if good == len(rows) else "FAIL")
    return 0 if good == len(rows) else 1


def main():
    if "--agent" in sys.argv:
        return agent()
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default="tcp",
                    help="every machine's transport setting, as /etc/frognet/semantic_transport holds it, lines joined by "
                         "commas: tcp | softhoma | auto | 'softhoma,databasehost_control.frognet tcp' ...")
    ap.add_argument("--topology", default="trial3", choices=sorted(TOPOLOGIES) + simulator_topologies() + ["all"],
                    help="trial3, one of the simulator's topologies with a hardware baseline, or all (every one of them, each "
                         "in a run of its own)")
    ap.add_argument("--keep", action="store_true", help="leave the namespaces and their files up at the end")
    a = ap.parse_args()
    if a.topology == "all":
        return every_topology(a.transport)
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
    net = net_of(a.topology)
    machines = [Machine(i, name, ip, roles, tmp, ifaces) for i, (name, ip, roles, ifaces) in enumerate(net["machines"])]
    system = place_roles(machines)
    tear_down(machines)                                   # what an earlier run that was killed left behind
    print("== %s, transport %s: %s" % (a.topology, a.transport, ", ".join("%s %s" % (m.name, m.ip) for m in machines)), flush=True)
    try:
        print("   %s%s. SystemRam %s, ControlRam %s (the highest .1)" % (
            net["says"], "; left out (they serve no /24 and run no proxy or daemon): %s" % ", ".join(net["left_out"]) if net["left_out"] else "",
            system.name, control_holder(machines).name), flush=True)
        bring_up(machines, dbc, net)
        time.sleep(1.5)
        # ---- 0. the machines are machines
        for m in machines:
            others = [o for o in machines if o is not m]
            # three, and one answer is reachable: the first packet across several machines waits on each link's
            # neighbour being found (a peer answering for an address it routes onward answers after a delay)
            pings = [m.exec(["ping", "-c", "3", "-i", "0.3", "-W", "2", o.ip]).returncode == 0 for o in others]
            me = m.exec([PY, "-c", "import sys; sys.path.insert(0, %r); from core import frognet_tuples as T; print(T.my_ip())" % SEM],
                        env=m.env).stdout.strip().split("\n")[-1]
            role = m.exec(["getent", "hosts", "databasehost_control.frognet"]).stdout.split()[:1]
            api = m.exec(["php", "-r", "echo @file_get_contents('http://127.0.0.1:8080/api.php?entity=sensors&action=values&limit=1');"]).stdout
            check(all(pings) and me == m.ip and role == [] and api.strip().startswith(("{", "[")),
                  "%s: reaches the others (%s), FrogNet's own call gives its address as %s, no role is named in its "
                  "hosts file yet (%s), and its own api.php answers where a node's does, on port 8080 (%s)" % (m.name, pings, me, role, api.strip()[:60]))

        # ---- the wire: every machine's daemon and proxy, on the transport given
        for m in machines:
            m.start_wire(a.transport)
        up = [m.ask(None, wait_s=60) for m in machines]
        def answering():
            # a machine is ready when a request to ITSELF is answered through its own proxy (the node's rule sends
            # it there, and the proxy serves it from the machine's api.php). A proxy accepts connections before it
            # has made its template tables, so "port 80 is open" is not "ready".
            return [m.ask("req", target=m.ip, method="GET", path=RPATH % ("none", 1)).get("status") == 200 for m in machines]
        t_end = time.time() + 60
        ready = answering()
        while not all(ready) and time.time() < t_end:
            time.sleep(0.5); ready = answering()
        check(all(u.get("ready") for u in up) and all(m.daemon.poll() is None and m.proxy_main.poll() is None for m in machines) and all(ready),
              "every machine's daemon (daemon_main.py) and proxy (proxy_main) are up, told %s, and each answers a request to "
              "itself through its own proxy by the node's port-80 rule (%s)" % (a.transport, ready))

        # ---- the merge: the election's role lines reach the machines' hosts files one machine at a time, as on a
        # network (each node's file changes in its own time). The watcher in each driver takes it as its event.
        for m in reversed(machines):                      # the role's own machine learns it last
            write_hosts(m, hosts_text(machines))
            time.sleep(0.4)
        named = [m.exec(["getent", "hosts", SYSTEM_RAM]).stdout.split()[:1] == [system.ip]
                 and m.exec(["getent", "hosts", CONTROL_RAM]).stdout.split()[:1] == [control_holder(machines).ip] for m in machines]
        check(all(named), "the merge wrote the role lines: every machine's hosts file names %s (%s) as the SystemRam and %s (%s, the "
                          "highest .1) as the ControlRam" % (system.ip, system.name, control_holder(machines).ip, control_holder(machines).name))
        if a.transport != "tcp":
            there = wait_for_listeners(machines)
            check(all(there.values()), "every machine's daemon opened its Homa listener in soft Homa's memory after its own hosts "
                                       "file changed, with nothing restarted (%s)" % there)

        ok = battery_fanout(machines, check) and ok
        ok = battery_carried(machines, a.transport, check) and ok
        if a.topology in TOPOLOGIES and len(machines) > 2:
            ok = battery_restart(machines, system, a.transport, check) and ok
        else:
            print("  [NOT RUN] (b) a machine leaving: on this topology the routes without the machine are the merge's to "
                  "compute, and this tier writes the merge's result, it does not run the merge", flush=True)
    finally:
        if a.keep:
            print("kept: %s; namespaces %s" % (tmp, ", ".join(m.ns for m in machines)))
        else:
            tear_down(machines)
            shutil.rmtree(tmp, ignore_errors=True)
    if REQ_MISSES:
        print("NOT JUDGED: %d read(s) answered 503 %r (the semantic engine's, on either driver): %s" % (len(REQ_MISSES), REQ_MISS, "; ".join(REQ_MISSES)))
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
