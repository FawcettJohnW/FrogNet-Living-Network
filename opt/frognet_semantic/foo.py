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
"""diag_child_onlink.py - one-shot instrumentation for test_child_onlink_uplink_oracle.

The oracle's final table on Seattle5 is byte-for-byte the two SEEDED routes, so
descend()+promote() added nothing. Every module it touches is checksum-identical
to the tarball where it passes, so this dumps the actual call sequence rather
than reasoning about it further.

Logs, in order: what descend is handed, every getHosts/echo/rtt/verify call and
its answer, every kernel route command and its rc, and the final table.

Run:
    cd /opt/frognet_semantic
    FROGNET_SENTINEL_DIR=$(mktemp -d) PYTHONPATH=. python3 diag_child_onlink.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from discovery.discovery import Discovery
from discovery.routes import Routes
from discovery.kernel import FakeKernel
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               FakeReflect, HostStore)
from discovery import descend as _descend

IDENT = "10.130.130.1"
MESH = [("10.130.130.1", "Seattle3"), ("10.250.250.1", "Seattle5"),
        ("10.102.60.1", "New-York-1"), ("10.111.11.1", "BABox")]

LOG = []
def log(tag, *a):
    line = f"[{tag}] " + " ".join(str(x) for x in a)
    LOG.append(line)
    print(line, flush=True)


def wrap(obj, name, label):
    """Log every call to obj.name and its return value."""
    if not hasattr(obj, name):
        log("WRAP-MISSING", f"{label}.{name} does not exist")
        return
    orig = getattr(obj, name)
    def w(*a, **k):
        try:
            r = orig(*a, **k)
        except Exception as e:
            log(label, f"{name}{a} -> RAISED {e!r}")
            raise
        log(label, f"{name}{a} -> {r!r}")
        return r
    try:
        setattr(obj, name, w)
    except Exception as e:
        log("WRAP-FAIL", f"{label}.{name}: {e!r}")


def main():
    echo = FakeEcho(answers={
        "10.250.250.1": "Seattle5,10.250.250.1,,",
        "10.250.250.2": "Seattle5,10.250.250.1,,",
    })
    rtt = FakeRtt(table={("wlan1", "10.250.250.1"): [20],
                         ("wlan1", "10.250.250.2"): [20]})
    gethosts = FakeGetHosts(children={"10.250.250.1": MESH})
    broker = FakeBroker(by_one={}, iface_channel={})

    k = FakeKernel(strict_gateway=True)
    k.seed(
        "10.130.130.0/24 dev wlan0 proto kernel scope link src 10.130.130.1",
        "default via 10.250.250.1 dev wlan1 onlink metric 601",
    )
    log("SEED", "\n        ".join(k.route_show().splitlines()))

    # every kernel route command + rc
    _route = k.route
    def route(*a, **kw):
        rc = _route(*a, **kw)
        log("KERNEL", "route", " ".join(str(x) for x in a), f"-> rc={rc}")
        return rc
    k.route = route

    routes = Routes(k, logger=lambda s: log("ROUTES", s), clock=lambda: 0.0)

    reflect = FakeReflect(kernel=k, reach={
        ("10.102.60.2", "wlan1", "10.250.250.1"),
        ("10.111.11.2", "wlan1", "10.250.250.1"),
    })

    for o, nm, lb in ((echo, "get", "ECHO"), (echo, "probe", "ECHO"),
                      (rtt, "measure", "RTT"), (rtt, "measure_or_loop", "RTT"),
                      (gethosts, "get", "GETHOSTS"),
                      (gethosts, "children_of", "GETHOSTS"),
                      (reflect, "reaches", "REFLECT")):
        wrap(o, nm, lb)

    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     local_ips={"10.130.130.1", "10.130.130.2",
                                "10.250.250.30", "127.0.0.1"},
                     dev_src_map={"wlan0": "10.130.130.1",
                                  "wlan1": "10.250.250.30"},
                     reflect=reflect, self_identity=IDENT,
                     logger=lambda s: log("DISC", s))
    disc.DEAD_IFACES = set()

    for nm in ("walk", "promote", "record", "consider"):
        wrap(disc, nm, "DISC")

    immediate = [("10.250.250.1", "wlan1")]
    log("CALL", f"descend(disc, ['wlan0','wlan1'], immediate={immediate})")
    try:
        _descend.descend(disc, ["wlan0", "wlan1"], immediate)
    except Exception as e:
        import traceback
        log("DESCEND", f"RAISED {e!r}")
        traceback.print_exc()
    log("CALL", "promote()")
    try:
        disc.promote()
    except Exception as e:
        import traceback
        log("PROMOTE", f"RAISED {e!r}")
        traceback.print_exc()

    print("\n=== FINAL TABLE ===")
    print(k.route_show())
    print("\n=== WANTED ===")
    print("  10.102.60.0/24 via 10.250.250.1 dev wlan1")
    print("  10.111.11.0/24 via 10.250.250.1 dev wlan1")
    print(f"\n=== {len(LOG)} instrumented events above ===")


if __name__ == "__main__":
    main()
