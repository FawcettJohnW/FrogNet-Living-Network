#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""run_two_boxes.py -- boxes on one memory [ONE_ORDER_V1]: each box holds the region, applies every box's intents in
write order, and answers its own clients. Checked with two boxes (A, B) on a memory started here:
  1  a write acknowledged on A is read on B, at once                      (one order: B's read is after A's write)
  2  a client blocked on B is served by a push on A                       (parked in the order, on every box)
  3  a blocked command's time runs out on its own box                     ('u' intent)
  4  two clients blocked on B and A, one push: the first to block gets it (arrival order is the order)
  5  CLIENT UNBLOCK on A of a client blocked on B
  6  CLIENT LIST on A lists B's clients; CLIENT KILL on A closes B's client
  7  MULTI/EXEC on A is atomic against INCRs on B                         (applied in one turn)
  8  a client hanging up while blocked on B leaves the region             (its 'g' intent)
  9  B stops; A goes on (a gone reader is not waited for after 3 s)
 10  a box joining after the ring has wrapped takes a copy of the region and serves: values, expiry, SCAN order,
     a client parked and a transaction open across the copy, both boxes in one order       (join by copy)
Usage: run_two_boxes.py <build-dir> <base-port>        MEMORY=host:port for a memory elsewhere"""
import os, socket, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coordinated import Redis
B, BASE = sys.argv[1], int(sys.argv[2])
RING = 256
LOGS = os.environ.get("LOGS", "/tmp/two-boxes-%d" % os.getpid()); os.makedirs(LOGS, exist_ok=True)
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
# MEMORY=host:port: a memory elsewhere (a fresh one: a box joins only a region whose history it can replay)
if os.environ.get("MEMORY"): ram = os.environ["MEMORY"]
else:
    start([B + "/ribbit-redis-host", "--listen", "127.0.0.1:%d" % BASE], "memory")
    assert up(BASE), "the memory never listened"
    ram = "127.0.0.1:%d" % BASE
env = {"RIBBIT_RAM": ram, "RIBBIT_RING": str(RING)}
A, PB = BASE + 1, BASE + 2
start([B + "/ribbit-redis-front", "--port", str(A), "--save", ""], "box-a", dict(env, RIBBIT_REDIS_CONFIG_OWNER="1"))
pb = start([B + "/ribbit-redis-front", "--port", str(PB), "--save", ""], "box-b", env)
assert up(A) and up(PB), "a box never listened"
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
check("a blocked command's time runs out on its own box", got.get("r") is None and 0.95 <= got["s"] < 2.5, repr(got))

# 4
first, second = {}, {}
t1 = threading.Thread(target=blpop, args=(PB, "q4", 5, first)); t1.start(); time.sleep(0.3)
t2 = threading.Thread(target=blpop, args=(A, "q4", 2, second)); t2.start(); time.sleep(0.3)
a.cmd("RPUSH", "q4", "one"); t1.join(); t2.join()
check("the first client to block gets the element, across boxes", first.get("r") == ["q4", "one"] and second.get("r") is None, "%r %r" % (first, second))

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
a.cmd("SET", "m", 0); stop = threading.Event(); seen_odd = []
def incr_b():
    c = Redis("127.0.0.1", PB, timeout=10)
    while not stop.is_set(): c.cmd("INCRBY", "m", 2)
def check_multi():
    c = Redis("127.0.0.1", A, timeout=10)
    for _ in range(200):
        c.cmd("MULTI"); c.cmd("INCR", "m"); c.cmd("GET", "m"); c.cmd("INCR", "m")
        r = c.cmd("EXEC")
        if not (isinstance(r, list) and int(r[2]) == int(r[1]) + 1): seen_odd.append(r)
th = threading.Thread(target=incr_b); th.start(); check_multi(); stop.set(); th.join()
check("MULTI/EXEC on A is atomic against INCRBY on B (200 transactions)", not seen_odd, repr(seen_odd[:3]))

# 8
s = socket.create_connection(("127.0.0.1", PB)); s.sendall(b"*3\r\n$5\r\nBLPOP\r\n$2\r\nq8\r\n$1\r\n0\r\n"); time.sleep(0.3)
before = a.cmd("INFO", "clients"); s.close(); time.sleep(0.5); after = a.cmd("INFO", "clients")
bc = lambda t: int([l for l in t.split("\r\n") if l.startswith("blocked_clients:")][0].split(":")[1])
check("a client hanging up while blocked on B leaves the region", bc(before) == 1 and bc(after) == 0, "blocked before %d after %d" % (bc(before), bc(after)))

# 9
pb.terminate(); pb.wait()
t0 = time.time(); fails = 0
for i in range(RING * 3):                       # more intents than the ring holds: B's slot would block A if waited for
    if a.cmd("SET", "after-b", i) != "OK": fails += 1
check("B stops; A goes on past the ring (%d writes in %.1f s)" % (RING * 3, time.time() - t0), fails == 0 and time.time() - t0 < 30)

# 10
for i in range(50): a.cmd("SET", "s%d" % i, "v%d" % i)
a.cmd("HSET", "h", "f1", "x", "f2", "y"); a.cmd("ZADD", "z", 1, "one", 2, "two"); a.cmd("XADD", "st", "1-1", "k", "v"); a.cmd("RPUSH", "l", "a", "b")
a.cmd("SET", "ttl", "t"); a.cmd("PEXPIRE", "ttl", 600000); a.cmd("INCRBYFLOAT", "fl", "1.5")
mx = Redis("127.0.0.1", A, timeout=10); mx.cmd("MULTI"); mx.cmd("INCR", "mxk")          # a transaction open across the copy
pk = Redis("127.0.0.1", A, timeout=20); pk.s.sendall(b"*3\r\n$5\r\nBLPOP\r\n$3\r\nq10\r\n$1\r\n0\r\n"); time.sleep(0.3)   # parked across it
C = BASE + 3
pc = start([B + "/ribbit-redis-front", "--port", str(C), "--save", ""], "box-c", env)
joined = up(C, 30)
check("a box joining after the ring wrapped takes a copy and serves", joined, open(LOGS + "/box-c.log").read()[-400:])
if joined:
    c = Redis("127.0.0.1", C, timeout=10)
    same = all(c.cmd("GET", "s%d" % i) == "v%d" % i for i in range(50)) and c.cmd("HGETALL", "h") == a.cmd("HGETALL", "h") \
        and c.cmd("ZRANGE", "z", 0, -1, "WITHSCORES") == a.cmd("ZRANGE", "z", 0, -1, "WITHSCORES") and c.cmd("XLEN", "st") == 1 \
        and c.cmd("LRANGE", "l", 0, -1) == ["a", "b"] and c.cmd("GET", "fl") == "1.5" and c.cmd("DBSIZE") == a.cmd("DBSIZE") \
        and c.cmd("SCAN", 0, "COUNT", 1000) == a.cmd("SCAN", 0, "COUNT", 1000) and 0 < c.cmd("PTTL", "ttl") <= 600000
    check("the copy is the region: values, types, expiry, SCAN order, DBSIZE", same)
    c.cmd("LPUSH", "q10", "served")
    pk.s.settimeout(5)
    try: served = pk._reply()
    except Exception as e: served = e
    check("a client parked on A before the copy is served by a push on the new box", served == ["q10", "served"], repr(served))
    r = mx.cmd("EXEC"); check("a transaction open on A across the copy commits", r == [1], repr(r))
    for i in range(100): (a if i % 2 else c).cmd("INCR", "after-copy")
    check("both boxes go on in one order", a.cmd("GET", "after-copy") == "100" and c.cmd("GET", "after-copy") == "100")
    cl = c.cmd("CLIENT", "LIST"); check("the new box lists A's clients", isinstance(cl, str) and cl.count("id=") == a.cmd("CLIENT", "LIST").count("id="))
for p in procs:
    if p.poll() is None: p.terminate()
print("RESULT %s: %d passed, %d failed; logs in %s" % ("PASS" if bad == 0 else "FAIL", ok, bad, LOGS))
sys.exit(0 if bad == 0 else 1)
