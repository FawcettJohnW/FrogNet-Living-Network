#!/usr/bin/env python3
"""hub_oracle.py -- [FDF_HUB_V1] oracle for fdf-hub's decisions against a fake kernel. Each case has the control that
makes it fail. Exits nonzero on any failure.
  1 ownership: second claimant of a /24 refused; the first keeps it (lowest announce id, not arrival order)
  2 AllowedIPs never moved by last-write: the kernel's cryptokey trap is mirrored, and the hub's table wins
  3 facts say who, mappings say what: a member without a verified mapping naming this hub gets a peer, no /24
  4 transit: first claimant only while unowned; an owned /24 beats a transit claim
  5 leave: a member's session ends -> its peer is deleted and its routes go; the holder's transit passes to the next
    claimant; one that comes back gets a peer from its facts as they are then (HUB_HAS_NO_MEMORY_V1)
    gets its prefixes with no new peer entry (no re-handshake)
  6 adopt-on-start: a stale never-handshook peer in the kernel is removed, a correct one is left untouched
  7 leaf: role=leaf gets its /32 as edge and allowed-ips, no /24
"""
import importlib.util, os, sys
spec = importlib.util.spec_from_loader("fdfhub", loader=None)
src = open(os.path.join(os.path.dirname(__file__), "..", "bin", "fdf-hub")).read()   # the installed tree: /opt/fdf/hub -> /opt/fdf/bin
hub = importlib.util.module_from_spec(spec); exec(compile(src, "fdf-hub", "exec"), hub.__dict__)
desired, reconcile, FakeKernel = hub.desired, hub.reconcile, hub.FakeKernel

fails = 0
def check(ok, what):
    global fails; print(("PASS  " if ok else "FAIL  ") + what)
    if not ok: fails += 1

def P(i, name, guid, subnet, pub, transit=(), role="node", alive=True, addr=None):
    f = {"role": role, "name": name, "guid": guid, "pubkey": pub, "transit": list(transit)}
    if role == "node": f["subnet"] = subnet
    else: f["addr"] = addr
    return {"id": i, "alive": alive, "name": name, "facts": f}
def M(eid, *hubs): return {"eid": eid, "rloc": ",".join(h + ":1:50" for h in hubs), "site": "t"}

# 1 ownership by lowest id, not arrival order
parts = [P(5, "late", "g-late", "10.9.9.0/24", "PUB-late"), P(2, "first", "g-first", "10.9.9.0/24", "PUB-first")]
want, owner, notes = desired(parts, [M("10.9.9.0/24", "hubA")], "hubA")
check(owner["10.9.9.0/24"] == "g-first" and "PUB-late" not in want, "1 second claimant refused, lowest id owns")
check(any("REFUSED" in n and "g-late" in n and "g-first" in n for n in notes), "1 refusal logged with both guids")
check(want["PUB-first"]["allowed"] == {"10.253.9.9/32", "10.9.9.0/24"}, "1 owner's allowed-ips = edge/32 + its /24")
# control: with the late one alone it owns
w2, o2, _ = desired([parts[0]], [M("10.9.9.0/24", "hubA")], "hubA")
check(o2["10.9.9.0/24"] == "g-late", "1c control: alone, the late claimant owns")

# 2 the kernel trap, and the hub's table winning over it
k = FakeKernel(); reconcile(k, want)
k.set_peer("PUB-rogue", {"10.9.9.0/24"})                  # someone ran wg set by hand: the kernel moved the prefix
check("10.9.9.0/24" not in k.peers()["PUB-first"], "2 control: the fake kernel mirrors WireGuard's last-write trap")
reconcile(k, want)
check(k.peers()["PUB-first"]["allowed"] == {"10.253.9.9/32", "10.9.9.0/24"} and "PUB-rogue" not in k.peers(), "2 reconcile restores the table; the rogue peer is deleted (not a member)")

# 3 facts without a verified mapping naming this hub: peer, no /24
want, owner, notes = desired([P(1, "n", "g-n", "10.1.1.0/24", "PUB-n")], [M("10.1.1.0/24", "hubB")], "hubA")
check(want["PUB-n"]["allowed"] == {"10.253.1.1/32"} and not want["PUB-n"]["routes"], "3 no mapping for this hub: edge only")
want, owner, notes = desired([P(1, "n", "g-n", "10.1.1.0/24", "PUB-n")], [M("10.1.1.0/24", "hubB", "hubA")], "hubA")
check("10.1.1.0/24" in want["PUB-n"]["allowed"] and want["PUB-n"]["routes"] == {"10.1.1.0/24": "10.253.1.1"}, "3c control: mapping names this hub: /24 installed")
want, owner, notes = desired([P(1, "n", "g-n", "10.1.1.0/24", "PUB-n")], [M("10.1.1.0/24", "hubA")], "hubA")
check(want["PUB-n"]["allowed"] == {"10.253.1.1/32", "10.1.1.0/24"}, "3 mapping present but nothing else: still only what facts claim")

# 4 transit: first claimant while unowned; owned beats transit
parts = [P(1, "a", "g-a", "10.1.1.0/24", "PUB-a", transit=["10.77.77.0/24"]),
         P(2, "b", "g-b", "10.2.2.0/24", "PUB-b", transit=["10.77.77.0/24"]),
         P(3, "c", "g-c", "10.77.77.0/24", "PUB-c")]
maps = [M("10.1.1.0/24", "hubA"), M("10.2.2.0/24", "hubA"), M("10.77.77.0/24", "hubA")]
want, owner, notes = desired(parts, maps, "hubA")
check(owner["10.77.77.0/24"] == "g-c" and "10.77.77.0/24" not in want["PUB-a"]["allowed"], "4 owned /24 beats both transit claims")
want, owner, notes = desired(parts[:2], maps, "hubA")
check(owner["10.77.77.0/24"] == "g-a" and "10.77.77.0/24" in want["PUB-a"]["allowed"] and "10.77.77.0/24" not in want["PUB-b"]["allowed"], "4 unowned transit: first claimant gets it")

# 5 leave: a's session ends -> its peer is DELETED now, transit passes to b; a comes back -> a peer from its facts now
# [HUB_HAS_NO_MEMORY_V1]
k = FakeKernel(); reconcile(k, want)
parts2 = [dict(parts[0], alive=False), parts[1]]
want, owner, notes = desired(parts2, maps, "hubA")
k.hs["PUB-a"] = 1000; n = reconcile(k, want, now=1010)
check("PUB-a" not in k.peers() and "10.77.77.0/24" in k.peers()["PUB-b"]["allowed"] and k.routes()["10.77.77.0/24"] == "10.253.2.2", "5 leave: peer deleted now, transit passed to the next claimant, route moved")
want_back, _, _ = desired(parts[:2], maps, "hubA")
k.log.clear(); reconcile(k, want_back, now=1100)
check(k.peers()["PUB-a"]["allowed"] == {"10.253.1.1/32", "10.1.1.0/24", "10.77.77.0/24"}, "5 came back: a peer again, from the records now")
want_gone, _, _ = desired(parts2, maps, "hubA"); reconcile(k, want_gone, now=1200)
check("PUB-a" not in k.peers(), "5 gone again: deleted again, no parking, no timer")

# 6 adopt-on-start: stale peer removed, correct peer untouched
k = FakeKernel(); k.p = {"PUB-b": {"10.253.2.2/32", "10.2.2.0/24", "10.77.77.0/24"}, "PUB-stale": {"10.253.5.5/32", "10.5.5.0/24"}}
k.r = {"10.2.2.0/24": "10.253.2.2", "10.77.77.0/24": "10.253.2.2", "10.5.5.0/24": "10.253.5.5"}
k.log.clear(); reconcile(k, want)
check(("del", "PUB-stale") in k.log and ("unroute", "10.5.5.0/24") in k.log and "PUB-stale" not in k.peers(), "6 adopt: stale peer deleted and its route removed")
check(not any(e[0] == "set" and e[1] == "PUB-b" for e in k.log), "6 adopt: the correct peer got no command")

# 7 leaf
want, owner, notes = desired([P(1, "lap", "g-lap", None, "PUB-lap", role="leaf", addr="10.252.0.7/32")], [], "hubA")
check(want["PUB-lap"]["allowed"] == {"10.252.0.7/32"} and want["PUB-lap"]["edge"] == "10.252.0.7" and want["PUB-lap"]["routes"] == {"10.252.0.7/32": ""}, "7 leaf: its /32 is edge, allowed-ips and an on-link route")

# 8 [HUB_PREFIXES_FROM_GRAPH_V1] members with rows: the graph decides what is behind whom.
#   Seattle7 (10.170.170.0/24) is a guest of Seattle5 (10.250.250.0/24, eth uplink). [OWN_TUNNEL_CARRIES_OWN_V1]:
#   Seattle7 holds a hub link, so its own peer carries its /24; Seattle5 carries only its own.
def rows(*r): return [{"iface": a, "ip": b, "value": c, "kind": d, "mac": e} for a, b, c, d, e in r]
s5 = P(1, "Seattle5", "g-s5", "10.250.250.0/24", "PUB-s5"); s5["facts"]["addrs"] = rows(
    ("wlan0", "10.250.250.1", "FrogNetHost.Seattle5", "own", "aa"), ("eth0", "192.168.0.21", "uplink eth 1000", "uplink", "aa"),
    ("wg0", "10.253.250.250", "", "hub", ""), ("wlan0", "10.250.250.63", "Seattle7", "given", "b8:27"))
s7 = P(2, "Seattle7", "g-s7", "10.170.170.0/24", "PUB-s7"); s7["facts"]["addrs"] = rows(
    ("wlan0", "10.170.170.1", "FrogNetHost.Seattle7", "own", "bb"), ("wlan1", "10.250.250.63", "Seattle7", "held", "b8:27"),
    ("wg0", "10.253.170.170", "", "hub", ""), ("wlan1", "10.250.250.63", "uplink wifi 72", "uplink", "b8:27"))
hub = {"id": 0, "alive": True, "name": "hubA", "facts": {"role": "hub", "pubkey": "H", "endpoint": "e"}}
maps8 = [M("10.250.250.0/24", "hubA"), M("10.170.170.0/24", "hubA")]
want, owner, notes = desired([hub, s5, s7], maps8, "hubA")
check(want["PUB-s7"]["allowed"] == {"10.253.170.170/32", "10.170.170.0/24"} and owner["10.170.170.0/24"] == "g-s7", "8 a child WITH a tunnel carries its own /24 on it")
check(want["PUB-s5"]["allowed"] == {"10.253.250.250/32", "10.250.250.0/24"}, "8 the parent carries only its own")
check(want["PUB-s7"]["routes"]["10.170.170.0/24"] == "10.253.170.170", "8 the hub routes the child's /24 to the child's edge")
# 8t the same child WITHOUT a tunnel (no hub row): the parent carries it, as in the netns y-behind-z test
s7t = dict(s7, facts=dict(s7["facts"], addrs=[r for r in s7["facts"]["addrs"] if r["kind"] != "hub"], pubkey=""))
want, owner, notes = desired([hub, s5, s7t], maps8, "hubA")
check(want["PUB-s5"]["allowed"] == {"10.253.250.250/32", "10.250.250.0/24", "10.170.170.0/24"} and owner["10.170.170.0/24"] == "g-s5", "8t tunnel-less child: the parent carries its /24")
check(want["PUB-s5"]["routes"]["10.170.170.0/24"] == "10.253.250.250", "8t the hub routes it to the parent's edge")
# 8b the parent leaves: the tunneled child is unchanged (its own link carried it all along)
want, owner, notes = desired([hub, s7], maps8, "hubA")
check(want["PUB-s7"]["allowed"] == {"10.253.170.170/32", "10.170.170.0/24"} and owner["10.170.170.0/24"] == "g-s7", "8b parent gone: nothing changes for the tunneled child")
# 8c tunnel-less child, parent's mapping for its /24 missing: carried by nobody, said
want, owner, notes = desired([hub, s5, s7t], [M("10.250.250.0/24", "hubA")], "hubA")
check("10.170.170.0/24" not in want["PUB-s5"]["allowed"] and any("10.170.170.0/24 by Seattle5: no verified mapping" in n for n in notes), "8c no mapping from the parent: the tunnel-less child's /24 is carried by nobody, said")
# 8d [HUB_HAS_NO_MEMORY_V1] the parent's daemon is dead (no mapping) but the tunneled child registered its own: carried
want, owner, notes = desired([hub, s5, s7], [M("10.170.170.0/24", "hubA")], "hubA")
check("10.170.170.0/24" in want["PUB-s7"]["allowed"] and "10.250.250.0/24" not in want["PUB-s5"]["allowed"], "8d child's own registration is enough; the parent without a mapping gets no /24")

# 9 [REACH_IS_A_LEAF_HERE_V1] a participant reaching this network (reach:true, the /32 the region gave it) is peered
#   as a leaf is; a participant that only reads (no reach) is not peered at all
r = P(1, "n", "g-n", None, "PUB-n", role="participant", addr="10.252.0.3/32"); r["facts"]["reach"] = True
want, owner, notes = desired([r], [], "hubA")
check(want.get("PUB-n", {}).get("allowed") == {"10.252.0.3/32"} and want["PUB-n"]["routes"] == {"10.252.0.3/32": ""}, "9 a reacher: its /32 is allowed-ips and an on-link route, like a leaf")
q = P(1, "n", "g-n", None, "PUB-n", role="participant", addr="10.252.0.3/32")
want, owner, notes = desired([q], [], "hubA")
check("PUB-n" not in want, "9c control: a participant without reach is not peered")

print("RESULT " + ("FAIL: %d" % fails if fails else "PASS")); sys.exit(1 if fails else 0)
