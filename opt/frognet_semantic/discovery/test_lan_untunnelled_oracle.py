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
"""
test_lan_untunnelled_oracle.py -- a node is on the LAN if reaching it does not
traverse a tunnel and it is not a loopback.

Drives the REAL is_on_lan / gather_candidates. Hop lists are stubbed; nothing else is.

The two rules this replaces, and why each failed on the live pond of 2026-08-01:

  "is its /24 one of my attached segments"   [LAN_IS_ATTACHED_V1]
      local_subnets is every address the node holds, including the DHCP client lease
      pointing UP at its parent. Seattle2 holds 10.160.160.47 on Seattle6's segment,
      so it called Seattle6 LAN and elected it. A lease pointing up is not LAN
      membership.

  "is it absent from my own WAN plane"       (the legacy fallback)
      Seattle6 and Seattle2 own no wg interface at all, so REACH_PLANE_TUPLE wrote
      wan=[] on both. Everything is absent from an empty plane, so the whole pond
      looks LAN -- including New-York-1 at 71.762, which would then win the media
      election on Seattle6.

Both are answered by looking at the path.

Run: python3 test_lan_untunnelled_oracle.py
"""
import sys
import time

sys.path.insert(0, "/opt/frognet_semantic")

from core import frognet_role_elect as RE

_p = _f = 0


def ck(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, extra))


# -- the pond, as `ip -4 -o addr show` reported it ---------------------------
LOCAL = {
    "seattle5": ["10.250.250.1", "10.250.250.2", "192.168.0.19", "10.254.0.5",
                 "10.253.200.30", "10.253.200.38", "10.253.201.14", "10.253.201.70",
                 "10.253.201.102"],
    "seattle6": ["10.160.160.1", "10.160.160.2", "10.250.250.221"],
    "seattle2": ["10.120.120.1", "10.120.120.2", "10.160.160.47"],
}

# Paths, as the chain Seattle5 -> Seattle6 -> Seattle2 makes them. Anything off the
# chain leaves through Seattle5's wg, so a 10.253 hop appears.
HOPS = {
    "seattle5": {
        "10.250.250.1": ["10.250.250.1"],
        "10.160.160.1": ["10.250.250.221", "10.160.160.1"],
        "10.120.120.1": ["10.250.250.221", "10.160.160.47", "10.120.120.1"],
        "10.102.60.1": ["10.253.200.30", "10.102.60.1"],
        "10.130.130.1": ["10.253.201.14", "10.130.130.1"],
        "10.199.199.1": ["10.253.201.70", "10.199.199.1"],
    },
    "seattle6": {
        "10.160.160.1": ["10.160.160.1"],
        "10.250.250.1": ["10.250.250.1"],
        "10.120.120.1": ["10.160.160.47", "10.120.120.1"],
        "10.102.60.1": ["10.250.250.1", "10.253.200.30", "10.102.60.1"],
        "10.130.130.1": ["10.250.250.1", "10.253.201.14", "10.130.130.1"],
    },
    "seattle2": {
        "10.120.120.1": ["10.120.120.1"],
        "10.160.160.1": ["10.160.160.1"],
        "10.250.250.1": ["10.160.160.1", "10.250.250.1"],
        "10.102.60.1": ["10.160.160.1", "10.250.250.1", "10.253.200.30", "10.102.60.1"],
        "10.130.130.1": ["10.160.160.1", "10.250.250.1", "10.253.201.14", "10.130.130.1"],
    },
}


def lan(node, ip):
    return RE.is_on_lan(ip, local_ips=LOCAL[node], hops=HOPS[node].get(ip))


# =============================================================================
print("-- a node is on its own LAN, and is never traced to prove it ------------")
# [SELF_IS_ALWAYS_LAN_V1] Seattle3, 2026-08-01: it had to traceroute its own .1 to be
# its own candidate. With no traceroute binary every candidate INCLUDING ITSELF came
# back False, the LAN pool emptied, no media host was elected, and it reported no
# media host on a LAN it is the only member of.
_real_trace = RE.trace_hops
RE.trace_hops = lambda ip, **kw: None          # no traceroute on this box at all
ck("self is on its own LAN with no traceroute at all",
   RE.is_on_lan("10.130.130.1", local_ips=["10.130.130.1", "10.130.130.2"]) is True)
ck("a secondary address on the same box counts as self",
   RE.is_on_lan("10.130.130.2", local_ips=["10.130.130.1", "10.130.130.2"]) is True)
ck("but nobody ELSE becomes local for want of a probe",
   RE.is_on_lan("10.250.250.1", local_ips=["10.130.130.1"]) is False)
ck("a lone node therefore still elects itself",
   RE.is_on_lan("10.130.130.1", local_ips=["10.130.130.1"]) is True)
RE.trace_hops = _real_trace
ck("and with traceroute present self is still not probed",
   RE.is_on_lan("10.130.130.1", local_ips=["10.130.130.1"], hops=None) is True)


print("-- the rule ------------------------------------------------------------")
ck("a direct neighbour is LAN", lan("seattle6", "10.120.120.1") is True)
ck("a path crossing a 10.253 hop is not LAN", lan("seattle6", "10.102.60.1") is False)
ck("the tunnel hop counts wherever it appears in the path",
   lan("seattle2", "10.102.60.1") is False)
ck("a loopback through our own address is not LAN",
   RE.is_on_lan("10.9.9.1", local_ips=LOCAL["seattle6"],
                hops=["10.160.160.1", "10.9.9.1"]) is False)
ck("no path, no LAN -- there is no third answer",
   RE.is_on_lan("10.9.9.1", local_ips=[], hops=[]) is False)


# =============================================================================
print("-- Seattle6: owns no tunnel, so its own WAN plane is empty --------------")
ck("Seattle5 is LAN to it -- one hop on wlan1, no tunnel",
   lan("seattle6", "10.250.250.1") is True)
ck("Seattle2 is LAN to it", lan("seattle6", "10.120.120.1") is True)
ck("New-York-1 is NOT LAN, though its own wan plane is empty",
   lan("seattle6", "10.102.60.1") is False)
ck("Seattle3 is NOT LAN either", lan("seattle6", "10.130.130.1") is False)

# CONTROL -- the legacy test against Seattle6's actual reach_plane (wan=[]).
wan_plane = set()          # REACH_PLANE_TUPLE wrote wan=[] on seattle6.log:471
ck("CONTROL: 'absent from my WAN plane' called New-York-1 LAN",
   RE._slash24("10.102.60.1") not in wan_plane)
ck("the WAN plane is not consulted at all now",
   RE.is_on_lan("10.102.60.1", local_ips=LOCAL["seattle6"],
                hops=HOPS["seattle6"]["10.102.60.1"]) is False)
ck("the path test disagrees, and it is right",
   lan("seattle6", "10.102.60.1") is False)


# =============================================================================
print("-- Seattle2: its lease points UP at Seattle6 ----------------------------")
ck("Seattle6 is LAN to Seattle2 -- one hop, no tunnel",
   lan("seattle2", "10.160.160.1") is True)
ck("Seattle5 is LAN to Seattle2 -- two hops, still no tunnel",
   lan("seattle2", "10.250.250.1") is True)

# CONTROL -- the attached-segment test, from Seattle2's real local_subnets.
attached = {"10.120.120.0/24", "10.160.160.0/24", "10.254.0.0/24"}
ck("CONTROL: the attached test called Seattle6 LAN via a lease pointing up",
   RE._slash24("10.160.160.1") in attached)
ck("CONTROL: and it could not see Seattle5 at all",
   RE._slash24("10.250.250.1") not in attached)
ck("the path test sees both", lan("seattle2", "10.160.160.1") is True
   and lan("seattle2", "10.250.250.1") is True)


# =============================================================================
print("-- Seattle5: the gateway -----------------------------------------------")
ck("both children are LAN",
   lan("seattle5", "10.160.160.1") is True and lan("seattle5", "10.120.120.1") is True)
for far in ("10.102.60.1", "10.130.130.1", "10.199.199.1"):
    ck("%s is behind a tunnel" % far, lan("seattle5", far) is False)


# =============================================================================
print("-- through gather_candidates -------------------------------------------")
NOW = int(time.time())
SCORE = {"10.102.60.1": 71.762, "10.120.120.1": 32.411, "10.130.130.1": 71.762,
         "10.160.160.1": 69.047, "10.250.250.1": 69.277}


def rows(*ips):
    return [{"SensorName": "SD:capability.host:%s:mediahost" % ip,
             "SensorAddress": ip, "UpdatedAtEpoch": NOW - 5,
             "data": {"ts": NOW - 5,
                      "capability": {"lan_ip": ip, "score_hint": SCORE[ip]}}}
            for ip in ips]


ALL = ("10.102.60.1", "10.120.120.1", "10.130.130.1", "10.160.160.1", "10.250.250.1")


class Handler:
    ROLE_NAME = "mediahost"

    def score(self, c):
        return c.get("score_hint", 0.0)

    def evaluate(self, hosts_list, lan_list):
        pool = lan_list or []
        return max(pool, key=lambda c: c.get("score_hint", 0.0)) if pool else None


h = Handler()
RE.T._values_raw = lambda service, dbhost=None, name_like=None, timeout=4.0, fresh_s=0: rows(*ALL)
RE._wan_subnets = lambda dbhost: set()

for node, expect in (("seattle6", "10.250.250.1"),
                     ("seattle2", "10.250.250.1"),
                     ("seattle5", "10.250.250.1")):
    RE.trace_hops = (lambda n: (lambda ip, **kw: HOPS[n].get(ip)))(node)
    hosts, lan_list = RE.gather_candidates(h, dbhost="10.250.250.1",
                                           local_ips=LOCAL[node])
    ips = sorted(c["lan_ip"] for c in lan_list)
    ck("%s: no tunnelled node in its LAN pool" % node,
       "10.102.60.1" not in ips and "10.130.130.1" not in ips, ips)
    ck("%s: elects %s" % (node, expect),
       h.evaluate(hosts, lan_list)["lan_ip"] == expect,
       h.evaluate(hosts, lan_list)["lan_ip"])


# =============================================================================
print("-- no path, no LAN ------------------------------------------------------")
# Seattle2 with no route to Seattle3: the trace yields nothing, so Seattle3 is not on
# its LAN. There is no second test consulted here. An earlier cut fell back to
# "absent from my WAN plane" and, because a tunnel-less node's plane is empty,
# Seattle3 at 71.762 won the media election on a box that could not trace to it.
RE.trace_hops = lambda ip, **kw: (HOPS["seattle2"].get(ip)
                                  if ip != "10.130.130.1" else None)
hosts, lan_list = RE.gather_candidates(h, dbhost="10.250.250.1",
                                       local_ips=LOCAL["seattle2"])
ips = sorted(c["lan_ip"] for c in lan_list)
ck("a candidate whose path could not be traced is not on the LAN",
   "10.130.130.1" not in ips, ips)
ck("and the election lands on a node that IS reachable without a tunnel",
   h.evaluate(hosts, lan_list)["lan_ip"] == "10.250.250.1",
   h.evaluate(hosts, lan_list)["lan_ip"])


print()
print("=== %d passed, %d failed ===" % (_p, _f))
print("ORACLE " + ("GREEN" if _f == 0 else "RED"))
sys.exit(0 if _f == 0 else 1)
