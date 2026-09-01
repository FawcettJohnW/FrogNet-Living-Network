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
"""election_split_merge_sim.py — drive the REAL election over the REAL network sim
across random splits and merges, with special-purpose specialist nodes per service.

Network truth: frognet_sim.Topology + discover_routes() — after convergence each node's
known_hosts IS its reachable candidate view. A SPLIT cuts L2 segments and re-converges;
a MERGE restores them and re-converges.

Election truth: frognet_role_elect.elect(handler) over the real core handlers
(databasehost = WAN-wide mysql gate, mediahost = LAN-only ffmpeg+libvpx gate, gamehost =
board class, python3-trivial). We feed each node's reachable set as that node's tuple
view and assert: (1) every node in a partition elects the SAME winner per role
(deterministic agreement), (2) a reachable specialist wins its role on merit, (3) on
split the winner re-elects within the island, (4) on merge it returns.
"""
import os, sys, types, time, random, itertools
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "commbundle"))
import frognet_sim as S

FAILS = []
def check(label, problems):
    print(("  [PASS] " if not problems else "  [FAIL] ") + label)
    for p in problems: print("         - " + p); FAILS.append(p)

# ---- specialist capability blobs (what install_<role> would advertise) -------------
def db_cap(ip, mon=False):   # mysql gate; 'mon' = the DB monster (off-mesh beefy box)
    return dict(lan_ip=ip, mysql_running=1,
                mem_available_kb=(64 if mon else 2)*1024*1024,
                cores=(16 if mon else 2), cpu_bench_total=(14000 if mon else 1500),
                disk_write_mbps=(900 if mon else 40), disk_fsync_ms=(1 if mon else 6))
def media_cap(ip, gpu=False, libvpx=True):
    return dict(lan_ip=ip, ffmpeg=1, libvpx=1 if libvpx else 0,
                cores=(16 if gpu else 2), cpu_bench_total=(14000 if gpu else 1500))
def game_cap(ip):            # board class: python3-trivial, every reachable box eligible
    return dict(lan_ip=ip)

# Map node -> which roles it advertises (a specialist advertises only its class).
def caps_for(roles_by_node, reachable_ips):
    """Build {role: {ip: cap}} from the per-node advertisement, restricted to the set
    of IPs reachable in THIS partition (a candidate you can't reach isn't a candidate)."""
    out = {"databasehost": {}, "mediahost": {}, "gamehost": {}}
    for ip, roles in roles_by_node.items():
        if ip not in reachable_ips: continue
        for role, blob in roles.items():
            out[role][ip] = dict(blob)
    return out

# ---- install a fake tuple module that serves a GIVEN partition's view -----------------
def install_view(caps_by_role, wan_subnets, my_ip):
    T = types.ModuleType("frognet_tuples"); now = int(time.time())
    rows = {("discovery","reach_plane"): [
        {"addr": my_ip, "var":"reach_plane",
         "value":{"wan_subnets": sorted(wan_subnets), "self_ip": my_ip, "ts": now}}]}
    for role, caps in caps_by_role.items():
        rr=[]
        for ip,cap in caps.items():
            rr.append({"addr":ip,"var":"capability",
                       "value":{"capability":cap,"loadavg":{"1":0.1},"temps_c":[],"ts":now}})
        rows[(role,"capability")]=rr
    def _get(service, var, dbhost=None, fresh_s=0, timeout=4.0): return rows.get((service,var),[])
    T.my_ip=lambda: my_ip; T.get=_get; T.put=lambda *a,**k: True
    T.DEFAULT_DBHOST="databasehost_control.frognet"
    sys.modules["frognet_tuples"]=T

def fresh_elect():
    for m in ("frognet_role_elect","frognet_service_hosts"): sys.modules.pop(m,None)
    import frognet_role_elect as RE
    from core.role_registry import role_handler
    return RE, role_handler

def slash24(ip): return ip.rsplit(".",1)[0]+".0/24"

# ---- reachability from the REAL sim: converge, read each node's known_hosts ----------
def reachable_sets(topo):
    S.discover_routes(topo, max_cycles=16)
    return {n: set(node.known_hosts.keys()) for n,node in topo.nodes.items()}

def elect_all(roles_by_node, node_ip, reach_ips, wan_subnets):
    """Run the real election for each role from the vantage of one node."""
    caps = caps_for(roles_by_node, reach_ips)
    install_view(caps, wan_subnets, node_ip)
    RE, role_handler = fresh_elect()
    res={}
    for role in ("databasehost","mediahost","gamehost"):
        h=role_handler(role); w=RE.elect(h)
        res[role]= w["lan_ip"] if w else None
    return res

def agree_and_report(tag, topo, roles_by_node, ip_of, wan_of):
    reach = reachable_sets(topo)
    # group nodes into partitions by mutual reachability (symmetric known_hosts)
    names=list(topo.nodes); seen=set(); groups=[]
    for n in names:
        if n in seen: continue
        grp={n}; frontier=[n]
        while frontier:
            cur=frontier.pop()
            for m in names:
                if m in grp: continue
                if ip_of[m] in reach[cur] and ip_of[cur] in reach[m]:
                    grp.add(m); frontier.append(m)
        seen|=grp; groups.append(sorted(grp))
    print(f"  {tag}: partitions = {groups}")
    problems=[]; summary={}
    for grp in groups:
        # every node in the partition sees the same reachable IP set for election input
        reach_ips=set(); 
        for n in grp: reach_ips|=reach[n]
        # WAN subnets within this island = the /24s of reachable nodes other than own LAN
        # role scope: mediahost is LAN-scoped (one winner PER LAN); databasehost &
        # gamehost are WAN-wide (one winner across the whole partition).
        LAN_SCOPED={"mediahost"}
        per_node={}
        for n in grp:
            wan = {slash24(ip_of[m]) for m in grp if slash24(ip_of[m])!=slash24(ip_of[n])}
            per_node[n]=elect_all(roles_by_node, ip_of[n], reach_ips, wan)
        # WAN-wide roles: every node in the partition must match
        for role in ("databasehost","gamehost"):
            vals={n:per_node[n][role] for n in grp}
            if len(set(vals.values()))!=1:
                problems.append(f"partition {grp} WAN role {role} DISAGREED: {vals}")
        # LAN-scoped roles: nodes sharing a /24 must match each other
        for role in LAN_SCOPED:
            by_lan={}
            for n in grp: by_lan.setdefault(slash24(ip_of[n]),{})[n]=per_node[n][role]
            for lan,vals in by_lan.items():
                if len(set(vals.values()))!=1:
                    problems.append(f"partition {grp} LAN role {role} on {lan} DISAGREED: {vals}")
        # summary: WAN winners once, mediahost per-LAN
        srec=dict((r,per_node[grp[0]][r]) for r in ("databasehost","gamehost"))
        srec["mediahost(by LAN)"]={slash24(ip_of[n]):per_node[n]["mediahost"] for n in grp}
        summary[tuple(grp)]=srec
    check(f"{tag}: all nodes in each partition agree on every role", problems)
    for grp,res in summary.items():
        print(f"      {list(grp)} -> {res}")
    return summary

# ====================================================================================
def build_world(seed):
    """Random three-site-ish topology + specialists. Returns (topo, roles_by_node,
    ip_of, segment list we can cut/restore)."""
    rng=random.Random(seed)
    t=S.Topology(f"rand{seed}")
    # three site gateways (.1), each its own /24; a transit /30 mesh between them
    sites={"SEA":"10.160.160.1","NYC":"10.102.60.1","AMS":"10.120.120.1"}
    for name,ip in sites.items():
        n=S.FrogNode(name); n.add_iface("eth0", ip+"/24"); t.add(n)
    # specialists hang off site LANs (non-.1 boxes on a site's /24)
    # DB monster off NYC; media GPU box off SEA; board box off AMS
    spec={"DBMON":("10.102.60.50","NYC"), "GPU":("10.160.160.50","SEA"), "BRD":("10.120.120.50","AMS")}
    for name,(ip,site) in spec.items():
        n=S.FrogNode(name); n.add_iface("eth0", ip+"/24"); t.add(n)
        t.link((name,"eth0"),(site,"eth0"))               # same /24 as its site gw
    # site-to-site transit: a shared /30-ish segment (model as one shared subnet each pair)
    # use distinct transit ifaces on the gateways
    segs=[]
    pairs=[("SEA","NYC"),("NYC","AMS"),("SEA","AMS")]
    for i,(a,b) in enumerate(pairs):
        sub=f"10.251.{i+1}.0/30"
        t.nodes[a].add_iface(f"wg{i}", f"10.251.{i+1}.1/30")
        t.nodes[b].add_iface(f"wg{i}", f"10.251.{i+1}.2/30")
        t.link((a,f"wg{i}"),(b,f"wg{i}"))
        segs.append(((a,f"wg{i}"),(b,f"wg{i}")))
    roles_by_node={
        sites["SEA"]:{"databasehost":db_cap(sites["SEA"]),"mediahost":media_cap(sites["SEA"]),"gamehost":game_cap(sites["SEA"])},
        sites["NYC"]:{"databasehost":db_cap(sites["NYC"]),"mediahost":media_cap(sites["NYC"]),"gamehost":game_cap(sites["NYC"])},
        sites["AMS"]:{"databasehost":db_cap(sites["AMS"]),"mediahost":media_cap(sites["AMS"]),"gamehost":game_cap(sites["AMS"])},
        "10.102.60.50":{"databasehost":db_cap("10.102.60.50",mon=True)},     # DB monster
        "10.160.160.50":{"mediahost":media_cap("10.160.160.50",gpu=True)},   # media GPU box
        "10.120.120.50":{"gamehost":game_cap("10.120.120.50")},              # board box
    }
    ip_of={n: t.nodes[n].ifaces["eth0"].ip for n in t.nodes}
    return t, roles_by_node, ip_of, segs

def run():
    print("== election across splits/merges on the real frognet_sim ==")
    for seed in (1,2,3):
        print(f"\n--- random world seed {seed} ---")
        t,roles,ip_of,segs = build_world(seed)
        whole = agree_and_report("WHOLE", t, roles, ip_of, None)
        # expectation while whole: DB monster (off NYC) wins data WAN-wide; media is LAN
        # so each LAN's own media box wins locally; board specialist wins gamehost.
        for grp,res in whole.items():
            if res["databasehost"]!="10.102.60.50":
                FAILS.append(f"seed{seed} WHOLE: DB monster should win data, got {res['databasehost']}")
        # --- SPLIT: cut ALL transit segments -> three isolated sites ---
        kept=list(t.segments)
        t.segments=[s for s in t.segments if s not in [list(x) for x in segs]]
        split = agree_and_report("SPLIT (sites isolated)", t, roles, ip_of, None)
        # each island now elects within itself: NYC keeps the DB monster; SEA/AMS elect own
        for grp,res in split.items():
            if "10.102.60.1" in [ip_of[n] for n in grp]:
                if res["databasehost"] not in ("10.102.60.50","10.102.60.1"):
                    FAILS.append(f"seed{seed} SPLIT NYC island data winner wrong: {res}")
        # --- MERGE: restore transit -> should return to the whole-network winners ---
        t.segments=kept
        merged = agree_and_report("MERGE (transit restored)", t, roles, ip_of, None)
        for grp,res in merged.items():
            if res["databasehost"]!="10.102.60.50":
                FAILS.append(f"seed{seed} MERGE: DB monster should win data again, got {res['databasehost']}")

    print("\n== RESULT ==")
    print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES:\n  - " + "\n  - ".join(FAILS))
    return 0 if not FAILS else 1

if __name__=="__main__":
    sys.exit(run())
