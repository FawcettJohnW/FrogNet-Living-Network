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
"""sim_cache_soak.py — steady-state soak of the in-proxy local_read_cache under the
REAL live write pattern, to prove the batch-invalidation fix is precise rather than
blunt.

Why this exists
---------------
[LRC_BATCH_INVALIDATE_V1] closes a staleness gap: upsert_batch wrote metrics that
never busted any cached read. The obvious one-line "fix" — just add "upsert_batch"
to _WRITE_ACTIONS — is WORSE than the bug, because _written_names finds no top-level
SensorName in {"items":[...]} , returns None, and takes the clear-all branch on every
batch. The module docstring warns exactly this: writes run ~288/min and a blunt
flush-on-any-write "would thrash the cache to uselessness".

This soak runs three arms over identical traffic and shows the tradeoff is real:

  OLD    upsert_batch not a write action  -> game cache safe, metric reads STALE
  NAIVE  upsert_batch clears everything   -> metric reads fresh, game cache DESTROYED
  NEW    upsert_batch busts by item name  -> metric reads fresh AND game cache safe

Traffic model (from the live system):
  - 8 nodes, each emitting one upsert_batch of ~25 sensors every 30s
    (FROGNET_METRICS_INTERVAL=30, proxy_metrics.py:37), randomly staggered
  - a board game polling one SD: state sensor at 5 Hz (the poll storm this cache
    was built for)
  - a monitor pulling per-host broad reads every 6s (cadence seen in
    /tmp/frognet_monitor.log)
  - TTL backstop 10s (FROGNET_LOCAL_READ_CACHE_TTL default)
"""
import importlib.util
import json
import os
import random
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

NODES = ["Seattle2", "Seattle3", "Seattle5", "Seattle6",
         "New-York-1", "New-York-2", "BABox", "BAMacBook"]
SENSOR_TYPES = ["SemanticProxy.Engine", "SemanticProxy.LinkQuality",
                "SemanticDaemon.Cache", "SemanticDaemon.LinkQuality",
                "Apache.Workers", "Topology.Links", "System.Perf"]
PEERS = [f"10.{i}.{i}.1" for i in (28, 102, 111, 120, 130, 160, 179, 250)]

GAME_SENSOR = "SD:boardgame.table1/state"
EMIT_INTERVAL = 30.0
GAME_POLL_HZ = 5.0
MONITOR_INTERVAL = 6.0
# The module docstring states writes run ~288/min. Those are SD: convergence
# singles (capability/presence/session), NOT the metric batches -- modelling only
# the batches under-counts write volume ~6x and hides the clear-all cost.
CONVERGENCE_WRITES_PER_MIN = 288.0
# A consumer that re-reads a BROAD query faster than the TTL is where an unbusted
# batch write is actually observable. The monitor (48s per-node revisit) is slower
# than the 10s TTL, so it can never see the staleness -- that is a real finding,
# not a reason to model an unrealistic monitor.
BROAD_POLL_HZ = 2.0
DURATION = 600.0          # 10 simulated minutes
TICK = 0.05

_p = _f = 0


def ck(n, c, x=""):
    global _p, _f
    if c:
        _p += 1
        print(f"  [PASS] {n}")
    else:
        _f += 1
        print(f"  [FAIL] {n}  {x}")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sensors_for(node):
    names = [f"{node}.{t}" for t in SENSOR_TYPES]
    names += [f"{node}.SemanticCache.Peer.{p}" for p in PEERS]
    return names


class Clock:
    t = 0.0


def run_arm(arm, lrc_path):
    """Run one arm. Returns dict of measurements."""
    L = _load(f"lrc_{arm}", lrc_path)
    # virtual clock: the module only calls time.time()
    L.time = types.SimpleNamespace(time=lambda: Clock.t)
    L._reset_for_test()

    if arm == "naive":
        # Simulate the one-line "fix": upsert_batch IS a write action, but name
        # extraction still cannot see into items[] -> None -> clear-all.
        if "upsert_batch" not in L._WRITE_ACTIONS:
            L._WRITE_ACTIONS = tuple(L._WRITE_ACTIONS) + ("upsert_batch",)
        target = "_written_names" if hasattr(L, "_written_names") else "_written_name"
        orig = getattr(L, target)

        def blunt(path, body, _orig=orig):
            p = L._params(path) or {}
            if p.get("action") == "upsert_batch":
                return None          # cannot attribute -> caller clears all
            return _orig(path, body)
        setattr(L, target, blunt)

    rng = random.Random(1337)
    Clock.t = 0.0

    # truth[name] = version counter, bumped every time that sensor is written
    truth = {}
    for n in NODES:
        for s in sensors_for(n):
            truth[s] = 0
    truth[GAME_SENSOR] = 0

    next_emit = {n: rng.uniform(0, EMIT_INTERVAL) for n in NODES}
    next_game = 0.0
    next_monitor = 0.0
    next_game_write = 0.0
    next_broad = 0.0
    next_conv = 0.0
    conv_period = 60.0 / CONVERGENCE_WRITES_PER_MIN

    game_hits = game_misses = 0
    monitor_reads = monitor_stale = 0
    broad_hits = broad_misses = broad_stale = 0
    total_busted = 0
    conv_writes = 0
    BROAD_NODE = "Seattle6"

    def game_key():
        return L.read_key("GET", "/api.php?entity=sensors&action=values"
                                 f"&SensorName={GAME_SENSOR}&parse=1")

    def host_key(node):
        return L.read_key("GET", "/api.php?entity=sensors&action=values"
                                 f"&SensorName__like={node}.%")

    def body_for(names):
        return json.dumps({"ok": True, "rows": [
            {"SensorName": s, "SensorID": abs(hash(s)) % 100000,
             "jsonData": json.dumps({"v": truth[s]})} for s in names]}).encode()

    while Clock.t < DURATION:
        # ---- metric emitters: one upsert_batch per node per interval ----
        for node in NODES:
            if Clock.t >= next_emit[node]:
                next_emit[node] += EMIT_INTERVAL
                names = sensors_for(node)
                for s in names:
                    truth[s] += 1
                items = [{"SensorName": s, "SensorType": "x",
                          "jsonData": {"v": truth[s]}} for s in names]
                total_busted += L.invalidate_for_write(
                    "POST", "/api.php?entity=sensor_data&action=upsert_batch",
                    json.dumps({"items": items}).encode())

        # ---- the game's own state write (a real single-sensor upsert) ----
        if Clock.t >= next_game_write:
            next_game_write += 2.0
            truth[GAME_SENSOR] += 1
            total_busted += L.invalidate_for_write(
                "POST", "/api.php?entity=sensor_data&action=upsert_by_name",
                json.dumps({"SensorName": GAME_SENSOR}).encode())

        # ---- SD: convergence singles (~288/min, the real write floor) ----
        if Clock.t >= next_conv:
            next_conv += conv_period
            conv_writes += 1
            cname = f"SD:capability.host:10.{rng.choice([28,102,111,120,130,160,179,250])}.1:role{conv_writes % 4}"
            truth[cname] = truth.get(cname, 0) + 1
            total_busted += L.invalidate_for_write(
                "POST", "/api.php?entity=sensor_data&action=upsert_by_name",
                json.dumps({"SensorName": cname}).encode())

        # ---- fast BROAD poller: re-reads one host's whole set at 2Hz ----
        if Clock.t >= next_broad:
            next_broad += 1.0 / BROAD_POLL_HZ
            names = sensors_for(BROAD_NODE)
            k = host_key(BROAD_NODE)
            hit = L.get(k)
            if hit is not None:
                broad_hits += 1
                doc = json.loads(hit["body"].decode())
                served = {r["SensorName"]: json.loads(r["jsonData"])["v"]
                          for r in doc["rows"]}
                if any(served.get(s) != truth[s] for s in names):
                    broad_stale += 1
            else:
                broad_misses += 1
                L.put(k, {"status": 200, "headers": {}, "body": body_for(names)})

        # ---- board game poll storm ----
        if Clock.t >= next_game:
            next_game += 1.0 / GAME_POLL_HZ
            k = game_key()
            if L.get(k) is not None:
                game_hits += 1
            else:
                game_misses += 1
                L.put(k, {"status": 200, "headers": {},
                          "body": body_for([GAME_SENSOR])})

        # ---- monitor broad per-host reads ----
        if Clock.t >= next_monitor:
            next_monitor += MONITOR_INTERVAL
            node = NODES[int(Clock.t / MONITOR_INTERVAL) % len(NODES)]
            k = host_key(node)
            hit = L.get(k)
            names = sensors_for(node)
            if hit is not None:
                monitor_reads += 1
                doc = json.loads(hit["body"].decode())
                served = {r["SensorName"]: json.loads(r["jsonData"])["v"]
                          for r in doc["rows"]}
                if any(served.get(s) != truth[s] for s in names):
                    monitor_stale += 1
            else:
                monitor_reads += 1
                L.put(k, {"status": 200, "headers": {}, "body": body_for(names)})

        Clock.t += TICK

    polls = game_hits + game_misses
    bpolls = broad_hits + broad_misses
    return {
        "arm": arm,
        "game_hit_rate": game_hits / polls if polls else 0.0,
        "game_polls": polls,
        "broad_polls": bpolls,
        "broad_hit_rate": broad_hits / bpolls if bpolls else 0.0,
        "broad_stale": broad_stale,
        "broad_stale_rate": broad_stale / bpolls if bpolls else 0.0,
        "conv_writes": conv_writes,
        "monitor_reads": monitor_reads,
        "monitor_stale": monitor_stale,
        "monitor_stale_rate": monitor_stale / monitor_reads if monitor_reads else 0.0,
        "busted": total_busted,
        "stats": L.stats(),
    }


def main():
    old = os.environ.get("FN_LRC_OLD") or os.path.join(
        HERE, "..", "..", "..", "..", "oldmods", "local_read_cache.py")
    new = os.environ.get("FN_LRC") or os.path.join(
        ROOT, "proxy", "local_read_cache.py")
    old = os.path.normpath(old)

    print(f"OLD module: {old}")
    print(f"NEW module: {new}")
    print(f"model: {len(NODES)} nodes x {len(sensors_for('X'))} sensors, "
          f"batch every {EMIT_INTERVAL:.0f}s; game {GAME_POLL_HZ:.0f}Hz; "
          f"{DURATION:.0f}s simulated\n")

    results = {}
    for arm, path in (("old", old), ("naive", new), ("new", new)):
        if not os.path.exists(path):
            print(f"  [SKIP] {arm}: {path} not found")
            continue
        r = run_arm(arm, path)
        results[arm] = r
        print(f"  {arm:>5}: game-hit {r['game_hit_rate']*100:6.2f}%  "
              f"broad-hit {r['broad_hit_rate']*100:6.2f}%  "
              f"broad-stale {r['broad_stale']:5d}/{r['broad_polls']} "
              f"({r['broad_stale_rate']*100:5.2f}%)  "
              f"monitor-stale {r['monitor_stale']}/{r['monitor_reads']}  "
              f"busted={r['busted']}")

    print()
    o, na, ne = results.get("old"), results.get("naive"), results.get("new")

    ck("S1 OLD leaves a fast broad reader STALE after an unbusted batch write",
       o["broad_stale"] > 0, f"broad_stale={o['broad_stale']}")
    ck("S2 NEW eliminates that staleness entirely",
       ne["broad_stale"] == 0, f"broad_stale={ne['broad_stale']}")
    ck("S3 NAIVE also eliminates it (both fixes close the correctness gap)",
       na["broad_stale"] == 0, f"broad_stale={na['broad_stale']}")

    # Corrected finding: read_key() normalises on the query, so the monitor and any
    # faster consumer of the SAME broad query SHARE one cache entry. The fast reader
    # keeps that entry warm, defeating the TTL backstop that would otherwise have
    # masked the bug -- so on OLD the slow monitor DOES observe staleness it could
    # never have produced on its own. Key sharing is what makes this reachable.
    ck("S4 OLD: a slow monitor observes staleness once a fast reader shares its key",
       o["monitor_stale"] > 0, f"monitor_stale={o['monitor_stale']}")
    ck("S4b NEW drives that monitor staleness to zero too",
       ne["monitor_stale"] == 0, f"monitor_stale={ne['monitor_stale']}")

    ck("S5 NEW preserves the game poll cache",
       ne["game_hit_rate"] >= o["game_hit_rate"] - 0.01,
       f"old={o['game_hit_rate']*100:.2f}% new={ne['game_hit_rate']*100:.2f}%")
    ck("S6 NEW preserves the broad-read cache as well as OLD did",
       ne["broad_hit_rate"] >= o["broad_hit_rate"] - 0.05,
       f"old={o['broad_hit_rate']*100:.2f}% new={ne['broad_hit_rate']*100:.2f}%")
    ck("S7 NAIVE costs cache effectiveness that NEW does not",
       na["broad_hit_rate"] < ne["broad_hit_rate"],
       f"naive={na['broad_hit_rate']*100:.2f}% new={ne['broad_hit_rate']*100:.2f}%")
    ck("S8 NAIVE busts strictly more than NEW (blunt vs precise)",
       na["busted"] > ne["busted"], f"naive={na['busted']} new={ne['busted']}")
    ck("S9 NEW busts more than OLD (it is actually doing the invalidation)",
       ne["busted"] > o["busted"], f"old={o['busted']} new={ne['busted']}")

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
