#!/usr/bin/env python3
# kart_service.py -- the kart service: where the lobby's intelligence is. [KART_SERVICE_V1]  (DESIGN.md: the rules)
# It runs beside the kart memory (the kart region everyone connects to first) as a participant of it: it reads the
# players' truths (lobby, chat) and the game-serving machines' (gamehost, hosting, server), and writes its decisions:
#   (kart, agreed, lobby)   the agreed parameters and the lobby's state, when either changes
#   (kart, launch, <race>)  this race, these players, these parameters, on this machine -- {} once its server is up
#   (kart, chat, kart-service#n)  what it did, said where the players are talking
# It asks no one anything and waits for no one: each decision is made on the answer to a held read.
#
#   kart_service.py --memory H:P [--api /ram.php] [--min-players 2]
import argparse, collections, json, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kartmem import Region, say, hp

ap = argparse.ArgumentParser()
ap.add_argument("--memory", required=True, help="the kart memory, host:port"); ap.add_argument("--api", default="/ram.php")
ap.add_argument("--min-players", type=int, default=2, help="a game launches by itself when this many free players are all ready")
a = ap.parse_args()
def log(m): say("kart_service", m)
H, P = hp(a.memory)
lock = threading.Lock()
st = {"agreed": None, "chat_n": 0, "seq": 0, "tried": collections.defaultdict(list), "started_seen": set(), "up_said": set(),
      "winner_said": set(), "blocked": None}   # blocked: the ready set whose launch failed everywhere -- not again until it changes
mem = None

def chat(text):
    st["chat_n"] += 1
    mem.write("chat", f"kart-service#{st['chat_n'] % 40}", {"who": "kart service", "text": text, "at": time.time()}, own=False)
    log(f"chat: {text}")

def choose_host(race, servers, launches, hosts):
    """[KART_SERVICE_V1] rule 3: a dedicated game-serving machine if one is up, else a player machine that can host;
    the one serving the fewest races, then by name; never one this race already tried."""
    load = collections.Counter([b.get("host") for b in servers.values()] + [b.get("host") for r, b in launches.items() if r != race])
    c = [(0 if b.get("kind") == "dedicated" else 1, load[m], m) for m, b in hosts.items() if m not in st["tried"][race]]
    return min(c)[2] if c else None

def evaluate():
    with lock:
        lob = mem.family("lobby"); servers = mem.family("server"); hosts = mem.family("gamehost")
        launches = mem.family("launch"); hosting = mem.family("hosting"); results = mem.family("result")
        racing = {p for b in servers.values() for p in b.get("players", [])} | {r["name"] for b in launches.values() for r in b.get("roster", [])}
        free = sorted(n for n in lob if n not in racing)
        want = [int(lob[n].get("laps", 3)) for n in free]
        cnt = collections.Counter(want)
        laps = min(cnt, key=lambda v: (-cnt[v], v)) if want else (st["agreed"] or {}).get("laps", 3)   # nobody free: as agreed
        ready = [n for n in free if lob[n].get("ready")]
        # rule 4: the launches this service has made
        for race, lb in launches.items():
            if race in servers:                                    # its server is up: the launch is done
                mem.write("launch", race, {})
                if race not in st["up_said"]:
                    st["up_said"].add(race); chat(f"race {race} is up on {servers[race].get('host')}: {', '.join(servers[race].get('players', []))}")
                continue
            h = hosting.get(race)
            if h and h.get("machine") == lb["host"] and h.get("state") == "starting": st["started_seen"].add((race, lb["host"])); continue
            failed = (h and h.get("machine") == lb["host"] and h.get("state") == "failed") or \
                     ((race, lb["host"]) in st["started_seen"] and not h) or lb["host"] not in hosts
            if failed:
                why = (h or {}).get("why") or ("its host agent is gone" if lb["host"] not in hosts else "its host agent's session ended")
                st["tried"][race].append(lb["host"]); nxt = choose_host(race, servers, launches, hosts)
                if nxt:
                    chat(f"race {race}: {lb['host']} could not serve it ({why}); trying {nxt}")
                    mem.write("launch", race, dict(lb, host=nxt, kind=hosts[nxt].get("kind")))
                else:
                    chat(f"race {race}: {lb['host']} could not serve it ({why}), and no other machine can")
                    mem.write("launch", race, {}); st["blocked"] = frozenset(r["name"] for r in lb.get("roster", []))
        # rule 5: say who won each race the first time its result is seen
        for race, r in results.items():
            if race not in st["winner_said"]:
                st["winner_said"].add(race)
                if st["agreed"] is not None and r.get("order"): chat(f"race {race} won by {r['order'][0]['kart']}")
        # rules 2 and 3: launch?
        pressed = [n for n in ready if lob[n].get("start")]
        go = (len(free) >= a.min_players and len(ready) == len(free)) or bool(pressed)
        state = f"waiting: {len(ready)} of {len(free)} free players ready (a game launches when all are, and at least {a.min_players})"
        if st["blocked"] is not None and st["blocked"] != frozenset(ready): st["blocked"] = None
        if go and st["blocked"] is not None:
            state = "the last launch failed on every machine: it waits for the lobby to change (a player readies, unreadies, joins or leaves)"
        elif go and not launches:
            st["seq"] += 1; race = f"{int(time.time()*1000):013d}-{st['seq']}"
            host = choose_host(race, servers, launches, hosts)
            if host:
                roster = [{"name": n, "bots": int(lob[n].get("bots", 0))} for n in ready]
                mem.write("launch", race, {"race": race, "host": host, "kind": hosts[host].get("kind"), "roster": roster, "laps": laps,
                                           "at": time.time()})
                chat(f"launching race {race}: {', '.join(ready)}, {laps} lap(s), on {host} ({hosts[host].get('kind')})" +
                     (f" -- {pressed[0]} pressed Start" if pressed else ""))
                state = f"launching race {race} on {host}"
            else:
                state = "ready, but no machine can serve the game (none has said it can)"
        elif launches:
            state = "launching " + ", ".join(f"{r} on {b['host']}" for r, b in launches.items())
        agreed = {"laps": laps, "min_players": a.min_players, "state": state, "free": free, "ready": ready,
                  "hosts": {m: b.get("kind") for m, b in hosts.items()}}
        if agreed != st["agreed"]:
            mem.write("agreed", "lobby", agreed); st["agreed"] = agreed; log(f"agreed: {laps} lap(s); {state}")

mem = Region("kart memory", H, P, a.api, "kart", log, on_change=lambda: evaluate())
mem.up.wait()
log(f"the kart service, on the kart memory {H}:{P}{a.api}; a game launches at {a.min_players} ready players, or on Start")
chat("the kart service is up")
evaluate()
try:
    while True: time.sleep(3600)
except KeyboardInterrupt:
    log("stopped")
