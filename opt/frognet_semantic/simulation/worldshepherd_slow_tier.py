#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
worldshepherd_slow_tier.py - [WORLDSHEPHERD_SLOW_TIER_V1] WorldShepherd SARA's read-ticket ledger in the simulator, with
every SARA on its own link.

The stores, each run by the same race:
  sql        Brandon Ray Davis's PostgresClaimStore (Sara_pro, branch
             worldshepherd/connectors-distributed-claim-contract-v1-20260913), unmodified, with its four SQL calls
             converted to cells in worldshepherd-ram (ribbit/examples/worldshepherd/ticket_sql.py). No PostgreSQL.
  memory     RibbitClaimStore on worldshepherd-ram: the same cells without the SQL in between.
  postgres   the control, only when WS_PG_DSN names a PostgreSQL: PostgresClaimStore on it, as stock.

What is simulated is the network (slowlink.py): each SARA is a separate process, and everything it says to the store --
its one session to worldshepherd-ram, or its Postgres connections -- goes through that SARA's own shaped link. The
issuer sits beside the store. The race's own instruction and results use a separate, unshaped session, so what a
link carried is the store's traffic and nothing else.

  deployments   lan     every SARA on the LAN (the control: what the relay itself costs)
                wan     every SARA across the WAN (wan-100ms: the 100 ms round trip of NY2 <-> Seattle7)
                4g      every SARA on 4G (40 ms each way +-15, 10 Mb/s)
                mixed   SARAs on lan, wifi, 4g and wan-100ms in turn
  workload      ribbit/examples/worldshepherd/harness/race.py: N SARAs, K tickets, every SARA claims every ticket in
                its own random order, T claims at once (--threads: a SARA serving T requests at a time), started
                together by a held read.
  it reports    for each run: every ticket won exactly once or not (the verdict), claims a second, a claim's median
                and worst 99th-percentile time, connections opened and bytes carried on the SARAs' links per claim.
  it fails      when any run of any store does not have every ticket won exactly once, or does not finish.
  --ramp        find how many claims at once each store carries: --threads is then a rising list; a store that
                does not finish a level (a SARA ends with an error, or no result within --limit seconds) is said to
                have BROKE there, with what it said, and is not run higher. BROKE does not fail a ramp; a ticket
                won twice or never still does. Tickets a run: at least two for every claiming thread.

usage (from opt/frognet_semantic):
  python3 simulation/worldshepherd_slow_tier.py --sara DIR [--deploy lan wan 4g mixed] [--clients 8] [--threads 1 8]
        [--tickets 25] [--ramp] [--limit 600] [--out DIR]
  WS_PG_DSN="host=... dbname=... user=... password=..." in the environment adds the PostgreSQL control.
  DIR (--sara): the Sara_pro branch above, checked out. The example must be built (make in ribbit/examples/worldshepherd).
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import slowlink as SL                         # noqa: E402

EXAMPLE = os.path.normpath(os.path.join(_HERE, "..", "ribbit", "examples", "worldshepherd"))
WHAT = {"postgres": "PostgresClaimStore on PostgreSQL", "sql": "PostgresClaimStore on the memory", "memory": "RibbitClaimStore on the memory"}
DEPLOY = {"lan": ["lan"], "wan": ["wan-100ms"], "4g": ["4g"], "mixed": ["lan", "wifi", "4g", "wan-100ms"]}


def prerequisites(sara, dsn):
    """[WORLDSHEPHERD_TIER_SAYS_WHAT_IS_MISSING_V1] every missing thing, named, before any run"""
    missing = []
    for rel in ("bin/worldshepherd-ram", "lib/libkartram.so", "lib/ribbit.py"):
        if not os.path.exists(os.path.join(EXAMPLE, rel)):
            missing.append(f"{os.path.join(EXAMPLE, rel)} (run make in {EXAMPLE})")
    if not os.path.exists(os.path.join(sara, "worldshepherd_sara", "connector_ticket_postgres.py")):
        missing.append(f"{sara}/worldshepherd_sara/connector_ticket_postgres.py (--sara is not the Sara_pro branch "
                       "worldshepherd/connectors-distributed-claim-contract-v1-20260913)")
    if dsn:                                   # the stock control is run only when a PostgreSQL is named
        try:
            import psycopg2
            psycopg2.connect(dsn, connect_timeout=5).close()
        except ImportError:
            missing.append("the Python module psycopg2 (pip install psycopg2-binary), for the PostgreSQL named in WS_PG_DSN")
        except Exception as e:                # noqa: BLE001 -- named and reported, the tier stops
            missing.append(f"a PostgreSQL that answers at WS_PG_DSN ({str(e).strip().splitlines()[0]})")
    try:
        import fastapi                        # noqa: F401 -- worldshepherd_sara/__init__.py imports it
    except ImportError:
        missing.append("the Python module fastapi (Sara_pro's package imports it)")
    return missing


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def pg_target(dsn):
    host = re.search(r"\bhost=(\S+)", dsn); port = re.search(r"\bport=(\d+)", dsn)
    return (host.group(1) if host else "127.0.0.1", int(port.group(1)) if port else 5432)


def dsn_through(dsn, port):
    rest = re.sub(r"\b(host|port)=\S+\s*", "", dsn).strip()
    return f"host=127.0.0.1 port={port} {rest}"


def carried(relays):
    return (sum(r.stats.get("up_bytes", 0) + r.stats.get("down_bytes", 0) for r in relays),
            sum(r.stats["connections"] for r in relays))


def one_race(store, links, tickets, threads, sara, dsn, out, label, limit_s):
    """one RAM manager, one relay pair per SARA, N claimants, one controller. Returns the controller's result plus
    what the links carried."""
    env = dict(os.environ, KARTRAM_LIB=os.path.join(EXAMPLE, "lib"),
               PYTHONPATH=os.pathsep.join([os.path.join(EXAMPLE, "harness"), EXAMPLE, os.path.join(EXAMPLE, "lib"), sara]))
    race = os.path.join(EXAMPLE, "harness", "race.py")
    port = free_port()
    ram_log = open(os.path.join(out, f"worldshepherd-ram-{label}-{store}.log"), "w")
    ram = subprocess.Popen([os.path.join(EXAMPLE, "bin", "worldshepherd-ram"), "--listen", f"127.0.0.1:{port}"],
                           env=env, stdout=ram_log, stderr=subprocess.STDOUT)
    deadline = time.time() + 10
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close(); break
        except OSError:
            if ram.poll() is not None or time.time() > deadline:
                raise RuntimeError(f"worldshepherd-ram did not start: see {ram_log.name}")
            time.sleep(0.05)
    ram_relays = [SL.Relay(("127.0.0.1", port), SL.profile(p), seed=i, name=f"sara{i}-ram") for i, p in enumerate(links)]
    pg_relays = [SL.Relay(pg_target(dsn), SL.profile(p), seed=1000 + i, name=f"sara{i}-pg") for i, p in enumerate(links)] if store == "postgres" else []
    kids = []
    try:
        for i, p in enumerate(links):
            log = open(os.path.join(out, f"sara{i}-{label}-{store}.log"), "w")
            kids.append((subprocess.Popen([sys.executable, race, "claimant", f"127.0.0.1:{port}", "--store-ram", ram_relays[i].where,
                                           "--name", f"sara{i}-{p}"],
                                          env=dict(env, WS_PG_DSN=dsn_through(dsn, pg_relays[i].port)) if pg_relays else env,
                                          stdout=log, stderr=subprocess.STDOUT), log))
        ctl = subprocess.Popen([sys.executable, race, "controller", f"127.0.0.1:{port}", "--claimants", str(len(links)),
                                "--tickets", str(tickets), "--threads", str(threads), "--store", store, "--label", label],
                               env=dict(env, WS_PG_DSN=dsn) if dsn else env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # the harness watches its own processes: a SARA that ends before the controller has its result broke, and says why
        started = time.time(); broke = None
        while ctl.poll() is None and broke is None:
            for i, (k, log) in enumerate(kids):
                if k.poll() is not None:
                    log.flush(); said = [ln for ln in open(log.name).read().splitlines() if ln.strip() and "DIAG" not in ln]
                    first = [ln for ln in said if "Error" in ln and "a claiming thread ended" not in ln]   # the error itself, not the claimant's report of it
                    broke = f"sara{i} ended: " + ((first or said)[-1 if not first else 0].strip()[:200] if said else f"exit {k.returncode}")
                    break
            if broke is None and time.time() - started > limit_s:
                broke = f"no result in {limit_s:g} s"
            if broke is None:
                time.sleep(0.2)
        if broke is not None:
            ctl.kill(); ctl.communicate()
            return {"verdict": "BROKE", "why": broke, "run": "", "claims": len(links) * tickets}
        out_text, err_text = ctl.communicate()
        line = next((ln for ln in out_text.splitlines() if ln.startswith("{")), None)
        if line is None:
            return {"verdict": "BROKE", "why": "the controller ended with no result: " + (err_text.strip().splitlines() or ["?"])[-1][:200],
                    "run": "", "claims": len(links) * tickets}
        res = json.loads(line)
        res["ram_bytes"], res["ram_connections"] = carried(ram_relays)
        res["pg_bytes"], res["pg_connections"] = carried(pg_relays) if pg_relays else (0, 0)
        return res
    finally:
        subprocess.run([sys.executable, race, "controller", f"127.0.0.1:{port}", "--end"], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        for k, log in kids:
            try:
                k.wait(timeout=30)
            except subprocess.TimeoutExpired:
                k.kill()
            log.close()
        for rl in ram_relays + pg_relays:
            rl.close()
        ram.terminate(); ram.wait(timeout=10); ram_log.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sara", required=True)
    ap.add_argument("--deploy", nargs="+", default=list(DEPLOY), choices=list(DEPLOY))
    ap.add_argument("--clients", nargs="+", type=int, default=[8])
    ap.add_argument("--tickets", type=int, default=25)
    ap.add_argument("--threads", nargs="+", type=int, default=[1, 8])
    ap.add_argument("--out", default=None)
    ap.add_argument("--ramp", action="store_true")
    ap.add_argument("--limit", type=float, default=600.0)
    a = ap.parse_args()
    sara = os.path.abspath(a.sara); dsn = os.environ.get("WS_PG_DSN", "")
    missing = prerequisites(sara, dsn)
    if missing:
        print("WORLDSHEPHERD SLOW TIER - FAIL before any run. Missing:")
        for m in missing:
            print("  - " + m)
        sys.exit(1)
    out = os.path.abspath(a.out) if a.out else tempfile.mkdtemp(prefix="worldshepherd-slow-")
    os.makedirs(out, exist_ok=True)
    version = open(os.path.join(EXAMPLE, "VERSION")).read().strip()
    print(f"WORLDSHEPHERD SLOW TIER - {version}\n  SARA's stores (Sara_pro at {sara}) over simulated links; "
          f"{a.tickets} tickets a run; logs in {out}", flush=True)
    stores = (["postgres"] if dsn else []) + ["sql", "memory"]
    if not dsn:
        print("  WS_PG_DSN is not set: no PostgreSQL control this run (the two stores on the memory are run)", flush=True)
    rows, ok, broke_at = [], True, {}
    for dep in a.deploy:
        for n in a.clients:
            for th in a.threads:
                links = [DEPLOY[dep][i % len(DEPLOY[dep])] for i in range(n)]
                print(f"== {dep}, {n} SARAs, {th} claim(s) at once each ({n * th} in flight): " + "; ".join(f"{p} ({SL.profile(p).label()})" for p in DEPLOY[dep]), flush=True)
                label = f"{dep}-{n}x{th}"
                tickets = max(a.tickets, 2 * th)             # every claiming thread has at least two tickets of its own
                got = {}
                for name in stores:
                    if (dep, name) in broke_at:
                        print(f"  {WHAT[name]:38s} not run: it broke at {broke_at[(dep, name)]} claims at once", flush=True)
                        continue
                    r = one_race(name, links, tickets, th, sara, dsn, out, label, a.limit)
                    row = {"deploy": dep, "saras": n, "threads": th, "in_flight": n * th, "tickets": tickets, "store": name,
                           "verdict": r["verdict"], "run": r["run"]}
                    if r["verdict"] == "BROKE":
                        row["why"] = r["why"]; rows.append(row); broke_at[(dep, name)] = n * th; ok = ok and a.ramp
                        print(f"  {WHAT[name]:38s} BROKE at {n * th} claims at once: {r['why']}", flush=True)
                        continue
                    nbytes, conns = (r["pg_bytes"], r["pg_connections"]) if name == "postgres" else (r["ram_bytes"], r["ram_connections"])
                    row.update({"won_once": r["won_once"], "won_twice": r["won_twice"], "never_won": r["never_won"],
                                "claims_per_s": r["claims_per_s"], "claim_ms_p50": r["claim_ms_p50"],
                                "claim_ms_p99_worst": r["claim_ms_p99_worst"], "slowest_sara_s": r["slowest_claimant_s"],
                                "bytes_per_claim": round(nbytes / r["claims"]), "connections": conns})
                    rows.append(row); got[name] = row; ok = ok and r["verdict"] == "PASS"
                    print(f"  {WHAT[name]:38s} {r['verdict']}  won once {r['won_once']}/{tickets}  {r['claims_per_s']:>8} claims/s  "
                          f"claim p50 {r['claim_ms_p50']:>8} ms  worst p99 {r['claim_ms_p99_worst']:>8} ms  "
                          f"{row['bytes_per_claim']:>6} B/claim  {conns} connection(s)", flush=True)
                if "postgres" in got:
                    for name in ("sql", "memory"):
                        if name in got:
                            print(f"  {name} / postgres: {got[name]['claims_per_s'] / got['postgres']['claims_per_s']:.1f}x the claims a second, "
                                  f"{got['postgres']['claim_ms_p50'] / got[name]['claim_ms_p50']:.1f}x shorter median claim", flush=True)
    path = os.path.join(out, "worldshepherd-slow-tier.jsonl")
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"WORLDSHEPHERD SLOW TIER - {'PASS' if ok else 'FAIL'} ({len(rows)} runs; every ticket won exactly once in "
          f"{sum(1 for r in rows if r['verdict'] == 'PASS')}; broke in {sum(1 for r in rows if r['verdict'] == 'BROKE')}); rows in {path}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
