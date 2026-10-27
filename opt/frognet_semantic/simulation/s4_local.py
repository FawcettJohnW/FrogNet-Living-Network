#!/usr/bin/env python3
"""
s4_local.py - [S4_LOCAL_V1] the TIER 4 comparison on ONE machine (the bring-up and the calibration runs; the
comparison itself is cross-site, s4_dist.py). Builds the bed, runs one arm, and measures:
  cold     start -> every node's walk to every served /24 ok and on a shortest path (time; bytes and packets sent)
  steady   bytes and packets sent over STEADY_S seconds with nothing changing
  cut      egress e1's hub link down -> every pair across the cut has no route and every other pair still reaches
  heal     the link back -> every pair ok again
  per node CPU (ms) and RSS (KB) of the arm's processes
usage: s4_local.py --arm isis [--profile tuned|default] --S 20 --E 2 [--out DIR]
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import s4_bed as B          # noqa: E402


BED = None


def verify(p, tables, sp, own, cut=frozenset()):
    """-> (ok pairs, on-shortest pairs, total, bad[:5]). With `cut`, pairs across it must have NO route."""
    served = {d["served"]: n for n, d in p["nodes"].items() if d.get("served")}
    ok = short = tot = 0
    bad = []
    for src in p["nodes"]:
        for net, dst in served.items():
            if dst == src:
                continue
            tot += 1
            path, v = B.walk(p, tables, src, net, own, frozenset(BED.silenced) if BED else frozenset())
            across = (src in cut) != (dst in cut)
            if across:
                if v == "NO ROUTE" or (v.startswith("NO HOLDER") and False):
                    ok += 1; short += 1
                else:
                    bad.append(f"{src}->{net}: expected no route across the cut, got {v} {'>'.join(path)}")
                continue
            if v == "ok":
                ok += 1
                if len(path) - 1 <= sp[src][net]:
                    short += 1
            else:
                bad.append(f"{src}->{net}: {v} {'>'.join(path)}")
    return ok, short, tot, bad[:5]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="isis")
    ap.add_argument("--profile", default="tuned")
    ap.add_argument("--S", type=int, default=20)
    ap.add_argument("--E", type=int, default=2)
    ap.add_argument("--steady", type=float, default=30)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--out", default=".")
    ap.add_argument("--frr-bin", default=None)
    ap.add_argument("--ram-bin", default=None, help="rina-host, fdf-client, fdf-events, wireguard-go")
    ap.add_argument("--fdf", default=None, help="the FD&F tree: bin/fdf-hub, bin/fdf-facts, lib/python")
    ap.add_argument("--cut", default="silent", choices=["silent", "carrier"],
                    help="silent: the link stays up and carries nothing (a WAN failure); carrier: the link goes down")
    a = ap.parse_args()
    p = B.plan(a.S, a.E, [{"name": "local", "ip": "127.0.0.1"}])
    sp, own = B.shortest(p), B.owners(p)
    work = tempfile.mkdtemp(prefix=f"s4-{a.arm}-S{a.S}E{a.E}-", dir=a.out)
    global BED
    bed = BED = B.Bed(p, "local").build()
    res = {"arm": a.arm, "profile": a.profile, "S": a.S, "E": a.E, "nodes": len(p["nodes"]), "cut": a.cut}
    w = None
    arm = None
    try:
        w = B.Watcher(bed)
        b0 = B.wire_bytes(bed)
        if a.arm == "isis":
            import s4_isis
            arm = s4_isis.Isis(bed, work, a.profile, a.frr_bin)
        elif a.arm == "ram":
            import s4_ram
            arm = s4_ram.Ram(bed, work, a.ram_bin, a.fdf)
        else:
            raise SystemExit(f"arm {a.arm}: isis or ram")
        t0 = arm.start()

        def all_ok(t):
            ok, short, tot, _ = verify(p, t, sp, own)
            return ok == tot
        good, tables = w.wait_until(all_ok, a.timeout)
        res["cold_s"] = round(time.time() - t0, 2) if good else None
        ok, short, tot, bad = verify(p, tables, sp, own)
        b1 = B.wire_bytes(bed)
        res.update(cold_ok=ok, cold_shortest=short, pairs=tot, cold_bad=bad, cold_bytes=b1[0] - b0[0], cold_packets=b1[1] - b0[1])
        time.sleep(a.steady)
        b2 = B.wire_bytes(bed)
        res.update(steady_s=a.steady, steady_bytes=b2[0] - b1[0], steady_packets=b2[1] - b1[1])
        # cut: e1's hub link down (its end, in e1's namespace)
        cut = {n for n, d in p["nodes"].items() if n == "e1" or d.get("egress") == "e1"}
        t1 = time.time()
        hub_end = next(i for i in p["nodes"]["hub"]["ifaces"] if i == "hl1")          # e1's hub link, hub side
        if a.cut == "carrier":
            B.sh("ip", "link", "set", "hl0", "down", ns="e1")
        else:
            bed.silence("e1", "hl0"); bed.silence("hub", hub_end)
        def cut_ok(t):
            ok, _, tot, _ = verify(p, t, sp, own, cut)
            return ok == tot
        good, tables = w.wait_until(cut_ok, a.timeout)
        res["cut_s"] = round(time.time() - t1, 2) if good else None
        ok, short, tot, bad = verify(p, tables, sp, own, cut)
        if not good:                                   # [DIAG-S4-CUT] what the watcher holds against what the kernel holds
            for n in ("hub", "e1"):
                print(f"[DIAG-S4-CUT] {n} watcher {sorted(tables.get(n, {}))[:6]} kernel {sorted(B.Bed.routes(bed, n))[:6]}",
                      file=sys.stderr)
        b3 = B.wire_bytes(bed)
        res.update(cut_ok=ok, cut_bad=bad, cut_bytes=b3[0] - b2[0], cut_packets=b3[1] - b2[1])
        t2 = time.time()
        if a.cut == "carrier":
            B.sh("ip", "link", "set", "hl0", "up", ns="e1")
        else:
            bed.silence("e1", "hl0", False); bed.silence("hub", hub_end, False)
        good, tables = w.wait_until(all_ok, a.timeout)
        res["heal_s"] = round(time.time() - t2, 2) if good else None
        ok, short, tot, bad = verify(p, tables, sp, own)
        b4 = B.wire_bytes(bed)
        res.update(heal_ok=ok, heal_shortest=short, heal_bad=bad, heal_bytes=b4[0] - b3[0], heal_packets=b4[1] - b3[1])
        u = arm.usage()
        cpu = sorted(v[0] for v in u.values()); rss = sorted(v[1] for v in u.values())
        res.update(cpu_ms_p50=cpu[len(cpu) // 2], cpu_ms_max=cpu[-1], rss_kb_p50=rss[len(rss) // 2], rss_kb_max=rss[-1],
                   rss_mb_total=round(sum(rss) / 1024, 1))
    finally:
        if arm:
            arm.stop()
        if w:
            w.close()
        bed.teardown()
    print(json.dumps(res, indent=1))
    json.dump(res, open(os.path.join(a.out, f"s4-{a.arm}-{a.profile}-S{a.S}-E{a.E}.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
