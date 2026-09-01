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
"""test_cache_coherence_oracle.py — the two caches that stand in front of MySQL must
not be pickier than MySQL, and must not go stale behind a batch write.

Both bugs proved here are the same family: a cache answers AUTHORITATIVELY with a
result the real database would not have given.

  local_read_cache (every node, in-proxy):
    [LRC_NO_CACHE_EMPTY_V1]     an empty result must never be stored — invalidation
                                keys on SensorName membership, so an empty entry has
                                no members and NOTHING can bust it but the TTL.
    [LRC_BATCH_INVALIDATE_V1]   upsert_batch carries {"items":[{SensorName..}..]}.
                                It must bust precisely those names — not nothing
                                (stale), and not everything (thrash: ~288 writes/min
                                would flush the cache to uselessness).

  data_cache (databasehost only, in-daemon):
    [DATACACHE_LIKE_FALLTHROUGH_V1]  a "<col>__like" filter must fall through to
                                Apache/MySQL. _match_row does equality, so a LIKE
                                matched no column and returned an authoritative [].
    [DATACACHE_CI_MATCH_V1]     the schema is COLLATE utf8mb4_unicode_ci, so string
                                compares are case-INSENSITIVE in MySQL. Case-sensitive
                                matching here returned [] for a row MySQL would return.

Run against the OLD modules and every G* below fails; against the NEW modules they
all pass. That contrast is the gate — see main().

Usage:
  ./test_cache_coherence_oracle.py            # test the tree's modules
  FN_LRC=<path> FN_DC=<path> ...              # point at specific module files
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

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
    """Load a module from an explicit path so we can A/B old vs new."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── fixtures: a realistic slice of the live Sensor table ──

SEATTLE6 = [
    (1684413, "Seattle6.SemanticProxy.Engine", "SemanticProxy.Engine"),
    (1684415, "Seattle6.SemanticDaemon.Cache", "SemanticDaemon.Cache"),
    (1684414, "Seattle6.Apache.Workers", "Apache.Workers"),
    (1684411, "Seattle6.Topology.Links", "Topology"),
]
NY2 = [
    (1684461, "New-York-2.Apache.Workers", "Apache.Workers"),
    (1684460, "New-York-2.SemanticProxy.Engine", "SemanticProxy.Engine"),
]


def seed_data_cache(D):
    D._ENABLED = True
    D._loaded = True
    D._ensure_started = lambda: None
    D._sensors = {}
    D._name_to_id = {}
    for sid, name, stype in SEATTLE6 + NY2:
        D._sensors[sid] = {"SensorID": sid, "FrogID": f"frog-{sid}",
                           "SensorAddress": "10.160.160.1",
                           "SensorNetwork": "10.160.160.0/24",
                           "SensorName": name, "SensorType": stype, "Tags": None}
        key = D._ci(name) if hasattr(D, "_ci") else name
        D._name_to_id[key] = sid
    D._sensor_data = {sid: {"SensorID": sid, "FrogID": f"frog-{sid}",
                            "jsonData": '{"v":1}'} for sid, _, _ in SEATTLE6 + NY2}


def rows_of(resp):
    return json.loads(resp)["rows"] if resp is not None else None


# ── G1/G2: data_cache ──

def g1_like_fallthrough(D):
    """A LIKE read must fall through (None), never answer authoritatively."""
    seed_data_cache(D)
    r = D.try_intercept(
        "/api.php?entity=sensors&action=list&SensorName__like=Seattle6.%", "")
    ck("G1 data_cache: sensors/list with __like falls through to MySQL",
       r is None, f"returned {r!r}")

    r = D.try_intercept(
        "/api.php?entity=sensors&action=values&SensorName__like=New-York-2.%&parse=1", "")
    ck("G1 data_cache: sensors/values with __like falls through to MySQL",
       r is None, f"returned {r!r}")

    # the exact-match read this cache exists to serve must STILL be served
    r = D.try_intercept("/api.php?entity=sensors&action=list"
                        "&SensorName=Seattle6.SemanticProxy.Engine&limit=1", "")
    ck("G1 data_cache: exact-name list still served from RAM (cache not disabled)",
       r is not None and len(rows_of(r)) == 1, f"returned {r!r}")

    r = D.try_intercept("/api.php?entity=sensors&action=get&SensorID=1684413", "")
    ck("G1 data_cache: get-by-SensorID still served from RAM",
       r is not None and json.loads(r)["row"]["SensorID"] == 1684413, f"{r!r}")


def g2_case_insensitive(D):
    """MySQL compares _ci; the cache must not be stricter."""
    seed_data_cache(D)
    exact = "/api.php?entity=sensors&action=list&SensorName=Seattle6.SemanticProxy.Engine"
    base = rows_of(D.try_intercept(exact, ""))
    ck("G2 data_cache: baseline exact-case match returns the row",
       base is not None and len(base) == 1, f"{base!r}")

    for variant in ("seattle6.semanticproxy.engine",
                    "SEATTLE6.SEMANTICPROXY.ENGINE",
                    "SeAtTlE6.SemanticProxy.Engine"):
        r = rows_of(D.try_intercept(
            f"/api.php?entity=sensors&action=list&SensorName={variant}", ""))
        ck(f"G2 data_cache: case variant matches like MySQL would ({variant[:22]}...)",
           r is not None and len(r) == 1 and r[0]["SensorID"] == 1684413,
           f"got {0 if r is None else len(r)} rows")

    # must not over-match: a genuinely different name still returns nothing
    r = rows_of(D.try_intercept(
        "/api.php?entity=sensors&action=list&SensorName=Seattle6.Nope.Missing", ""))
    ck("G2 data_cache: unrelated name still returns no rows (no over-match)",
       r is not None and len(r) == 0, f"got {r!r}")

    # SensorType filter is also a _ci column
    r = rows_of(D.try_intercept(
        "/api.php?entity=sensors&action=list&SensorType=apache.workers", ""))
    ck("G2 data_cache: _ci matching applies to other string columns too",
       r is not None and len(r) == 2, f"got {0 if r is None else len(r)} rows")


def g3_name_index_ci(D):
    """upsert_by_name with odd casing must resolve to the SAME SensorID, and must
    not mint a second _name_to_id entry for a row MySQL considers unique."""
    seed_data_cache(D)
    before_ids = set(D._name_to_id.values())
    before_len = len(D._name_to_id)
    body = json.dumps({"SensorName": "SEATTLE6.SemanticProxy.Engine",
                       "jsonData": {"v": 2}})
    r = D.try_intercept(
        "/api.php?entity=sensor_data&action=upsert_by_name", body)
    ck("G3 data_cache: differently-cased upsert_by_name resolves in-cache (no miss)",
       r is not None and json.loads(r).get("ok") is True, f"{r!r}")
    ck("G3 data_cache: it updated the EXISTING SensorID, not a new one",
       D._sensor_data[1684413]["jsonData"] == '{"v":2}',
       f'jsonData={D._sensor_data[1684413]["jsonData"]!r}')
    ck("G3 data_cache: no duplicate name-index entry created",
       len(D._name_to_id) == before_len and set(D._name_to_id.values()) == before_ids,
       f"name_to_id grew {before_len} -> {len(D._name_to_id)}")


# ── G4/G5/G6: local_read_cache ──

def _resp(body: bytes):
    return {"status": 200, "headers": {}, "body": body}


def _body(names):
    return json.dumps({"ok": True,
                       "rows": [{"SensorName": n, "SensorID": i}
                                for i, n in enumerate(names)]}).encode()


def g4_no_cache_empty(L):
    L._reset_for_test()
    k = L.read_key("GET", "/api.php?entity=sensors&action=list"
                          "&SensorName__like=Seattle6.%")
    ck("G4 lrc: a broad read is cacheable in principle", k is not None)

    L.put(k, _resp(b'{"ok":true,"rows":[]}'))
    ck("G4 lrc: an EMPTY list result is never stored (no sticky hole)",
       L.get(k) is None, "empty result was cached")

    kg = L.read_key("GET", "/api.php?entity=sensor_data&action=get&SensorID=999")
    L.put(kg, _resp(b'{"ok":true,"row":null}'))
    ck("G4 lrc: an EMPTY get result is never stored",
       L.get(kg) is None, "empty get was cached")

    L.put(k, _resp(_body(["Seattle6.SemanticProxy.Engine"])))
    ck("G4 lrc: a populated result IS still cached (cache not neutered)",
       L.get(k) is not None)


def g5_batch_invalidates(L):
    """The live emit path: proxy_metrics/daemon_metrics POST one upsert_batch
    holding a whole tick of sensors. It must bust exactly those names."""
    L._reset_for_test()
    k6 = L.read_key("GET", "/api.php?entity=sensors&action=values"
                           "&SensorName__like=Seattle6.%")
    kny = L.read_key("GET", "/api.php?entity=sensors&action=values"
                            "&SensorName__like=New-York-2.%")
    L.put(k6, _resp(_body([n for _, n, _ in SEATTLE6])))
    L.put(kny, _resp(_body([n for _, n, _ in NY2])))
    ck("G5 lrc: two host result-sets cached", L.get(k6) and L.get(kny))

    batch = json.dumps({"items": [
        {"SensorName": "Seattle6.SemanticProxy.Engine",
         "SensorType": "SemanticProxy.Engine", "jsonData": {"t": 2}},
        {"SensorName": "Seattle6.Apache.Workers",
         "SensorType": "Apache.Workers", "jsonData": {"t": 2}},
    ]}).encode()
    n = L.invalidate_for_write(
        "POST", "/api.php?entity=sensor_data&action=upsert_batch", batch)

    ck("G5 lrc: upsert_batch busts the affected host's cached read",
       L.get(k6) is None, "Seattle6 read survived a write to its sensors (STALE)")
    ck("G5 lrc: it does NOT bust an unrelated host (precise, not clear-all)",
       L.get(kny) is not None, "New-York-2 read was flushed too (THRASH)")
    ck("G5 lrc: reports exactly one busted entry", n == 1, f"busted={n}")


def g6_batch_edges(L):
    # an empty batch wrote nothing -> must bust nothing (not clear-all)
    L._reset_for_test()
    k = L.read_key("GET", "/api.php?entity=sensors&action=values&SensorName=SD:x")
    L.put(k, _resp(_body(["SD:x"])))
    n = L.invalidate_for_write("POST",
                               "/api.php?entity=sensor_data&action=upsert_batch",
                               json.dumps({"items": []}).encode())
    ck("G6 lrc: an empty items[] busts nothing",
       n == 0 and L.get(k) is not None, f"busted={n}")

    # a body we cannot attribute at all -> still the safe clear-all
    L._reset_for_test()
    L.put(k, _resp(_body(["SD:x"])))
    n = L.invalidate_for_write("POST",
                               "/api.php?entity=sensor_data&action=upsert",
                               b"<<unparseable>>")
    ck("G6 lrc: an unattributable write still clears all (safe, not stale)",
       n == 1 and L.get(k) is None, f"busted={n}")

    # single-sensor writes must keep working exactly as before
    L._reset_for_test()
    L.put(k, _resp(_body(["SD:x"])))
    n = L.invalidate_for_write("POST",
                               "/api.php?entity=sensor_data&action=upsert_by_name",
                               json.dumps({"SensorName": "SD:x"}).encode())
    ck("G6 lrc: single-name upsert_by_name still busts precisely",
       n == 1 and L.get(k) is None, f"busted={n}")

    # a batch naming a sensor nobody cached busts nothing
    L._reset_for_test()
    L.put(k, _resp(_body(["SD:x"])))
    n = L.invalidate_for_write(
        "POST", "/api.php?entity=sensor_data&action=upsert_batch",
        json.dumps({"items": [{"SensorName": "SD:unrelated"}]}).encode())
    ck("G6 lrc: a batch naming uncached sensors busts nothing",
       n == 0 and L.get(k) is not None, f"busted={n}")


def main():
    lrc = os.environ.get("FN_LRC") or os.path.join(
        ROOT, "proxy", "local_read_cache.py")
    dc = os.environ.get("FN_DC") or os.path.join(
        ROOT, "daemon", "engine", "data_cache.py")
    print(f"local_read_cache: {lrc}")
    print(f"data_cache      : {dc}\n")

    L = _load("lrc_under_test", lrc)
    D = _load("dc_under_test", dc)

    print("-- data_cache --")
    g1_like_fallthrough(D)
    g2_case_insensitive(D)
    g3_name_index_ci(D)
    print("-- local_read_cache --")
    g4_no_cache_empty(L)
    g5_batch_invalidates(L)
    g6_batch_edges(L)

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
