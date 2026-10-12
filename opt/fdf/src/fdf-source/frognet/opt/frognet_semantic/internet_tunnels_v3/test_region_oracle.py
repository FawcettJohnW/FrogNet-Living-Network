#!/usr/bin/env python3
"""test_region_oracle.py -- [REGION_V1] oracle: the broker's four paths answered from the region, with controls.
  1 register writes the facts file (the node's fdf-client announces it); nothing goes to any broker
  2 my-channels: one channel per LIVE hub that holds my key (facts.peers); a dead hub, or one without my key yet, yields none (controls)
  3 remote_subnets come ONLY from verified mappings naming that hub; a prefix mapped to another hub is absent (control)
  4 update-subnets: transit into facts, maps republished; unchanged -> "unchanged"
  5 publish_maps: locator set ordered by handshake RTT, prio 1 = fastest; a hub without a tunnel is not a locator
  6 transit-map shape: {"map": {"10.x.y": [/24s]}} from alive nodes only
"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["FDF_FACTS"] = os.path.join(tempfile.mkdtemp(), "facts.json")
import internet_tunnels_v3.config as config
config.REGION_RAM = "127.0.0.1:1"; config.REGION_AREA = "frognet.test"
config.NODE_NAME = "Seattle9"; config.NODE_GUID = "g-9"; config.LOCAL_SUBNET = "10.9.9.0/24"; config.PUBKEY = "PK9"; config.POND = "frognet"
from internet_tunnels_v3 import region

fails = 0
def check(ok, what):
    global fails; print(("PASS  " if ok else "FAIL  ") + what)
    if not ok: fails += 1

HUBS = [{"id": 1, "alive": True, "name": "hubA", "facts": {"role": "hub", "name": "hubA", "pubkey": "HPA", "endpoint": "203.0.113.1:51820", "addr": "10.253.253.254", "area": "frognet.test", "subnet": "10.123.123.0/24", "peers": ["PK1", "PK9"]}},
        {"id": 2, "alive": True, "name": "hubB", "facts": {"role": "hub", "name": "hubB", "pubkey": "HPB", "endpoint": "203.0.113.2:51820", "addr": "10.253.253.254", "area": "frognet.test", "peers": ["PK9"]}}]
NODES = [{"id": 3, "alive": True, "name": "n1", "facts": {"role": "node", "name": "Seattle1", "subnet": "10.1.1.0/24", "transit": ["10.77.77.0/24"], "pubkey": "PK1"}},
         {"id": 4, "alive": False, "name": "n2", "facts": {"role": "node", "name": "Seattle2", "subnet": "10.2.2.0/24", "pubkey": "PK2"}}]
MAPS = [{"eid": "10.1.1.0/24", "rloc": "hubA:1:50,hubB:2:50", "site": "frognet.test"},
        {"eid": "10.77.77.0/24", "rloc": "hubA:1:50", "site": "frognet.test"},
        {"eid": "10.2.2.0/24", "rloc": "hubB:1:50", "site": "frognet.test"},
        {"eid": "10.9.9.0/24", "rloc": "hubA:1:50,hubB:2:50", "site": "frognet.test"}]
state = {"parts": HUBS + NODES, "maps": MAPS}
region._call = lambda body: ({"participants": state["parts"]} if body["op"] == "participants" else {"mappings": state["maps"]})

# 1 register
r = region.post("/api/v4/register", {"pond": "frognet", "pubkey": "PK9", "subnet": "10.9.9.0/24", "node_name": "Seattle9", "guid": "g-9"})
f = json.load(open(os.environ["FDF_FACTS"]))
check(r["node_id"] == "g-9" and f["role"] == "node" and f["subnet"] == "10.9.9.0/24" and f["pubkey"] == "PK9", "1 register wrote the facts file")

# 2 my-channels, one per live hub
ch = region.get("/api/v4/my-channels", {"pubkey": "PK9"})
names = sorted(c["channel_name"] for c in ch["channels"])
check(names == ["hub-hubA", "hub-hubB"], "2 one channel per live hub")
w = {c["channel_name"]: c["wg_config"] for c in ch["channels"]}
check(w["hub-hubA"]["edge_ip"] == "10.253.9.9" and w["hub-hubA"]["edge_mask"] == 16 and w["hub-hubA"]["droplet_pubkey"] == "HPA"
      and w["hub-hubA"]["droplet_endpoint"] == "203.0.113.1:51820" and w["hub-hubA"]["allowed_ips"] == "10.0.0.0/8", "2 wg_config: edge from our /24, hub's pubkey/endpoint, 10/8")
state["parts"] = [dict(HUBS[0], alive=False), HUBS[1]] + NODES
ch2 = region.get("/api/v4/my-channels", {})
check([c["channel_name"] for c in ch2["channels"]] == ["hub-hubB"], "2c control: a hub whose session is down yields no channel")
check(ch2["digest"] != ch["digest"], "2 digest changes with the channel set")
state["parts"] = [dict(HUBS[0], facts=dict(HUBS[0]["facts"], peers=["PK1"])), HUBS[1]] + NODES
check([c["channel_name"] for c in region.get("/api/v4/my-channels", {})["channels"]] == ["hub-hubB"],
      "2h control: a live hub whose facts.peers does not hold my key yet yields no channel ([HUB_SAYS_ITS_PEERS_V1])")
state["parts"] = HUBS + NODES

# 3 remote_subnets only from verified mappings naming that hub, never ours
check(w["hub-hubA"]["remote_subnets"] == ["10.1.1.0/24", "10.123.123.0/24", "10.77.77.0/24"], "3 hubA reaches 10.1.1, its own /24 (it is a node), the transit 10.77.77; not 10.2.2 (mapped to hubB only); not ours")
check(w["hub-hubB"]["remote_subnets"] == ["10.1.1.0/24", "10.2.2.0/24"], "3 hubB reaches 10.1.1 and 10.2.2; not 10.77.77")

# 4 update-subnets
r = region.post("/api/v4/update-subnets", {"pubkey": "PK9", "transit_subnets": ["10.88.88.0/24"]})
f = json.load(open(os.environ["FDF_FACTS"]))
check(r["status"] == "updated" and r["added"] == ["10.88.88.0/24"] and f["transit"] == ["10.88.88.0/24"], "4 transit into facts")
r = region.post("/api/v4/update-subnets", {"pubkey": "PK9", "transit_subnets": ["10.88.88.0/24"]})
check(r["status"] == "unchanged", "4c control: same transit -> unchanged")

# 5 publish_maps ordering by rtt; a hub without a tunnel is not a locator
region.publish_maps({"hubB": 40, "hubA": 90})
f = json.load(open(os.environ["FDF_FACTS"]))
eids = {m["eid"]: m["rlocs"] for m in f["maps"]}
check(eids == {"10.9.9.0/24": "hubB:1:50,hubA:2:50", "10.88.88.0/24": "hubB:1:50,hubA:2:50"}, "5 our /24 and transit mapped, fastest hub prio 1")
region.publish_maps({"hubA": 90})
f = json.load(open(os.environ["FDF_FACTS"]))
check(all(m["rlocs"] == "hubA:1:50" for m in f["maps"]), "5c control: hub without a tunnel is not a locator")
region.publish_maps({})
f = json.load(open(os.environ["FDF_FACTS"]))
check(f["maps"] == [], "5 no tunnels -> no mappings (fdf-client withdraws them)")

# 6 transit-map
tm = region.get("/api/v4/transit-map", {})
check(tm["map"] == {"10.1.1": ["10.1.1.0/24", "10.77.77.0/24"]}, "6 transit-map from alive nodes only (Seattle2 is down)")

print("RESULT " + ("FAIL: %d" % fails if fails else "PASS")); sys.exit(1 if fails else 0)
