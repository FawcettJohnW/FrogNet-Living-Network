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

The transport is the word given (--transport tcp | chorus | auto). The tier is run on tcp first: the TCP driver
must pass the battery before anything is concluded about another transport, and that run is the control.

Needs root, network namespaces, php (mysqli, curl), socat, MariaDB answering root on its socket, the simulator's
DB_CONFIG (setup.sh), and for chorus the Homa API interface built. Says what is missing and FAILS.

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
        return {"key": key, "machines": [(n, ip, tuple(r), [("eth0", ip + "/8")]) for n, ip, r in t],
                "segments": [[(n, "eth0") for n, _ip, _r in t]], "left_out": [], "says": "one L2 segment"}
    import frognet_sim as H
    topo = getattr(H, key)()
    H.assign_identities(topo)
    base = json.load(open(os.path.join(HERE, "baselines", key + ".json")))
    serves = [n for n, x in topo.nodes.items() if x.frognet_ip and x.frognet_ip.endswith(".1") and x.frognet_ip.startswith("10.")]
    machines = [(n, topo.nodes[n].frognet_ip, (), [(i.name, i.addr_cidr) for i in topo.nodes[n].ifaces.values()]) for n in serves]
    segments = [[(n, i) for n, i in seg if n in serves] for seg in topo.segments]
    return {"key": key, "machines": machines, "segments": [g for g in segments if len(g) > 1],
            "left_out": [n for n in topo.nodes if n not in serves],
            "says": "%s; baseline recorded on %s: %d of %d pairs reachable" % (
                topo.label, base.get("source"), base["reachability"]["pairs_ok"], base["reachability"]["pairs_total"])}


def the_merge(net, machines, alive, why, roles=True, pause=0.4):
    """[A_NETWORK_EVENT_RUNS_THE_MERGE_V1] John, 2026-11-17: "Why doesn't the machine leaving run the merge? Isn't it
    a network event? Those automatically run the merge." and "The simulator needs to trigger the merge, just like
    the real thing."

    Every network event in this tier (the first boot, a machine leaving, a machine returning) runs the merge over
    the network AS IT IS NOW, and what the merge gives is what the machines get. Nothing is written by hand:

      what a node forgets  runMerge.bash begins by flushing core/not_frognet's marks; so does this, on each machine.
      routes and blocks  the simulator's merge, frognet_sim.discover_routes, over the topology with the machines
                         that are not there taken out of it: each node's observe, plan and commit until nothing
                         changes. A machine's routes are that node's committed routes; its hosts block is that
                         node's known hosts (so a machine cut off from another does not have it in its block).
                         trial3 is one segment: every machine there knows every other, and there are no routes.
      the ControlRam     the elector's own rule over the machine's block: the highest .1.
      the SystemRam      the election chooses among capabilities, and this tier does not run the election. Its
                         rule here: the role stays with its holder while the holder is in the block; when it is
                         not, the first machine of the block (the topology's order) that is not the ControlRam's.

    Then each machine's routes are replaced and its hosts file rewritten, one machine at a time, the SystemRam's
    own machine last, as a network's files change each in its own time. The drivers take the rewrite as their
    event. Returns nothing; each machine has .block, .system, .control and .roles after it."""
    names = {m.name for m in alive}
    routes = {m.name: [] for m in alive}
    if net["key"] in TOPOLOGIES:
        for m in alive:
            m.block = list(alive)
    else:
        import contextlib, io
        import frognet_sim as H
        topo = getattr(H, net["key"])()
        gone = {m.name for m in machines} - names
        for n in gone:
            del topo.nodes[n]
        topo.segments = [[(n, i) for n, i in seg if n not in gone] for seg in topo.segments]
        with contextlib.redirect_stdout(io.StringIO()):
            H.discover_routes(topo)
        for m in alive:
            node = topo.nodes[m.name]
            m.block = [o for o in alive if o is m or o.ip in node.known_hosts]
            routes[m.name] = [(r.dest, r.via, r.dev, r.metric) for r in node.routes if r.via]
    for m in machines:
        m.roles = ()
    held = {m.name: getattr(m, "system", None) for m in alive}
    for m in alive:                                       # one election in a block: every member of it names the same two
        m.control = control_holder(m.block)
        still = [held[o.name] for o in m.block if held[o.name] is not None and held[o.name] in m.block]
        rest = [o for o in m.block if o is not m.control] or [m.control]
        m.system = still[0] if still else rest[0]         # the role stays with a holder that is there
    for m in alive:
        m.roles = tuple(r for r, holder in (("databasehost", m.system), ("databasehost_control", m.control)) if holder is m)
    order = sorted(alive, key=lambda m: (m is m.system, -machines.index(m)))     # the SystemRam's own machine last
    for m in order:
        # the first thing a node's merge does (usr/local/bin/runMerge.bash, flush_not_frognet): forget which addresses
        # it had found not to be FrogNet. A machine that was away is one of them on every node that tried it.
        r = m.exec([PY, "-m", "core.not_frognet", "flush"], env=dict(m.env, PYTHONPATH=SEM))
        if r.returncode != 0:
            raise RuntimeError("the merge's flush of not_frognet on %s: %s" % (m.name, (r.stderr or r.stdout).strip()[-300:]))
        for l in m.exec(["ip", "-4", "route", "show"]).stdout.splitlines():
            w = l.split()
            if "via" in w:
                m.exec(["ip", "route", "del", w[0], "via", w[w.index("via") + 1], "dev", w[w.index("dev") + 1]])
        for dest, via, dev, metric in routes[m.name]:
            # "via <the far machine's .1> dev wgN" is not on the link's own /30: WireGuard hands it to the peer; on an
            # ordinary link the kernel is told the same with onlink and the peer answers for what it routes onward.
            # src: a node's installed route carries its identity (discovery.Discovery.dev_src; promote() stamps it).
            r = m.exec(["ip", "route", "add", dest, "via", via, "dev", dev, "onlink", "metric", str(metric), "src", m.ip])
            if r.returncode != 0 and "File exists" not in r.stderr:
                raise RuntimeError("the merge's route %s via %s dev %s on %s: %s" % (dest, via, dev, m.name, r.stderr.strip()))
        write_hosts(m, hosts_text(m, roles))
        time.sleep(pause)
    # [A_MERGE_IS_READ_BY_EVERY_NODE_V1] John, 2026-11-17: "There should not be any communications except between the
    # machine and the ram host" and "Notification does not propagate between nodes. That is a violation. Use held
    # reads." A node's converged merge writes its facts into the RAM (usr/local/bin/runMerge.bash, region mode:
    # "the facts write is the notification"); every node has a read held on the RAM, that read returns, and the node
    # runs its own merge, which rewrites its hosts file. So the LAST node to change is followed by a merge on every
    # other node. This tier had written each machine's files once and stopped, so a machine whose files were written
    # before the SystemRam's machine had its routes never had another event, though on a network it would have
    # (found on topo_dual_wg_transit_bridge and topo_multilan_shared_wg_transit, 17 Nov). Here: after the last
    # machine's files are in place, each other machine's hosts file is rewritten once more, as by its own merge
    # (same routes; the file may say the same thing). Nothing passes from one machine to another.
    for m in order[:-1]:
        write_hosts(m, hosts_text(m, roles))
        time.sleep(pause / 4)
    print("   the merge (%s): %s" % (why, "; ".join("%s knows %d, SystemRam %s, ControlRam %s" % (
        m.name, len(m.block), m.system.name, m.control.name) for m in alive)), flush=True)


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
        word = getattr(self, "setting", None) or word     # [THE_FILE_SAYS_WHAT_A_NODE_SENDS_V1] --tcp-machine: its own file
        env = dict(self.env, FROGNET_SEM_TRANSPORT=word)
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

    def timed(lists, seconds):
        """[CHANNEL_SWAP_TIER_V1] Each list is one program repeating its requests until `seconds` have passed. Every
        request is returned as (when it was sent, when it was answered, its status, whether the answer is right)."""
        end, out = time.time() + seconds, [[] for _ in lists]

        def right(q, g):
            if g["status"] != 200:
                return False
            try:
                d = json.loads(g["body"])
            except ValueError:
                return False
            if q["method"] == "GET":
                t, k = q["path"].split("SensorType=")[1].split("&")[0], int(q["path"].split("limit=")[1].split("&")[0])
                rows = d.get("rows", [])
                return len(rows) == k and all(x.get("SensorType") == t for x in rows)
            return bool(d.get("ok"))

        def work(k):
            i = 0
            while time.time() < end:
                q = lists[k][i % len(lists[k])]; i += 1
                t0 = time.time()
                g = req(q["target"], q["method"], q["path"], q.get("body"))
                out[k].append([t0, time.time(), g["status"], right(q, g), g["body"][:160] if g["status"] != 200 else ""])
        ts = [threading.Thread(target=work, args=(k,)) for k in range(len(lists))]
        for t in ts: t.start()
        for t in ts: t.join()
        return out

    print(MARK + json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        c = json.loads(line)
        try:
            if c["op"] == "timed":
                r = timed(c["lists"], c["seconds"])
            elif c["op"] == "req":
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


# [ROLE_MEMORIES_V1] A tier with more memories than the two every network has names each here: the memory's host name
# -> the Machine that holds it. Their role lines are written with the election's, in every hosts file whose machine
# knows the holder. Empty for every tier that does not set it.
MORE_MEMORIES = {}


def hosts_text(m, roles=True):
    """A node's hosts file after a merge: every machine in ITS block and, once the election has run, the role lines."""
    out = ["127.0.0.1 localhost"]
    for o in m.block:
        out.append("%s FrogNetHost.%s %s" % (o.ip, o.name, o.name))
    if roles:
        out.append("%s databasehost.frognet" % m.system.ip)
        out.append("%s databasehost_control.frognet" % m.control.ip)
        for name in sorted(MORE_MEMORIES):
            if MORE_MEMORIES[name] in m.block:
                out.append("%s %s" % (MORE_MEMORIES[name].ip, name))
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
        m.block = [m]
        write_hosts(m, hosts_text(m, roles=False))           # booted; no merge has run: it knows itself and no role
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
        # each machine's own /etc/sentinels (core/not_frognet.py's list of addresses it found not to be FrogNet).
        # Until 17 Nov 20:40 every machine shared the simulator host's one file, so one machine's mark was all of theirs.
        m.env["FROGNET_SENTINEL_DIR"] = os.path.join(os.path.dirname(m.hosts), "sentinels")
        os.makedirs(m.env["FROGNET_SENTINEL_DIR"], exist_ok=True)
        # [THE_DOOR_ON_A_MACHINE_V1] the door tier's door where a node's door is: /dc/ram.php in the web root, served by
        # php on 8080, which hands each request to simulation/door_origin.py on this machine
        import door_origin
        os.mkdir(os.path.join(web, "dc"))
        open(os.path.join(web, "dc", "ram.php"), "w").write(door_origin.RAM_PHP % DOOR_PORT)
        m.run([PY, os.path.join(HERE, "door_origin.py"), str(DOOR_PORT)], env=m.env,
              stdout=open(os.path.join(m.dir, "door.log"), "w"), stderr=subprocess.STDOUT)
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
                    miss += 1; REQ_MISSES.append("%s -> %s %s" % (a.name, b.name, q["path"][-44:]))   # counted, and it FAILS (below)
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
                                                       "" if not miss else " [%d of the failures are 503 %r, the semantic engine's]" % (miss, REQ_MISS),
                                                       "" if fine else " -- WRONG: warm %s; %d bad %s; same=%s; last=%s" % (
                                                           [w.get("status") for w in warm], len(bad), bad[:3], same, {n: last.get(n) for n in mine})))
    return good


# [A_FAILURE_IS_A_FAILURE_V1] John, 2026-11-17: "If it is a legitimate failure, it is a legitimate failure and needs to
# be treated as such." From 14:00 to 20:04 that day this tier counted a read answered 503 with the reason below and
# did not hold it against the pair, because it is the semantic engine's and happens on TCP as on Homa. A request
# that went in and came back 503 failed, whoever's fault. It fails the pair again. It is still counted by name, so
# the result says how many of the failures were this one (EXPLORATION-LOG.md section 7, item 0a).
DOOR_PORT = 8091
LINK_PORT = 9700
TCP_LISTENING = "[RETURN_ONLY_V1] listening on"


def shape_links(machines, links, check):
    """[SHAPED_LINKS_BETWEEN_MACHINES_V1] John, 2026-11-17: "Wire in the shaped links." Each machine's uplink is given
    a bearer's delay, jitter, bandwidth and loss by simulation/shaped_link.py (the simulator's own shaper,
    slowlink.py, on every TCP connection the machine makes to another machine). `links` is a profile's name for
    every machine, or "mixed" for one drawn per machine. Before any daemon or proxy starts."""
    import shaped_link
    said = {}
    for m in machines:
        m.link = shaped_link.profile_for(links, m.n)
    for m in machines:
        log = os.path.join(m.dir, "link.log")
        # [A_LINK_HAS_TWO_ENDS_V1] each uplink is told the other machines' uplinks: a connection has both
        m.run([PY, os.path.join(HERE, "shaped_link.py"), "--port", str(LINK_PORT), "--src", m.ip, "--profile", m.link,
               "--seed", str(m.n), "--peers", ",".join("%s=%s" % (o.ip, o.link) for o in machines if o is not m)],
              env=m.env, stdout=open(log, "w"), stderr=subprocess.STDOUT)
        t_end = time.time() + 20
        while "[SHAPED-LINK]" not in open(log).read() and time.time() < t_end:
            time.sleep(0.1)
        said[m.name] = "[SHAPED-LINK]" in open(log).read()
        for rule in (["-A", "OUTPUT", "-m", "mark", "--mark", "2", "-j", "RETURN"],
                     ["-A", "OUTPUT", "-p", "tcp", "-d", m.ip + "/32", "!", "--dport", "80", "-j", "RETURN"],
                     ["-A", "OUTPUT", "-p", "tcp", "-d", "10.0.0.0/8", "!", "--dport", "80", "-j", "REDIRECT", "--to-ports", str(LINK_PORT)]):
            r = m.exec(["iptables", "-t", "nat"] + rule)
            if r.returncode != 0:
                raise RuntimeError("iptables %s in %s: %s" % (" ".join(rule), m.ns, r.stderr.strip()))
    import slowlink
    check(all(said.values()), "shaped links: every machine's TCP to another machine goes through its own uplink and the far machine's: %s" % "; ".join(
        "%s %s (%s)" % (m.name, m.link, slowlink.profile(m.link).label()) for m in machines))


def wait_daemons(machines, setting, wait_s=60):
    """A machine is there when its daemon is LISTENING, by the daemon's own log line, not when its proxy answers.
    Found 17 Nov 21:50: a returned machine answered itself through its proxy (which needs no daemon) while its
    daemon was still starting, the tier called it back, and the next machine's requests to it were refused."""
    words = set([words_of(setting)[0]]) | set(words_of(setting)[1].values())
    if not (words & {"tcp", "auto"}):
        return {m.name: True for m in machines}          # no TCP listener is told to exist; the Homa one is waited for by name
    t_end, there = time.time() + wait_s, {}
    while time.time() < t_end:
        there = {m.name: TCP_LISTENING in open(m.daemon_log, errors="replace").read() for m in machines}
        if all(there.values()):
            break
        time.sleep(0.2)
    return there


def battery_large(machines, check):
    """(c) Large frames, each way, with contents that change and do not compress, and a short frame while a long one
    is in flight. The origin is the door on the target machine (simulation/door_origin.py): op=blob returns n hex
    characters this side can recompute from the seed; op=echo returns the length and SHA-256 of what the origin
    was GIVEN, in canonical form (the wire carries JSON by its values). A large frame is many fields of 50,000
    characters: the semantic codec's string field holds 65,535 bytes and refuses a longer one with that reason
    (found here, 17 Nov 22:10, with one field of 100,000). Sizes cross the Homa driver's message limit (240,000 bytes on chorus, then soft Homa), so above it a frame goes
    in pieces and comes back on spares. Each machine asks the next one in the topology's order."""
    import hashlib
    import door_origin
    import slowlink
    ok_all = True
    nonce = "%d" % (time.time() * 1000)
    # [A_FRAME_THE_LINK_CAN_CARRY_V1] John, 18 Nov, asked whether a frame a link cannot carry in the driver's budget is
    # a driver failure or the right answer when the app is told: the right answer. So on shaped links a pair is sent
    # only the sizes its link carries in FITS_S seconds (the narrower of the two machines' uplinks; TCP's own budget
    # for an RPC is 30 s), and ONE pair per topology whose link cannot carry 3,000,000 characters in that time is sent
    # it anyway, to see it end with a said error and not with wrong data or silence.
    FITS_S, over_done = 20.0, False

    def rate_of(a, b):
        links = [slowlink.profile(m.link) for m in (a, b) if getattr(m, "link", None)]
        return min(l.rate_Bps for l in links) if links else None
    ring = [(machines[i], machines[(i + 1) % len(machines)]) for i in range(len(machines))] if len(machines) > 1 else []
    for a, b in ring:
        rows, fine = [], True
        rate = rate_of(a, b)
        sizes = [n for n in (1000, 100000, 300000, 1000000, 3000000) if rate is None or n / rate <= FITS_S]
        if len(sizes) < 5:
            rows.append("this pair's link carries %.0f kb/s, so sizes over %s are not sent (they need more than %.0f s)" % (
                rate * 8 / 1e3, format(sizes[-1], ","), FITS_S))
        for size in sizes:
            seed = "%s-%s-%d-%s" % (a.name, b.name, size, nonce)
            g = a.ask("req", wait_s=300, target=b.ip, method="GET", path="/dc/ram.php?op=blob&n=%d&seed=%s" % (size, seed))
            try:
                got_down = json.loads(g["body"])
                down = g["status"] == 200 and got_down.get("data") == door_origin.parts(size, seed)
            except (ValueError, KeyError, TypeError):
                got_down, down = {}, False
            body = json.dumps({"pad": door_origin.parts(size, "up-" + seed)})
            canon = door_origin.canonical(body)
            p = a.ask("req", wait_s=300, target=b.ip, method="POST", path="/dc/ram.php?op=echo", body=body)
            try:
                got = json.loads(p["body"])
                up = p["status"] == 200 and got.get("n") == len(canon) and got.get("sha") == hashlib.sha256(canon.encode()).hexdigest()
            except (ValueError, KeyError, TypeError):
                up = False
            if not down and g.get("status") == 200:
                # which answer is it, then? (the seed and size it says it is, and how many of its fields are not this request's)
                try:
                    w = door_origin.parts(size, seed); d = got_down.get("data") or {}
                    bad = [k for k in sorted(w) if d.get(k) != w[k]]
                    g = dict(g, body="says seed=%s (asked %s) n=%s; %d fields (want %d); %d fields not this request's: %s; the first of them is "
                                     "%d characters, starts %s, want %s" % (got_down.get("seed"), seed, got_down.get("n"), len(d), len(w), len(bad), bad[:6],
                                     len(d.get(bad[0], "")) if bad else 0, (d.get(bad[0]) or "")[:12] if bad else "", w[bad[0]][:12] if bad else ""))
                except Exception as e:
                    g = dict(g, body="unreadable: %r" % e)
            rows.append("%d: reply %s %.0f ms, request %s %.0f ms" % (size, "exact" if down else "WRONG (%s %s)" % (g.get("status"), str(g.get("body"))[:420]),
                                                                    g.get("ms", -1), "exact" if up else "WRONG (%s %s)" % (p.get("status"), str(p.get("body"))[:80]), p.get("ms", -1)))
            fine = fine and down and up
        # a short frame while a long one is in flight: one program fetching 3,000,000 characters, four asking small things
        seed = "%s-%s-long-%s" % (a.name, b.name, nonce)
        small = {"target": b.ip, "method": "GET", "path": "/dc/ram.php?op=rams&app=openeoc"}
        longest = sizes[-1]
        lists = [[{"target": b.ip, "method": "GET", "path": "/dc/ram.php?op=blob&n=%d&seed=%s" % (longest, seed)}]] + [[small] * 8 for _ in range(4)]
        out = a.ask("streams", wait_s=300, lists=lists)
        try:
            long_ok = out[0][0]["status"] == 200 and json.loads(out[0][0]["body"])["data"] == door_origin.parts(longest, seed)
            shorts = [r for l in out[1:] for r in l]
            short_ok = all(r["status"] == 200 and json.loads(r["body"]).get("ok") is True for r in shorts)
            long_ms, short_ms = out[0][0]["ms"], sorted(r["ms"] for r in shorts)
            first = short_ms[0] < long_ms
        except (ValueError, KeyError, TypeError, IndexError):
            long_ok = short_ok = first = False; long_ms, short_ms = -1, [-1]
        fine = fine and long_ok and short_ok and first
        check(fine, "(c) %s -> %s: frames of 1,000 to %s characters each way, contents recomputed and hashed at the far end [%s]; "
                    "and 32 short requests during one of %s: all exact, the long one %.0f ms, the short ones %.0f ms median, "
                    "the first of them back in %.0f ms" % (a.name, b.name, format(longest, ","), "; ".join(rows), format(longest, ","),
                                                           long_ms, short_ms[len(short_ms) // 2], short_ms[0]))
        if rate is not None and 3000000 / rate > FITS_S and not over_done:
            over_done = True
            seed = "%s-%s-over-%s" % (a.name, b.name, nonce)
            t0 = time.time()
            g = a.ask("req", wait_s=300, target=b.ip, method="GET", path="/dc/ram.php?op=blob&n=3000000&seed=%s" % seed)
            took = time.time() - t0
            try:
                exact = g.get("status") == 200 and json.loads(g["body"]).get("data") == door_origin.parts(3000000, seed)
            except (ValueError, KeyError, TypeError):
                exact = False
            said = g.get("status") == 503 and any(w in str(g.get("body")) for w in ("Timeout", "budget", "no reply in"))
            check(exact or said, "(c) %s -> %s: 3,000,000 characters over a link of %.0f kb/s, which needs %.0f s or more: %s in %.0f s"
                  % (a.name, b.name, rate * 8 / 1e3, 3000000 / rate,
                     "it arrived exact" if exact else "the driver ended it and said so (%s)" % str(g.get("body"))[:140] if said
                     else "WRONG: neither exact nor a said time limit (%s %s)" % (g.get("status"), str(g.get("body"))[:200]), took))
            time.sleep(max(0.0, 3000000 * 1.4 / rate - took))    # what was abandoned is still on the link: let it pass before the next pair
        ok_all = ok_all and fine
    return ok_all


def battery_door(machines, check):
    """(d) The door tier's scenarios (simulation/door_semantic_tier.py SCENARIOS, unchanged), between machines: the
    asking machine is the phone's node, the origin machine has the door behind its web server. Every answer through
    the wire must equal the door's own answer (door_semantic_tier.door on the same configuration), and the door
    must have been GIVEN exactly the logins and enrolments that were sent, in order. A call marked LOCAL in a
    scenario is made on the origin machine itself, through its own proxy, as in the door tier."""
    import door_semantic_tier as D
    ok_all = True
    pairs = [(machines[0], machines[1])] + ([(machines[-1], machines[0])] if len(machines) > 2 else [])
    for asking, origin in pairs:
        for name, seq in D.SCENARIOS:
            D.CONFIG.clear(); D.CONFIG.update(json.loads(json.dumps(D.CONFIG0)))
            origin.ask("local", method="GET", path="/dc/ram.php?__door=reset")
            sent, wrong, n = [], [], 0
            for c in seq():
                if c[0] == "CONFIG":
                    c[1]()                                                       # this side's copy of the door's configuration
                    origin.ask("local", method="GET", path="/dc/ram.php?__door=add_role&name=" + c[2].split()[1])
                    continue
                local = c[0] == "LOCAL"
                method, q, body = c[1:] if local else c
                path = "/dc/ram.php?" + q
                raw = json.dumps(body) if body is not None else None
                want_st, want = D.door(method, path, raw or "")
                if body is not None:
                    sent.append([q.split("&")[0][3:], body["user"], body["password"]])
                g = (origin if local else asking).ask("req", target=origin.ip, method=method, path=path, body=raw)
                n += 1
                try:
                    same = json.loads(g["body"]) == json.loads(want)
                except (ValueError, KeyError, TypeError):
                    same = False
                if not (same and g.get("status") == want_st):
                    wrong.append("%d %s %s%s: HTTP %s %s (the door says %s %s)" % (n, method, q.split("&")[0], " on the origin itself" if local else "",
                                                                                  g.get("status"), str(g.get("body"))[:120], want_st, want[:120]))
            given = json.loads(origin.ask("local", method="GET", path="/dc/ram.php?__door=received")["body"])
            fine = not wrong and given == sent
            check(fine, "(d) %s asks %s's door, \"%s\": %d calls, every answer the door's own, and the door was given the %d logins "
                        "and enrolments that were sent, in order%s" % (asking.name, origin.name, name, n, len(sent),
                        "" if fine else " -- WRONG: %s; sent %d, given %d%s" % ("; ".join(wrong[:3]) or "answers right", len(sent), len(given),
                        "" if given == sent else ", first difference %s" % next(((s_, g_) for s_, g_ in zip(sent, given) if s_ != g_), (sent[len(given):][:1], given[len(sent):][:1])))))
            ok_all = ok_all and fine
    return ok_all


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


def battery_restart(net, machines, system, setting, check):
    """(b) The SystemRam's machine leaves the network and comes back. John, 2026-11-17: "If the system restarted that
    is a network event that everything else on the network will see", and of this battery: "Isn't it a network
    event? Those automatically run the merge."

    The machine is killed where it stands. That is a network event, so the merge runs over the network without it
    (the_merge): the routes are the merge's for the topology that is left, each remaining machine's block is what
    it can still reach, and the role goes to a machine that is there. Where the machine was the only way between
    two others, they are in different blocks afterwards, each with its own roles, and are not asked to reach each
    other. On chorus the memory went with the machine, so every other machine's Homa socket went down; each must
    be listening again, in the memory where ITS block's role is now, with nothing restarted, and the wire must
    carry the same work inside each block. Then the machine comes back, which is a network event, the merge runs
    over the whole network, and the wire carries the same work to and from it.

    History: the first version (17 Nov 13:35) kept every hosts file naming the dead machine; the second (13:50)
    wrote the merge's result by hand and so could run on one flat topology only. EXPLORATION-LOG.md section 3."""
    homa = words_of(setting)[0] != "tcp"
    others = [m for m in machines if m is not system]
    before = {m.name: listens(m) for m in machines}
    gone = system.stop_wire()
    time.sleep(1.0)
    check(len(gone) >= 2, "(b) %s (the SystemRam) is killed where it stands (%d processes)" % (system.name, len(gone)))
    the_merge(net, machines, others, "%s left" % system.name)
    named = [m.exec(["getent", "hosts", SYSTEM_RAM]).stdout.split()[:1] == [m.system.ip] and m.system is not system
             and system.ip not in open(m.hosts).read() for m in others]
    blocks = sorted(set(tuple(o.name for o in m.block) for m in others))
    check(all(named), "(b) the merge ran without %s: it is out of every remaining machine's hosts block, and each block has a "
                      "SystemRam that is there (%d block%s: %s)" % (system.name, len(blocks), "" if len(blocks) == 1 else "s",
                      "; ".join("%s, SystemRam %s" % ("+".join(b), next(m for m in others if m.name == b[0]).system.name) for b in blocks)))
    if homa:
        # [WE_OWN_THE_WIRE_V1] a machine leaving ends the connections to it and nothing else: no listener closes
        there = {m.name: listens(m) == before.get(m.name) and listens(m) >= 1 for m in others}
        check(all(there.values()), "(b) every remaining machine's daemon is still listening on Homa as it was: a machine "
                                   "leaving closed no listener and nothing was restarted (%s)" % there)
    alive = all(m.daemon.poll() is None and m.proxy_main.poll() is None for m in others)
    check(alive, "(b) no daemon or proxy on a remaining machine has ended")
    pairs = [(a, b) for a in others for b in others if a is not b and b in a.block]
    good = alive
    if pairs:
        good = battery_fanout(machines, check, pairs=pairs, tag="(b) without %s, " % system.name) and good
    # it comes back: it starts as a node does before any merge reaches it (it knows itself, and no role)
    system.block = [system]
    system.system = None
    write_hosts(system, hosts_text(system, roles=False))
    for name in ("daemon.log", "proxy.log"):              # what it said before it left is kept: its new life starts new logs
        old = os.path.join(system.dir, name)
        if os.path.exists(old):
            shutil.copyfile(old, old + ".before-it-left")
    system.start_wire(setting)
    up = system.ask(None, wait_s=60)
    t_end, ready = time.time() + 60, False
    while not ready and time.time() < t_end:
        ready = system.ask("req", target=system.ip, method="GET", path=RPATH % ("none", 1)).get("status") == 200
        time.sleep(0.3)
    heard = wait_daemons([system], setting)
    check(up.get("ready") and ready and all(heard.values()),
          "(b) %s is back: its daemon is listening, its proxy is up and it answers itself through its own proxy" % system.name)
    before = {m.name: listens(m) for m in machines}
    moved = {m.name: m.system for m in others}
    the_merge(net, machines, machines, "%s returned" % system.name)
    whole = all(len(m.block) == len(machines) and m.system is machines[0].system for m in machines)
    check(whole, "(b) the merge ran with %s back: every machine knows all %d again and names %s as the SystemRam" % (
        system.name, len(machines), machines[0].system.name))
    if homa:
        there = wait_for_listeners([system])
        check(all(there.values()), "(b) %s's daemon is listening on Homa (%s)" % (system.name, there))
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
        # [A_WORD_PER_RAM_CONNECTION_V1] per connection. [THE_FILE_SAYS_WHAT_A_NODE_SENDS_V1] the word is the SENDER's
        want = {o.ip: word_between(getattr(m, "setting", None) or word, m, o) for o in others}
        fine = "LD_PRELOAD" not in log and sorted(set(homa) | set(tcp)) == sorted(want)
        for ip, w in want.items():
            if w == "tcp":
                fine = fine and ip in tcp and ip not in homa
            elif w == "chorus":
                fine = fine and ip in homa and ip not in tcp
        fine = fine and not unknown
        good = good and fine
        check(fine, "%s: told %s, so to each machine: %s; its proxy's sessions on the Homa driver: %s; on the TCP driver: %s; each "
                    "end named itself in its HELLO by the node's own getEth0Address: %s"
              % (m.name, word.replace(",", "; "), ", ".join("%s %s" % kv for kv in sorted(want.items())), homa or "none", tcp or "none", not unknown))
    return good


def every_topology(setting, links="none"):
    """Each of the simulator's topologies in a run of its own (a process of its own: nothing of one is left for the
    next), and one line for each at the end."""
    rows, t_all = [], time.time()
    for key in simulator_topologies():
        t0 = time.time()
        r = subprocess.run([PY, os.path.abspath(__file__), "--transport", setting, "--topology", key, "--links", links], capture_output=True, text=True)
        out = r.stdout
        res = ([l for l in out.splitlines() if l.startswith("RESULT")] or ["RESULT FAIL (no result: %s)" % (r.stderr.strip()[-200:] or out[-200:])])[-1]
        meds = [float(x) for x in re.findall(r"\(median ([\d.]+) ms\)", out) if float(x) >= 0]
        rows.append((key, res, out.count("[PASS]"), out.count("[FAIL]"), len(re.findall(r"concurrent reads", out)),
                     sum(meds) / len(meds) if meds else -1, len(re.findall(r"^OF THE FAILURES", out, re.M)), time.time() - t0))
        print("%-34s %s  pass %d fail %d  pairs %d  mean of pair medians %.1f ms  %s%.0fs" % (
            key, res, rows[-1][2], rows[-1][3], rows[-1][4], rows[-1][5], "engine REQ_MISS seen  " if rows[-1][6] else "", rows[-1][7]), flush=True)
        for l in out.splitlines():
            if "[FAIL]" in l:
                print("      " + l.strip()[:400], flush=True)
    good = sum(1 for r in rows if r[1] == "RESULT PASS")
    print("TOPOLOGIES %d of %d PASS on %s, links %s (%.0fs)" % (good, len(rows), setting, links, time.time() - t_all))
    print("RESULT", "PASS" if good == len(rows) else "FAIL")
    return 0 if good == len(rows) else 1


def _keep_what_is_said(a):
    """[A_TIER_KEEPS_WHAT_IT_SAID_V1] run_all.py runs this tier with its output thrown away. Found 22 Nov 2026: the
    all-topologies run failed inside the full simulator twice and passed alone, and there was nothing to read.
    Everything this run prints is also kept in /tmp, one file for each transport and topology, replaced each run."""
    import re
    name = "/tmp/wire_between_machines_tier.%s.%s.last.txt" % (re.sub(r"[^A-Za-z0-9_.-]+", "_", a.transport), re.sub(r"[^A-Za-z0-9_.-]+", "_", a.topology))
    try:
        kept = open(name, "w", buffering=1)
    except OSError:
        return
    out = sys.stdout

    class Both:
        def write(self, text):
            try:
                kept.write(text)
            except (OSError, ValueError):
                pass
            return out.write(text)

        def flush(self):
            out.flush()

        def __getattr__(self, n):
            return getattr(out, n)
    sys.stdout = Both()


def main():
    if "--agent" in sys.argv:
        return agent()
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default="tcp",
                    help="every machine's transport setting, as /etc/frognet/semantic_transport holds it, lines joined by "
                         "commas: tcp | chorus | auto | 'chorus,databasehost_control.frognet tcp' ...")
    ap.add_argument("--tcp-machine", default="",
                    help="one machine whose own file says tcp, whatever --transport gives the others (Seattle3B, say)")
    ap.add_argument("--topology", default="trial3", choices=sorted(TOPOLOGIES) + simulator_topologies() + ["all"],
                    help="trial3, one of the simulator's topologies with a hardware baseline, or all (every one of them, each "
                         "in a run of its own)")
    ap.add_argument("--links", default="none",
                    help="each machine's uplink: none (a perfect link), a profile of simulation/slowlink.py (lan, wifi, 4g, halow, "
                         "geo, wan-100ms, 4g-lossy, wifi-lossy, halow-lossy, jammed), or mixed (one drawn per machine)")
    ap.add_argument("--keep", action="store_true", help="leave the namespaces and their files up at the end")
    a = ap.parse_args()
    _keep_what_is_said(a)
    if a.topology == "all":
        return every_topology(a.transport, a.links)
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg), flush=True)

    need = missing()
    if need:
        print("RESULT FAIL: this tier needs " + "; ".join(need))
        return 1
    dbc = json.load(open(os.environ["FROGNET_DB_CONFIG"]))
    tmp = tempfile.mkdtemp(prefix="wire-machines-")
    net = net_of(a.topology)
    machines = [Machine(i, name, ip, roles, tmp, ifaces) for i, (name, ip, roles, ifaces) in enumerate(net["machines"])]
    tear_down(machines)                                   # what an earlier run that was killed left behind
    print("== %s, transport %s: %s" % (a.topology, a.transport, ", ".join("%s %s" % (m.name, m.ip) for m in machines)), flush=True)
    try:
        print("   %s%s" % (net["says"], "; left out (they serve no /24 and run no proxy or daemon): %s" % ", ".join(net["left_out"])
                                     if net["left_out"] else ""), flush=True)
        bring_up(machines, dbc, net)
        if a.links != "none":
            shape_links(machines, a.links, check)
        the_merge(net, machines, machines, "the machines found each other; no election yet", roles=False, pause=0)
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
            # [THE_FILE_SAYS_WHAT_A_NODE_SENDS_V1] John's network, 22 Nov: one node's file says tcp, the others send it
            # framed connections. Its daemon takes them; what it sends is tcp.
            m.setting = "tcp" if m.name == a.tcp_machine else None
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
        heard = wait_daemons(machines, a.transport)
        check(all(u.get("ready") for u in up) and all(m.daemon.poll() is None and m.proxy_main.poll() is None for m in machines) and all(ready)
              and all(heard.values()),
              "every machine's daemon (daemon_main.py) is listening and its proxy (proxy_main) is up, told %s, and each answers a "
              "request to itself through its own proxy by the node's port-80 rule (%s)" % (a.transport, ready))

        # ---- the merge: the election's role lines reach the machines' hosts files one machine at a time, as on a
        # network (each node's file changes in its own time). The watcher in each driver takes it as its event.
        the_merge(net, machines, machines, "the election ran")
        system = machines[0].system
        named = [m.exec(["getent", "hosts", SYSTEM_RAM]).stdout.split()[:1] == [m.system.ip]
                 and m.exec(["getent", "hosts", CONTROL_RAM]).stdout.split()[:1] == [m.control.ip] for m in machines]
        check(all(named) and all(m.system is system and len(m.block) == len(machines) for m in machines),
              "the merge ran: every machine knows all %d and its hosts file names %s (%s) as the SystemRam and %s (%s, the "
              "highest .1) as the ControlRam" % (len(machines), system.ip, system.name, machines[0].control.ip, machines[0].control.name))
        if a.transport != "tcp":
            there = wait_for_listeners(machines)
            check(all(there.values()), "every machine's daemon is listening on Homa (%s)" % there)

        ok = battery_fanout(machines, check) and ok
        ok = battery_carried(machines, a.transport, check) and ok
        ok = battery_large(machines, check) and ok
        if len(machines) > 1:
            ok = battery_door(machines, check) and ok
        if len(machines) > 1:
            ok = battery_restart(net, machines, system, a.transport, check) and ok
    finally:
        if a.keep:
            print("kept: %s; namespaces %s" % (tmp, ", ".join(m.ns for m in machines)))
        else:
            tear_down(machines)
            shutil.rmtree(tmp, ignore_errors=True)
    if REQ_MISSES:
        print("OF THE FAILURES: %d read(s) answered 503 %r (the semantic engine's, on either driver): %s" % (len(REQ_MISSES), REQ_MISS, "; ".join(REQ_MISSES)))
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
