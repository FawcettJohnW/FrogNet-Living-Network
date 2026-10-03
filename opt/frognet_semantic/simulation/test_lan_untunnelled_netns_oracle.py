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
test_lan_untunnelled_netns_oracle.py -- the LAN rule, over a REAL multi-LAN topology.

Namespaces, veth pairs, kernel forwarding and the real traceroute binary. No stubbed
hops: the stubs are what let a broken model pass. An earlier version of this fixture
put the tunnel's far-end /30 and the destination address on the SAME namespace, so the
packet reached its final destination at TTL 1, nothing expired a TTL, no router hop
appeared, and the rule looked broken when it was the fixture that was.

Reproduces the pond hop-for-hop, checked against real hardware output:

    s5 -> NY-1   10.253.200.29 -> 10.102.60.1
    s5 -> NY-2   10.253.200.29 -> 10.253.200.242 -> 10.28.28.1
    s5 -> S2     10.160.160.1  -> 10.120.120.1
    s5 -> S6     10.160.160.1

Needs root, ip and traceroute. SKIPs (exit 0) when they are absent rather than
pretending to have tested anything.

Run: sudo python3 test_lan_untunnelled_netns_oracle.py
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOPO = os.path.join(HERE, "netns_seattle_chain.sh")
NODES = ("s5", "s6", "s2", "nygw", "ny1", "ny2gw", "ny2")

LOCAL = {"s5": ["10.250.250.1", "10.253.200.30"],
         "s6": ["10.160.160.1", "10.250.250.221"],
         "s2": ["10.120.120.1", "10.160.160.47"]}
SEATTLE = ["10.250.250.1", "10.160.160.1", "10.120.120.1"]
TUNNELLED = ["10.102.60.1", "10.28.28.1"]
SCORE = {"10.250.250.1": 69.277, "10.160.160.1": 69.047, "10.120.120.1": 32.411,
         "10.102.60.1": 71.762, "10.28.28.1": 55.0}      # NY-1 is the pond's best box

_p = _f = 0


def ck(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, extra))


if os.geteuid() != 0 or not shutil.which("ip") or not shutil.which("traceroute"):
    print("SKIP: needs root, ip and traceroute")
    sys.exit(0)

subprocess.run(["bash", TOPO], check=True, capture_output=True)
try:
    probe = os.path.join(HERE, "_netns_probe.py")
    with open(probe, "w") as fh:
        fh.write('''import json, os, sys
sys.path.insert(0, "/opt/frognet_semantic")
os.environ["PATH"] = os.environ.get("PATH", "") + ":/usr/sbin"
from core import frognet_role_elect as RE
mine = json.loads(sys.argv[1]); out = {}
for d in json.loads(sys.argv[2]):
    out[d] = [RE.trace_hops(d, max_hops=8, wait=1),
              RE.is_on_lan(d, local_ips=mine)]
print(json.dumps(out))
''')
    import json
    result = {}
    for node in ("s5", "s6", "s2"):
        r = subprocess.run(["ip", "netns", "exec", node, sys.executable, probe,
                            json.dumps(LOCAL[node]),
                            json.dumps(SEATTLE + TUNNELLED)],
                           capture_output=True, text=True, timeout=180)
        result[node] = json.loads(r.stdout)

    print("-- the tunnel hop is visible where the pond says it is ----------------")
    hops_ny1 = result["s5"]["10.102.60.1"][0]
    ck("s5 -> NY-1 crosses 10.253.200.29",
       hops_ny1 and hops_ny1[0] == "10.253.200.29", hops_ny1)
    hops_ny2 = result["s5"]["10.28.28.1"][0]
    ck("s5 -> NY-2 crosses two transit hops",
       hops_ny2 and hops_ny2[:2] == ["10.253.200.29", "10.253.200.242"], hops_ny2)
    ck("a tunnel-less child still sees the transit hop",
       any(h.startswith("10.253.") for h in result["s6"]["10.102.60.1"][0] or []),
       result["s6"]["10.102.60.1"][0])
    ck("and so does its child, two hops down",
       any(h.startswith("10.253.") for h in result["s2"]["10.102.60.1"][0] or []),
       result["s2"]["10.102.60.1"][0])

    print("-- every node agrees on the same LAN ----------------------------------")
    for node in ("s5", "s6", "s2"):
        lan = sorted(d for d in SEATTLE + TUNNELLED if result[node][d][1])
        ck("%s: the LAN is the Seattle chain" % node, lan == sorted(SEATTLE), lan)
        ck("%s: nothing behind a tunnel is on it" % node,
           not any(result[node][d][1] for d in TUNNELLED))
        win = max(lan, key=lambda i: SCORE[i])
        ck("%s: elects 10.250.250.1" % node, win == "10.250.250.1", win)

    print("-- back-to-back traces are stable ------------------------------------")
    # UDP traceroute depends on the destination's port-unreachable, which Linux
    # rate-limits to 1/s per host, so repeated probes alternate clean/all-stars and
    # the LAN pool flaps. This pins the ICMP-echo mode that does not.
    rep = os.path.join(HERE, "_netns_rep.py")
    with open(rep, "w") as fh:
        fh.write('''import sys, os
sys.path.insert(0, "/opt/frognet_semantic")
os.environ["PATH"] = os.environ.get("PATH", "") + ":/usr/sbin"
from core import frognet_role_elect as RE
print(sum(1 for _ in range(5) if RE.is_on_lan("10.160.160.1",
      local_ips=["10.250.250.1"])))
''')
    r = subprocess.run(["ip", "netns", "exec", "s5", sys.executable, rep],
                       capture_output=True, text=True, timeout=180)
    ck("five consecutive traces to the same LAN box all succeed",
       r.stdout.strip() == "5", r.stdout.strip() + r.stderr[-200:])
    os.remove(rep)

    print("-- the pond's highest-scoring box does not win ------------------------")
    ck("NY-1 scores highest of all", max(SCORE, key=SCORE.get) == "10.102.60.1")
    ck("and is elected by nobody",
       all(not result[n]["10.102.60.1"][1] for n in ("s5", "s6", "s2")))
finally:
    for n in NODES:
        subprocess.run(["ip", "netns", "del", n], capture_output=True)
    try:
        os.remove(os.path.join(HERE, "_netns_probe.py"))
    except OSError:
        pass

print()
print("=== %d passed, %d failed ===" % (_p, _f))
print("ORACLE " + ("GREEN" if _f == 0 else "RED"))
sys.exit(0 if _f == 0 else 1)
