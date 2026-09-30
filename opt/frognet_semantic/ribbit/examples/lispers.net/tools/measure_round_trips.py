#!/usr/bin/env python3
"""[ROUND_TRIPS_V1] Round trips per LISP operation: the blocking calls the request thread waits out, from
transport.stats "calls" around each operation. John 2026-09-26: "take advantage of the fact that you own api.php and
make the compound operations happen in one call." Over the Internet every one of these is a full RTT (fnw1 at
streamingfrog: ~22 ms each; registration.put 93 ms = 4). A mutating operation must cost ONE; a read answered from a
held view costs none.
usage: tools/measure_round_trips.py HOST PORT   (exit 1 while any mutating operation costs more than one)"""
import json, subprocess, sys, time
host, port = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else ("127.0.0.1", "8788")
p = subprocess.Popen(["./ribbit_cpp/ribbit-lisp", "--ram", host, port], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def call(op, **a):
    p.stdin.write(json.dumps({"operation": op, "args": a}) + "\n"); p.stdin.flush()
    r = json.loads(p.stdout.readline())
    if not r.get("ok"): raise RuntimeError("%s: %s" % (op, r.get("error")))
    return r.get("result")
def calls():
    s = call("transport.stats"); return (json.loads(s) if isinstance(s, str) else s)["calls"]
rows = []
def measure(label, op, mutating=True, **a):
    c0 = calls(); t0 = time.perf_counter(); r = call(op, **a); ms = (time.perf_counter() - t0) * 1000; n = calls() - c0
    rows.append((label, n, mutating, r, ms))
# warm the held views the operations use, so their one-time arm is not counted against them
call("site.add", iid="0", prefix="198.51.0.0/16", group="", accept_more_specifics=True)
call("resolver.wait_site", iid="0", prefix="198.51.0.0/16", group="", active=True)
call("resolution.get", iid="0", prefix="198.51.1.1/32", group="")
measure("site.add", "site.add", iid="0", prefix="198.18.0.0/16", group="", accept_more_specifics=True)
call("resolver.wait_site", iid="0", prefix="198.18.0.0/16", group="", active=True)
measure("registration.put (new)", "registration.put", iid="0", prefix="198.18.1.0/24", group="", rloc_set=["192.0.2.1"])
measure("registration.put (replace)", "registration.put", iid="0", prefix="198.18.1.0/24", group="", rloc_set=["192.0.2.2"])
measure("registration.delete", "registration.delete", iid="0", prefix="198.18.1.0/24", group="")
measure("map_cache.add", "map_cache.add", iid="0", prefix="198.19.1.0/24", group="", rloc_set=["192.0.2.3"])
measure("map_cache.delete", "map_cache.delete", iid="0", prefix="198.19.1.0/24", group="")
measure("ddt.add", "ddt.add", iid="0", prefix="198.20.0.0/16", group="", rloc_set=["192.0.2.90"])
measure("ddt.delete", "ddt.delete", iid="0", prefix="198.20.0.0/16", group="")
measure("database_mapping.add", "database_mapping.add", iid="0", prefix="198.21.1.0/24", group="", rloc_set=["192.0.2.4"])
measure("database_mapping.delete", "database_mapping.delete", iid="0", prefix="198.21.1.0/24", group="")
measure("map_resolver.add", "map_resolver.add", address="192.0.2.53")
measure("map_resolver.delete", "map_resolver.delete", address="192.0.2.53")
measure("site.delete", "site.delete", iid="0", prefix="198.18.0.0/16", group="")
measure("resolution.get (held)", "resolution.get", mutating=False, iid="0", prefix="198.51.1.1/32", group="")
call("site.delete", iid="0", prefix="198.51.0.0/16", group="")
p.stdin.close(); p.wait(timeout=10)
bad = 0
print("%-28s %11s %10s" % ("operation", "round trips", "wall ms"))
for label, n, mut, r, ms in rows:
    over = (mut and n > 1) or (not mut and n > 0)
    bad += over
    print("%-28s %11d %10.1f %s" % (label, n, ms, "  <-- over" if over else ""))
print("\n%s: %d operation(s) over" % ("ROUND-TRIPS PASS" if not bad else "ROUND-TRIPS FAIL", bad))
sys.exit(1 if bad else 0)
