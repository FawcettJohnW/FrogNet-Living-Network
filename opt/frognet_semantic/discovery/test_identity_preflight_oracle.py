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
test_identity_preflight_oracle.py - decision table for the front-of-runMerge
identity gate (discovery.identity_preflight.decide). Catalogs the common ways a
node's identity interface gets misconfigured/down and the expected outcome:

  PROCEED  identity iface up and bearing .1/.2 (wired eth0 OR AP wlan0/1)
  FAIL     identity iface down, missing, or missing .1/.2  -> merge aborts loudly
  SOFT     soft stand-alone enabled -> synthesize the interface (the exception)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.identity_preflight import decide, PROCEED, FAIL, SOFT

CASES = [
    # name, kwargs, expected action
    ("wired eth0 up w/ .1+.2 (Seattle5 clean)",
     dict(mode="wired", soft=False, soft_ip="", ident_prefix="10.250.250",
          ident_iface="eth0", iface_up=True,
          iface_addrs={"10.250.250.1", "10.250.250.2"}), PROCEED),
    ("wired eth0 DOWN (cable pulled, no AP failover)",
     dict(mode="wired", soft=False, soft_ip="", ident_prefix="10.250.250",
          ident_iface="eth0", iface_up=False, iface_addrs=set()), FAIL),
    ("AP wlan0 up w/ .1+.2",
     dict(mode="wireless", soft=False, soft_ip="", ident_prefix="10.160.160",
          ident_iface="wlan0", iface_up=True,
          iface_addrs={"10.160.160.1", "10.160.160.2"}), PROCEED),
    ("AP wlan0 DOWN (Seattle6 doc-5: identity iface linkdown)",
     dict(mode="wireless", soft=False, soft_ip="", ident_prefix="10.160.160",
          ident_iface="wlan0", iface_up=False, iface_addrs=set()), FAIL),
    ("AP wlan0 up but missing .2",
     dict(mode="wireless", soft=False, soft_ip="", ident_prefix="10.160.160",
          ident_iface="wlan0", iface_up=True, iface_addrs={"10.160.160.1"}), FAIL),
    ("AP wlan0 up but bears neither .1 nor .2",
     dict(mode="wireless", soft=False, soft_ip="", ident_prefix="10.160.160",
          ident_iface="wlan0", iface_up=True, iface_addrs={"192.168.0.21"}), FAIL),
    ("no identity iface resolved",
     dict(mode="wireless", soft=False, soft_ip="", ident_prefix="10.160.160",
          ident_iface="", iface_up=False, iface_addrs=set()), FAIL),
    ("soft enabled w/ presented IP, no real iface -> synthesize",
     dict(mode="wireless", soft=True, soft_ip="10.250.250.1", ident_prefix="10.250.250",
          ident_iface="", iface_up=False, iface_addrs=set()), SOFT),
    ("soft enabled, IP from dnsmasq subnet -> synthesize",
     dict(mode="wired", soft=True, soft_ip="", ident_prefix="10.130.130",
          ident_iface="eth0", iface_up=False, iface_addrs=set()), SOFT),
    ("soft enabled but no IP and no subnet -> fail",
     dict(mode="wireless", soft=True, soft_ip="", ident_prefix="",
          ident_iface="", iface_up=False, iface_addrs=set()), FAIL),
]


def main():
    ok = True
    for name, kw, want in CASES:
        got, reason = decide(**kw)
        if got == want:
            print(f"  PASS [{want:^7}] {name}")
        else:
            ok = False
            print(f"  FAIL {name}: got {got} ({reason}), want {want}")

    # ---- identity-iface + served-prefix RESOLUTION (must NOT assume eth0,
    #      must read the served subnet OFF THE INTERFACE, not dnsmasq) ----------
    from discovery.identity_preflight import resolve_identity_iface
    from discovery.mapinterfaces import Iface

    def rcheck(cond, msg):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + msg)
        ok = ok and cond

    # REAL NY1 ip a: identity on eth0 (10.102.60.1/.2), eth1 is the WAN client,
    # wlan0 down. dnsmasq HINT is the wrong subnet (10.250.250) - the bug that
    # ABORTed the node. The served prefix must come from eth0, so the node PROCEEDs.
    ny1 = [Iface("lo", "unknown", ["127.0.0.1/8"]),
           Iface("eth0", "up", ["10.102.60.1/24", "10.102.60.2/24"]),  # identity
           Iface("eth1", "up", ["192.168.1.245/24"]),                  # WAN client
           Iface("wlan0", "down", []),
           Iface("frognet0", "unknown", ["10.254.1.4/24"]),
           Iface("wg0", "unknown", ["10.253.203.98/30"])]
    dev, addrs, served = resolve_identity_iface(ny1, "10.250.250")  # wrong dnsmasq hint
    rcheck(dev == "eth0", "NY1: identity resolves to eth0 (the iface bearing the served .1)")
    rcheck(served == "10.102.60",
           "NY1: served prefix derived from eth0 (10.102.60), NOT the bad dnsmasq hint")
    rcheck(decide(mode="wired", soft=False, soft_ip="", ident_prefix=served,
                  ident_iface=dev, iface_up=True, iface_addrs=addrs)[0] == PROCEED,
           "NY1: PROCEEDs (eth0 bears .1/.2 of its own served subnet)")

    # two DHCP clients + AP identity on wlan0 -> resolves wlan0, served from wlan0
    apnode = [Iface("eth0", "up", ["192.168.1.10/24"]),
              Iface("eth1", "up", ["192.168.9.10/24"]),
              Iface("wlan0", "up", ["10.160.160.1/24", "10.160.160.2/24"])]
    d2, _a2, s2 = resolve_identity_iface(apnode, "")
    rcheck(d2 == "wlan0" and s2 == "10.160.160",
           "AP node w/ two DHCP clients: identity=wlan0, served=10.160.160")

    # borrowed .191 lease on a FrogNet /24 is NOT the identity bearer
    leaf = [Iface("eth0", "up", ["10.160.160.191/24"]),
            Iface("wlan0", "up", ["10.130.130.1/24", "10.130.130.2/24"])]
    rcheck(resolve_identity_iface(leaf, "")[0] == "wlan0",
           "leaf: a borrowed .191 lease is not mistaken for the identity iface")

    # no interface bears any served .1 -> unresolved (-> decide FAILs)
    none = resolve_identity_iface([Iface("eth0", "up", ["192.168.7.42/24"])], "10.250.250")
    rcheck(none == ("", set(), ""),
           "no served .1 anywhere -> identity unresolved (fail-closed)")

    print()
    print("ALL IDENTITY-PREFLIGHT CHECKPOINTS PASS" if ok else "IDENTITY-PREFLIGHT PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
