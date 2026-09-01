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
test_kernel_oracle.py - prove the FakeKernel `ip route` engine is faithful by
replaying the EXACT mutation sequence from the uploaded merge log (the oracle)
and asserting the resulting table equals the oracle's own readback at each
checkpoint:

  1. seed = enter snapshot (connected/kernel + pre-existing winner routes)
  2. replay every promote-stage `ip route ...` spec (install_if_changed)
  3. assert full table == oracle SNAP label=post_promote (main)
  4. replay the exit sweep, assert table == oracle final `ip r` (10.x rows)

If this passes, the route-mutation chokepoint is proven against real kernel
behavior and everything built on top of it stands on solid ground.

Run: python3 test_kernel_oracle.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel  # noqa: E402


# --- ORACLE checkpoint 1: connected/kernel + pre-existing routes at `enter` ---
# (verbatim from [DIAG-ROUTE] SNAP label=enter main ...)
ENTER_SEED = [
    "10.28.28.0/24 via 10.28.28.1 dev eth0 metric 22 onlink",
    "10.102.60.0/24 dev eth0 proto kernel scope link src 10.102.60.1",
    "10.111.11.0/24 dev wg0 scope link metric 22",
    "10.179.179.0/24 dev wg1 scope link metric 22",
    "10.250.250.0/24 dev wg2 scope link metric 22",
    "10.253.203.88/30 dev wg2 proto kernel scope link src 10.253.203.90",
    "10.253.203.96/30 dev wg0 proto kernel scope link src 10.253.203.98",
    "10.253.203.104/30 dev wg1 proto kernel scope link src 10.253.203.106",
    "10.254.1.0/24 dev frognet0 proto kernel scope link src 10.254.1.4",
]

# --- ORACLE: promote-stage mutations, in order, as the bash `ip ...` argv ----
# KEEP lines (route_matches True) are NO-OPs - included as comments for fidelity.
# Each tuple is the args passed to kernel.route(*args).
PROMOTE_MUTATIONS = [
    # KEEP 10.179.179.0/24 (already correct)
    ("replace", "10.179.179.2/32", "dev", "wg1", "metric", "5", "src", "10.253.203.106"),
    # [HOP_VIA_V1] NY-2 is a relayed host: replace the stale onlink seed with the
    # one-hop route via the uplink parent (10.102.60.230), alias follows the via.
    ("replace", "10.28.28.0/24", "via", "10.102.60.230", "dev", "eth0", "metric", "22"),
    ("replace", "10.28.28.2/32", "via", "10.102.60.230", "dev", "eth0", "metric", "5"),
    ("replace", "10.160.160.0/24", "dev", "wg2", "metric", "22", "src", "10.253.203.90"),
    ("replace", "10.160.160.2/32", "dev", "wg2", "metric", "5", "src", "10.253.203.90"),
    ("replace", "10.160.160.0/24", "dev", "wg1", "metric", "100", "src", "10.253.203.106"),
    # KEEP 10.250.250.0/24 (already correct)
    ("replace", "10.250.250.2/32", "dev", "wg2", "metric", "5", "src", "10.253.203.90"),
    ("replace", "10.120.120.0/24", "dev", "wg2", "metric", "22", "src", "10.253.203.90"),
    ("replace", "10.120.120.2/32", "dev", "wg2", "metric", "5", "src", "10.253.203.90"),
    ("replace", "10.130.130.0/24", "dev", "wg1", "metric", "22", "src", "10.253.203.106"),
    ("replace", "10.130.130.2/32", "dev", "wg1", "metric", "5", "src", "10.253.203.106"),
    ("replace", "10.130.130.0/24", "dev", "wg2", "metric", "100", "src", "10.253.203.90"),
]

# --- ORACLE checkpoint 2: SNAP label=post_promote main (verbatim, sorted) -----
ORACLE_POST_PROMOTE = [
    "10.28.28.0/24 via 10.102.60.230 dev eth0 metric 22",
    "10.28.28.2 via 10.102.60.230 dev eth0 metric 5",
    "10.102.60.0/24 dev eth0 proto kernel scope link src 10.102.60.1",
    "10.111.11.0/24 dev wg0 scope link metric 22",
    "10.120.120.0/24 dev wg2 scope link src 10.253.203.90 metric 22",
    "10.120.120.2 dev wg2 scope link src 10.253.203.90 metric 5",
    "10.130.130.0/24 dev wg1 scope link src 10.253.203.106 metric 22",
    "10.130.130.0/24 dev wg2 scope link src 10.253.203.90 metric 100",
    "10.130.130.2 dev wg1 scope link src 10.253.203.106 metric 5",
    "10.160.160.0/24 dev wg2 scope link src 10.253.203.90 metric 22",
    "10.160.160.0/24 dev wg1 scope link src 10.253.203.106 metric 100",
    "10.160.160.2 dev wg2 scope link src 10.253.203.90 metric 5",
    "10.179.179.0/24 dev wg1 scope link metric 22",
    "10.179.179.2 dev wg1 scope link src 10.253.203.106 metric 5",
    "10.250.250.0/24 dev wg2 scope link metric 22",
    "10.250.250.2 dev wg2 scope link src 10.253.203.90 metric 5",
    "10.253.203.88/30 dev wg2 proto kernel scope link src 10.253.203.90",
    "10.253.203.96/30 dev wg0 proto kernel scope link src 10.253.203.98",
    "10.253.203.104/30 dev wg1 proto kernel scope link src 10.253.203.106",
    "10.254.1.0/24 dev frognet0 proto kernel scope link src 10.254.1.4",
]

# --- ORACLE: exit sweep deletions (verbatim from _sweep_probe_routes) ---------
# NOTE: tar source filters `metric 6`; oracle deletes the metric-5 .2 aliases.
# Behavior taken from the LOG (authority). FLAGGED in STATUS for live-box check.
EXIT_SWEEP = [
    ("del", "10.28.28.2"),
    ("del", "10.120.120.2"),
    ("del", "10.130.130.2"),
    ("del", "10.160.160.2"),
    ("del", "10.179.179.2"),
    ("del", "10.250.250.2"),
]

# --- ORACLE checkpoint 3: final `ip r`, 10.x rows only (verbatim) -------------
ORACLE_FINAL_IPR = [
    "10.28.28.0/24 via 10.102.60.230 dev eth0 metric 22",
    "10.102.60.0/24 dev eth0 proto kernel scope link src 10.102.60.1",
    "10.111.11.0/24 dev wg0 scope link metric 22",
    "10.120.120.0/24 dev wg2 scope link src 10.253.203.90 metric 22",
    "10.130.130.0/24 dev wg1 scope link src 10.253.203.106 metric 22",
    "10.130.130.0/24 dev wg2 scope link src 10.253.203.90 metric 100",
    "10.160.160.0/24 dev wg2 scope link src 10.253.203.90 metric 22",
    "10.160.160.0/24 dev wg1 scope link src 10.253.203.106 metric 100",
    "10.179.179.0/24 dev wg1 scope link metric 22",
    "10.250.250.0/24 dev wg2 scope link metric 22",
    "10.253.203.88/30 dev wg2 proto kernel scope link src 10.253.203.90",
    "10.253.203.96/30 dev wg0 proto kernel scope link src 10.253.203.98",
    "10.253.203.104/30 dev wg1 proto kernel scope link src 10.253.203.106",
    "10.254.1.0/24 dev frognet0 proto kernel scope link src 10.254.1.4",
]


# [REAP_STALE_WINNERS_V1] Canonical post-reap final table for the FULL-DISCOVERY
# oracles (walk / hosts / fabric / runmerge). Those run the real discovery +
# reaper, where runMerge does a complete discovery every pass and the winners are
# the whole truth - so 10.111.11 dev wg0 (BABox over wg0, NOT discovered as a
# winner in those flows) is reaped. test_kernel_oracle replays a fixed mutation
# list with NO reaper and keeps ORACLE_FINAL_IPR for its own checkpoints; the
# difference between the two tables IS the reaper's effect.
ORACLE_FINAL_IPR_REAPED = [r for r in ORACLE_FINAL_IPR
                          if r != "10.111.11.0/24 dev wg0 scope link metric 22"]


def _diff(label, got, want):
    if got == want:
        print(f"  PASS {label} ({len(got)} routes)")
        return True
    print(f"  FAIL {label}")
    gs, ws = set(got), set(want)
    for line in want:
        if line not in gs:
            print(f"    MISSING: {line}")
    for line in got:
        if line not in ws:
            print(f"    EXTRA  : {line}")
    # ordering check if sets equal
    if gs == ws and got != want:
        print("    (set matches but ORDER differs)")
        for i, (a, b) in enumerate(zip(got, want)):
            if a != b:
                print(f"    order@{i}: got={a!r} want={b!r}")
                break
    return False


def main():
    ok = True
    k = FakeKernel()
    k.seed(*ENTER_SEED)
    ok &= _diff("checkpoint-1 enter seed", k.table(), ENTER_SEED)

    for m in PROMOTE_MUTATIONS:
        rc = k.route(*m)
        assert rc == 0, f"mutation rc!=0: {m}"

    ok &= _diff("checkpoint-2 post_promote", k.table(), ORACLE_POST_PROMOTE)

    for m in EXIT_SWEEP:
        k.route(*m)
    ok &= _diff("checkpoint-3 final ip r", k.table(), ORACLE_FINAL_IPR)

    print()
    if ok:
        print("ALL KERNEL ORACLE CHECKPOINTS PASS")
        return 0
    print("KERNEL ORACLE PROOF FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
