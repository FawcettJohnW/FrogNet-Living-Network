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
agent_workload/tuplespace/frogtorch.py -- the calls a training loop makes.

[LIBRARY_CALLS_V1]

The point of the comparison is code complexity, so this is the surface. What a
stock DDP script needs, against what this needs.

STOCK
    os.environ["MASTER_ADDR"] = ...          a machine that must be up first
    os.environ["MASTER_PORT"] = ...          a port that must be free
    os.environ["RANK"] = ...                 an identity assigned externally
    os.environ["WORLD_SIZE"] = ...           a count fixed before anyone starts
    dist.init_process_group("gloo")          a barrier: nobody proceeds alone
    model = DistributedDataParallel(model)   hooks on every gradient
    ...                                      loss.backward() blocks on allreduce
    dist.destroy_process_group()             and a launcher to start them all

THIS
    w = frogtorch.worker("seattle5")         who I am. Nothing waits.
    ...
    g = w.reduce("grad", grads, step=n)      publish mine, read theirs, reduce
    ...
    w.retire()                               stop publishing

There is no world size because nobody is counted, no rank because nobody is
ordered, no master because nobody is asked, and no init because there is
nothing to join. A worker that starts late starts. A worker that stops stops.

Everything below is a thin arrangement of ReduceGroup and FrogNetStore. It is
here so the training loop reads like a training loop.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core import frognet_tuples as T

from .finite import FiniteWatch
from .reduce_by_read import ReduceGroup, default_accept
from .tensor_plane import TensorClient, TensorServer
from .torch_store import FrogNetStore, SERVICE

# [IMPORT_GUARD_V2] 2026-09-25: diag is first-party and the tree is always on the path; a stub hid a broken install.
from core.frognet_diag import diag

def this_worker() -> str:
    """A default identity: node name, thread if there is more than one.

    Under <service>.<variable>.<instance> the instance is a NAME, not an
    address -- a node has several addresses and one identity, and a tuple
    naming the address goes stale when the address moves.
    """
    node = os.environ.get("FROGNET_NODE") or socket.gethostname().split(".")[0]
    t = threading.current_thread()
    return node if t is threading.main_thread() else "%s-%s" % (node, t.name)


class Worker:
    """One participant. Threads in one process or processes across the mesh --
    the code is the same, which is the point of the model."""

    def __init__(self, name: Optional[str] = None,
                 dbhost: Optional[str] = None,
                 max_lag: Optional[int] = None,
                 accept: Optional[Callable] = None,
                 data_plane: bool = False,
                 bind_ip: str = "0.0.0.0",
                 advertise_host: str = "127.0.0.1",
                 payload_mode: Optional[str] = None):
        self.name = name or this_worker()
        self.dbhost = dbhost or T.DEFAULT_DBHOST
        self.accept = accept or (
            (lambda mine, theirs, age: default_accept(mine, theirs, age,
                                                      max_lag))
            if max_lag is not None else None)
        self.payload_mode = payload_mode
        #: [FIRST_NON_FINITE_V1] ONE latch per worker, shared by the data
        #: plane, every reduction group and the training loop. Separate
        #: watches per module would each know their own stage and none of
        #: them the order, and the order is the whole question.
        self.watch = FiniteWatch(self.name)
        #: [THE_DESCRIPTOR_HAS_A_DATA_PLANE_V1] With data_plane=True this
        #: worker serves its tensors on a permanent socket and reads peers'
        #: over one, and the space carries only descriptors. Without it, the
        #: tensors travel in the cells, which is honest for small ones and is
        #: the only mode that works before a plane exists.
        self.server = (TensorServer(bind_ip, watch=self.watch)
                       if data_plane else None)
        self.client = (TensorClient(self.name, watch=self.watch)
                       if data_plane else None)
        self.advertise_host = advertise_host
        self._groups: Dict[str, ReduceGroup] = {}
        self._store: Optional[FrogNetStore] = None
        self._lock = threading.RLock()
        diag("DIAG-FROGTORCH", "worker", name=self.name, dbhost=self.dbhost,
             data_plane=("port=%d" % self.server.port) if self.server
             else "none")

    # -- the reduction ----------------------------------------------------

    def group(self, name: str) -> ReduceGroup:
        with self._lock:
            g = self._groups.get(name)
            if g is None:
                g = ReduceGroup(name, self.name, dbhost=self.dbhost,
                                accept=self.accept,
                                payload_mode=self.payload_mode,
                                server=self.server, client=self.client,
                                server_host=self.advertise_host,
                                watch=self.watch)
                self._groups[name] = g
            return g

    def publish(self, name: str, tensor, step: Optional[int] = None):
        """Make this worker's contribution readable. Returns immediately."""
        return self.group(name).publish(tensor, step=step)

    def reduce(self, name: str, tensor, step: Optional[int] = None,
               op: str = "mean", report: bool = False):
        """Publish mine, read the current set, reduce locally.

        The one call a training loop makes where a stock script calls
        allreduce -- except that this one does not block on anybody. Pass
        report=True to get the (tensor, report) pair; the report says which
        workers were used, which were dropped and how far behind each was.
        """
        g = self.group(name)
        g.publish(tensor, step=step)
        out, rep = g.reduce(step=step, op=op, my_tensor=tensor)
        return (out, rep) if report else out

    def read(self, name: str, step: Optional[int] = None, op: str = "mean"):
        """Reduce what others have published without publishing anything.

        For a participant that consumes but does not contribute -- an
        evaluator, a checkpointer, a model serving inference off the current
        parameters. It is a reader like any other; nothing has to be told.
        """
        return self.group(name).reduce(step=step, op=op, include_self=False)

    def peers(self, name: str) -> List[str]:
        """Who is currently publishing this reduction. World size, observed."""
        return self.group(name).workers()

    # -- the control plane ------------------------------------------------

    @property
    def store(self) -> FrogNetStore:
        """A c10d Store, for the parts of PyTorch that still want one."""
        with self._lock:
            if self._store is None:
                self._store = FrogNetStore(self.name, dbhost=self.dbhost)
            return self._store

    def announce(self, _kind: str = "worker", **facts) -> None:
        """Publish current state about this worker, by KIND.

        [ONE_FACT_ONE_ADDRESS_V1]

        This wrote every announced fact into a single bag at
        `worker:<name>`, and `put` replaces. So:

            w.announce(device="cpu", cores=8)
            w.announce(shard=3)
            roster()["w0"]  ->  {"shard": 3}

        The device and core count are gone. The docstring invited exactly
        that by listing "device, batch size, dataset shard, model hash" as
        things to announce, which are four different kinds of fact with four
        different lifetimes.

        Two consequences, and the second is worse than the first. A fact is
        destroyed by announcing an unrelated one. And every fact inherits
        the age of the most recent write, so a static capability looks as
        fresh as a per-step shard assignment -- which makes `age_s` useless
        for deciding whether a capability is still true.

        Facts now live under a kind, and a kind is rewritten as a whole.
        `announce(device=...)` and `announce("progress", step=...)` do not
        touch each other and age independently. `roster()` merges them, so
        a reader that wants everything still gets everything -- with each
        kind's own age rather than one age for all of it.
        """
        T.put(SERVICE, "worker", "worker:%s:%s" % (self.name, _kind),
              dict(facts), dbhost=self.dbhost, own=False)
        diag("DIAG-FROGTORCH", "announce", worker=self.name, kind=_kind,
             facts=facts)

    def roster(self) -> Dict[str, Dict[str, Any]]:
        """Everything every worker has announced, with how old it is."""
        out: Dict[str, Dict[str, Any]] = {}
        for r in T.get(SERVICE, "worker", dbhost=self.dbhost):
            sc = r.get("scope", "")
            parts = sc.split(":")
            # worker:<name>:<kind>, and worker:<name> from anything written
            # before kinds existed.
            if len(parts) >= 3:
                w, kind = parts[1], parts[2]
            elif len(parts) == 2:
                w, kind = parts[1], "worker"
            else:
                continue
            v = dict(r.get("value") or {})
            # [ONE_FACT_ONE_ADDRESS_V1] Each kind keeps its OWN age. Merging
            # them under one `age_s` is what made a static capability look
            # as fresh as a per-step fact.
            v["age_s"] = r.get("age_s")
            slot = out.setdefault(w, {})
            slot.setdefault("_kinds", {})[kind] = v
            for k, val in v.items():
                if k != "age_s":
                    slot[k] = val
            # the freshest kind's age, for callers that just want one number
            ages = [d.get("age_s") for d in slot["_kinds"].values()
                    if d.get("age_s") is not None]
            slot["age_s"] = min(ages) if ages else None
        return out

    def wait_for(self, name: str, n: int, timeout: float = 60.0) -> List[str]:
        """Block until n workers are publishing `name`.

        The one place a barrier is honest: at the start of a demonstration you
        may want everyone present before measuring. It is a POLL of current
        state, not a rendezvous -- nobody is waiting on this worker, and a
        worker that never arrives costs only this caller's timeout.
        """
        deadline = time.time() + timeout
        while True:
            ws = self.peers(name)
            if len(ws) >= n:
                diag("DIAG-FROGTORCH", "wait_for satisfied", name=name,
                     want=n, have=len(ws), workers=ws)
                return ws
            if time.time() >= deadline:
                raise TimeoutError(
                    "wait_for(%r, %d): only %d publishing after %.0fs: %s"
                    % (name, n, len(ws), timeout, ws))
            time.sleep(0.05)

    def retire(self) -> None:
        """Stop participating. Publishes the fact rather than vanishing."""
        for g in list(self._groups.values()):
            g.retire()
        # [ONE_FACT_ONE_ADDRESS_V1] Retirement is a lifecycle fact and gets
        # its own address. This wrote {"retired": True} as the WHOLE worker
        # bag, so retiring erased every fact the worker had announced --
        # device, shard, model hash, all of it -- and a reader asking "what
        # was this worker running?" got nothing but the tombstone.
        #
        # Writing it as its own kind means retirement no longer competes
        # with what the worker said about itself, and both keep their own
        # age: a peer can see that the worker retired ten seconds ago AND
        # what it was doing when it did.
        self.announce("lifecycle", retired=True)
        diag("DIAG-FROGTORCH", "retire", worker=self.name,
             groups=list(self._groups), plane=self.plane_stats())
        if self.client is not None:
            self.client.close()
        if self.server is not None:
            self.server.close()

    def plane_stats(self) -> Dict[str, Any]:
        """What the data plane actually moved. `reduction` is bytes offered
        over bytes sent -- the SAME suppression, measured rather than
        claimed."""
        if self.server is None:
            return {"data_plane": "none"}
        return {"server": self.server.stats(), "client": self.client.stats()}

    def stats(self) -> Dict[str, Any]:
        s = {"worker": self.name, "groups": {}, "plane": self.plane_stats(),
             # [FIRST_NON_FINITE_V1] Reported on every run, not only a bad
             # one: "nothing went non-finite" is a result and has to be
             # stated, or its absence reads as nobody having looked.
             "finite": self.watch.summary()}
        # [A_DEPARTURE_IS_AN_OBSERVATION_V1] One observer's account of every
        # peer it stopped being able to use, and what was true at the moment
        # it noticed. Reported whether or not anybody left.
        s["departures"] = []
        for n, g in self._groups.items():
            s["groups"][n] = dict(g.stats)
            s["departures"].extend(g.departure_report())
        if self._store is not None:
            s["store_single_server_calls"] = self._store.foreign_call_count()
        return s

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.retire()
        return False


def worker(name: Optional[str] = None, **kw) -> Worker:
    """The whole setup. No master, no port, no rank, no world size."""
    return Worker(name, **kw)
