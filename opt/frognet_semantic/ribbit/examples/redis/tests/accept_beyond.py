#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""accept_beyond.py -- acceptance, part 2: what the Redis region does that stock Redis cannot. Every test runs on BOTH
arms with the same clients and the same checks; stock's result is measured, never assumed.

Usage: accept_beyond.py <arm: stock|ribbit> <test: endpoints|loss|scale> <base-port> <redis-src> <build-dir> [K]

The arms, each giving K endpoints a client can connect to:
  stock   redis-server 7.2.11: a primary on base+1 and K-1 replicas (replicaof the primary) on base+2..  -- what stock
          Redis offers for more than one endpoint without Cluster (Cluster splits the keyspace; a key on another node
          answers MOVED to a client that is not cluster-aware, so it is not one keyspace behind K endpoints).
  ribbit  one RAM host on base, K fronts on base+1.. connected up to it.

Tests (each prints PASS/FAIL lines and one RESULT line):
  endpoints  every endpoint takes writes and reads the same keyspace: 4 clients per endpoint INCR one key 500 times
             each; every INCR must succeed and every endpoint must then read the full total.
  loss       clients write through endpoints 1 and 2; endpoint 1 is killed halfway; the clients on endpoint 2 carry
             on; endpoint 1 is then started again. Every key written before the loss must be readable through the
             restarted endpoint and the survivor, and the survivor must have kept taking writes.
  scale      redis-benchmark (-c 50 -P 16 -n 200000, SET then GET, 100k random keys) against all K endpoints at
             once, requests/s summed. SET goes where the arm takes writes (stock: the primary only).
No fallbacks: a server that does not come up stops the test with its name.
"""
import os, re, signal, socket, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coordinated import Redis

ARM, TEST, BASE, RSRC, BUILD = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
K = int(sys.argv[6]) if len(sys.argv) > 6 else 2
API = "/Fawcett.Redis.ram_interface.php"
STOCK = os.path.join(RSRC, "src", "redis-server.stock")
if not os.path.exists(STOCK): STOCK = os.path.join(RSRC, "src", "redis-server")
BENCH = os.path.join(RSRC, "src", "redis-benchmark")
env = dict(os.environ, FROGNET_BLOB_ROOT="/tmp/accept-blobs"); os.makedirs(env["FROGNET_BLOB_ROOT"], exist_ok=True)
env.pop("RIBBIT_RAM", None)
procs = {}
fails = 0

def check(ok, what):
    global fails
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    fails += 0 if ok else 1

def up(port, name, t=15):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            r = Redis("127.0.0.1", port, timeout=2).cmd("PING")
            if r == "PONG": return
        except OSError: pass
        time.sleep(0.05)
    sys.exit("%s on port %d never answered PING" % (name, port))

def start_endpoint(i):
    """Endpoint i (1-based) of this arm, on base+i."""
    port = BASE + i
    if ARM == "stock":
        d = "/tmp/accept-stock-%d" % port; os.makedirs(d, exist_ok=True)   # a replica's sync file lands here, not in the tree
        args = [STOCK, "--port", str(port), "--save", "", "--appendonly", "no", "--bind", "127.0.0.1", "--daemonize", "no", "--dir", d]
        if i > 1: args += ["--replicaof", "127.0.0.1", str(BASE + 1)]
        procs[i] = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        procs[i] = subprocess.Popen([BUILD + "/ribbit-redis-front", "--port", str(port), "--save", ""],
                                    env=dict(env, RIBBIT_RAM="127.0.0.1:%d" % BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    up(port, "endpoint %d" % i)
    if ARM == "stock" and i > 1:   # a replica serves the keyspace only once its first sync is done
        t0 = time.time()
        while "master_link_status:up" not in str(Redis("127.0.0.1", port).cmd("INFO", "replication")):
            if time.time() - t0 > 15: sys.exit("replica %d never finished its sync with the primary" % i)
            time.sleep(0.1)

def start_arm():
    if ARM == "ribbit":
        procs[0] = subprocess.Popen([BUILD + "/ribbit-redis-host", "--listen", "127.0.0.1:%d" % BASE, "--api", API, "--quiet"],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while True:
            try: socket.create_connection(("127.0.0.1", BASE), timeout=1).close(); break
            except OSError:
                if time.time() - t0 > 15: sys.exit("RAM host never listened on %d" % BASE)
                time.sleep(0.05)
    for i in range(1, K + 1): start_endpoint(i)

def stop_all():
    for p in procs.values():
        try: p.kill(); p.wait(5)
        except Exception: pass

def is_err(r): return isinstance(r, Exception)

def t_endpoints():
    per, n = 4, 500
    results = {}
    def worker(ep, w):
        c = Redis("127.0.0.1", BASE + ep); ok = err = 0; first = None
        for _ in range(n):
            r = c.cmd("INCR", "acc:counter")
            if is_err(r):
                err += 1; first = first or str(r)
            else: ok += 1
        results[(ep, w)] = (ok, err, first)
    th = [threading.Thread(target=worker, args=(ep, w)) for ep in range(1, K + 1) for w in range(per)]
    for t in th: t.start()
    for t in th: t.join()
    want = K * per * n
    for ep in range(1, K + 1):
        ok = sum(results[(ep, w)][0] for w in range(per)); err = sum(results[(ep, w)][1] for w in range(per))
        first = next((results[(ep, w)][2] for w in range(per) if results[(ep, w)][2]), "")
        check(err == 0, "endpoint %d: %d INCRs accepted, %d refused%s" % (ep, ok, err, (" (" + first + ")") if first else ""))
    time.sleep(0.5)   # a stock replica is asynchronous; give it every chance to catch up before it is read
    for ep in range(1, K + 1):
        v = Redis("127.0.0.1", BASE + ep).cmd("GET", "acc:counter")
        check(str(v) == str(want), "endpoint %d reads the total: %s of %d INCRs sent across all endpoints" % (ep, v, want))

def t_loss():
    if K < 2: sys.exit("loss needs K >= 2")
    stop = threading.Event(); written = {1: [], 2: []}; refused = {1: 0, 2: 0}; after_loss = {2: 0}; lost_at = [None]
    def writer(ep):
        c = Redis("127.0.0.1", BASE + ep, timeout=5); i = 0
        while not stop.is_set():
            k = "loss:%d:%d" % (ep, i); i += 1
            try: r = c.cmd("SET", k, "v")
            except (OSError, ConnectionError): break          # its endpoint is gone: this client stops
            if is_err(r):
                refused[ep] += 1; time.sleep(0.01)
            else:
                written[ep].append(k)
                if lost_at[0] is not None and ep == 2: after_loss[2] += 1
    th = [threading.Thread(target=writer, args=(ep,)) for ep in (1, 2)]
    for t in th: t.start()
    time.sleep(2.0)
    procs[1].kill(); procs[1].wait(); lost_at[0] = time.time()
    time.sleep(2.0); stop.set()
    for t in th: t.join()
    before = written[1] + [k for k in written[2]]
    check(after_loss[2] > 0, "after endpoint 1 was killed, endpoint 2 accepted %d more writes (%d refused in all)" % (after_loss[2], refused[2]))
    def readable(ep):
        c = Redis("127.0.0.1", BASE + ep); n = 0
        for j in range(0, len(before), 500):
            chunk = before[j:j + 500]; r = c.cmd("EXISTS", *chunk); n += r if isinstance(r, int) else 0
        return n
    n = readable(2)
    check(n == len(before), "while endpoint 1 is down, the survivor (endpoint 2) reads %d of %d keys written" % (n, len(before)))
    start_endpoint(1); time.sleep(3.0)   # 3 s: a stock replica reconnects to a restarted primary within about 1 s
    for ep, label in ((1, "endpoint 1, started again"), (2, "endpoint 2, after endpoint 1 is back")):
        n = readable(ep)
        check(n == len(before), "%s: reads %d of %d keys written" % (label, n, len(before)))

def bench(port, test):
    out = subprocess.run([BENCH, "-p", str(port), "-q", "-c", "50", "-P", "16", "-n", "200000", "-r", "100000", "-t", test],
                         capture_output=True, text=True, timeout=600).stdout.replace("\r", "\n")
    m = re.findall(r"([0-9.]+) requests per second", out)
    return float(m[-1]) if m else 0.0

def t_scale():
    for test in ("set", "get"):
        ports = [BASE + 1] * K if (ARM == "stock" and test == "set") else [BASE + i for i in range(1, K + 1)]
        res = [0.0] * K
        def go(j): res[j] = bench(ports[j], test)
        th = [threading.Thread(target=go, args=(j,)) for j in range(K)]
        for t in th: t.start()
        for t in th: t.join()
        print("MEASURE %s %s K=%d total %.0f req/s  per endpoint %s" % (ARM, test.upper(), K, sum(res), " ".join("%.0f" % r for r in res)), flush=True)
        check(min(res) > 0, "%s: every one of %d benchmark clients completed" % (test.upper(), K))

start_arm()
try:
    {"endpoints": t_endpoints, "loss": t_loss, "scale": t_scale}[TEST]()
finally:
    stop_all()
print("RESULT %s %s %s (K=%d): %d fail" % ("PASS" if not fails else "FAIL", ARM, TEST, K, fails), flush=True)
sys.exit(1 if fails else 0)
