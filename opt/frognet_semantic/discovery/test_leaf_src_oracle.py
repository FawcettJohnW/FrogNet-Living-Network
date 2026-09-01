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
test_leaf_src_oracle.py - [LEAF_SRC_PIN_V1] regression for the bug the fabric
oracles missed: a leaf that reaches the mesh over a BORROWED discovery lease on
another node's segment must originate from its own identity .1, not the lease.

The pre-fix harness only modeled nodes whose egress-interface address WAS their
identity (system.py seeds each node `... dev eth0 ... src {n.one}`) and only
asserted route *egress* (which dev), never the source address a packet carries.
So the leak - leaf sources from the lease (10.130.130.47), which crosses the
tunnel with no return route - could not surface. This models the leaf directly
and asserts the installed routes pin src=identity.

Scenario (Seattle2): identity 10.120.120.1 (served /24 on wlan0, hostapd),
reaches the mesh as a wlan1 client leased 10.130.130.47 on Seattle3's segment,
default/exit via 10.130.130.1 (Seattle3) -> 10.250.250.1 (Seattle5) -> tunnel.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.fixdefault import FixDefaultRoute
from discovery.discovery import Discovery
from discovery.routes import Routes
from discovery.sources import HostStore

IDENT = "10.120.120.1"          # leaf identity .1 (served FROGNET /24 on wlan0)
LEASE = "10.130.130.47"         # borrowed discovery lease on the egress iface
GW = "10.130.130.1"             # Seattle3, the leaf's uplink / exit next-hop


def _leaf_fdr(k, self_identity):
    return FixDefaultRoute(
        k,
        dev_ip4={"wlan0": IDENT, "wlan1": LEASE},
        up_devs={"wlan0", "wlan1"},
        connected_prefixes={"wlan1": {"10.130.130"}, "wlan0": {"10.120.120"}},
        ping=lambda via, dev: True,
        frognet_interfaces=["wlan0", "wlan1"],
        local_ips={IDENT, "10.120.120.2", LEASE},
        logger=lambda s: None,
        self_identity=self_identity,
    )


def main():
    ok = True

    # 1) FIXED: leaf exit-default pins src=identity (not the lease).
    k = FakeKernel()
    k.seed("10.130.130.0/24 dev wlan1 proto kernel scope link src 10.130.130.47")
    _leaf_fdr(k, IDENT).install_exit_defaults([("Seattle5", "wlan1", GW, "FAST")])
    d = k.show_default()
    if any(f"src {IDENT}" in ln for ln in d):
        print(f"  PASS leaf default pins src={IDENT}")
    else:
        ok = False
        print(f"  FAIL leaf default missing src={IDENT}: {d}")
    if any(LEASE in ln for ln in d):
        ok = False
        print(f"  FAIL leaf default still references lease {LEASE}: {d}")

    # 2) CONTROL: no identity threaded -> srcless default == the pre-fix leak.
    #    (Proves the assertion above actually discriminates the bug.)
    k2 = FakeKernel()
    _leaf_fdr(k2, "").install_exit_defaults([("Seattle5", "wlan1", GW, "FAST")])
    d2 = k2.show_default()
    if d2 and not any(" src " in ln for ln in d2):
        print("  PASS control: srcless default reproduces the pre-fix leak")
    else:
        ok = False
        print(f"  FAIL control: expected srcless default, got {d2}")

    # 3) discovery dev_src: identity on BOTH LAN and wg egress. A wg route to a
    #    host BEYOND the tunnel peer (a child) must source from identity, not the
    #    transit /30 - NY1 proved a /30-sourced route (10.120.120 via wg2 src
    #    10.253.203.90) is 100% loss because the child has no route back to the /30.
    disc = Discovery(Routes(FakeKernel()), None, None, None, None, HostStore(),
                     local_ips={IDENT, LEASE}, dev_src_map={"wg0": "10.253.203.70"},
                     self_identity=IDENT)
    if disc.dev_src("wlan1") == IDENT:
        print("  PASS dev_src pins identity on LAN egress")
    else:
        ok = False
        print(f"  FAIL dev_src(wlan1)={disc.dev_src('wlan1')!r} want {IDENT!r}")
    if disc.dev_src("wg0") == IDENT:
        print("  PASS dev_src pins identity on wg egress too (children beyond the "
              "peer reply to a routable identity, not the transit /30)")
    else:
        ok = False
        print(f"  FAIL dev_src(wg0)={disc.dev_src('wg0')!r} want {IDENT!r} "
              "(transit /30 src breaks the return path for children)")

    # 4b) NY1 reproduction: promote a wg /24 to a child behind the tunnel peer and
    #     confirm the installed route carries src=identity, not the wg transit /30.
    kn = FakeKernel()
    dn = Discovery(Routes(kn), None, None, None, None, HostStore(),
                   local_ips={"10.102.60.1"}, dev_src_map={"wg2": "10.253.203.90"},
                   self_identity="10.102.60.1")
    nsrc = dn.dev_src("wg2")
    dn.r.install_if_changed("10.120.120.0/24", "", "wg2", 22, 0, nsrc)
    nline = " ".join(x for x in (kn.route_show("10.120.120.0/24") or "").replace(";", " ").split())
    if "src 10.102.60.1" in nline and "10.253.203.90" not in nline:
        print("  PASS NY1: wg child route 10.120.120/24 pins src=identity (not /30)")
    else:
        ok = False
        print(f"  FAIL NY1 wg child route src: {nline}")

    # 4) promote a relayed LAN winner and confirm the installed /24 + alias are
    #    src-LESS. [DOWNSTREAM_VIA_SRC_LESS_V1] John's rule: a /24 reached VIA a
    #    LAN first hop must not carry src - the reply returns via the hop to this
    #    node's lease, not to an off-segment identity the far side can't route back
    #    to. promote still passes src=identity; install_if_changed strips it. (This
    #    checkpoint previously locked in the too-wide leak; inverted when the fix
    #    landed, per [SRC_PIN_SCOPE].)
    kp = FakeKernel()
    dp = Discovery(Routes(kp), None, None, None, None, HostStore(),
                   local_ips={IDENT, LEASE}, dev_src_map={}, self_identity=IDENT)
    src = dp.dev_src("wlan1")
    # mirror promote()'s install of winner /24 + .2 alias for a relayed peer
    dp.r.install_if_changed("10.250.250.0/24", GW, "wlan1", 22, 1, src)
    dp.r.install_if_changed("10.250.250.2/32", GW, "wlan1", 5, 1, src)
    tbl = kp.route_show().replace(";", "\n").splitlines()
    promoted = [ln for ln in tbl if ln.startswith("10.250.250")]
    if promoted and all(" src " not in ln and " onlink" not in ln for ln in promoted):
        print("  PASS promoted LAN via-routes are src-LESS and onlink-LESS (John's rule)")
    else:
        ok = False
        print(f"  FAIL promoted LAN via-routes must be src-less AND onlink-less: {promoted}")

    # 5b) WAN-egress guard: a default via a NON-10 gateway must NOT be pinned
    #     to the frognet identity (would break Internet egress). Identity is set,
    #     but the next-hop is a WAN gateway -> src must be absent.
    kw = FakeKernel()
    kw.seed("192.168.0.0/24 dev wlan0 proto kernel scope link src 192.168.0.27")
    fdrw = FixDefaultRoute(
        kw,
        dev_ip4={"wlan0": "192.168.0.27", "eth0": "10.250.250.1"},
        up_devs={"wlan0", "eth0"},
        connected_prefixes={"wlan0": {"192.168.0"}, "eth0": {"10.250.250"}},
        ping=lambda via, dev: True,
        frognet_interfaces=["wlan0", "eth0"],
        local_ips={"192.168.0.27", "10.250.250.1"},
        logger=lambda s: None,
        self_identity="10.250.250.1",
    )
    fdrw.install_offlan_defaults([("192.168.0.1", "wlan0")])
    dw = kw.show_default()
    if dw and not any(" src " in ln for ln in dw):
        print("  PASS WAN-egress default left unpinned (src=WAN iface)")
    else:
        ok = False
        print(f"  FAIL WAN-egress default wrongly pinned: {dw}")

    print()
    print("ALL LEAF-SRC CHECKPOINTS PASS" if ok else "LEAF-SRC PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
