"""[ELECTION_FROM_FACTS_V1] simulator: databasehost.frognet switchover from the region's facts, no store.

Five members shaped like the fleet (HappyDog 10.123.123.1, New-York-2 10.28.28.1, Seattle5 10.250.250.1, Seattle7
10.170.170.1, Seattle3B 10.251.251.1), each with a capability fact. Every node runs the SAME elect_now the daemon runs
(as itself: its own .1, its own /etc/hosts in a temp dir), over the same participants. Then: the databasehost dies
(alive=False) -> every node must name the same replacement at once; it returns -> every node must name it again; a
member with no mysql must never be databasehost; the store must never be consulted (T.get raises here).

    PYTHONPATH=/opt/frognet_semantic python3 discovery/dbhost_switch_sim.py
"""
import copy, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["FROGNET_OFFLINE_TUPLES"] = "0"
from core import frognet_tuples as T
# [STORE_READS_COUNTED_V1] counted, not raised: a raise inside a caller's `except Exception` is swallowed and the
# read goes unseen (gather_candidates' reach_plane read did exactly that through r16). Every call is recorded and
# the last check asserts there were none.
STORE_READS = []
def _no_store(*a, **k):
    STORE_READS.append((a, k))
    raise AssertionError("the store was read during a region election: %s %s" % (a, k))
T.get = _no_store                                                  # the store is not an input; prove it
from discovery.elect import elect_now

def cap(mysql, cores, mem_gb, disk, ffmpeg=True):
    return {"cores": cores, "cpu_mhz": 2000, "cpu_bench_total": cores * 3000.0, "mem_total_kb": int(mem_gb * 1024 * 1024),
            "mem_available_kb": int(mem_gb * 1024 * 1024 * 0.6), "disk_write_mbps": 300.0, "disk_fsync_ms": 3.0,
            "disk_free_gb": 40.0, "disk_class": disk, "disk_total_gb": 120.0, "av_port": 8554,
            "mysql": mysql, "mysql_running": mysql, "ffmpeg": ffmpeg, "libvpx": ffmpeg, "hw_encoder": "none",
            "loadavg": {"1": 0.2, "5": 0.2, "15": 0.2}, "temps_c": []}

def member(name, dot1, c):
    sub = dot1.rsplit(".", 1)[0] + ".0/24"
    return {"name": name, "alive": True, "facts": {"role": "node", "name": name, "guid": "g-" + name, "subnet": sub,
            "addrs": [{"iface": "wlan0", "ip": dot1, "value": "FrogNetHost." + name, "kind": "own", "mac": "aa"}],
            "capability": c, "capability_ts": 1000.0}}

FLEET = [member("HappyDog",   "10.123.123.1", cap(True,  4, 8,  "ssd")),
         member("New-York-2", "10.28.28.1",   cap(True,  4, 4,  "sdcard")),
         member("Seattle5",   "10.250.250.1", cap(True,  4, 8,  "ssd")),
         member("Seattle7",   "10.170.170.1", cap(False, 4, 4,  "sdcard")),       # no mysql: never databasehost
         member("Seattle3B",  "10.251.251.1", cap(True,  4, 16, "nvme"))]
# LAN relations as the fleet has them: New-York-2 holds a lease HappyDog gave; Seattle7 holds one Seattle5 gave
def lease(parent, child, ip, mac):
    parent["facts"]["addrs"].append({"iface": "wlan0", "ip": ip, "value": child["name"], "kind": "given", "mac": mac})
    child["facts"]["addrs"].append({"iface": "wlan1", "ip": ip, "value": child["name"], "kind": "held", "mac": mac})
    child["facts"]["addrs"].append({"iface": "wlan1", "ip": ip, "value": "uplink wifi 72", "kind": "uplink", "mac": mac})
lease(FLEET[0], FLEET[1], "10.123.123.230", "b8:27:01")
lease(FLEET[2], FLEET[3], "10.250.250.63", "b8:27:02")
HUB = {"name": "hubA", "alive": True, "facts": {"role": "hub", "pubkey": "H", "endpoint": "e"}}

FAIL = 0
def check(name, cond, detail=""):
    global FAIL
    print(("PASS  " if cond else "FAIL  ") + name + (("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond: FAIL += 1

def elect_everywhere(parts, tag):
    """Every alive node elects as itself. Returns {node: (databasehost, mediahost)} and the slowest election's ms."""
    out, worst = {}, 0.0
    alive = [(p["facts"]["addrs"][0]["ip"], p["name"]) for p in parts if p["alive"] and p["facts"].get("role") == "node"]
    for p in parts:
        if not p["alive"] or p["facts"].get("role") != "node":
            continue
        me = p["facts"]["addrs"][0]["ip"]
        d = tempfile.mkdtemp(prefix="hosts-%s-" % p["name"])
        lines = []
        t0 = time.monotonic()
        r = elect_now(logger=lines.append, alive_hosts=alive, participants=[HUB] + parts, self_ip=me,
                      hosts_path=os.path.join(d, "hosts"), apply=False)
        ms = (time.monotonic() - t0) * 1000; worst = max(worst, ms)
        out[p["name"]] = (r["databasehost"], r["mediahost"])
        if os.environ.get("SIM_VERBOSE"):
            print("--- %s as %s:\n    " % (tag, p["name"]) + "\n    ".join(l for l in lines if "ELECT" in l or "DBHOST" in l or "SERVICE_ELECT" in l))
    return out, worst

def agree(res, role_idx):
    vals = {v[role_idx] for v in res.values()}
    return (vals.pop() if len(vals) == 1 else None), vals

parts = copy.deepcopy(FLEET)
res, ms = elect_everywhere(parts, "steady")
db, _ = agree(res, 0)
check("steady: every node names the same databasehost", db is not None, res)
check("steady: it is Seattle3B (mysql, most memory, nvme)", db == "10.251.251.1", db)
check("steady: slowest election under 200 ms (no store, no network)", ms < 200, "%.0f ms" % ms)
print("      databasehost=%s mediahost per node=%s  (%.0f ms worst)" % (db, {k: v[1] for k, v in res.items()}, ms))
check("mediahost is on-segment from the GRAPH: HappyDog and New-York-2 (one LAN) agree", res["HappyDog"][1] == res["New-York-2"][1], res)
check("mediahost: Seattle5 and Seattle7 (one LAN) agree", res["Seattle5"][1] == res["Seattle7"][1], res)
check("mediahost: Seattle3B alone on its LAN names itself", res["Seattle3B"][1] == "10.251.251.1", res)

# ---- the databasehost dies
for p in parts:
    if p["name"] == "Seattle3B": p["alive"] = False
t0 = time.monotonic(); res, ms = elect_everywhere(parts, "3B dead"); dt = (time.monotonic() - t0) * 1000
db2, split = agree(res, 0)
check("3B dead: every node switches databasehost.frognet, and to the same host", db2 is not None and db2 != "10.251.251.1", res)
check("3B dead: the replacement has mysql (never Seattle7)", db2 in ("10.123.123.1", "10.250.250.1", "10.28.28.1"), db2)
check("3B dead: all five decided within one event (%.0f ms for all nodes)" % dt, dt < 1000)
print("      databasehost=%s" % db2)

# ---- it comes back
for p in parts:
    if p["name"] == "Seattle3B": p["alive"] = True
res, ms = elect_everywhere(parts, "3B back")
db3, _ = agree(res, 0)
check("3B back: every node names it again", db3 == "10.251.251.1", res)

# ---- the replacement dies too (two down): still unanimous, still a mysql host
for p in parts:
    if p["name"] in ("Seattle3B",) or p["facts"]["addrs"][0]["ip"] == db2: p["alive"] = False
res, ms = elect_everywhere(parts, "two dead")
db4, _ = agree(res, 0)
check("two dead: unanimous, mysql host", db4 is not None and db4 not in ("10.251.251.1", db2, "10.170.170.1"), res)

# ---- a capability change: 3B back, Seattle5 grows to 32 GB -> still 3B? (memory is the DB score) -> whoever, but unanimous
parts = copy.deepcopy(FLEET)
for p in parts:
    if p["name"] == "Seattle5": p["facts"]["capability"]["mem_total_kb"] = 32 * 1024 * 1024
res, ms = elect_everywhere(parts, "cap change")
db5, _ = agree(res, 0)
check("capability change: unanimous (%s)" % db5, db5 is not None, res)

# ---- a member whose facts carry no capability yet (just joined): never elected, never splits the vote
parts = copy.deepcopy(FLEET)
parts.append(member("Newbie", "10.9.9.1", None)); parts[-1]["facts"].pop("capability")
res, ms = elect_everywhere(parts, "newbie")
db6, _ = agree(res, 0)
check("member without a capability fact: ignored, still unanimous", db6 == "10.251.251.1" and "Newbie" in res, res)

# ---- [ELECT_NODES_ONLY_V1] a phone (leaf) at 10.252.0.1 -- the first leaf in every pond, ending in .1 -- and a reaching
#      participant at 10.252.0.3 are in the daemon's alive hosts (its plan names them), but they are not nodes: neither is
#      the control host nor a candidate. (Before: the phone was elected control on every node that saw it.)
parts = copy.deepcopy(FLEET)
parts.append({"name": "phone", "alive": True, "facts": {"role": "leaf", "name": "phone", "pubkey": "p", "addr": "10.252.0.1/32"}})
parts.append({"name": "visitor", "alive": True, "facts": {"role": "participant", "name": "visitor", "reach": True, "pubkey": "v", "addr": "10.252.0.3/32"}})
alive = [(p["facts"]["addrs"][0]["ip"], p["name"]) for p in parts if p["alive"] and p["facts"].get("role") == "node"] + [("10.252.0.1", "phone")]
me = next(p for p in parts if p["facts"].get("role") == "node" and p["alive"])["facts"]["addrs"][0]["ip"]
lines = []
r = elect_now(logger=lines.append, alive_hosts=alive, participants=[HUB] + parts, self_ip=me,
              hosts_path=os.path.join(tempfile.mkdtemp(prefix="hosts-leaf-"), "hosts"), apply=False)
ctl = next((l for l in lines if l.startswith("ELECT: control=")), "")
pool = next((l for l in lines if l.startswith("ELECT: pool=")), "")
check("a leaf at 10.252.0.1 is not the control host", "control=10.252.0.1" not in ctl, ctl)
check("nor in the pool, nor elected", "10.252.0.1" not in pool and r["databasehost"] != "10.252.0.1" and r["mediahost"] != "10.252.0.1", (pool, r["databasehost"], r["mediahost"]))
check("but it is named: '10.252.0.1 phone', and no FrogNetAdmin line claims 10.252.0.2", "10.252.0.1 phone" in r["etc_hosts"] and not any(l.startswith("10.252.0.2 ") for l in r["etc_hosts"]), [l for l in r["etc_hosts"] if l.startswith("10.252.")])

check("the store was never read (calls to T.get counted)", not STORE_READS, STORE_READS[:3])

print("\nRESULT " + ("PASS" if not FAIL else "FAIL %d" % FAIL))
sys.exit(1 if FAIL else 0)
