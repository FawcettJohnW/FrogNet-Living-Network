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
test_pingpong_return_ip_oracle.py - [PINGPONG_RETURN_DEV_V1]

Proves _get_alive_return_ip(dev) returns the EGRESS DEVICE's local 10.x, not the
node's eth0 identity. Folds in John's 2026-06-19 finding: the old dev-blind
selector stamped 10.250.250.1 (eth0 identity) on every probe, so a wg-bound
direct probe advertised a fabric-routed return addr -> hairpin -> false RTT_LOOP.

Pure-function test: ip-addr + gethostname are mocked. The discriminator vs old
code is the wg case (NEW=tunnel /30; OLD ignored dev -> always hostname identity,
and OLD's signature took no arg so _get_alive_return_ip("wg2") TypeErrors).
"""
import sys, os, socket
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import discovery.real_backends as rb

_ADDR = {
    "wg2":   "10: wg2  inet 10.253.203.118/30 scope global wg2\n",
    "wg0":   "8: wg0   inet 10.253.203.126/30 scope global wg0\n",
    "eth0":  ("2: eth0  inet 10.250.250.1/24 brd 10.250.250.255 scope global eth0\n"
              "2: eth0  inet 10.250.250.2/24 brd 10.250.250.255 scope global secondary eth0\n"),
    "wlan1": "6: wlan1 inet 192.168.0.19/24 scope global wlan1\n",   # no 10.x -> fallback
}


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    rb.subprocess.check_output = lambda cmd, text=True: _ADDR.get(cmd[-1], "")
    socket.gethostname = lambda: "FrogNetHost"
    # [HOSTS_ONLY_V1] The identity fallback reads /etc/hosts now, not the resolver --
    # on a node every hostname is "FrogNetHost" and resolv.conf starts
    # `nameserver 127.0.0.1`, so asking DNS what our own name means is asking a
    # question with a different answer per box. Stub the file, not getaddrinfo.
    import core.hosts_only as _ho
    _ho._cached_map = {"FrogNetHost": "10.250.250.1"}
    _ho._cached_at = float("inf")

    check(rb._get_alive_return_ip("wg2") == "10.253.203.118",
          "wg2 -> its own /30 (10.253.203.118), NOT the eth0 identity")
    check(rb._get_alive_return_ip("wg0") == "10.253.203.126",
          "wg0 -> its own /30 (10.253.203.126)")
    check(rb._get_alive_return_ip("eth0") == "10.250.250.1",
          "eth0 -> its own 10.x (eth0 IS the egress here, so identity is correct)")
    check(rb._get_alive_return_ip("wlan1") == "10.250.250.1",
          "wlan1 has no 10.x -> hostname-identity fallback")
    check(rb._get_alive_return_ip() == "10.250.250.1",
          "no dev -> hostname identity (legacy fallback preserved)")

    print()
    print("ALL RETURN-IP ORACLE CHECKPOINTS PASS" if ok
          else "RETURN-IP ORACLE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
