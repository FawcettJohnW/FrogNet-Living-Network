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
proxy/local_read_cache.py  [LOCAL_READ_CACHE_V1]

In-proxy in-memory cache for repeated LOCAL-ORIGIN api.php reads.

Why this exists
---------------
Local services (e.g. a game board) poll the same read thousands of times:
    GET /api.php?entity=sensors&action=values&...&SensorName=SD:...table1/state
Each poll is intercepted by the proxy on :80 and forwarded to Apache->MySQL.
The daemon's data_cache never sees these - it lives in a different process
(the daemon's engine.execute), while local-origin reads flow through the proxy.
This holds the response in the PROXY's own RAM and serves repeats from there,
so the poll storm never reaches Apache/MySQL.

Coherence: invalidate-on-write (per SensorName)
-----------------------------------------------
A write (POST upsert/update/create/delete) to a sensor busts every cached read
whose response CONTAINED that SensorName. That precisely scopes invalidation:
  - a write to SD:...state.table1 busts the backgammon polls (their result holds
    that name) - narrow AND broad queries alike, because a broad query's result
    set lists its members;
  - a convergence capability write (a name NOT in those result sets) does NOT
    bust the game cache. This matters: writes run ~288/min, so a blunt
    flush-on-any-write would thrash the cache to uselessness.
A short max-age TTL backstops the only case name-membership can miss: a brand
new sensor that should newly appear in a broad query whose cached result
predates it.

Scope: lives on EVERY node. It only caches that proxy's own local forwards -
nothing global, nothing cross-host - so it is safe everywhere, unlike the
databasehost-only data_cache.

Disable: FROGNET_LOCAL_READ_CACHE=0
Tune TTL backstop: FROGNET_LOCAL_READ_CACHE_TTL=10  (seconds)
"""
from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import urlparse, parse_qs

from core import tuple_key as _tk    # [TUPLE_KEY_V1] THE key for tuple-store calls, shared with data_cache and templates

_ENABLED = os.environ.get("FROGNET_LOCAL_READ_CACHE", "1").strip() == "1"
_TTL = float(os.environ.get("FROGNET_LOCAL_READ_CACHE_TTL", "10"))  # backstop only

_READ_ACTIONS = _tk.READ_ACTIONS
# [LRC_UPSERT_BATCH_IS_A_WRITE_V1] upsert_batch was missing. daemon_metrics.py sends
# the whole per-node metric set through it (one round-trip instead of N) and api.php
# handles it - so the busiest writer on the node did not bust the read cache, and
# reads kept being served pre-write values until something else happened to bust it.
_WRITE_ACTIONS = _tk.WRITE_ACTIONS
_ENTITIES = _tk.TUPLE_ENTITIES

_lock = threading.RLock()
# [TUPLE_KEY_V1] tuple key -> {call controls -> {"resp": {status, headers, body}, "names": set[str], "at": float}}.
# The key is THE tuple key (core/tuple_key.py); an answer also depends on the call's controls (action, limit, order,
# parse, fresh_s), so each stored answer is filed under its controls and serves only a call with the same ones.
_cache: dict = {}
_stats = {"hits": 0, "misses": 0, "stores": 0, "writes_seen": 0, "busted": 0}


def _params(path: str):
    return _tk.params(path)


def read_key(method: str, path: str):
    """(tuple key, call controls) for a cacheable read, else None."""
    if not _ENABLED:
        return None
    k = _tk.read_key(method, path)
    return None if k is None else (k, _tk.controls(path))


def _names_from_body(body: bytes) -> set:
    """SensorNames present in a values/list/get response (for invalidation)."""
    names = set()
    try:
        doc = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return names
    rows = doc.get("rows")
    if rows is None and doc.get("row") is not None:
        rows = [doc["row"]]
    for r in (rows or []):
        if isinstance(r, dict):
            n = r.get("SensorName")
            if n:
                names.add(n)
    return names


def get(key):
    if key is None:
        return None
    k, ctl = key
    with _lock:
        per = _cache.get(k)
        e = per.get(ctl) if per else None
        if e is None:
            _stats["misses"] += 1
            return None
        if _TTL > 0 and (time.time() - e["at"]) > _TTL:
            per.pop(ctl, None)
            if not per:
                _cache.pop(k, None)
            _stats["misses"] += 1
            return None
        _stats["hits"] += 1
        r = e["resp"]
        return {"status": r["status"], "headers": dict(r["headers"]), "body": r["body"]}


def put(key, resp):
    """Store a 200 read response keyed for future repeats."""
    if key is None or not _ENABLED:
        return
    if int(resp.get("status", 0)) != 200:
        return
    body = resp.get("body") or b""
    # [LRC_NO_CACHE_EMPTY_V1] Never cache a NEGATIVE result. An empty answer is not a
    # fact about the data, it is a fact about that one moment - the row had not been
    # written yet, the daemon had not loaded, a filter we could not express returned
    # nothing. Caching it makes a transient miss STICKY: every later repeat is served
    # the empty answer from RAM and never re-asks, so a node that starts publishing a
    # second later stays invisible until the entry is busted. Cheap to re-ask, very
    # expensive to be wrong. Positive results still cache normally.
    try:
        _doc = json.loads(body.decode("utf-8", "replace"))
        _rows = _doc.get("rows")
        if _rows is not None and len(_rows) == 0:
            _stats["empty_skipped"] = _stats.get("empty_skipped", 0) + 1
            return
        if _rows is None and _doc.get("row") is None and _doc.get("ok") is True:
            _stats["empty_skipped"] = _stats.get("empty_skipped", 0) + 1
            return
    except (ValueError, AttributeError):
        pass          # not JSON we understand - fall through and cache as before
    k, ctl = key
    with _lock:
        _cache.setdefault(k, {})[ctl] = {
            "resp": {"status": 200,
                     "headers": dict(resp.get("headers") or {}),
                     "body": body},
            "names": _names_from_body(body),
            "at": time.time(),
        }
        _stats["stores"] += 1


def _written_names(path: str, body: bytes):
    return _tk.written_names(path, body)


def invalidate_for_write(method: str, path: str, body: bytes) -> int:
    """Bust cached reads affected by a write. Returns count busted."""
    if not _ENABLED or method != "POST":
        return 0
    p = _params(path)
    if not p or p.get("entity") not in _ENTITIES:
        return 0
    if p.get("action") not in _WRITE_ACTIONS:
        return 0
    names = _written_names(path, body)
    with _lock:
        _stats["writes_seen"] += 1
        if names is None:
            # Cannot identify the target - be safe, not stale: clear all.
            n = sum(len(per) for per in _cache.values())
            _cache.clear()
            _stats["busted"] += n
            return n
        busted = 0
        for k in list(_cache):
            per = _cache[k]
            for ctl in [c for c, e in per.items() if e["names"] & names]:
                per.pop(ctl, None)
                busted += 1
            if not per:
                _cache.pop(k, None)
        _stats["busted"] += busted
        return busted


def stats():
    with _lock:
        return dict(_stats)


def clear_all() -> int:
    """[NODE_HEARTBEAT_TS_V1] Drop every cached local read: the database it was
    read from may no longer be the one being served. Returns entries dropped."""
    with _lock:
        n = sum(len(per) for per in _cache.values())
        _cache.clear()
        return n


def _reset_for_test():
    with _lock:
        _cache.clear()
        for k in _stats:
            _stats[k] = 0
