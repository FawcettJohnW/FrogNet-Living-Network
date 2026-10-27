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
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s_scale_tier.py - [S_SCALE_TIER_V1] TIER 3: experiment S (day-claims) in the simulator. S subnets crossing E egress
points, discovered the FD&F way: from memory. No probe, no trace, no route is sent or followed during discovery.

  facts    each node's facts.addrs rows, exactly as fdf-facts writes them (own .1, held lease with its mac, the lease
           its egress gave, uplink, the egress's hub edge 10.253.A.B), announced by a REAL fdf-client session per node
           into a REAL RAM host (rinab rina-host). The hub announces itself (role hub) the same way.
  plans    each node's plan from the participants read, by FD&F's own graph (fdf_graph.Graph(participants).plan(me))
           -- the code FrogNet's graph_apply turns into the kernel. Each egress then publishes the plan's maps
           (facts.maps, as region.publish_plan_maps) and they come back through the same memory.
  verify   the ONE place routes are followed: the final verification plan, complete -- every node to every served
           /24, walked over the nodes' plans (explicit routes, hub routes, default) and the hub's table (the maps the
           participants hold). Each pair's path, its hop count and the hop count the topology allows. Written whole
           to verify-S<S>-E<E>.tsv; any pair unreached, looped, or longer than the topology allows fails the run.

The topology: E egress nodes, each serving its /24 on its LAN and holding a hub link (wg); the other S-E nodes are
children dealt round-robin onto the egress LANs, each serving its own /24 (lan0) behind a lease from its egress (eth1).

An event follows: the last child starts serving one more /24 (a second LAN). Its new facts reach the memory, every
plan is recomputed, the maps are republished, and the new /24 is verified from every node.

Measured per E: time for the memory to hold every member, plan CPU per node (p50 / max), the maps round, the
participants read each member makes (bytes), the event's time to the memory and to every plan, and the verification.

usage (from opt/frognet_semantic):
  python3 simulation/s_scale_tier.py --bin DIR [--fdfpy DIR] [--S 200] [--E 1 2 5 ...] [--out DIR] [--port N]
  DIR holds rina-host, fdf-client, fdf-events (rinab build/static or the day-claims package's own).
"""
import argparse
import json
import os
import socket
import statistics as st
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
API = "/FawcettInnovations.RINA.ram_interface.php"


def now_ms():
    return time.monotonic() * 1000


# ------------------------------------------------------------------ the topology, as facts
def mac(n, i):
    return "02:%02x:%02x:%02x:%02x:%02x" % (i, (n >> 24) & 255, (n >> 16) & 255, (n >> 8) & 255, n & 255)


def topology(S, E):
    """{name: facts} for S nodes over E egress points, plus the expected parent of each child."""
    assert 1 <= E <= S <= 250
    nodes, parent, seats = {}, {}, {i: 0 for i in range(1, E + 1)}
    for i in range(1, E + 1):
        nodes[f"e{i}"] = {"name": f"e{i}", "guid": f"g-e{i}", "role": "node", "subnet": f"10.20.{i}.0/24", "pond": "",
                          "addrs": [{"iface": "eth0", "ip": f"10.20.{i}.1", "value": f"FrogNetHost.e{i}", "kind": "own", "mac": mac(i, 1)},
                                    {"iface": "wan0", "ip": f"198.18.{i // 256}.{i % 256}", "value": "uplink eth 1000", "kind": "uplink", "mac": mac(i, 2)},
                                    {"iface": "wg0", "ip": f"10.253.20.{i}", "value": "", "kind": "hub", "mac": ""}]}
    for k in range(E + 1, S + 1):
        e = (k - E - 1) % E + 1
        seats[e] += 1
        lease = f"10.20.{e}.{19 + seats[e]}"
        cm = mac(k, 3)
        nodes[f"c{k}"] = {"name": f"c{k}", "guid": f"g-c{k}", "role": "node", "subnet": f"10.20.{k}.0/24", "pond": "",
                          "addrs": [{"iface": "eth1", "ip": lease, "value": f"e{e}", "kind": "held", "mac": cm},
                                    {"iface": "lan0", "ip": f"10.20.{k}.1", "value": f"FrogNetHost.c{k}", "kind": "own", "mac": mac(k, 4)},
                                    {"iface": "eth1", "ip": lease, "value": "uplink eth 1000", "kind": "uplink", "mac": cm}]}
        nodes[f"e{e}"]["addrs"].append({"iface": "eth0", "ip": lease, "value": f"c{k}", "kind": "given", "mac": cm})
        parent[f"c{k}"] = f"e{e}"
    for f in nodes.values():
        f["addrs"].sort(key=lambda r: (r["kind"], r["iface"], r["ip"]))
    hub = {"name": "hubA", "guid": "g-hubA", "role": "hub", "pubkey": "sim", "endpoint": "198.18.255.1:51820"}
    return nodes, hub, parent


def allowed_hops(src, dst_owner, parent):
    """Hops the topology allows from src to the node serving the /24: up to the egress, across the hub, down."""
    if src == dst_owner:
        return 0
    es, ed = parent.get(src, src), parent.get(dst_owner, dst_owner)
    up = 1 if src in parent else 0
    down = 1 if dst_owner in parent else 0
    if es == ed:
        return up + down
    return up + 2 + down                    # src -> its egress -> hub -> the other egress -> dst


# ------------------------------------------------------------------ the memory
class Region:
    """A real RAM host and a real fdf-client session per member, on loopback."""

    def __init__(self, bindir, port, work):
        self.bindir, self.port, self.work = bindir, port, work
        self.where = f"127.0.0.1:{port}"
        self.url = f"http://{self.where}{API}?op=rina"
        self.clients = {}
        log = open(os.path.join(work, "host.log"), "w")
        self.host = subprocess.Popen([os.path.join(bindir, "rina-host"), "--listen", self.where, "--api", API],
                                     env=dict(os.environ, RINA_REGION_FILE=os.path.join(work, "region.perm")),
                                     stdout=log, stderr=log)
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                if self.host.poll() is not None:
                    raise RuntimeError(f"rina-host ended at start: see {work}/host.log")
                time.sleep(0.05)
        else:
            raise RuntimeError("rina-host did not listen")
        # [S_HELD_READ_V1] one held session that says when the participants change: the harness waits on its lines
        self.events = subprocess.Popen([os.path.join(bindir, "fdf-events")], env=dict(os.environ, RINA_RAM=self.where),
                                       stdout=subprocess.PIPE, stderr=open(os.path.join(work, "events.log"), "w"), text=True, bufsize=1)
        self.ev = threading.Condition()
        self.nev = 0
        threading.Thread(target=self._events, daemon=True).start()

    def _events(self):
        for line in self.events.stdout:
            with self.ev:
                if line.strip().split("\t", 1)[0] in ("p", "m", "ready"):   # [EVENT_KIND_FIRST_FIELD_V1]
                    self.nev += 1
                self.ev.notify_all()

    def call(self, body):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        return json.loads(raw), len(raw)

    def write_facts(self, name, facts):
        p = os.path.join(self.work, "facts", f"{name}.json")
        t = p + ".tmp"
        with open(t, "w") as f:
            json.dump(facts, f)
        os.replace(t, p)

    def join(self, name, facts):
        self.write_facts(name, facts)
        d = os.path.join(self.work, "var", name)
        os.makedirs(d, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in ("RINA_AREA", "RINA_SITE_KEY", "FDF_REACH", "FDF_NAME")}
        env.update(RINA_RAM=self.where, FDF_FACTS=os.path.join(self.work, "facts", f"{name}.json"), FDF_VAR=d)
        self.clients[name] = subprocess.Popen([os.path.join(self.bindir, "fdf-client")], env=env,
                                              stdout=open(os.path.join(d, "client.log"), "w"), stderr=subprocess.STDOUT)

    def wait_until(self, test, timeout_s, what):
        """Held: re-read only when the region says the participants changed."""
        t_end = time.monotonic() + timeout_s
        while True:
            with self.ev:
                seen = self.nev
            parts, size = self.call({"op": "participants"})
            if test(parts["participants"]):
                return parts["participants"], size
            with self.ev:
                while self.nev == seen:
                    left = t_end - time.monotonic()
                    if left <= 0:
                        raise RuntimeError(f"timed out after {timeout_s} s waiting for {what}")
                    self.ev.wait(left)

    def close(self):
        for p in list(self.clients.values()) + [self.events, self.host]:
            if p.poll() is None:
                p.terminate()
        for p in list(self.clients.values()) + [self.events, self.host]:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()


# ------------------------------------------------------------------ plans and the verification plan
def plans_of(parts, names, Graph):
    t0 = time.process_time()
    g = Graph(parts)
    build_ms = (time.process_time() - t0) * 1000
    out, cpu = {}, []
    for n in names:
        t = time.process_time()
        out[n] = g.plan(n)
        cpu.append((time.process_time() - t) * 1000)
    return g, out, build_ms, cpu


def verify(parts, plans, parent, nodes, path_out):
    """Every node to every served /24, over the plans and the hub's table. The only route-following in the tier."""
    owner_of_ip, served_by = {}, {}
    for p in parts:
        f = p.get("facts") or {}
        for r in f.get("addrs") or []:
            if r.get("kind") in ("own", "held"):
                owner_of_ip[r["ip"]] = p["name"]
            if r.get("kind") == "own" and r["ip"].endswith(".1"):
                served_by[r["ip"].rsplit(".", 1)[0] + ".0/24"] = p["name"]
    hub_table = {}
    for p in parts:
        for m in (p.get("facts") or {}).get("maps") or []:
            hub_table.setdefault(m["eid"], []).append(p["name"])
    bad, rows, longer = [], 0, 0
    with open(path_out, "w") as out:
        out.write("src\tdst\tpath\thops\tallowed\tverdict\n")
        for src in sorted(plans):
            for net in sorted(served_by):
                dst = served_by[net]
                path, at, verdict = [src], src, ""
                while not verdict:
                    if net in {r for r in plans[at]["own"]}:
                        verdict = "ok"
                        break
                    if len(path) > 12:
                        verdict = "LOOP"
                        break
                    pl = plans[at]
                    nxt = None
                    for dest, via, dev in pl["routes"]:
                        if dest == net:
                            nxt = owner_of_ip.get(via)
                            if nxt is None:
                                verdict = f"NO HOLDER OF {via}"
                            break
                    if nxt is None and not verdict and pl.get("hub_active") and net in pl["hub_routes"]:
                        holders = hub_table.get(net, [])
                        if len(holders) != 1:
                            verdict = f"HUB HAS {len(holders)} MAPPINGS"
                        else:
                            path.append("hub")
                            nxt = holders[0]
                    if nxt is None and not verdict and pl.get("default"):
                        nxt = owner_of_ip.get(pl["default"][0])
                        if nxt is None:
                            verdict = f"NO HOLDER OF DEFAULT {pl['default'][0]}"
                    if nxt is None and not verdict:
                        verdict = "NO ROUTE"
                    if nxt:
                        path.append(nxt)
                        at = nxt
                hops = len(path) - 1
                allowed = allowed_hops(src, dst, parent)
                if verdict == "ok" and hops > allowed:
                    verdict = "LONGER"
                    longer += 1
                rows += 1
                out.write(f"{src}\t{net}\t{'>'.join(path)}\t{hops}\t{allowed}\t{verdict}\n")
                if verdict != "ok":
                    bad.append(f"{src} -> {net} ({dst}): {verdict} {'>'.join(path)}")
    return rows, bad


# ------------------------------------------------------------------ one (S, E)
def run(S, E, a, Graph, outdir):
    nodes, hub, parent = topology(S, E)
    work = tempfile.mkdtemp(prefix=f"s{S}e{E}-", dir=a.work)
    os.makedirs(os.path.join(work, "facts"))
    reg = Region(a.bin, a.port, work)
    res = {"S": S, "E": E}
    try:
        changing = f"c{S}" if S > E else f"e{S}"
        order = [changing] + [f"e{i}" for i in range(1, E + 1) if f"e{i}" != changing] + \
                [n for n in nodes if n != changing and not n.startswith("e")]
        t0 = now_ms()
        reg.join("hubA", hub)
        for n in order:
            reg.join(n, nodes[n])
        names = set(nodes) | {"hubA"}
        parts, size = reg.wait_until(lambda P: {p["name"] for p in P if p.get("alive")} >= names, a.timeout,
                                     f"all {len(names)} members alive")
        res["memory_full_ms"] = round(now_ms() - t0)
        res["participants_bytes"] = size
        # plans, then the egress maps through the memory
        t1 = now_ms()
        g, plans, build_ms, cpu = plans_of(parts, sorted(nodes), Graph)
        res.update(graph_build_ms=round(build_ms, 1), plan_cpu_p50_ms=round(st.median(cpu), 2),
                   plan_cpu_max_ms=round(max(cpu), 2), plans_cpu_ms=round(sum(cpu) + build_ms, 1))
        rl = "hubA:1:50"
        want = {}
        for n, pl in plans.items():
            maps = [{"eid": p, "rlocs": rl} for p in pl["maps"]]
            if maps:
                f = dict(nodes[n], maps=maps)
                nodes[n] = f
                reg.write_facts(n, f)
                want[n] = len(maps)

        def maps_in(P):
            got = {p["name"]: len((p.get("facts") or {}).get("maps") or []) for p in P if p.get("alive")}
            return all(got.get(n) == k for n, k in want.items())
        parts, size = reg.wait_until(maps_in, a.timeout, "every egress's maps in the memory")
        res["maps_round_ms"] = round(now_ms() - t1)
        res["participants_bytes_with_maps"] = size
        res["cold_ms"] = round(now_ms() - t0)
        rows, bad = verify(parts, plans, parent, nodes, os.path.join(outdir, f"verify-S{S}-E{E}.tsv"))
        res.update(pairs=rows, pairs_bad=len(bad), bad=bad[:8])
        # the event: the changing node serves one more /24
        f = dict(nodes[changing])
        f["addrs"] = sorted(f["addrs"] + [{"iface": "lan1", "ip": "10.21.0.1", "value": f"FrogNetHost.{changing}", "kind": "own", "mac": mac(S, 9)}],
                            key=lambda r: (r["kind"], r["iface"], r["ip"]))
        nodes[changing] = f
        t2 = now_ms()
        reg.write_facts(changing, f)

        def has_new(P):
            return any(r.get("ip") == "10.21.0.1" for p in P if p["name"] == changing for r in (p.get("facts") or {}).get("addrs") or [])
        parts, size = reg.wait_until(has_new, a.timeout, "the event in the memory")
        res["event_to_memory_ms"] = round(now_ms() - t2)
        g, plans, build_ms, cpu = plans_of(parts, sorted(nodes), Graph)
        res["event_plans_cpu_ms"] = round(sum(cpu) + build_ms, 1)
        ex = parent.get(changing, changing)
        pl = plans[ex]
        maps = [{"eid": p, "rlocs": rl} for p in pl["maps"]]
        nodes[ex] = dict(nodes[ex], maps=maps)
        reg.write_facts(ex, nodes[ex])
        parts, size = reg.wait_until(lambda P: any(m["eid"] == "10.21.0.0/24" for p in P if p["name"] == ex
                                                   for m in (p.get("facts") or {}).get("maps") or []), a.timeout, "the new map")
        res["event_to_maps_ms"] = round(now_ms() - t2)
        res["event_bytes_per_member_read"] = size
        rows2, bad2 = verify(parts, plans, parent, nodes, os.path.join(outdir, f"verify-S{S}-E{E}-after-event.tsv"))
        res.update(event_pairs=rows2, event_pairs_bad=len(bad2), event_bad=bad2[:8])
        dead = [n for n, p in reg.clients.items() if p.poll() is not None]
        res["clients_ended"] = dead
        # [S_INOTIFY_V1] every member here is one kernel user: past fs.inotify.max_user_instances a client cannot watch
        # its facts. Only the members whose facts change in the run (the egress nodes, the changing node) need it; the
        # rest are counted as this machine's limit, not a result.
        watchfail = [n for n in reg.clients if "FACTS WATCH FAILED" in open(os.path.join(work, "var", n, "client.log")).read()]
        need = {changing} | {f"e{i}" for i in range(1, E + 1)}
        res["facts_watch_failed"] = [n for n in watchfail if n in need]
        res["facts_watch_unneeded_failed"] = len([n for n in watchfail if n not in need])
    finally:
        reg.close()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--fdfpy", default=next((d for d in (os.environ.get("FDF_PY", ""),
                                                        os.path.join(_PARENT, "../fdf/src/fdf-source/fdf/lib/python"),
                                                        "/opt/fdf/lib/python")
                                              if d and os.path.isfile(os.path.join(d, "fdf_graph.py"))), ""),
                    help="where fdf_graph.py is: FDF_PY, else the repo's FD&F source, else this machine's /opt/fdf/lib/python")
    ap.add_argument("--S", type=int, default=200)
    ap.add_argument("--E", type=int, nargs="+", default=[1, 2, 5, 10, 20, 50, 100])
    ap.add_argument("--out", default=".")
    ap.add_argument("--work", default=None)
    ap.add_argument("--port", type=int, default=51979)
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args()
    a.bin = os.path.abspath(a.bin)
    ino0 = None
    try:                                                   # root: raise the per-user inotify limit for the run, put it back
        if os.geteuid() == 0:
            ino0 = int(open("/proc/sys/fs/inotify/max_user_instances").read())
            if ino0 < 8 * a.S:
                open("/proc/sys/fs/inotify/max_user_instances", "w").write(str(8 * a.S))
            else:
                ino0 = None
    except OSError:
        ino0 = None
    if not a.fdfpy or not os.path.isfile(os.path.join(a.fdfpy, "fdf_graph.py")):
        print(f"S SCALE: FAIL - no fdf_graph.py (FDF_PY, the repo's FD&F source, /opt/fdf/lib/python): {a.fdfpy!r}")
        return 1
    for b in ("rina-host", "fdf-client", "fdf-events"):
        if not os.access(os.path.join(a.bin, b), os.X_OK):
            print(f"S SCALE: FAIL - {b} not in --bin {a.bin}")
            return 1
    sys.path.insert(0, os.path.abspath(a.fdfpy))
    import fdf_graph
    print(f"fdf_graph from {os.path.abspath(a.fdfpy)}; binaries from {a.bin}")
    os.makedirs(a.out, exist_ok=True)
    print(f"TIER 3 S SCALE - S={a.S} subnets across E egress points; FD&F discovery from memory (real rina-host, "
          f"real fdf-client per node, fdf_graph {fdf_graph.BUILD_TAG}); routes followed only in the verification plan")
    allres, nbad = [], 0
    for E in a.E:
        if E > a.S:
            continue
        r = run(a.S, E, a, fdf_graph.Graph, a.out)
        allres.append(r)
        ok = r["pairs_bad"] == 0 and r["event_pairs_bad"] == 0 and not r["clients_ended"] and not r["facts_watch_failed"]
        nbad += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] E={E:3d}  memory full {r['memory_full_ms']/1000:6.1f} s  maps {r['maps_round_ms']:5d} ms  "
              f"plan cpu p50 {r['plan_cpu_p50_ms']:6.2f} ms max {r['plan_cpu_max_ms']:6.2f}  read {r['participants_bytes_with_maps']/1024:6.1f} KB  "
              f"verified {r['pairs'] - r['pairs_bad']}/{r['pairs']}  event: memory {r['event_to_memory_ms']} ms, maps {r['event_to_maps_ms']} ms, "
              f"verified {r['event_pairs'] - r['event_pairs_bad']}/{r['event_pairs']}", flush=True)
        for b in r["bad"] + r["event_bad"]:
            print(f"         {b}")
        if r["facts_watch_unneeded_failed"]:
            print(f"         ({r['facts_watch_unneeded_failed']} members whose facts never change could not watch them: this machine's inotify limit)")
        if r["clients_ended"] or r["facts_watch_failed"]:
            print(f"         clients ended: {r['clients_ended'][:5]}  facts watch failed: {r['facts_watch_failed']}")
    if ino0 is not None:
        open("/proc/sys/fs/inotify/max_user_instances", "w").write(str(ino0))
    json.dump(allres, open(os.path.join(a.out, f"s-scale-S{a.S}.json"), "w"), indent=2)
    print(f"S SCALE: {'PASS' if not nbad else 'FAIL'} ({len(allres)} runs, {nbad} failing); verification plans in {os.path.abspath(a.out)}")
    return 1 if nbad else 0


if __name__ == "__main__":
    sys.exit(main())
