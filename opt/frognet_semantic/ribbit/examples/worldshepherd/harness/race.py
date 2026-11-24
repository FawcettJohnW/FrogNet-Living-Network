# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# race.py -- many SARAs, on as many machines as you like, claiming the same tickets at the same moment.
# Everything goes through the memory: nobody is told another machine's name, and nothing polls.
#
#   on every machine that takes part (any number of times per machine):
#       python3 race.py claimant  RAM_HOST:PORT
#   on one machine, once the claimants are up:
#       python3 race.py controller RAM_HOST:PORT --claimants N --tickets K --store memory|sql|postgres [--threads T] [--label TEXT]
#
#   a claimant   writes (ws.race, can, <who>) = what it is, then holds a read on (ws.race, go): the controller's
#                instruction. When one is written it claims every ticket named, in its own random order (on T threads
#                when the go says so: a SARA serving T requests at once, on its one session), through the
#                store named, and writes (ws.race, result.<run>, <who>) = what it won and how long it took. Then it
#                holds the read again. It ends when a go says {"end": true}.
#   the controller  holds a read on (ws.race, can) until N claimants are there, registers K tickets (it is the
#                issuer), writes the go, holds a read on (ws.race, result.<run>) until N results are there, and says
#                whether every ticket was won exactly once, and the claims per second.
#
# RAM_HOST:PORT is worldshepherd-ram. --store memory: RibbitClaimStore, the tickets are its cells
# --store sql      Brandon's PostgresClaimStore, unmodified, its four SQL calls converted to cells (ticket_sql.py):
#                  no PostgreSQL anywhere
# claimant --store-ram HOST:PORT: the address this SARA reaches the store's RAM manager at, when it is not the one
#                  the race itself uses (the simulator gives each SARA its own shaped link to the store)
# --store postgres Brandon's PostgresClaimStore, unmodified: each claimant and the controller connect to the Postgres
#                  named by their own WS_PG_DSN (never written to the memory). The go still comes through the memory.
import argparse, hashlib, json, os, random, secrets, sys, threading, time

import ribbit
from ribbit_claim_store import BUILD, RibbitClaimStore

from ribbit_claim_store import API
S = "ws.race"


def open_memory(hostport):
    host, port = hostport.rsplit(":", 1)
    return ribbit.Memory(host, int(port), API)


def make_store(kind, where, who):
    if kind == "memory":
        return RibbitClaimStore(where, name=who)
    if kind == "sql":
        import ticket_sql
        from worldshepherd_sara.connector_ticket_postgres import PostgresClaimStore
        return PostgresClaimStore(ticket_sql.connection_factory(where, name=who))
    if kind == "postgres":
        import psycopg2
        from worldshepherd_sara.connector_ticket_postgres import PostgresClaimStore
        dsn = os.environ.get("WS_PG_DSN")
        if not dsn:
            raise RuntimeError("race: --store postgres needs WS_PG_DSN in this process's environment")
        return PostgresClaimStore(lambda: psycopg2.connect(dsn))
    raise RuntimeError("race: no store called %r" % kind)


def ticket_ids(run, count):
    """the run's tickets: derived from the run's name by everyone, so the go carries a count and not a list"""
    return ["%s-%04d-%s" % (run, k, hashlib.sha256(("%s:%d" % (run, k)).encode()).hexdigest()[:24]) for k in range(count)]


def held(memory, variable, last):
    """the family's cells written after `last`, held until there are some"""
    while True:
        cells = memory.read(S, variable, "", after=last, wait_s=ribbit.HOLD_S)
        if cells:
            return cells


def claimant(a):
    memory = open_memory(a.ram)
    store_where = a.store_ram or a.ram                                      # the SARA's own way to the store
    who = "%s-%d-%s" % (a.name, os.getpid(), secrets.token_hex(3))
    memory.write(S, "can", who, {"build": BUILD, "pid": os.getpid()}, own=True)
    print("[race] claimant %s: %s, RAM manager %s; holding a read on the go" % (who, BUILD, a.ram), flush=True)
    last = max([c["id"] for c in memory.read(S, "go")] + [0])
    stores = {}
    while True:
        for go in sorted(held(memory, "go", last), key=lambda c: c["id"]):
            last = max(last, go["id"]); g = go["bag"]
            if g.get("end"):
                print("[race] claimant %s: told to end" % who, flush=True); memory.close(); return
            store = stores.get(g["store"]) or stores.setdefault(g["store"], make_store(g["store"], store_where, who))
            tickets = ticket_ids(g["run"], g["tickets"]); random.shuffle(tickets)
            threads = int(g.get("threads", 1))

            def claim_these(mine, out):                # one thread's share; what it finds goes in its own slot
                won, reasons, each = [], {}, []
                for t in mine:
                    t1 = time.perf_counter()
                    c = store.claim(ticket_id=t, ticket_sha256=hashlib.sha256(t.encode()).hexdigest(), now=g["now"])
                    each.append(time.perf_counter() - t1)
                    if c.ok: won.append(t)
                    else: reasons[c.reason] = reasons.get(c.reason, 0) + 1
                out.append((won, reasons, each))
            slots = [[] for _ in range(threads)]
            workers = [threading.Thread(target=claim_these, args=(tickets[k::threads], slots[k])) for k in range(threads)]
            t0 = time.perf_counter()
            for w in workers: w.start()
            for w in workers: w.join()
            dt = time.perf_counter() - t0
            if any(not s_ for s_ in slots):
                raise RuntimeError("race: a claiming thread ended without a result (its error is above)")
            won, reasons, each = [], {}, []
            for s_ in slots:
                won += s_[0][0]; each += s_[0][2]
                for k, v in s_[0][1].items(): reasons[k] = reasons.get(k, 0) + v
            each.sort()
            for t in won:                                      # each win is its own cell, in the winner's own space
                memory.write(S, "won." + g["run"], t + "|" + who, {"t": t})
            memory.write(S, "result." + g["run"], who, {"won": len(won), "lost": reasons, "claims": len(tickets), "s": dt,
                          "p50_ms": round(1000 * each[len(each) // 2], 2), "p99_ms": round(1000 * each[min(len(each) - 1, (99 * len(each)) // 100)], 2)})
            print("[race] claimant %s: run %s (%s): %d claims in %.3f s, won %d" % (who, g["run"], g["store"], len(tickets), dt, len(won)), flush=True)


def controller(a):
    memory = open_memory(a.ram)
    if a.end:
        memory.write_landed(S, "go", "end-" + secrets.token_hex(3), {"end": True}); print("[race] end written"); return 0
    cans, last = {}, 0
    for c in memory.read(S, "can"):
        last = max(last, c["id"])
        if c["bag"]: cans[c["instance"]] = c["bag"]
    while len(cans) < a.claimants:
        for c in held(memory, "can", last):
            last = max(last, c["id"])
            if c["bag"]: cans[c["instance"]] = c["bag"]
            else: cans.pop(c["instance"], None)
    builds = sorted({b.get("build") for b in cans.values()})
    run = secrets.token_hex(4); now = time.time()
    store = make_store(a.store, a.ram, "issuer")
    if a.store == "postgres": store.initialize()
    tickets = ticket_ids(run, a.tickets)
    t0 = time.perf_counter()
    for t in tickets:
        store.register(ticket_id=t, ticket_sha256=hashlib.sha256(t.encode()).hexdigest(), expires_at=now + 300)
    reg_s = time.perf_counter() - t0
    memory.write(S, "go", run, {"run": run, "store": a.store, "tickets": len(tickets), "now": now + 1, "threads": a.threads})
    results, last = {}, 0
    while len(results) < len(cans):
        for c in held(memory, "result." + run, last):
            last = max(last, c["id"]); results[c["instance"]] = c["bag"]
    wins = {}
    for c in memory.read(S, "won." + run):                  # read after every result: each claimant wrote its wins first
        t, who = c["instance"].split("|", 1); wins.setdefault(t, []).append(who)
    said = sum(r["won"] for r in results.values())
    if said != sum(len(w) for w in wins.values()):
        raise RuntimeError("race: the claimants say %d wins and the memory holds %d win cells" % (said, sum(len(w) for w in wins.values())))
    never = [t for t in tickets if t not in wins]; twice = {t: w for t, w in wins.items() if len(w) > 1}
    consumed = sum(1 for t in tickets if store.status(t).get("consumed"))
    total = sum(r["claims"] for r in results.values()); slowest = max(r["s"] for r in results.values())
    ok = not never and not twice and consumed == len(tickets)
    out = {"label": a.label, "store": a.store, "run": run, "claimants": len(results), "threads": a.threads, "tickets": len(tickets),
           "claims": total, "won_once": len(tickets) - len(never) - len(twice), "won_twice": len(twice),
           "never_won": len(never), "store_says_consumed": consumed, "register_per_s": round(len(tickets) / reg_s, 1),
           "claims_per_s": round(total / slowest, 1), "slowest_claimant_s": round(slowest, 3),
           "claim_ms_p50": sorted(r["p50_ms"] for r in results.values())[len(results) // 2],
           "claim_ms_p99_worst": max(r["p99_ms"] for r in results.values()), "builds": builds,
           "verdict": "PASS" if ok else "FAIL"}
    print(json.dumps(out)); return 0 if ok else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("role", choices=["claimant", "controller"]); p.add_argument("ram")
    p.add_argument("--name", default="sara"); p.add_argument("--claimants", type=int, default=2)
    p.add_argument("--tickets", type=int, default=200); p.add_argument("--store", default="memory")
    p.add_argument("--label", default=""); p.add_argument("--store-ram", default=""); p.add_argument("--threads", type=int, default=1); p.add_argument("--end", action="store_true")
    a = p.parse_args()
    sys.exit(claimant(a) if a.role == "claimant" else controller(a))
