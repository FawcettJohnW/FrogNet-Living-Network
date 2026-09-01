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
sim/topology.py - the New-York-1 merge scenario as captured in the oracle log,
reconstructed as live inputs for the ported discovery. Reproduces:
  - 3 direct tunnels (wg0 BABox DEAD, wg1 BAMacBook, wg2 Seattle5)
  - relayed Seattle2/3/6 reached behind wg1 and/or wg2 (getHosts vouching)
  - New-York-2 on the eth0 LAN segment (seg_relay 10.102.60.230)
  - broker authority: direct peers in handshake_rtts; relayed peers absent

build_ny1() returns (discovery, kernel, seeds) ready to drive.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import FakeEcho, FakeRtt, FakeGetHosts, FakeBroker, HostStore

# connected/kernel + bringup routes present when sync_interfaces starts (SNAP enter)
ENTER_SEED = [
    "10.28.28.0/24 via 10.28.28.1 dev eth0 metric 22 onlink",
    "10.102.60.0/24 dev eth0 proto kernel scope link src 10.102.60.1",
    "10.111.11.0/24 dev wg0 scope link metric 22",
    "10.179.179.0/24 dev wg1 scope link metric 22",
    "10.250.250.0/24 dev wg2 scope link metric 22",
    "10.253.203.88/30 dev wg2 proto kernel scope link src 10.253.203.90",
    "10.253.203.96/30 dev wg0 proto kernel scope link src 10.253.203.98",
    "10.253.203.104/30 dev wg1 proto kernel scope link src 10.253.203.106",
    "10.254.1.0/24 dev frognet0 proto kernel scope link src 10.254.1.4",
]

# echo CSVs (self-describing: name,<own .1>,<upstream>,<2nd-upstream>) from makeHostJson
_NODES = {
    # name        net            echo csv                                              extra answer addrs
    "New-York-2": ("10.28.28",   "New-York-2,10.28.28.1,10.102.60.230,0.0.0.0",        ["10.102.60.230"]),
    "BAMacBook":  ("10.179.179", "BAMacBook,10.179.179.1,172.16.26.155,0.0.0.0",       []),
    "Seattle5":   ("10.250.250", "Seattle5,10.250.250.1,192.168.0.27,192.168.0.21",    []),
    "Seattle3":   ("10.130.130", "Seattle3,10.130.130.1,0.0.0.0,0.0.0.0",              []),
    "Seattle6":   ("10.160.160", "Seattle6,10.160.160.1,10.251.251.221,0.0.0.0",       []),
    "Seattle2":   ("10.120.120", "Seattle2,10.120.120.1,10.130.130.47,0.0.0.0",        []),
    # BABox: wg0 DEAD (empty echo); excluded by health check, never probed.
}


def build_ny1(logger=lambda s: None):
    echo_answers = {}
    for name, (net, csv, extra) in _NODES.items():
        echo_answers[f"{net}.1"] = csv
        echo_answers[f"{net}.2"] = csv
        for a in extra:
            echo_answers[a] = csv
    echo = FakeEcho(answers=echo_answers)

    rtt = FakeRtt(table={
        ("wg1", "10.179.179.2"): [194],
        ("wg1", "10.250.250.2"): [0],     # rejected (channel mismatch) - measured, discarded
        ("wg1", "10.130.130.2"): [252],
        ("wg1", "10.160.160.2"): [257],
        ("wg2", "10.250.250.2"): [147],
        ("wg2", "10.179.179.2"): [0],     # rejected
        ("wg2", "10.130.130.2"): [312],
        ("wg2", "10.120.120.2"): [228],
        ("wg2", "10.160.160.2"): [236],
        ("eth0", "10.28.28.2"):  [55, 56, 89],
    })

    gethosts = FakeGetHosts(children={
        "10.179.179.1": ["10.250.250.1", "10.130.130.1", "10.160.160.1"],
        "10.250.250.1": ["10.179.179.1", "10.130.130.1", "10.120.120.1", "10.160.160.1"],
        "10.28.28.1":   [],
    })

    broker = FakeBroker(
        by_one={
            "10.179.179.1": ("BAMacBook-10.179.179", "10.179.179.0/24", "10.179.179.1"),
            "10.250.250.1": ("Seattle5-10.250.250", "10.250.250.0/24", "10.250.250.1"),
            "10.111.11.1":  ("BABox-10.111.11", "10.111.11.0/24", "10.111.11.1"),
        },
        iface_channel={
            "wg0": "BABox-10.111.11",
            "wg1": "BAMacBook-10.179.179",
            "wg2": "Seattle5-10.250.250",
        },
    )

    hoststore = HostStore()
    local_ips = {"10.102.60.1", "10.102.60.2", "10.254.1.4",
                 "10.253.203.90", "10.253.203.98", "10.253.203.106", "127.0.0.1"}
    dev_src_map = {"wg1": "10.253.203.106", "wg2": "10.253.203.90", "wg0": "10.253.203.98"}

    k = FakeKernel()
    k.seed(*ENTER_SEED)
    routes = Routes(k, logger=logger, clock=lambda: 0.0)
    disc = Discovery(routes, echo, rtt, gethosts, broker, hoststore,
                     local_ips, dev_src_map, logger=logger)
    disc.DEAD_IFACES = {"wg0"}                # health check killed wg0 (BABox echo empty)
    disc._promote_order = [                    # oracle CAND/promote hash order
        "10.179.179.0/24", "10.28.28.0/24", "10.160.160.0/24",
        "10.250.250.0/24", "10.120.120.0/24", "10.130.130.0/24",
    ]

    seeds = dict(
        active_devs=["eth0", "eth1", "frognet0", "wg1", "wg2"],   # post health filter
        dev_ip={"eth0": "10.102.60.1", "eth1": "192.168.1.245",
                "frognet0": "10.254.1.4", "wg1": "10.253.203.106", "wg2": "10.253.203.90"},
        leases=["10.102.60.218"],
        disk_cache=[],
        active_states=[
            ("wg0", ["10.111.11.0/24", "10.120.120.0/24", "10.130.130.0/24",
                     "10.160.160.0/24", "10.179.179.0/24", "10.250.250.0/24"]),  # DEAD -> skipped
            ("wg1", ["10.179.179.0/24"]),
            ("wg2", ["10.250.250.0/24", "10.120.120.0/24",
                     "10.130.130.0/24", "10.160.160.0/24"]),
        ],
        wg_kernel_nets={"wg1": ["10.179.179.0/24"], "wg2": ["10.250.250.0/24"]},
        arp_neigh={"eth0": ["10.102.60.186", "10.102.60.230", "10.28.28.2",
                            "10.102.60.147", "10.102.60.78", "10.102.60.210",
                            "10.102.60.218", "10.102.60.182", "10.28.28.1"]},
    )
    upstream_seed = {"eth0": "10.102.60.1", "frognet0": "10.254.1.1"}
    return disc, k, seeds, upstream_seed


# ===========================================================================
# Seattle6 - real runMerge hardware scenarios (grounded against doc-5/doc-3).
# A LAN-relay node: identity 10.160.160/24 on wlan0 (hostapd/AP), transit
# 10.250.250/24 on wlan1, single uplink via Seattle5 (10.250.250.1). Used by
# test_seattle6_oracle.py for the good path AND the identity-interface-down
# negative case (the common breakage: the AP/identity iface loses carrier).
# ===========================================================================
S6_IDENT = "10.160.160.1"
S6_GW = "10.250.250.1"

# final 10.x table the box ended with (doc-5 ip r), independent of link state.
ORACLE_FINAL_S6 = [
    "10.28.28.0/24 via 10.250.250.1 dev wlan1 metric 22",
    "10.102.60.0/24 via 10.250.250.1 dev wlan1 metric 22",
    "10.160.160.0/24 dev wlan0 proto kernel scope link src 10.160.160.1",
    "10.179.179.0/24 via 10.250.250.1 dev wlan1 metric 22",
    "10.241.241.0/24 via 10.250.250.1 dev wlan1 metric 22",
    "10.250.250.0/24 dev wlan1 proto kernel scope link src 10.250.250.221 metric 600",
]


def build_seattle6(logger=lambda s: None, wlan0_linkdown=True):
    """Drive the REAL discovery over fake edges encoding Seattle6's view.
    wlan0_linkdown toggles the identity interface's carrier (the negative case).
    Seeded CONNECTED-ONLY so the four /24 winners are computed, not planted."""
    wlan0_line = ("10.160.160.0/24 dev wlan0 proto kernel scope link "
                  "src 10.160.160.1" + (" linkdown" if wlan0_linkdown else ""))
    wlan1_line = ("10.250.250.0/24 dev wlan1 proto kernel scope link "
                  "src 10.250.250.221 metric 600")

    echo = FakeEcho(answers={
        "10.250.250.2": "Seattle5,10.241.241.1,0.0.0.0,0.0.0.0",
        "10.179.179.2": "BAMacBook,10.179.179.1,0.0.0.0,0.0.0.0",
        "10.102.60.2":  "New-York-1,10.102.60.1,0.0.0.0,0.0.0.0",
        "10.28.28.2":   "New-York-2,10.28.28.1,0.0.0.0,0.0.0.0",
        # 10.111.11.2 absent -> FAIL_ECHO (BABox dead); 10.250.250.20 -> NOT_FROGNET
    })
    rtt = FakeRtt(table={
        ("wlan1", "10.250.250.2"): [24], ("wlan1", "10.179.179.2"): [109],
        ("wlan1", "10.102.60.2"):  [127], ("wlan1", "10.28.28.2"):   [134],
    })
    gethosts = FakeGetHosts(children={
        "10.241.241.1": ["10.179.179.1", "10.102.60.1", "10.111.11.1", "10.28.28.1"],
    })
    broker = FakeBroker(by_one={}, iface_channel={})        # LAN-only

    k = FakeKernel()
    k.seed(wlan0_line, wlan1_line)
    routes = Routes(k, logger=logger, clock=lambda: 0.0)
    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     local_ips={"10.160.160.1", "10.160.160.2", "10.250.250.221", "127.0.0.1"},
                     dev_src_map={"wlan0": "10.160.160.1", "wlan1": "10.250.250.221"},
                     logger=logger, self_identity=S6_IDENT)
    disc.DEAD_IFACES = set()
    disc._promote_order = ["10.241.241.0/24", "10.179.179.0/24",
                           "10.102.60.0/24", "10.28.28.0/24"]
    seeds = dict(
        active_devs=["wlan0", "wlan1", "frognet0"],
        dev_ip={"wlan0": "10.160.160.1", "wlan1": "10.250.250.221", "frognet0": "10.254.1.4"},
        leases=[], disk_cache=[], active_states=[], wg_kernel_nets={},
        arp_neigh={"wlan1": ["10.250.250.2", "10.250.250.1", "10.250.250.20"]},
    )
    return disc, k, seeds
