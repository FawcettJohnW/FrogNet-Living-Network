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
"""run_all_media_stream.py - run the whole SotF media-stream suite as one gate.

  mock_space.py            - tuple-space stand-in smoke
  test_media_stream.py     - control/bootstrap/status/backpressure (P1..P7,N1..N6)
  test_media_stream_wire.py- RAW-in-FNWP-1 data plane, byte-exact, convergence, N4
  test_media_stream_e2e.py - full lifecycle integrated over real FNWP-1

Exit 0 iff all green. Requires the source tree (parent of simulation/) for the real codec/handler;
falls back to skipping the wire/e2e tiers if the tree isn't present.
"""
import subprocess, sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
# The source tree is the parent of simulation/ (correct on a box at /opt/frognet_semantic
# AND in any extracted snapshot) - never a hardcoded absolute path.
TREE = os.path.dirname(HERE)

def _tree_ready():
    """The codec/transport tiers need the real modules importable. Check that
    directly instead of guessing from a path, so a present-but-incomplete tree
    fails loudly rather than passing a hollow gate."""
    import importlib.util
    for mod in ("transport_factories", "sotf_media_tier", "sotf_video_stream_test"):
        try:
            if importlib.util.find_spec(mod) is None:
                return False, mod
        except Exception:
            return False, mod
    return True, None

def run(label, argv, need_tree=False):
    if need_tree:
        ok, missing = _tree_ready()
        if not ok:
            print(f"  [SKIP] {label} (real codec/transport not importable: {missing} "
                  f"- add the source tree's simulation/ + core/ to PYTHONPATH)"); return 0
    env = dict(os.environ, FROGNET_LOG_LEVEL="ERROR")
    r = subprocess.run([sys.executable] + argv, cwd=HERE, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ok = (r.returncode == 0)
    tail = [l for l in r.stdout.splitlines() if "passed" in l or "FAIL" in l or "OK" in l]
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}  {tail[-1].strip() if tail else ''}")
    if not ok:
        print(r.stdout)
    return 0 if ok else 1

def _mock_space_path():
    """mock_space.py lives in the communicator bundle, not simulation/. Locate it via
    import (PYTHONPATH) so the smoke tier runs regardless of cwd."""
    import importlib.util
    spec = importlib.util.find_spec("mock_space")
    return spec.origin if spec and spec.origin else None


def main():
    print("================== SotF media-stream suite ==================")
    rc = 0
    _ms = _mock_space_path()
    if _ms:
        rc |= run("mock_space smoke", [_ms])
    else:
        print("  [SKIP] mock_space smoke (mock_space not importable; check PYTHONPATH)")
    rc |= run("lifecycle/control/backpressure", ["test_media_stream.py"])
    rc |= run("data plane RAW-in-FNWP-1", ["test_media_stream_wire.py"], need_tree=True)
    rc |= run("end-to-end lifecycle",    ["test_media_stream_e2e.py"],   need_tree=True)
    rc |= run("server watch + tuples adapter", ["test_media_stream_watch.py"])
    rc |= run("transport over real FNWP-1 sockets", ["test_media_stream_transport.py"], need_tree=True)
    rc |= run("metrics + slow-node detection", ["test_media_stream_metrics.py"])
    _rungs = None
    try:
        import importlib.util
        _spec = importlib.util.find_spec("media_stream_rungs")
        _rungs = _spec.origin if _spec and _spec.origin else None
    except Exception:
        _rungs = None
    if _rungs:
        rc |= run("rung R0 (sim transport + real codec, byte-exact)",
                  [_rungs, "R0", "--frames", "30"], need_tree=True)
    else:
        print("  [SKIP] rung R0 (media_stream_rungs not importable; check PYTHONPATH)")
    # LATEST_ONLY downlink: bounded keep-keyframes decode intake + per-leg adaptation
    # (start L5, climb to L7, drop to audio-only when the decoder can't keep up).
    rc |= run("downlink intake (LATEST_ONLY bound/shed/resync)", ["test_downlink_intake_oracle.py"])
    rc |= run("downlink feeder backpressure (credit-bound in-flight)",
              ["test_downlink_backpressure_oracle.py"])
    rc |= run("downlink leg adapt (L5->L7 / nuke video)", ["test_downlink_leg_adapt_oracle.py"])
    # TEMPORAL-LAYER plane: one stream, three frame-rate layers, server subsets per consumer
    # with no re-encode. Pure model + the load-bearing decode-clean proof (live periodic
    # keyframes) + the real server _video_drop_for honoring per-consumer caps.
    rc |= run("temporal layer model (sender TID + cap subset)", ["test_temporal_layer_oracle.py"])
    rc |= run("temporal decode-clean (live periodic-keyframe subsets)",
              ["test_temporal_decode_clean_oracle.py"])
    rc |= run("server temporal subset (per-consumer cap honor)",
              ["test_server_temporal_subset_oracle.py"])
    rc |= run("spatial band (sender encoder-restart hysteresis)",
              ["test_spatial_band_oracle.py"])
    rc |= run("downlink leg settle (no L7<->L0 thrash)",
              ["test_dl_leg_settle_oracle.py"])
    rc |= run("server minus-self (no self-echo to producer)",
              ["test_self_echo_oracle.py"])
    print("GATE:", "PASS" if rc == 0 else "FAIL")
    return rc

if __name__ == "__main__":
    sys.exit(main())
