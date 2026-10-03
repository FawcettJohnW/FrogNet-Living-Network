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
test_wave_parallel_equivalence_oracle.py - [WAVE_PARALLEL_V1]

Proves parallel waves produce BYTE-IDENTICAL output to sequential: same CAND,
same final route table. Parallelism overlaps the slow echo/rtt I/O; the decision
logic is the unchanged sequential walk reading a prewarmed cache. If these ever
diverge, the parallel path is unsafe - gate blocks it.

Runs the SAME seeded scenario twice (parallel off, then on) over the real
descend_downstream + promote, comparing the kernel table and CAND.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import HostStore

# Deterministic fakes: echo answers for a small chain, rtt fixed, no reflect.
class Echo:
    def __init__(self, answers): self.answers = answers
    def echo_probe(self, pip):
        return self.answers.get(pip, "")
class Rtt:
    def measure_rtt(self, dev, pip): return "100"
class GetHosts:
    def __init__(self, children): self.children = children
    def get_hosts(self, host_path, pip):
        return self.children.get(host_path, [])
class Broker:
    def broker_for_peer_ip(self, dest1): return None
    def channel_for_iface(self, dev): return None
    def transits(self, host_path, cdest): return True
class Reflect:
    def probe(self, o, target): return None  # never LOOP

# Scenario: eth0 reaches .230 (NY-2 10.28.28), wg2 reaches Seattle5 10.250.250
# which getHosts-children Seattle6 (10.160.160).
def make(parallel):
    k = FakeKernel()
    k.seed("10.102.60.0/24 dev eth0 proto kernel scope link src 10.102.60.1")
    routes = Routes(k, clock=lambda: 0.0)
    echo = Echo({
        "10.28.28.2":   "New-York-2,10.28.28.1,0,0",
        "10.250.250.2": "Seattle5,10.250.250.1,0,0",
        "10.160.160.2": "Seattle6,10.160.160.1,0,0",
    })
    d = Discovery(routes, echo, Rtt(), GetHosts({"10.250.250.1": [("10.160.160.1","Seattle6")]}),
                  Broker(), HostStore(), local_ips={"10.102.60.1","127.0.0.1"},
                  dev_src_map={}, self_identity="10.102.60.1", reflect=Reflect(),
                  own_subnet="10.102.60.0/24", has_own_uplink=True)
    d.parallel_waves = parallel
    seeds = dict(
        active_devs=["eth0","wg2"],
        dev_ip={"eth0":"10.102.60.1","wg2":"10.253.203.90"},
        leases=["10.102.60.230"],
        disk_cache=[],
        active_states=[("wg2",["10.250.250.0/24"])],
        wg_kernel_nets={"wg2":[]},
        arp_neigh={},
    )
    d.descend_downstream(**seeds)
    d.promote()
    return dict(d.CAND), k.table(), dict(d._probe_cache)

seq_cand, seq_table, seq_cache = make(False)
par_cand, par_table, par_cache = make(True)

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

check(seq_cand == par_cand, "CAND identical parallel vs sequential")
check(seq_table == par_table, "final route table identical parallel vs sequential")
# [WAVE_PARALLEL_V1] wave-2 property: the depth-2 child (Seattle6 10.160.160.2,
# reached via getHosts of Seattle5 over wg2) must be PREWARMED in parallel mode -
# proving the wave-2 batch fired, not just wave-1. Sequential mode never prewarms,
# so its cache is empty. Without this, an inert change would still pass the
# equivalence checks above.
WAVE2_KEY = "wg2|10.160.160.2|"
check(WAVE2_KEY in par_cache, f"wave-2 child prewarmed in parallel ({WAVE2_KEY})")
check(not seq_cache, "sequential mode prewarms nothing (cache empty)")
if seq_cand != par_cand:
    print("    seq CAND:", seq_cand); print("    par CAND:", par_cand)
if seq_table != par_table:
    for l in seq_table:
        if l not in par_table: print("    seq-only:", l)
    for l in par_table:
        if l not in seq_table: print("    par-only:", l)

print("\n[ PASS ] test_wave_parallel_equivalence_oracle - IDENTICAL" if ok
      else "\n[ FAIL ] test_wave_parallel_equivalence_oracle")
sys.exit(0 if ok else 1)
