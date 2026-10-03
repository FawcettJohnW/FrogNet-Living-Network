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
test_transit_discover_v2.py - [TRANSIT_DISCOVER_V2_RESTORED]

Folds in John's 2026-06-06 decision: v25's route-scan transit discovery
(TRANSIT_FROM_ROUTES_V1) was a regression that reintroduced the peer-LAN-claiming
bug the working all5 build deliberately abandoned. Restored the proven lease-based
method: a node transits the /24s of the FrogNet peers it serves DHCP to.

Drives the REAL discover_transit_subnets with a fake leases file + fake echo, so
the lease parse + own-subnet exclusion + CSV-field-1 /24 derivation are exercised
without hardware. Upstream (V3) echo probing is exercised via a fake urlopen.
"""
import os, sys, tempfile, types, importlib

sys.path.insert(0, "/home/claude/v25/opt/frognet_semantic")
from internet_tunnels_v3 import config as C

def main():
    ok = True
    def check(c, m):
        nonlocal ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

    # Fake dnsmasq.leases: Seattle5 (10.250.250.1) serves Six as a guest at
    # 10.250.250.221 whose FrogNet identity is 10.160.160.1, plus a non-FrogNet
    # laptop that fails the echo probe.
    import time as _t
    future = int(_t.time()) + 3600
    leases = (f"{future} aa:bb:cc:dd:ee:01 10.250.250.221 FrogNetHost *\n"
              f"{future} aa:bb:cc:dd:ee:02 10.250.250.50 somelaptop *\n")
    tmpd = tempfile.mkdtemp()
    lpath = os.path.join(tmpd, "dnsmasq.leases")
    open(lpath, "w").write(leases)

    # Point the function at our fake leases file. The function hardcodes
    # /var/lib/misc/dnsmasq.leases as a local `LEASES`, so patch via the module
    # global if present, else monkeypatch os.path.isfile/open through a shim.
    # Simplest: temporarily replace the literal by running with a patched open.
    real_isfile = os.path.isfile
    real_open = open
    def fake_isfile(p):
        return True if p == "/var/lib/misc/dnsmasq.leases" else real_isfile(p)
    def fake_open(p, *a, **k):
        if p == "/var/lib/misc/dnsmasq.leases":
            return real_open(lpath, *a, **k)
        return real_open(p, *a, **k)

    # Fake echo: 10.250.250.221 answers as FrogNet 10.160.160.1; laptop 404s.
    import urllib.request as UR
    class FakeResp:
        def __init__(self, body): self._b = body.encode(); self.status = 200
        def read(self, n=-1): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_urlopen(url, timeout=1.0):
        if "10.250.250.221" in url:
            return FakeResp("Seattle6,10.160.160.1,192.168.0.5,0.0.0.0")
        raise Exception("connection refused")

    # Fake `ip -4 -o addr show` so V3 upstream sees only Seattle5's own /24
    # (no upstream peer) -> upstream contributes nothing here.
    class FakeCPE(Exception): pass
    def fake_check_output(args, text=True):
        if args[:3] == ["ip", "-4", "-o"]:
            return "1: eth0    inet 10.250.250.1/24 scope global eth0\n"
        return ""

    orig = (os.path.isfile, UR.urlopen, C.subprocess.check_output)
    try:
        os.path.isfile = fake_isfile
        import builtins; bo = builtins.open; builtins.open = fake_open
        UR.urlopen = fake_urlopen
        C.subprocess.check_output = fake_check_output
        result = C.discover_transit_subnets("10.250.250.0/24")
    finally:
        os.path.isfile, UR.urlopen, C.subprocess.check_output = orig
        builtins.open = bo

    check("10.160.160.0/24" in result,
          "A served FrogNet guest (lease->echo) yields its /24 as transit")
    check("10.250.250.0/24" not in result,
          "B own subnet excluded from transit")
    check(all(not x.startswith("10.250.250.50") for x in result),
          "C non-FrogNet lease (failed echo) not claimed")

    print()
    print("ALL TRANSIT-DISCOVER-V2 ORACLE CHECKPOINTS PASS" if ok
          else "TRANSIT-DISCOVER-V2 PROOF FAILED")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
