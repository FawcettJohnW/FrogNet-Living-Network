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
mock_space.py - in-memory stand-in for frognet_tuples, for the simulator only.

The production tuple space (frognet_tuples.put/get/get_all) talks to api.php over
HTTP against databasehost.frognet. In the sim we need the SAME surface without a
live DB, so the media-stream lifecycle (create-stream, connection-info, control
intents, status/bearer) can be exercised end to end. This mirrors the real
semantics that matter:

  - put(service,var,scope,value): upsert_by_name keyed on SensorName = SD:<var>.<scope>.
    A later put to the same (service,var,scope) OVERWRITES (upsert), like the real DB.
  - get(service,var): all rows for one variable across scopes.
  - get_all(service): all variables for a service, honoring fresh_s by ts staleness.
  - session_scope/host_scope/var_name: identical helpers.
  - ts stamped on every write so fresh_s aging works (the real reader uses it).

One write becomes visible to ALL readers immediately (the tuple-space multicast
property the design leans on). This is the GENERAL space - databasehost semantics.
The media ingestion QUEUE is a different structure (media_stream.py), not this.

It is deliberately a faithful subset: enough to drive the lifecycle and the
positive/negative tests, no network, no auth.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

SD_PREFIX = "SD:"
DEFAULT_DBHOST = "databasehost.frognet"


def session_scope(session_id: str) -> str:
    return f"session:{session_id}"


def host_scope(ip: str = "10.0.0.1", pid: int = 1) -> str:
    return f"host:{ip}:{pid}"


def node_scope(ip: str = "10.0.0.1") -> str:
    return f"host:{ip}"


def var_name(var: str, scope: str) -> str:
    return f"{SD_PREFIX}{var}.{scope}"


class MockSpace:
    """One shared in-memory tuple space. Construct ONE and hand it to every
    endpoint in a sim so they share state the way nodes share databasehost."""

    def __init__(self):
        # key: (service, SensorName) -> {"data": value_dict, "addr": writer_addr}
        self._rows: Dict[Any, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    # -- writer -------------------------------------------------------------
    def put(self, service: str, var: str, scope: str, value: Dict[str, Any],
            dbhost: str = DEFAULT_DBHOST, addr: str = "10.0.0.1",
            own: bool = True, timeout: float = 4.0) -> bool:
        payload = dict(value)
        payload.setdefault("ts", int(time.time()))
        name = var_name(var, scope)
        with self._lock:
            self._rows[(service, name)] = {"data": payload, "addr": addr}
        return True

    def drop(self, service: str, var: str, scope: str) -> bool:
        name = var_name(var, scope)
        with self._lock:
            return self._rows.pop((service, name), None) is not None

    # -- readers ------------------------------------------------------------
    def get_all(self, service: str, dbhost: str = DEFAULT_DBHOST,
                fresh_s: int = 0, timeout: float = 4.0) -> List[Dict[str, Any]]:
        now = int(time.time())
        out: List[Dict[str, Any]] = []
        with self._lock:
            items = [(k, dict(v)) for k, v in self._rows.items() if k[0] == service]
        for (svc, sn), row in items:
            data = row.get("data")
            if not isinstance(data, dict):
                continue
            try:
                ts = int(data.get("ts", 0) or 0)
            except (TypeError, ValueError):
                continue
            if fresh_s and (now - ts) > fresh_s:
                continue
            name = sn[len(SD_PREFIX):] if sn.startswith(SD_PREFIX) else sn
            var, _, scope = name.partition(".")
            out.append({"var": var, "scope": scope, "name": sn,
                        "addr": row.get("addr", ""), "value": data})
        return out

    def get(self, service: str, var: str, dbhost: str = DEFAULT_DBHOST,
            fresh_s: int = 0, timeout: float = 4.0) -> List[Dict[str, Any]]:
        return [r for r in self.get_all(service, dbhost, fresh_s, timeout)
                if r["var"] == var]

    # convenience: one row for a (var, scope), or None
    def get_one(self, service: str, var: str, scope: str,
                fresh_s: int = 0) -> Optional[Dict[str, Any]]:
        want = var_name(var, scope)
        for r in self.get_all(service, fresh_s=fresh_s):
            if r["name"] == want:
                return r["value"]
        return None


if __name__ == "__main__":
    # smoke: write becomes visible to all readers; upsert overwrites; fresh_s ages.
    s = MockSpace()
    sess = session_scope("sotf-demo")
    s.put("mediastream", "conn_info", sess,
          {"addr": "mediahost.frognet", "tx": 9101, "rx": 9102})
    v = s.get_one("mediastream", "conn_info", sess)
    assert v and v["tx"] == 9101, v
    # upsert overwrite
    s.put("mediastream", "conn_info", sess,
          {"addr": "mediahost.frognet", "tx": 9201, "rx": 9202})
    v = s.get_one("mediastream", "conn_info", sess)
    assert v["tx"] == 9201, v
    # get across scopes
    s.put("mediastream", "bearer", "host:10.0.0.5", {"level_idx": 3})
    s.put("mediastream", "bearer", "host:10.0.0.6", {"level_idx": 5})
    assert len(s.get("mediastream", "bearer")) == 2
    # fresh_s aging
    s.put("mediastream", "old", sess, {"ts": int(time.time()) - 999})
    assert s.get_one("mediastream", "old", sess, fresh_s=10) is None
    print("mock_space OK: write-visible-to-all, upsert, multi-scope get, fresh_s aging")
