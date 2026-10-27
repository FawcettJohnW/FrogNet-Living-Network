#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_microloop.py - [S4_MICROLOOP_V1] measure IS-IS TRANSIENT micro-loops on the live bed. This is arm 3 of the loop-
prevention study (the MEASURED one): plain FRR isisd with no loop prevention, so that the modeled IS-IS loop curve in
s4_loopfree.py is anchored by real counts. It answers, with numbers, John Day's own teaching point -- that TTL squelches
looping packets but does not eliminate loops: here is how many loops actually form after a cut, and how long each lives.

HOW IT WORKS
  Through the reconvergence window after a link cut, sample EVERY node's FIB rapidly (default every 50 ms). Each sample
  is a snapshot {node: {dest/24: next-hop node}}. For each destination, follow the next-hop chain from every node; a
  chain that revisits a node is a forwarding loop for that destination in that snapshot. Track each distinct loop
  (its destination + the set of nodes on it) across snapshots to get: how many formed, and how long each persisted
  (first_seen .. last_seen). A loop that spans only two snapshots at 50 ms still counts -- these are brief by nature.

TWO MODES
  --selftest   feed a scripted sequence of FIB snapshots with known loops and verify the detector's count and lifetimes
               (no bed, no root; this proves the logic that the bed run relies on).
  --bed        sample the real namespaces. Reads each node's table with `ip netns exec <ns> ip route` and maps the
               next-hop IP back to a node via the plan's address map. Orchestrated the FrogNet way: the controller
               announces the cut and the sample window as a tuple in shared RAM; this tool is picked up by held read and
               writes its result back the same way (no polling of the controller). See the controller wiring note below.

OUTPUT (per run): {"window_ms", "interval_ms", "samples", "loops":[{"dest","nodes","first_ms","last_ms","life_ms"}],
                   "loops_total", "loop_node_events", "max_life_ms"} -- loops_total is distinct loops observed;
loop_node_events sums members affected; max_life_ms is the longest-lived loop. Plain IS-IS is expected to be > 0 here,
which is the whole point; SPB and FrogNet would be 0 (held / refused).

usage:
  s4_microloop.py --selftest
  s4_microloop.py --bed --plan <plan.json> --window-ms 4000 --interval-ms 50     # run on the bed, after a cut
"""
import argparse
import json
import subprocess
import sys
import time

BUILD = "s4-microloop-2026-10-26-r1"


# ---- the detector: snapshots -> loops with lifetimes -------------------------------------------------------------
def loops_in_snapshot(fib):
    """fib = {node: {dest: next_hop_node}}. Return {dest: frozenset(loop_nodes)} for every destination whose next-hop
    chain, started from some node, revisits a node (a forwarding loop). Deterministic: the loop is the cycle's node
    set, so the same loop from any entry node collapses to one entry."""
    found = {}
    dests = {d for t in fib.values() for d in t}
    for dest in dests:
        for start in fib:
            seen, cur, path = set(), start, []
            while cur is not None and cur not in seen:
                seen.add(cur); path.append(cur)
                nh = fib.get(cur, {}).get(dest)
                if nh == cur:                              # points at self: not a transit loop
                    nh = None
                cur = nh
            if cur is not None and cur in seen:            # revisited -> cycle; take the cycle's node set
                cyc = frozenset(path[path.index(cur):])
                if len(cyc) >= 2:
                    found[dest] = cyc
                    break                                  # one loop per dest per snapshot is enough to flag it
    return found


def track(snapshots, interval_ms):
    """snapshots = list of (t_ms, fib). Track each distinct (dest, loop-node-set) across time -> lifetimes."""
    live = {}                                              # (dest, nodes) -> [first_ms, last_ms]
    done = []
    for t_ms, fib in snapshots:
        now = loops_in_snapshot(fib)
        keys = {(d, nodes) for d, nodes in now.items()}
        for k in keys:
            if k in live:
                live[k][1] = t_ms
            else:
                live[k] = [t_ms, t_ms]
        for k in list(live):
            if k not in keys:
                f, l = live.pop(k)
                done.append((k[0], k[1], f, l))
    for k, (f, l) in live.items():
        done.append((k[0], k[1], f, l))
    loops = [{"dest": d, "nodes": sorted(nodes), "first_ms": f, "last_ms": l,
              "life_ms": (l - f) + interval_ms}                # at least one interval wide
             for (d, nodes, f, l) in done]
    loops.sort(key=lambda x: (-x["life_ms"], x["dest"]))
    return {
        "interval_ms": interval_ms, "samples": len(snapshots),
        "window_ms": (snapshots[-1][0] - snapshots[0][0]) if snapshots else 0,
        "loops": loops, "loops_total": len(loops),
        "loop_node_events": sum(len(x["nodes"]) for x in loops),
        "max_life_ms": max((x["life_ms"] for x in loops), default=0),
    }


# ---- bed mode: read real namespace FIBs --------------------------------------------------------------------------
def _ip_to_node(plan):
    m = {}
    for name, d in plan["nodes"].items():
        for ip in d.get("ips", []):
            m[ip] = name
        if d.get("subnet3"):
            m[d["subnet3"] + ".1"] = name
    return m


def read_bed_fib(plan):
    """One snapshot across the bed: {node: {dest/24: next_hop_node}} from `ip netns exec <ns> ip route`."""
    ip2n = _ip_to_node(plan)
    fib = {}
    for name, d in plan["nodes"].items():
        ns = d.get("netns", name)
        out = subprocess.run(["ip", "netns", "exec", ns, "ip", "route"],
                             capture_output=True, text=True).stdout
        t = {}
        for line in out.splitlines():
            p = line.split()
            if not p or not p[0].endswith("/24") or not p[0].startswith("10."):
                continue
            dest = p[0]
            via = p[p.index("via") + 1] if "via" in p else None
            if via and via in ip2n:
                t[dest] = ip2n[via]
        fib[name] = t
    return fib


def sample_bed(plan, window_ms, interval_ms):
    snaps = []
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < window_ms:
        t_ms = int((time.monotonic() - t0) * 1000)
        snaps.append((t_ms, read_bed_fib(plan)))
        time.sleep(interval_ms / 1000.0)
    return track(snaps, interval_ms)


# ---- selftest: scripted snapshots with known loops ---------------------------------------------------------------
def selftest():
    # A -> B -> A for dest D, present in snapshots at 50,100,150 ms (3 samples, life ~ 150 ms); clean before and after.
    clean = {"A": {"D": "G"}, "B": {"D": "G"}, "G": {"D": "G"}}
    loop2 = {"A": {"D": "B"}, "B": {"D": "A"}, "G": {"D": "G"}}
    # a 3-node loop X->Y->Z->X for dest E in one snapshot only (life ~ one interval)
    loop3 = {"X": {"E": "Y"}, "Y": {"E": "Z"}, "Z": {"E": "X"}}
    snaps = [(0, clean), (50, loop2), (100, loop2), (150, loop2), (200, clean),
             (250, {**clean, **loop3}), (300, clean)]
    r = track(snaps, 50)
    ok = True
    def want(cond, msg):
        nonlocal ok
        print(("  [PASS] " if cond else "  [FAIL] ") + msg); ok = ok and cond
    want(r["loops_total"] == 2, f"two distinct loops detected (got {r['loops_total']})")
    byd = {x["dest"]: x for x in r["loops"]}
    want("D" in byd and byd["D"]["nodes"] == ["A", "B"], "the A<->B loop on D is found")
    want("D" in byd and byd["D"]["life_ms"] == 150, f"A<->B loop life = 3 samples x 50ms = 150 (got {byd.get('D',{}).get('life_ms')})")
    want("E" in byd and byd["E"]["nodes"] == ["X", "Y", "Z"], "the 3-node loop on E is found")
    want("E" in byd and byd["E"]["life_ms"] == 50, f"single-sample loop life = 1 interval = 50 (got {byd.get('E',{}).get('life_ms')})")
    want(r["max_life_ms"] == 150, "max life reported")
    print("\n" + ("SELFTEST PASS" if ok else "SELFTEST FAIL") + f"   build={BUILD}")
    return 0 if ok else 1


# ---- controller wiring note (how the bed run is driven, the FrogNet way) -----------------------------------------
# The controller (s4_dist.py) announces, as a tuple in the shared RAM 'tables' family: the cut to make and the sample
# window. Each node holds a read on its family; when the cut-tuple lands it (a) applies/awaits the cut and (b) runs
# `s4_microloop.py --bed` for the window, then writes the result back by join() -- the controller collects it on its own
# held read. No polling, one tarball (this file ships in the existing day-claims package via make_day_claims.sh).

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--bed", action="store_true")
    ap.add_argument("--plan")
    ap.add_argument("--window-ms", type=int, default=4000)
    ap.add_argument("--interval-ms", type=int, default=50)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.bed:
        if not a.plan:
            print("--bed needs --plan <plan.json>", file=sys.stderr); return 2
        plan = json.load(open(a.plan))
        print(json.dumps(sample_bed(plan, a.window_ms, a.interval_ms), indent=1))
        return 0
    print("nothing to do: pass --selftest or --bed --plan <plan.json>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
