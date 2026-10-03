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
"""frogsim_online.py - ONLINE mode: validate the engine's converged routes against
a REAL Linux FIB. Each node becomes its own network namespace with real veth
interfaces + addresses; the node's derived routing table (from the real discovery
engine's offline convergence) is installed into that namespace's actual kernel FIB;
then every destination is resolved with `ip route get`. The kernel is the oracle:

  - a route whose `via` is not on-link is REJECTED by the kernel ("invalid gateway")
  - `ip route get <dst>` returns the FIB's real winner (dev + via)
  - we assert the kernel's winner MATCHES the engine's route_egress for every pair

Needs CAP_NET_ADMIN (netns + veth). Run:  sudo -E python3 frogsim_online.py
(or in a container that already has net-admin, as here).
"""
import os, sys, subprocess, re
WT = os.environ.get("FROGNET_SEMANTIC_ROOT", "/home/claude/descend_good/opt/frognet_semantic")
sys.path.insert(0, WT)
from discovery.sim.system import System, TopologySpec, NodeSpec as N   # noqa: E402
from discovery.sim.fabric import route_egress                          # noqa: E402


def sh(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd}\n{r.stderr.strip()}")
    return r.returncode, (r.stdout + r.stderr)


def _devs_and_addrs(routes):
    devs, addrs = set(), {}
    for line in routes:
        p = line.split()
        if "dev" in p:
            devs.add(p[p.index("dev") + 1])
        if "proto" in p and "kernel" in p and "src" in p:
            addrs[p[p.index("dev") + 1]] = p[p.index("src") + 1]
    return devs, addrs


def setup_netns(ns, routes):
    """Build the namespace and install the derived table. Returns install problems
    (real ones only - 'File exists' on a redundant route is benign)."""
    sh(f"ip netns add {ns}")
    sh(f"ip netns exec {ns} ip link set lo up")
    devs, addrs = _devs_and_addrs(routes)
    for d in devs:
        sh(f"ip netns exec {ns} ip link add {d} type veth peer name {d}p")
        sh(f"ip netns exec {ns} ip link set {d} up")
    for d, a in addrs.items():
        sh(f"ip netns exec {ns} ip addr add {a}/24 dev {d}")
    problems = []
    for line in routes:
        p = line.split()
        if "proto" in p and "kernel" in p:      # connected route auto-created by the addr
            continue
        rc, out = sh(f"ip netns exec {ns} ip route add {line}", check=False)
        if rc != 0:
            low = out.lower()
            if "invalid gateway" in low:
                problems.append(f"KERNEL REJECTED (off-link via): {line}")
            elif "file exists" not in low:       # benign: redundant/duplicate metric row
                problems.append(f"install failed: {line} -> {out.strip()}")
    return problems


def _fib_get(ns, dst):
    rc, out = sh(f"ip netns exec {ns} ip route get {dst}", check=False)
    if rc != 0 or "unreachable" in out.lower():
        return None
    dev = via = None
    m = re.search(r"\bdev\s+(\S+)", out)
    if m:
        dev = m.group(1)
    m = re.search(r"\bvia\s+(\S+)", out)
    if m:
        via = m.group(1)
    return dev, via


def validate_online(system, node, ns):
    """Assert the real FIB resolves every other node's /24 AND agrees with the engine."""
    probs = []
    for other, ond in system.node.items():
        if other == node or getattr(ond, "dead", False):
            continue
        dst = ond.one
        got = _fib_get(ns, dst)
        if got is None:
            probs.append(f"{node}: real FIB cannot resolve {dst} ({other})")
            continue
        exp = route_egress(system.routes[node], dst)     # engine's winner (dev, metric, via)
        if exp is None:
            probs.append(f"{node}: engine had no route to {dst} but FIB resolved {got}")
            continue
        exp_dev, _m, exp_via = exp
        got_dev, got_via = got
        if got_dev != exp_dev:
            probs.append(f"{node}->{dst}: dev mismatch kernel={got_dev} engine={exp_dev}")
        if (exp_via or None) != (got_via or None):
            probs.append(f"{node}->{dst}: via mismatch kernel={got_via} engine={exp_via}")
    return probs


def run_topology_online(name, spec):
    s = System(spec)
    s.converge(max_cycles=25)
    if s.validate():
        return [f"{name}: OFFLINE convergence failed (not testing online)"], 0
    problems, npairs = [], 0
    for node in list(s.node):
        if getattr(s.node[node], "dead", False):
            continue
        ns = f"fg_{os.getpid()}_{name}_{node}"[:60].replace(".", "_")
        sh(f"ip netns del {ns}", check=False)
        try:
            problems += setup_netns(ns, s.routes[node])
            problems += validate_online(s, node, ns)
            npairs += sum(1 for o in s.node if o != node)
        finally:
            sh(f"ip netns del {ns}", check=False)
    return problems, npairs


def run():
    import frogsim_topologies as FT              # SAME 18 the offline gate proves
    cases = dict(FT.topologies())
    cases["seattle_chain"] = FT.seattle_chain()
    ok = 0
    for name, spec in cases.items():
        try:
            probs, npairs = run_topology_online(name, spec)
        except Exception as e:
            probs, npairs = [f"{name}: EXC {e!r}"], 0
        if not probs:
            print(f"  PASS  {name:16} {npairs:3} pairs - real FIB resolves all, matches engine"); ok += 1
        else:
            print(f"  FAIL  {name:16} {len(probs)} problems e.g. {probs[0]}")
    total = len(cases)
    print(f"\nONLINE FIB GATE: {'PASS' if ok == total else 'FAIL'} "
          f"({ok}/{total} topologies validated on a real kernel FIB)")
    return ok == total


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
