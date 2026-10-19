#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# run_suite.py -- Mosquitto's broker suite against ONE broker, every test measured by metrics_wrap.py.
#
#   run_suite.py --build-root DIR --label stock|ribbit --out results.jsonl [--jobs N] [--retries R] [--only REGEX]
#
# DIR is a Mosquitto build root: DIR/src/mosquitto is the broker the tests start (stock Mosquitto, or the Ribbit
# wrapper), DIR/plugins and DIR/test are Mosquitto's own. The test list is Mosquitto's own (DIR/test/broker/test.py),
# run as Mosquitto's ptest runs it: each test gets its own ports, a failed test is retried after the rest (default 4
# times, as ptest does). Every attempt is a row in --out with "attempt"; the summary says which passed first time.
import argparse, json, os, re, subprocess, sys, tempfile, time

ap = argparse.ArgumentParser()
ap.add_argument('--build-root', required=True); ap.add_argument('--label', required=True)
ap.add_argument('--out', required=True); ap.add_argument('--jobs', type=int, default=8)
ap.add_argument('--retries', type=int, default=4); ap.add_argument('--only', default=None)
ap.add_argument('--minport', type=int, default=1888)   # ptest's: some tests assume a broker on 1888
a = ap.parse_args()

root = os.path.abspath(a.build_root)
bdir = os.path.join(root, 'test', 'broker')
wrap = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'metrics_wrap.py')
# Mosquitto's own list, as ptest runs it: a commented-out entry is one Mosquitto does not run
listing = '\n'.join(l for l in open(os.path.join(bdir, 'test.py')).read().splitlines() if not l.lstrip().startswith('#'))
tests = [(int(n), p[2:], x) for n, p, x in
         re.findall(r"\((\d+),\s*'(\./[^']+)'\s*(?:,\s*'([^']*)')?\s*\)", listing)]
if a.only:
    tests = [t for t in tests if re.search(a.only, t[1])]
env = dict(os.environ, BUILD_ROOT=root)

free = list(range(a.minport + 1, a.minport + 4 * a.jobs + 9))
# 09-plugin-delayed-auth connects to mosq_test's default port (1888) whatever port it was given: under ptest it passes
# only when some broker happens to be listening there. Here it is given 1888 itself, so it is deterministic for every
# broker, and no other test uses 1888.
# 23-security-acl-file-reload is listed with 2 ports, and mosq_test.get_port() returns 1888 unless given exactly one: it
# listens on 1888 too, whatever it is given. Both are run on 1888, never at the same time.
OWN_1888 = {'09-plugin-delayed-auth.py', '23-security-acl-file-reload.py'}
# A broker a previous run's failed test left behind on one of these ports answers this run's clients and turns its
# tests into someone else's failures. Say so, at the start and in the summary.
import socket as _socket
busy = []
for port in [a.minport] + free:
    s_ = _socket.socket(); s_.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    try: s_.bind(('127.0.0.1', port))
    except OSError: busy.append(port)
    finally: s_.close()
if busy:
    print(f"WARNING: ports {busy} are already in use (a leftover broker?): tests given them will fail", flush=True)
queue = [(n, p, x, 1) for n, p, x in tests]
running, results, logs = [], {}, {}
t_start = time.time()
while queue or running:
    while queue and len(running) < a.jobs and len(free) >= queue[0][0]:
        if queue[0][1] in OWN_1888 and any(r[2] in OWN_1888 for r in running):
            queue.append(queue.pop(0)); break
        n, p, x, attempt = queue.pop(0)
        ports = [a.minport] if p in OWN_1888 else [free.pop(0) for _ in range(n)]
        # some tests keep files in a directory named after their port and remove it only when they pass: one left by
        # an earlier test on the same port would make this one fail for that test's reason
        for q in ports:
            leftover = os.path.join(bdir, str(q))
            if os.path.isdir(leftover):
                import shutil; shutil.rmtree(leftover, ignore_errors=True)
        args = [sys.executable, wrap, os.path.abspath(a.out), a.label, os.path.join(bdir, p)] + ([x] if x else []) + [str(q) for q in ports]
        lf = tempfile.TemporaryFile()   # a file, not a pipe: a chatty test must never block on a full pipe
        # its own session: whatever the test leaves running (a broker its failure path never stopped) is killed with it
        proc = subprocess.Popen(args, cwd=bdir, env=env, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
        running.append((proc, n, p, x, attempt, ports, time.time(), lf))
    time.sleep(0.05)
    for r in list(running):
        proc, n, p, x, attempt, ports, t0, lf = r
        if proc.poll() is None:
            continue
        try: os.killpg(proc.pid, 9)          # [TEST_LEAVES_NOTHING_V1] leftovers of this test only
        except ProcessLookupError: pass
        running.remove(r); free.extend(q for q in ports if q != a.minport)
        lf.seek(0); out = lf.read().decode('utf-8', 'replace'); lf.close()
        ok = proc.returncode == 0
        print(f"{time.time()-t0:7.3f}s {'PASS' if ok else 'FAIL'} a{attempt} {p}", flush=True)
        results.setdefault(p, []).append(ok); logs[p] = out
        if not ok:
            with open(os.path.abspath(a.out) + '.attempts.log', 'a') as f:
                f.write(f"===== {p} attempt {attempt}\n{out}\n")
        if not ok and attempt <= a.retries:
            queue.append((n, p, x, attempt + 1))

first = sum(1 for p in results if results[p][0]); passed = sum(1 for p in results if any(results[p]))
failed = sorted(p for p in results if not any(results[p]))
print(f"\n{a.label}: {passed}/{len(results)} passed ({first} first time), {len(failed)} failed, {time.time()-t_start:.1f}s")
if busy:
    print(f"WARNING: ports {busy} were in use before the run started")
for p in failed:
    print("FAILED", p)
with open(os.path.abspath(a.out) + '.failed.log', 'w') as f:
    for p in failed:
        f.write(f"===== {p}\n{logs[p]}\n")
sys.exit(0 if not failed else 1)
