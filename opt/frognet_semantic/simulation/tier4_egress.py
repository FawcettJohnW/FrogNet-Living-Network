#!/usr/bin/env python3
"""tier4_egress.py -- [TIER4_SECOND_EGRESS_V1] a second egress point for a LAN, on tier4_transport's bed (real kernel,
real planner and committer, every tunnel relayed by the hub with a delay per uplink, measured RTTs, real packets by
name). BOX-ONLY.

Derived from the documented topo_asym_lan2_wg_lan3 (site A: A1, A2 on a peering LAN, A2 the gateway with a tunnel
to B1; site B: B1, B2, B3 on a peering LAN). The derivation: A1 gets an uplink of its own and a tunnel to B1 (A1 wg1
10.250.2.1/30 <-> B1 wg1 10.250.2.2/30). Site A now has two egress points; each pays its own leg to the hub.

Scenarios (one-way leg to the hub, ms; site B's leg 35):
  faster   A1 5,  A2 10   A1's own exit is the better one by 10 ms RTT
  under    A1 30, A2 10   A1's own exit is worse by 40 ms RTT: under the planner's switch bar (>=100 ms AND >=20%)
  over     A1 120, A2 10  A1's own exit is worse by 220 ms RTT: over it
For each: converge; every pair by name; then cut the tunnel A1's traffic to B1 leaves on (the bearer stops and the
segment goes, as live_failures.drop_link), re-converge from the learned state (keep_state, [CONVERGE_CONTINUES_V1]), every pair again. Then restore it and re-converge once more.
OUT/egress.jsonl gets one line per (scenario, phase, pair); OUT/egress-summary.txt the readable lines."""
import json, os, subprocess, sys, time
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import tier4_transport as T
import frognet_sim as H
import live_engine as LE
from netns_backend import make_ipr_factory

SCEN = [("faster", {"A1": 5.0, "A2": 10.0}), ("under", {"A1": 30.0, "A2": 10.0}), ("over", {"A1": 120.0, "A2": 10.0})]


def topo_two_exits():
    t = H.topo_asym_lan2_wg_lan3()
    t.label = "site A with two egress points (A2's tunnel and A1's own) <-> site B"
    t.nodes["A1"].add_iface("wg1", "10.250.2.1/30")
    t.nodes["B1"].add_iface("wg1", "10.250.2.2/30")
    t.link(("A1", "wg1"), ("B1", "wg1"))
    return t


def restart_relay(bed, drop=None):
    """The hub relay for every tunnel except `drop` (a (node, iface) end of the tunnel to cut)."""
    if bed.relay: bed.relay.terminate(); bed.relay.wait()
    links = []
    for (na, ia, pa, nb, ib, pb) in bed.prov.wg_links:
        if drop and drop in ((na, ia), (nb, ib)): continue
        d = bed.delay[(na, ia, nb)]; links.append([pa, pb, d, d])
    cfg = os.path.join(bed.out, f"{bed.bname}.relay.json"); json.dump({"offset": T.OFF, "links": links}, open(cfg, "w"))
    bed.relay = subprocess.Popen([sys.executable, "-I", os.path.join(_HERE, "wg_bearer_relay.py"), cfg],
                                 stdout=open(os.path.join(bed.out, f"{bed.bname}.relay.log"), "a"), stderr=subprocess.STDOUT)


def kernel_routes(bed):
    return {n: T.sh("ip", "netns", "exec", bed.ns(n), "ip", "-o", "route")[1] for n in bed.topo.nodes}


def main():
    out = os.environ.get("OUT", os.path.abspath("tier4-egress-out")); os.makedirs(out, exist_ok=True)
    jl = open(os.path.join(out, "egress.jsonl"), "a"); summ = open(os.path.join(out, "egress-summary.txt"), "a")
    def say(s): print(s, flush=True); summ.write(s + "\n"); summ.flush()
    say(f"=== second egress point: BEARER={T.BEARER} site legs {T.HUB_LEGS[:2]} "
        f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===")
    allok = True
    for name, nl in SCEN:
        bed = T.Bed(f"egress_{name}", out, topo=topo_two_exits(), node_leg=nl); orig = (H._probe_direct, H._trace_rtt_via)
        try:
            bed.up(); rtt = bed.measure_edges()
            dead = [k for k, v in rtt.items() if v is None]
            if dead: say(f"[FAIL] {name}: edges never answered {dead}"); allok = False; continue
            bed.patch_probes()
            tun = {k: v for k, v in rtt.items() if k[1].startswith("wg")}
            say(f"--- {name}: A1 leg {nl['A1']}, A2 leg {nl['A2']}; measured tunnel RTTs " +
                ", ".join(f"{a}/{d}->{b} {v:.1f}" for (a, d, b), v in sorted(tun.items())))

            def phase(label):
                bed.write_hosts(); rows = bed.pairs()
                for r in rows: r.update(scenario=name, phase=label); jl.write(json.dumps(r) + "\n")
                jl.flush()
                n = len(rows); got = sum(r["rx"] == r["n"] for r in rows); named = sum(r["by_name"] for r in rows)
                key = {(r["src"], r["dst"]): r for r in rows}
                def show(a, b):
                    r = key[(a, b)]
                    return (f"{a}->{b}: {' '.join(r['path'] or ['-'])} | back {' '.join(r['back'] or ['-'])} | "
                            f"RTT {r['rtt_min']} ms (floor {r['floor_rtt']}, lowest {r['best_rtt']})")
                say(f"  [{label}] {bed.cycles} cycles; packets {got}/{n} pairs all answered; by name {named}/{n}")
                for a, b in (("A1", "B1"), ("A1", "B3"), ("B2", "A1"), ("A2", "B1")): say("      " + show(a, b))
                return rows, got == n and named == n

            bed.converge(); rows, ok1 = phase("converged")
            used = next(r for r in rows if r["src"] == "A1" and r["dst"] == "B1")["path"]
            # the tunnel A1's traffic to B1 leaves on: A1's own wg1, or A2's wg0
            cut = ("A1", "wg1") if used and used[0] == "A1/wg1" else ("A2", "wg0")
            peer = {("A1", "wg1"): "B1", ("A2", "wg0"): "B1"}[cut]
            restart_relay(bed, drop=cut)
            seg_saved = list(bed.topo.segments)
            bed.topo.segments = [s for s in bed.topo.segments if cut not in s]
            bed.cycles = LE.converge_real(bed.topo, ipr_factory=make_ipr_factory("real", bed.prov), keep_state=True)
            _, ok2 = phase(f"cut {cut[0]}/{cut[1]}")
            restart_relay(bed); bed.topo.segments = seg_saved
            for _ in range(15):                     # the restored tunnel handshakes before the probes measure it
                if T.ping(bed.ns(cut[0]), bed.topo.nodes[peer].ifaces["wg1" if cut[0] == "A1" else "wg0"].ip,
                          dev=cut[1], n=3)["rx"]: break
                time.sleep(1)
            bed.cycles = LE.converge_real(bed.topo, ipr_factory=make_ipr_factory("real", bed.prov), keep_state=True)
            _, ok3 = phase("restored")
            allok &= ok1 and ok2 and ok3
        except Exception as e:
            allok = False; say(f"[FAIL] {name}: {type(e).__name__}: {e}")
        finally:
            H._probe_direct, H._trace_rtt_via = orig
            bed.down()
    say("ALL PASS" if allok else "FAILURES")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
