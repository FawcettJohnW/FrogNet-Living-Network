#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""run_cache.py -- [CACHED_UNTIL_TOLD_V1] a front answers a GET it has read before, itself, until the region says the
key changed. Two fronts (A, B) on one memory started here:
  1  repeated GETs on A are answered by A: the region runs the first one only        (its GET calls, INFO commandstats)
  2  a write on A, then a GET on A: the new value, every time, from 20 connections   (the notice comes before the reply)
  3  a write on B: A's next GETs give the new value once A has read the notice; how long that takes is measured
  4  every way a string key changes, each done on B, each seen on A: SET, DEL, APPEND, INCR, SETRANGE, GETDEL, GETSET,
     RENAME (both keys), EXPIRE then PERSIST, MSET, FLUSHDB, FLUSHALL, SWAPDB, MOVE, a MULTI/EXEC, a key that expires
  5  a key with a time to live is not kept (it changes with nobody writing)
  6  db 1 is not db 0: SELECT on the connection keeps the copies apart
  7  RESP3 (HELLO 3): a missing key is "_", kept and answered as RESP3, never handed to a RESP2 connection
  8  binary keys and values, and values past one frame (500 KB), kept exactly
  9  a GET inside MULTI is queued by the region, not answered from the copy
Usage: run_cache.py <build-dir> <base-port>"""
import os, re, socket, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coordinated import Redis
B, BASE = sys.argv[1], int(sys.argv[2])
LOGS = os.environ.get("LOGS", "/tmp/cache-%d" % os.getpid()); os.makedirs(LOGS, exist_ok=True)
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
start([B + "/ribbit-redis-host", "--listen", "127.0.0.1:%d" % BASE], "memory"); assert up(BASE), "the memory never listened"
env = {"RIBBIT_RAM": "127.0.0.1:%d" % BASE}
PA, PB = BASE + 1, BASE + 2
start([B + "/ribbit-redis-front", "--port", str(PA), "--save", ""], "front-a", dict(env, RIBBIT_REDIS_CONFIG_OWNER="1"))
start([B + "/ribbit-redis-front", "--port", str(PB), "--save", ""], "front-b", env)
assert up(PA) and up(PB), "a front never listened"
ok = bad = 0
def check(name, cond, detail=""):
    global ok, bad
    if cond: ok += 1; print("PASS " + name, flush=True)
    else: bad += 1; print("FAIL " + name + (": " + detail if detail else ""), flush=True)
def R(p): return Redis("127.0.0.1", p, timeout=10)
a, b = R(PA), R(PB)
def get_calls():
    info = a.cmd("INFO", "commandstats")
    m = re.search(r"cmdstat_get:calls=(\d+)", info or ""); return int(m.group(1)) if m else 0
stale = [0]
def seen_on_a(key, want, limit=2.0):            # A's GETs until it gives `want`; seconds it took, or None
    t0 = time.time(); stale[0] = 0
    while time.time() - t0 < limit:
        if a.cmd("GET", key) == want: return time.time() - t0
        stale[0] += 1
    return None

# 1
a.cmd("SET", "c1", "v"); a.cmd("GET", "c1"); c0 = get_calls()
for _ in range(200): a.cmd("GET", "c1")
c1 = get_calls()
check("1 200 repeated GETs on A answered by A (region GET calls %d -> %d)" % (c0, c1), c1 == c0, "the region ran %d of them" % (c1 - c0))

# 2
conns = [R(PA) for _ in range(20)]; wrong = 0
for i in range(1000):
    w, r = conns[i % 20], conns[(i * 7 + 3) % 20]
    r.cmd("GET", "c2"); w.cmd("SET", "c2", str(i))
    if r.cmd("GET", "c2") != str(i): wrong += 1
check("2 a write on A then a GET on A, 1000 times over 20 connections: the new value every time", wrong == 0, "%d stale" % wrong)

# 3
lat = []; never = 0; stale_reads = []
for i in range(300):
    a.cmd("GET", "c3"); b.cmd("SET", "c3", str(i))
    t = seen_on_a("c3", str(i))
    if t is None: never += 1
    else: lat.append(t); stale_reads.append(stale[0])
lat.sort()
check("3 a write on B is seen on A after A reads the notice (300 writes)", never == 0, "%d never seen in 2 s" % never)
if lat: print("MEASURE 3 from B's acknowledged write to A giving the new value: p50 %.3f ms, p99 %.3f ms, max %.3f ms; A's first GET already new in %d of %d, at most %d stale GETs"
              % (lat[len(lat) // 2] * 1e3, lat[int(len(lat) * .99)] * 1e3, lat[-1] * 1e3, stale_reads.count(0), len(lat), max(stale_reads)), flush=True)

# 4
def case(name, setup, change, key, want):
    for c in setup: b.cmd(*c)
    a.cmd("GET", key); a.cmd("GET", key)          # A holds it
    for c in change: b.cmd(*c)
    t = seen_on_a(key, want)
    check("4 %s on B seen on A" % name, t is not None, "A still gives %r" % a.cmd("GET", key))
case("SET", [("SET", "k4", "x")], [("SET", "k4", "y")], "k4", "y")
case("DEL", [("SET", "k4", "x")], [("DEL", "k4")], "k4", None)
case("APPEND", [("SET", "k4", "x")], [("APPEND", "k4", "z")], "k4", "xz")
case("INCR", [("SET", "k4", "5")], [("INCR", "k4")], "k4", "6")
case("SETRANGE", [("SET", "k4", "hello")], [("SETRANGE", "k4", "1", "a")], "k4", "hallo")
case("GETDEL", [("SET", "k4", "x")], [("GETDEL", "k4")], "k4", None)
case("GETSET", [("SET", "k4", "x")], [("GETSET", "k4", "w")], "k4", "w")
case("RENAME (source)", [("SET", "k4", "x"), ("DEL", "k4b")], [("RENAME", "k4", "k4b")], "k4", None)
a.cmd("GET", "k4b")
b.cmd("SET", "k4", "q"); b.cmd("SET", "k4b", "old"); a.cmd("GET", "k4b"); a.cmd("GET", "k4b"); b.cmd("RENAME", "k4", "k4b")
check("4 RENAME (destination) on B seen on A", seen_on_a("k4b", "q") is not None, "A still gives %r" % a.cmd("GET", "k4b"))
case("MSET", [("SET", "k4", "x")], [("MSET", "k4", "m", "other", "1")], "k4", "m")
case("FLUSHDB", [("SET", "k4", "x")], [("FLUSHDB",)], "k4", None)
case("FLUSHALL", [("SET", "k4", "x")], [("FLUSHALL",)], "k4", None)
case("MULTI/EXEC", [("SET", "k4", "x")], [("MULTI",), ("SET", "k4", "t"), ("EXEC",)], "k4", "t")
case("MOVE", [("SET", "k4", "x")], [("MOVE", "k4", "3")], "k4", None)
b.cmd("SELECT", "1"); b.cmd("SET", "k4", "in1"); b.cmd("SELECT", "0")
case("SWAPDB", [("SET", "k4", "in0")], [("SWAPDB", "0", "1")], "k4", "in1")
b.cmd("SWAPDB", "0", "1")
case("EXPIRE then PERSIST", [("SET", "k4", "x")], [("EXPIRE", "k4", "100"), ("SET", "k4", "x2"), ("PERSIST", "k4")], "k4", "x2")
case("PEXPIRE that runs out", [("SET", "k4", "x")], [("PEXPIRE", "k4", "50")], "k4", None)

# 5
b.cmd("SET", "k5", "x", "PX", "300"); a.cmd("GET", "k5"); c0 = get_calls(); a.cmd("GET", "k5"); c1 = get_calls()
check("5 a key with a time to live is not kept (its GET goes to the region)", c1 > c0)
time.sleep(0.4)
check("5 ... and is gone when its time runs out", a.cmd("GET", "k5") is None)

# 6
s = R(PA); b.cmd("SET", "k6", "db0"); b.cmd("SELECT", "1"); b.cmd("SET", "k6", "db1"); b.cmd("SELECT", "0")
s.cmd("GET", "k6"); s.cmd("GET", "k6"); s.cmd("SELECT", "1")
check("6 SELECT 1: db 1's value, not db 0's copy", s.cmd("GET", "k6") == "db1" and s.cmd("GET", "k6") == "db1")
s.cmd("SELECT", "0")
check("6 SELECT 0 again: db 0's value", s.cmd("GET", "k6") == "db0")

# 7
def raw(port, cmds):
    sk = socket.create_connection(("127.0.0.1", port)); out = b""
    for c in cmds:
        sk.sendall(b"*%d\r\n" % len(c) + b"".join(b"$%d\r\n%s\r\n" % (len(x), x) for x in c)); time.sleep(0.05)
    sk.settimeout(0.5)
    try:
        while True:
            d = sk.recv(65536)
            if not d: break
            out += d
    except socket.timeout: pass
    sk.close(); return out
b.cmd("DEL", "k7")
r3 = raw(PA, [[b"HELLO", b"3"], [b"GET", b"k7"], [b"GET", b"k7"], [b"GET", b"k7"]])
check("7 RESP3: a missing key is _ every time (kept as RESP3)", r3.endswith(b"_\r\n_\r\n_\r\n"), repr(r3[-30:]))
r2 = raw(PA, [[b"GET", b"k7"], [b"GET", b"k7"]])
check("7 RESP2 beside it: $-1, not the RESP3 copy", r2 == b"$-1\r\n$-1\r\n", repr(r2))

# 8
key = bytes(range(256)); val = bytes(range(255, -1, -1)) * 3
sk = socket.create_connection(("127.0.0.1", PB)); sk.sendall(b"*3\r\n$3\r\nSET\r\n$256\r\n" + key + b"\r\n$768\r\n" + val + b"\r\n"); sk.recv(100); sk.close()
want = b"$768\r\n" + val + b"\r\n"
got = raw(PA, [[b"GET", key], [b"GET", key]])
check("8 a binary key and value, kept and answered exactly", got == want + want, "%d bytes" % len(got))
big = "B" * 500000; b.cmd("SET", "k8", big); a.cmd("GET", "k8")
check("8 a 500 KB value, kept and answered exactly", a.cmd("GET", "k8") == big)

# 9
b.cmd("SET", "k9", "x"); a.cmd("GET", "k9"); a.cmd("GET", "k9")
a.cmd("MULTI"); q = a.cmd("GET", "k9"); res = a.cmd("EXEC")
check("9 a GET inside MULTI is QUEUED by the region", q == "QUEUED" and res == ["x"], "%r %r" % (q, res))

for p in procs: p.terminate()
for p in procs: p.wait()
print("RESULT %s: %d passed, %d failed" % ("PASS" if bad == 0 else "FAIL", ok, bad))
sys.exit(0 if bad == 0 else 1)
