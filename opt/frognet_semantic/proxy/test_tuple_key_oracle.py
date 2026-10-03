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
"""proxy/test_tuple_key_oracle.py  [TUPLE_KEY_V1]

core/tuple_key.py is THE key for tuple-store calls; proxy/local_read_cache.py and daemon/engine/data_cache.py now take
their key, entities, actions, control keys, sensor columns and query parse from it. This proves the rewiring changed no
behaviour: the pre-TUPLE_KEY_V1 local_read_cache (file given as argv[1]) and the current one run the same random
sequences of reads, stored answers and writes, and every lookup, invalidation count and stat must agree; the current
data_cache query parse must equal the pre-TUPLE_KEY_V1 parse on every path, malformed ones included.
Run: python3 proxy/test_tuple_key_oracle.py OLD_local_read_cache.py [OLD_data_cache.py]
"""
import importlib.util, json, os, random, sys
from urllib.parse import parse_qs, urlparse
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import proxy.local_read_cache as NEW
from core import tuple_key as TK
spec = importlib.util.spec_from_file_location("lrc_old", sys.argv[1]); OLD = importlib.util.module_from_spec(spec); spec.loader.exec_module(OLD)
fails = []
def ck(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("   [%s]" % detail if detail and not cond else ""))
    if not cond: fails.append(name)
rng = random.Random(20260925)
NAMES = ["n1.net.cpu.load", "n2.net.cpu.load", "SD:game.table1/state", "SD:game.table2/state", "é.x"]
def read_path():
    p = {"entity": rng.choice(["sensors", "sensor_data", "other"]), "action": rng.choice(["values", "list", "get", "upsert"])}
    for k in rng.sample(["SensorType", "SensorName", "SensorID", "Tags", "limit", "order", "parse", "fresh_s", "SensorName__like", "x"], rng.randrange(0, 5)):
        p[k] = rng.choice(["CPU", "5", "", NAMES[rng.randrange(5)], "d"])
    items = list(p.items()); rng.shuffle(items)
    return "/api.php?" + "&".join("%s=%s" % kv for kv in items)
def resp(names):
    return {"status": rng.choice([200, 200, 200, 404]), "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": True, "rows": [{"SensorName": n} for n in names]}).encode()}
def write():
    path = "/api.php?entity=%s&action=%s" % (rng.choice(["sensors", "sensor_data"]), rng.choice(["upsert", "upsert_batch", "update", "values"]))
    k = rng.random()
    if k < .25: path += "&SensorName=" + rng.choice(NAMES); body = b""
    elif k < .5: body = json.dumps({"SensorName": rng.choice(NAMES), "Value": 1}).encode()
    elif k < .7: body = json.dumps({"items": [{"SensorName": n} for n in rng.sample(NAMES, 2)]}).encode()
    elif k < .85: body = ("SensorName=" + rng.choice(NAMES)).encode()
    else: body = b"{not json"
    return path, body
for m in (OLD, NEW): m._TTL = 0; m._reset_for_test()
ops, bad = 0, 0
for step in range(20000):
    r = rng.random()
    if r < .55:
        path = read_path(); method = rng.choice(["GET", "GET", "POST"])
        ko, kn = OLD.read_key(method, path), NEW.read_key(method, path)
        if (ko is None) != (kn is None): bad += 1; continue
        go, gn = OLD.get(ko), NEW.get(kn)
        if go != gn: bad += 1
        if go is None and ko is not None:
            rs = resp(rng.sample(NAMES, rng.randrange(0, 3))); OLD.put(ko, dict(rs)); NEW.put(kn, dict(rs))
    else:
        path, body = write(); method = rng.choice(["POST", "POST", "GET"])
        if OLD.invalidate_for_write(method, path, body) != NEW.invalidate_for_write(method, path, body): bad += 1
    ops += 1
ck("local_read_cache: %d operations, every lookup and invalidation agrees (%d differ)" % (ops, bad), bad == 0)
so, sn = OLD.stats(), NEW.stats()
ck("local_read_cache: the stats agree", so == sn, "%r vs %r" % (so, sn))
ck("local_read_cache: clear_all drops the same number of entries", OLD.clear_all() == NEW.clear_all())
# data_cache parse: the pre-TUPLE_KEY_V1 _parse_api_path, verbatim
def old_parse(path):
    if not path or "api.php" not in path: return None
    try:
        q = urlparse(path).query
        return {k: (v[0] if v else "") for k, v in parse_qs(q, keep_blank_values=True).items()}
    except Exception: return None
import daemon.engine.data_cache as DC
paths = [read_path() for _ in range(5000)] + ["", "/x", "http://[bad/api.php?a=1", "/api.php?a=1&a=2&b", "/api.php", "//api.php?entity=sensors"]
ck("data_cache: the query parse equals the old one on %d paths" % len(paths), all(DC._parse_api_path(p) == old_parse(p) for p in paths))
ck("one key: local_read_cache's key is tuple_key.read_key", all((NEW.read_key("GET", p) or (None,))[0] == TK.read_key("GET", p) for p in paths))
print("\n%s: %d fail" % ("ALL TUPLE-KEY ORACLE CHECKS PASS" if not fails else "TUPLE-KEY ORACLE FAILED", len(fails))); sys.exit(1 if fails else 0)
