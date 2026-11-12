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
agent_workload/tuplespace/frogtrain.py -- training as shared memory.

[MEMORY_IS_NOT_A_TRANSPORT_V1]

torch_store and reduce_by_read replaced two COMPONENTS of the message model.
This replaces the MODEL. Every part of the distributed training flow exists
because processes cannot see each other's memory, and given memory they can
see, most of it does not become cheaper -- it becomes absent.

  WHAT THE MESSAGE MODEL NEEDS            WHY, AND WHAT REPLACES IT
  ------------------------------------    ------------------------------------
  rendezvous, MASTER_ADDR, MASTER_PORT    so ranks can find each other.
                                          Nothing: a worker publishes what it
                                          is and reads what the others are.

  RANK and WORLD_SIZE                     so collectives know their shape and
                                          so data can be partitioned by index.
                                          Nothing: identity is a name, and
                                          the fabric's width is whoever is
                                          currently publishing.

  broadcast of the initial parameters     because rank 0 holds the only copy.
    (DDP.__init__ mentions broadcast 16   Nothing: the initial parameters are
     times)                               a cell. Whoever arrives reads it.

  gradient BUCKETING                      to amortise per-message overhead.
    (34 mentions)                         Nothing: there is no message to
                                          amortise. State is written once and
                                          read by whoever wants it.

  overlapping comm with backward          to hide message latency behind
                                          compute. Nothing to hide: a publish
                                          is a write.

  the implicit barrier in allreduce       because a collective is not complete
                                          until every rank arrives. Nothing:
                                          nobody waits for anybody.

  DistributedSampler                      so ranks do not duplicate data,
                                          partitioned by rank/world computed
                                          BEFORE anyone starts. Replaced by
                                          CLAIMING: a worker takes a shard
                                          from shared state, and a shard whose
                                          claimant went quiet is reclaimed by
                                          someone else. Elasticity falls out;
                                          it is not a feature.

  checkpoint written by rank 0            because there is one writer and no
                                          shared state. Replaced by: the
                                          parameter cell IS the checkpoint,
                                          and resuming is reading it. A
                                          worker joining late and a worker
                                          resuming after a crash are the same
                                          code path.

  destroy_process_group                   to tear the group down. Nothing: a
                                          worker stops publishing.

MUTUAL EXCLUSION WITHOUT ATOMICS
--------------------------------
Claiming a shard looks like it needs a compare-and-swap, and it does not.
CAS exists because a single server must arbitrate contenders. Here every cell
has exactly ONE writer -- its own worker -- so nobody can be raced.

    each worker writes ONLY its own intent:  train.claim.<worker> = {shard}
    each worker reads every intent
    every worker applies the SAME deterministic rule to the same data

The rule is: for a contested shard, the claim with the earliest store
envelope wins, ties broken by worker name. Every reader computes the same
answer from the same cells without asking anyone. A loser re-picks. Duplicated
work is possible for one poll interval and is wasteful rather than wrong,
which is the trade a shared-memory model makes instead of a lock.

WHAT "FUNCTIONS LIKE THE ORIGINAL" MEANS HERE
---------------------------------------------
Same model, same data, same total work, same final quality -- reached without
any of the machinery above. It is not the same implementation and is not
meant to be.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import zlib
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import frogtorch_ram_client as T  # [ALL_IN_THE_REGION_V1] FrogTorch's own RAM

from .reduce_by_read import ReduceGroup, default_accept
from .tensor_plane import TensorClient

# [IMPORT_GUARD_V2] 2026-09-25: diag is first-party and the tree is always on the path; a stub hid a broken install.
from core.frognet_diag import diag, diag_exc

SERVICE = os.environ.get("FROGNET_TRAIN_SERVICE", "train")

#: A claim older than this, with no progress published, is treated as
#: abandoned and may be taken. It is not a lease and there is nothing to
#: renew: the worker republishes its claim as it works, so the AGE of the
#: cell is the liveness signal. A worker that died stops refreshing and its
#: shard becomes available without anyone being notified.
CLAIM_STALE_S = float(os.environ.get("FROGNET_TRAIN_CLAIM_STALE_S", "10"))

POLL_S = float(os.environ.get("FROGNET_TRAIN_POLL_S", "0.2"))


# ---------------------------------------------------------------------------
# The shared state. Four kinds of cell, each with exactly one writer.
# ---------------------------------------------------------------------------
#
#   train.run.<run>          the job. Written by whoever arrives first and
#                            read by everyone after. Not a master -- just the
#                            first writer of a cell nobody had written.
#   train.claim.<worker>     what this worker is working on. One writer.
#   train.progress.<worker>  what this worker has finished. One writer.
#   train.params.<worker>    this worker's current parameters. One writer.
#   train.ckpt.<run>         the checkpoint. Last write wins, because current
#                            state is exactly what a checkpoint is.


def _put(var, scope, value, dbhost):
    return T.put(SERVICE, var, scope, value, dbhost=dbhost, own=False)


def _rows(var, dbhost):
    out = []
    for r in T.get(SERVICE, var, dbhost=dbhost):
        scope = r.get("scope", "")
        _, _, who = scope.rpartition(":")
        out.append((who, r.get("value") or {}, r.get("age_s"),
                    r.get("ts_env") or 0))
    return out


class Job:
    """The job as a cell. There is no master and no rank 0.

    The first worker to look finds nothing and writes it; every worker after
    finds it and uses it. If two write at once they write the SAME content --
    the job description is derived from the caller's arguments, not from who
    got there first -- so the race is not a race.
    """

    def __init__(self, run: str, spec: Dict[str, Any],
                 dbhost: Optional[str] = None):
        self.run = run
        self.dbhost = dbhost or T.DEFAULT_DBHOST
        existing = self.read()
        if existing is None:
            _put("run", "run:%s" % run, spec, self.dbhost)
            diag("DIAG-TRAIN", "job created", run=run, spec=spec)
            self.spec = spec
        else:
            self.spec = existing
            if existing != spec:
                diag("DIAG-TRAIN", "job ALREADY DEFINED and differs",
                     run=run, mine=spec, theirs=existing)
        self.shards = int(self.spec["shards"])

    def read(self) -> Optional[Dict[str, Any]]:
        for who, v, _age, _ts in _rows("run", self.dbhost):
            if who == self.run:
                return v
        return None


class Claims:
    """Shard assignment by declaration and deterministic resolution.

    No CAS, no lock, no arbiter. A worker writes what it intends, reads what
    everyone else intends, and resolves contention with a rule every reader
    computes identically.
    """

    def __init__(self, worker: str, dbhost: str):
        self.worker = worker
        self.dbhost = dbhost
        self.mine: Optional[int] = None
        #: [A_CELL_CANNOT_BE_READ_IN_TWO_DIRECTIONS_V1] The last shard this
        #: worker wrote into its claim cell. The cell is rewritten only when
        #: this changes -- see declare().
        self._declared: Any = "unset"

    def declare(self, shard: Optional[int], done: List[int]) -> None:
        """Publish this worker's intent and its finished work. One writer.

        [A_CELL_CANNOT_BE_READ_IN_TWO_DIRECTIONS_V1]
        The claim cell used to be rewritten on every call, because the
        training loop calls this every step to prove the worker is alive. The
        age of a cell IS the liveness signal, so that was the right instinct
        and the wrong cell: arbitration resolves a contested shard by EARLIEST
        envelope over the same field. Refreshing moved the owner's envelope
        later, and an owner that proved it was working handed its shard to a
        later claimant -- deterministically, and agreed by every reader:

            t+0  alpha declares shard 0
            t+2  beta declares shard 0     -> owner alpha
            t+4  alpha refreshes           -> owner BETA

        take() had already returned by then and never re-surveys, so both
        workers trained the same shard.

        One field cannot mean "how long ago this worker was seen" and "how
        long this worker has held this shard" at once. So the two signals
        are separated onto the two cells that already exist. The claim cell
        carries INTENT and is written only when the intent changes, which
        makes its envelope the time the claim began and stable under any
        amount of refreshing. The progress cell carries LIVENESS and is
        written every call, which is what the caller was asking for.
        """
        if shard != self._declared:
            _put("claim", "claim:%s" % self.worker,
                 {"shard": shard, "ts": int(time.time())}, self.dbhost)
            self._declared = shard
            diag("DIAG-TRAIN", "claim written", worker=self.worker,
                 shard=shard)
        _put("progress", "progress:%s" % self.worker,
             {"done": sorted(done)}, self.dbhost)

    def survey(self) -> Tuple[Dict[int, str], set]:
        """Who owns what, and what is finished. [ALL_IN_THE_REGION_V1] Ownership is the shard's owner cell, set by
        compare-and-set and held by its owner's session; a worker that left has no cell. Finished work is every
        worker's own progress cell. Nothing here is judged by age."""
        owner: Dict[int, str] = {}
        done: set = set()
        for r in T.get_all(SERVICE, wait_s=0.0):
            v = r.get("var", "")
            if v.startswith("owner.s"):
                owner[int(v[len("owner.s"):])] = r.get("value")
            elif v == "progress":
                done.update(int(x) for x in (r.get("value") or {}).get("done", []))
        return owner, done

    def take(self, n_shards: int, my_done: List[int]) -> Optional[int]:
        """Claim a shard, or None when there is nothing left to do.

        [ALL_IN_THE_REGION_V1] A claim is the region's own compare-and-set on the shard's owner cell, held by this
        worker's session: whoever sets it first owns it, every later claimant is told who did, and the cell goes
        when its owner's session goes -- which frees the shard. Nothing waits for other declarations to land, and
        nothing judges liveness by a cell's age.
        """
        _owner, done = self.survey()
        free = [s for s in range(n_shards) if s not in done]
        if not free:
            self.mine = None
            self.declare(None, my_done)
            return None
        start = zlib.crc32(self.worker.encode()) % len(free)
        for sh in free[start:] + free[:start]:
            got = T.ram().op("ccas", service=SERVICE, var="owner.s%d" % sh, expected=None,
                             desired=self.worker, life="session")["value"]
            if got == self.worker:
                self.declare(sh, my_done)
                self.mine = sh
                diag("DIAG-TRAIN", "claimed", shard=sh, worker=self.worker)
                return sh
        self.mine = None
        self.declare(None, my_done)
        return None

    def wait_change(self, timeout_s: float) -> None:
        """Hold until any claim or progress cell changes: an owner finishing or leaving is what frees work."""
        ids = [r["id"] for r in T.get_all(SERVICE, wait_s=0.0)]
        T.get_all(SERVICE, wait_s=timeout_s, unlike=ids)


class Trainer:
    """A participant. It has no rank, joins nothing, and waits for nobody.

    Construct it, call `work()`, and it does whatever remains. Start a second
    one at any time and it joins. Kill one and its shard is picked up. Resume
    after a crash and it reads the checkpoint -- which is the same code path
    as joining, because they are the same thing.
    """

    def __init__(self, name: Optional[str] = None, run: str = "default",
                 spec: Optional[Dict[str, Any]] = None,
                 dbhost: Optional[str] = None,
                 data_plane: bool = False, advertise_host: str = "127.0.0.1",
                 max_lag: int = 32):
        self.name = name or socket.gethostname().split(".")[0]
        self.dbhost = dbhost or T.DEFAULT_DBHOST
        self.job = Job(run, spec or {"shards": 8}, self.dbhost)
        self.claims = Claims(self.name, self.dbhost)
        # [ALL_IN_THE_REGION_V1] Parameters go into this machine's FrogTorch region; this process serves nothing.
        self.server = None
        if data_plane:
            from .frogtorch_ram_client import RegionPlane
            _ram = T.ram()
            self.server = RegionPlane(_ram, self.name)
            self.server.region = _ram.local_region(self.name)
            self.server.port = self.server.region[1]
            advertise_host = self.server.region[0]
        self.client = TensorClient(self.name) if data_plane else None
        self.params = ReduceGroup(
            "params", self.name, dbhost=self.dbhost,
            accept=lambda a, b, c: default_accept(a, b, c, max_lag),
            payload_mode=("descriptor" if data_plane else "value"),
            server=self.server, client=self.client,
            server_host=advertise_host)
        self.done: List[int] = []
        self.stats = {"shards": 0, "steps": 0, "reconciled": 0,
                      "reclaimed": 0, "resumed_from_checkpoint": False}
        diag("DIAG-TRAIN", "trainer", worker=self.name, run=run,
             shards=self.job.shards,
             data_plane=("port=%d" % self.server.port) if self.server
             else "none")

    # -- the checkpoint IS the state ---------------------------------------

    def checkpoint(self, vec) -> None:
        _put("ckpt", "ckpt:%s" % self.job.run,
             {"w": [float(x) for x in vec], "by": self.name,
              "ts": int(time.time())}, self.dbhost)

    def read_checkpoint(self):
        for who, v, _age, _ts in _rows("ckpt", self.dbhost):
            if who == self.job.run and v.get("w"):
                return v["w"], v.get("by")
        return None, None

    # -- the loop ----------------------------------------------------------

    def work(self, train_shard: Callable, get_params: Callable,
             set_params: Callable, on_epoch_end: Optional[Callable] = None,
             max_seconds: float = 600.0) -> Dict[str, Any]:
        """Do whatever is left.

        train_shard(shard, publish) -> runs one shard, calling publish(vec)
        as often as it likes. get_params()/set_params(vec) move the model.

        There is no epoch barrier. A worker finishes when the shared state
        says every shard is done -- which it observes, rather than being told.
        """
        ck, by = self.read_checkpoint()
        if ck is not None:
            set_params(ck)
            self.stats["resumed_from_checkpoint"] = True
            diag("DIAG-TRAIN", "resumed", worker=self.name, from_=by)

        deadline = time.time() + max_seconds
        while time.time() < deadline:
            shard = self.claims.take(self.job.shards, self.done)
            if shard is None:
                _owner, done = self.claims.survey()
                if len(done) >= self.job.shards:
                    diag("DIAG-TRAIN", "all shards accounted for",
                         worker=self.name, done=len(done))
                    break
                # Someone else holds the rest. Keep reconciling so a peer
                # that dies is noticed -- and so this worker's parameters
                # keep converging with theirs rather than going stale.
                self._reconcile(get_params, set_params)
                self.claims.wait_change(max(0.0, deadline - time.time()))   # [ONE_HELD_READ_V1] not a poll
                continue

            if shard in self.done:
                continue
            self.stats["shards"] += 1

            def publish(vec, step):
                self.claims.declare(shard, self.done)   # refresh liveness
                out = self.params.reduce(step=step, op="mean",
                                         my_tensor=vec)[0]
                self.stats["steps"] += 1
                self.stats["reconciled"] += 1
                return out

            train_shard(shard, publish)
            self.done.append(shard)
            self.claims.declare(None, self.done)
            self.checkpoint(get_params())
            if on_epoch_end:
                on_epoch_end(shard)

        self.retire()
        return dict(self.stats)

    def _reconcile(self, get_params, set_params):
        try:
            out, _ = self.params.reduce(step=self.stats["steps"], op="mean",
                                        my_tensor=get_params())
            set_params(out)
            self.stats["reconciled"] += 1
        except RuntimeError:
            pass          # nothing to reconcile with yet; not an error

    def peers(self) -> List[str]:
        return self.params.workers()

    def retire(self) -> None:
        self.claims.declare(None, self.done)
        self.params.retire()
        if self.client:
            self.client.close()
        if self.server:
            self.server.close()
        diag("DIAG-TRAIN", "retired", worker=self.name, stats=self.stats)
