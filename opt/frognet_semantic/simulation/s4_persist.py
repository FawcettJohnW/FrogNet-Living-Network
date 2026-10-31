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
s4_persist.py - [S4_PERSIST_V1] John Day's question (2026-10-30): "There is no guarantee that the inconsistent data used
in the last update will be consistent in the next ... across a network that large inconsistencies could easily persist
for several updates." How long does an IS-IS inconsistency -- and any forwarding loop it causes -- last, counted in time
AND in SPF runs, as the network grows?

TIER 4 (real kernel): every node is a network namespace running real FRR zebra + isisd; every link is a veth pair with
a netem one-way delay. Nothing is modeled. Generic graphs (not the bed's hub shape): ring, grid, mesh, each at a size.

What is measured, exactly (no sampling):
  FIB     `ip -ts monitor route` in every namespace from before the event until quiet: every route install, replace and
          delete with a microsecond timestamp, one clock for all nodes (one machine). Replaying them gives every node's
          next hop for every destination at every instant.
  SPF     isisd logs each SPF run (debug isis spf-events, microsecond timestamps): the "updates" Day counts.
  loops   at every instant, for every destination, a cycle in the next-hop graph is a forwarding loop. Each distinct loop
          gets its lifetime and the number of SPF runs its member nodes made while it lived.
  window  per destination, event -> the last FIB change for it (the inconsistency window).

Events: cut (carrier down on one link: detected at once), silent (100% loss on one link: detected by the hold timer),
partition (silent loss on every link across a cut that splits the graph in two), nodefail (a node's links go silent).

The detector ships with negative tests (selftest): injected 2- and 3-node loops MUST be found, a clean timeline MUST read 0.

usage:
  s4_persist.py selftest
  s4_persist.py run --topo ring|grid|mesh --n N --event cut|silent|partition|nodefail [--profile tuned|default]
                    [--seed S] [--wan-ms 35] [--lan-ms 1] [--sites 3] [--out DIR]
"""
import argparse
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
from datetime import datetime

BUILD = "s4-persist-2026-10-31-r1"
NS = "p-"
FRR = os.environ.get("FRR_BIN", "/usr/lib/frr")
SOCK = "/run/s4p"


def log(*a):
    print(f"[{BUILD}]", *a, flush=True)


def sh(*argv, ns=None, check=True):
    cmd = (["ip", "netns", "exec", NS + str(ns)] if ns is not None else []) + [str(a) for a in argv]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip()}")
    return r


# ----------------------------------------------------------------------------------------------------------- topology
def topology(kind, n, seed=1, sites=3, wan_ms=35.0, lan_ms=1.0):
    """-> {"nodes": [i...], "site": {i: s}, "links": [(a, b, delay_ms)]}. Nodes are split into `sites` contiguous
    blocks (like Seattle / New York / San Francisco); a link inside a site gets lan_ms, a link between sites wan_ms."""
    rnd = random.Random(seed)
    nodes = list(range(n))
    site = {i: min(sites - 1, i * sites // n) for i in nodes}
    edges = set()
    if kind == "ring":
        for i in nodes:
            edges.add(tuple(sorted((i, (i + 1) % n))))
    elif kind == "grid":
        c = max(2, int(round(n ** 0.5)))
        for i in nodes:
            r, k = divmod(i, c)
            if k + 1 < c and i + 1 < n:
                edges.add((i, i + 1))
            if i + c < n:
                edges.add((i, i + c))
    elif kind == "mesh":
        # a connected random graph of average degree ~4: a random spanning tree plus extra edges, more likely nearby
        order = nodes[:]
        rnd.shuffle(order)
        for j in range(1, n):
            a, b = order[j], order[rnd.randrange(j)]
            edges.add(tuple(sorted((a, b))))
        target = 2 * n
        while len(edges) < target:
            a = rnd.randrange(n)
            b = (a + rnd.choice([-3, -2, -1, 1, 2, 3, rnd.randrange(n)])) % n
            if a != b:
                edges.add(tuple(sorted((a, b))))
    else:
        raise ValueError(kind)
    links = [(a, b, lan_ms if site[a] == site[b] else wan_ms) for a, b in sorted(edges)]
    return {"kind": kind, "n": n, "seed": seed, "nodes": nodes, "site": site, "links": links}


def lo_addr(i):
    return f"10.99.{i // 256}.{i % 256}"


def link_addrs(j):
    """link j -> (a-side, b-side) /31 addresses in 100.64.0.0/10"""
    v = (100 << 24) | (64 << 16) | (2 * j)
    f = lambda x: f"{x >> 24 & 255}.{x >> 16 & 255}.{x >> 8 & 255}.{x & 255}"
    return f(v), f(v + 1)


def components(nodes, edges):
    """{node: component id} over undirected edges"""
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in edges:
        parent[find(a)] = find(b)
    return {n: find(n) for n in nodes}


def split(t):
    """a cut that splits the graph roughly in half: the links between node blocks [0, n/2) and [n/2, n)"""
    half = t["n"] // 2
    return [j for j, (a, b, _) in enumerate(t["links"]) if (a < half) != (b < half)]


# -------------------------------------------------------------------------------------------------------------- relay
def tap_open(name):
    import fcntl
    import struct
    fd = os.open("/dev/net/tun", os.O_RDWR | os.O_NONBLOCK)
    IFF_TAP, IFF_NO_PI, TUNSETIFF = 0x0002, 0x1000, 0x400454CA
    fcntl.ioctl(fd, TUNSETIFF, struct.pack("16sH", name.encode(), IFF_TAP | IFF_NO_PI))
    return fd


class Relay:
    """One thread carries every link: a frame read from one end is written to the other end `delay` seconds later
    (constant delay keeps each direction in order). A silent link drops every frame in both directions."""

    def __init__(self):
        import threading
        self.peer, self.delay, self.link, self.silent = {}, {}, {}, set()
        self.stop_flag = False
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.frames = 0

    def add(self, j, fa, fb, delay):
        self.peer[fa], self.peer[fb] = fb, fa
        self.delay[fa] = self.delay[fb] = delay
        self.link[fa] = self.link[fb] = j

    def start(self):
        self.thread.start()

    def loop(self):
        import heapq
        import selectors
        sel = selectors.DefaultSelector()
        for fd in self.peer:
            sel.register(fd, selectors.EVENT_READ)
        due, seq = [], 0
        while not self.stop_flag:
            now = time.monotonic()
            while due and due[0][0] <= now:
                _, _, fd, frame = heapq.heappop(due)
                try:
                    os.write(fd, frame)
                except OSError:
                    pass
            timeout = max(0.0, due[0][0] - now) if due else 0.2
            for key, _ in sel.select(timeout):
                fd = key.fd
                while True:
                    try:
                        frame = os.read(fd, 65536)
                    except BlockingIOError:
                        break
                    except OSError:
                        break
                    if not frame:
                        break
                    if self.link[fd] in self.silent:
                        continue
                    seq += 1
                    self.frames += 1
                    heapq.heappush(due, (time.monotonic() + self.delay[fd], seq, self.peer[fd], frame))

    def close(self):
        self.stop_flag = True
        self.thread.join(timeout=2)
        for fd in self.peer:
            try:
                os.close(fd)
            except OSError:
                pass


# ---------------------------------------------------------------------------------------------------------------- bed
class Bed:
    def __init__(self, t, work, profile):
        self.t, self.work, self.profile = t, work, profile
        self.pids, self.mons = {}, {}
        self.ifname = {}                      # (node, j) -> interface name
        self.owner = {}                       # address -> node

    def build(self):
        for i in self.t["nodes"]:
            subprocess.run(["ip", "netns", "add", NS + str(i)], check=True)
            sh("ip", "link", "set", "lo", "up", ns=i)
            sh("ip", "addr", "add", lo_addr(i) + "/32", "dev", "lo", ns=i)
            sh("sysctl", "-qw", "net.ipv4.ip_forward=1", ns=i)
            self.owner[lo_addr(i)] = i
        # [LINK_RELAY_V1] every link is two TAP devices joined by the relay (below), which holds each frame for the
        # link's one-way delay and drops everything on a silent link. One mechanism on every machine: it needs no netem
        # (absent from some kernels, e.g. this workspace's), and the relay is what makes a link silent.
        self.relay = Relay()
        for j, (a, b, d) in enumerate(self.t["links"]):
            ia, ib = f"l{j}a", f"l{j}b"
            fa, fb = tap_open(ia), tap_open(ib)
            subprocess.run(["ip", "link", "set", ia, "netns", NS + str(a)], check=True)
            subprocess.run(["ip", "link", "set", ib, "netns", NS + str(b)], check=True)
            self.relay.add(j, fa, fb, d / 1000.0)
            xa, xb = link_addrs(j)
            for node, ifc, addr in ((a, ia, xa), (b, ib, xb)):
                sh("ip", "addr", "add", addr + "/31", "dev", ifc, ns=node)
                sh("ip", "link", "set", ifc, "up", ns=node)
                self.ifname[(node, j)] = ifc
                self.owner[addr] = node
        self.relay.start()

    def frr_conf(self, i):
        timers = {"default": [], "tuned": [" lsp-gen-interval 1", " spf-interval 1",
                  " spf-delay-ietf init-delay 50 short-delay 200 long-delay 5000 holddown 10000 time-to-learn 500"]}
        hello = {"default": [], "tuned": [" isis hello-interval 1", " isis hello-multiplier 3"]}
        sysid = f"{i + 1:012d}"
        lines = [f"hostname n{i}", f"log file {self.work}/n{i}/isisd.log debugging", "log timestamp precision 6",
                 "debug isis spf-events", "router isis D", f" net 49.0001.{sysid[0:4]}.{sysid[4:8]}.{sysid[8:12]}.00"]
        lines += timers[self.profile] + [" is-type level-2-only", " metric-style wide", "!",
                                         "interface lo", " ip router isis D", " isis passive", "!"]
        for (node, j), ifc in self.ifname.items():
            if node == i:
                lines += [f"interface {ifc}", " ip router isis D", " isis network point-to-point"] + hello[self.profile] + ["!"]
        return "\n".join(lines) + "\n"

    def start(self):
        for i in self.t["nodes"]:
            d, s = f"{self.work}/n{i}", f"{SOCK}/n{i}"
            os.makedirs(d, exist_ok=True)
            os.makedirs(s, exist_ok=True)
            # [ROUTES_CARRY_NEXTHOPS_V1] no kernel nexthop objects: a change of next hop then always shows as a route
            # change in `ip monitor route`, never as a silent edit of a shared nexthop group the route only names by id
            open(f"{d}/zebra.conf", "w").write(f"hostname n{i}\nlog file {d}/zebra.log informational\n"
                                               "no zebra nexthop kernel enable\n")
            open(f"{d}/isisd.conf", "w").write(self.frr_conf(i))
            common = ["-d", "-u", "root", "-g", "root", "-z", f"{s}/zserv.api", "--vty_socket", s]
            self._daemon(i, "zebra", common + ["-f", f"{d}/zebra.conf", "-i", f"{d}/zebra.pid"])
        for i in self.t["nodes"]:
            d, s = f"{self.work}/n{i}", f"{SOCK}/n{i}"
            self._wait(lambda: os.path.exists(f"{s}/zserv.api"), 10, f"zebra API on n{i}")
            common = ["-d", "-u", "root", "-g", "root", "-z", f"{s}/zserv.api", "--vty_socket", s]
            self._daemon(i, "isisd", common + ["-f", f"{d}/isisd.conf", "-i", f"{d}/isisd.pid"])
        for i in self.t["nodes"]:
            d = f"{self.work}/n{i}"
            self.pids[i] = [self._pid(f"{d}/{x}.pid") for x in ("zebra", "isisd")]

    def _daemon(self, i, prog, args):
        out = open(f"{self.work}/n{i}/{prog}.out", "w")
        rc = subprocess.run(["ip", "netns", "exec", NS + str(i), f"{FRR}/{prog}"] + args, stdout=out,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode
        if rc != 0:
            raise RuntimeError(f"{prog} on n{i} exited {rc}: see {self.work}/n{i}/{prog}.out")

    @staticmethod
    def _wait(cond, secs, what):
        t = time.time() + secs
        while time.time() < t:
            if cond():
                return
            time.sleep(0.02)
        raise RuntimeError(f"timed out waiting for {what}")

    def _pid(self, path):
        self._wait(lambda: os.path.exists(path) and open(path).read().strip(), 10, path)
        return int(open(path).read().strip())

    def routes(self, i):
        """{dest_ip: set(next-hop nodes)} from the kernel table now (dest = another node's loopback)"""
        out = sh("ip", "-o", "route", "show", ns=i).stdout
        return parse_table(out, self.owner)

    def converged(self):
        want = {lo_addr(k) for k in self.t["nodes"]}
        for i in self.t["nodes"]:
            have = set(self.routes(i)) | {lo_addr(i)}
            if have != want:
                return False
        return True

    def monitor(self):
        for i in self.t["nodes"]:
            f = open(f"{self.work}/n{i}/mon.txt", "w")
            p = subprocess.Popen(["ip", "netns", "exec", NS + str(i), "ip", "-ts", "-o", "monitor", "route"], stdout=f,
                                 stderr=subprocess.STDOUT)
            self.mons[i] = p

    def stop_monitor(self):
        for p in self.mons.values():
            p.terminate()
        for p in self.mons.values():
            p.wait()

    def silent(self, j):
        self.relay.silent.add(j)                      # both directions drop from now on; carrier stays up

    def cut(self, j):
        a, b, _ = self.t["links"][j]
        for node in (a, b):
            sh("ip", "link", "set", self.ifname[(node, j)], "down", ns=node)

    def teardown(self):
        for ps in self.pids.values():
            for pid in ps:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        time.sleep(0.5)
        if getattr(self, "relay", None):
            self.relay.close()
        for i in self.t["nodes"]:
            subprocess.run(["ip", "netns", "del", NS + str(i)], capture_output=True)
        subprocess.run(["rm", "-rf", SOCK])


# ------------------------------------------------------------------------------------------------- tables and timeline
ROUTE = re.compile(r"^(Deleted )?(\d+\.\d+\.\d+\.\d+)(?:/32)?\s(.*)$")


def nexthops(rest, owner):
    """next-hop nodes named in one route line (single 'via X' or multipath 'nexthop via X ...')"""
    return {owner[v] for v in re.findall(r"via (\d+\.\d+\.\d+\.\d+)", rest) if v in owner}


def parse_table(text, owner):
    out = {}
    for line in text.splitlines():
        m = ROUTE.match(line.strip())
        if m and not m.group(1) and m.group(2).startswith("10.99.") and "proto" in m.group(3):
            out[m.group(2)] = nexthops(m.group(3), owner)
    return out


TS = re.compile(r"^\[(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)\]\s(.*)$")


def ts(s):
    return datetime.fromisoformat(s).timestamp()


def read_monitor(path, node, owner):
    """-> [(t, node, dest, set(nexthops) or None for deleted)]"""
    ev = []
    for line in open(path):
        m = TS.match(line.rstrip("\n"))
        if not m:
            continue
        r = ROUTE.match(m.group(2).strip())
        if not r or not r.group(2).startswith("10.99."):
            continue
        ev.append((ts(m.group(1)), node, r.group(2), None if r.group(1) else nexthops(r.group(3), owner)))
    return ev


# [SPF_RUN_IS_THE_CALLBACK_V1] isisd logs this line from isis_run_spf_cb, the timer callback that runs SPF: one line,
# one SPF run ("schedule called" lines are requests, many per run, and are not counted)
SPF = re.compile(r"^(\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\.\d+).*SPF needed, periodic SPF")


def read_spf(path):
    out = []
    try:
        for line in open(path):
            m = SPF.match(line)
            if m:
                out.append(datetime.strptime(m.group(1), "%Y/%m/%d %H:%M:%S.%f").timestamp())
    except OSError:
        pass
    return out


# ------------------------------------------------------------------------------------------------------------ detector
def cycles(nh, dest_owner):
    """next-hop graph for one destination: nh = {node: set(nexthops)}. -> list of frozenset(cycle nodes). A cycle in the
    graph (any branch of a multipath can be taken by some flow) is a forwarding loop."""
    found, color = [], {}
    for s in nh:
        if color.get(s):
            continue
        stack, path, onpath = [(s, iter(sorted(nh.get(s, ()))))], [s], {s}
        color[s] = 1
        while stack:
            x, it = stack[-1]
            y = next(it, None)
            if y is None:
                stack.pop(); path.pop(); onpath.discard(x); color[x] = 2
                continue
            if y == dest_owner:
                continue
            if y in onpath:
                found.append(frozenset(path[path.index(y):]))
            elif not color.get(y):
                color[y] = 1; stack.append((y, iter(sorted(nh.get(y, ()))))); path.append(y); onpath.add(y)
    return found


def analyse(initial, events, t_event, owner_of_dest, dead=frozenset()):
    """initial: {node: {dest: set}} at t_event; events: sorted [(t, node, dest, set|None)]; dead: the links the event
    silenced or cut, as frozenset({a, b}).
    [DEAD_LINK_IS_NO_LOOP_V1] a next hop across a dead link carries nothing (the packet is lost on the link, not
    returned), so that edge is left out of the next-hop graph: a cycle through it is not a forwarding loop.
    -> loops [{dest, nodes, start, end}], window {dest: seconds}"""
    fib = {n: {d: set(v) for d, v in tbl.items()} for n, tbl in initial.items()}
    open_loops, done, last = {}, [], {}
    i = 0
    while i < len(events):
        t = events[i][0]
        touched = set()
        while i < len(events) and events[i][0] == t:
            _, node, dest, nh = events[i]
            if nh is None:
                fib[node].pop(dest, None)
            else:
                fib[node][dest] = nh
            touched.add(dest)
            last[dest] = t
            i += 1
        for dest in touched:
            graph = {n: {y for y in fib[n].get(dest, ()) if frozenset((n, y)) not in dead} for n in fib}
            now = set(cycles(graph, owner_of_dest[dest]))
            for key in [k for k in open_loops if k[0] == dest and k[1] not in now]:
                done.append({"dest": dest, "nodes": sorted(key[1]), "start": open_loops.pop(key), "end": t})
            for c in now:
                open_loops.setdefault((dest, c), t)
    for (dest, c), start in open_loops.items():
        done.append({"dest": dest, "nodes": sorted(c), "start": start, "end": None})
    window = {d: last[d] - t_event for d in last}
    return done, window


def selftest():
    """negative tests: the detector must find injected loops and read 0 on a clean timeline"""
    own = {"D": 9}
    init = {0: {"D": {1}}, 1: {"D": {2}}, 2: {"D": {9}}, 9: {}}
    clean = [(1.0, 0, "D", {2})]
    two = [(1.0, 1, "D", {0}), (1.2, 1, "D", {2})]                     # 0->1->0 for 200 ms
    three = [(1.0, 2, "D", {0}), (1.5, 2, "D", {9})]                   # 0->1->2->0 for 500 ms
    multi = [(1.0, 2, "D", {9, 0})]                                    # one ECMP branch loops: must be caught
    ok = True
    deadlink = frozenset({frozenset((0, 1))})                          # the same 0<->1 hairpin over a dead link: not a loop
    for name, ev, want, dead in (("clean", clean, 0, frozenset()), ("two-node", two, 1, frozenset()),
                                 ("three-node", three, 1, frozenset()), ("ecmp-branch", multi, 1, frozenset()),
                                 ("dead-link", two, 0, deadlink)):
        loops, _ = analyse(init, ev, 0.0, own, dead)
        got = len(loops)
        good = got == want and all((l["end"] is None) == (name == "ecmp-branch") for l in loops)
        print(f"  selftest {name:12s} loops={got} want={want} {'PASS' if good else 'FAIL'}"
              + (f" lifetime={[round(l['end'] - l['start'], 3) for l in loops if l['end']]}" if loops else ""))
        ok &= good
    print("SELFTEST", "PASS" if ok else "FAIL")
    return ok


# ----------------------------------------------------------------------------------------------------------------- run
def last_line_time(paths):
    m = 0.0
    for p in paths:
        try:
            m = max(m, os.path.getmtime(p))
        except OSError:
            pass
    return m


def run(a):
    t = topology(a.topo, a.n, a.seed, a.sites, a.wan_ms, a.lan_ms)
    work = os.path.abspath(a.out or f"persist-{a.topo}-{a.n}-{a.event}-{a.profile}-{int(time.time())}")
    os.makedirs(work, exist_ok=True)
    log(f"topo={a.topo} n={a.n} links={len(t['links'])} event={a.event} profile={a.profile} work={work}")
    bed = Bed(t, work, a.profile)
    res = {"build": BUILD, "frr": sh(f"{FRR}/isisd", "--version").stdout.splitlines()[0], "args": vars(a),
           "links": len(t["links"])}
    try:
        bed.build()
        bed.start()
        t0 = time.time()
        bed._wait(bed.converged, a.converge_timeout, "convergence")
        res["cold_s"] = round(time.time() - t0, 3)
        log(f"converged in {res['cold_s']} s")
        time.sleep(a.settle)
        bed.monitor()
        time.sleep(1.0)
        initial = {i: bed.routes(i) for i in t["nodes"]}
        # [EVENT_CLOCK_FIRST_V1] the event's time is taken BEFORE the first change is applied, so no route change it
        # causes can fall before it and be dropped from the timeline
        if a.event in ("cut", "silent"):
            targets = [split(t)[0]]
        elif a.event == "partition":
            targets = split(t)
        else:
            victim = t["n"] // 2
            targets = [j for j, (x, y, _) in enumerate(t["links"]) if victim in (x, y)]
        t_ev = time.time()
        for j in targets:
            (bed.cut if a.event == "cut" else bed.silent)(j)
        res["event_links"] = [t["links"][j][:2] for j in targets]
        mons = [f"{work}/n{i}/mon.txt" for i in t["nodes"]]
        deadline = t_ev + a.max_wait
        while time.time() < deadline:
            time.sleep(1.0)
            if time.time() - max(last_line_time(mons), t_ev) >= a.quiet:
                break
        bed.stop_monitor()
        res["observed_s"] = round(time.time() - t_ev, 3)
        events = sorted(e for i in t["nodes"] for e in read_monitor(f"{work}/n{i}/mon.txt", i, bed.owner) if e[0] >= t_ev)
        owner_of_dest = {lo_addr(i): i for i in t["nodes"]}
        dead = frozenset(frozenset(t["links"][j][:2]) for j in targets)
        loops, window = analyse(initial, events, t_ev, owner_of_dest, dead)
        spf = {i: [x for x in read_spf(f"{work}/n{i}/isisd.log") if x >= t_ev] for i in t["nodes"]}
        # [LIVE_VS_GONE_DEST_V1] after the event, is the destination still reachable from the loop's nodes over the
        # surviving links? A loop toward a live destination traps traffic that has somewhere to go; one toward a
        # destination the event cut off traps traffic that was going to be lost anyway. Both are loops; both reported.
        comp = components(t["nodes"], [l[:2] for l in t["links"] if frozenset(l[:2]) not in dead])
        for l in loops:
            l["dest_reachable"] = comp[owner_of_dest[l["dest"]]] == comp[l["nodes"][0]]
            l["open"] = l["end"] is None                    # still looping when observation stopped
            end = l["end"] if l["end"] is not None else time.time()
            l["lifetime_ms"] = round((end - l["start"]) * 1000, 3)
            per = [sum(1 for x in spf[nd] if l["start"] <= x <= end) for nd in l["nodes"]]
            l["spf_runs_during_max"] = max(per) if per else 0
            l["start_ms"] = round((l["start"] - t_ev) * 1000, 3)
            del l["start"], l["end"]
        res["loadavg_1m"] = os.getloadavg()[0]          # one machine runs every node: report how busy it was
        res["cpus"] = os.cpu_count()
        res["route_changes"] = len(events)
        res["loops"] = loops
        res["loops_n"] = len(loops)
        res["loops_open_at_end"] = sum(1 for l in loops if l.get("open"))
        res["max_loop_ms"] = max((l["lifetime_ms"] for l in loops), default=0)
        res["max_spf_runs_spanned"] = max((l["spf_runs_during_max"] for l in loops), default=0)
        for cls, sel in (("live", True), ("gone", False)):
            ls = [l for l in loops if l["dest_reachable"] == sel]
            res[f"{cls}_loops_n"] = len(ls)
            res[f"{cls}_max_loop_ms"] = max((l["lifetime_ms"] for l in ls), default=0)
            res[f"{cls}_max_spf_runs"] = max((l["spf_runs_during_max"] for l in ls), default=0)
            res[f"{cls}_spf_hist"] = {str(k): sum(1 for l in ls if l["spf_runs_during_max"] == k)
                                      for k in sorted({l["spf_runs_during_max"] for l in ls})}
        res["window_max_ms"] = round(max(window.values(), default=0) * 1000, 3)
        res["spf_runs_after_event"] = {"max": max(len(v) for v in spf.values()), "total": sum(len(v) for v in spf.values())}
        log(f"route changes {len(events)}; loops to LIVE dests {res['live_loops_n']} (longest {res['live_max_loop_ms']} ms, "
            f"SPF runs {res['live_spf_hist']}); to GONE dests {res['gone_loops_n']} (longest {res['gone_max_loop_ms']} ms); "
            f"window {res['window_max_ms']} ms; load {res['loadavg_1m']:.2f}")
    finally:
        if not a.keep:
            bed.teardown()
        json.dump(res, open(f"{work}/result.json", "w"), indent=1)
        log(f"result {work}/result.json")
    return res


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("selftest")
    r = sp.add_parser("run")
    r.add_argument("--topo", default="ring", choices=["ring", "grid", "mesh"])
    r.add_argument("--n", type=int, default=16)
    r.add_argument("--event", default="partition", choices=["cut", "silent", "partition", "nodefail"])
    r.add_argument("--profile", default="tuned", choices=["tuned", "default"])
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--sites", type=int, default=3)
    r.add_argument("--wan-ms", type=float, default=35.0)
    r.add_argument("--lan-ms", type=float, default=1.0)
    r.add_argument("--settle", type=float, default=5.0)
    r.add_argument("--quiet", type=float, default=15.0)
    r.add_argument("--max-wait", type=float, default=180.0)
    r.add_argument("--converge-timeout", type=float, default=300.0)
    r.add_argument("--keep", action="store_true")
    r.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "selftest":
        sys.exit(0 if selftest() else 1)
    run(a)


if __name__ == "__main__":
    main()
