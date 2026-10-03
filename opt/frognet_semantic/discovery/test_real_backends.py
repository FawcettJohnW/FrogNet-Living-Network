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
test_real_backends.py - exercise the network-free parts of the Real backends:
  - echo CSV validation against the oracle's 8 echo CSVs (BABox empty rejected)
  - frognet_alive rtt parse
  - getHosts JSON parse
  - RealBroker file I/O against a fixture handshake_rtts.json + active/*.json,
    reproducing the oracle broker mapping + channel_for_iface
The subprocess/network methods (echo_probe/get_hosts/measure_rtt/ping) are
UNVALIDATED here (no box) and flagged accordingly.
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.real_backends import (
    valid_echo, parse_rtt, parse_gethosts, broker_lookup, RealBroker)

def main():
    ok = True

    # echo regex vs oracle CSVs
    valid = [
        "BAMacBook,10.179.179.1,172.16.26.155,0.0.0.0",
        "Seattle5,10.250.250.1,192.168.0.27,192.168.0.21",
        "Seattle3,10.130.130.1,0.0.0.0,0.0.0.0",
        "FrogNetHost.New-York-1,10.102.60.1,192.168.1.245,0.0.0.0",
        "New-York-2,10.28.28.1,10.102.60.230,0.0.0.0",
    ]
    if all(valid_echo(c) for c in valid) and not valid_echo("") and not valid_echo("garbage"):
        print("  PASS echo CSV validation (5 valid, empty+garbage rejected)")
    else:
        ok = False; print("  FAIL echo validation")

    # rtt parse
    if parse_rtt("194|fast") == "194" and parse_rtt("0|x") == "1" and parse_rtt("") == "" and parse_rtt("252.7|sem") == "252":
        print("  PASS frognet_alive rtt parse")
    else:
        ok = False; print("  FAIL rtt parse")

    # getHosts parse
    g = parse_gethosts('[{"ip":"10.250.250.1","name":"S5"},{"ip":"10.130.130.1"},{"x":1}]')
    if g == [("10.250.250.1", "S5"), ("10.130.130.1", "")] and parse_gethosts("nope") == []:
        print("  PASS getHosts JSON parse")
    else:
        ok = False; print(f"  FAIL getHosts parse: {g}")

    # broker_lookup pure
    hs = {
        "BAMacBook-10.179.179": {"peer_dot_one": "10.179.179.1", "subnet": "10.179.179.0/24"},
        "Seattle5-10.250.250": {"peer_dot_one": "10.250.250.1", "subnet": "10.250.250.0/24"},
        "BABox-10.111.11": {"peer_dot_one": "10.111.11.1", "subnet": "10.111.11.0/24"},
    }
    if (broker_lookup(hs, "10.179.179.1") == ("BAMacBook-10.179.179", "10.179.179.0/24", "10.179.179.1")
            and broker_lookup(hs, "10.130.130.1") is None):
        print("  PASS broker_lookup (direct peers resolve, relayed -> None)")
    else:
        ok = False; print("  FAIL broker_lookup")

    # RealBroker file I/O (actually exercises the production read path)
    with tempfile.TemporaryDirectory() as d:
        hp = os.path.join(d, "handshake_rtts.json")
        ad = os.path.join(d, "active"); os.mkdir(ad)
        json.dump(hs, open(hp, "w"))
        json.dump({"interface": "wg1", "channel_name": "BAMacBook-10.179.179"},
                  open(os.path.join(ad, "wg1.json"), "w"))
        json.dump({"interface": "wg2", "channel_name": "Seattle5-10.250.250"},
                  open(os.path.join(ad, "wg2.json"), "w"))
        rb = RealBroker(handshake_path=hp, active_dir=ad)
        if (rb.broker_for_peer_ip("10.250.250.1") == ("Seattle5-10.250.250", "10.250.250.0/24", "10.250.250.1")
                and rb.channel_for_iface("wg1") == "BAMacBook-10.179.179"
                and rb.channel_for_iface("wg2") == "Seattle5-10.250.250"):
            print("  PASS RealBroker file I/O (handshake + active/*.json)")
        else:
            ok = False; print("  FAIL RealBroker file I/O")

    print()
    print("ALL REAL-BACKEND CHECKS PASS" if ok else "REAL-BACKEND CHECKS FAILED")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
