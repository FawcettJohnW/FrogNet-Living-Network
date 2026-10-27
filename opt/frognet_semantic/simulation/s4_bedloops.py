#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_bedloops.py - [S4_BEDLOOPS_V1] measure forwarding loops in the REAL bed IS-IS FIBs, with negative tests that prove
the detector has teeth. Arm 3's measured half: build the next-hop graph from each node's `ip route show table all`
(captured in an s4-dist run's ram_cold_diag), and for every destination find any forwarding cycle.

A detector that can only ever return 0 proves nothing, so this ships with NEGATIVE tests: known loops injected into real
(and synthetic) FIBs that the detector MUST flag, plus the clean FIB as the positive control that MUST read 0. The real
runs read 0; the negative tests confirm that 0 is a pass, not a blind spot.

  --check <run.json>      detect loops in a real run's ram_cold FIBs (prints per-dest loops + total)
  --negative <run.json>   inject a 2-node and a 3-node loop into that run's real FIBs, confirm each is caught, and
                          confirm the unmodified FIBs read 0 (the on-real-data negative test)
  --selftest              synthetic FIBs: clean (0), 2-cycle, 3-cycle, 4-cycle, and a self-pointing non-loop
"""
import json
import re
import sys


# ---- detector: a forwarding loop for dest D is a cycle in D's next-hop digraph (owner terminates) ----------------
def loops(owns, fib, nodes, dests):
    found = []
    for D in dests:
        owner = {n for n in nodes if D in owns.get(n, set())}
        adj = {n: [h for h in fib.get(n, {}).get(D, []) if h != n and n not in owner] for n in nodes}
        WHITE, GREY, BLACK = 0, 1, 2
        color = {n: WHITE for n in nodes}
        cyc = None
        def dfs(u, stack):
            nonlocal cyc
            color[u] = GREY; stack.append(u)
            for v in adj.get(u, ()):
                if v in owner:
                    continue
                if color[v] == GREY:                       # back edge -> cycle
                    cyc = stack[stack.index(v):]; return True
                if color[v] == WHITE and dfs(v, stack):
                    return True
            color[u] = BLACK; stack.pop(); return False
        for s in nodes:
            if color[s] == WHITE and s not in owner and dfs(s, []):
                break
        if cyc:
            found.append((D, sorted(cyc)))
    return found


# ---- build FIBs from a run's ram_cold_diag -----------------------------------------------------------------------
def fib_from_run(path):
    d = json.load(open(path))
    diag = d.get("ram_cold_diag") or {}
    routes = {}
    for _mach, ns in diag.items():
        if isinstance(ns, dict):
            for node, e in ns.items():
                ra = e.get("route_all") if isinstance(e, dict) else None
                out = ra.get("out") if isinstance(ra, dict) else (ra if isinstance(ra, str) else None)
                if out:
                    routes[node] = out
    ip2node = {}
    for node, out in routes.items():
        for ip in re.findall(r"local (\d+\.\d+\.\d+\.\d+) ", out):
            ip2node[ip] = node
    owns, fib = {}, {}
    for node, out in routes.items():
        owns.setdefault(node, set()); fib.setdefault(node, {})
        lines = out.split("\n")
        for i, ln in enumerate(lines):
            m = re.match(r"(10\.20\.\d+\.\d+/24)\s", ln)
            if not m:
                continue
            dest = m.group(1)
            if "proto kernel" in ln and "scope link" in ln:
                owns[node].add(dest)
            elif "proto isis" in ln:
                nhs = re.findall(r"via (\d+\.\d+\.\d+\.\d+)", ln)
                j = i + 1
                while j < len(lines) and lines[j].lstrip().startswith("nexthop via"):
                    nhs += re.findall(r"via (\d+\.\d+\.\d+\.\d+)", lines[j]); j += 1
                nn = sorted({ip2node[x] for x in nhs if x in ip2node})
                if nn:
                    fib[node][dest] = nn
    nodes = sorted(routes)
    dests = sorted({x for s in owns.values() for x in s} | {x for f in fib.values() for x in f})
    return d.get("run"), owns, fib, nodes, dests


# ---- modes -------------------------------------------------------------------------------------------------------
def check(path):
    run, owns, fib, nodes, dests = fib_from_run(path)
    ls = loops(owns, fib, nodes, dests)
    print(f"run {run}: {len(nodes)} nodes, {len(dests)} dest subnets")
    for D, c in ls:
        print(f"  LOOP {D}: cycle {c}")
    print(f"TOTAL forwarding loops (real bed IS-IS FIBs): {len(ls)}")
    return 0


def negative(path):
    run, owns, fib, nodes, dests = fib_from_run(path)
    ok = True
    def want(cond, msg):
        nonlocal ok; print(("  [PASS] " if cond else "  [FAIL] ") + msg); ok = ok and cond
    base = len(loops(owns, fib, nodes, dests))
    want(base == 0, f"unmodified real FIBs read 0 loops (positive control) — got {base}")
    # pick a dest and two/three transit nodes that currently route to it (not its owner)
    D = next(d for d in dests)
    owner = {n for n in nodes if D in owns.get(n, set())}
    transit = [n for n in nodes if n not in owner and D in fib.get(n, {})][:3]
    import copy
    # NEGATIVE 1: 2-node hairpin A<->B for D
    f2 = copy.deepcopy(fib); a, b = transit[0], transit[1]
    f2[a][D] = [b]; f2[b][D] = [a]
    l2 = loops(owns, f2, nodes, dests)
    want(any(set(c) == {a, b} for _, c in l2), f"injected 2-node loop {a}<->{b} on {D} is caught")
    # NEGATIVE 2: 3-node cycle A->B->C->A for D
    f3 = copy.deepcopy(fib); a, b, c = transit[0], transit[1], transit[2]
    f3[a][D] = [b]; f3[b][D] = [c]; f3[c][D] = [a]
    l3 = loops(owns, f3, nodes, dests)
    want(any(set(cc) == {a, b, c} for _, cc in l3), f"injected 3-node loop {a}->{b}->{c}->{a} on {D} is caught")
    # NEGATIVE 3: removing the injected loop returns to clean
    want(len(loops(owns, fib, nodes, dests)) == 0, "detector returns to 0 on the restored FIBs (no false carry-over)")
    print("\n" + ("NEGATIVE TESTS PASS (real bed FIBs)" if ok else "NEGATIVE TESTS FAIL"))
    return 0 if ok else 1


def selftest():
    ok = True
    def want(cond, msg):
        nonlocal ok; print(("  [PASS] " if cond else "  [FAIL] ") + msg); ok = ok and cond
    N = ["A", "B", "C", "E", "G"]; D = "10.20.9.0/24"
    owns = {n: set() for n in N}; owns["G"] = {D}
    clean = {"A": {D: ["G"]}, "B": {D: ["G"]}, "C": {D: ["G"]}, "E": {D: ["G"]}}
    want(len(loops(owns, clean, N, [D])) == 0, "clean FIB reads 0 (positive control)")
    want(any(set(c) == {"A", "B"} for _, c in loops(owns, {"A": {D: ["B"]}, "B": {D: ["A"]}}, N, [D])),
         "2-node loop A<->B caught")
    want(any(set(c) == {"A", "B", "C"} for _, c in
             loops(owns, {"A": {D: ["B"]}, "B": {D: ["C"]}, "C": {D: ["A"]}}, N, [D])),
         "3-node loop A->B->C->A caught")
    want(any(set(c) == {"A", "B", "C", "E"} for _, c in
             loops(owns, {"A": {D: ["B"]}, "B": {D: ["C"]}, "C": {D: ["E"]}, "E": {D: ["A"]}}, N, [D])),
         "4-node loop caught")
    want(len(loops(owns, {"A": {D: ["A"]}, "B": {D: ["G"]}}, N, [D])) == 0,
         "a self-pointing next-hop (A->A) is NOT a transit loop")
    want(len(loops(owns, {"A": {D: ["B", "G"]}, "B": {D: ["A"]}}, N, [D])) == 1,
         "ECMP: a loop through one of two next-hops is still caught")
    print("\n" + ("SELFTEST PASS" if ok else "SELFTEST FAIL"))
    return 0 if ok else 1


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        return selftest()
    if len(sys.argv) >= 3 and sys.argv[1] == "--check":
        return check(sys.argv[2])
    if len(sys.argv) >= 3 and sys.argv[1] == "--negative":
        return negative(sys.argv[2])
    print("usage: s4_bedloops.py --selftest | --check <run.json> | --negative <run.json>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
