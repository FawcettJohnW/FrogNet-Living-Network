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
"""test_vouch_ts_gate_oracle.py - [VOUCH_TS_GATE_V1] John's rule 2026-07-06:
the tuple has a timestamp; USING it is the consumer's responsibility. A vouch
for a host whose own SD:capability assertion is absent-or-ancient in a
POPULATED store is hearsay about a node that stopped speaking: no candidate,
no propagation, no child walk, no probe spend. Empty/unreachable store (cold
bootstrap) => gate off, legacy behavior. Fails on pre-gate code (the corpse
vouch sailed into CAND and burned a probe ladder every merge - Seattle6,
field, 2026-07-06)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               FakeVerify, HostStore)

FAILS = []
def check(ok, label):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok: FAILS.append(label)

def mk(caps):
    k = FakeKernel()
    r = Routes(k, clock=lambda: 0.0)
    logs = []
    # relay 10.130.130.1 vouches two children: fresh 10.77.77.9 and ghost 10.88.88.9
    d = Discovery(r, FakeEcho(answers={}), FakeRtt(table={}),
                  FakeGetHosts(children={"10.130.130.1": ["10.77.77.9", "10.88.88.9"]}),
                  FakeBroker(), HostStore(),
                  local_ips={"10.250.250.1", "127.0.0.1"}, dev_src_map={},
                  logger=logs.append, self_identity="10.250.250.1",
                  own_subnet="10.250.250.0/24", has_own_uplink=True,
                  uplink_dev="", verify=FakeVerify(), caps=caps)
    return k, r, d, logs

def run(caps):
    k, r, d, logs = mk(caps)
    d.walk("eth0", "10.130.130.1", 1)
    blob = "\n".join(logs)
    return d, blob

print("=== populated store: fresh host vouched, silent host refused ===")
d, blob = run(lambda: {"10.77.77.1": 120, "10.130.130.1": 60})
check("VOUCH dest=10.77.77.0/24" in blob,
      "fresh self-assertion (age 120s) -> vouch proceeds")
check("VOUCH_STALE dest=10.88.88.0/24" in blob
      and "no_self_assertion_in_populated_store" in blob,
      "no self-assertion in populated store -> vouch refused")
check("VOUCH dest=10.88.88.0/24 via" not in blob,
      "refused vouch never becomes a candidate")
check(not any("ip=10.88.88" in l and "WALK" in l for l in blob.splitlines()),
      "refused vouch's child is never walked (no probe spend)")

print("=== populated store: ancient self-assertion refused ===")
d, blob = run(lambda: {"10.77.77.1": 120, "10.88.88.1": 999999, "10.130.130.1": 60})
check("VOUCH_STALE dest=10.88.88.0/24" in blob and "self_assertion_age=999999" in blob,
      "ancient self-assertion (>max) -> vouch refused with age in the log")

print("=== cold bootstrap: empty store -> gate off, legacy behavior ===")
d, blob = run(lambda: {})
check("VOUCH dest=10.88.88.0/24" in blob,
      "empty store -> gate off -> legacy vouch proceeds")
check("gate=off" in blob, "gate-off is said loudly")

print("=== no evidence system wired -> legacy, silent ===")
d, blob = run(None)
check("VOUCH dest=10.88.88.0/24" in blob, "caps=None -> legacy vouch proceeds")

print()
print("ALL VOUCH-TS-GATE CHECKS PASS" if not FAILS
      else f"VOUCH-TS-GATE CHECKS FAILED: {len(FAILS)}")
sys.exit(0 if not FAILS else 1)
