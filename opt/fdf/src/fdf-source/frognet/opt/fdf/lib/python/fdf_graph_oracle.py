#!/usr/bin/env python3
"""[GRAPH_ORACLE_V1] PLAN-RAM-DISCOVERY §6: the graph's answers for the agreed topologies, exact. No kernel.
Rows carry their source (kind): own / held (my lease, my mac) / given (my dnsmasq lease, client mac) / uplink / hub / leaf.
Run: python3 /opt/fdf/lib/python/fdf_graph_oracle.py"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdf_graph import Graph

FAIL = 0


def check(name, got, want):
    global FAIL
    if got == want:
        print("ok   ", name)
    else:
        FAIL += 1
        print("FAIL ", name, "\n   got ", got, "\n   want", want)


def own(iface, ip, name):            return {"iface": iface, "ip": ip, "value": name, "kind": "own"}
def held(iface, ip, mac):            return {"iface": iface, "ip": ip, "value": "", "kind": "held", "mac": mac}
def given(iface, ip, mac, name):     return {"iface": iface, "ip": ip, "value": name, "kind": "given", "mac": mac}
def uplink(iface, ip, typ, rate, dead=False):
    return {"iface": iface, "ip": ip, "value": "uplink %s %s%s" % (typ, rate, " dead" if dead else ""), "kind": "uplink"}
def hub(iface, ip, name="StreamingFrog"): return {"iface": iface, "ip": ip, "value": name, "kind": "hub"}
def leaf(ip, name):                  return {"iface": "wg", "ip": ip, "value": name, "kind": "leaf"}


def host(name, rows, role="node", alive=True):
    return {"name": name, "alive": alive, "facts": {"role": role, "addrs": rows}}


HUB = {"name": "StreamingFrog", "alive": True, "facts": {"role": "hub", "pubkey": "H", "endpoint": "1.2.3.4:50889"}}
MY, MX, MB, MC, MQ, MK = "aa:1", "aa:2", "aa:3", "aa:4", "aa:5", "aa:6"

# ---------------------------------------------------------------- 1. x -> y -> z -> RAM -> a -> b -> c
T1 = [HUB,
      host("z", [uplink("eth0", "192.168.0.33", "eth", 1000), own("wlan0", "10.3.3.1", "FrogNetHost.z"), hub("wg0", "10.253.3.3"),
                 given("wlan0", "10.3.3.20", MY, "y")]),
      host("y", [held("wlan1", "10.3.3.20", MY), own("wlan0", "10.2.2.1", "FrogNetHost.y"), given("wlan0", "10.2.2.30", MX, "x")]),
      host("x", [held("wlan1", "10.2.2.30", MX), own("wlan0", "10.1.1.1", "FrogNetHost.x")]),
      host("a", [uplink("eth0", "192.168.9.9", "eth", 1000), own("wlan0", "10.7.7.1", "FrogNetHost.a"), hub("wg0", "10.253.7.7"),
                 given("wlan0", "10.7.7.40", MB, "b")]),
      host("b", [held("wlan1", "10.7.7.40", MB), own("wlan0", "10.8.8.1", "FrogNetHost.b"), given("wlan0", "10.8.8.50", MC, "c")]),
      host("c", [held("wlan1", "10.8.8.50", MC), own("wlan0", "10.9.9.1", "FrogNetHost.c")])]
if __name__ == "__main__":
  g = Graph(T1)
  check("1 links", sorted(g.links), sorted([("y", "z", "10.3.3.20", "10.3.3.1"), ("x", "y", "10.2.2.30", "10.2.2.1"),
                                            ("b", "a", "10.7.7.40", "10.7.7.1"), ("c", "b", "10.8.8.50", "10.8.8.1")]))
  px = g.plan("x")
  check("1 x exit is z", px["exit"], "z")
  check("1 x default via y's .1 on wlan1", px["default"], ("10.2.2.1", "wlan1"))
  check("1 x routes: z's /24 via 10.2.2.1 (y's /24 is x's own wire), and every remote /24 via the same next hop toward its exit",
        px["routes"], [("10.3.3.0/24", "10.2.2.1", "wlan1"), ("10.7.7.0/24", "10.2.2.1", "wlan1"), ("10.8.8.0/24", "10.2.2.1", "wlan1"), ("10.9.9.0/24", "10.2.2.1", "wlan1")])
  check("1 x registers nothing (no hub link)", px["maps"], [])
  check("1 x has no hub routes", px["hub_routes"], [])
  pz = g.plan("z")
  check("1 z is its own exit", pz["exit"], "z")
  check("1 z default none", pz["default"], None)
  check("1 z routes: y via its lease, x via y's lease", pz["routes"], [("10.2.2.0/24", "10.3.3.20", "wlan0"), ("10.1.1.0/24", "10.3.3.20", "wlan0")])
  check("1 z registers x,y,z", pz["maps"], ["10.1.1.0/24", "10.2.2.0/24", "10.3.3.0/24"])
  check("1 z hub routes: a,b,c", pz["hub_routes"], ["10.7.7.0/24", "10.8.8.0/24", "10.9.9.0/24"])
  check("1 z hub iface", pz["hub_iface"], "wg0")
  pa = g.plan("a")
  check("1 a registers a,b,c", pa["maps"], ["10.7.7.0/24", "10.8.8.0/24", "10.9.9.0/24"])
  check("1 a hub routes: x,y,z", pa["hub_routes"], ["10.1.1.0/24", "10.2.2.0/24", "10.3.3.0/24"])
  check("1 hosts lines everywhere", pa["hosts"], [("10.1.1.1", "FrogNetHost.x"), ("10.2.2.1", "FrogNetHost.y"), ("10.3.3.1", "FrogNetHost.z"),
                                                    ("10.7.7.1", "FrogNetHost.a"), ("10.8.8.1", "FrogNetHost.b"), ("10.9.9.1", "FrogNetHost.c")])
  pc = g.plan("c")
  check("1 c default via b", pc["default"], ("10.8.8.1", "wlan1"))
  check("1 c routes a and the far site via b", pc["routes"], [("10.7.7.0/24", "10.8.8.1", "wlan1"), ("10.1.1.0/24", "10.8.8.1", "wlan1"), ("10.2.2.0/24", "10.8.8.1", "wlan1"), ("10.3.3.0/24", "10.8.8.1", "wlan1")])
  
  # ---------------------------------------------------------------- 1b. adjacency is a lease pair: same string, wrong mac -> no link
  T1b = [h if h["name"] != "z" else host("z", [uplink("eth0", "192.168.0.33", "eth", 1000), own("wlan0", "10.3.3.1", "FrogNetHost.z"),
                                               hub("wg0", "10.253.3.3"), given("wlan0", "10.3.3.20", "de:ad", "someone-else")]) for h in T1]
  g1b = Graph(T1b)
  check("1b stale/other lease with the same ip is not a link", [l for l in g1b.links if l[0] == "y"], [])
  check("1b y then has no exit, no default", (g1b.plan("y")["exit"], g1b.plan("y")["default"]), (None, None))
  check("1b z no longer registers y or x", g1b.plan("z")["maps"], ["10.3.3.0/24"])
  # a held lease nobody gave (parent outside the region) -> no link, no guess
  T1c = [h for h in T1 if h["name"] != "z"]
  check("1c parent absent -> y is a root with no exit", Graph(T1c).plan("y")["exit"], None)
  
  # ---------------------------------------------------------------- 2. two uplinks on one LAN: eth 1000 vs wifi 72
  # p (wifi uplink) serves 10.5.5.0/24; q (eth uplink) is a guest on it; child k is a guest on it too.
  T2 = [HUB,
        host("p", [uplink("wlan1", "192.168.1.5", "wifi", 72), own("wlan0", "10.5.5.1", "FrogNetHost.p"), hub("wg0", "10.253.5.5"),
                   given("wlan0", "10.5.5.60", MK, "k"), given("wlan0", "10.5.5.70", MQ, "q")]),
        host("q", [uplink("eth0", "192.168.1.6", "eth", 1000), held("wlan1", "10.5.5.70", MQ), own("wlan0", "10.6.6.1", "FrogNetHost.q"),
                   hub("wg0", "10.253.6.6")]),
        host("k", [held("wlan1", "10.5.5.60", MK), own("wlan0", "10.4.4.1", "FrogNetHost.k")])]
  g2 = Graph(T2)
  pk = g2.plan("k")
  check("2 k picks q (eth 1000) over p (wifi 72): speed first, not hops", pk["exit"], "q")
  check("2 k default toward q goes through the LAN's parent p (guests never route guest-to-guest)", pk["default"], ("10.5.5.1", "wlan1"))
  check("2 p forwards k's traffic to q via q's lease", g2.plan("p")["routes"], [("10.4.4.0/24", "10.5.5.60", "wlan0"), ("10.6.6.0/24", "10.5.5.70", "wlan0")])
  check("2 q registers q and k, not p", g2.plan("q")["maps"], ["10.4.4.0/24", "10.6.6.0/24"])
  check("2 p registers only itself", g2.plan("p")["maps"], ["10.5.5.0/24"])
  T2b = [h if h["name"] != "p" else host("p", [uplink("wlan1", "192.168.1.5", "eth", 1000), own("wlan0", "10.5.5.1", "FrogNetHost.p"),
                                              hub("wg0", "10.253.5.5"), given("wlan0", "10.5.5.60", MK, "k"), given("wlan0", "10.5.5.70", MQ, "q")]) for h in T2]
  check("2b equal rates -> lower .1 (p, 10.5.5.1 < 10.6.6.1)", Graph(T2b).plan("k")["exit"], "p")
  T2c = [h if h["name"] != "q" else host("q", [uplink("eth0", "192.168.1.6", "eth", 1000, dead=True), held("wlan1", "10.5.5.70", MQ),
                                              own("wlan0", "10.6.6.1", "FrogNetHost.q"), hub("wg0", "10.253.6.6")]) for h in T2]
  check("2c dead uplink ignored -> p", Graph(T2c).plan("k")["exit"], "p")
  
  # ---------------------------------------------------------------- 2d. forest: a host with two held leases keeps one upward link
  T2d = T1 + [host("w", [held("wlan1", "10.3.3.21", "aa:9"), held("wlan2", "10.7.7.41", "aa:9"), own("wlan0", "10.12.12.1", "FrogNetHost.w")])]
  T2d = [h if h["name"] not in ("z", "a") else
         (host("z", T1[1]["facts"]["addrs"] + [given("wlan0", "10.3.3.21", "aa:9", "w")]) if h["name"] == "z"
          else host("a", T1[4]["facts"]["addrs"] + [given("wlan0", "10.7.7.41", "aa:9", "w")])) for h in T2d]
  g2d = Graph(T2d)
  check("2d both candidate links recorded", sorted(l[1] for l in g2d.links if l[0] == "w"), ["a", "z"])
  check("2d exactly one parent chosen (equal rates -> lower .1 = z)", g2d.parent["w"][0], "z")
  check("2d only z registers w", (g2d.plan("z")["maps"].count("10.12.12.0/24"), g2d.plan("a")["maps"].count("10.12.12.0/24")), (1, 0))
  
  # ---------------------------------------------------------------- 3. duplicate .1: two live hosts claim 10.3.3.1
  T3 = T1 + [host("z2", [uplink("eth0", "192.168.0.99", "eth", 1000), own("wlan0", "10.3.3.1", "FrogNetHost.z2"), hub("wg0", "10.253.3.4")])]
  g3 = Graph(T3)
  check("3 conflict named with both hosts", g3.conflicts, [("10.3.3.1", ["z", "z2"])])
  check("3 nobody routes the conflicted /24 (a's hub routes lack it)", g3.plan("a")["hub_routes"], ["10.1.1.0/24", "10.2.2.0/24"])
  check("3 the conflicted .1 has no hosts line", [n for ip, n in g3.plan("a")["hosts"] if ip == "10.3.3.1"], [])
  
  # ---------------------------------------------------------------- 6. a leaf (/32) appears
  T6 = T1 + [host("Laptop", [leaf("10.252.0.7", "Laptop")], role="leaf")]
  g6 = Graph(T6)
  check("6 leaf hosts line", [n for ip, n in g6.plan("z")["hosts"] if ip == "10.252.0.7"], ["Laptop"])
  check("6 leaf /32 is a hub route for hub-linked hosts", "10.252.0.7/32" in g6.plan("z")["hub_routes"], True)
  check("6 leaf changes nothing else for z", g6.plan("z")["routes"], pz["routes"])
  check("6 x (no hub link) gets no leaf route", g6.plan("x")["hub_routes"], [])
  
  # ---------------------------------------------------------------- 7. today's hardware: Seattle7 is a guest of Seattle5 (not in the region)
  T7 = [HUB, host("Seattle7", [own("wlan0", "10.170.170.1", "FrogNetHost.Seattle7"), held("wlan1", "10.250.250.63", "b8:27"),
                               hub("wg0", "10.253.170.170"), uplink("wlan1", "10.250.250.63", "wifi", 72)]),
        host("Seattle3B", [own("wlan0", "10.251.251.1", "FrogNetHost.Seattle3B"), uplink("wlan1", "192.168.0.33", "wifi", 72), hub("wg0", "10.253.251.251")])]
  g7 = Graph(T7)
  check("7 no links (parent absent)", g7.links, [])
  check("7 Seattle7 routes 10.251.251.0/24 through the hub", g7.plan("Seattle7")["hub_routes"], ["10.251.251.0/24"])
  check("7 Seattle7 registers itself", g7.plan("Seattle7")["maps"], ["10.170.170.0/24"])
  check("7 hosts", g7.plan("Seattle7")["hosts"], [("10.170.170.1", "FrogNetHost.Seattle7"), ("10.251.251.1", "FrogNetHost.Seattle3B")])
  
  # 7b. Seattle5 joins the region: Seattle7's "uplink" is a lease Seattle5 gave -> Seattle5 is the parent and the exit
  T7b = T7 + [host("Seattle5", [own("wlan0", "10.250.250.1", "FrogNetHost.Seattle5"), uplink("eth0", "192.168.0.21", "eth", 1000),
                                hub("wg0", "10.253.250.250"), given("wlan0", "10.250.250.63", "b8:27", "Seattle7")])]
  g7b = Graph(T7b)
  check("7b Seattle7 is now Seattle5's child", g7b.parent.get("Seattle7", ("",))[0], "Seattle5")
  check("7b Seattle7's exit is Seattle5, default via 10.250.250.1", (g7b.plan("Seattle7")["exit"], g7b.plan("Seattle7")["default"]), ("Seattle5", ("10.250.250.1", "wlan1")))
  # [OWN_TUNNEL_CARRIES_OWN_V1] Seattle7 holds a hub link: it carries its own /24 on it and reaches remote /24s on it
  check("7b Seattle7 has no LAN routes to remote /24s (they are hub routes on its own link)", g7b.plan("Seattle7")["routes"], [])
  check("7b Seattle5 registers only its own (Seattle7 has a tunnel)", g7b.plan("Seattle5")["maps"], ["10.250.250.0/24"])
  check("7b Seattle7 registers its own /24", g7b.plan("Seattle7")["maps"], ["10.170.170.0/24"])
  check("7b Seattle7's own hub link is active: remote /24 is a hub route", (g7b.plan("Seattle7")["hub_routes"], g7b.plan("Seattle7")["hub_active"]), (["10.251.251.0/24"], True))
  # 7b' the same Seattle7 WITHOUT a tunnel: a tunnel-less child, Seattle5 carries it (the netns y-behind-z case)
  T7bn = [dict(h, facts=dict(h["facts"], addrs=[r for r in h["facts"]["addrs"] if r["kind"] != "hub"])) if h["name"] == "Seattle7" else h for h in T7b]
  g7bn = Graph(T7bn)
  check("7b' tunnel-less Seattle7 routes Seattle3B's /24 via Seattle5 explicitly", g7bn.plan("Seattle7")["routes"], [("10.251.251.0/24", "10.250.250.1", "wlan1")])
  check("7b' Seattle5 registers both", g7bn.plan("Seattle5")["maps"], ["10.170.170.0/24", "10.250.250.0/24"])
  check("7b' tunnel-less Seattle7 registers nothing, no hub routes", (g7bn.plan("Seattle7")["maps"], g7bn.plan("Seattle7")["hub_routes"], g7bn.plan("Seattle7")["hub_active"]), ([], [], False))
  # 7c Seattle5 is the exit but holds no hub link: Seattle7's own link is the active one (same answer under OWN_TUNNEL_CARRIES_OWN_V1)
  T7c = T7 + [host("Seattle5", [own("wlan0", "10.250.250.1", "FrogNetHost.Seattle5"), uplink("eth0", "192.168.0.21", "eth", 1000),
                                given("wlan0", "10.250.250.63", "b8:27", "Seattle7")])]
  g7c = Graph(T7c)
  check("7c exit still Seattle5 (LAN default)", (g7c.plan("Seattle7")["exit"], g7c.plan("Seattle7")["default"]), ("Seattle5", ("10.250.250.1", "wlan1")))
  check("7c Seattle7's own hub link is active: remote /24 is a hub route, not via Seattle5", (g7c.plan("Seattle7")["hub_routes"], g7c.plan("Seattle7")["hub_active"], g7c.plan("Seattle7")["routes"]), (["10.251.251.0/24"], True, []))
  check("7c Seattle7 registers its own /24", g7c.plan("Seattle7")["maps"], ["10.170.170.0/24"])
  
  # 9 [SUBNET_FALLBACK_ROW_V1] a member on an older build: no rows, just facts.subnet -> named and routed, nothing else
  T9 = T1 + [{"name": "Old", "alive": True, "facts": {"role": "node", "subnet": "10.44.44.0/24", "pubkey": "o"}}]
  g9 = Graph(T9)
  check("9 old member named from its subnet", [n for ip, n in g9.plan("z")["hosts"] if ip == "10.44.44.1"], ["FrogNetHost.Old"])
  check("9 old member's /24 is a hub route for exits", "10.44.44.0/24" in g9.plan("z")["hub_routes"], True)
  check("9 old member has no links, no exit", (g9.parent.get("Old"), g9.plan("Old")["exit"]), (None, None))

  # 10 [LEAF_ROW_FROM_ADDR_V1] a leaf with NO rows, only facts.addr (the phone; a laptop running only fdf-client): every
  #    hub-linked node routes its /32 on the hub link, a tunnel-less child routes it via its exit, all name it.
  #    (r19 nested this under the subnet test: no node routed the phone; Seattle5 sent 10.252.0.1 out wlan1.)
  T10 = T1 + [{"name": "phone", "alive": True, "facts": {"role": "leaf", "name": "phone", "pubkey": "p", "addr": "10.252.0.1/32"}}]
  g10 = Graph(T10)
  check("10 z (hub link) routes the leaf's /32 on it", "10.252.0.1/32" in g10.plan("z")["hub_routes"], True)
  check("10 y (child of z, no tunnel) routes it via z", [r for r in g10.plan("y")["routes"] if r[0] == "10.252.0.1/32"], [("10.252.0.1/32", "10.3.3.1", "wlan1")])
  check("10 the leaf is named", [n for ip, n in g10.plan("z")["hosts"] if ip == "10.252.0.1"], ["phone"])
  check("10c control: a leaf on a member's LAN (no addr) gets no /32", [r for r in Graph(T1 + [{"name": "p2", "alive": True, "facts": {"role": "leaf", "name": "p2", "lan": {"ip": "10.3.3.77"}}}]).plan("z")["hub_routes"] if r.startswith("10.252")], [])

  # 11 [REACH_IS_A_LEAF_HERE_V1] a participant REACHING this network (reach:true, the /32 the region gave it) is routed
  #    as a leaf is; a participant that only reads is routed and named by nobody
  T11 = T1 + [{"name": "n", "alive": True, "facts": {"role": "participant", "name": "n", "pubkey": "p", "reach": True, "addr": "10.252.0.3/32"}}]
  check("11 z routes a reacher's /32 on its hub link", "10.252.0.3/32" in Graph(T11).plan("z")["hub_routes"], True)
  T11c = T1 + [{"name": "n", "alive": True, "facts": {"role": "participant", "name": "n", "guid": "g"}}]
  check("11c control: a reading participant gets no route", [r for r in Graph(T11c).plan("z")["hub_routes"] if r.startswith("10.252")], [])

  print("\n%s" % ("ALL OK" if not FAIL else "%d FAILED" % FAIL))
  sys.exit(1 if FAIL else 0)
