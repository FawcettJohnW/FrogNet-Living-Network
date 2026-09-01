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
"""test_lan_neighbor_resolver_oracle.py - [LAN_NEIGHBOR_IS_A_RESOLVER_V1]

Seattle3, 2026-08-13. Its merge wrote:
    NEIGHBOR_VIA_SENTINEL wrote 1 mapping(s): {'10.250.250.221': '10.160.160.1'}
    TUNNEL_PEERS n=0
and `nslookup brokerhost.seattle2` returned SERVFAIL, while the same query run
ON Seattle2 answered 10.120.120.63.

Seattle6 (10.160.160.1) is the LAN neighbour that can forward toward Seattle2's
pond, and no tunnel exists to name it. This oracle FAILS on the old code (the
address never reaches resolv.conf) and PASSES on the new.

Run:  python3 -m discovery.test_lan_neighbor_resolver_oracle
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []


def check(got, want, what):
    if got != want:
        FAILURES.append(f"{what}\n     got:  {got!r}\n     want: {want!r}")
        print(f"  FAIL {what}")
    else:
        print(f"  ok   {what}")


def main():
    tmp = tempfile.mkdtemp(prefix="nfsent-")
    os.environ["FROGNET_SENTINEL_DIR"] = tmp

    from discovery import live
    from discovery.resolv import build_resolv

    # Seattle3's own addresses, from its echo line:
    #   FrogNetHost.Seattle3,10.130.130.1,10.250.250.191,0.0.0.0
    local_ips = ["10.130.130.1", "10.130.130.2", "10.250.250.191", "127.0.0.1"]

    # --- 1. no sentinel yet: no fallback, empty is the honest answer -------
    check(live._lan_neighbor_resolvers(local_ips), [],
          "absent sentinel yields no neighbours (no fallback)")

    # --- 2. the sentinel Seattle3's merge actually wrote -------------------
    with open(os.path.join(tmp, "frognet_neighbor_via"), "w") as f:
        f.write("10.250.250.221 10.160.160.1\n")
    check(live._lan_neighbor_resolvers(local_ips), ["10.160.160.1"],
          "field 2 (.1 identity) is used, not field 1 (the lease)")

    # --- 3. self is never listed -----------------------------------------
    with open(os.path.join(tmp, "frognet_neighbor_via"), "w") as f:
        f.write("10.250.250.221 10.160.160.1\n")
        f.write("10.130.130.7 10.130.130.1\n")      # ourselves
        f.write("# a comment line\n")
        f.write("10.250.250.99 10.160.160.1\n")      # dupe identity
    check(live._lan_neighbor_resolvers(local_ips), ["10.160.160.1"],
          "self excluded, comments skipped, identities deduped")

    # --- 4. it lands in resolv.conf ahead of the WAN gateway --------------
    # Seattle3 had TUNNEL_PEERS n=0 and DNSMASQ_UPSTREAM next_hop=10.250.250.221
    wan_ns = [] + [] + live._lan_neighbor_resolvers(local_ips) + ["10.250.250.221"]
    lines = build_resolv("Seattle3", "FrogNetHost.Seattle3", wan_ns,
                         self_ip="10.130.130.1")
    ns = [l.split()[1] for l in lines if l.startswith("nameserver ")]
    check(ns, ["127.0.0.1", "10.130.130.1", "10.160.160.1", "10.250.250.221"],
          "order: loopback, self, LAN neighbour, WAN gateway last")

    # --- 5. the regression this exists to catch ---------------------------
    old_wan_ns = [] + [] + ["10.250.250.221"]        # pre-change composition
    old_lines = build_resolv("Seattle3", "FrogNetHost.Seattle3", old_wan_ns,
                             self_ip="10.130.130.1")
    old_ns = [l.split()[1] for l in old_lines if l.startswith("nameserver ")]
    check("10.160.160.1" in old_ns, False,
          "OLD composition genuinely lacked the neighbour (oracle is not vacuous)")

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} check(s):")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("PASS - [LAN_NEIGHBOR_IS_A_RESOLVER_V1]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
