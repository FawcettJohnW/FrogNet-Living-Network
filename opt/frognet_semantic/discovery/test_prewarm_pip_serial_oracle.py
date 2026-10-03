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
test_prewarm_pip_serial_oracle.py - [PREWARM_PER_PIP_SERIAL_V1]

A probe route is `<pip>/32 metric 6`; the kernel keys on (dest, metric), not dev.
So concurrent `ip route replace <pip>/32 dev wgX` for the SAME pip on different
devs clobber each other - the live Seattle5 trace shows `spec="... dev wg0 ..."
after="... dev wg1 ..."`, corrupting per-dev rtt and making a relay-reached subnet
reshuffle its winner every pass.

prewarm_wave must therefore parallelize ACROSS distinct pips but run one pip's
devs SEQUENTIALLY. This oracle records the install order (max_workers=1 forces a
deterministic schedule) and asserts every pip's probes are CONTIGUOUS - i.e. the
three devs of a shared pip are one group, never interleaved with another pip's
probe. Contiguous == owns the /32 while measuring == no clobber.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery, net_dot
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore)

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

k = FakeKernel()
d = Discovery(Routes(k, clock=lambda: 0.0), FakeEcho(answers={}), FakeRtt(table={}),
              FakeGetHosts(children={}), FakeBroker(), HostStore(),
              local_ips={"10.250.250.1", "127.0.0.1"}, dev_src_map={},
              self_identity="10.250.250.1", own_subnet="10.250.250.0/24")

order = []
d._prewarm_probe = lambda dev, ip, pv: order.append((net_dot(ip, 2), dev))

# 10.28.28.1 (NY-2) is reachable over all three tunnels (shared pip), interleaved
# in the seed list with two single-dev pips. A naive flat map would let the three
# 10.28.28.2 installs race; per-pip grouping must keep them together.
seeds = [
    ("wg2", "10.28.28.1", ""),
    ("wg0", "10.111.11.1", ""),
    ("wg0", "10.28.28.1", ""),
    ("wg1", "10.179.179.1", ""),
    ("wg1", "10.28.28.1", ""),
]
d.prewarm_wave(seeds, max_workers=1)

# group the recorded order by pip and assert each pip's runs are one contiguous block
blocks = []
for pip, dev in order:
    if blocks and blocks[-1][0] == pip:
        blocks[-1][1].append(dev)
    else:
        blocks.append((pip, [dev]))
pip_block_count = {}
for pip, _ in blocks:
    pip_block_count[pip] = pip_block_count.get(pip, 0) + 1

print("  install order:", order)
print("  blocks:", blocks)
check(all(n == 1 for n in pip_block_count.values()),
      "each pip's probes form ONE contiguous block (same-pip devs never interleave)")
shared = net_dot("10.28.28.1", 2)
shared_devs = next((devs for pip, devs in blocks if pip == shared), [])
check(sorted(shared_devs) == ["wg0", "wg1", "wg2"],
      "the shared pip's three tunnel devs all run in its single serial group")

print("\n[ PASS ] test_prewarm_pip_serial_oracle - ALL CHECKPOINTS PASS" if ok
      else "\n[ FAIL ] test_prewarm_pip_serial_oracle")
sys.exit(0 if ok else 1)
