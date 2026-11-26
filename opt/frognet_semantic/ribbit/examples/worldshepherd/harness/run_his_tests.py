#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""run_his_tests.py -- Brandon's own connector tests, unmodified, on the memory; then the race between processes.

    python3 harness/run_his_tests.py --sara DIR [--ram PATH] [--claimants 8] [--tickets 400]

DIR is a checkout of Sara_pro at the branch worldshepherd/connectors-distributed-claim-contract-v1-20260913.
It starts one worldshepherd-ram (the app's RAM host, which fdf-app serves; PATH is another build of it) on a free local
port, and runs against it:
  1  his tests/test_connector_*.py with the shared store replaced by RibbitClaimStore            (WS_STORE=memory)
  2  the same tests with his own PostgresClaimStore, its four SQL statements carried out as cells (WS_STORE=sql)
  3  one session used by many threads, both stores
  4  the race: N claimant processes claim the same tickets; every ticket must be won exactly once
Each line it prints is one of those, with the command it ran. Exit 0 only if all four pass.
"""
import argparse, glob, json, os, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--sara", required=True)
ap.add_argument("--ram", default=os.environ.get("WS_RAM_HOST") or os.path.join(HERE, "bin", "worldshepherd-ram"))
ap.add_argument("--claimants", type=int, default=8)
ap.add_argument("--tickets", type=int, default=400)
A = ap.parse_args()
for f in (A.ram, os.path.join(HERE, "lib", "libkartram.so"), os.path.join(A.sara, "worldshepherd_sara", "connector_ticket_shared.py")):
    if not os.path.exists(f):
        sys.exit("run_his_tests: %s is not there (README.md: Build)" % f)
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
T = tempfile.mkdtemp(prefix="ws-his-tests-")               # the RAM host's log, and the claimants'
ram = subprocess.Popen([A.ram, "--listen", "127.0.0.1:%d" % port, "--api", "/FawcettInnovations.RINA.ram_interface.php"],
                       stdout=subprocess.DEVNULL, stderr=open(T + "/worldshepherd-ram.log", "w"))
for _ in range(100):
    try: socket.create_connection(("127.0.0.1", port), 0.2).close(); break
    except OSError: time.sleep(0.05)
else:
    sys.exit("run_his_tests: worldshepherd-ram did not listen on 127.0.0.1:%d: %s" % (port, open(T + "/worldshepherd-ram.log").read()[-400:]))
where = "127.0.0.1:%d" % port
SRC = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "fdf", "src", "fdf-source", "fdf", "lib", "python"))
if not os.environ.get("FDF_PY") and os.path.isdir(SRC):     # in a checkout the fdf library is in its source tree; on a node, /opt/fdf/lib/python
    os.environ["FDF_PY"] = SRC
env = dict(os.environ, WS_RAM=where, PYTHONPATH=os.pathsep.join(   # the caller's path is kept, after these: on a node it names the packages the tarball carries
    [os.path.join(HERE, "harness"), HERE, os.path.join(HERE, "lib"), A.sara] + [x for x in [os.environ.get("PYTHONPATH")] if x]))
bad = 0
def line(ok, what):
    global bad; bad += 0 if ok else 1
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
try:
    for n, store in ((1, "memory"), (2, "sql")):
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "on_the_memory"] + sorted(glob.glob(os.path.join(A.sara, "tests", "test_connector_*.py"))),
                           cwd=A.sara, env=dict(env, WS_STORE=store), capture_output=True, text=True)
        tail = [l for l in p.stdout.splitlines() if l.strip()][-2:]
        line(p.returncode == 0, "%d his connector tests, WS_STORE=%s: %s" % (n, store, " | ".join(tail)))
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", os.path.join(HERE, "harness", "test_one_session_many_threads.py")], cwd=HERE, env=env, capture_output=True, text=True)
    line(p.returncode == 0, "3 one session, many threads: %s" % ([l for l in p.stdout.splitlines() if l.strip()] or ["?"])[-1])
    race = os.path.join(HERE, "harness", "race.py")
    cl = [subprocess.Popen([sys.executable, race, "claimant", where, "--name", "c%d" % i], env=env, stdout=subprocess.DEVNULL, stderr=open(T + "/claimant-%d.log" % i, "w"))
          for i in range(A.claimants)]
    for store in ("memory", "sql"):
        p = subprocess.run([sys.executable, race, "controller", where, "--claimants", str(A.claimants), "--tickets", str(A.tickets), "--store", store],
                           env=env, capture_output=True, text=True)
        try:
            r = json.loads([l for l in p.stdout.splitlines() if l.startswith("{")][-1])
            line(r.get("verdict") == "PASS", "4 the race, %s: %d processes, %d tickets: won once %s, twice %s, never %s; %s claims/s, claim median %s ms"
                 % (store, A.claimants, A.tickets, r.get("won_once"), r.get("won_twice"), r.get("never_won"), r.get("claims_per_s"), r.get("claim_ms_p50")))
        except (IndexError, ValueError):
            line(False, "4 the race, %s: no result: %s" % (store, (p.stdout + p.stderr)[-300:]))
    subprocess.run([sys.executable, race, "controller", where, "--end"], env=env, capture_output=True)
    for c in cl: c.wait(30)
finally:
    ram.terminate()
    print("logs in", T)
print("RESULT", "PASS" if not bad else "FAIL: %d" % bad)
sys.exit(1 if bad else 0)
