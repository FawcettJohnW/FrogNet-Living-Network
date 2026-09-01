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
run_discovery_all.py - ONE invocation over the entire discovery plane.

    cd /opt/frognet_semantic && PYTHONPATH=. python3 -m discovery.run_discovery_all

Five legs, each already runnable on its own; this exists so none of them can be
forgotten. An oracle that is not in a runner is one refactor away from rotting
silently, which is exactly how loop_scenarios went unwatched.

  ORACLES     run_discovery_oracles      the 80-oracle regression gate
  PROOF       sim.proof_plane            declared topologies + churn/mobility
  LIFECYCLE   sim.lifecycle              node lifecycle scenarios
  SYSTEM      sim.system                 A-C-D cross-tunnel transitive getHosts
  LOOPS       sim.loop_scenarios         back-vouch / split-horizon audit

Gating, and why it is not uniform:

LOOPS is [ADVISORY] by default. Its own docstring calls it "the oracle the
split-horizon fix must satisfy on every topology", and it currently reports
back-vouch loops -- so gating on it today would paint the whole plane red for a
known-open question rather than for a new break. It is NOT hidden: the count is
printed every run and a CHANGE in that count is called out, because a silent
oracle is the failure mode this file exists to prevent. Promote it with
--gate-loops (or FROGNET_GATE_LOOPS=1) the day the count reaches zero, and
delete this paragraph.

The loop count is recorded in .discovery_loop_baseline next to this file. First
run writes it; later runs diff against it. Going UP is reported as a regression
even in advisory mode. Going DOWN means the baseline is stale -- rewrite it with
--bless-loops so the improvement is locked in and cannot silently reverse.

[SENTINEL_ISOLATION_V1] Every leg gets its own FROGNET_SENTINEL_DIR. Cross-run
leakage through /etc/sentinels/not_frognet.tsv poisoned discovery oracles on
2026-07-05, and these legs converge real topologies against sim IPs.

[PYCACHE_PURGE_V1] __pycache__ is purged and bytecode writing disabled before
any project module is imported, so a freshly-applied overlay cannot be shadowed
by a stale .pyc. Applying a patch and re-running is the whole point of this file.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))             # .../discovery
_FROGNET = os.path.dirname(_HERE)                              # .../frognet_semantic
_BASELINE = os.path.join(_HERE, ".discovery_loop_baseline")

# name, module, gating
#   True     -> a non-zero exit fails the run
#   False    -> reported, never fails the run (see LOOPS above)
LEGS = [
    ("ORACLES",   "discovery.run_discovery_oracles", True),
    ("PROOF",     "discovery.sim.proof_plane",       True),
    ("LIFECYCLE", "discovery.sim.lifecycle",         True),
    ("SYSTEM",    "discovery.sim.system",            True),
    ("LOOPS",     "discovery.sim.loop_scenarios",    False),
]

_LOOP_TOTAL_RE = re.compile(
    r"TOTAL back-vouch loops.*?:\s*(\d+)", re.IGNORECASE)


def _purge_pycache():
    """[PYCACHE_PURGE_V1] before any project import."""
    for root, dirs, _files in os.walk(_FROGNET):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                dirs.remove(d)


def _run(mod, tee):
    env = dict(os.environ,
               PYTHONPATH=_FROGNET + os.pathsep + os.environ.get("PYTHONPATH", ""),
               PYTHONDONTWRITEBYTECODE="1")
    # [SENTINEL_ISOLATION_V1] fresh not_frognet dir per LEG.
    env["FROGNET_SENTINEL_DIR"] = tempfile.mkdtemp(
        prefix="nf_%s_" % mod.rsplit(".", 1)[-1])
    t0 = time.time()
    p = subprocess.run([sys.executable, "-m", mod],
                       cwd=_FROGNET, env=env, capture_output=True, text=True)
    out = p.stdout + p.stderr
    if tee:
        sys.stdout.write(out)
        sys.stdout.flush()
    tail = ""
    for ln in reversed(out.splitlines()):
        if ln.strip() and not ln.startswith("[FROGNET-BUILD]"):
            tail = ln.strip()
            break
    return p.returncode, tail, out, time.time() - t0


def _read_baseline():
    try:
        with open(_BASELINE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _write_baseline(n):
    with open(_BASELINE, "w") as f:
        f.write("%d\n" % n)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="One invocation over the whole discovery plane.")
    ap.add_argument("--only", metavar="LEG", action="append", default=[],
                    help="run only this leg (repeatable): "
                         + ", ".join(n for n, _, _ in LEGS))
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="stream each leg's full output instead of a tail")
    ap.add_argument("--gate-loops", action="store_true",
                    default=os.environ.get("FROGNET_GATE_LOOPS") == "1",
                    help="make LOOPS gating (use once the count reaches zero)")
    ap.add_argument("--bless-loops", action="store_true",
                    help="record the current loop count as the new baseline")
    args = ap.parse_args(argv)

    want = {s.upper() for s in args.only}
    legs = [l for l in LEGS if not want or l[0] in want]
    if want and not legs:
        print("no such leg: " + ", ".join(sorted(want)), file=sys.stderr)
        return 2

    _purge_pycache()

    print("=" * 78)
    print("FrogNet discovery plane - %d leg(s)" % len(legs))
    print("  tree: %s" % _FROGNET)
    print("=" * 78)

    results = []
    loops_now = None
    for name, mod, gating in legs:
        if gating:
            label = ""
        else:
            label = " [ADVISORY]" if not args.gate_loops else ""
        print("\n---- %s%s  (%s)" % (name, label, mod))
        rc, tail, out, secs = _run(mod, args.verbose)
        if name == "LOOPS":
            m = _LOOP_TOTAL_RE.search(out)
            if m:
                loops_now = int(m.group(1))
        gated = gating or (name == "LOOPS" and args.gate_loops)
        status = "PASS" if rc == 0 else ("FAIL" if gated else "OPEN")
        results.append((name, status, gated, rc, secs))
        if not args.verbose:
            print("  %s" % tail)
        print("  [%s] rc=%d  %.1fs" % (status, rc, secs))

    # ---- loop-count trend -------------------------------------------------
    loop_note = None
    if loops_now is not None:
        base = _read_baseline()
        if args.bless_loops:
            _write_baseline(loops_now)
            loop_note = "loop baseline recorded: %d" % loops_now
        elif base is None:
            _write_baseline(loops_now)
            loop_note = "loop baseline initialised: %d" % loops_now
        elif loops_now > base:
            loop_note = ("BACK-VOUCH LOOPS ROSE: %d -> %d. This is a regression "
                         "even while LOOPS is advisory." % (base, loops_now))
        elif loops_now < base:
            loop_note = ("back-vouch loops FELL: %d -> %d. Re-run with "
                         "--bless-loops to lock it in." % (base, loops_now))
        else:
            loop_note = "back-vouch loops unchanged at %d" % loops_now

    # ---- summary ----------------------------------------------------------
    print("\n" + "=" * 78)
    print("SUMMARY")
    for name, status, gated, rc, secs in results:
        mark = "" if gated else "  (advisory)"
        print("  %-6s %-10s %6.1fs%s" % (status, name, secs, mark))

    if loop_note:
        print("\n  %s" % loop_note)

    failed = [n for n, s, g, _rc, _t in results if g and s != "PASS"]
    rose = loops_now is not None and (_read_baseline() or 0) < loops_now

    ok = not failed and not rose
    print("\nDISCOVERY PLANE: %s" % ("PASS" if ok else "FAIL"))
    if failed:
        print("  FAILED: " + ", ".join(failed))
    if rose:
        print("  loop count rose above baseline")
    if loops_now and not args.gate_loops and not failed and not rose:
        print("  (LOOPS still reports %d; advisory until the split-horizon "
              "work lands)" % loops_now)
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
