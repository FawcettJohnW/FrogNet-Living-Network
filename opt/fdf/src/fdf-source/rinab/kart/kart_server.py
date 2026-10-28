#!/usr/bin/env python3
# kart_server.py -- a race server: the race's own memory and the code that runs the race. [KART_RACE_SERVER_V1]
# A game in the lobby starts it on its own machine when the winning start intent names it host (DESIGN.md). It:
#   1 starts a rina-host on a port the system picks (the race memory), on this machine
#   2 reads the racers' records from the main memory and writes the race into its memory: meta, grid, records
#   3 only then writes (kart, server, <race>) into the main memory -- its own tuple, gone with its session
#   4 follows the karts' truths in its memory and keeps each kart's lap times
#   5 when every kart has finished or is gone (or it is told to stop: SIGTERM/SIGINT), writes the result and each
#     racer's updated record into the main memory as PERMANENT tuples, writes its server tuple {}, stops its memory,
#     and exits. Killed outright (-9), its server tuple goes with its session and nothing is written: gone is gone.
#
#   kart_server.py --main H:P --main-api API --intent JSON --advertise ADDR --rina-host PATH [--max-s S]
# Everything it does is said on stdout, one line each, "kart_server: ...".
import argparse, json, math, os, signal, subprocess, sys, tempfile, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ribbit import Memory, RibbitError

ap = argparse.ArgumentParser()
ap.add_argument("--main", required=True); ap.add_argument("--main-api", default="/ram.php")
ap.add_argument("--intent", required=True, help="the winning start intent: {race, host, roster:[{name,bots}], laps}")
ap.add_argument("--advertise", required=True, help="the address the racers reach this machine at")
ap.add_argument("--rina-host", required=True); ap.add_argument("--max-s", type=float, default=600.0)
a = ap.parse_args()
I = json.loads(a.intent); RACE = I["race"]; LAPS = int(I["laps"])
PLAYERS = [p["name"] for p in I["roster"]]
KARTS = []                                    # (kart, owning player, robot)
for p in I["roster"]:
    KARTS.append((p["name"], p["name"], False))
    KARTS += [(f'{p["name"]}-bot{k+1}', p["name"], True) for k in range(int(p.get("bots", 0)))]
def say(m): sys.stdout.write(f"kart_server: [{RACE}] {m}\n"); sys.stdout.flush()
def hp(s): h, p = s.rsplit(":", 1); return h, int(p)

# 1 the race memory, on a port the system picks; it ends with this program
d = tempfile.mkdtemp(prefix="kart-race-")
def die_with_me():                             # Linux: the race memory gets SIGTERM if this program goes, however
    import ctypes; ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)
env = dict(os.environ, RINA_REGION_FILE=os.path.join(d, "race.perm")); env.pop("RINA_MAP_AREA", None)
host = subprocess.Popen([a.rina_host, "--listen", "0.0.0.0:0", "--api", "/ram.php"], env=env, stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE, text=True, preexec_fn=die_with_me)
port = None
for line in host.stderr:                       # [BOUND_PORT_SAID_V1] "... listening on 0.0.0.0:<port>"
    if "[FDF_VERSION_V1]" in line and "listening on" in line: port = int(line.rsplit(":", 1)[1]); break
if not port: sys.exit(f"kart_server: [{RACE}] the race memory did not start ({a.rina_host})")
threading.Thread(target=lambda: [None for _ in host.stderr], daemon=True).start()   # keep its stderr drained
say(f"race memory on port {port} ({a.rina_host})")

# 2 fill it from the main memory
mh, mp = hp(a.main)
try: main = Memory(mh, mp, api=a.main_api)
except RibbitError as e: host.terminate(); sys.exit(f"kart_server: [{RACE}] cannot reach the main memory: {e}")
rec = {c["instance"]: c["bag"] for c in main.read("kart", "record") if c["bag"]}
race = Memory("127.0.0.1", port)
def best(p): return rec.get(p, {}).get("best_lap") or 1e9
order = sorted(KARTS, key=lambda k: (best(k[1]), k[1], k[2], k[0]))     # fastest player's karts at the front
start_at = time.time() + 5.0                   # the karts hold their slots until then
for slot, (k, owner, robot) in enumerate(order):
    race.write("race", "grid", k, {"slot": slot, "t0": -math.pi/2 - 0.12*(slot//2), "lane": 22 if slot % 2 else -22,
                                   "owner": owner, "robot": robot})
for p in PLAYERS: race.write("race", "record", p, rec.get(p, {"races": 0, "wins": 0, "best_lap": None, "laps": 0}))
race.write_landed("race", "meta", "info", {"race": RACE, "laps": LAPS, "players": PLAYERS, "karts": [k[0] for k in KARTS],
                                           "start_at": start_at, "host": I["host"]})
say(f"race memory filled: {len(KARTS)} karts on the grid, {len(rec)} record(s) read from the main memory")

# 3 tell everyone
server = {"race": RACE, "addr": a.advertise, "port": port, "api": "/ram.php", "players": PLAYERS,
          "karts": [k[0] for k in KARTS], "laps": LAPS, "start_at": start_at, "host": I["host"]}
main.write("kart", "server", RACE, server, own=True)
say(f"running: {a.advertise}:{port}, written to the main memory as (kart, server, {RACE})")

# 4 the race, from the karts' own truths
laps = {k[0]: 0 for k in KARTS}; lap_at = {k[0]: [] for k in KARTS}; gone = set(); seen = set()
stop = threading.Event(); why = {"r": ""}
def follower():
    try:
        for cells in race.follow("race", "kart"):
            now = time.time()
            for c in cells:
                k = c["instance"]
                if k not in laps: continue
                if not c["bag"]: gone.add(k); continue
                seen.add(k); n = int(c["bag"].get("lap", 0))
                while laps[k] < n: laps[k] += 1; lap_at[k].append(now)       # a lap completed, as the kart says
            done = all(laps[k] >= LAPS or k in gone for k in laps)
            if done and not stop.is_set(): why["r"] = "every kart finished or is gone"; stop.set()
    except RibbitError as e:
        if not stop.is_set(): why["r"] = f"its race memory failed: {e}"; stop.set()
threading.Thread(target=follower, daemon=True).start()
def on_signal(sig, _): why["r"] = f"told to stop ({signal.Signals(sig).name})"; stop.set()
signal.signal(signal.SIGTERM, on_signal); signal.signal(signal.SIGINT, on_signal)
while not stop.wait(1.0):
    if time.time() - start_at > a.max_s: why["r"] = f"the race's time limit ({a.max_s:.0f} s)"; stop.set()
    elif time.time() - start_at > 20 and not seen: why["r"] = "no kart ever raced"; stop.set()
say(f"race over: {why['r']}")

# 5 write back, permanently, then go
def race_time(k): return (lap_at[k][-1] - start_at) if laps[k] >= LAPS else None
def best_lap(k):
    t = [start_at] + lap_at[k]; return min((t[j+1]-t[j] for j in range(len(t)-1)), default=None)
fin = sorted([k for k in laps if laps[k] >= LAPS], key=race_time)
rest = sorted([k for k in laps if laps[k] < LAPS], key=lambda k: (-laps[k], lap_at[k][-1] if lap_at[k] else 1e18))
standing = [{"kart": k, "player": next(o for kk, o, _ in KARTS if kk == k), "laps": laps[k],
             "time": round(race_time(k), 2) if race_time(k) else None,
             "best_lap": round(best_lap(k), 2) if best_lap(k) else None} for k in fin + rest]
result = {"race": RACE, "host": I["host"], "laps": LAPS, "players": PLAYERS, "finished_at": time.time(),
          "why": why["r"], "order": standing}
try:
    main.write_landed("kart", "result", RACE, result, permanent=True)
    for p in PLAYERS:
        r = dict(rec.get(p, {"races": 0, "wins": 0, "best_lap": None, "laps": 0}))
        place = next(i + 1 for i, s in enumerate(standing) if s["kart"] == p)
        mine = next(s for s in standing if s["kart"] == p)
        r["races"] = r.get("races", 0) + 1; r["laps"] = r.get("laps", 0) + mine["laps"]
        if place == 1 and mine["laps"] >= LAPS: r["wins"] = r.get("wins", 0) + 1
        if mine["best_lap"] and (not r.get("best_lap") or mine["best_lap"] < r["best_lap"]): r["best_lap"] = mine["best_lap"]
        r["last_race"] = RACE; r["last_place"] = place; r["of"] = len(standing)
        main.write_landed("kart", "record", p, r, permanent=True)
    race.write_landed("race", "meta", "info", {})     # the race says it is over, in its own memory, first
    main.write_landed("kart", "server", RACE, {})
    say(f"written back: the result ({len(fin)} of {len(standing)} finished) and {len(PLAYERS)} record(s), permanent")
except RibbitError as e:
    say(f"could NOT write back to the main memory: {e}")
host.terminate(); host.wait()
say("race memory stopped; exiting")
