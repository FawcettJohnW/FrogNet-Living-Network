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
agent_workload/tuplespace/torch_store.py -- PyTorch's control plane in RAM.

[THE_STORE_IS_THE_SPACE_V1]

torch.distributed's TCPStore is a key-value server hosted on rank 0. Every
other rank opens a TCP connection to it and asks. That server exists for one
reason: processes cannot see each other's memory. Given memory they can all
see, the server is not a component to reimplement -- it is a component that
stops being necessary.

So this is a c10d Store whose backing is the FrogNet tuple space. A key is an
address, a value is a cell, and reading is reading. No rank 0, no master
address, no port, no connection, no rendezvous.

    <service>.<variable>.<instance>       ->  torch.<key>.<writer>

What the c10d Store surface asks for, and what it gets here
-----------------------------------------------------------
  set/get/add/delete_key/check/num_keys/list_keys    direct
  multi_get/multi_set                                the batch form, native
  wait                                               poll the space
  compare_set                                        SEE BELOW
  append                                             read-modify-write
  queue_push/queue_pop/queue_len                     SEE BELOW

compare_set is a compare-and-swap. It exists because TCPStore is one server
arbitrating concurrent writers to a shared key. In the tuple space each cell
has ONE writer -- the node it describes -- so there is nothing to arbitrate.
It is implemented here, honestly, under a lock, and it LOGS every call:
anything reaching it is a caller that still thinks it is talking to a shared
server, and that is information about the caller.

FROGNET_TORCH_CAS controls what happens then:
    warn   (default) do it, log it, carry on
    strict raise, so a caller depending on cross-writer CAS is found rather
           than silently served
    quiet  do it without logging

The queue API is the same story one level up and takes the same switch.

Everything logs through [DIAG-TSTORE]. The point of that is that when a
workflow behaves differently from the TCPStore baseline, the reason is in the
journal and not in a second run with extra prints.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

import torch.distributed as dist

from core import frognet_tuples as T

# [IMPORT_GUARD_V2] 2026-09-25: diag is first-party and the tree is always on the path; a stub hid a broken install.
from core.frognet_diag import diag, diag_exc

#: Service coordinate for everything PyTorch writes.
SERVICE = os.environ.get("FROGNET_TORCH_SERVICE", "torch")

#: How often wait()/poll loops re-read. The space is current state; there is
#: nothing to subscribe to. [ALWAYS_POLL]
POLL_S = float(os.environ.get("FROGNET_TORCH_POLL_S", "0.05"))

#: [ASK_ONCE_AND_WAIT_V1] Cap on how long the STORE may hold one read open, so
#: the loop still re-evaluates its deadline. Same knob name and default as the
#: backends use, because it is the same decision.
BLOCK_CAP = float(os.environ.get("FROGNET_BLOCK_CAP_S", "5.0"))


def _wait_for_key(store, key: str, left: float) -> None:
    """[A_TUPLE_HAS_THREE_COORDINATES_V1 - John 2026-09-14] Wait for THIS key.

    Service and var are known; scope is the writer and stays wildcarded, which
    is exactly why a reader does not need to know who will publish. One call,
    one satisfiable condition: at least one row whose SensorName starts
    SD:<key>. -- any writer.

    What this replaces: a loop on POLL_S asking whether the key had appeared
    (20 asks/second, per waiting key, per rank), and before that a wait on
    "the service has grown by one row", which any unrelated write satisfies and
    which is not the question get() asks.
    """
    T.get_all(SERVICE, dbhost=store.dbhost,
              wait_s=min(left, BLOCK_CAP), min_rows=1,
              name_like="%s%s.%%" % (T.SD_PREFIX, key))


class FrogNetStore(dist.Store):
    """c10d Store over the tuple space.

    `writer` is this participant's identity -- a node name under your address
    form, a thread id when the whole group is one process. It is the third
    coordinate of everything this instance writes, and it is why two writers
    never collide.
    """

    def __init__(self, writer: str, dbhost: Optional[str] = None,
                 timeout_s: float = 300.0) -> None:
        super().__init__()
        self.writer = str(writer)
        self.dbhost = dbhost or T.DEFAULT_DBHOST
        self._timeout_s = float(timeout_s)
        self._lock = threading.RLock()
        self._foreign_calls = 0
        diag("DIAG-TSTORE", "store open", writer=self.writer,
             dbhost=self.dbhost, service=SERVICE, poll_s=POLL_S,
             foreign_primitive_mode=FOREIGN_PRIMITIVE)

    # -- reading ----------------------------------------------------------

    def _rows(self, key: Optional[str] = None, wait_s: float = 0.0,
              min_rows: int = 0) -> List[Dict[str, Any]]:
        """Every cell for a key, across writers. `get_all` is the shipped read
        path; this only filters what it returns.

        [ASK_ONCE_AND_WAIT_V1 - John 2026-09-14] wait_s/min_rows are forwarded
        so get() and wait() can let the STORE hold the question open instead of
        re-asking every POLL_S. Opt-in: a caller that passes neither gets the
        old immediate read, so nothing that reads the space as current state
        changes behaviour.
        """
        rows = T.get_all(SERVICE, dbhost=self.dbhost,
                         wait_s=wait_s, min_rows=min_rows)
        out = []
        for r in rows:
            scope = r.get("scope", "")
            k, _, w = scope.rpartition(":")
            if key is not None and k != key:
                continue
            r = dict(r)
            r["key"], r["writer"] = k, w
            out.append(r)
        return out

    def _newest(self, key: str) -> Optional[Dict[str, Any]]:
        """The current value of a key.

        A key with one writer has one cell and this is trivial. A key written
        by several -- which is a caller still thinking in server terms --
        resolves by envelope timestamp, the store's clock, never a payload's.
        [ENVELOPE_TS_V1] It logs when it has to choose, because having to
        choose is the interesting part.
        """
        rows = self._rows(key)
        if not rows:
            return None
        if len(rows) > 1:
            self._foreign(
                "key has %d writers" % len(rows), key=key,
                writers=[r["writer"] for r in rows],
                resolution="newest envelope")
            rows.sort(key=lambda r: (r.get("ts_env") or 0))
        return rows[-1]

    def _foreign(self, what: str, **kw) -> None:
        """A caller reached for a single-server primitive. Count it, log it,
        and under strict, refuse."""
        self._foreign_calls += 1
        diag("DIAG-TSTORE", "SINGLE-SERVER PRIMITIVE: %s" % what,
             mode=FOREIGN_PRIMITIVE, n=self._foreign_calls, **kw)
        if FOREIGN_PRIMITIVE == "strict":
            raise ForeignPrimitive(
                "%s -- this primitive assumes one arbitrating server. In the "
                "tuple space each cell has one writer. Set "
                "FROGNET_TORCH_CAS=warn to allow it." % what)

    # -- the c10d Store surface -------------------------------------------

    def set(self, key: str, value) -> None:
        with self._lock:
            ok = T.put(SERVICE, key, _scope(key, self.writer), _enc(value),
                       dbhost=self.dbhost, own=False)
        diag("DIAG-TSTORE", "set", key=key, writer=self.writer, ok=ok,
             bytes=len(value) if hasattr(value, "__len__") else None)
        if not ok:
            raise RuntimeError("tuple write failed for key %r" % key)

    def get(self, key: str) -> bytes:
        # [ASK_ONCE_AND_WAIT_V1 - John 2026-09-14] Block in the store, do not
        # spin here. This looped on POLL_S (50 ms) asking whether a key had
        # appeared yet -- 20 asks a second, per waiting key, per rank, for a
        # question whose answer only changes when a peer writes.
        #
        # The condition has to be SATISFIABLE. The read returns every cell of
        # SERVICE and filters here, so "this key exists" is not something the
        # store can test; "the service has grown" is. Waiting for one more row
        # than we have seen is the same question asked once instead of
        # hundreds of times, and a write by any peer wakes it to re-check.
        deadline = time.time() + self._timeout_s
        n = 0
        while True:
            row = self._newest(key)
            if row is not None:
                diag("DIAG-TSTORE", "get", key=key, writer=row["writer"],
                     polls=n, age_s=row.get("age_s"))
                return _dec(row["value"])
            if time.time() >= deadline:
                diag("DIAG-TSTORE", "get TIMEOUT", key=key, polls=n,
                     timeout_s=self._timeout_s)
                raise RuntimeError(
                    "FrogNetStore.get(%r) timed out after %.1fs and %d polls. "
                    "Nobody has published that cell. Under "
                    "%s.<key>.<writer> the readers do not know who the writer "
                    "will be, so a key nobody writes never appears -- unlike "
                    "TCPStore, where the server would still be listening."
                    % (key, self._timeout_s, n, SERVICE))
            n += 1
            _left = deadline - time.time()
            if _left > 0:
                _wait_for_key(self, key, _left)
            else:
                time.sleep(POLL_S)

    def check(self, keys: List[str]) -> bool:
        present = {r["key"] for r in self._rows()}
        ok = all(k in present for k in keys)
        diag("DIAG-TSTORE", "check", keys=list(keys), ok=ok)
        return ok

    def wait(self, keys, timeout=None) -> None:
        deadline = time.time() + (
            timeout.total_seconds() if hasattr(timeout, "total_seconds")
            else (timeout if timeout is not None else self._timeout_s))
        n = 0
        # [ASK_ONCE_AND_WAIT_V1] Same change as get(): the store holds the
        # question until the service grows, instead of this thread asking 20
        # times a second whether the keys have shown up.
        while not self.check(list(keys)):
            if time.time() >= deadline:
                missing = [k for k in keys
                           if k not in {r["key"] for r in self._rows()}]
                diag("DIAG-TSTORE", "wait TIMEOUT", keys=list(keys),
                     missing=missing, polls=n)
                raise RuntimeError(
                    "FrogNetStore.wait timed out waiting for %r" % (missing,))
            n += 1
            _left = deadline - time.time()
            if _left > 0:
                _wait_for_key(self, key, _left)
            else:
                time.sleep(POLL_S)
        diag("DIAG-TSTORE", "wait satisfied", keys=list(keys), polls=n)

    def add(self, key: str, amount: int) -> int:
        """Read-modify-write on this writer's own cell.

        TCPStore's add is a counter shared by every rank -- that is how a
        barrier is built on it. Here it is this writer's own counter, because
        this writer owns the cell. A caller using add() to count RANKS is
        using it as a rendezvous and gets told so.
        """
        with self._lock:
            rows = self._rows(key)
            mine = [r for r in rows if r["writer"] == self.writer]
            if len(rows) > len(mine):
                self._foreign("add() on a key other writers also hold",
                              key=key, writers=[r["writer"] for r in rows],
                              note="TCPStore.add is a SHARED counter; this is "
                                   "a per-writer one")
            cur = int(_dec(mine[0]["value"]) or b"0") if mine else 0
            new = cur + int(amount)
            T.put(SERVICE, key, _scope(key, self.writer), _enc(str(new)),
                  dbhost=self.dbhost, own=False)
        diag("DIAG-TSTORE", "add", key=key, amount=amount, was=cur, now=new)
        return new

    def compare_set(self, key: str, expected, desired) -> bytes:
        """CAS, honestly, under a lock -- and logged every time.

        [THE_STORE_IS_THE_SPACE_V1] There is nothing to arbitrate when a cell
        has one writer, so any use of this is a caller that still believes in
        the server. It works; it just tells you.
        """
        self._foreign("compare_set", key=key)
        with self._lock:
            row = self._newest(key)
            cur = _dec(row["value"]) if row else b""
            exp = expected if isinstance(expected, bytes) else \
                str(expected).encode()
            if cur == exp or (not row and not exp):
                self.set(key, desired)
                diag("DIAG-TSTORE", "compare_set TOOK", key=key)
                return desired if isinstance(desired, bytes) else \
                    str(desired).encode()
            diag("DIAG-TSTORE", "compare_set REFUSED", key=key,
                 current=cur[:32], expected=exp[:32])
            return cur

    def delete_key(self, key: str) -> bool:
        """Clear this writer's cell for a key.

        Memory holds current state, not history: the write of an empty value
        IS the deletion, and a reader sees the new current value rather than
        an absence it has to interpret.
        """
        with self._lock:
            existed = any(r["writer"] == self.writer
                          for r in self._rows(key))
            T.put(SERVICE, key, _scope(key, self.writer), _enc(b""),
                  dbhost=self.dbhost, own=False)
        diag("DIAG-TSTORE", "delete_key", key=key, existed=existed)
        return existed

    def append(self, key: str, value) -> None:
        with self._lock:
            row = self._newest(key)
            cur = _dec(row["value"]) if row else b""
            v = value if isinstance(value, bytes) else str(value).encode()
            self.set(key, cur + v)
        diag("DIAG-TSTORE", "append", key=key, added=len(v))

    def multi_get(self, keys: List[str]) -> List[bytes]:
        """One read for many cells -- the batch form, native here.

        This is what get_many is for: a rank naming the peers it cares about
        instead of reading the whole service and filtering.
        """
        rows = {r["key"]: r for r in self._rows()}
        out, missing = [], []
        for k in keys:
            r = rows.get(k)
            if r is None:
                missing.append(k)
                out.append(b"")
            else:
                out.append(_dec(r["value"]))
        diag("DIAG-TSTORE", "multi_get", n=len(keys), missing=missing)
        if missing:
            raise RuntimeError("multi_get: no cell for %r" % missing)
        return out

    def multi_set(self, keys: List[str], values: List) -> None:
        for k, v in zip(keys, values):
            self.set(k, v)
        diag("DIAG-TSTORE", "multi_set", n=len(keys))

    def num_keys(self) -> int:
        n = len({r["key"] for r in self._rows()})
        diag("DIAG-TSTORE", "num_keys", n=n)
        return n

    def list_keys(self) -> List[str]:
        ks = sorted({r["key"] for r in self._rows()})
        diag("DIAG-TSTORE", "list_keys", n=len(ks))
        return ks

    def set_timeout(self, timeout) -> None:
        self._timeout_s = (timeout.total_seconds()
                           if hasattr(timeout, "total_seconds")
                           else float(timeout))
        diag("DIAG-TSTORE", "set_timeout", timeout_s=self._timeout_s)

    # -- the queue API ----------------------------------------------------
    #
    # Same story as compare_set: a queue is a server's structure. It is
    # implemented on this writer's own cell, and every call is logged.

    def queue_push(self, key: str, value) -> None:
        self._foreign("queue_push", key=key)
        with self._lock:
            row = self._newest(key)
            q = json.loads(_dec(row["value"]) or b"[]") if row else []
            q.append((value if isinstance(value, str)
                      else bytes(value).decode("latin-1")))
            self.set(key, json.dumps(q))

    def queue_pop(self, key: str, block: bool = True) -> bytes:
        self._foreign("queue_pop", key=key)
        deadline = time.time() + self._timeout_s
        while True:
            with self._lock:
                row = self._newest(key)
                q = json.loads(_dec(row["value"]) or b"[]") if row else []
                if q:
                    head = q.pop(0)
                    self.set(key, json.dumps(q))
                    return head.encode("latin-1")
            if not block or time.time() >= deadline:
                raise RuntimeError("queue_pop(%r): empty" % key)
            # [ASK_ONCE_AND_WAIT_V1 - John 2026-09-14] queue_pop is a blocking
            # read like the others. A producer's push grows the service, which
            # is the condition the store can test -- unlike "this queue has an
            # element", which lives inside a value. It wakes on any write and
            # re-checks, which is correct and cheap; the alternative was 20
            # asks a second per waiting consumer.
            _left = deadline - time.time()
            if _left > 0:
                _wait_for_key(self, key, _left)
            else:
                time.sleep(POLL_S)

    def queue_len(self, key: str) -> int:
        row = self._newest(key)
        return len(json.loads(_dec(row["value"]) or b"[]")) if row else 0

    def has_extended_api(self) -> bool:
        return True

    # -- what a server cannot do ------------------------------------------

    def participants(self, key: str) -> List[str]:
        """Every writer currently publishing a key.

        There is no TCPStore equivalent. World size is not configured here, it
        is observed: whoever is publishing is who is present. A rank that
        stopped is a rank that stopped appearing, and the readers notice
        because they are reading -- no collective has to throw.
        """
        ws = sorted({r["writer"] for r in self._rows(key)})
        diag("DIAG-TSTORE", "participants", key=key, n=len(ws), writers=ws)
        return ws

    def foreign_call_count(self) -> int:
        """How many times this run reached for a single-server primitive. A
        workflow that ports cleanly ends at zero."""
        return self._foreign_calls
