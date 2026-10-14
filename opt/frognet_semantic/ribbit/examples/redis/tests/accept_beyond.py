#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""accept_beyond.py -- acceptance, part 2: what the Redis region does beside stock Redis set up the way an operator
would set it up for the same job. Every test runs on every arm with the same clients and the same checks; stock's
result is measured, never assumed.

Usage: accept_beyond.py <arm> <test> <base-port> <redis-src> <build-dir> [K]

Arms (each gives K endpoints a client can connect to, on base+1..base+K):
  stock           redis-server 7.2.11, persistence off: a primary and K-1 replicas (replicaof the primary). Matches
                  the region's own state (no persistence) -- the like-for-like arm.
  stock-aof       the same, with AOF on (appendonly yes, appendfsync everysec, the default fsync policy) -- stock with
                  the persistence an operator would turn on.
  stock-sentinel  stock-aof plus one Sentinel watching the primary (down-after 1000 ms, failover-timeout 5000 ms)
                  -- stock with automatic recovery: Sentinel promotes a replica when the primary dies.
  ribbit          one RAM host on base, K fronts connected up to it. The host keeps the region in memory only.
Stock's arms reach more than one endpoint without Cluster. Cluster splits the keyspace across nodes; a cluster-aware
client follows MOVED to the owner, so with such a client Cluster serves one keyspace from several nodes -- not tested
here, and nothing here claims the region beats it.

Tests (each prints PASS/FAIL/MEASURE lines and one RESULT line):
  endpoints  every endpoint takes writes and reads the same keyspace: 4 clients per endpoint INCR one key 500 times;
             every INCR must succeed and every endpoint must then read the full total.
  loss       endpoint failure. Clients write through endpoints 1 and 2 (distinct keys, each acknowledged write
             recorded); endpoint 1 is killed after 2 s and the writers carry on 8 s more; endpoint 1 is started again.
             Measured: writes endpoint 2 accepted after the loss and the time to the first one; then, polling up to 30 s
             after the restart, how many acknowledged writes each endpoint reads. PASS needs endpoint 2 to keep
             taking writes and every acknowledged write readable everywhere.
  hostloss   failure of the place the data lives: stock's primary, the region's RAM host. Writers on endpoint 1 for
             2 s; the data's home is killed and started again; every acknowledged write must be readable through
             endpoint 1 within 30 s. The region keeps its memory in the host process: this test is expected to show
             what that costs.
  scale      redis-benchmark (-c 50 -P 16 -n 200000, SET then GET, 100k random keys) against all K endpoints at
             once, requests/s summed. SET goes where the arm takes writes (stock: the primary only).
No fallbacks: a server that does not come up stops the test with its name.
"""
import os, re, shutil, socket, subprocess, sys, threading, time, atexit
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coordinated import Redis

ARM, TEST, BASE, RSRC, BUILD = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
K = int(sys.argv[6]) if len(sys.argv) > 6 else 2
ARMS = ("stock", "stock-aof", "stock-sentinel", "ribbit")
if ARM not in ARMS: sys.exit("arm must be one of %s" % ", ".join(ARMS))
STOCKISH = ARM.startswith("stock")
API = "/Fawcett.Redis.ram_interface.php"
STOCK = os.path.join(RSRC, "src", "redis-server.stock")
if not os.path.exists(STOCK): STOCK = os.path.join(RSRC, "src", "redis-server")
BENCH = os.path.join(RSRC, "src", "redis-benchmark")
env = dict(os.environ, FROGNET_BLOB_ROOT="/tmp/accept-blobs"); os.makedirs(env["FROGNET_BLOB_ROOT"], exist_ok=True)
env.pop("RIBBIT_RAM", None)
SENTINEL_PORT = BASE + K + 1
WORK = "/tmp/accept-%s-%d" % (ARM, BASE)        # every stock file (AOF, replica sync, sentinel.conf) lands here
shutil.rmtree(WORK, ignore_errors=True); os.makedirs(WORK)
procs = {}
fails = 0
atexit.register(lambda: [p.kill() for p in procs.values()])

def check(ok, what):
    global fails
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    fails += 0 if ok else 1

def measure(what): print("MEASURE " + what, flush=True)

def answers(port):
    try: return Redis("127.0.0.1", port, timeout=2).cmd("PING") == "PONG"
    except OSError: return False

def up(port, name, t=15):
    t0 = time.time()
    while time.time() - t0 < t:
        if answers(port): return
        time.sleep(0.05)
    sys.exit("%s on port %d never answered PING" % (name, port))

def start_endpoint(i, as_replica_of=None):
    """Endpoint i (1-based) of this arm, on base+i. Stock: endpoint 1 starts as the primary unless told otherwise."""
    port = BASE + i
    if STOCKISH:
        d = os.path.join(WORK, str(port)); os.makedirs(d, exist_ok=True)
        args = [STOCK, "--port", str(port), "--save", "", "--bind", "127.0.0.1", "--daemonize", "no", "--dir", d,
                "--appendonly", "no" if ARM == "stock" else "yes"]
        primary = as_replica_of if as_replica_of else (None if i == 1 else BASE + 1)
        if primary: args += ["--replicaof", "127.0.0.1", str(primary)]
        procs[i] = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        procs[i] = subprocess.Popen([BUILD + "/ribbit-redis-front", "--port", str(port), "--save", ""],
                                    env=dict(env, RIBBIT_RAM="127.0.0.1:%d" % BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    up(port, "endpoint %d" % i)

def wait_synced(i, t=15):
    t0 = time.time()
    while "master_link_status:up" not in str(Redis("127.0.0.1", BASE + i).cmd("INFO", "replication")):
        if time.time() - t0 > t: sys.exit("replica %d never finished its sync with the primary" % i)
        time.sleep(0.1)

def start_host():
    procs[0] = subprocess.Popen([BUILD + "/ribbit-redis-host", "--listen", "127.0.0.1:%d" % BASE, "--api", API, "--quiet"],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while True:
        try: socket.create_connection(("127.0.0.1", BASE), timeout=1).close(); return
        except OSError:
            if time.time() - t0 > 15: sys.exit("RAM host never listened on %d" % BASE)
            time.sleep(0.05)

def start_sentinel():
    conf = os.path.join(WORK, "sentinel.conf")
    with open(conf, "w") as f:
        f.write("port %d\nbind 127.0.0.1\ndir %s\nsentinel monitor acc 127.0.0.1 %d 1\n"
                "sentinel down-after-milliseconds acc 1000\nsentinel failover-timeout acc 5000\n" % (SENTINEL_PORT, WORK, BASE + 1))
    procs["sentinel"] = subprocess.Popen([STOCK, conf, "--sentinel"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    up(SENTINEL_PORT, "sentinel")
    t0 = time.time()       # the sentinel has found the replicas before anything is killed
    while True:
        r = Redis("127.0.0.1", SENTINEL_PORT).cmd("SENTINEL", "REPLICAS", "acc")
        if isinstance(r, list) and len(r) >= K - 1: return
        if time.time() - t0 > 15: sys.exit("sentinel never discovered the replicas")
        time.sleep(0.2)

def start_arm():
    # [NO_STALE_SERVER_V1] a port already answering belongs to a server this run did not start: refuse, by name
    for p in list(range(BASE, BASE + K + 1)) + ([SENTINEL_PORT] if ARM == "stock-sentinel" else []):
        try: socket.create_connection(("127.0.0.1", p), timeout=0.5).close(); sys.exit("port %d is already in use by a server this run did not start" % p)
        except OSError: pass
    if ARM == "ribbit": start_host()
    for i in range(1, K + 1): start_endpoint(i)
    if STOCKISH:
        for i in range(2, K + 1): wait_synced(i)
    if ARM == "stock-sentinel": start_sentinel()

def kill(key):
    p = procs.pop(key); p.kill(); p.wait()

def is_err(r): return isinstance(r, Exception)

def readable(port, keys):
    c = Redis("127.0.0.1", port, timeout=5); n = 0
    for j in range(0, len(keys), 500):
        r = c.cmd("EXISTS", *keys[j:j + 500]); n += r if isinstance(r, int) else 0
    return n

def poll_readable(ports, keys, t=30):
    """{port: readable count}, polled until every port reads them all or t seconds pass."""
    t0 = time.time(); got = {}
    while True:
        for p in ports:
            try: got[p] = readable(p, keys)
            except (OSError, ConnectionError): got[p] = 0
        if all(v == len(keys) for v in got.values()) or time.time() - t0 > t: return got, time.time() - t0
        time.sleep(0.5)

# ---------------------------------------------------------------- tests
def t_endpoints():
    per, n = 4, 500
    results = {}
    def worker(ep, w):
        c = Redis("127.0.0.1", BASE + ep); ok = err = 0; first = None
        for _ in range(n):
            r = c.cmd("INCR", "acc:counter")
            if is_err(r): err += 1; first = first or str(r)
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
    time.sleep(0.5)   # a stock replica is asynchronous; it gets every chance to catch up before it is read
    for ep in range(1, K + 1):
        v = Redis("127.0.0.1", BASE + ep).cmd("GET", "acc:counter")
        check(str(v) == str(want), "endpoint %d reads the total: %s of %d INCRs sent across all endpoints" % (ep, v, want))

def writers(eps, seconds, during=None, at=2.0):
    """Writers on each endpoint in eps, SET of distinct keys, each acknowledged key recorded; during() runs `at` s in."""
    stop = threading.Event(); acked = {e: [] for e in eps}; refused = {e: 0 for e in eps}; first_after = [None]; event_t = [None]
    def w(ep):
        c = None; i = 0
        while not stop.is_set():
            try:
                if c is None: c = Redis("127.0.0.1", BASE + ep, timeout=3)
                k = "w:%d:%d" % (ep, i); i += 1
                r = c.cmd("SET", k, "v")
            except (OSError, ConnectionError):
                c = None; time.sleep(0.05); continue       # its endpoint is gone or not answering: try again shortly
            if is_err(r): refused[ep] += 1; time.sleep(0.01)
            else:
                acked[ep].append(k)
                if event_t[0] is not None and first_after[0] is None and ep != 1: first_after[0] = time.time() - event_t[0]
    th = [threading.Thread(target=w, args=(e,)) for e in eps]
    for t in th: t.start()
    if during:
        time.sleep(at); event_t[0] = time.time(); during(); time.sleep(max(0.0, seconds - at))
    else: time.sleep(seconds)
    stop.set()
    for t in th: t.join()
    return acked, refused, first_after[0], event_t[0]

def t_loss():
    if K < 2: sys.exit("loss needs K >= 2")
    def lose():
        kill(1)
    acked, refused, first_after, t_event = writers([1, 2], 10.0, during=lose, at=2.0)
    all_keys = acked[1] + acked[2]
    measure("%s: endpoint 2 accepted %d writes in all, refused %d; first write accepted after endpoint 1 was killed: %s"
            % (ARM, len(acked[2]), refused[2], ("%.2f s after" % first_after) if first_after is not None else "none within 8 s"))
    check(first_after is not None, "endpoint 2 kept taking writes after endpoint 1 was killed")
    start_endpoint(1)   # started as it was configured; with Sentinel, Sentinel decides what it becomes
    got, waited = poll_readable([BASE + 1, BASE + 2], all_keys)
    for ep in (1, 2):
        check(got[BASE + ep] == len(all_keys), "endpoint %d after endpoint 1 is back (polled %.1f s): reads %d of %d acknowledged writes"
              % (ep, waited, got[BASE + ep], len(all_keys)))

def t_hostloss():
    acked, refused, _, _ = writers([1], 2.0)
    keys = acked[1]
    if ARM == "ribbit": kill(0); time.sleep(0.5); start_host()
    else:
        kill(1); time.sleep(0.5)
        start_endpoint(1)
    got, waited = poll_readable([BASE + 1], keys)
    check(got[BASE + 1] == len(keys), "after the data's home (%s) was killed and started again, endpoint 1 reads %d of %d acknowledged writes (polled %.1f s)"
          % ("the RAM host" if ARM == "ribbit" else "the primary", got[BASE + 1], len(keys), waited))

def bench(port, test):
    if os.environ.get("BENCH_WARM") == "1":                       # [WARM_READS_V1] the same run once first, not measured
        subprocess.run([BENCH, "-p", str(port), "-q", "-c", os.environ.get("BENCH_C", "50"), "-P", os.environ.get("BENCH_P", "16"),
                        "-n", os.environ.get("BENCH_N", "200000"), "-r", "100000", "-t", test], capture_output=True, timeout=600)
    out = subprocess.run([BENCH, "-p", str(port), "-q", "-c", os.environ.get("BENCH_C", "50"), "-P", os.environ.get("BENCH_P", "16"),
                          "-n", os.environ.get("BENCH_N", "200000"), "-r", "100000", "-t", test],
                         capture_output=True, text=True, timeout=600).stdout.replace("\r", "\n")
    m = re.findall(r"([0-9.]+) requests per second", out)
    return float(m[-1]) if m else 0.0

def t_scale():
    for test in os.environ.get("BENCH_TESTS", "set get").split():
        ports = [BASE + 1] * K if (STOCKISH and test == "set") else [BASE + i for i in range(1, K + 1)]
        res = [0.0] * K
        def go(j): res[j] = bench(ports[j], test)
        th = [threading.Thread(target=go, args=(j,)) for j in range(K)]
        for t in th: t.start()
        for t in th: t.join()
        measure("%s %s K=%d total %.0f req/s  per endpoint %s" % (ARM, test.upper(), K, sum(res), " ".join("%.0f" % r for r in res)))
        check(min(res) > 0, "%s: every one of %d benchmark clients completed" % (test.upper(), K))

start_arm()
try:
    {"endpoints": t_endpoints, "loss": t_loss, "hostloss": t_hostloss, "scale": t_scale}[TEST]()
finally:
    for p in procs.values():
        try: p.kill(); p.wait(5)
        except Exception: pass
    procs.clear()
print("RESULT %s %s %s (K=%d): %d fail" % ("PASS" if not fails else "FAIL", ARM, TEST, K, fails), flush=True)
sys.exit(1 if fails else 0)
