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
daf_discovery_tier.py - [DAF_DISCOVERY_SIM_V1] FrogNet discovery done by John Day's DAF model, in the simulator.

The simulator's discovery (frognet_sim.sync_interfaces) has two halves:
  wave 1  each node probes its direct L2 peers on every interface -- a local measurement;
  wave 2  each node asks each peer for the hosts the PEER knows (getHosts.php: gossip), and probes each through it.
Here the second half's question -- who exists -- is answered by the DAF instead of by gossip. Every simulated node is
a real member of a real DAF on a real RAM host (rinab's rina-host, op=daf), over its own FNW1 session (daf-agent):

  enroll        the node joins the DAF  "frognet-<topology>#1"  as role "node"
  join "nodes"  a whatevercast name (rule all, session lifetime) with its facts: its .1 and served /24
  resolve       "nodes", live members only  ->  the hosts every peer is asked about (frognet_sim.get_hosts_response)
  "control"     a whatevercast name with rule one, ranked by the .1 as a number, reserved ranges filtered out:
                the control host, checked against discovery/hosts.py control_host_ip() on every node
  session end   a killed node's agent dies; its session ends; it leaves "nodes" -- gone is gone

Measurement stays local (wave 1 and the wave-2 probes through a peer are the simulator's own). The real planner and
committer run unchanged (live_engine.converge_real over FakeIPRoute). The oracle is the simulator's own:
  - every topology's committed route tables and reachability must equal its baseline (recorded from HARDWARE);
  - every failure scenario's honest expectation must hold (no phantom route, partition honest, survivors reach).

usage (from opt/frognet_semantic):
  PYTHONPATH=$PWD PYTHONUTF8=1 FROGNET_PROXY_ROOT=$PWD FROGNET_BIN_ROOT=$PWD/../../usr/local/bin \
      python3 simulation/daf_discovery_tier.py [--rinab DIR] [--port N] [--json OUT]
Needs rinab built: tools/build.sh (makes build/rina-host and build/bin/daf-agent).
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import frognet_sim as H                      # noqa: E402
import live_engine as LE                     # noqa: E402
import live_failures as LF                   # noqa: E402
import regression_baselines as RB            # noqa: E402
from discovery.hosts import control_host_ip  # noqa: E402

API = "/FawcettInnovations.RINA.ram_interface.php"


class Agent:
    """One simulated node's session: a daf-agent process. Killing it ends the session."""
    calls = 0

    def __init__(self, exe, where):
        self.p = subprocess.Popen([exe, where], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def call(self, fn, **kw):
        kw["fn"] = fn
        self.p.stdin.write(json.dumps(kw) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError(f"daf-agent ended during {fn}")
        Agent.calls += 1
        return json.loads(line)

    def must(self, fn, **kw):
        r = self.call(fn, **kw)
        if not r.get("ok"):
            raise RuntimeError(f"{fn} refused: {r.get('error')} {r.get('why')}")
        return r

    def watch(self, D, name, rev, s):
        self.p.stdin.write(json.dumps({"watch": [D, name, rev, s]}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())["rev"]

    def kill(self):
        self.p.kill()
        self.p.wait()

    def close(self):
        if self.p.poll() is None:
            self.p.stdin.close()
            self.p.wait()


def ipnum(ip):
    a, b, c, d = (int(x) for x in ip.split("."))
    return ((a * 256 + b) * 256 + c) * 256 + d


def reserved(ip):
    return ip.startswith("10.253.") or ip.startswith("10.254.")


class DafDiscovery:
    """The DAF as the simulator's source of 'who exists'. One per topology."""

    def __init__(self, exe, where, D):
        self.exe, self.where, self.D = exe, where, D
        self.agents = {}
        self.current = None                      # the node whose sync_interfaces is running
        self.cache = {}                          # node -> its resolve of "nodes" for this cycle
        self.admin = Agent(exe, where)
        self.admin.must("create_daf", dan=D.split("#")[0], daii=D.split("#")[1], owner="simulator")
        self.admin.must("enroll", daf=D, apn="sim-admin", role="admin")
        self.admin.must("define_name", daf=D, name="nodes", rule="all", lifetime="session")
        self.admin.must("define_name", daf=D, name="control", rule="one", lifetime="session")

    def enroll(self, node):
        a = Agent(self.exe, self.where)
        facts = {"ip": node.frognet_ip, "ipnum": ipnum(node.frognet_ip), "reserved": reserved(node.frognet_ip),
                 "subnet": next(iter(node.ifaces.values())).subnet}
        # [DAF_DISCOVERY_FROGNETHOST_V1] Every node is a member of the DAF. Only a FrogNetHost -- the owner of a /24,
        # its .1, outside the reserved pools -- joins "nodes" and "control": a discovery destination is a /24's owner,
        # and the control candidates are exactly control_host_ip()'s. A worker or SSID client (a .5 riding on its AP's
        # /24) is reached through that /24, never as a destination of its own. Listing workers in "nodes" made each
        # node probe them through their AP and install backup routes hardware never had (topo_two_aps_ham_radio,
        # topo_mixed_iface_no_router), and made a worker a control candidate (DAF picked 10.241.241.5).
        host = facts["ip"].endswith(".1") and not facts["reserved"]
        e = a.must("enroll", daf=self.D, apn=node.name, role="frognethost" if host else "worker", facts=facts)
        if host:
            a.must("join", daf=self.D, name="nodes", facts=facts)
            a.must("join", daf=self.D, name="control", facts=facts)
        self.agents[node.name] = a
        return e

    def members(self, node_name):
        """The live members of "nodes", as this node's own session reads them: [(ip, name)]."""
        if node_name not in self.cache:
            r = self.agents[node_name].must("resolve", daf=self.D, name="nodes", live=True)
            self.cache[node_name] = [(m["facts"]["ip"], m["apn"]) for m in r["members"]]
        return self.cache[node_name]

    def control(self, node_name):
        r = self.agents[node_name].must("resolve", daf=self.D, name="control", live=True,
                                        filter=[{"f": "reserved", "v": False}], rank={"f": "ipnum"})
        return r["members"][0]["facts"]["ip"] if r["members"] else ""

    def kill(self, name):
        self.agents.pop(name).kill()

    def close(self):
        for a in self.agents.values():
            a.close()
        self.admin.close()


def install(dd):
    """Route the simulator's 'ask the peer who it knows' to the DAF, as the asking node's own session reads it."""
    orig_sync, orig_hosts = H.sync_interfaces, H.get_hosts_response

    def sync(topo, node):
        dd.current = node.name
        dd.cache.pop(node.name, None)            # one fresh read of the DAF per node per cycle
        try:
            return orig_sync(topo, node)
        finally:
            dd.current = None

    def hosts(topo, peer_name):
        if dd.current is None:                   # a caller outside discovery (reachability checks): unchanged
            return orig_hosts(topo, peer_name)
        return list(dd.members(dd.current))

    H.sync_interfaces, H.get_hosts_response = sync, hosts
    return lambda: (setattr(H, "sync_interfaces", orig_sync), setattr(H, "get_hosts_response", orig_hosts))


def start_host(rinab, port, tmp):
    env = dict(os.environ, RINA_REGION_FILE=os.path.join(tmp, "region.perm"))
    log = open(os.path.join(tmp, "host.log"), "w")
    p = subprocess.Popen([os.path.join(rinab, "build/rina-host"), "--listen", f"127.0.0.1:{port}", "--api", API],
                         env=env, stdout=log, stderr=log)
    import socket
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return p
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("rina-host did not start")


def run_topology(key, exe, where, legacy_cycles):
    base = RB.load_baseline(key)
    builder = getattr(H, key)
    topo = builder()
    H.assign_identities(topo)
    dd = DafDiscovery(exe, where, f"frognet-{key.replace('_', '-')}#1")
    t0 = time.monotonic()
    for n in topo.nodes.values():
        dd.enroll(n)
    enroll_ms = (time.monotonic() - t0) * 1000
    calls0 = Agent.calls
    undo = install(dd)
    try:
        cycles = LE.converge_real(topo, ipr_factory=RB._fake_factory)
    finally:
        undo()
    rdiff = RB._route_diff(base["committed_routes"], RB._routes_of(topo))
    reach = RB._reach_of(topo)
    # control host: the DAF's rule `one` against discovery/hosts.py's function over each node's hosts block
    # Hard check: every FrogNetHost (the machines that run control) computes, over its own converged hosts block, the
    # same control host the DAF elects. A worker's block is a partial gossip view; where it differs from the DAF's
    # single answer it is reported, not failed -- the DAF gives every member one answer, gossip may not.
    ctl_bad, ctl_worker_views = [], []
    for n in topo.nodes.values():
        block = [f"{ip} {name}" for ip, name in n.known_hosts.items()] + [f"{n.frognet_ip} {n.name}"]
        want, got = control_host_ip(block), dd.control(n.name)
        if want != got:
            (ctl_bad if n.frognet_ip.endswith(".1") else ctl_worker_views).append(f"{n.name}: hosts.py {want or '(none)'} / DAF {got}")
    res = {"topology": key, "label": base["topology"], "nodes": len(topo.nodes), "baseline_source": base["source"],
           "routes_match": not rdiff, "route_diff": rdiff, "reach": reach, "reach_match": reach == base["reachability"],
           "cycles_daf": cycles, "cycles_baseline": base["convergence_cycles"], "cycles_gossip": legacy_cycles,
           "control_match": not ctl_bad, "control_detail": ctl_bad, "worker_views_differ": ctl_worker_views, "daf_calls": Agent.calls - calls0,
           "enroll_ms": round(enroll_ms, 1)}
    dd.close()
    return res


def legacy_cycles_of(key):
    topo = getattr(H, key)()
    return LE.converge_real(topo, ipr_factory=RB._fake_factory)


def run_scenario(fp, exe, where):
    scn = json.load(open(fp))
    name = os.path.splitext(os.path.basename(fp))[0]
    topo = getattr(H, scn["topology"])()
    H.assign_identities(topo)
    ips = {n.name: n.frognet_ip for n in topo.nodes.values()}
    dd = DafDiscovery(exe, where, f"frognet-scn-{name.replace('_', '-')}#1")
    for n in topo.nodes.values():
        dd.enroll(n)
    undo = install(dd)
    try:
        LE.converge_real(topo, ipr_factory=RB._fake_factory)
        op, args = scn["fault"]["op"], scn["fault"].get("args", [])
        dead_net, gone_ms = None, None
        if op == "kill_node":
            # the node dies: its session ends, and a watcher on "nodes" sees it leave
            watcher = Agent(exe, where)
            watcher.must("enroll", daf=dd.D, apn="watcher", role="observer")
            rev = watcher.must("resolve", daf=dd.D, name="nodes")["rev"]
            t0 = time.monotonic()
            dd.kill(args[0])
            got = watcher.watch(dd.D, "nodes", rev, 5.0)
            gone_ms = round((time.monotonic() - t0) * 1000, 1) if got else None
            still = [m["apn"] for m in watcher.must("resolve", daf=dd.D, name="nodes", live=True)["members"]]
            watcher.close()
            _ip, dead_net = LF.kill_node(topo, *args)
        elif op == "drop_link":
            LF.drop_link(topo, *args)
            still = None
        LE.converge_real(topo, ipr_factory=RB._fake_factory)
    finally:
        undo()
    viol = RB._check_expect(topo, scn.get("expect", {}), ips, dead_net)
    if op == "kill_node" and args[0] in (still or []):
        viol.append(f"{args[0]} still listed in nodes after its session ended")
    dd.close()
    return {"scenario": name, "topology": scn["topology"], "fault": f"{op} {' '.join(args)}", "pass": not viol,
            "violations": viol, "left_nodes_ms": gone_ms}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rinab", default=os.path.join(_PARENT, "../fdf/src/fdf-source/rinab"))
    ap.add_argument("--port", type=int, default=51977)
    ap.add_argument("--json")
    a = ap.parse_args()
    rinab = os.path.abspath(a.rinab)
    exe = os.path.join(rinab, "build/bin/daf-agent")
    tmp = tempfile.mkdtemp(prefix="daf_discovery_")
    host = start_host(rinab, a.port, tmp)
    where = f"127.0.0.1:{a.port}"
    print("DAF DISCOVERY TIER - FrogNet discovery by the DAF model (op=daf on a real rina-host), "
          "real planner + committer, hardware baselines as the oracle")
    results, scen = [], []
    try:
        for key in RB.list_baseline_keys():
            legacy = legacy_cycles_of(key)
            r = run_topology(key, exe, where, legacy)
            results.append(r)
            ok = r["routes_match"] and r["reach_match"] and r["control_match"]
            print(f"  [{'PASS' if ok else 'FAIL'}] {key:34s} nodes {r['nodes']:2d}  routes {'=' if r['routes_match'] else '!='} "
                  f"{r['baseline_source']}  reach {r['reach']['pairs_ok']}/{r['reach']['pairs_total']}  control "
                  f"{'agrees' if r['control_match'] else 'DIFFERS'}  cycles daf {r['cycles_daf']} / gossip {r['cycles_gossip']}")
            if not ok:
                for node, d in r["route_diff"].items():
                    print(f"           {node} +{d['added']} -{d['removed']}")
                for c in r["control_detail"]:
                    print(f"           control {c}")
            for c in r["worker_views_differ"]:
                print(f"           (info) worker gossip view: {c}")
        for fp in sorted(glob.glob(os.path.join(RB.SCENARIO_DIR, "*.json"))):
            s = run_scenario(fp, exe, where)
            scen.append(s)
            print(f"  [{'PASS' if s['pass'] else 'FAIL'}] scenario {s['scenario']:28s} {s['fault']:16s} "
                  f"left 'nodes' in {s['left_nodes_ms']} ms" + (f"  {s['violations']}" if s["violations"] else ""))
    finally:
        host.terminate()
        host.wait()
    bad = [r for r in results if not (r["routes_match"] and r["reach_match"] and r["control_match"])] + \
          [s for s in scen if not s["pass"]]
    if a.json:
        json.dump({"topologies": results, "scenarios": scen}, open(a.json, "w"), indent=2)
    print(f"DAF DISCOVERY: {'PASS' if not bad else 'FAIL'} ({len(results)} topologies, {len(scen)} failure scenarios, "
          f"{len(bad)} failing)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
