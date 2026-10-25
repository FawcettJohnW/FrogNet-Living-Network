#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_ram_agent.py - [S4_RAM_AGENT_V1] the shared-RAM arm's per-node step on the TIER 4 bed, run inside the node's
namespace. It is the tunnel daemon's region loop (poll.py `_one`) cut to what routing needs, built from FrogNet's own
modules, not a re-implementation:

  start      region.register_node()            this node's facts (role, name, guid, subnet, pubkey, pond)
  each event region.events()                   one held fdf-events session: "ready", then "p"/"m" per change. No timer.
             region.participants()             the members and their facts, from the memory
             region.my_channels()              (a node with a key) the hub channel, once the hub says it holds my key;
                                               brought up here as the daemon's bring-up does: wireguard-go, setconf,
                                               edge address, up                  <- the one step standing in for the daemon
             graph_apply.Applier.apply()       FD&F's plan (fdf_graph) into this kernel, by diff, with hold-down
             region.publish_plan_maps()        the plan's maps toward the live hub; fdf-client signs and registers them
  gone       the region's session ended: applied with the positive failure "region" (withdrawn now), the hub tunnel
             torn down, and the events session started again 5 s later -- the daemon's own loss path and rejoin

HANDSHAKE_DEAD_SEC is FrogNet's own (600 s); HOLD_DOWN_SEC is graph_apply's own (60 s) unless S4_HOLD_DOWN says
otherwise (reported with the run when it does).

env: S4_ME, S4_GUID, S4_WG (wg iface here; "" = no tunnel), S4_WG_KEY, FROGNET_DIR, FDF_PY, FDF_BIN, FDF_FACTS,
     RINA_RAM, RINA_AREA, FROGNET_REGION_HOSTS, FROGNET_GEN, FROGNET_WORLD_GEN
"""
import json
import logging
import os
import subprocess
import sys
import time

sys.path.insert(0, os.environ["FROGNET_DIR"])
sys.path.insert(0, os.environ.get("FDF_PY", "/opt/fdf/lib/python"))
import internet_tunnels_v3.config as c          # noqa: E402

ME = os.environ["S4_ME"]
WG = os.environ.get("S4_WG", "")
c.REGION_RAM = os.environ["RINA_RAM"]
c.REGION_AREA = os.environ["RINA_AREA"]
c.NODE_NAME = ME
c.NODE_GUID = os.environ.get("S4_GUID", "g-" + ME)
c.POND = os.environ["RINA_AREA"]
c.HANDSHAKE_DEAD_SEC = 600
c.PUBKEY = ""
if WG:
    c.PUBKEY = subprocess.run(["wg", "pubkey"], input=open(os.environ["S4_WG_KEY"]).read(), capture_output=True,
                              text=True).stdout.strip()
logging.basicConfig(level=logging.WARNING, format="%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S",
                    stream=sys.stdout)
c.log = logging.getLogger("s4-ram")
from internet_tunnels_v3 import region, graph_apply          # noqa: E402
from internet_tunnels_v3.graph_apply import Applier, World   # noqa: E402
if os.environ.get("S4_HOLD_DOWN"):
    graph_apply.HOLD_DOWN_SEC = float(os.environ["S4_HOLD_DOWN"])
c.LOCAL_SUBNET = region.own_subnet()


class W(World):
    def hub_alive(self, iface):
        """FrogNet's rule (wg.wg_handshake_age < HANDSHAKE_DEAD_SEC), read from `wg show` -- wg.py itself pulls in the
        daemon's tracing and config modules."""
        r = subprocess.run(["wg", "show", iface, "latest-handshakes"], capture_output=True, text=True)
        ts = [int(x.split()[1]) for x in r.stdout.splitlines() if len(x.split()) == 2 and x.split()[1] != "0"]
        return bool(ts) and time.time() - max(ts) < c.HANDSHAKE_DEAD_SEC


def bring_up():
    """The hub channel, as the daemon's bring-up makes it: wireguard-go, the config region.my_channels() gives, the
    edge address, up. Returns the hub's name, or "" if no hub holds my key yet."""
    ch = region.my_channels()["channels"]
    if not ch:
        return ""
    w = ch[0]["wg_config"]
    conf = os.path.join(os.path.dirname(os.environ["S4_WG_KEY"]), "wg.conf")
    with open(conf, "w") as f:
        f.write("[Interface]\nPrivateKey = %s\n\n[Peer]\nPublicKey = %s\nEndpoint = %s\nAllowedIPs = %s\n"
                "PersistentKeepalive = 25\n" % (open(os.environ["S4_WG_KEY"]).read().strip(), w["droplet_pubkey"],
                                                w["droplet_endpoint"], w["allowed_ips"]))
    if subprocess.run(["ip", "link", "show", WG], capture_output=True).returncode != 0:
        subprocess.run(["wireguard-go", WG], check=True, capture_output=True)
    subprocess.run(["wg", "setconf", WG, conf], check=True)
    subprocess.run(["ip", "addr", "replace", "%s/%s" % (w["edge_ip"], w["edge_mask"]), "dev", WG], check=True)
    subprocess.run(["ip", "link", "set", WG, "up"], check=True)
    c.log.warning("S4-RAM: hub channel to %s up on %s (edge %s)", ch[0]["node_name"], WG, w["edge_ip"])
    return ch[0]["node_name"]


def main():
    region.register_node()
    ap = Applier(W(config=c, log=c.log), ME)
    hub = ""
    while True:
        for ev in region.events():
            t0 = time.monotonic()
            if ev == "gone":
                out = ap.apply([], positive_failures={"region"})
                if WG and subprocess.run(["ip", "link", "show", WG], capture_output=True).returncode == 0:
                    subprocess.run(["ip", "link", "del", WG], capture_output=True)   # no region, no tunnel (the daemon's rule)
                    hub = ""
                c.log.warning("S4-RAM: region gone: withdrawn routes=%d, tunnel torn down; rejoin in 5 s",
                              len(out["routes_removed"]))
                break
            parts = region.participants()
            if WG and not hub:
                hub = bring_up()
            out = ap.apply(parts)
            c.live_hubs = {hub: None} if hub else {}
            maps = out.get("maps", [])
            if hub and out.get("own"):                       # [OWN_MAP_WHILE_TUNNEL_UP_V1], as the daemon does
                maps = sorted(set(maps) | set(out["own"]))
            if out.get("maps_changed") or (maps and not (region.read_facts().get("maps") or [])):
                region.publish_plan_maps(maps)
            c.log.warning("S4-RAM: event %s members=%d +%d/-%d held=%d hub=%s %d ms", ev,
                          sum(1 for p in parts if p.get("alive")), len(out["routes_added"]), len(out["routes_removed"]),
                          len(out["held"]), hub or "-", int((time.monotonic() - t0) * 1000))
        time.sleep(5)                                        # [REJOIN_EVERY_5S_V1], as the daemon does


if __name__ == "__main__":
    main()
