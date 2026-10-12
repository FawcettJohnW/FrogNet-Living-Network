#!/usr/bin/env python3
"""apply_once.py -- one rebuild of a node's plan from the region, applied to THIS namespace with the real World
(the daemon's region-loop step 2-3, without the daemon). For tools/netns-graph-test.sh.
   env: RINA_RAM, RINA_AREA, FDF_FACTS, NODE_NAME, NODE_GUID, PUBKEY, LOCAL_SUBNET, FROGNET_DIR, FDF_PY, HOLD_DOWN_SEC,
        HUB_IFACE (the wg iface name here), STATE (a json file the applier's state persists in between calls)
"""
import json
import os
import sys
import pickle

sys.path.insert(0, os.environ["FROGNET_DIR"])
sys.path.insert(0, os.environ.get("FDF_PY", "/opt/fdf/lib/python"))
import internet_tunnels_v3.config as c
c.REGION_RAM = os.environ["RINA_RAM"]; c.REGION_AREA = os.environ["RINA_AREA"]
c.LOCAL_SUBNET = os.environ["LOCAL_SUBNET"]; c.NODE_NAME = os.environ["NODE_NAME"]; c.NODE_GUID = os.environ["NODE_GUID"]
c.PUBKEY = os.environ.get("PUBKEY", ""); c.POND = "p"; c.HANDSHAKE_DEAD_SEC = 600
import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stdout)
c.log = logging.getLogger("apply")
from internet_tunnels_v3 import region, graph_apply
from internet_tunnels_v3.graph_apply import Applier, World
graph_apply.HOLD_DOWN_SEC = float(os.environ.get("HOLD_DOWN_SEC", "60"))


class W(World):
    def hub_alive(self, iface):            # wireguard-go in a netns: the handshake is what the test checks elsewhere
        return os.environ.get("HUB_ALIVE", "1") == "1"


state = os.environ.get("STATE", "")
ap = None
if state and os.path.exists(state):
    with open(state, "rb") as f:
        ap = pickle.load(f)
    ap.w = W(config=c, log=c.log)
if ap is None:
    ap = Applier(W(config=c, log=c.log), c.NODE_NAME)
failures = set(sys.argv[1:])
parts = [] if "region" in failures else region.world()[0]        # [WORLD_IN_ONE_READ_V1]
# the plan's hub iface is whatever my wg row says; in the daemon the channel name decides. Here: HUB_IFACE.
import time as _tm; _t0 = _tm.monotonic()
out = ap.apply(parts, positive_failures=failures)
print("APPLY_TIMED apply_ms=%d route_cmds=%s/%sms probes=%s/%sms" % ((_tm.monotonic() - _t0) * 1000, out.get("route_cmds"), out.get("route_cmds_ms"), out.get("probes"), out.get("probes_ms")))
c.live_hubs = {os.environ.get("HUB_NAME", "hubA"): 10}
region.publish_plan_maps(out.get("maps", []) if out.get("hub_active") else [])
print("APPLY", json.dumps({k: v for k, v in out.items() if k in ("routes_added", "routes_removed", "routes_refused", "default", "hosts_added", "hosts_removed", "held", "maps", "hub_active", "applied")}, default=str))
if state:
    w = ap.w; ap.w = None
    with open(state, "wb") as f:
        pickle.dump(ap, f)
