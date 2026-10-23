#!/usr/bin/env python3
"""tier4_transport.py [TOPO ...] -- [TIER4_BEARER_ON_THE_EDGE_V1] TIER 4 with a bearer under every tunnel and real
packets over the converged tables. BOX-ONLY (Linux, root, wireguard-go). Same documented topologies, same real
planner and committer, same netns provisioning as live_engine's TIER 4; four things added:

  1. Each WireGuard tunnel's UDP runs through wg_bearer_relay.py, which delays it: the edge carries a bearer
     parameter (AI_README: "A topology edge cannot carry bearer parameters ... That join is the missing piece").
     Delay per tunnel: a site is the served /24's second octet; a tunnel inside a site gets INTRA_MS one way, one
     between sites gets the topology's WAN table below (default WAN_MS). Ethernet segments are kernel bridges (no
     delay added).
  2. The planner's RTTs are MEASURED: before converging, every node pings every L2 peer over each device (min of 5),
     and the model's probes (frognet_sim._probe_direct / _trace_rtt_via) sum those measurements instead of
     EDGE_RTT_BY_KIND. Everything after the probe -- plan, commit, the kernel -- is the real code.
  3. After convergence: forwarding on, each node's /etc/hosts (/etc/netns/<ns>/hosts) written from what the node
     learned (known_hosts), and every node pings every other node BY NAME from its own .1, 10 pings each.
  4. For every pair: the path the kernel actually took (ip route get, hop by hop), its delay floor, and the
     lowest-delay path in the graph. "best" = the taken path's floor equals the lowest-delay path's.

Writes OUT/<topo>.jsonl (one line per pair) and OUT/summary.txt. Env: OUT (default ./tier4-transport-out),
WAN_MS (20), INTRA_MS (1), FDF_WG_IMPL (wireguard-go).
A served /24 with nothing else on it gets a veth pair (no dummy driver here) carrying its address ([SERVED_LAN_DUMMY_V1]): the
provisioner only builds devices that sit on a segment, and a node's own .1 must exist for it to send from it."""
import heapq, ipaddress, json, os, re, statistics, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__)); _PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p not in sys.path: sys.path.insert(0, p)
os.environ.setdefault("FROGNET_LOG_LEVEL", "ERROR")
os.environ.setdefault("FDF_WG_IMPL", "wireguard-go")
import frognet_sim as H
import live_engine as LE
from netns_backend import NetnsProvisioner, ShellRunner, make_ipr_factory

OFF = -20000                  # relay ports sit 20000 below the tunnel ports (51820.. -> 31820..)
WAN_MS = float(os.environ.get("WAN_MS", "20")); INTRA_MS = float(os.environ.get("INTRA_MS", "1"))
# one-way ms between sites (second octet of the served /24), per topology. Chosen so some direct tunnels are slower
# than a two-tunnel detour: the lowest-delay route is then not the fewest-hop one.
WAN = {
    "pond": {(241, 122): 35, (241, 44): 20, (122, 44): 25},          # Seattle, New York, Austin
    "topo_asym_three_sites": {(90, 91): 10, (91, 92): 10, (90, 92): 30},
    "topo_multilan_shared_wg_transit": {(140, 141): 10, (140, 142): 25, (140, 143): 5, (141, 142): 10,
                                        (141, 143): 30, (142, 143): 5},
    "topo_dual_wg_transit_bridge": {(150, 151): 10, (150, 152): 10, (151, 152): 30, (153, 154): 15,
                                    (153, 152): 10, (154, 152): 5},
}
ALL = ["topo_pair", "topo_chain_3", "topo_chain_5", "topo_star", "topo_ring_4", "topo_snowflake",
       "topo_lan_only_chain", "topo_pond_full_mesh", "topo_pond_with_workers", "topo_pond_hub_spoke", "topo_mixed",
       "topo_asym_lan2_wg_lan3", "topo_asym_chain_wg_star", "topo_asym_ring_wg_lan", "topo_asym_three_sites",
       "topo_multilan_shared_wg_transit", "topo_dual_wg_transit_bridge", "topo_solo_wlan0_ap",
       "topo_two_aps_ham_radio", "topo_mixed_iface_no_router"]


def sh(*argv, input=None):
    r = subprocess.run(list(argv), capture_output=True, text=True, input=input)
    return r.returncode, r.stdout, r.stderr


def site(node):
    return int(next(iter(node.ifaces.values())).ip.split(".")[1])


def one_way(bname, topo, na, nb):
    sa, sb = site(topo.nodes[na]), site(topo.nodes[nb])
    if sa == sb: return INTRA_MS
    table = WAN.get("pond" if bname.startswith("topo_pond") else bname, {})
    return float(table.get((sa, sb), table.get((sb, sa), WAN_MS)))


def ping(ns, target, src=None, dev=None, n=5, interval=0.2):
    argv = ["ip", "netns", "exec", ns, "ping", "-n", "-q", "-c", str(n), "-i", str(interval), "-W", "2"]
    if src: argv += ["-I", src]
    elif dev: argv += ["-I", dev]
    rc, out, err = sh(*argv, target)
    m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/", out)
    rx = re.search(r"(\d+) received", out)
    return {"rx": int(rx.group(1)) if rx else 0, "n": n,
            "min": float(m.group(1)) if m else None, "avg": float(m.group(2)) if m else None,
            "max": float(m.group(3)) if m else None, "err": (err.strip()[-200:] if rc and not m else "")}


class Bed:
    """One topology on the real kernel with delayed bearers."""
    def __init__(self, bname, out):
        self.bname, self.out = bname, out
        self.topo = getattr(H, bname)(); H.assign_identities(self.topo)
        self.prov = NetnsProvisioner(self.topo, runner=ShellRunner())
        self.prov.endpoint = lambda node, iface, peer_port: f"127.0.0.1:{peer_port + OFF}"
        self.relay = None
        self.delay = {}      # (node, dev, peer) -> one-way ms on that edge (0 on ethernet)

    def ns(self, n): return self.prov._ns(n)

    def up(self):
        t = self.topo
        self.prov.teardown(); self.prov.provision(); self.prov.write_wrappers()
        linked = {(n, i) for seg in t.segments for (n, i) in seg}
        for name, node in t.nodes.items():                      # [SERVED_LAN_DUMMY_V1]
            for iname, iface in node.ifaces.items():
                if (name, iname) not in linked:
                    sh("ip", "netns", "exec", self.ns(name), "ip", "link", "add", iname, "type", "veth", "peer",
                       "name", f"{iname[:9]}-lan")
                    sh("ip", "netns", "exec", self.ns(name), "ip", "link", "set", f"{iname[:9]}-lan", "up")
                    sh("ip", "netns", "exec", self.ns(name), "ip", "addr", "add", iface.addr_cidr, "dev", iname)
                    sh("ip", "netns", "exec", self.ns(name), "ip", "link", "set", iname, "up")
            for k, v in (("net.ipv4.ip_forward", "1"), ("net.ipv4.conf.all.rp_filter", "0"),
                         ("net.ipv4.conf.default.rp_filter", "0")):
                sh("ip", "netns", "exec", self.ns(name), "sysctl", "-qw", f"{k}={v}")
        links = []
        for (na, ia, pa, nb, ib, pb) in self.prov.wg_links:
            d = one_way(self.bname, t, na, nb)
            links.append([pa, pb, d, d]); self.delay[(na, ia, nb)] = d; self.delay[(nb, ib, na)] = d
        for seg in t.segments:
            for (n, i) in seg:
                for (pn, _pi, _pip) in t.l2_peers(n, i):
                    self.delay.setdefault((n, i, pn), 0.0)
        cfg = os.path.join(self.out, f"{self.bname}.relay.json")
        json.dump({"offset": OFF, "links": links}, open(cfg, "w"))
        if links:
            self.relay = subprocess.Popen([sys.executable, "-I", os.path.join(_HERE, "wg_bearer_relay.py"), cfg],
                                          stdout=open(os.path.join(self.out, f"{self.bname}.relay.log"), "w"),
                                          stderr=subprocess.STDOUT)

    def measure_edges(self):
        """Ping every L2 peer over each device until it answers (a tunnel needs its first handshake)."""
        t = self.topo; rtt = {}
        jobs = [(n, i, pn, pip) for n in t.nodes for i in t.nodes[n].ifaces for (pn, _pi, pip) in t.l2_peers(n, i)]
        def one(j):
            n, i, pn, pip = j
            for _ in range(15):
                r = ping(self.ns(n), pip, dev=i, n=5, interval=0.2)
                if r["rx"] >= 3:          # answering: measure again, so no handshake wait is in the number
                    r = ping(self.ns(n), pip, dev=i, n=5, interval=0.2)
                    if r["rx"] >= 3: return j, r["min"]
                time.sleep(1)
            return j, None
        with ThreadPoolExecutor(32) as ex:
            for (n, i, pn, pip), v in ex.map(one, jobs): rtt[(n, i, pn)] = v
        self.rtt = rtt
        return rtt

    def patch_probes(self):
        """The model's probes report the measured edge RTTs (everything downstream is the real planner/committer)."""
        rtt = self.rtt; topo = self.topo
        def edge(cur, dev, peer): return rtt.get((cur, dev, peer))
        def probe_direct(topo_, src, peer, dev):
            for (pn, _pi, _pip) in topo_.l2_peers(src.name, dev):
                if pn == peer.name: return edge(src.name, dev, pn)
            return None
        orig = H._trace_rtt_via
        def trace(topo_, start_node, dst_ip, max_hops=16):
            # the model's own walk, with edge_rtt replaced per hop by the measured (node, dev, next node) RTT
            current = start_node; total = 0.0
            for _ in range(max_hops):
                if current.frognet_ip == dst_ip or current.frognet_admin_ip == dst_ip: return total
                for iface in current.ifaces.values():
                    if ipaddress.ip_address(dst_ip) not in ipaddress.ip_network(iface.subnet): continue
                    if iface.ip == dst_ip: return total
                    peer_served = None
                    for (pn, _pi, pip) in topo_.l2_peers(current.name, iface.name):
                        peer = topo_.nodes[pn]; peer_served = next(iter(peer.ifaces), None)
                        if pip == dst_ip or (peer.frognet_admin_ip == dst_ip and _pi == peer_served) \
                                or peer.frognet_ip == dst_ip:
                            e = edge(current.name, iface.name, pn)
                            return None if e is None else total + e
                    return None
                r = current.find_route(dst_ip)
                if r is None or r.via is None: return None
                if current.ifaces.get(r.dev) is None: return None
                peers = topo_.l2_peers(current.name, r.dev); nxt = None
                if H.iface_kind(r.dev) == "wg":
                    if len(peers) == 1: nxt = topo_.nodes[peers[0][0]]
                else:
                    for (pn, _pi, pip) in peers:
                        if pip == r.via or topo_.nodes[pn].frognet_ip == r.via: nxt = topo_.nodes[pn]; break
                if nxt is None or nxt is current: return None
                e = edge(current.name, r.dev, nxt.name)
                if e is None: return None
                total += e; current = nxt
            return None
        H._probe_direct = probe_direct; H._trace_rtt_via = trace
        return orig

    def converge(self):
        self.cycles = LE.converge_real(self.topo, ipr_factory=make_ipr_factory("real", self.prov))
        return self.cycles

    def write_hosts(self):
        for name, node in self.topo.nodes.items():
            d = f"/etc/netns/{self.ns(name)}"; os.makedirs(d, exist_ok=True)
            lines = ["127.0.0.1 localhost"] + [f"{ip} {nm}" for ip, nm in sorted(node.known_hosts.items())]
            open(f"{d}/hosts", "w").write("\n".join(lines) + "\n")

    def walk(self, src, dst_ip, max_hops=16):
        """The path the kernel takes from src's .1 to dst_ip: [(node, dev, next node), ...], or None."""
        t = self.topo; cur = src; path = []; from_ip = t.nodes[src].frognet_ip
        for _ in range(max_hops):
            node = t.nodes[cur]
            if any(i.ip == dst_ip for i in node.ifaces.values()): return path
            rc, out, _ = sh("ip", "netns", "exec", self.ns(cur), "ip", "-o", "route", "get", dst_ip,
                            *(["from", from_ip] if cur == src else []))
            if rc: return None
            dev = re.search(r" dev (\S+)", out); via = re.search(r" via (\S+)", out)
            if not dev: return None
            dev = dev.group(1); via = via.group(1) if via else dst_ip
            peers = t.l2_peers(cur, dev); nxt = None
            if dev.startswith("wg"):
                if len(peers) == 1: nxt = peers[0][0]
            else:
                for (pn, _pi, pip) in peers:
                    pnode = t.nodes[pn]
                    if pip == via or pnode.frognet_ip == via or any(i.ip == via for i in pnode.ifaces.values()):
                        nxt = pn; break
            if nxt is None: return None
            path.append((cur, dev, nxt)); cur = nxt
        return None

    def best(self, src, dst):
        """Lowest one-way delay over the graph, src node to dst node (ethernet edges 0, ties by fewer hops)."""
        dist = {src: (0.0, 0)}; pq = [(0.0, 0, src)]
        while pq:
            d, h, u = heapq.heappop(pq)
            if (d, h) > dist.get(u, (1e18, 0)): continue
            if u == dst: return d, h
            for i in self.topo.nodes[u].ifaces:
                for (pn, _pi, _pip) in self.topo.l2_peers(u, i):
                    nd = (d + self.delay[(u, i, pn)], h + 1)
                    if nd < dist.get(pn, (1e18, 0)): dist[pn] = nd; heapq.heappush(pq, (nd[0], nd[1], pn))
        return None

    def pairs(self):
        t = self.topo; rows = []
        jobs = [(a, b) for a in t.nodes for b in t.nodes if a != b and t.nodes[a].frognet_ip and t.nodes[b].frognet_ip]
        def one(j):
            a, b = j; A, B = t.nodes[a], t.nodes[b]
            rc, out, _ = sh("ip", "netns", "exec", self.ns(a), "getent", "hosts", b)
            resolved = out.split()[0] if rc == 0 and out.split() else None
            target = resolved or B.frognet_ip
            r = ping(self.ns(a), b if resolved else B.frognet_ip, src=A.frognet_ip, n=10, interval=0.2)
            path = self.walk(a, B.frognet_ip); back = self.walk(b, A.frognet_ip)   # routing may differ each way
            fwd = None if path is None else sum(self.delay.get(e, 0.0) for e in path)
            ret = None if back is None else sum(self.delay.get(e, 0.0) for e in back)
            floor = None if fwd is None or ret is None else (fwd + ret) / 2      # one-way average; floor_rtt = fwd+ret
            bst = self.best(a, b)
            return {"topo": self.bname, "src": a, "dst": b, "dst_ip": B.frognet_ip, "by_name": resolved == B.frognet_ip,
                    "resolved": resolved, "rx": r["rx"], "n": r["n"], "rtt_min": r["min"], "rtt_avg": r["avg"],
                    "rtt_max": r["max"], "err": r["err"],
                    "path": None if path is None else [f"{x}/{d}" for (x, d, _y) in path] + [b],
                    "back": None if back is None else [f"{x}/{d}" for (x, d, _y) in back] + [a],
                    "fwd_ms": fwd, "ret_ms": ret, "symmetric": (path is not None and back is not None and
                        [x for (x, _d, _y) in path] + [b] == list(reversed([x for (x, _d, _y) in back] + [a]))),
                    "hops": None if path is None else len(path), "floor_rtt": None if floor is None else 2 * floor,
                    "best_rtt": None if bst is None else 2 * bst[0], "best_hops": None if bst is None else bst[1],
                    "best": (fwd is not None and bst is not None and abs(fwd - bst[0]) < 1e-9)}   # forward path vs lowest
        with ThreadPoolExecutor(16) as ex: rows = list(ex.map(one, jobs))
        return rows

    def down(self):
        if self.relay: self.relay.terminate(); self.relay.wait()
        self.prov.teardown()
        for name in self.topo.nodes:
            sh("rm", "-rf", f"/etc/netns/{self.ns(name)}")


def q(v, p):
    v = sorted(x for x in v if x is not None)
    return v[min(len(v) - 1, int(p * len(v)))] if v else float("nan")


def main(argv):
    out = os.environ.get("OUT", os.path.abspath("tier4-transport-out")); os.makedirs(out, exist_ok=True)
    names = argv or ALL
    summ = open(os.path.join(out, "summary.txt"), "a")
    def say(s): print(s, flush=True); summ.write(s + "\n"); summ.flush()
    say(f"=== TIER 4 + bearer delay + real packets: WAN_MS={WAN_MS} INTRA_MS={INTRA_MS} "
        f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===")
    allok = True
    for bname in names:
        bed = Bed(bname, out); orig = (H._probe_direct, H._trace_rtt_via)
        try:
            bed.up(); rtt = bed.measure_edges()
            dead = [k for k, v in rtt.items() if v is None]
            json.dump({f"{k[0]}/{k[1]}->{k[2]}": v for k, v in rtt.items()}, open(os.path.join(out, f"{bname}.edges.json"), "w"), indent=0)
            if dead:
                say(f"[FAIL] {bname}: {len(dead)} edges never answered: {dead[:4]}"); allok = False; continue
            bed.patch_probes(); cyc = bed.converge(); bed.write_hosts()
            ok, total, fails = H.verify_reachability(bed.topo)
            rows = bed.pairs()
            with open(os.path.join(out, f"{bname}.jsonl"), "w") as f:
                for r in rows: f.write(json.dumps(r) + "\n")
            n = len(rows); got = sum(r["rx"] == r["n"] for r in rows); some = sum(r["rx"] > 0 for r in rows)
            named = sum(r["by_name"] for r in rows); best = sum(r["best"] for r in rows)
            over = [r["rtt_min"] - r["floor_rtt"] for r in rows if r["rtt_min"] is not None and r["floor_rtt"] is not None]
            worse = [r for r in rows if r["fwd_ms"] is not None and r["best_rtt"] is not None and not r["best"]]
            asym = sum(1 for r in rows if not r["symmetric"])
            wan = sorted({d for d in bed.delay.values() if d > 0})
            say(f"{'[PASS]' if got == n and named == n else '[FAIL]'} {bed.topo.label} ({bname}): {cyc} cycles; "
                f"model trace {ok}/{total}; packets {got}/{n} pairs all 10 answered ({some} any); by name {named}/{n}; "
                f"lowest-delay route {best}/{n}; different path back {asym}/{n}; RTT min p50 {q([r['rtt_min'] for r in rows], .5):.2f} "
                f"max {q([r['rtt_min'] for r in rows], 1):.2f} ms; over floor p50 {q(over, .5):.2f} max {q(over, 1):.2f} ms; "
                f"tunnel one-way ms {wan or '-'}")
            for r in worse[:6]:
                say(f"        not lowest-delay: {r['src']}->{r['dst']} took {' '.join(r['path'])} one-way {r['fwd_ms']:.0f} "
                    f"vs lowest {r['best_rtt'] / 2:.0f} ms ({r['best_hops']} hops); return {' '.join(r['back'] or [])} "
                    f"{r['ret_ms']} ms; measured RTT {r['rtt_min']}")
            for r in [r for r in rows if r["rx"] < r["n"]][:6]:
                say(f"        lost: {r['src']}->{r['dst']} {r['rx']}/{r['n']} path {r['path']} {r['err']}")
            allok &= (got == n and named == n)
        except Exception as e:
            allok = False; say(f"[FAIL] {bname}: {type(e).__name__}: {e}")
        finally:
            H._probe_direct, H._trace_rtt_via = orig
            bed.down()
    say("ALL PASS" if allok else "FAILURES")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
