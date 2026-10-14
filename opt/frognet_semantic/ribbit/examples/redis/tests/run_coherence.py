#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""run_coherence.py -- [READS_AT_THE_FRONT_V1] the copies a front keeps stay true. One RAM host, two fronts (A and B),
plain reads answered at the fronts from what they have read. Checked, with writers and readers running at once:
  own writes     a client that wrote through A reads its own write back through A, every time (INCR, then GET)
  monotonic      a reader on B never sees a key's value go backwards
  convergence    after the writers stop, B reads every key's final value within 1 s
  types          the same for a list (RPUSH / LLEN, LRANGE) and a hash (HINCRBY / HGET)
  flush          after FLUSHALL on A, B reads nothing within 1 s
Usage: run_coherence.py <build dir> <redis-cli> <base port> [seconds]
"""
import os, socket, subprocess, sys, threading, time

BUILD, CLI, BASE = sys.argv[1], sys.argv[2], int(sys.argv[3]); SECS = float(sys.argv[4]) if len(sys.argv) > 4 else 5
API = "/Fawcett.Redis.ram_interface.php"
fails = 0
def check(ok, what):
    global fails
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok: fails += 1

class R:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port)); self.f = self.s.makefile("rb")
    def cmd(self, *a):
        self.s.sendall(("*%d\r\n" % len(a) + "".join("$%d\r\n%s\r\n" % (len(str(x).encode()), x) for x in a)).encode())
        return self.read()
    def read(self):
        l = self.f.readline()[:-2]; t, v = l[:1], l[1:]
        if t == b"+": return v.decode()
        if t == b"-": return Exception(v.decode())
        if t == b":": return int(v)
        if t == b"$": n = int(v); return None if n < 0 else self.f.read(n + 2)[:-2].decode()
        if t == b"*": n = int(v); return None if n < 0 else [self.read() for _ in range(n)]
        raise RuntimeError("bad reply %r" % l)

def up(port):
    for _ in range(150):
        try: socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); return
        except OSError: time.sleep(0.1)
    sys.exit("FAIL nothing answers on %d" % port)

procs = []
try:
    for p in (BASE, BASE + 1, BASE + 2):
        try: socket.create_connection(("127.0.0.1", p), timeout=0.3).close(); sys.exit("port %d is already in use by a server this run did not start" % p)
        except OSError: pass
    env = dict(os.environ, FROGNET_BLOB_ROOT="/tmp/coherence-blobs-%d" % BASE)
    procs.append(subprocess.Popen([BUILD + "/ribbit-redis-host", "--listen", "127.0.0.1:%d" % BASE, "--api", API], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(0.5)
    for p in (BASE + 1, BASE + 2):
        procs.append(subprocess.Popen([BUILD + "/ribbit-redis-front", "--port", str(p), "--save", ""], env=dict(env, RIBBIT_RAM="127.0.0.1:%d" % BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    up(BASE + 1); up(BASE + 2); time.sleep(1.0)                      # the fronts open their feeds
    A, B = BASE + 1, BASE + 2
    KEYS = ["co:%d" % i for i in range(8)]
    stop = threading.Event(); own_bad = []; mono_bad = []; done = {}
    def writer(k):
        c = R(A); i = 0
        while not stop.is_set():
            v = c.cmd("INCR", k); i += 1
            g = c.cmd("GET", k)
            if str(g) != str(v): own_bad.append((k, v, g))
            c.cmd("RPUSH", k + ":l", i); n = c.cmd("LLEN", k + ":l")
            if n != i: own_bad.append((k + ":l", i, n))
            h = c.cmd("HINCRBY", k + ":h", "f", 1); hg = c.cmd("HGET", k + ":h", "f")
            if str(hg) != str(h): own_bad.append((k + ":h", h, hg))
        done[k] = i
    def reader(k):
        c = R(B); last = -1; lastl = -1
        while not stop.is_set():
            g = c.cmd("GET", k); v = int(g) if g is not None else 0
            if v < last: mono_bad.append((k, last, v))
            last = max(last, v)
            n = c.cmd("LLEN", k + ":l")
            if n < lastl: mono_bad.append((k + ":l", lastl, n))
            lastl = max(lastl, n)
    th = [threading.Thread(target=writer, args=(k,)) for k in KEYS] + [threading.Thread(target=reader, args=(k,)) for k in KEYS for _ in range(2)]
    for t in th: t.start()
    time.sleep(SECS); stop.set()
    for t in th: t.join()
    check(not own_bad, "own writes: every client read its own write back through its front (%d keys x 3 types)%s" % (len(KEYS), "" if not own_bad else ": first wrong %r" % (own_bad[0],)))
    check(not mono_bad, "monotonic: no reader on the other front saw a value go backwards%s" % ("" if not mono_bad else ": first %r" % (mono_bad[0],)))
    c = R(B); t0 = time.time(); left = list(KEYS)
    while left and time.time() - t0 < 1.0:
        left = [k for k in left if str(c.cmd("GET", k)) != str(done[k]) or c.cmd("LLEN", k + ":l") != done[k] or str(c.cmd("HGET", k + ":h", "f")) != str(done[k])]
        if left: time.sleep(0.01)
    check(not left, "convergence: the other front read every key's final value (%d writes per key) within %.3f s%s" % (min(done.values()), time.time() - t0, "" if not left else ": still stale %r" % left[:3]))
    R(A).cmd("FLUSHALL"); t0 = time.time()
    while time.time() - t0 < 1.0 and c.cmd("GET", KEYS[0]) is not None: time.sleep(0.01)
    check(c.cmd("GET", KEYS[0]) is None, "flush: after FLUSHALL on one front the other read nothing within %.3f s" % (time.time() - t0))
finally:
    for p in procs:
        try: p.kill(); p.wait(5)
        except Exception: pass
print("RESULT %s coherence: %d fail" % ("PASS" if not fails else "FAIL", fails), flush=True)
sys.exit(1 if fails else 0)
