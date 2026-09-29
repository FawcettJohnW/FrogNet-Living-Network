#!/usr/bin/env python3
"""coordinated.py -- a coordinated test and benchmark of one Redis region from many machines, coordinated through
Redis itself. Nothing connects into any machine: every participant and the coordinator talk only to their own front
(or any Redis endpoint serving the region), and the go signal is a Redis list.

  on every machine (against its own front):
      python3 coordinated.py participate --run RUN --name NAME [--port 6379] [-a PASSWORD] [--clients 20]
  anywhere (after or before the participants start; it waits for them):
      python3 coordinated.py coordinate  --run RUN --expect N  [--port 6379] [-a PASSWORD] [--seconds 20]

The run, all under coord:<RUN>: (every key expires after an hour):
  1. register   participant: SADD members NAME; then BLPOP go:NAME 0 -- blocked in the region, waiting
  2. go         coordinator: when N members are registered, LPUSH go:<each> {"t0":..., "seconds":S}; each participant
                wakes, waits until t0 (a common start a moment ahead, so all machines load together)
  3. load       participant: --clients connections for S seconds, each doing INCR total, one command at a time,
                counting the INCRs acknowledged and timing each; writes its markers SET mark:NAME:i "NAME:i"
  4. done       participant: HSET result:NAME {count, ops/s, p50, p99, ...}; RPUSH done NAME; BLPOP verify:NAME 0
  5. verify     coordinator: after N done, LPUSH verify:<each>; each participant GETs every other participant's
                markers and checks the exact values; RPUSH verified {"name":..., "missing":..., "wrong":...}
  6. judge      coordinator: total == sum of every participant's acknowledged INCRs (no write lost, none applied
                twice -- across machines, fronts and the Internet); every marker visible everywhere; the numbers.
Exit status 0 only if every check passes.
"""
import argparse, json, os, socket, sys, threading, time

# ---------------------------------------------------------------- a minimal RESP client (no dependencies)
class Redis:
    def __init__(self, host, port, password=None, timeout=None):
        self.s = socket.create_connection((host, port)); self.s.settimeout(timeout); self.buf = b""
        if password:
            r = self.cmd("AUTH", password)
            if isinstance(r, Exception): raise r
    def _line(self):
        while b"\r\n" not in self.buf:
            d = self.s.recv(1 << 16)
            if not d: raise ConnectionError("the front closed the connection")
            self.buf += d
        i = self.buf.index(b"\r\n"); l = self.buf[:i]; self.buf = self.buf[i + 2:]; return l
    def _exact(self, n):
        while len(self.buf) < n + 2:
            d = self.s.recv(1 << 16)
            if not d: raise ConnectionError("the front closed the connection")
            self.buf += d
        v = self.buf[:n]; self.buf = self.buf[n + 2:]; return v
    def _reply(self):
        l = self._line(); t, rest = l[:1], l[1:]
        if t == b"+": return rest.decode()
        if t == b"-": return RuntimeError(rest.decode())
        if t == b":": return int(rest)
        if t == b"$": n = int(rest); return None if n < 0 else self._exact(n).decode("utf-8", "replace")
        if t == b"*":
            n = int(rest); return None if n < 0 else [self._reply() for _ in range(n)]
        raise RuntimeError("unexpected reply %r" % l)
    def cmd(self, *args):
        a = [x if isinstance(x, bytes) else str(x).encode() for x in args]
        self.s.sendall(b"*%d\r\n" % len(a) + b"".join(b"$%d\r\n%s\r\n" % (len(x), x) for x in a))
        return self._reply()
    def must(self, *args):
        r = self.cmd(*args)
        if isinstance(r, Exception): raise r
        return r

def k(run, *parts): return "coord:%s:%s" % (run, ":".join(str(p) for p in parts))
TTL = 3600

# ---------------------------------------------------------------- participant
def participate(a):
    r = Redis(a.host, a.port, a.password)
    r.must("SADD", k(a.run, "members"), a.name); r.must("EXPIRE", k(a.run, "members"), TTL)
    print("[%s] registered for run %s; waiting for go" % (a.name, a.run), flush=True)
    go = r.must("BLPOP", k(a.run, "go", a.name), 0)                      # blocked in the region
    g = json.loads(go[1]); t0, seconds, a.markers = g["t0"], g["seconds"], g["markers"]   # the coordinator's numbers govern
    wait = t0 - time.time()
    print("[%s] go: load starts in %.2fs for %ss with %d connections" % (a.name, max(0, wait), seconds, a.clients), flush=True)
    if wait > 0: time.sleep(wait)

    lat = []; counts = [0] * a.clients; errors = []; lock = threading.Lock()
    def client(i):
        try:
            c = Redis(a.host, a.port, a.password); mine = []; n = 0
            end = t0 + seconds
            while time.time() < end:
                s = time.perf_counter(); v = c.cmd("INCR", k(a.run, "total")); e = time.perf_counter()
                if isinstance(v, Exception): errors.append(str(v)); break
                n += 1; mine.append((e - s) * 1000)
            counts[i] = n
            with lock: lat.extend(mine)
        except Exception as ex:
            errors.append("%s: %s" % (type(ex).__name__, ex))
    th = [threading.Thread(target=client, args=(i,)) for i in range(a.clients)]
    for t in th: t.start()
    for t in th: t.join()
    total = sum(counts); lat.sort()
    for i in range(a.markers): r.must("SET", k(a.run, "mark", a.name, i), "%s:%d" % (a.name, i), "EX", TTL)
    res = {"name": a.name, "count": total, "seconds": seconds, "ops_per_s": round(total / seconds, 1), "clients": a.clients,
           "p50_ms": round(lat[len(lat) // 2], 2) if lat else None, "p99_ms": round(lat[int(len(lat) * .99)], 2) if lat else None,
           "max_ms": round(lat[-1], 2) if lat else None, "errors": errors[:5], "markers": a.markers}
    r.must("SET", k(a.run, "result", a.name), json.dumps(res), "EX", TTL)
    r.must("RPUSH", k(a.run, "done"), a.name); r.must("EXPIRE", k(a.run, "done"), TTL)
    print("[%s] load done: %d INCRs acknowledged, %.1f/s, p50 %s ms, p99 %s ms%s" % (a.name, total, total / seconds, res["p50_ms"], res["p99_ms"],
          ("  ERRORS: %s" % errors[:3]) if errors else ""), flush=True)

    v = json.loads(r.must("BLPOP", k(a.run, "verify", a.name), 0)[1])  # everyone has written their markers
    missing = wrong = 0
    for other in v["members"]:
        if other == a.name: continue
        for i in range(v["markers"]):
            got = r.must("GET", k(a.run, "mark", other, i))
            if got is None: missing += 1
            elif got != "%s:%d" % (other, i): wrong += 1
    r.must("RPUSH", k(a.run, "verified"), json.dumps({"name": a.name, "missing": missing, "wrong": wrong}))
    r.must("EXPIRE", k(a.run, "verified"), TTL)
    print("[%s] verified the others' markers: %d missing, %d wrong" % (a.name, missing, wrong), flush=True)
    return 0 if not errors and not missing and not wrong else 1

# ---------------------------------------------------------------- coordinator
def coordinate(a):
    r = Redis(a.host, a.port, a.password)
    r.cmd("DEL", k(a.run, "total"), k(a.run, "done"), k(a.run, "verified"))
    print("[coordinator] run %s: waiting for %d participants" % (a.run, a.expect), flush=True)
    t_wait = time.time()
    while True:
        members = sorted(r.must("SMEMBERS", k(a.run, "members")) or [])
        if len(members) >= a.expect: break
        if time.time() - t_wait > a.register_timeout: sys.exit("[coordinator] only %d of %d registered: %s" % (len(members), a.expect, members))
        time.sleep(0.5)                                                  # (a poll only while waiting for people)
    if len(members) > a.expect:
        # a participant stopped in an earlier attempt of this run stays registered: waiting for it would never end
        sys.exit("[coordinator] %d registered for run %s, expected %d: %s -- some are left from an earlier attempt; "
                 "start again with a new --run name" % (len(members), a.run, a.expect, ", ".join(members)))
    t0 = time.time() + a.lead
    print("[coordinator] %d registered: %s -- GO at +%.1fs for %ds" % (len(members), ", ".join(members), a.lead, a.seconds), flush=True)
    for m in members: r.must("LPUSH", k(a.run, "go", m), json.dumps({"t0": t0, "seconds": a.seconds, "markers": a.markers})); r.must("EXPIRE", k(a.run, "go", m), TTL)
    done = []
    while len(done) < len(members):                                      # each done arrives as a list push
        x = r.must("BLPOP", k(a.run, "done"), a.seconds + a.lead + 120)
        if x is None: sys.exit("[coordinator] timed out waiting for done; have %s" % done)
        done.append(x[1])
    for m in members: r.must("LPUSH", k(a.run, "verify", m), json.dumps({"members": members, "markers": a.markers})); r.must("EXPIRE", k(a.run, "verify", m), TTL)
    ver = []
    while len(ver) < len(members):
        x = r.must("BLPOP", k(a.run, "verified"), 120)
        if x is None: sys.exit("[coordinator] timed out waiting for verification; have %s" % ver)
        ver.append(json.loads(x[1]))
    res = [json.loads(r.must("GET", k(a.run, "result", m))) for m in members]
    total = int(r.must("GET", k(a.run, "total")) or 0); acked = sum(x["count"] for x in res)
    ok_count = total == acked
    ok_vis = all(v["missing"] == 0 and v["wrong"] == 0 for v in ver)
    ok_err = all(not x["errors"] for x in res)
    print("\n== run %s: %d machines, %d s, started together" % (a.run, len(members), a.seconds))
    print("%-16s %8s %9s %8s %8s %8s %8s" % ("participant", "clients", "INCRs", "ops/s", "p50 ms", "p99 ms", "max ms"))
    for x in res: print("%-16s %8d %9d %8.1f %8s %8s %8s" % (x["name"], x["clients"], x["count"], x["ops_per_s"], x["p50_ms"], x["p99_ms"], x["max_ms"]))
    print("%-16s %8d %9d %8.1f" % ("ALL", sum(x["clients"] for x in res), acked, acked / a.seconds))
    print("\nCHECK counter: region total %d, acknowledged INCRs %d -> %s" % (total, acked, "PASS (no write lost, none applied twice)" if ok_count else "FAIL"))
    print("CHECK visibility: every machine's markers read back exactly on every other -> %s%s" % ("PASS" if ok_vis else "FAIL", "" if ok_vis else " %s" % ver))
    print("CHECK errors: %s" % ("none" if ok_err else [(x["name"], x["errors"]) for x in res if x["errors"]]))
    ok = ok_count and ok_vis and ok_err
    print("RESULT", "GREEN" if ok else "RED")
    return 0 if ok else 1

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("role", choices=["participate", "coordinate"])
    ap.add_argument("--run", required=True, help="a name for this run, the same on every machine")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=6379)
    ap.add_argument("-a", dest="password", default=os.environ.get("REDISCLI_AUTH"))
    ap.add_argument("--name", help="participant: this machine's name"); ap.add_argument("--clients", type=int, default=20)
    ap.add_argument("--expect", type=int, help="coordinator: how many participants"); ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--markers", type=int, default=100, help="coordinator: marker keys each participant writes")
    ap.add_argument("--lead", type=float, default=2.0, help="coordinator: seconds between GO and the common start")
    ap.add_argument("--register-timeout", type=float, default=600)
    a = ap.parse_args()
    if a.role == "participate":
        if not a.name: ap.error("participate needs --name")
        sys.exit(participate(a))
    if not a.expect: ap.error("coordinate needs --expect")
    sys.exit(coordinate(a))

if __name__ == "__main__":
    main()
