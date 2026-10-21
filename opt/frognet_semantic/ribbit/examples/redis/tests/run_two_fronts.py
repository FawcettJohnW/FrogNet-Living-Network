#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""run_two_fronts.py -- fronts on one memory [ONE_KEYSPACE_V1]: the keyspace is in the memory, once; a front holds none
of it and answers its clients from its calls. Checked with two fronts (A, B) on a memory started here:
  1  a write acknowledged on A is read on B, at once                      (one keyspace)
  2  a client blocked on B is served by a push on A                       (the push's write wakes it)
  3  a blocked command's time runs out                                    (the region's deadline)
  4  two clients blocked on B and A, one push: the first to block gets it (its claim ticket)
  5  CLIENT UNBLOCK on A of a client blocked on B
  6  CLIENT LIST on A lists B's clients; CLIENT KILL on A closes B's client (an idle one: the kill cell B reads)
  7  MULTI/EXEC on A beside INCRBYs on B: every EXEC answers, no increment lost (no isolation: ruled 2026-09-29)
  8  a client hanging up while blocked on B leaves the region
  9  B stops: its session ends, its clients leave the region, A goes on
 10  a front started later serves the same keyspace at once -- nothing to copy, replay or join: values, expiry, SCAN
     order; a client parked and a transaction open on A meanwhile; both fronts in one order
Usage: run_two_fronts.py <build-dir> <base-port>        MEMORY=host:port for a memory elsewhere"""
import os, socket, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coordinated import Redis
B, BASE = sys.argv[1], int(sys.argv[2])
LOGS = os.environ.get("LOGS", "/tmp/two-fronts-%d" % os.getpid()); os.makedirs(LOGS, exist_ok=True)
procs = []
def up(p, t=10):
    t0 = time.time()
    while time.time() - t0 < t:
        try: socket.create_connection(("127.0.0.1", p), timeout=1).close(); return True
        except OSError: time.sleep(0.02)
    return False
def start(cmd, name, env=None):
    p = subprocess.Popen(cmd, env=dict(os.environ, **(env or {})), stdin=subprocess.DEVNULL, stdout=open(LOGS + "/" + name + ".log", "w"), stderr=subprocess.STDOUT)
    procs.append(p); return p
# MEMORY=host:port: a memory elsewhere
if os.environ.get("MEMORY"): ram = os.environ["MEMORY"]
else:
    start([B + "/ribbit-redis-host", "--listen", "127.0.0.1:%d" % BASE], "memory")
    assert up(BASE), "the memory never listened"
    ram = "127.0.0.1:%d" % BASE
env = {"RIBBIT_RAM": ram}
A, PB = BASE + 1, BASE + 2
start([B + "/ribbit-redis-front", "--port", str(A), "--save", ""], "front-a", dict(env, RIBBIT_REDIS_CONFIG_OWNER="1"))
pb = start([B + "/ribbit-redis-front", "--port", str(PB), "--save", ""], "front-b", env)
assert up(A) and up(PB), "a front never listened"
ok = bad = 0
def check(name, cond, detail=""):
    global ok, bad
    if cond: ok += 1; print("PASS " + name, flush=True)
    else: bad += 1; print("FAIL " + name + (": " + detail if detail else ""), flush=True)
a, b = Redis("127.0.0.1", A, timeout=10), Redis("127.0.0.1", PB, timeout=10)

# 1
misses = 0
for i in range(500):
    a.cmd("SET", "k", i)
    if b.cmd("GET", "k") != str(i): misses += 1
check("a write acknowledged on A is read on B at once (500 rounds)", misses == 0, "%d stale reads" % misses)

# 2
got = {}
def blpop(port, key, t, into):
    c = Redis("127.0.0.1", port, timeout=t + 10); t0 = time.time(); into["r"] = c.cmd("BLPOP", key, t); into["s"] = time.time() - t0
th = threading.Thread(target=blpop, args=(PB, "q2", 5, got)); th.start(); time.sleep(0.3)
a.cmd("LPUSH", "q2", "hello"); th.join()
check("a client blocked on B is served by a push on A", got.get("r") == ["q2", "hello"] and got["s"] < 2, repr(got))

# 3
got = {}; blpop(PB, "q3", 1, got)
check("a blocked command's time runs out", got.get("r") is None and 0.95 <= got["s"] < 2.5, repr(got))

# 4
first, second = {}, {}
t1 = threading.Thread(target=blpop, args=(PB, "q4", 5, first)); t1.start(); time.sleep(0.3)
t2 = threading.Thread(target=blpop, args=(A, "q4", 2, second)); t2.start(); time.sleep(0.3)
a.cmd("RPUSH", "q4", "one"); t1.join(); t2.join()
check("the first client to block gets the element, across fronts", first.get("r") == ["q4", "one"] and second.get("r") is None, "%r %r" % (first, second))

# 5
blk = Redis("127.0.0.1", PB, timeout=10); bid = blk.cmd("CLIENT", "ID"); res = {}
def blocked(): res["r"] = blk.cmd("BLPOP", "q5", 0)
th = threading.Thread(target=blocked); th.start(); time.sleep(0.3)
n = a.cmd("CLIENT", "UNBLOCK", bid, "ERROR"); th.join(5)
check("CLIENT UNBLOCK on A of a client blocked on B", n == 1 and isinstance(res.get("r"), RuntimeError) and "UNBLOCKED" in str(res.get("r")), "%r %r" % (n, res))

# 6
vic = Redis("127.0.0.1", PB, timeout=5); vid = vic.cmd("CLIENT", "ID")
lst = a.cmd("CLIENT", "LIST")
listed = isinstance(lst, str) and ("id=%d " % vid) in lst
k = a.cmd("CLIENT", "KILL", "ID", vid)
try: r = vic.cmd("PING"); closed = isinstance(r, Exception)
except (ConnectionError, OSError): closed = True
check("CLIENT LIST on A lists B's client; CLIENT KILL on A closes it", listed and k == 1 and closed, "listed=%r killed=%r closed=%r" % (listed, k, closed))

# 7
# no isolation (ruled 2026-09-29): another connection's write may land inside an EXEC; nothing may be lost
a.cmd("SET", "m", 0); stop = threading.Event(); seen_odd = []; incrbys = [0]; bad_exec = []
def incr_b():
    c = Redis("127.0.0.1", PB, timeout=10)
    while not stop.is_set(): c.cmd("INCRBY", "m", 2); incrbys[0] += 1
def check_multi():
    c = Redis("127.0.0.1", A, timeout=10)
    for _ in range(200):
        c.cmd("MULTI"); c.cmd("INCR", "m"); c.cmd("GET", "m"); c.cmd("INCR", "m")
        r = c.cmd("EXEC")
        if not (isinstance(r, list) and len(r) == 3): bad_exec.append(r)
        elif int(r[2]) != int(r[1]) + 1: seen_odd.append(r)
th = threading.Thread(target=incr_b); th.start(); check_multi(); stop.set(); th.join()
final = int(a.cmd("GET", "m")); want = 2 * incrbys[0] + 2 * 200
print("MEASURE EXEC with another front's write landing inside it: %d of 200 (no isolation, ruled 2026-09-29)" % len(seen_odd), flush=True)
check("MULTI/EXEC on A beside INCRBY on B: every EXEC answers, no increment lost (200 transactions, %d INCRBYs)" % incrbys[0],
      not bad_exec and final == want, "bad=%r final=%d want=%d" % (bad_exec[:2], final, want))

# 8
s = socket.create_connection(("127.0.0.1", PB)); s.sendall(b"*3\r\n$5\r\nBLPOP\r\n$2\r\nq8\r\n$1\r\n0\r\n"); time.sleep(0.3)
before = a.cmd("INFO", "clients"); s.close(); time.sleep(0.5); after = a.cmd("INFO", "clients")
bc = lambda t: int([l for l in t.split("\r\n") if l.startswith("blocked_clients:")][0].split(":")[1])
check("a client hanging up while blocked on B leaves the region", bc(before) == 1 and bc(after) == 0, "blocked before %d after %d" % (bc(before), bc(after)))

# 9
bstay = Redis("127.0.0.1", PB, timeout=5); bstay_id = bstay.cmd("CLIENT", "ID")
pb.terminate(); pb.wait(); time.sleep(0.5)
t0 = time.time(); fails = 0
for i in range(768):
    if a.cmd("SET", "after-b", i) != "OK": fails += 1
lst = a.cmd("CLIENT", "LIST")
check("B stops: its clients leave the region; A goes on (768 writes in %.1f s)" % (time.time() - t0), fails == 0 and ("id=%d " % bstay_id) not in lst, "fails=%d listed=%r" % (fails, ("id=%d " % bstay_id) in lst))

# 10
for i in range(50): a.cmd("SET", "s%d" % i, "v%d" % i)
a.cmd("HSET", "h", "f1", "x", "f2", "y"); a.cmd("ZADD", "z", 1, "one", 2, "two"); a.cmd("XADD", "st", "1-1", "k", "v"); a.cmd("RPUSH", "l", "a", "b")
a.cmd("SET", "ttl", "t"); a.cmd("PEXPIRE", "ttl", 600000); a.cmd("INCRBYFLOAT", "fl", "1.5")
mx = Redis("127.0.0.1", A, timeout=10); mx.cmd("MULTI"); mx.cmd("INCR", "mxk")          # a transaction open meanwhile
pk = Redis("127.0.0.1", A, timeout=20); pk.s.sendall(b"*3\r\n$5\r\nBLPOP\r\n$3\r\nq10\r\n$1\r\n0\r\n"); time.sleep(0.3)   # parked meanwhile
C = BASE + 3
pc = start([B + "/ribbit-redis-front", "--port", str(C), "--save", ""], "front-c", env)
t0 = time.time(); joined = up(C, 30); took = time.time() - t0
check("a front started later serves at once (%.2f s to listen)" % took, joined and took < 2, open(LOGS + "/front-c.log").read()[-400:])
if joined:
    c = Redis("127.0.0.1", C, timeout=10)
    same = all(c.cmd("GET", "s%d" % i) == "v%d" % i for i in range(50)) and c.cmd("HGETALL", "h") == a.cmd("HGETALL", "h") \
        and c.cmd("ZRANGE", "z", 0, -1, "WITHSCORES") == a.cmd("ZRANGE", "z", 0, -1, "WITHSCORES") and c.cmd("XLEN", "st") == 1 \
        and c.cmd("LRANGE", "l", 0, -1) == ["a", "b"] and c.cmd("GET", "fl") == "1.5" and c.cmd("DBSIZE") == a.cmd("DBSIZE") \
        and c.cmd("SCAN", 0, "COUNT", 1000) == a.cmd("SCAN", 0, "COUNT", 1000) and 0 < c.cmd("PTTL", "ttl") <= 600000
    check("it serves the one keyspace: values, types, expiry, SCAN order, DBSIZE", same)
    c.cmd("LPUSH", "q10", "served")
    pk.s.settimeout(5)
    try: served = pk._reply()
    except Exception as e: served = e
    check("a client parked on A is served by a push on the new front", served == ["q10", "served"], repr(served))
    r = mx.cmd("EXEC"); check("a transaction open on A meanwhile commits", r == [1], repr(r))
    for i in range(100): (a if i % 2 else c).cmd("INCR", "after-copy")
    check("both fronts go on in one order", a.cmd("GET", "after-copy") == "100" and c.cmd("GET", "after-copy") == "100")
    cl = c.cmd("CLIENT", "LIST"); check("the new front lists A's clients", isinstance(cl, str) and cl.count("id=") == a.cmd("CLIENT", "LIST").count("id="))
for p in procs:
    if p.poll() is None: p.terminate()
print("RESULT %s: %d passed, %d failed; logs in %s" % ("PASS" if bad == 0 else "FAIL", ok, bad, LOGS))
sys.exit(0 if bad == 0 else 1)
