#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""acceptance_sara.py -- one SARA of the acceptance study (simulation/worldshepherd_acceptance.py). [WS_ACCEPTANCE_V1]

    acceptance_sara.py TEST_RAM --store-ram HOST:PORT --name NAME [--store memory|sql]

It is a SARA and nothing else: one store (the SharedClaimStore contract: register, claim, status) on WorldShepherd's
RAM, reached by this SARA's own link (--store-ram). What it is asked to do and what happened are cells in a second,
separate memory (TEST_RAM, service ws.accept), which the study never stops:

    (ws.accept, can, NAME)          {"who", "pid", "build"}                       this SARA's own, gone with its session
    (ws.accept, do.NAME, <n>)       {"calls": [[op, ticket, sha, exp], ...], "threads": T} or {"end": 1}    the study's
    (ws.accept, done.NAME, <n>)     {"r": [[op, ticket, ok, reason, started, ended, error], ...], "mine": N}   this SARA's

It holds one read on its own do family; each cell written there is done and answered in order. Nothing is polled.
op: issue (register), claim, status. started and ended are this machine's clock (time.time()) around the one call
on the store: a call the store holds while the memory is gone is held here, and its times show for how long.
"""
import argparse
import faulthandler
import hashlib
import os
import signal
import sys
import threading
import time

import ribbit
import race                       # open_memory, make_store: the race's, so a SARA here is made as a SARA there

S = "ws.accept"
BUILD = "ws-acceptance-sara 2026-11-25a [WS_ACCEPTANCE_V1]"


def sha_of(ticket):
    return hashlib.sha256(ticket.encode()).hexdigest()


def one(store, call):
    op, ticket, sha, exp = (list(call) + [None, None])[:4]
    ok, reason, err = None, "", ""
    t0 = time.time()
    try:
        if op == "issue":
            store.register(ticket_id=ticket, ticket_sha256=sha, expires_at=exp); ok = True
        elif op == "claim":
            c = store.claim(ticket_id=ticket, ticket_sha256=sha, now=time.time()); ok, reason = bool(c.ok), c.reason
        elif op == "status":
            s = store.status(ticket)
            known = s.get("known") if isinstance(s, dict) else getattr(s, "known", None)
            consumed = (s.get("consumed") if isinstance(s, dict) else getattr(s, "consumed", None))
            ok, reason = bool(known), "consumed" if consumed else ("known" if known else "unknown")
        else:
            err = "no op called %r" % op
    except Exception as e:                    # the store raises by name; the study is told which and counts it
        err = "%s: %s" % (type(e).__name__, str(e)[:240])
    return [op, ticket, ok, reason, round(t0, 4), round(time.time(), 4), err]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("ram"); p.add_argument("--store-ram", required=True); p.add_argument("--name", required=True)
    p.add_argument("--store", default="memory")
    a = p.parse_args()
    faulthandler.register(signal.SIGUSR1, all_threads=True)      # the study asks a SARA that does not answer where every thread is
    memory = race.open_memory(a.ram)
    store = race.make_store(a.store, a.store_ram, a.name)
    who = getattr(store, "who", a.name)
    last = max([c["id"] for c in memory.read(S, "do." + a.name)] + [0])
    memory.write(S, "can", a.name, {"who": who, "pid": os.getpid(), "build": BUILD}, own=True)
    print("[accept] %s (%s): %s, store %s at %s; holding a read on (%s, do.%s)" % (a.name, who, BUILD, a.store, a.store_ram, S, a.name), flush=True)
    while True:
        cells = memory.read(S, "do." + a.name, "", after=last, wait_s=ribbit.HOLD_S)
        for do in sorted(cells, key=lambda c: c["id"]):
            last = max(last, do["id"]); g = do["bag"]
            if not g:
                continue
            if g.get("end"):
                print("[accept] %s: told to end" % a.name, flush=True); memory.close(); return 0
            calls, threads = g["calls"], max(1, int(g.get("threads", 1)))
            slots = [[] for _ in range(threads)]

            def share(mine, out):
                for c in mine:
                    out.append(one(store, c))
            workers = [threading.Thread(target=share, args=(calls[k::threads], slots[k])) for k in range(threads)]
            for w in workers: w.start()
            for w in workers: w.join()
            rows = [r for s_ in slots for r in s_]
            memory.write(S, "done." + a.name, do["instance"], {"r": rows, "who": who, "mine": len(getattr(store, "_mine", ()))})
            print("[accept] %s: %s: %d call(s), %d raised, slowest %.3f s" % (a.name, do["instance"], len(rows), sum(1 for r in rows if r[6]),
                                                                              max([r[5] - r[4] for r in rows] + [0])), flush=True)


if __name__ == "__main__":
    sys.exit(main())
