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
core/tuple_key.py  [TUPLE_KEY_V1]

THE key for calls to the tuple store (api.php, entity sensors / sensor_data). One definition, used by everything that
keys tuple traffic -- proxy/local_read_cache.py, daemon/engine/data_cache.py, and the template keying -- so the three
can never disagree about what a key is.

  read  (GET, action in READ_ACTIONS):  entity + the address pattern -- every query key that is not a control
  write (POST, action in WRITE_ACTIONS): entity + the SensorName(s) written

action and the controls (order, limit, parse, fresh_s) are not tuple identity: they are values of the CALL. Anything
whose result depends on them keeps them alongside what it stores under the key, never in the key.

The definitions are the ones the two caches already used, moved here unchanged: entities / read and write actions /
the written-names rule from local_read_cache.py, the control keys and the sensor columns from data_cache.py.
"""
from __future__ import annotations

import json
from typing import Dict, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

TUPLE_ENTITIES = ("sensors", "sensor_data")
READ_ACTIONS = ("values", "list", "get")
# [LRC_UPSERT_BATCH_IS_A_WRITE_V1] upsert_batch is a write (daemon_metrics sends a node's whole metric set through it)
WRITE_ACTIONS = ("upsert", "upsert_by_name", "upsert_batch", "update", "create", "delete")
# api.php's filter allow-list for the sensors handlers (api.php:614); a key outside it is a filter the RAM cannot express
SENSOR_COLS = ("SensorID", "FrogID", "SensorAddress", "SensorNetwork", "SensorName", "SensorType", "SensorLocation", "Tags")
# query keys that are not filters: the call's controls
CONTROL_KEYS = ("entity", "action", "order", "limit", "parse", "fresh_s")


def params(path: str) -> Optional[Dict[str, str]]:
    """The api.php query as {key: first value}; None when this is not an api.php path (or the query cannot be parsed,
    as both caches have always treated it: no key, nothing cached, the call goes through)."""
    if not path or "api.php" not in path:
        return None
    try:
        q = urlparse(path).query
        return {k: (v[0] if v else "") for k, v in parse_qs(q, keep_blank_values=True).items()}
    except Exception:
        return None


def split(p: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """(filters, controls): the address pattern and the call's controls."""
    return ({k: v for k, v in p.items() if k not in CONTROL_KEYS}, {k: v for k, v in p.items() if k in CONTROL_KEYS})


def read_key(method: str, path: str) -> Optional[str]:
    """THE key of a tuple read, or None when the call is not one."""
    if method != "GET":
        return None
    p = params(path)
    if not p or p.get("entity") not in TUPLE_ENTITIES or p.get("action") not in READ_ACTIONS:
        return None
    filters, _ = split(p)
    return "entity=%s|%s" % (p["entity"], "&".join("%s=%s" % kv for kv in sorted(filters.items())))


def controls(path: str) -> Tuple[Tuple[str, str], ...]:
    """The call's controls (action included), sorted: what a stored answer must match besides its key."""
    p = params(path) or {}
    return tuple(sorted(split(p)[1].items()))


def written_names(path: str, body: bytes) -> Optional[Set[str]]:
    """The SensorName(s) a write addresses: the query's, a JSON body's (items[] for a batch), or a form body's.
    None when they cannot be determined. [LRC_BATCH_INVALIDATE_V1] (moved here from proxy/local_read_cache.py)."""
    p = params(path) or {}
    if p.get("SensorName"):
        return {p["SensorName"]}
    try:
        text = (body or b"").decode("utf-8", "replace")
    except Exception:
        return None
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            items = d.get("items")
            if isinstance(items, list):
                return {it["SensorName"] for it in items if isinstance(it, dict) and it.get("SensorName")}
            if d.get("SensorName"):
                return {d["SensorName"]}
    except Exception:
        pass
    try:
        for k, v in parse_qs(text, keep_blank_values=True).items():
            if k == "SensorName" and v:
                return {v[0]}
    except Exception:
        pass
    return None


def write_key(method: str, path: str, body: bytes) -> Optional[str]:
    """THE key of a tuple write, or None when the call is not one or its names cannot be determined."""
    if method != "POST":
        return None
    p = params(path)
    if not p or p.get("entity") not in TUPLE_ENTITIES or p.get("action") not in WRITE_ACTIONS:
        return None
    names = written_names(path, body)
    if names is None:
        return None
    return "entity=%s|SensorName=%s" % (p["entity"], ",".join(sorted(names)))
