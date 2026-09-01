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
run_all_tests.py - run every Communicator test and report a single verdict.

Covers, end to end:
  codex        - SotF media codex: AV raw byte-exact, control SAME/DIFF, flag/options,
                 scale up+down, hostReset on databasehost float
  mediahost    - reconciliation loop: on-demand rungs (start on demand, stop when idle),
                 minus-self mix, float-safe plan rebuild
  movies       - real VP8 movies + PCM through the codex, byte-exact
  udp_vs_sotf  - degradation comparison: UDP dies ~3% loss, SotF holds
  bandwidth    - throttled pipe: scaler drops the ladder to fit
  degrade      - architectural degradation sweep (latency/loss/bw breaking points)

Usage: python3 run_all_tests.py
Exit code 0 iff every test passes / runs clean.
"""
import os, sys, subprocess

# [ASCII_SAFE_OUTPUT_V1] C/POSIX-locale terminals set stdout to ASCII; any non-ASCII
# byte printed (here or from a child test) would raise UnicodeEncodeError. Force UTF-8.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PKG = os.path.dirname(os.path.abspath(__file__))
TESTS = PKG  # installed layout: tests live in this simulation dir
ENV = dict(os.environ, FROGNET_LOG_LEVEL="ERROR",
           PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
           PYTHONPATH=os.pathsep.join([p for p in [os.path.dirname(PKG), "/etc/frognet_bundles/communicator", os.path.abspath(os.path.join(PKG,"..","..","..","etc","frognet_bundles","communicator"))] if os.path.isdir(p)]))

# (script, how to judge pass): "rc" = exit 0; or a substring that must appear in output
SUITE = [
    ("sim_sotf_media_codex.py", "passed, 0 failed"),
    ("sim_mediahost.py", "passed, 0 failed"),
    ("run_movies.py", "ALL OK"),
    ("compare_udp_vs_sotf.py", "unviewable at"),     # runs + reports the knees
    ("compare_bandwidth.py", "BANDWIDTH axis"),       # runs + prints the table
    ("degrade_sweep.py", "standard breaks at RTT"),
    ("tests_topology.py", "PROFILE SWEEP"),   # runs + reports breaking points
]

def run_one(script, needle):
    path = os.path.join(TESTS, script)
    if not os.path.exists(path):
        return False, "MISSING"
    try:
        out = subprocess.run([sys.executable, path], cwd=PKG, env=ENV,
                             capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    text = out.stdout + out.stderr
    ok = (out.returncode == 0) and (needle in text)
    tail = [l for l in text.strip().splitlines() if l.strip()][-1:] or [""]
    return ok, tail[0][:80]

def main():
    print("=" * 64)
    print("  FrogNet Communicator - full test suite")
    print("=" * 64)
    results = []
    for script, needle in SUITE:
        ok, info = run_one(script, needle)
        results.append(ok)
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {script:28s} {info}")
    npass = sum(results); n = len(results)
    print("-" * 64)
    print(f"  {npass}/{n} suites passed")
    print("=" * 64)
    return 0 if npass == n else 1

if __name__ == "__main__":
    sys.exit(main())
