#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""test_region_backends_oracle - [REGION_BACKENDS_V1] the REAL merge engine (descend -> routes -> kernel) with the
gather answered from the FD&F region's records instead of the echo/getHosts crawl. Hub-and-spoke: New-York-1
(10.102.60.1) has ONE tunnel, wg1, to the hub; the daemon's active state says which remote /24s ride it (the
verified mappings). Asserts, each with its control:

  1 every ALIVE member is named in /etc/hosts from the records in ONE merge (no crawl, no depth, no re-merge)
  2 a member whose session is down is NOT named (control)
  3 published != reachable (plan 3.6): an alive member whose .2 does not answer over the tunnel is a candidate
    that fails its probe (PROBE_FAIL said), gets NO route, and -- the engine's own rule, HOSTS_GATE -- no name
    either (a name without a route would be a lie to dnsmasq's clients); the members that answer get their /24
  4 an address that is not a member returns no echo (NOT_FROGNET), exactly as the crawl's unknown address did
  5 a leaf (role=leaf) echoes its own addr and is listed as a host
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import HostStore, FakeRtt, FakeBroker
from discovery.propagate import Propagator
from discovery.real_backends import RegionRecords, RegionEcho, RegionGetHosts
from discovery.runmerge import run_merge

FAILS = []
def check(ok, label):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

def member(i, name, subnet, alive=True, role="node", addr=None, hosts=()):
    f = {"role": role, "name": name, "guid": "g-" + name, "pubkey": "PK-" + name, "transit": [], "hosts": list(hosts)}
    if role == "node": f["subnet"] = subnet
    else: f["addr"] = addr
    return {"id": i, "alive": alive, "name": name, "facts": f}

PARTS = [
    member(1, "New-York-1", "10.102.60.0/24"),                                   # us
    member(2, "Seattle5", "10.250.250.0/24"),                                    # answers over wg1
    member(3, "BAMacBook", "10.179.179.0/24"),                                   # answers over wg1
    member(4, "Seattle2", "10.120.120.0/24"),                                    # alive, published, UNREACHABLE (tunnel down)
    member(5, "Seattle6", "10.160.160.0/24", alive=False),                       # session down
    member(6, "Laptop", None, role="leaf", addr="10.252.0.7/32"),
]
REMOTE = ["10.250.250.0/24", "10.179.179.0/24", "10.120.120.0/24", "10.252.0.7/32"]   # what the daemon says rides wg1

def build():
    rec = RegionRecords(ram="test", participants=PARTS)
    local_ips = {"10.102.60.1", "10.102.60.2", "10.253.102.60", "127.0.0.1"}
    echo, gethosts = RegionEcho(rec), RegionGetHosts(rec, local_ips)
    rtt = FakeRtt(table={                       # the path measurement: who answers on .2 over wg1
        ("wg1", "10.250.250.2"): [147, 147, 147, 147],
        ("wg1", "10.179.179.2"): [194, 194, 194, 194],
        ("wg1", "10.252.0.7"):   [60, 60, 60, 60],
        # 10.120.120.2: absent -> dead over this tunnel
    })
    broker = FakeBroker(by_one={}, iface_channel={"wg1": "hub-hubA"})
    hoststore = HostStore()
    k = FakeKernel()
    k.seed("10.102.60.0/24 dev eth0 proto kernel scope link src 10.102.60.1",
           "10.253.0.0/16 dev wg1 proto kernel scope link src 10.253.102.60")
    routes = Routes(k, logger=lambda s: None, clock=lambda: 0.0)
    disc = Discovery(routes, echo, rtt, gethosts, broker, hoststore, local_ips,
                     {"wg1": "10.253.102.60"}, logger=lambda s: None,
                     propagator=Propagator(hoststore, logger=lambda s: None))
    seeds = dict(active_devs=["eth0", "wg1"],
                 dev_ip={"eth0": "10.102.60.1", "wg1": "10.253.102.60"},
                 leases=[], disk_cache=[],
                 active_states=[("wg1", REMOTE)],
                 wg_kernel_nets={"wg1": []},
                 arp_neigh={"eth0": []})
    return disc, k, seeds, {"eth0": "10.102.60.1"}, echo, gethosts

disc, k, seeds, upstream, echo, gethosts = build()
LOG = []
disc.log = LOG.append
out = run_merge(disc, k, seeds, upstream, local=("10.102.60.1", "New-York-1"),
                bringup_peers=[("hubA", "10.253.253.254")], depth=0, concurrent_attempt=True,
                logger=LOG.append)
hosts = "\n".join(out["etc_hosts"])
table = "\n".join(out["final_table"])

# 1 alive members named from the records in one merge
for name, ip in [("Seattle5", "10.250.250.1"), ("BAMacBook", "10.179.179.1")]:
    check(f"{ip} FrogNetHost.{name}" in hosts, f"1 {name} named from the region's records")
# 2 control: session down -> not named
check("FrogNetHost.Seattle6" not in hosts, "2 control: Seattle6 (session down) is NOT named")
# 3 published != reachable
check("10.250.250.0/24 dev wg1" in table, "3 Seattle5 routed via wg1 (answered)")
check("10.179.179.0/24 dev wg1" in table, "3 BAMacBook routed via wg1 (answered)")
check("10.120.120.0/24" not in table, "3 Seattle2: alive and published, but its .2 did not answer -> NO route committed")
check(any("PROBE_FAIL" in l and "10.120.120.2" in l for l in LOG), "3 Seattle2's failed probe is SAID (PROBE_FAIL)")
check("FrogNetHost.Seattle2" not in hosts, "3 Seattle2 not named either (HOSTS_GATE: no route, no name)")
# 4 unknown address: no echo
check(echo.echo_probe("10.77.77.2") == "", "4 a non-member address has no echo (NOT_FROGNET)")
check(echo.echo_probe("10.250.250.2") == "Seattle5,10.250.250.1,0.0.0.0,0.0.0.0", "4c control: a member's .2 echoes its CSV (no uplink rows: 0.0.0.0 as getWlan0IP/getWlan1IP)")
# 4u [ECHO_UPSTREAM_FROM_THE_ROWS_V1] fields 3-4 are the member's uplink rows, in order (getFrogNet.bash's wlan0/wlan1 IPs)
from discovery.real_backends import region_echo
_f = {"name": "New-York-2", "addrs": [{"kind": "own", "ip": "10.28.28.1"}, {"kind": "uplink", "ip": "192.168.1.40"}]}
check(region_echo(_f, "10.28.28.1") == "New-York-2,10.28.28.1,192.168.1.40,0.0.0.0", "4u one uplink: field 3 its ip, field 4 0.0.0.0")
_f["addrs"].append({"kind": "uplink", "ip": "10.3.3.20"})
check(region_echo(_f, "10.28.28.1") == "New-York-2,10.28.28.1,192.168.1.40,10.3.3.20", "4u two uplinks: both, in row order")
# 5 leaf
check(echo.echo_probe("10.252.0.7") == "Laptop,10.252.0.7,0.0.0.0,0.0.0.0", "5 leaf echoes its own addr")
check(("10.252.0.7", "Laptop") in gethosts.get_hosts("10.250.250.1", "10.250.250.2"), "5 leaf listed as a host")
check(("10.102.60.1", "New-York-1") not in gethosts.get_hosts("10.250.250.1", "10.250.250.2"), "5 we are not listed to ourselves")

if FAILS:
    print(f"REGION BACKENDS ORACLE FAILED: {FAILS}")
    sys.exit(1)
print("ALL REGION BACKENDS ORACLE CHECKS PASS")
