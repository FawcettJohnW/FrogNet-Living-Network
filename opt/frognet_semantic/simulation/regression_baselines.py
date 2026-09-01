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
regression_baselines.py - the closed feedback loop's offline half.

The idea: a run BLESSES the observable contract of each topology into a versioned
baseline, and thereafter the offline gate RE-RUNS the real planner+committer and
asserts the output still matches. A hardware run blesses the truth (committed
route tables read back from the real kernel); the offline gate then catches any
regression in planner/committer/harness WITHOUT going back to the box.

Three artifacts, all JSON on disk, all replayable offline:

  baselines/<builder>.json    - per-topology contract:
        committed_routes  : {node: [canonical route string, ...]}  (the /24 table)
        reachability      : {pairs_ok, pairs_total, unreachable[]}
        convergence_cycles: int      (reported, not gated - timing varies)
        source            : "hardware" | "offline"
     The committed-route table needs NO probe to capture: on the box it's
     `ip route show` readback (real truth); offline it's the real engine over
     FakeIPRoute. A diff between current-offline and a hardware baseline is
     exactly a regression signal.

  failure_scenarios/<name>.json - a fault + its honest expectation, e.g.
        {topology, fault:{op,args}, expect:{survivors_all_reach|no_phantom_to|
         partition:{groups:[[...],...]}}}
     Every captured real fault becomes a permanent offline failure test. The
     replayer reuses live_failures' kill_node/drop_link/_no_phantom_to helpers.

  model_calibration.json (model_feedback.py) - measured RTTs + raw failures.
     apply_calibration() folds measured RTTs into frognet_sim.EDGE_RTT_BY_KIND;
     run_all applies it before the model tier so timing reflects hardware.

Provenance matters: a mismatch against a "hardware" baseline is a real regression
(offline code no longer reproduces blessed kernel truth). A mismatch against an
"offline" baseline is DRIFT since the last bless - possibly intended; re-record
to re-bless. Both are surfaced with the baseline's source so severity is clear.
"""
import datetime
import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from frognet_log import get_logger
from frognet_route.iproute import FakeIPRoute
import frognet_sim as H

log = get_logger("simulation.regression_baselines")

SCHEMA = 1
# [BUILD_STAMP_V1] Bump on every shipped overlay. Printed by run_all + live_engine
# and stamped into each baseline, so a stale or misapplied overlay (e.g. extracted
# to the wrong path) is visible at a glance instead of silently running old code.
SIM_BUILD = "2026-06-05-v9"
BASELINE_DIR = os.environ.get("FROGNET_SIM_BASELINE_DIR",
                              os.path.join(_HERE, "baselines"))
SCENARIO_DIR = os.environ.get("FROGNET_SIM_SCENARIO_DIR",
                              os.path.join(_HERE, "failure_scenarios"))


def _fake_factory(name):
    return FakeIPRoute()


# ---------------------------------------------------------------------------
# Contract capture (backend-agnostic: same code blesses offline OR hardware)
# ---------------------------------------------------------------------------

def _routes_of(topo):
    """Canonical committed-route table per node: sorted route strings.
    Route.__repr__ -> '<dest> [via <via> ]dev <dev> metric <m>'. After a
    converge, node.routes holds connected /24s + committed /24s (mirrored from
    the real OR fake kernel by live_engine._mirror_into_model)."""
    return {n.name: sorted(repr(r) for r in n.routes)
            for n in topo.nodes.values()}


def _reach_of(topo):
    ok, total, fails = H.verify_reachability(topo)
    # normalize: drop the path trace (after ': '), keep the stable header token
    # ('PROD a -> ip (b)') so the unreachable SET is path-independent.
    unreachable = sorted(f.split(": ", 1)[0].strip() for f in fails)
    return {"pairs_ok": ok, "pairs_total": total, "unreachable": unreachable}


def capture(key, topo, cycles, source):
    return {
        "schema": SCHEMA,
        "key": key,
        "topology": topo.label,
        "source": source,
        "sim_build": SIM_BUILD,
        "recorded_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "convergence_cycles": cycles,
        "reachability": _reach_of(topo),
        "committed_routes": _routes_of(topo),
    }


def write_baseline(d, dirpath=BASELINE_DIR):
    os.makedirs(dirpath, exist_ok=True)
    path = os.path.join(dirpath, f"{d['key']}.json")
    with open(path, "w") as f:
        json.dump(d, f, indent=2, sort_keys=True)
    return path


def load_baseline(key, dirpath=BASELINE_DIR):
    with open(os.path.join(dirpath, f"{key}.json")) as f:
        return json.load(f)


def list_baseline_keys(dirpath=BASELINE_DIR):
    return sorted(os.path.splitext(os.path.basename(p))[0]
                  for p in glob.glob(os.path.join(dirpath, "*.json")))


# ---------------------------------------------------------------------------
# Record (bless): offline by default; hardware when driven by a real backend
# ---------------------------------------------------------------------------

def record_all(source="offline", ipr_factory=None, dirpath=BASELINE_DIR):
    """Bless every harness topology's contract into a baseline. Offline by
    default (FakeIPRoute). For a hardware bless, drive it from live_engine.main
    with FROGNET_SIM_BACKEND=real (see live_engine), which captures real kernel
    readback per topology."""
    import live_engine as LE
    factory = ipr_factory or _fake_factory
    written = []
    for bname in LE._BUILDERS:
        builder = getattr(H, bname, None)
        if builder is None:
            continue
        topo = builder()
        cycles = LE.converge_real(topo, ipr_factory=factory)
        write_baseline(capture(bname, topo, cycles, source), dirpath)
        written.append(bname)
    return written


# ---------------------------------------------------------------------------
# Check (regression): re-run the real engine offline, diff against baselines
# ---------------------------------------------------------------------------

def _route_diff(base_routes, cur_routes):
    diffs = {}
    for node in sorted(set(base_routes) | set(cur_routes)):
        b = set(base_routes.get(node, []))
        c = set(cur_routes.get(node, []))
        added, removed = sorted(c - b), sorted(b - c)
        if added or removed:
            diffs[node] = {"added": added, "removed": removed}
    return diffs


def check_all(dirpath=BASELINE_DIR):
    """For each baseline: rebuild the topology, converge through the REAL engine
    offline, and assert committed routes + reachability still match. Returns
    (rc, regressions). rc=0 also when there are no baselines yet (SKIP)."""
    keys = list_baseline_keys(dirpath)
    if not keys:
        print("  [SKIP] no baselines yet - bless with "
              "`python3 run_all.py --record-baselines` (offline) or a hardware "
              "run (see SIM_STATUS)")
        return 0, []
    import live_engine as LE
    regressions = []
    for key in keys:
        base = load_baseline(key, dirpath)
        src = base.get("source", "offline")
        builder = getattr(H, key, None)
        if builder is None:
            print(f"  [FAIL] {key}: baseline exists but builder missing in frognet_sim")
            regressions.append(key)
            continue
        topo = builder()
        cycles = LE.converge_real(topo, ipr_factory=_fake_factory)
        rdiff = _route_diff(base["committed_routes"], _routes_of(topo))
        cur_reach = _reach_of(topo)
        reach_ok = cur_reach == base["reachability"]
        if rdiff or not reach_ok:
            regressions.append(key)
            sev = "REGRESSION vs HARDWARE" if src == "hardware" else "DRIFT vs offline"
            print(f"  [FAIL] {key}: {sev} baseline")
            if not reach_ok:
                print(f"           reachability: baseline {base['reachability']} "
                      f"-> now {cur_reach}")
            for node, d in rdiff.items():
                if d["added"]:
                    print(f"           {node} +{d['added']}")
                if d["removed"]:
                    print(f"           {node} -{d['removed']}")
        else:
            cyc_note = ("" if cycles == base["convergence_cycles"]
                        else f", cycles {base['convergence_cycles']}->{cycles}")
            print(f"  [PASS] {key}: matches {src} baseline{cyc_note}")
    return (1 if regressions else 0), regressions


# ---------------------------------------------------------------------------
# Failure-scenario replay: data-driven faults -> honest-state assertions
# ---------------------------------------------------------------------------

def _partition_ok(topo, groups, ips):
    """Within each group all-pairs reach; across groups NONE reach (honest
    partition, no false reach)."""
    import live_failures as LF
    detail = []
    for gi, g in enumerate(groups):
        for a in g:
            for b in g:
                if a == b or a not in topo.nodes or b not in topo.nodes:
                    continue
                if not LF._delivers(topo, a, ips[b], b):
                    detail.append(f"within g{gi}: {a} !-> {b}")
    for i in range(len(groups)):
        for j in range(len(groups)):
            if i == j:
                continue
            for a in groups[i]:
                for b in groups[j]:
                    if a not in topo.nodes or b not in topo.nodes:
                        continue
                    if LF._delivers(topo, a, ips[b], b):
                        detail.append(f"cross g{i}->g{j}: {a} FALSE-REACHED {b}")
    return (not detail), detail


def _check_expect(topo, expect, ips, dead_net):
    import live_failures as LF
    fails = []
    if expect.get("survivors_all_reach"):
        rf = H.verify_reachability(topo)[2]
        if rf:
            fails.append(f"survivors_all_reach violated: {rf[:2]}")
    if expect.get("no_phantom_to") and dead_net:
        nop, who = LF._no_phantom_to(topo, dead_net)
        if not nop:
            fails.append(f"phantom route to dead /24 held by {who}")
    if "partition" in expect:
        ok, detail = _partition_ok(topo, expect["partition"]["groups"], ips)
        if not ok:
            fails.append(f"partition dishonest: {detail[:3]}")
    return fails


def replay_scenarios(dirpath=SCENARIO_DIR):
    """Replay every saved failure scenario through the REAL engine offline and
    assert its honest expectation. rc=0 with SKIP when none saved."""
    import live_engine as LE
    import live_failures as LF
    files = sorted(glob.glob(os.path.join(dirpath, "*.json")))
    if not files:
        print("  [SKIP] no failure scenarios saved yet")
        return 0
    failed = []
    for fp in files:
        with open(fp) as f:
            scn = json.load(f)
        name = os.path.splitext(os.path.basename(fp))[0]
        builder = getattr(H, scn["topology"], None)
        if builder is None:
            print(f"  [FAIL] {name}: topology {scn['topology']} not in frognet_sim")
            failed.append(name)
            continue
        topo = builder()
        H.assign_identities(topo)
        ips = {n.name: n.frognet_ip for n in topo.nodes.values()}
        LE.converge_real(topo, ipr_factory=_fake_factory)  # baseline converge
        dead_net = None
        op = scn["fault"]["op"]
        args = scn["fault"].get("args", [])
        if op == "kill_node":
            _ip, dead_net = LF.kill_node(topo, *args)
        elif op == "drop_link":
            LF.drop_link(topo, *args)
        else:
            print(f"  [FAIL] {name}: unknown fault op {op!r}")
            failed.append(name)
            continue
        LE.converge_real(topo, ipr_factory=_fake_factory)  # re-converge post-fault
        viol = _check_expect(topo, scn.get("expect", {}), ips, dead_net)
        if viol:
            failed.append(name)
            print(f"  [FAIL] {name} ({scn['topology']}, {op}{args}): {viol}")
        else:
            print(f"  [PASS] {name} ({scn['topology']}, {op}{args})")
    return 1 if failed else 0


# Known-good seed scenarios (the live_failures cases, as portable artifacts).
_SEED_SCENARIOS = {
    "ring4_kill_B": {
        "topology": "topo_ring_4",
        "fault": {"op": "kill_node", "args": ["B"]},
        "expect": {"survivors_all_reach": True, "no_phantom_to": "B"},
    },
    "chain5_kill_C_partition": {
        "topology": "topo_chain_5",
        "fault": {"op": "kill_node", "args": ["C"]},
        "expect": {"no_phantom_to": "C",
                   "partition": {"groups": [["A", "B"], ["D", "E"]]}},
    },
    "pond_kill_NewYork": {
        "topology": "topo_pond_full_mesh",
        "fault": {"op": "kill_node", "args": ["NewYork"]},
        "expect": {"survivors_all_reach": True, "no_phantom_to": "NewYork"},
    },
}


def seed_scenarios(dirpath=SCENARIO_DIR):
    """Write the known-good seed scenarios (idempotent - only missing ones).
    These are the same properties live_failures asserts, made data-driven so a
    captured real fault can be added the same way without touching code."""
    os.makedirs(dirpath, exist_ok=True)
    written = []
    for name, scn in _SEED_SCENARIOS.items():
        path = os.path.join(dirpath, f"{name}.json")
        if not os.path.exists(path):
            with open(path, "w") as f:
                json.dump(scn, f, indent=2, sort_keys=True)
            written.append(name)
    return written


def main():
    """Default: run the offline regression suite (check baselines + replay
    scenarios). --record-baselines blesses current offline behavior + seeds
    scenarios. --record-source sets provenance for a record run."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--record-baselines", action="store_true",
                    help="bless current behavior into baselines + seed scenarios")
    ap.add_argument("--record-source", default="offline",
                    choices=["offline", "hardware"])
    args = ap.parse_args()
    if args.record_baselines:
        keys = record_all(source=args.record_source)
        scns = seed_scenarios()
        print(f"recorded {len(keys)} baselines (source={args.record_source}) -> {BASELINE_DIR}")
        print(f"seeded {len(scns)} new failure scenarios -> {SCENARIO_DIR}")
        return 0
    print("=== regression vs learned baselines ===")
    rc1, regr = check_all()
    print("=== failure-scenario replay ===")
    rc2 = replay_scenarios()
    rc = 1 if (rc1 or rc2) else 0
    print("\nALL REGRESSION CHECKS PASS" if rc == 0
          else f"\nREGRESSIONS: baselines={regr}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
