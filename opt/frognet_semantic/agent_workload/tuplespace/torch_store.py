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

[THE_STORE_IS_THE_SPACE_V1], rebuilt on FrogTorch's own RAM ([ALL_IN_THE_REGION_V1], 2026-11-11).

torch.distributed's TCPStore is a key-value server hosted on rank 0, and every other rank opens a connection to it
and asks. Given memory every rank can see, that server is not a component to reimplement; it stops being necessary.
This is a c10d Store whose backing is the FrogTorch control region. A key is an address, a value is a cell, and
reading is reading: no rank 0, no master address, no rendezvous.

    c10d key  ->  service torch.store.<namespace>, variable <key>, instance <writer>

  set / get / check / wait          the writer's own cell; get and wait are ONE held read each, never re-asked
  add                               a partitioned counter: each writer keeps its own running total in its own
                                    cell, and the value is the sum. No two writers ever write one cell.
  delete_key / num_keys / list_keys read or remove what is there
  multi_get / multi_set / append    the batch forms
  compare_set, queue_*              the two primitives that are not "write your own cell". They are the region's
                                    own operations, done in one call against the memory itself, not by a
                                    conversation between ranks.

`frognet://<namespace>` is registered as a rendezvous scheme, so init_process_group(init_method="frognet://run7")
needs no MASTER_ADDR, no MASTER_PORT and no TCPStore.
"""
from __future__ import annotations

import base64
import datetime
import os
import threading
from typing import Dict, List, Optional

import torch.distributed as dist

from . import frogtorch_ram_client as R

BUILD = "torch_store 20261111-r1 [ALL_IN_THE_REGION_V1]"
SERVICE = os.environ.get("FROGNET_TORCH_SERVICE", "torch")
DEFAULT_TIMEOUT_S = float(os.environ.get("FROGNET_TORCH_STORE_TIMEOUT_S", "300"))


def _enc(value) -> str:
    if isinstance(value, str):
        value = value.encode()
    return base64.b64encode(bytes(value)).decode()


def _dec(s: str) -> bytes:
    return base64.b64decode(s.encode())


class FrogNetStore(dist.Store):
    def __init__(self, writer: str, dbhost: Optional[str] = None, timeout_s: float = DEFAULT_TIMEOUT_S,
                 namespace: str = "default") -> None:
        super().__init__()
        self.writer = str(writer)
        self.svc = "%s.store.%s" % (SERVICE, namespace)
        self._timeout_s = float(timeout_s)
        self._ram = R.ram()
        self._totals: Dict[str, int] = {}       # this writer's own running totals, by counter key
        self._lock = threading.Lock()

    # ---- reading ------------------------------------------------------------------------------------------
    def _rows(self, key: Optional[str] = None, wait_s: float = 0.0, min_rows: int = 0):
        return self._ram.get_all(self.svc, var=key, wait_s=wait_s, min_rows=min_rows)

    def _value(self, rows) -> Optional[bytes]:
        """A plain key: the newest write. A counter (written by add): the sum of every writer's total."""
        if not rows:
            return None
        if all(isinstance(r["value"], dict) and "count" in r["value"] for r in rows):
            return str(sum(int(r["value"]["count"]) for r in rows)).encode()
        newest = max(rows, key=lambda r: r["id"])["value"]
        if isinstance(newest, dict) and "count" in newest:
            return str(int(newest["count"])).encode()
        return _dec(newest)

    # ---- the Store surface ----------------------------------------------------------------------------------
    def set(self, key: str, value) -> None:
        self._ram.put(self.svc, key, self.writer, _enc(value))

    def get(self, key: str) -> bytes:
        rows = self._rows(key, wait_s=self._timeout_s, min_rows=1)       # [ONE_HELD_READ_V1]
        v = self._value(rows)
        if v is None:
            raise RuntimeError("FrogNetStore: key %r was not written within %.0fs" % (key, self._timeout_s))
        return v

    def check(self, keys: List[str]) -> bool:
        return all(self._rows(k) for k in keys)

    def wait(self, keys, timeout=None) -> None:
        t = self._timeout_s if timeout is None else (timeout.total_seconds()
                                                     if isinstance(timeout, datetime.timedelta) else float(timeout))
        for k in keys:
            if not self._rows(k, wait_s=t, min_rows=1):
                raise RuntimeError("FrogNetStore: wait for %r timed out after %.0fs" % (k, t))

    def add(self, key: str, amount: int) -> int:
        with self._lock:
            mine = self._totals.get(key, 0) + int(amount)
            self._totals[key] = mine
            self._ram.put(self.svc, key, self.writer, {"count": mine})
        return int(self._value(self._rows(key)) or b"0")

    def compare_set(self, key: str, expected, desired) -> bytes:
        exp = None if expected in (None, b"", "") else _enc(expected)
        v = self._ram.op("ccas", service=self.svc, var=key, expected=exp, desired=_enc(desired))["value"]
        return b"" if v is None else _dec(v)

    def delete_key(self, key: str) -> bool:
        with self._lock:
            self._totals.pop(key, None)
        return self._ram.op("cdelvar", service=self.svc, var=key)["removed"] > 0

    def append(self, key: str, value) -> None:
        rows = self._rows(key)
        cur = self._value(rows) or b""
        self.set(key, cur + (value.encode() if isinstance(value, str) else bytes(value)))

    def multi_get(self, keys: List[str]) -> List[bytes]:
        return [self.get(k) for k in keys]

    def multi_set(self, keys: List[str], values: List) -> None:
        for k, v in zip(keys, values):
            self.set(k, v)

    def list_keys(self) -> List[str]:
        return sorted({r["var"] for r in self._rows()})

    def num_keys(self) -> int:
        return len(self.list_keys())

    def set_timeout(self, timeout) -> None:
        self._timeout_s = timeout.total_seconds() if isinstance(timeout, datetime.timedelta) else float(timeout)

    def queue_push(self, key: str, value) -> None:
        self._ram.op("qpush", service=self.svc, var=key, value=_enc(value))

    def queue_pop(self, key: str, block: bool = True) -> bytes:
        r = self._ram.op("qpop", service=self.svc, var=key, wait_s=self._timeout_s if block else 0.0)
        if not r.get("ok"):
            raise RuntimeError("FrogNetStore: queue %r is empty" % key)
        return _dec(r["value"])

    def queue_len(self, key: str) -> int:
        return int(self._ram.op("qlen", service=self.svc, var=key)["n"])

    def has_extended_api(self) -> bool:
        return True

    def participants(self, key: str) -> List[str]:
        """Every writer currently holding a cell under a key: who is present is observed, not configured."""
        return sorted({r["scope"] for r in self._rows(key)})

    def foreign_call_count(self) -> int:
        return 0


def _rendezvous(url: str, timeout=datetime.timedelta(seconds=DEFAULT_TIMEOUT_S), **kwargs):
    """frognet://<namespace>?rank=R&world_size=W -- what init_process_group gets in place of a TCPStore."""
    from urllib.parse import parse_qs, urlparse
    u = urlparse(url)
    q = {k: v[-1] for k, v in parse_qs(u.query).items()}
    rank = int(q.get("rank", kwargs.get("rank", -1)))
    world = int(q.get("world_size", kwargs.get("world_size", -1)))
    if rank < 0 or world < 1:
        raise ValueError("frognet:// needs rank and world_size (in the URL or from init_process_group)")
    ns = (u.netloc + u.path).strip("/") or "default"
    store = FrogNetStore("r%d" % rank, timeout_s=timeout.total_seconds(), namespace=ns)
    yield (store, rank, world)
    raise RuntimeError("frognet:// does not support re-rendezvous")


def register() -> None:
    from torch.distributed.rendezvous import register_rendezvous_handler
    try:
        register_rendezvous_handler("frognet", _rendezvous)
    except RuntimeError:
        pass                                   # already registered in this process


register()
