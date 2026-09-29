#!/usr/bin/env python3
"""run_concurrency.py -- the concurrency audit. Redis executes one command at a time; the region executes commands from
different fronts truly in parallel (one host thread per front session). Every command family that reads a key's
state and writes from it is hammered here from several fronts at once, and checked against the guarantee Redis gives.
Usage: run_concurrency.py <build-dir> <base-port> [fronts=3] [threads-per-front=6] [only=family,...]"""
import os, socket, subprocess, sys, threading, time, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coordinated import Redis
B, BASE = sys.argv[1], int(sys.argv[2]); NF = int(sys.argv[3]) if len(sys.argv) > 3 else 3
NT = int(sys.argv[4]) if len(sys.argv) > 4 else 6; ONLY = set(sys.argv[5].split(",")) if len(sys.argv) > 5 else None
API = "/Fawcett.Redis.ram_interface.php"; env = dict(os.environ, FROGNET_BLOB_ROOT="/tmp/conc-blobs"); os.makedirs(env["FROGNET_BLOB_ROOT"], exist_ok=True)
def up(p):
    t0 = time.time()
    while time.time() - t0 < 10:
        try: socket.create_connection(("127.0.0.1", p), timeout=1).close(); return
        except OSError: time.sleep(0.02)
    sys.exit("port %d never opened" % p)
procs = [subprocess.Popen([B + "/ribbit-redis-host", "--listen", "127.0.0.1:%d" % BASE, "--api", API, "--quiet"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
up(BASE)
for i in range(NF):
    procs.append(subprocess.Popen([B + "/ribbit-redis-front", "--port", str(BASE + 1 + i), "--save", ""], env=dict(env, RIBBIT_RAM="127.0.0.1:%d" % BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    up(BASE + 1 + i)
PORTS = [BASE + 1 + i for i in range(NF)]
admin = Redis("127.0.0.1", PORTS[0])
results = []
def run(n_per_thread, work):
    """work(conn, tid, i) for i in range(n_per_thread), on NF*NT threads spread over the fronts; returns per-thread outputs"""
    outs = [None] * (NF * NT)
    def t(tid):
        c = Redis("127.0.0.1", PORTS[tid % NF]); o = []
        for i in range(n_per_thread): o.append(work(c, tid, i))
        outs[tid] = o
    th = [threading.Thread(target=t, args=(k,)) for k in range(NF * NT)]
    for x in th: x.start()
    for x in th: x.join()
    return outs
def family(name, ok, detail):
    results.append((name, ok, detail)); print(("PASS " if ok else "FAIL ") + name + " -- " + detail, flush=True)
def want(name): return ONLY is None or name in ONLY
N = 150; W = NF * NT
# ---- counters: the final value is the sum
for cmd, key, args, getter in [("INCR", "c:incr", [], ["GET", "c:incr"]), ("INCRBY", "c:incrby", ["3"], ["GET", "c:incrby"]),
                               ("DECR", "c:decr", [], ["GET", "c:decr"]), ("INCRBYFLOAT", "c:float", ["0.5"], ["GET", "c:float"]),
                               ("HINCRBY", "h:c", ["f", "2"], ["HGET", "h:c", "f"]), ("HINCRBYFLOAT", "h:cf", ["f", "0.5"], ["HGET", "h:cf", "f"]),
                               ("ZINCRBY", "z:c", ["1", "m"], ["ZSCORE", "z:c", "m"])]:
    if not want(cmd): continue
    admin.cmd("DEL", key)
    if cmd in ("HINCRBY", "HINCRBYFLOAT"): run(N, lambda c, t, i, cmd=cmd, key=key, args=args: c.cmd(cmd, key, *args))
    elif cmd == "ZINCRBY": run(N, lambda c, t, i: c.cmd("ZINCRBY", key, "1", "m"))
    else: run(N, lambda c, t, i, cmd=cmd, key=key, args=args: c.cmd(cmd, key, *args))
    per = {"INCR": 1, "INCRBY": 3, "DECR": -1, "INCRBYFLOAT": 0.5, "HINCRBY": 2, "HINCRBYFLOAT": 0.5, "ZINCRBY": 1}[cmd]
    exp = per * N * W; got = float(admin.cmd(*getter) or 0)
    family(cmd, abs(got - exp) < 1e-6, "expected %g, region has %g (%+g)" % (exp, got, got - exp))
# ---- pushes: every element present once
for cmd in ("LPUSH", "RPUSH"):
    if not want(cmd): continue
    admin.cmd("DEL", "l:" + cmd); run(N, lambda c, t, i, cmd=cmd: c.cmd(cmd, "l:" + cmd, "%d:%d" % (t, i)))
    got = admin.cmd("LRANGE", "l:" + cmd, "0", "-1") or []
    family(cmd, len(got) == N * W and len(set(got)) == N * W, "pushed %d, list has %d (%d distinct)" % (N * W, len(got), len(set(got))))
if want("XADD"):
    admin.cmd("DEL", "x:add"); ids = run(N, lambda c, t, i: c.cmd("XADD", "x:add", "*", "v", "%d:%d" % (t, i)))
    flat = [x for o in ids for x in o]; n = int(admin.cmd("XLEN", "x:add"))
    errs = [x for x in flat if isinstance(x, Exception)]; flat = [x for x in flat if not isinstance(x, Exception)]
    if errs: print("XADD errors: %d, first: %s" % (len(errs), errs[0]))
    walked = n
    if n != N * W:   # XLEN is a rank difference; XRANGE walks the entries themselves -- which one is short?
        rng = admin.cmd("XRANGE", "x:add", "-", "+") or []; walked = len(rng)
        missing = sorted(set(flat) - set(e[0] for e in rng))
        print("XADD diagnosis: XLEN %d, XRANGE walked %d, ids returned but not in XRANGE: %s" % (n, walked, missing[:5]))
    family("XADD", not errs and n == N * W and len(set(flat)) == N * W, "added %d, XLEN %d, %d distinct ids returned, %d errors" % (N * W, n, len(set(flat)), len(errs)))
if want("HSET"):
    admin.cmd("DEL", "h:fields"); run(N, lambda c, t, i: c.cmd("HSET", "h:fields", "%d:%d" % (t, i), "v"))
    n = int(admin.cmd("HLEN", "h:fields")); family("HSET (distinct fields)", n == N * W, "set %d fields, HLEN %d" % (N * W, n))
if want("SADD"):
    admin.cmd("DEL", "s:add"); run(N, lambda c, t, i: c.cmd("SADD", "s:add", "%d:%d" % (t, i)))
    n = int(admin.cmd("SCARD", "s:add")); family("SADD (distinct members)", n == N * W, "added %d, SCARD %d" % (N * W, n))
# ---- pops and takes: nothing delivered twice, nothing lost
for cmd, fill, pop in [("LPOP", ["RPUSH", "l:pop"], lambda c: c.cmd("LPOP", "l:pop")), ("RPOP", ["RPUSH", "l:rpop"], lambda c: c.cmd("RPOP", "l:rpop")),
                       ("SPOP", ["SADD", "s:pop"], lambda c: c.cmd("SPOP", "s:pop")), ("ZPOPMIN", ["ZADD", "z:pop"], lambda c: c.cmd("ZPOPMIN", "z:pop"))]:
    if not want(cmd): continue
    key = fill[1]; admin.cmd("DEL", key); M = N * W
    for j in range(0, M, 500):
        chunk = [str(x) for x in range(j, min(M, j + 500))]
        if cmd == "ZPOPMIN": args = [y for x in chunk for y in (x, "e" + x)]; admin.cmd("ZADD", key, *args)
        else: admin.cmd(fill[0], key, *["e" + x for x in chunk])
    got = [x for o in run(N, lambda c, t, i, pop=pop: pop(c)) for x in o]
    items = [(g[0] if isinstance(g, list) and g else g) for g in got if g]
    dup = len(items) - len(set(items))
    family(cmd, dup == 0 and len(items) == M, "%d elements, %d delivered, %d delivered twice" % (M, len(items), dup))
if want("GETDEL"):
    got = 0
    for r_ in range(60):
        admin.cmd("SET", "g:del", "v"); outs = run(1, lambda c, t, i: c.cmd("GETDEL", "g:del"))
        got += sum(1 for o in outs for x in o if x == "v") - 1
    family("GETDEL", got == 0, "60 rounds, %d extra deliveries of the one value" % got)
# ---- conditional sets: exactly one winner
for cmd in ("SETNX", "SET NX", "HSETNX", "MSETNX"):
    if not want(cmd.split()[0]): continue
    extra = 0
    for r_ in range(60):
        admin.cmd("DEL", "nx:k", "nx:h", "nx:k2")
        if cmd == "SETNX": outs = run(1, lambda c, t, i: c.cmd("SETNX", "nx:k", str(t))); wins = sum(1 for o in outs for x in o if x == 1)
        elif cmd == "SET NX": outs = run(1, lambda c, t, i: c.cmd("SET", "nx:k", str(t), "NX")); wins = sum(1 for o in outs for x in o if x == "OK")
        elif cmd == "HSETNX": outs = run(1, lambda c, t, i: c.cmd("HSETNX", "nx:h", "f", str(t))); wins = sum(1 for o in outs for x in o if x == 1)
        else: outs = run(1, lambda c, t, i: c.cmd("MSETNX", "nx:k", str(t), "nx:k2", str(t))); wins = sum(1 for o in outs for x in o if x == 1)
        extra += wins - 1
    family(cmd, extra == 0, "60 rounds of %d racers, %d extra winners" % (W, extra))
# ---- moves: conserved
for cmd in ("LMOVE", "SMOVE"):
    if not want(cmd): continue
    a_, b_ = "mv:a:" + cmd, "mv:b:" + cmd; admin.cmd("DEL", a_, b_); M = N * W // 2
    fill = "RPUSH" if cmd == "LMOVE" else "SADD"
    for j in range(0, M, 500): admin.cmd(fill, a_, *["e%d" % x for x in range(j, min(M, j + 500))])
    if cmd == "LMOVE": run(N, lambda c, t, i: c.cmd("LMOVE", a_, b_, "LEFT", "RIGHT") if t % 2 == 0 else c.cmd("LMOVE", b_, a_, "LEFT", "RIGHT"))
    else: run(N, lambda c, t, i: c.cmd("SMOVE", a_, b_, "e%d" % ((t * 7919 + i) % M)) if t % 2 == 0 else c.cmd("SMOVE", b_, a_, "e%d" % ((t * 104729 + i) % M)))
    if cmd == "LMOVE": allv = (admin.cmd("LRANGE", a_, "0", "-1") or []) + (admin.cmd("LRANGE", b_, "0", "-1") or [])
    else: allv = (admin.cmd("SMEMBERS", a_) or []) + (admin.cmd("SMEMBERS", b_) or [])
    family(cmd, len(allv) == M and len(set(allv)) == M, "%d elements, now %d (%d distinct)" % (M, len(allv), len(set(allv))))
# ---- edits in place: every writer's edit present
if want("APPEND"):
    admin.cmd("DEL", "e:app"); run(N, lambda c, t, i: c.cmd("APPEND", "e:app", "x"))
    n = int(admin.cmd("STRLEN", "e:app")); family("APPEND", n == N * W, "appended %d bytes, STRLEN %d" % (N * W, n))
if want("SETBIT"):
    admin.cmd("DEL", "e:bit"); run(N, lambda c, t, i: c.cmd("SETBIT", "e:bit", str(t * N + i), "1"))
    n = int(admin.cmd("BITCOUNT", "e:bit")); family("SETBIT (distinct bits)", n == N * W, "set %d bits, BITCOUNT %d" % (N * W, n))
if want("SETRANGE"):
    admin.cmd("DEL", "e:rng"); run(N, lambda c, t, i: c.cmd("SETRANGE", "e:rng", str(t * N + i), "y"))
    v = admin.cmd("GET", "e:rng") or ""; n = v.count("y"); family("SETRANGE (distinct offsets)", n == N * W, "wrote %d offsets, %d present" % (N * W, n))
if want("XREADGROUP"):
    admin.cmd("DEL", "x:grp"); M = N * W
    for j in range(M): admin.cmd("XADD", "x:grp", "*", "v", str(j))
    admin.cmd("XGROUP", "CREATE", "x:grp", "g", "0")
    got = run(N, lambda c, t, i: c.cmd("XREADGROUP", "GROUP", "g", "c%d" % t, "COUNT", "1", "STREAMS", "x:grp", ">"))
    ids = []
    for o in got:
        for x in o:
            if isinstance(x, list) and x and x[0][1]: ids.append(x[0][1][0][0])
    dup = len(ids) - len(set(ids))
    family("XREADGROUP", dup == 0 and len(ids) == M, "%d entries, %d delivered, %d delivered twice" % (M, len(ids), dup))

# ---- [second pass] conditionals, moves, removals with counted replies, claims, registers
def rounds(name, setup, work, winners_of, want=1, n=40):
    extra = 0
    for _ in range(n):
        setup(); outs = run(1, work); extra += abs(winners_of(outs) - want)
    family(name, extra == 0, "%d rounds of %d racers, %d off from %d winner(s) per round" % (n, W, extra, want))
if want("EXPIRE"):
    rounds("EXPIRE NX", lambda: (admin.cmd("DEL", "ex:k"), admin.cmd("SET", "ex:k", "v")),
           lambda c, t, i: c.cmd("EXPIRE", "ex:k", str(1000 + t), "NX"), lambda o: sum(1 for x in o for y in x if y == 1))
if want("RENAMENX"):
    def setup_rn():
        admin.cmd("DEL", "rn:dst", *["rn:src%d" % t for t in range(W)])
        for t in range(W): admin.cmd("SET", "rn:src%d" % t, str(t))
    rounds("RENAMENX", setup_rn, lambda c, t, i: c.cmd("RENAMENX", "rn:src%d" % t, "rn:dst"), lambda o: sum(1 for x in o for y in x if y == 1))
if want("COPY"):
    rounds("COPY (no REPLACE)", lambda: (admin.cmd("DEL", "cp:dst"), admin.cmd("SET", "cp:src", "v")),
           lambda c, t, i: c.cmd("COPY", "cp:src", "cp:dst"), lambda o: sum(1 for x in o for y in x if y == 1))
if want("MOVE"):
    bad = 0
    for _ in range(30):
        for d in range(1, 9): admin.cmd("SELECT", str(d)); admin.cmd("DEL", "mv:k")
        admin.cmd("SELECT", "0"); admin.cmd("SET", "mv:k", "v")
        run(1, lambda c, t, i: c.cmd("MOVE", "mv:k", str(1 + t % 8)))
        where = 0
        for d in range(0, 9): admin.cmd("SELECT", str(d)); where += int(admin.cmd("EXISTS", "mv:k"))
        admin.cmd("SELECT", "0"); bad += where != 1
    family("MOVE", bad == 0, "30 rounds: the key present in exactly one database -- %d rounds wrong" % bad)
if want("LINSERT"):
    admin.cmd("DEL", "li:k"); admin.cmd("RPUSH", "li:k", "a", "b")
    run(N // 3, lambda c, t, i: c.cmd("LINSERT", "li:k", "AFTER", "a", "%d:%d" % (t, i)))
    got = admin.cmd("LRANGE", "li:k", "0", "-1") or []; M = (N // 3) * W
    ok = len(got) == M + 2 and got[0] == "a" and got[-1] == "b" and len(set(got)) == M + 2
    family("LINSERT (one gap)", ok, "inserted %d between a and b, list has %d (%d distinct), ends %s..%s" % (M, len(got) - 2, len(set(got)) - 2, got[:1], got[-1:]))
if want("LREM"):
    admin.cmd("DEL", "lr:k"); M = N * W
    for j in range(0, M, 500): admin.cmd("RPUSH", "lr:k", *["x"] * min(500, M - j))
    counts = run(N, lambda c, t, i: c.cmd("LREM", "lr:k", "1", "x"))
    tot = sum(y for o in counts for y in o); left = int(admin.cmd("LLEN", "lr:k"))
    family("LREM (counted)", tot == M and left == 0, "%d elements, replies sum to %d, %d left" % (M, tot, left))
if want("XDEL"):
    admin.cmd("DEL", "xd:k"); M = N * W; ids = [admin.cmd("XADD", "xd:k", "*", "v", str(j)) for j in range(M)]
    counts = run(N, lambda c, t, i: c.cmd("XDEL", "xd:k", ids[(t * 7 + i) % M], ids[(t * 13 + i * 3) % M]))
    tot = sum(y for o in counts for y in o); left = int(admin.cmd("XLEN", "xd:k"))
    family("XDEL (counted)", tot == M - left, "replies sum to %d, entries removed %d" % (tot, M - left))
if want("XACK"):
    admin.cmd("DEL", "xa:k"); M = N * W
    for j in range(M): admin.cmd("XADD", "xa:k", "*", "v", str(j))
    admin.cmd("XGROUP", "CREATE", "xa:k", "g", "0"); got = admin.cmd("XREADGROUP", "GROUP", "g", "c", "COUNT", str(M), "STREAMS", "xa:k", ">")
    ids = [e[0] for e in got[0][1]]
    counts = run(N, lambda c, t, i: c.cmd("XACK", "xa:k", "g", ids[(t * 7 + i) % M], ids[(t * 11 + i * 5) % M]))
    tot = sum(y for o in counts for y in o); pend = admin.cmd("XPENDING", "xa:k", "g")[0]
    family("XACK (counted)", tot == M - int(pend), "replies sum to %d, acknowledged %d" % (tot, M - int(pend)))
if want("XCLAIM"):
    admin.cmd("DEL", "xc:k"); M = 200
    for j in range(M): admin.cmd("XADD", "xc:k", "*", "v", str(j))
    admin.cmd("XGROUP", "CREATE", "xc:k", "g", "0"); got = admin.cmd("XREADGROUP", "GROUP", "g", "old", "COUNT", str(M), "STREAMS", "xc:k", ">")
    ids = [e[0] for e in got[0][1]]
    time.sleep(0.3)                                      # every entry idle for 300 ms: claimable once, then idle ~0
    outs = run(1, lambda c, t, i: c.cmd("XCLAIM", "xc:k", "g", "c%d" % t, "200", *ids, "JUSTID"))
    claimed = [x for o in outs for y in o for x in (y or [])]
    family("XCLAIM (min idle)", len(claimed) == M and len(set(claimed)) == M,
           "%d entries idle past the minimum, %d claims answered (%d distinct) -- each must be claimed exactly once" % (M, len(claimed), len(set(claimed))))
if want("PFADD"):
    admin.cmd("DEL", "hl:par", "hl:seq"); M = N * W
    run(N, lambda c, t, i: c.cmd("PFADD", "hl:par", "e%d:%d" % (t, i)))
    for j in range(0, M, 500): admin.cmd("PFADD", "hl:seq", *["e%d:%d" % (x // N, x % N) for x in range(j, min(M, j + 500))])
    a_, b_ = int(admin.cmd("PFCOUNT", "hl:par")), int(admin.cmd("PFCOUNT", "hl:seq"))
    family("PFADD", a_ == b_, "concurrent PFCOUNT %d, same elements added one after another %d" % (a_, b_))
if want("GETSET"):
    admin.cmd("SET", "gs:k", "start")
    outs = run(N, lambda c, t, i: c.cmd("GETSET", "gs:k", "%d:%d" % (t, i)))
    handed = [y for o in outs for y in o] + [admin.cmd("GET", "gs:k")]
    family("GETSET (chain)", len(handed) == len(set(handed)) and "start" in handed, "%d values set, %d handed back, %d distinct" % (N * W, len(handed) - 1, len(set(handed)) - 1))

for p in procs: p.kill()
bad = [r for r in results if not r[1]]
print("\nRESULT %s: %d of %d families hold their guarantee under concurrency; failing: %s" % ("GREEN" if not bad else "RED", len(results) - len(bad), len(results), ", ".join(r[0] for r in bad) or "none"))
sys.exit(1 if bad else 0)
