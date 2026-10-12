#!/usr/bin/env python3
"""test_region_oracle.py -- [REGION_V1] oracle: this node's side of the region, with controls.
  1 register_node writes the facts file (the node's fdf-client announces it): pubkey is the daemon's, pond is RINA_AREA
  2 my_channels: one channel per LIVE hub that holds my key (facts.peers); a dead hub, or one without my key yet, yields none (controls)
  3 remote_subnets come ONLY from verified mappings naming that hub; a prefix mapped to another hub is absent (control)
  5 publish_plan_maps: locator set ordered by handshake RTT, prio 1 = fastest; a hub without a tunnel is not a locator
"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["FDF_FACTS"] = os.path.join(tempfile.mkdtemp(), "facts.json")
import internet_tunnels_v3.config as config
config.REGION_RAM = "127.0.0.1:1"; config.REGION_AREA = "frognet.test"
config.NODE_NAME = "Seattle9"; config.NODE_GUID = "g-9"; config.LOCAL_SUBNET = "10.9.9.0/24"; config.PUBKEY = "PK9"
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

# 1 register_node
region.own_subnet = lambda: "10.9.9.0/24"
region.register_node()
f = json.load(open(os.environ["FDF_FACTS"]))
check(f["role"] == "node" and f["guid"] == "g-9" and f["name"] == "Seattle9" and f["subnet"] == "10.9.9.0/24" and f["pubkey"] == "PK9"
      and f["pond"] == "frognet.test", "1 register_node wrote the facts file (pubkey the daemon's, pond = RINA_AREA)")

# 2 my-channels, one per live hub
ch = region.my_channels()
names = sorted(c["channel_name"] for c in ch["channels"])
check(names == ["hub-hubA", "hub-hubB"], "2 one channel per live hub")
w = {c["channel_name"]: c["wg_config"] for c in ch["channels"]}
check(w["hub-hubA"]["edge_ip"] == "10.253.9.9" and w["hub-hubA"]["edge_mask"] == 16 and w["hub-hubA"]["droplet_pubkey"] == "HPA"
      and w["hub-hubA"]["droplet_endpoint"] == "203.0.113.1:51820" and w["hub-hubA"]["allowed_ips"] == "10.0.0.0/8", "2 wg_config: edge from our /24, hub's pubkey/endpoint, 10/8")
state["parts"] = [dict(HUBS[0], alive=False), HUBS[1]] + NODES
ch2 = region.my_channels()
check([c["channel_name"] for c in ch2["channels"]] == ["hub-hubB"], "2c control: a hub whose session is down yields no channel")
check(ch2["digest"] != ch["digest"], "2 digest changes with the channel set")
state["parts"] = [dict(HUBS[0], facts=dict(HUBS[0]["facts"], peers=["PK1"])), HUBS[1]] + NODES
check([c["channel_name"] for c in region.my_channels()["channels"]] == ["hub-hubB"],
      "2h control: a live hub whose facts.peers does not hold my key yet yields no channel ([HUB_SAYS_ITS_PEERS_V1])")
state["parts"] = HUBS + NODES

# 3 remote_subnets only from verified mappings naming that hub, never ours
check(w["hub-hubA"]["remote_subnets"] == ["10.1.1.0/24", "10.123.123.0/24", "10.77.77.0/24"], "3 hubA reaches 10.1.1, its own /24 (it is a node), the transit 10.77.77; not 10.2.2 (mapped to hubB only); not ours")
check(w["hub-hubB"]["remote_subnets"] == ["10.1.1.0/24", "10.2.2.0/24"], "3 hubB reaches 10.1.1 and 10.2.2; not 10.77.77")

# 5 publish_plan_maps ordering by rtt; a hub without a tunnel is not a locator
PLAN = ["10.9.9.0/24", "10.88.88.0/24"]
config.live_hubs = {"hubB": 40, "hubA": 90}; region.publish_plan_maps(PLAN)
f = json.load(open(os.environ["FDF_FACTS"]))
eids = {m["eid"]: m["rlocs"] for m in f["maps"]}
check(eids == {"10.9.9.0/24": "hubB:1:50,hubA:2:50", "10.88.88.0/24": "hubB:1:50,hubA:2:50"}, "5 the plan's prefixes mapped, fastest hub prio 1")
config.live_hubs = {"hubA": 90}; region.publish_plan_maps(PLAN)
f = json.load(open(os.environ["FDF_FACTS"]))
check(all(m["rlocs"] == "hubA:1:50" for m in f["maps"]), "5c control: hub without a tunnel is not a locator")
config.live_hubs = {}; region.publish_plan_maps(PLAN)
f = json.load(open(os.environ["FDF_FACTS"]))
check(f["maps"] == [], "5 no tunnels -> no mappings (fdf-client withdraws them)")

print("RESULT " + ("FAIL: %d" % fails if fails else "PASS")); sys.exit(1 if fails else 0)
