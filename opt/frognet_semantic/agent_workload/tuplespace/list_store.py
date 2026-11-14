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
agent_workload/tuplespace/list_store.py -- the tuple space in a list.

[LIST_BACKEND_SUBSTITUTES_THE_BACKEND_NOT_THE_STORE_V1]

Why this exists
---------------
Development and simulation need a tuple store that runs without MariaDB and
api.php. Four oracles already grew their own inline `FakeTuples`, each a dozen
lines, each subtly different -- and simulation/test_tuple_address_oracle.py
states the hazard in its own docstring: every stub of this store its author
wrote agreed with him, and the real one did not. That is how five rows came to
sit at one address while a Communicator watched its writes succeed.

So this does NOT reimplement the store. It replaces the BACKEND underneath it.

    core.frognet_tuples.get_all()          <- real code, runs unchanged
        parses the SD: prefix, splits var/scope, honours fresh_s,
        derives ts_env and age_s, warns on missing envelopes
      -> core.frognet_tuples._values_raw() <- THIS module supplies the rows

Everything a caller can observe -- row shape, envelope handling, the
`fresh_s` contract, the SD: stripping -- is the shipped implementation. Only
where the rows physically live changes. A stub that reimplements `get_all` can
disagree with the real one; a stub that only hands it rows cannot.

The address is the schema's, not an invention
---------------------------------------------
    UNIQUE KEY idx_tuple_address (SensorName, SensorType, SensorAddress)

Three coordinates. A write replaces on all three and on nothing less. That
matters: the existing inline fakes replace on (service, var, scope) and drop
the address, so in them two different NODES writing the same variable at the
same scope collide into one row, where the real store keeps them apart. That
is exactly the class of divergence this module exists to stop, and it is why
the address oracle can be pointed at this backend as well as at MariaDB --
same assertions, both backends, any disagreement is a bug in one of them.

Usage
-----
    from agent_workload.tuplespace import list_store

    with list_store.installed():          # scoped: restores on exit
        T.put("torch", "grad", "rank:3", {...})
        rows = T.get("torch", "grad")

or for a whole process (tests, a sim run):

    list_store.install()

Nothing here talks to a network, so `dbhost` is accepted and ignored -- except
that it is RECORDED, so a caller that passes two different dbhosts to one
process can see that it did.
"""

from __future__ import annotations

import contextlib
import copy
import json
import time as _time
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

#: The store's own clock, in the store's own units. get_all() reads this as
#: `UpdatedAtEpoch` and derives ts_env/age_s from it. [ENVELOPE_TS_V1] -- one
#: clock decides freshness and it is the one that stamped the row, so a
#: writer's payload `ts` is never consulted here either.
_now = time.time


class ListStore:
    """Rows in a list, addressed by (SensorName, SensorType, SensorAddress).

    Thread-safe: a single lock around the list. The real store is a database
    with row locking and the PyTorch work is threads over one space, so an
    unsynchronised list would produce races this backend does not have and the
    real one does not either.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rows: List[Dict[str, Any]] = []
        #: [DO_NOT_SCAN_WHAT_YOU_CAN_INDEX_V1] rows by SensorType.
        #:
        #: `values_raw` walked every row on every read. That was harmless
        #: when a store held a handful of cells, and stopped being harmless
        #: when the generation went into the key: nothing is overwritten, so
        #: a long run accumulates a row per generation per rank and the scan
        #: grows with it. Measured: 2.5 us at 100 rows, 110.8 us at 5000, on
        #: a path a collective takes several times per operation.
        #:
        #: The real store has an index because it is a database. This one
        #: stands in for it and should not have a different cost curve.
        self._by_service: Dict[str, List[Dict[str, Any]]] = {}
        #: (SensorName, SensorType, SensorAddress) -> row. The identity the
        #: upsert matches on, indexed, so a put does not read the table.
        self._by_key: Dict[Any, Dict[str, Any]] = {}
        self._next_id = 1
        #: dbhosts this process has addressed. Not used for routing -- kept so
        #: a test that accidentally splits its writes across two store names
        #: can discover that rather than wonder where its rows went.
        self.dbhosts_seen: set = set()
        #: every write, in order, for tests that need to assert on history
        #: even though the store itself keeps only current state.
        self.write_log: List[Tuple[float, str, str, str, str]] = []

    # -- the backend surface frognet_tuples calls -------------------------

    def values_raw(self, service: str, dbhost: str = "",
                   name_like: Optional[str] = None,
                   timeout: float = 4.0,
                   fresh_s: int = 0) -> List[Dict[str, Any]]:
        """Stand-in for core.frognet_tuples._values_raw.

        Same filters the real one applies server-side: SensorType when a
        service is named, exact SensorName when one is given, and the
        `fresh_s` window against the ENVELOPE.

        SERVICE_ANY is the empty string in the real module -- a query with no
        SensorType filter -- so an empty `service` here means the same thing
        rather than meaning "a service called nothing".
        """
        self.dbhosts_seen.add(dbhost)
        cutoff = (_now() - fresh_s) if fresh_s else None
        out: List[Dict[str, Any]] = []
        with self._lock:
            # [DO_NOT_SCAN_WHAT_YOU_CAN_INDEX_V1] Ask the index when a
            # service is named -- which is every call the tuple layer makes.
            src = (self._by_service.get(service, ()) if service
                   else self._rows)
            for r in src:
                if name_like is not None and r["SensorName"] != name_like:
                    continue
                if cutoff is not None and r["UpdatedAtEpoch"] < cutoff:
                    continue
                # [A_COPY_THE_CALLER_CANNOT_SHARE_V1] The caller gets rows it
                # may mutate without editing the store, which is what an HTTP
                # response gives it. deepcopy is 5.77 us a row; the payload
                # is JSON-shaped by construction, so a parse of the text this
                # store already holds is 2.63 us AND shares nothing -- which
                # is exactly what the real store's response does.
                out.append({"SensorID": r["SensorID"],
                            "SensorName": r["SensorName"],
                            "SensorType": r["SensorType"],
                            "SensorAddress": r["SensorAddress"],
                            "UpdatedAtEpoch": r["UpdatedAtEpoch"],
                            "data": json.loads(r["data_json"])})
        return out

    def upsert_by_name(self, service: str, name: str, addr: str,
                       payload: Dict[str, Any], dbhost: str = "") -> bool:
        """Stand-in for the write half of put(): api.php's upsert_by_name.

        Replaces on the FULL unique key. Anything less and this backend
        becomes a different store from the one it is standing in for.
        """
        # [DO_NOT_SCAN_WHAT_YOU_CAN_INDEX_V1], write half.
        #
        # The read path was indexed by service and the write path was not:
        # every put walked every row looking for the identity triple. The
        # real store does this with a UNIQUE KEY and never scans. Standing in
        # for it while scanning makes this backend a different store from the
        # one it represents -- and worse for the case the space is actually
        # used in, where generation-keyed names mean nothing is ever updated,
        # so EVERY write is an insert that first reads the whole table.
        #
        # An identity index costs one dict entry per row and makes the lookup
        # what api.php's unique key already makes it.
        self.dbhosts_seen.add(dbhost)
        with self._lock:
            ts = _now()
            key = (name, service, addr)
            r = self._by_key.get(key)
            if r is not None:
                r["data_json"] = json.dumps(payload)
                r["UpdatedAtEpoch"] = ts
                self.write_log.append((ts, "update", service, name, addr))
                return True
            row = {
                "SensorID": self._next_id,
                "SensorName": name,
                "SensorType": service,
                "SensorAddress": addr,
                "data_json": json.dumps(payload),
                "UpdatedAtEpoch": ts,
            }
            self._rows.append(row)
            self._by_service.setdefault(service, []).append(row)
            self._by_key[key] = row
            self._next_id += 1
            self.write_log.append((ts, "insert", service, name, addr))
        return True

    def delete_by_id(self, sensor_id: Any, dbhost: str = "") -> bool:
        with self._lock:
            n = len(self._rows)
            gone = [r for r in self._rows if r["SensorID"] == sensor_id]
            self._rows = [r for r in self._rows if r["SensorID"] != sensor_id]
            for g in gone:
                self._by_key.pop((g["SensorName"], g["SensorType"],
                                  g["SensorAddress"]), None)
                lst = self._by_service.get(g["SensorType"])
                if lst:
                    self._by_service[g["SensorType"]] = [
                        r for r in lst if r["SensorID"] != sensor_id]
            return len(self._rows) != n

    # -- inspection, for tests --------------------------------------------

    def rows(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r, data=json.loads(r["data_json"]))
                    for r in self._rows]

    def count(self) -> int:
        with self._lock:
            return len(self._rows)

    def addresses(self) -> List[Tuple[str, str, str]]:
        """Every occupied address. len(addresses()) == count() always -- if it
        ever does not, the unique key has been violated, which is the original
        bug this store is shaped to make impossible."""
        with self._lock:
            return [(r["SensorName"], r["SensorType"], r["SensorAddress"])
                    for r in self._rows]

    def age_row(self, name: str, service: str, addr: str,
                seconds: float) -> bool:
        """Push one row's envelope back in time. For testing fresh_s and
        staleness without sleeping."""
        with self._lock:
            for r in self._rows:
                if (r["SensorName"] == name and r["SensorType"] == service
                        and r["SensorAddress"] == addr):
                    r["UpdatedAtEpoch"] -= seconds
                    return True
        return False

    def clear(self) -> None:
        with self._lock:
            self._rows = []
            self.write_log = []


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------
#
# The real module stays imported and in use. Only the two functions that touch
# the network are rebound, so get_all/get/var_name/role_scope and every guard
# in put() remain the shipped code.

_ACTIVE: Optional[ListStore] = None
_SAVED: Dict[str, Any] = {}


def active() -> Optional[ListStore]:
    return _ACTIVE


def install(store: Optional[ListStore] = None, my_ip: str = "10.0.0.1"):
    """Rebind core.frognet_tuples' backend to a ListStore. Returns the store.

    `my_ip` is pinned because scope helpers derive from it, and on a machine
    that is not a FrogNet node the real my_ip() answers with whatever the
    host happens to have -- which makes a scope, and therefore an address,
    depend on where the test ran.
    """
    global _ACTIVE, _SAVED
    from core import frognet_tuples as T

    st = store or ListStore()
    if not _SAVED:
        _SAVED = {"_values_raw": T._values_raw,
                  "_delete_by_id": T._delete_by_id,
                  "my_ip": T.my_ip,
                  "put": T.put}

    def _values_raw(service, dbhost=T.DEFAULT_DBHOST, name_like=None,
                    timeout=4.0, fresh_s=0, wait_s=0.0, min_rows=0):
        """[ASK_ONCE_AND_WAIT_V1] The in-process store honours the blocking
        read too, so a caller behaves the same against either backend.

        Without this the stand-in raised TypeError the moment the real
        signature grew a parameter -- which is the right failure, but it
        means the two must be kept in step deliberately.
        """
        rows = st.values_raw(service, dbhost, name_like, timeout, fresh_s)
        if wait_s > 0 and min_rows > 0 and len(rows) < min_rows:
            deadline = _time.time() + min(wait_s, 30.0)
            nap = 0.0002
            while _time.time() < deadline and len(rows) < min_rows:
                _time.sleep(nap)
                nap = min(nap * 1.5, 0.005)
                rows = st.values_raw(service, dbhost, name_like, timeout,
                                     fresh_s)
        return rows

    def _delete_by_id(dbhost, sensor_id, timeout=4.0):
        return st.delete_by_id(sensor_id, dbhost)

    def put(service, var, scope, value, dbhost=T.DEFAULT_DBHOST,
            timeout=4.0, own=True):
        # Mirrors the real put(): stamp ts into the payload, build the
        # SensorName from var+scope, address by my_ip(). The stamp is the
        # writer's own and is NOT what freshness is judged on -- the envelope
        # is -- but callers read it, so it has to be there.
        payload = dict(value)
        payload.setdefault("ts", int(_now()))
        return st.upsert_by_name(service, T.var_name(var, scope),
                                 T.my_ip(), payload, dbhost)

    T._values_raw = _values_raw
    T._delete_by_id = _delete_by_id
    T.my_ip = lambda: my_ip
    T.put = put
    _ACTIVE = st
    return st


def uninstall() -> None:
    global _ACTIVE, _SAVED
    if not _SAVED:
        return
    from core import frognet_tuples as T
    for k, v in _SAVED.items():
        setattr(T, k, v)
    _SAVED = {}
    _ACTIVE = None


@contextlib.contextmanager
def installed(store: Optional[ListStore] = None, my_ip: str = "10.0.0.1"):
    """Scoped install. Restores the real backend on exit even if the body
    raises -- a test that leaves the store rebound poisons every test after
    it in the same process."""
    st = install(store, my_ip)
    try:
        yield st
    finally:
        uninstall()
