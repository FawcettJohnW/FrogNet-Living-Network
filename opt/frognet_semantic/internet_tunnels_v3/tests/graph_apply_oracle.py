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
"""[GRAPH_APPLY_ORACLE_V1] PLAN-RAM-DISCOVERY §6 cases 4-5 and the admission/failure contracts, against a fake world.
Run: python3 internet_tunnels_v3/tests/graph_apply_oracle.py   (from /opt/frognet_semantic)"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import tempfile as _tf
_d = _tf.mkdtemp()                                                      # [GEN_V1] the counter and facts stay out of /var
os.environ["FROGNET_GEN"] = _d + "/gen"; os.environ["FROGNET_WORLD_GEN"] = _d + "/world"; os.environ["FDF_FACTS"] = _d + "/facts.json"
os.environ.setdefault("FDF_PY", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "fdf", "lib", "python"))
sys.path.insert(0, os.environ["FDF_PY"])
from internet_tunnels_v3 import graph_apply
from internet_tunnels_v3.graph_apply import Applier, World
from fdf_graph_oracle import T1, host, own, held, given, uplink, hub, HUB, MY, MX

FAIL = 0


def check(name, got, want):
    global FAIL
    if got == want:
        print("ok   ", name)
    else:
        FAIL += 1
        print("FAIL ", name, "\n   got ", got, "\n   want", want)


class Fake(World):
    def __init__(self):
        super().__init__()
        self.t = 1000.0
        self.kernel = set()
        self.default = None
        self.files = {}
        self.writes = 0
        self.probes = []
        self.probe_ok = True
        self.hub_ok = True
        self.said = []

    def routes(self): return set(self.kernel)
    def route_replace(self, dest, via, dev, metric=22):
        self.kernel.add((dest, via, dev)); return True
    def route_del(self, dest, via, dev, metric=22):
        self.kernel.discard((dest, via, dev)); return True
    def default_set(self, via, dev): self.default = (via, dev); return True
    def has_default(self): return self.default is not None or getattr(self, "dhcp_default", False)
    def default_del(self): self.default = None; return True
    def probe(self, dot1, via, dev):
        self.probes.append((dot1, via, dev)); return self.probe_ok
    def hub_alive(self, iface): return self.hub_ok
    def write_if_changed(self, path, content):
        if self.files.get(path) == content:
            return False
        self.files[path] = content
        if not path.endswith(".json"): self.writes += 1          # the hosts sentinel; <sentinel>.json is checked by content
        return True
    def now(self): return self.t
    def say(self, msg): self.said.append(msg)


# ---------------------------------------------------------------- z applies T1
w = Fake()
ap = Applier(w, "z", hosts_sentinel="/sent/frognet_hosts")
c = ap.apply(T1)
check("z: LAN routes installed at once (their members are alive), hub routes too (handshake is their gate)",
      sorted(w.kernel), sorted([("10.2.2.0/24", "10.3.3.20", "wlan0"), ("10.1.1.0/24", "10.3.3.20", "wlan0"),
                                ("10.7.7.0/24", "", "wg0"), ("10.8.8.0/24", "", "wg0"), ("10.9.9.0/24", "", "wg0")]))
check("z: no echo probe is sent: alive is the session [ALIVE_IS_THE_SESSION_V1]", w.probes, [])
check("z: no default (own uplink)", w.default, None)
check("z: hosts sentinel written once, ip name per line", (w.writes, w.files["/sent/frognet_hosts"].splitlines()[:2]),
      (1, ["10.1.1.1 FrogNetHost.x", "10.2.2.1 FrogNetHost.y"]))
import json as _j
_hj = _j.loads(w.files.get("/sent/frognet_hosts.json", "[]"))
check("z: hosts json (getHosts.php's input) = the hosts sentinel's hosts (z included), echo from each member's facts [HOSTJSON_FROM_THE_REGION_V1]",
      ([h["ip"] for h in _hj], [h["echo"] for h in _hj if h["ip"] in ("10.2.2.1", "10.3.3.1")]),
      ([l.split()[0] for l in w.files["/sent/frognet_hosts"].splitlines()],
       ["FrogNetHost.y,10.2.2.1,0.0.0.0,0.0.0.0", "FrogNetHost.z,10.3.3.1,192.168.0.33,0.0.0.0"]))   # no "name" in the fixture facts: the hosts name stands in; fields 3-4 = uplink rows [ECHO_UPSTREAM_FROM_THE_ROWS_V1]
check("z: applied flag", c["applied"], True)
# [DEVICE_ROW_V1] a leaf (a phone: facts.addr, no rows) is in the hosts json with role=leaf and alive=true, the nodes
# with role=node; the monitor draws the leaf as "<name> DEVICE ONLINE"
_wl = Fake(); _apl = Applier(_wl, "z", hosts_sentinel="/sent/frognet_hosts")
_apl.apply(T1 + [{"name": "phone", "alive": True, "facts": {"role": "leaf", "name": "phone", "pubkey": "p", "addr": "10.252.0.1/32"}}])
_hl = {h["ip"]: h for h in _j.loads(_wl.files.get("/sent/frognet_hosts.json", "[]"))}
check("z: hosts json carries the leaf with role leaf, alive, its echo; nodes are role node [DEVICE_ROW_V1]",
      (_hl.get("10.252.0.1", {}).get("role"), _hl.get("10.252.0.1", {}).get("alive"), _hl.get("10.252.0.1", {}).get("echo"), _hl.get("10.2.2.1", {}).get("role")),
      ("leaf", True, "phone,10.252.0.1,0.0.0.0,0.0.0.0", "node"))
_apg = Applier(Fake(), "z", hosts_sentinel="/sent/frognet_hosts")
_apg.apply(T1 + [{"name": "phone", "alive": True, "facts": {"role": "leaf", "name": "phone", "guid": "g-phone", "pubkey": "p", "addr": "10.252.0.1/32"}}])
_hg = {h["ip"]: h for h in _j.loads(_apg.w.files.get("/sent/frognet_hosts.json", "[]"))}
check("z: hosts json carries each member's guid from its facts ('' when its facts name none) [ONE_MONITOR_TRUTH_V1]",
      (_hg.get("10.252.0.1", {}).get("guid"), "guid" in _hg.get("10.2.2.1", {})), ("g-phone", True))
check("z: maps for the hub", c["maps"], ["10.1.1.0/24", "10.2.2.0/24", "10.3.3.0/24"])

# ---------------------------------------------------------------- 5. same table again -> nothing happens
probes_before = len(w.probes)
c = ap.apply(T1)
check("5 no change: nothing applied, nothing written, nothing probed", (c["applied"], w.writes, len(w.probes) - probes_before), (False, 1, 0))

# ---------------------------------------------------------------- 4. hold-down: x's row vanishes and returns within 60 s
T1_no_x = [h for h in T1 if h["name"] != "x"]
w.t += 5
c = ap.apply(T1_no_x)
check("4 x vanished: route and name HELD, not removed", (("10.1.1.0/24", "10.3.3.20", "wlan0") in w.kernel, "10.1.1.1" in ap.applied_hosts, c["applied"]), (True, True, False))
check("4 held list names it", sorted(c["held"]), [("host", "10.1.1.1"), ("route", ("10.1.1.0/24", "10.3.3.20", "wlan0"))])
w.t += 30
c = ap.apply(T1)                                   # back within the window
check("4 x returned within hold-down: still there, nothing re-probed, nothing applied", (("10.1.1.0/24", "10.3.3.20", "wlan0") in w.kernel, c["applied"], len(w.probes) - probes_before), (True, False, 0))
w.t += 5
ap.apply(T1_no_x)
w.t += 61
c = ap.apply(T1_no_x)                              # gone for good
check("4 x gone past hold-down: route and name removed once", (("10.1.1.0/24", "10.3.3.20", "wlan0") in w.kernel, "10.1.1.1" in ap.applied_hosts, c["routes_removed"], c["hosts_removed"]),
      (False, False, [("10.1.1.0/24", "10.3.3.20", "wlan0")], [("10.1.1.1", "FrogNetHost.x")]))
check("4 hosts sentinel rewritten exactly once for the removal", w.writes, 2)
c = ap.apply(T1_no_x)
check("4 steady again", c["applied"], False)

# ---------------------------------------------------------------- [ALIVE_IS_THE_SESSION_V1] a next hop that would not echo changes nothing
w2 = Fake(); w2.probe_ok = False
ap2 = Applier(w2, "z", hosts_sentinel="/sent/h")
c = ap2.apply(T1)
check("no probe: LAN routes and names installed from the region alone, nothing probed",
      (sorted(r for r in w2.kernel if r[1]), w2.probes),
      (sorted([("10.2.2.0/24", "10.3.3.20", "wlan0"), ("10.1.1.0/24", "10.3.3.20", "wlan0")]), []))
w2.probe_ok = True
c = ap2.apply(T1)

# ---------------------------------------------------------------- positive failure: hub handshake dead -> hub routes gone NOW, LAN routes untouched
w2.hub_ok = False
c = ap2.apply(T1)
check("hub dead: hub routes removed at once (no hold-down), LAN routes kept", (sorted(r for r in w2.kernel if not r[1]), len([r for r in w2.kernel if r[1]])), ([], 2))
w2.hub_ok = True
c = ap2.apply(T1)
check("hub back: hub routes return", len([r for r in w2.kernel if not r[1]]), 3)

# ---------------------------------------------------------------- region gone -> everything withdrawn now
c = ap2.apply([], positive_failures={"region"})
check("region gone: all routes and names withdrawn now", (w2.kernel, ap2.applied_hosts, w2.default), (set(), {}, None))

# ---------------------------------------------------------------- x (a child) gets a default route, and loses it after hold-down when z leaves
w3 = Fake()
ap3 = Applier(w3, "x", hosts_sentinel="/sent/h")
ap3.apply(T1)
check("x default set toward y's .1", w3.default, ("10.2.2.1", "wlan1"))
T1_no_z = [h for h in T1 if h["name"] != "z"]
ap3.apply(T1_no_z)
check("z vanished: x's default held", w3.default, ("10.2.2.1", "wlan1"))
w3.t += 61
ap3.apply(T1_no_z)
check("z gone for good: x's default removed", w3.default, None)

# ---------------------------------------------------------------- a child with a DHCP default already: the applier leaves it to FrogNet's ladder
w4 = Fake(); w4.dhcp_default = True
ap4 = Applier(w4, "x", hosts_sentinel="/sent/h")
c = ap4.apply(T1)
check("x with an existing default: applier sets none (FrogNet's exit ladder owns it)", (w4.default, c["default"]), (None, None))
check("x still gets its explicit remote routes via y", sorted(w4.kernel), sorted([("10.3.3.0/24", "10.2.2.1", "wlan1"), ("10.7.7.0/24", "10.2.2.1", "wlan1"), ("10.8.8.0/24", "10.2.2.1", "wlan1"), ("10.9.9.0/24", "10.2.2.1", "wlan1")]))

# ---------------------------------------------------------------- membership transitions are reported at once, hold-down or not
w5 = Fake(); ap5 = Applier(w5, "z", hosts_sentinel="/sent/h")
ap5.apply(T1)
c = ap5.apply([h for h in T1 if h["name"] != "a"])
check("a went dead: reported now (its .1), while its route is merely held", (c["went"], ("10.7.7.0/24", "", "wg0") in w5.kernel), ([("a", "10.7.7.1")], True))
c = ap5.apply(T1)
check("a back: reported as came", c["came"], [("a", "10.7.7.1")])

# ---- [APPLIED_IS_THE_KERNEL_V1] a route the kernel dropped by itself (its interface went: the hub channel torn down
#      with this node's session and built again) is installed again on the next pass, not "remembered" as present
w6 = Fake(); ap6 = Applier(w6, "z", hosts_sentinel="/sent/h6")
ap6.apply(T1)
hub6 = {r for r in w6.kernel if r[1] == "" and r[2] == "wg0"}
check("kernel-lost: there are hub routes to lose", bool(hub6), True)
w6.kernel -= hub6                                           # wg0 deleted and recreated: the kernel took its routes with it
c = ap6.apply(T1)
check("kernel-lost: every one is installed again on the next pass", (hub6 <= w6.kernel, sorted(hub6) == sorted(r for r in c["routes_added"] if r in hub6)), (True, True))
check("kernel-lost: said", any("kernel no longer holds" in m for m in w6.said), True)
c = ap6.apply(T1)
check("kernel-lost control: nothing lost, nothing re-installed", c["routes_added"], [])

# ---- [GEN_V1] the counter moves on impact only
from internet_tunnels_v3 import gen as _gen
g0 = _gen.read()
wg = Fake(); apg = Applier(wg, "a", hosts_sentinel=os.path.join(_d, "hosts_g"))
c = apg.apply(T1)
check("gen: first rebuild bumps (member_came)", (_gen.read(), c.get("gen"), "member_came" in c["impact"]), (g0 + 1, g0 + 1, True))
c = apg.apply(T1)
check("gen: same world again does NOT bump", (_gen.read(), c["impact"], "gen" in c), (g0 + 1, [], False))
c = apg.apply([h for h in T1 if h["name"] != "z"])
check("gen: a member going is a bump", (_gen.read(), "member_went" in c["impact"]), (g0 + 2, True))
check("gen: GEN line says why and by", any(m.startswith("GEN: %d -> %d why=member_went by=applier" % (g0 + 1, g0 + 2)) for m in wg.said), True)
import json as _j
check("gen: announced as the gen fact", _j.load(open(os.environ["FDF_FACTS"]))["gen"], g0 + 2)

print("\n%s" % ("ALL OK" if not FAIL else "%d FAILED" % FAIL))
sys.exit(1 if FAIL else 0)
