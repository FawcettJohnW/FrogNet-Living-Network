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
oracle_self_identity_lines.py

Two rules, pinned on the python merge path (discovery/hosts.py + resolv.py):

  [FROGNETHOST_IS_NOT_LOOPBACK_V1]
      /etc/hosts must NOT carry the bare alias "FrogNetHost" on the 127.0.0.1
      line when a real 10.x line owns it. The loopback line sorts first and
      resolvers take the first match, so the bare name resolved to 127.0.0.1
      and a peer aiming at "FrogNetHost" reached itself.

  [SELF_IP_IS_A_RESOLVER_V1]
      /etc/resolv.conf must list this node's own address immediately after
      nameserver 127.0.0.1 — before any peer and before the WAN gateway.

Red on the running tree, green on the patched one.

Run:  python3 oracle_self_identity_lines.py [/path/to/discovery/parent]
"""
import sys, os, importlib

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/opt/frognet_semantic"
sys.path.insert(0, ROOT)
for m in [k for k in sys.modules if k.startswith("discovery")]:
    del sys.modules[m]

from discovery.hosts import build_etc_hosts          # noqa: E402
from discovery.resolv import build_resolv            # noqa: E402

FAILS = []


def ck(label, ok, detail=""):
    if ok:
        print(f"  ok: {label}")
    else:
        print(f"  FAIL {label}{(' — ' + detail) if detail else ''}")
        FAILS.append(label)


SELF = "10.160.160.1"
FROGNET_HOSTS = [
    f"{SELF} FrogNetHost.Seattle6",
    f"{SELF} FrogNetAdmin.Seattle6",
    "10.250.250.1 FrogNetHost.Seattle5",
    "10.250.250.2 FrogNetAdmin.Seattle5",
    "10.102.60.1 FrogNetHost.New-York-1",
]

print("=== /etc/hosts: the bare alias belongs to the real address ===")
etc = build_etc_hosts(FROGNET_HOSTS, self_ip=SELF)

loop = [l for l in etc if l.startswith("127.0.0.1")]
ck("a 127.0.0.1 line is still present", len(loop) == 1, repr(loop))
ck("127.0.0.1 does NOT claim the bare alias",
   all(l.split()[2:] == [] or "FrogNetHost" not in l.split()[2:] for l in loop),
   repr(loop))
ck("127.0.0.1 still maps localhost",
   any(l.split()[1:2] == ["localhost"] for l in loop), repr(loop))

self_lines = [l for l in etc if l.startswith(SELF + " ")]
ck("the self line carries the bare alias",
   any(l.split()[-1] == "FrogNetHost" for l in self_lines), repr(self_lines))

# The whole point: first match for the bare name must be the real address.
first = next((l for l in etc
              if "FrogNetHost" in l.split()[1:] and not l.startswith("#")), "")
ck("first line owning the bare alias is the real address, not loopback",
   first.startswith(SELF), repr(first))

# A peer's line must never gain the alias.
peer_lines = [l for l in etc if l.startswith("10.250.250.1 ")]
ck("a peer never gets the bare alias",
   all(l.split()[-1] != "FrogNetHost" for l in peer_lines), repr(peer_lines))

print("=== /etc/resolv.conf: our own address is second ===")
res = build_resolv("Seattle6", "FrogNetHost.Seattle6",
                   ["10.250.250.1", "172.16.26.1"], self_ip=SELF)
ns = [l.split()[1] for l in res if l.startswith("nameserver ")]
ck("127.0.0.1 is first", ns[:1] == ["127.0.0.1"], repr(ns))
ck("our own address is immediately second", ns[1:2] == [SELF], repr(ns))
ck("peers and WAN follow it", ns[2:] == ["10.250.250.1", "172.16.26.1"], repr(ns))
ck("listed exactly once", ns.count(SELF) == 1, repr(ns))

# Degenerate inputs must not invent a line.
res2 = build_resolv("Seattle6", "FrogNetHost.Seattle6", ["172.16.26.1"], self_ip="")
ns2 = [l.split()[1] for l in res2 if l.startswith("nameserver ")]
ck("no self_ip -> nothing invented", ns2 == ["127.0.0.1", "172.16.26.1"], repr(ns2))

res3 = build_resolv("Seattle6", "FrogNetHost.Seattle6", [], self_ip="127.0.0.1")
ns3 = [l.split()[1] for l in res3 if l.startswith("nameserver ")]
ck("a loopback self_ip is not duplicated", ns3 == ["127.0.0.1"], repr(ns3))

print()
print("RESULT: " + ("PASS" if not FAILS else f"FAIL ({len(FAILS)})"))
sys.exit(1 if FAILS else 0)
