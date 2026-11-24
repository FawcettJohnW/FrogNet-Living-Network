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
worldshepherd_slow_tier.py - [WORLDSHEPHERD_SLOW_TIER_V1] WorldShepherd SARA's read-ticket ledger on the memory against
SARA's own Postgres store, in the simulator, with every SARA on its own link.

Both systems run here unmodified: Brandon Ray Davis's PostgresClaimStore (Sara_pro, branch
worldshepherd/connectors-distributed-claim-contract-v1-20260913) on a real PostgreSQL, and RibbitClaimStore on
worldshepherd-ram (ribbit/examples/worldshepherd). What is simulated is the network (slowlink.py): each SARA is a
separate process, and everything it says to the store -- its Postgres connections, or its one session to
worldshepherd-ram -- goes through that SARA's own shaped link. The issuer sits beside the store.

  deployments   lan     every SARA on the LAN (the control: what the relay itself costs)
                wan     every SARA across the WAN (wan-100ms: the 100 ms round trip of NY2 <-> Seattle7)
                4g      every SARA on 4G (40 ms each way +-15, 10 Mb/s)
                mixed   SARAs on lan, wifi, 4g and wan-100ms in turn
  workload      ribbit/examples/worldshepherd/harness/race.py, the same driver for both: N SARAs, K tickets, every SARA
                claims every ticket in its own random order, T claims at once (--threads: a SARA serving T requests
                at a time), started together by a held read. The instruction and the
                results go through the memory for both systems (each SARA's session, on its link).
  it reports    for each run: every ticket won exactly once or not (the verdict), claims a second, a claim's median
                and worst 99th-percentile time, connections opened and bytes carried on the SARAs' links per claim.
                For Postgres the bytes are its connections'. For the memory they are the session's, less what the same
                race put on the session when the store was Postgres (the instruction and the results).
  it fails      when any run of either system does not have every ticket won exactly once.

usage (from opt/frognet_semantic):
  WS_PG_DSN="host=127.0.0.1 dbname=ws user=ws password=..." python3 simulation/worldshepherd_slow_tier.py --sara DIR
        [--deploy lan wan 4g mixed] [--clients 8] [--threads 1 8] [--tickets 25] [--out DIR]
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
    if not dsn:
        missing.append("WS_PG_DSN in the environment (the PostgreSQL the stock store uses)")
    else:
        try:
            import psycopg2
            psycopg2.connect(dsn, connect_timeout=5).close()
        except ImportError:
            missing.append("the Python module psycopg2 (pip install psycopg2-binary)")
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


def one_race(store, links, tickets, threads, sara, dsn, out, label):
    """one RAM manager, one relay pair per SARA, N claimants, one controller. Returns the controller's result plus
    what the links carried."""
    env = dict(os.environ, KARTRAM_LIB=os.path.join(EXAMPLE, "lib"),
               PYTHONPATH=os.pathsep.join([os.path.join(EXAMPLE, "harness"), EXAMPLE, os.path.join(EXAMPLE, "lib"), sara]))
    race = os.path.join(EXAMPLE, "harness", "race.py")
    port = free_port(); filed = os.path.join(out, f"tickets-{label}-{store}.log")
    ram_log = open(os.path.join(out, f"worldshepherd-ram-{label}-{store}.log"), "w")
    ram = subprocess.Popen([os.path.join(EXAMPLE, "bin", "worldshepherd-ram"), "--listen", f"127.0.0.1:{port}"],
                           env=dict(env, WORLDSHEPHERD_RAM_FILE=filed), stdout=ram_log, stderr=subprocess.STDOUT)
    deadline = time.time() + 10
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close(); break
        except OSError:
            if ram.poll() is not None or time.time() > deadline:
                raise RuntimeError(f"worldshepherd-ram did not start: see {ram_log.name}")
            time.sleep(0.05)
    ram_relays = [SL.Relay(("127.0.0.1", port), SL.profile(p), seed=i, name=f"sara{i}-ram") for i, p in enumerate(links)]
    pg_relays = [SL.Relay(pg_target(dsn), SL.profile(p), seed=1000 + i, name=f"sara{i}-pg") for i, p in enumerate(links)]
    kids = []
    try:
        for i, p in enumerate(links):
            log = open(os.path.join(out, f"sara{i}-{label}-{store}.log"), "w")
            kids.append((subprocess.Popen([sys.executable, race, "claimant", ram_relays[i].where, "--name", f"sara{i}-{p}"],
                                          env=dict(env, WS_PG_DSN=dsn_through(dsn, pg_relays[i].port)),
                                          stdout=log, stderr=subprocess.STDOUT), log))
        r = subprocess.run([sys.executable, race, "controller", f"127.0.0.1:{port}", "--claimants", str(len(links)),
                            "--tickets", str(tickets), "--threads", str(threads), "--store", store, "--label", label],
                           env=dict(env, WS_PG_DSN=dsn), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = next((ln for ln in r.stdout.splitlines() if ln.startswith("{")), None)
        if line is None:
            raise RuntimeError(f"the controller gave no result ({label}, {store}): {r.stderr.strip()[-600:]}")
        res = json.loads(line)
        res["ram_bytes"], res["ram_connections"] = carried(ram_relays)
        res["pg_bytes"], res["pg_connections"] = carried(pg_relays)
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
    print(f"WORLDSHEPHERD SLOW TIER - {version}\n  against PostgresClaimStore (Sara_pro at {sara}) over simulated links; "
          f"{a.tickets} tickets a run; logs in {out}", flush=True)
    rows, ok = [], True
    for dep in a.deploy:
        for n in a.clients:
            for th in a.threads:
                links = [DEPLOY[dep][i % len(DEPLOY[dep])] for i in range(n)]
                print(f"== {dep}, {n} SARAs, {th} claim(s) at once each: " + "; ".join(f"{p} ({SL.profile(p).label()})" for p in DEPLOY[dep]), flush=True)
                label = f"{dep}-{n}x{th}"
                pg = one_race("postgres", links, a.tickets, th, sara, dsn, out, label)
                mem = one_race("memory", links, a.tickets, th, sara, dsn, out, label)
                claims = pg["claims"]
                # what the race itself put on the SARAs' sessions (the instruction, the results), seen when the store was Postgres
                mem_store_bytes = max(0, mem["ram_bytes"] - pg["ram_bytes"])
                for name, r, nbytes, conns in (("postgres", pg, pg["pg_bytes"], pg["pg_connections"]),
                                               ("memory", mem, mem_store_bytes, mem["ram_connections"])):
                    row = {"deploy": dep, "saras": n, "threads": th, "tickets": a.tickets, "store": name, "verdict": r["verdict"],
                           "won_once": r["won_once"], "won_twice": r["won_twice"], "never_won": r["never_won"],
                           "claims_per_s": r["claims_per_s"], "claim_ms_p50": r["claim_ms_p50"],
                           "claim_ms_p99_worst": r["claim_ms_p99_worst"], "slowest_sara_s": r["slowest_claimant_s"],
                           "bytes_per_claim": round(nbytes / claims), "connections": conns, "run": r["run"]}
                    rows.append(row); ok = ok and r["verdict"] == "PASS"
                    print(f"  {name:9s} {r['verdict']}  won once {r['won_once']}/{a.tickets}  {r['claims_per_s']:>8} claims/s  "
                          f"claim p50 {r['claim_ms_p50']:>8} ms  worst p99 {r['claim_ms_p99_worst']:>8} ms  "
                          f"{row['bytes_per_claim']:>6} B/claim  {conns} connection(s)", flush=True)
                print(f"  memory / postgres: {mem['claims_per_s'] / pg['claims_per_s']:.1f}x the claims a second, "
                      f"{pg['claim_ms_p50'] / mem['claim_ms_p50']:.1f}x shorter median claim", flush=True)
    path = os.path.join(out, "worldshepherd-slow-tier.jsonl")
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"WORLDSHEPHERD SLOW TIER - {'PASS' if ok else 'FAIL'} ({len(rows)} runs; every ticket won exactly once in "
          f"{sum(1 for r in rows if r['verdict'] == 'PASS')}); rows in {path}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
