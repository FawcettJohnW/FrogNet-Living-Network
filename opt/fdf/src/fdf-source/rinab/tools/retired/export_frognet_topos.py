#!/usr/bin/env python3
"""export_frognet_topos.py FROGNET_ROOT OUT.json -- [EXP3_FROGNET_TOPOLOGIES_V1] FD&F experiment 3 runs FrogNet's own 20
discovery topologies. This imports FrogNet's simulation/frognet_sim.py UNCHANGED and calls its 20 builders in the order
its main() runs them, writing each topology's structure: its nodes, and its L2 segments (the (node, iface) pairs that
share one subnet -- LAN or WireGuard alike). Nothing is re-typed by hand."""
import json, os, sys
root = sys.argv[1]
os.environ.setdefault("FROGNET_PROXY_ROOT", os.path.join(root, "opt/frognet_semantic"))
os.environ.setdefault("FROGNET_BIN_ROOT", os.path.join(root, "usr/local/bin"))
sys.path.insert(0, os.path.join(root, "opt/frognet_semantic/simulation"))
import frognet_sim as fs
BUILDERS = ["topo_pair", "topo_chain_3", "topo_chain_5", "topo_star", "topo_ring_4", "topo_snowflake", "topo_lan_only_chain",
            "topo_pond_full_mesh", "topo_pond_with_workers", "topo_pond_hub_spoke", "topo_mixed", "topo_asym_lan2_wg_lan3",
            "topo_asym_chain_wg_star", "topo_asym_ring_wg_lan", "topo_asym_three_sites", "topo_multilan_shared_wg_transit",
            "topo_dual_wg_transit_bridge", "topo_solo_wlan0_ap", "topo_two_aps_ham_radio", "topo_mixed_iface_no_router"]
# the same 20, in the same order, as frognet_sim.main() runs them (checked against its source below)
src = open(fs.__file__).read(); loop = src[src.index("for builder in (topo_pair,"):src.index("all_ok &= run_topo(builder())")]
assert [b.strip(" ,\n():") for b in loop.split("for builder in (")[1].split(",") if b.strip(" ,\n():")] == BUILDERS, "frognet_sim's topology list changed"
out = []
for b in BUILDERS:
    t = getattr(fs, b)()
    out.append({"builder": b, "label": t.label, "nodes": list(t.nodes.keys()),
                "segments": [[[n, i] for (n, i) in seg] for seg in t.segments],
                # [MATRIX_DISCOVERY_V1] each interface's address as FrogNet's topology assigns it (addr_cidr)
                "addrs": {n: {i: f.addr_cidr for i, f in node.ifaces.items()} for n, node in t.nodes.items()}})
json.dump(out, open(sys.argv[2], "w"), indent=1)
print(f"{len(out)} topologies, {sum(len(t['nodes']) for t in out)} nodes, {sum(len(t['segments']) for t in out)} segments -> {sys.argv[2]}")
