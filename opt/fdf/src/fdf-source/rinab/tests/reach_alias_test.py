#!/usr/bin/env python3
# reach_alias_test.py -- [ALIAS_QUARANTINE_V1] fdf-reach's alias choice, as a pure function: stability across session loss,
# quarantine against any other use, exhaustion refused. usage: reach_alias_test.py PATH/TO/fdf-reach
import importlib.machinery, importlib.util, sys
ld = importlib.machinery.SourceFileLoader("fdf_reach", sys.argv[1]); spec = importlib.util.spec_from_loader("fdf_reach", ld)
R = importlib.util.module_from_spec(spec); ld.exec_module(R)
FAILS = 0
def check(ok, what):
    global FAILS
    print(("PASS  " if ok else "FAIL  ") + what); FAILS += 0 if ok else 1
Q = R.QUARANTINE_S; now = 1_000_000.0
pick = lambda area, d, old={}, taken=set(), scope={}, rel={}, t=now: R.pick_alias(area, d, old, set(taken), scope, rel, t)
check(pick("B", "10.7.7.0/24") == "100.64.0.0/24", "nothing held: the first block")
check(pick("B", "10.7.7.0/24", old={"100.64.9.0/24": "10.7.7.0/24"}) == "100.64.9.0/24", "a reach keeps the block it has")
rel = {"100.64.5.0/24": {"area": "B", "real": "10.7.7.0/24", "t": now - 10 * Q}}
check(pick("B", "10.7.7.0/24", rel=rel) == "100.64.5.0/24", "B back after a session loss (long ago): its own block again")
rel = {"100.64.0.0/24": {"area": "B", "real": "10.7.7.0/24", "t": now - 5}}
check(pick("F", "10.7.7.0/24", rel=rel) == "100.64.1.0/24", "B's block released 5 s ago: F does not get it")
check(pick("B", "10.8.8.0/24", rel=rel) == "100.64.1.0/24", "nor does B for a DIFFERENT prefix (another use is another use)")
check(pick("B", "10.7.7.0/24", rel=rel) == "100.64.0.0/24", "B for the same prefix: its block at once")
rel = {"100.64.0.0/24": {"area": "B", "real": "10.7.7.0/24", "t": now - Q - 1}}
check(pick("F", "10.7.7.0/24", rel=rel) == "100.64.0.0/24", "after the quarantine any use may have it")
rel = {"100.64.0.0/24": {"area": "B", "real": "10.7.7.0/24", "t": now - 5}}
check(pick("F", "10.9.9.9/32", rel=rel) == "100.64.1.1/32", "a /32 inside a quarantined /24 is quarantined too (and never an .0 or .255 address)")
check(pick("F", "10.7.7.0/24", rel={"100.64.0.0/24": {"area": "B", "real": "10.7.7.0/24", "t": now - 10 * Q}}, taken={"100.64.0.0/24"}) == "100.64.1.0/24",
      "B's old block, held by another reach now: never shared")
check(pick("B", "10.7.7.0/24", rel={"100.64.0.0/24": {"area": "B", "real": "10.7.7.0/24", "t": now}}, scope={"100.64.0.0/16": "a route here"}) == "100.65.0.0/24",
      "a block that collides with this machine's own scope is never used, even B's own")
try:
    big = {"100.64.0.0/10": {"area": "X", "real": "10.0.0.0/8", "t": now}}
    pick("B", "10.7.7.0/24", rel=big); check(False, "every block quarantined: refused")
except R.Refused as e: check("no free alias block" in str(e), "every block quarantined: refused, saying so: %s" % e)
check(all(not R.ipaddress.ip_network(pick("B", "10.7.7.0/32", taken={str(h) + "/32" for h in []})).overlaps(R.SELVES) for _ in [0]), "never the private self range")
print("RESULT " + ("PASS" if FAILS == 0 else "FAIL: %d" % FAILS)); sys.exit(1 if FAILS else 0)
