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
sim/databasehost_convergence_check.py - every host must elect the SAME databasehost.

Reproduces "I run merges across all hosts and they all get different answers."
Each node reads the SAME capability set from databasehost_control (deterministic
highest-.1) and runs the REAL election (frognet_role_elect + database_handler).
The election is pure over that set, so every vantage MUST land on the same winner.

The break: one candidate's capability blob has a DICT where score() expects a
number (here mem_available_kb). database_handler.score() does raw int(...) on it
and throws; database_handler.evaluate() called score() with no guard, so ONE bad
blob aborted the whole role's evaluate -> the merge fell to its fallback. That
fallback is exactly where hosts diverge: a build that self-elects on failure has
every node crown ITSELF -> N different answers; a build that returns None elects
nobody. Either way the legitimate winner (.130) is thrown away.

PROVES (with the [EVAL_ISOLATE_V1] guard in evaluate):
  1. CLEAN set  -> all vantages converge on the same, correct winner (.130).
  2. POISONED set (one dict-in-a-score-field blob) -> evaluate SKIPS that
     candidate and still elects .130 from EVERY vantage. No crash, no fallback,
     no divergence.
REGRESSION (revert the guard): RE.elect raises on the poisoned set -> the merge
cannot complete the election -> the fallback divergence the log shows.
"""
from __future__ import annotations
import os, sys, types, time

_HERE = os.path.abspath(__file__)
_ROOT = _HERE
for _ in range(3):
    _ROOT = os.path.dirname(_ROOT)                      # .../opt/frognet_semantic
_WORK = os.path.dirname(os.path.dirname(_ROOT))         # .../right_discovery
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_WORK, "etc", "frognet_bundles", "communicator"))

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems:
            print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

_FAKE_T = None

def install_tuples(db_caps, reach_wan, my_ip):
    """Fake transient that honors fresh_s. One singleton module bound under both
    the flat and the canonical core name (the moved election code imports
    core.frognet_tuples)."""
    global _FAKE_T
    now = int(time.time())
    rows = {("discovery", "reach_plane"): [
        {"addr": my_ip, "var": "reach_plane",
         "value": {"wan_subnets": reach_wan, "self_ip": my_ip, "ts": now}}]}
    rr = []
    for ip, cap in db_caps.items():
        rr.append({"addr": ip, "var": "capability",
                   "value": {"capability": dict(cap), "loadavg": {"1": 0.1},
                             "temps_c": [], "ts": now}})
    rows[("databasehost", "capability")] = rr
    if _FAKE_T is None:
        T = types.ModuleType("frognet_tuples")
        def _get(service, var, dbhost=None, fresh_s=0, timeout=4.0):
            out = T._rows.get((service, var), [])
            if fresh_s:
                t = int(time.time())
                out = [r for r in out if t - int(r["value"].get("ts", 0)) <= fresh_s]
            return out
        T.my_ip = lambda: T._my_ip
        T.get = _get
        T.put = lambda *a, **k: True
        T.DEFAULT_DBHOST = "databasehost_control.frognet"
        _FAKE_T = T
        sys.modules["frognet_tuples"] = T
        sys.modules["core.frognet_tuples"] = T
        import core as _core
        _core.frognet_tuples = T
    _FAKE_T._rows = rows
    _FAKE_T._my_ip = my_ip


def fresh_handlers():
    for m in ("frognet_role_elect", "frognet_service_hosts",
              "core.frognet_role_elect", "core.frognet_service_hosts",
              "core.database_handler"):
        sys.modules.pop(m, None)
    import frognet_role_elect as RE
    from core.database_handler import DatabaseRoleHandler
    return RE, DatabaseRoleHandler


# Real-ish pond. .130 is the legitimate best mysql box; .179 is the poison blob
# (mem_available_kb is a DICT - exactly the int()-not-'dict' the log throws).
def _good(ip, mem_gb, cpu, wmbps):
    return dict(lan_ip=ip, mysql_running=1, mem_available_kb=mem_gb * 1024 * 1024,
                cores=4, cpu_bench_total=cpu, disk_write_mbps=wmbps, disk_fsync_ms=3)

CLEAN = {
    "10.130.130.1": _good("10.130.130.1", 8, 9000, 120),   # Seattle3 - best
    "10.120.120.1": _good("10.120.120.1", 4, 4000, 60),    # Seattle2
    "10.250.250.1": _good("10.250.250.1", 2, 1500, 40),    # Seattle5 (highest IP, weakest)
}
POISON = dict(CLEAN)
POISON["10.179.179.1"] = dict(lan_ip="10.179.179.1", mysql_running=1,
                              mem_available_kb={"clobbered": [1, 2, 3]},   # <-- dict, not a number
                              cores=4, cpu_bench_total=9000, disk_write_mbps=120, disk_fsync_ms=3)

VANTAGES = ["10.130.130.1", "10.120.120.1", "10.250.250.1", "10.102.60.1"]
REACH = []   # island: all candidates are LAN to the election
EXPECT = "10.130.130.1"


def _elect_from(my_ip, caps):
    install_tuples(caps, REACH, my_ip)
    RE, DBH = fresh_handlers()
    try:
        win = RE.elect(DBH())
        return win["lan_ip"] if win else "NONE"
    except Exception as e:
        return f"CRASH:{type(e).__name__}"


def run():
    print("=== every host elects the SAME databasehost over one shared set ===")

    clean = {v: _elect_from(v, CLEAN) for v in VANTAGES}
    probs = []
    if len(set(clean.values())) != 1:
        probs.append(f"clean set DIVERGED across hosts: {clean}")
    if set(clean.values()) != {EXPECT}:
        probs.append(f"clean winner should be {EXPECT} everywhere, got {clean}")
    check("[CONVERGE] clean capability set -> all hosts agree on .130", probs)

    poisoned = {v: _elect_from(v, POISON) for v in VANTAGES}
    probs = []
    if len(set(poisoned.values())) != 1:
        probs.append(f"poisoned set DIVERGED across hosts: {poisoned}  "
                     f"(one malformed blob aborted evaluate -> fallback per node)")
    if set(poisoned.values()) != {EXPECT}:
        probs.append(f"with the guard, the malformed blob is skipped and {EXPECT} "
                     f"must still win everywhere, got {poisoned}")
    check("[CONVERGE] one malformed blob present -> still all agree on .130 (no divergence)", probs)

    # The election is pure over the candidate set it reads. So once the crash is
    # gone, the ONLY remaining way hosts disagree is if they read DIFFERENT sets -
    # i.e. databasehost_control.frognet resolves to different nodes per vantage
    # because discovery has not converged on the control host. Prove that: feed
    # each node a different "control view" and watch them diverge even WITH the
    # guard. This is why divergence can persist after the malformed blob is fixed.
    viewA = {k: CLEAN[k] for k in ("10.130.130.1", "10.120.120.1")}   # node A's control sees these
    viewB = {k: CLEAN[k] for k in ("10.250.250.1",)}                  # node B's control sees only itself
    a = _elect_from("10.130.130.1", viewA)
    b = _elect_from("10.250.250.1", viewB)
    probs = []
    if a == b:
        probs.append(f"expected divergence on mismatched control views, both got {a}")
    check("[DIAGNOSIS] mismatched control-host data -> hosts diverge even WITH the guard "
          f"(A={a} B={b}); persistent divergence == discovery/control-resolution, not the election", probs)

    print()
    print("  clean   :", clean)
    print("  poisoned:", poisoned)
    print()
    print("ALL DATABASEHOST-CONVERGENCE CHECKS PASS" if not FAILS
          else "DATABASEHOST-CONVERGENCE FAILURES ABOVE")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(run())
