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
test_tunnel_peer_dot2_oracle.py - a tunnel peer is measured on .2 and nowhere else.
[DISCOVERY_ON_DOT2_ONLY_V1], John 2026-09-25: all discovery happens on .2, and NO
FALLBACKS.

Replaces test_tunnel_peer_health_emit_oracle, which asserted the
[TUNNEL_PEER_HEALTH_EMIT_V1] rescue: a tunnel immediate whose .2 did not measure was
re-probed on its PRODUCTION .1 (srcless metric-6 /32 over the tunnel) and emitted as
measured. That hid every dark .2 it rescued.

SCENARIO: Seattle5 with one tunnel wg2 -> New-York-1 (10.102.60.1). The :9009
backend answers for EVERY target, NY1's .1 included, so the old rescue always
succeeds here.

  answers: NY1's .2 measures over wg2  -> emitted IMMEDIATE, route installed.
  silent:  NY1's .2 does not measure   -> NOT emitted; IMMEDIATE_NO_ANSWER names
           ip, the .2 target and dev; no .1 address is given a probe route.

FAILS on the old code (silent run: srcless 10.102.60.1/32 dev wg2 probe, pong, emit).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.discovery import Discovery
from discovery.routes import Routes
from discovery.kernel import FakeKernel
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore, FakeVerify)
from discovery import descend as _descend

IDENT = "10.250.250.1"


def run(dot2_answers):
    logs = []
    echo = FakeEcho(answers={"10.102.60.2": "New-York-1,10.102.60.1,,"} if dot2_answers else {})
    rtt = FakeRtt(table={("wg2", "10.102.60.2"): [119]} if dot2_answers else {})
    gethosts = FakeGetHosts(children={})     # nothing vouches NY1: immediate-only
    broker = FakeBroker(by_one={}, iface_channel={"wg2": "New-York-1-10.102.60"})
    k = FakeKernel()
    k.seed(
        "default via 192.168.0.1 dev wlan1 metric 601",
        "10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1",
        "10.102.60.0/24 dev wg2 scope link metric 22",
    )
    routes = Routes(k, logger=lambda s: None, clock=lambda: 0.0)
    verify = FakeVerify()                     # pongs for every target, .1 included
    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     local_ips={"10.250.250.1", "10.250.250.2", "127.0.0.1"},
                     dev_src_map={"wg2": "10.253.200.94"},
                     self_identity=IDENT, verify=verify,
                     logger=logs.append)
    disc.DEAD_IFACES = set()
    k.mutate_log.clear()
    _descend.descend(disc, ["eth0", "wg2"], [("10.102.60.1", "wg2")])
    writes = list(k.mutate_log)
    emitted = list(disc.CAND.get("10.102.60.0/24", []))
    disc.promote()
    return emitted, logs, writes, k.route_show()


def _dot1_probe_routes(writes):
    out = []
    for verb, dest, _metric, _rc in writes:
        host = dest[:-3] if dest.endswith("/32") else dest
        if "/" not in host and host.endswith(".1"):
            out.append((verb, dest))
    return out


def main():
    a_emit, a_logs, a_writes, a_tbl = run(dot2_answers=True)
    s_emit, s_logs, s_writes, s_tbl = run(dot2_answers=False)
    a_installed = any(line.startswith("10.102.60.2") and "dev wg2" in line
                      for line in a_tbl.replace(";", "\n").splitlines())
    named = [l for l in s_logs if l.startswith("IMMEDIATE_NO_ANSWER plane=.2 ip=10.102.60.1 ")
             and "target=10.102.60.2" in l and "dev=wg2" in l]
    d1a, d1s = _dot1_probe_routes(a_writes), _dot1_probe_routes(s_writes)

    checks = [
        ("NY1 emitted IMMEDIATE when its .2 measures over wg2",
         any("|wg2|" in c for c in a_emit)),
        ("promote installed the descend-produced wg2 route (answers run)", a_installed),
        ("NY1 NOT emitted when its .2 is silent, although its .1 would pong", not s_emit),
        ("silent .2 is an IMMEDIATE_NO_ANSWER naming ip, .2 target and dev", bool(named)),
        (f"no .1 address given a probe route (answers run: {d1a or 'none'})", not d1a),
        (f"no .1 address given a probe route (silent run: {d1s or 'none'})", not d1s),
    ]
    print("=== TUNNEL PEER .2 ORACLE ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in checks):
        print("ALL TUNNEL-PEER-DOT2 ORACLE CHECKPOINTS PASS")
        return 0
    print("TUNNEL-PEER-DOT2 ORACLE FAILED")
    print("--- silent-run logs (IMMEDIATE/PROBE lines) ---")
    print("\n".join(l for l in s_logs if l.startswith(("IMMEDIATE", "PROBE", "CANDIDATE"))) or "(none)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
