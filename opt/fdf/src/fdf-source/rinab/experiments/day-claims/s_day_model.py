#!/usr/bin/env python3
"""s_day_model.py PB_DIR [S] [E ...] -- [S_DAY_MODEL_V1] experiment S, Day's arm: his method's discovery on the SAME
graphs as tools/netns-scale-test.sh (S subnets, E egress points, a child per remaining subnet on an egress node's LAN),
from rlite's link-state routing as written (rlite user/uipcps, commit as cloned):
  - every N-1 adjacency is two LowerFlow objects (one per direction), each originated by its local node;
  - a new or newer object is forwarded to every neighbour except the one it came from (lower-flows.cpp:499-515);
  - every member's name is a DFTEntry and its address an AddrAllocRequest, replicated to all the same way
    (DFT "fully-replicated", AddrAllocator "distributed");
  - an enrolling member receives the whole RIB from its neighbour (enroll.cpp:1100-1127), at most 10 objects per CDAP
    message ("limit = 10; /* Hardwired */");
  - every 30 s (kRIBRefreshIntvalSecs) every member re-sends its own objects to every neighbour.
Sizes are real encodings: PB_DIR holds BaseRIB_pb2/CDAP_pb2 compiled from rlite's own .proto files; each CDAP message
is counted with the RINA management PCI and its shim (UDP/IPv4/Ethernet) as WIRE_HDR bytes, stated below.
Five physical shapes of the egress level ([S_TOPOLOGY_FAMILIES_V1]): chain, binary tree, star (every egress node's N-1
flow to a hub: the bed's paths), partial mesh (ring plus chords, degree 4), full mesh (every pair of egress nodes).
Writes one JSON line per (layout, E) to stdout."""
import json, math, sys, collections
sys.path.insert(0, sys.argv[1])
import BaseRIB_pb2 as R, CDAP_pb2 as C

WIRE_HDR = 20 + 8 + 20 + 14          # RINA EFCP/management PCI (~20), UDP 8, IPv4 20, Ethernet 14
LIMIT = 10                            # objects per CDAP message (enroll.cpp:1087, 1151)
REFRESH_S = 30                        # kRIBRefreshIntvalSecs
def name(n): return "%s.n.DIF" % n    # an IPCP name of the bed's node names' length

def cdap(obj_class, obj_name, value: bytes) -> int:
    m = C.CDAPMessage(); m.op_code = C.M_CREATE; m.obj_class = obj_class; m.obj_name = obj_name; m.invoke_id = 1
    m.obj_value.byteval = value
    return len(m.SerializeToString()) + WIRE_HDR

def lf_msg(pairs):
    l = R.LowerFlowList()
    for a, b in pairs:
        f = l.flows.add(); f.local_node = name(a); f.remote_node = name(b); f.cost = 1; f.seqnum = 1; f.state = True; f.age = 0
    return cdap("lfdb_entries", "/mgmt/routing/table", l.SerializeToString())

def dft_msg(nodes):
    l = R.DFTSlice()
    for n in nodes:
        e = l.entries.add(); e.appl_name.ap_name = "FrogNetHost." + n; e.ipcp_name = name(n); e.seqnum = 1
    return cdap("dft_entries", "/mgmt/dft/table", l.SerializeToString())

def aa_msg(nodes):
    l = R.AddrAllocEntries()
    for i, n in enumerate(nodes):
        e = l.entries.add(); e.requestor = name(n); e.address = 1000 + i
    return cdap("aa_entries", "/mgmt/addralloc/table", l.SerializeToString())

def graph(S, E, layout):
    """nodes and N-1 adjacencies, the bed's graph: hub (star only), egress e1..eE, child c<k> on egress ((k-1)%E)+1"""
    adj = collections.defaultdict(set)
    eg = ["e%d" % i for i in range(1, E + 1)]
    def link(a, b): adj[a].add(b); adj[b].add(a)
    # [S_TOPOLOGY_FAMILIES_V1] the egress level's physical shape; the children's LANs are the same in every family
    if layout == "star":                                     # every egress node to a hub (the bed's paths)
        for e in eg: link("hub", e)
    elif layout == "chain":                                  # e1 - e2 - ... - eE
        for a, b in zip(eg, eg[1:]): link(a, b)
    elif layout == "tree":                                   # binary tree: e_i's parent is e_(i//2)
        for i in range(2, E + 1): link("e%d" % i, "e%d" % (i // 2))
    elif layout == "partial":                                # a ring plus chords to i+2: degree 4 (fewer at small E)
        for i in range(E):
            for d in (1, 2):
                j = (i + d) % E
                if j != i: link(eg[i], eg[j])
    elif layout == "mesh":                                   # every pair of egress nodes
        for i, a in enumerate(eg):
            for b in eg[i + 1:]: link(a, b)
    else:
        raise SystemExit("unknown layout " + layout)
    for k in range(E + 1, S + 1):
        c, e = "c%d" % k, "e%d" % ((k - 1) % E + 1); adj[c].add(e); adj[e].add(c)
    for e in eg: adj[e]                                      # E=1 mesh: the lone egress node exists
    return adj

def bfs(adj, src):
    d = {src: 0}; q = collections.deque([src])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in d: d[v] = d[u] + 1; q.append(v)
    return d

def flood_transfers(adj):
    """one new object from any member: each member forwards it to every neighbour but the sender, once (duplicates are
    dropped by add() returning false): transfers = sum over members of deg, minus (members - 1) receptions that were
    the first copy's own arrival... counted exactly by simulation from a leaf child"""
    return sum(len(v) for v in adj.values()) - (len(adj) - 1)

def model(S, E, layout):
    adj = graph(S, E, layout); N = len(adj); L = sum(len(v) for v in adj.values()) // 2
    one_lf, one_dft, one_aa = lf_msg([("a", "b")]), dft_msg(["x"]), aa_msg(["x"])
    per_obj = (lf_msg([("a", "b")] * LIMIT) - WIRE_HDR) / LIMIT      # a full message's bytes per object
    src = "c%d" % S if S > E else "e%d" % E
    hops = bfs(adj, src); ecc = max(hops.values())
    diam = max(max(bfs(adj, u).values()) for u in adj)
    # one change: a new name (DFTEntry) at the last child, flooded
    tr = flood_transfers(adj)
    change = {"transfers": tr, "bytes": tr * one_dft, "members_processing": N, "max_hops": ecc,
              # crossings of the egress level (the WAN): star -- the source egress node to the hub once, the hub to the
              # E-1 others; mesh -- the source egress node to E-1, each of them on to the E-2 others but the sender
              "wan_transfers": sum(1 for u in adj if not u.startswith("c") for v in adj[u] if not v.startswith("c"))}  # egress-level links, both ways (an upper bound on its crossings)
    # cold start: members enroll in BFS order from e1 (star: from the hub); each gets the whole RIB its neighbour holds
    root = "hub" if layout == "star" else "e1"; order = sorted(bfs(adj, root).items(), key=lambda x: x[1])
    joined = set(); objs = 0; msgs = 0; bts = 0; lfdb = 0; names = 0
    for u, _ in order:
        nb = [v for v in adj[u] if v in joined]
        if nb:  # enrollment sync from one neighbour: neighbours list + LFDB + DFT + AA, LIMIT per message
            for cnt, enc in ((lfdb, lambda k: lf_msg([("a", "b")] * k)), (names, lambda k: dft_msg(["x"] * k)), (names, lambda k: aa_msg(["x"] * k))):
                full, rest = divmod(cnt, LIMIT); msgs += full + (1 if rest else 0); objs += cnt
                bts += full * enc(LIMIT) + (enc(rest) if rest else 0)
            msgs += 8; bts += 8 * cdap("enrollment", "/mgmt/enrollment", b"\x08\x01")   # connect/r, start/r, stop/r, status/r
        joined.add(u)
        # its new objects (a LowerFlow per new adjacency in each direction, its DFT and AA entries) flood the joined part
        sub = {v: {w for w in adj[v] if w in joined} for v in joined}
        ft = sum(len(s) for s in sub.values()) - (len(sub) - 1)
        new_lf = 2 * len(nb); lfdb += new_lf; names += 1
        k = new_lf + 2; objs += k * ft; msgs += 3 * ft if nb else 0
        bts += ft * (lf_msg([("a", "b")] * max(new_lf, 1)) + one_dft + one_aa) if nb else 0
    cold = {"object_transfers": objs, "messages": msgs, "bytes": bts}
    # refresh: every member, every REFRESH_S, its own objects (deg LowerFlows + DFT + AA) to each neighbour
    rmsg = sum(len(adj[u]) * math.ceil((len(adj[u]) + 2) / LIMIT) for u in adj)
    rbytes = sum(len(adj[u]) * (lf_msg([("a", "b")] * len(adj[u])) + one_dft + one_aa) for u in adj)
    return {"layout": layout, "S": S, "E": E, "members": N, "links": L, "eccentricity_hops": ecc, "diameter_hops": diam,
            "change": change, "cold": cold, "refresh_per_30s": {"messages": rmsg, "bytes": rbytes},
            "sizes": {"one_lowerflow_msg": one_lf, "one_dft_msg": one_dft, "one_aa_msg": one_aa, "lowerflow_bytes_per_obj_full_msg": round(per_obj, 1)}}

if __name__ == "__main__":
    S = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    Es = [int(x) for x in sys.argv[3:]] or [1, 2, 5, 10, 20, 50, 100]
    for layout in ("chain", "tree", "star", "partial", "mesh"):
        for E in Es: print(json.dumps(model(S, E, layout)))
