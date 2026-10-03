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
oracle_exit_gw_dot1.py — [LAN_GW_DOT1_V1] applies to the exit ladder too.

Seattle3, 2026-08-13:

    PURGE decision=delete reason=lan_gw_not_dot1 via=10.250.250.221 metric=601
    PURGE decision=delete reason=lan_gw_not_dot1 via=10.250.250.221 metric=602
    ...
    EXIT_INSTALL role=primary default via=10.250.250.221 metric=601
    EXIT_INSTALL role=backup  default via=10.250.250.221 metric=602

purge_dead_defaults deleted the bad defaults; choose_exit_hosts put the same
address straight back, because nh comes from route_get() — whatever the kernel
routes toward the exit host — and that was Seattle6's DHCP lease .221, a client
address on Seattle5's segment, not a gateway. The correct next hop is
10.250.250.1.

Downstream: manageResolv/live take the default route's gateway as a WAN
nameserver, .221 answers nothing on 53, and the node cannot resolve.

Red on the running tree, green on the fix.

Run:  python3 oracle_exit_gw_dot1.py [/path/to/discovery/parent]
"""
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/opt/frognet_semantic"
sys.path.insert(0, ROOT)
for m in [k for k in sys.modules if k.startswith("discovery")]:
    del sys.modules[m]

from discovery.fixdefault import FixDefaultRoute   # noqa: E402

FAILS = []


def ck(label, ok, detail=""):
    print(("  ok: " if ok else "  FAIL ") + label + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


class FakeKernel:
    def __init__(self):
        self.calls = []

    def route(self, *args):
        self.calls.append(list(args))
        return 0

    def show_default(self):
        return []


# The Seattle3 picture: peers reachable, but the kernel's next hop toward the
# far exits is .221 (Seattle6's lease) while .1 (Seattle5) is the real gateway.
ROUTE_GET = {
    "10.102.60.1":  ("wlan1", "10.250.250.221"),
    "10.102.60.2":  ("wlan1", "10.250.250.221"),
    "10.155.155.1": ("wlan1", "10.250.250.1"),
    "10.160.160.1": ("wlan1", "10.250.250.1"),
}


def build(**kw):
    f = FixDefaultRoute(
        FakeKernel(),
        dev_ip4={"wlan1": "10.250.250.191", "wlan0": "10.130.130.1"},
        up_devs=["wlan0", "wlan1"],
        connected_prefixes={"wlan1": {"10.250.250"}, "wlan0": {"10.130.130"}},
        ping=lambda ip, dev=None: True,
        frognet_interfaces=["wlan0", "wlan1"],
        local_ips={"10.130.130.1", "10.130.130.2", "10.250.250.191"},
        forced_line=None,
        logger=kw.get("logger", lambda *_a: None),
        route_get=lambda h: ROUTE_GET.get(h),
        # _exit_json_usable wants ok + exit_present + a live ts/ttl window.
        get_default_route=lambda h: {"ok": True, "exit_present": True,
                                     "ts": int(__import__("time").time()),
                                     "ttl_sec": 60},
        known_hosts=lambda: list(ROUTE_GET.keys()),
        semantic_cidrs=set(),
    )
    return f


print("=== choose_exit_hosts must not return a non-.1 LAN next hop ===")
logs = []
f = build(logger=logs.append)
try:
    exits = f.choose_exit_hosts()
except Exception as e:                                    # noqa: BLE001
    exits = []
    print(f"  (choose_exit_hosts raised {type(e).__name__}: {e})")

nhs = sorted({nh for (_h, _d, nh, _k) in exits})
ck("no exit candidate uses 10.250.250.221 (a DHCP lease, not a gateway)",
   "10.250.250.221" not in nhs, f"nhs={nhs}")
ck("the real gateway 10.250.250.1 is still a candidate",
   "10.250.250.1" in nhs, f"nhs={nhs}")
ck("every 10.x next hop is its own /24's .1",
   all(nh.rsplit(".", 1)[0] + ".1" == nh for nh in nhs if nh.startswith("10.")),
   f"nhs={nhs}")

print("=== and the ladder never installs one ===")
k = FakeKernel()
f2 = build()
f2.k = k
if exits:
    f2.install_exit_defaults(exits)
installed = [c[c.index("via") + 1] for c in k.calls
             if "via" in c and "default" in c]
ck("no default installed via 10.250.250.221",
   "10.250.250.221" not in installed, f"installed={installed}")

print()
print("RESULT: " + ("PASS" if not FAILS else f"FAIL ({len(FAILS)})"))
sys.exit(1 if FAILS else 0)
