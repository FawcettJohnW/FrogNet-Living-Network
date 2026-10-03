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
model_feedback.py - close the loop: take measurements from a real-hardware run
and use them to make the offline model more faithful.

Two directions:
  ingest  - a real run (RealIPRoute / proxy on the box) records per-edge RTT
            samples and any failures into a JSON calibration file.
  apply   - the offline harness loads that file and OVERRIDES its guessed
            EDGE_RTT_BY_KIND with measured medians, and exposes recorded real
            failures as scenarios to replay. No source edits - applied at
            runtime to frognet_sim.

Calibration JSON shape:
  {
    "edge_rtt_ms": {"wg": 31.2, "wlan": 7.5, ...},   # measured medians by kind
    "failures": [                                     # real faults to replay
       {"kind": "tunnel_drop", "node": "NY-1", "dev": "wg1", "note": "..."}
    ]
  }

Why this matters: EDGE_RTT_BY_KIND in frognet_sim is currently GUESSED
(eth=2, wlan=5, ham=20, wg=25 ms). The first real run replaces guesses with
truth, so FAST/SEMANTIC decisions and convergence timing reflect the actual
network. Each subsequent run refines it.
"""
import json
import os
import sys
import statistics

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from frognet_log import get_logger

log = get_logger("simulation.model_feedback")
DEFAULT_PATH = os.path.join(_HERE, "model_calibration.json")


class RunRecorder:
    """A real backend run accumulates per-kind RTT samples + failures here, then
    writes the calibration file. Wire RealIPRoute / proxy probes to .rtt()."""
    def __init__(self):
        self.samples = {}      # iface-kind -> [rtt_ms, ...]
        self.failures = []     # list of dicts

    def rtt(self, iface_kind, rtt_ms):
        self.samples.setdefault(iface_kind, []).append(float(rtt_ms))

    def failure(self, kind, **kw):
        self.failures.append({"kind": kind, **kw})

    def write(self, path=DEFAULT_PATH):
        edge = {k: round(statistics.median(v), 2)
                for k, v in self.samples.items() if v}
        data = {"edge_rtt_ms": edge, "failures": self.failures}
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        log.info("wrote calibration: %d kinds, %d failures -> %s",
                 len(edge), len(self.failures), path)
        return data


def apply_calibration(path=DEFAULT_PATH):
    """Override frognet_sim.EDGE_RTT_BY_KIND with measured medians, in-process.
    Returns (applied_dict, failures) or ({}, []) if no calibration file."""
    if not os.path.isfile(path):
        return {}, []
    with open(path) as f:
        data = json.load(f)
    import frognet_sim as H
    edge = data.get("edge_rtt_ms", {})
    H.EDGE_RTT_BY_KIND.update({k: float(v) for k, v in edge.items()})
    log.info("applied measured RTTs to model: %s", edge)
    return edge, data.get("failures", [])


def main():
    """CLI: ingest a raw RTT dump (TSV `kind\\trtt_ms` lines) into calibration,
    or show the current calibration's effect on the model."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest", help="TSV file of 'iface_kind<TAB>rtt_ms' samples")
    ap.add_argument("--show", action="store_true", help="show current calibration")
    args = ap.parse_args()
    if args.ingest:
        rec = RunRecorder()
        with open(args.ingest) as f:
            for ln in f:
                parts = ln.split()
                if len(parts) >= 2:
                    try:
                        rec.rtt(parts[0], float(parts[1]))
                    except ValueError:
                        pass
        d = rec.write()
        print(f"calibration written: {d['edge_rtt_ms']}")
        return 0
    edge, fails = apply_calibration()
    if not edge:
        print("no calibration file yet (run on hardware first to produce one)")
    else:
        print(f"measured RTTs applied to model: {edge}")
        print(f"recorded real failures to replay: {len(fails)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
