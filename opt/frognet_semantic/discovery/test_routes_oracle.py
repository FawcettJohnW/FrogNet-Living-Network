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
test_routes_oracle.py - prove the decision layer (route_matches /
install_if_changed / sweep_probe_routes) reproduces the oracle's promote stage:
the KEEP-vs-write decisions AND the final table, driven through Routes (which
goes through RTMUT -> kernel), not by hand-applied mutations.

Promote calls are replayed in the oracle's exact dest order, with the exact
(dest, via, dev, metric, onlink, src) the bash promote() computed.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel          # noqa: E402
from discovery.routes import Routes               # noqa: E402
from discovery.test_kernel_oracle import (        # noqa: E402
    ENTER_SEED, ORACLE_POST_PROMOTE, ORACLE_FINAL_IPR, _diff,
)

# (dest, via, dev, metric, onlink, src, expect)  expect in {"KEEP","INSTALL"}
# Order = oracle promote() hash order; values = oracle PROMOTE_*/install_if_changed.
PROMOTE_CALLS = [
    ("10.179.179.0/24", "",           "wg1", 22, 0, "",               "KEEP"),
    ("10.179.179.2/32", "",           "wg1",  5, 0, "10.253.203.106", "INSTALL"),
    ("10.28.28.0/24",   "10.102.60.230", "eth0",22, 0, "",            "INSTALL"),
    ("10.28.28.2/32",   "10.102.60.230", "eth0", 5, 0, "",            "INSTALL"),
    ("10.160.160.0/24", "",           "wg2", 22, 0, "10.253.203.90",  "INSTALL"),
    ("10.160.160.2/32", "",           "wg2",  5, 0, "10.253.203.90",  "INSTALL"),
    ("10.160.160.0/24", "",           "wg1",100, 0, "10.253.203.106", "INSTALL"),
    ("10.250.250.0/24", "",           "wg2", 22, 0, "",               "KEEP"),
    ("10.250.250.2/32", "",           "wg2",  5, 0, "10.253.203.90",  "INSTALL"),
    ("10.120.120.0/24", "",           "wg2", 22, 0, "10.253.203.90",  "INSTALL"),
    ("10.120.120.2/32", "",           "wg2",  5, 0, "10.253.203.90",  "INSTALL"),
    ("10.130.130.0/24", "",           "wg1", 22, 0, "10.253.203.106", "INSTALL"),
    ("10.130.130.2/32", "",           "wg1",  5, 0, "10.253.203.106", "INSTALL"),
    ("10.130.130.0/24", "",           "wg2",100, 0, "10.253.203.90",  "INSTALL"),
]


def main():
    ok = True
    logs = []
    k = FakeKernel(); k.seed(*ENTER_SEED)
    r = Routes(k, logger=logs.append, clock=lambda: 0.0)

    decisions = []
    for dest, via, dev, metric, onlink, src, _ in PROMOTE_CALLS:
        before = len(logs)
        r.install_if_changed(dest, via, dev, metric, onlink, src)
        emitted = logs[before:]
        if any(l.startswith("[DIAG-ROUTE] KEEP") for l in emitted):
            decisions.append("KEEP")
        elif any(l.startswith("ROUTE_INSTALL ") for l in emitted):
            decisions.append("INSTALL")
        else:
            decisions.append("?")

    expect = [c[6] for c in PROMOTE_CALLS]
    if decisions == expect:
        print(f"  PASS decision sequence ({decisions.count('KEEP')} KEEP, "
              f"{decisions.count('INSTALL')} INSTALL)")
    else:
        ok = False
        print("  FAIL decision sequence")
        for i,(d,e,c) in enumerate(zip(decisions,expect,PROMOTE_CALLS)):
            if d != e:
                print(f"    @{i} {c[0]} m{c[3]}: got {d} want {e}")

    ok &= _diff("post_promote table", k.table(), ORACLE_POST_PROMOTE)

    swept = r.sweep_probe_routes()
    if swept == 6:
        print("  PASS sweep count (6)")
    else:
        ok = False; print(f"  FAIL sweep count: got {swept} want 6")
    ok &= _diff("final ip r", k.table(), ORACLE_FINAL_IPR)

    print()
    print("ALL ROUTES ORACLE CHECKPOINTS PASS" if ok else "ROUTES ORACLE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
