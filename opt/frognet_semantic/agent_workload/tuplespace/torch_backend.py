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
agent_workload/tuplespace/torch_backend.py -- FrogNet as a torch.distributed
backend, so PyTorch's own suite can be run against it.

[THE_SUITE_TESTS_THE_INTERFACE_NOT_THE_MODEL_V1]

PyTorch's distributed tests -- test_c10d_gloo.py (131 tests) and
torch.testing._internal.distributed.distributed_test.py (315, of which 71
are DDP-level) -- are conformance tests for the process-group interface.
They call allreduce, broadcast, allgather, scatter, gather, barrier, and
construct AllreduceOptions and ReduceOp.

FrogTorch has none of that, on purpose: ranks, world size, rendezvous,
process groups, barriers and collectives are what Phase I removed. Running
the suite against FrogTorch directly would report a failure for every test,
each saying "this is not an implementation of ProcessGroupGloo" -- true,
intended, and worth nothing.

So this is the other thing the suite can honestly measure: whether FrogNet
can SUPPORT collective semantics when something asks for them. Not whether
the application uses them.

    application ---> torch.distributed collective API
                         |
                         v
                  this backend
                         |
                         v
                  FrogNet shared state (publish / observe / read)

A collective becomes: publish my contribution under a cell named for this
operation and this round, wait until every rank's cell is present, read them
all, combine. No message passing. The barrier that a collective needs is
recovered from absence -- a rank that has not published is a cell that is
not there yet.

WHAT PASSING WOULD MEAN, AND WHAT IT WOULD NOT

It would mean FrogNet's shared state is sufficient to implement the
collective contract exactly, including its failure semantics -- a real
result, and the one an infrastructure reviewer will ask for first.

It would NOT mean the FrogTorch application model is validated, or that any
of Phase I's findings depend on it. Those were about not needing this
interface at all. A backend that passes is evidence FrogNet is capable of
the old model, not evidence the old model is worth keeping.
"""

from __future__ import annotations

import datetime
import os
import sys
import threading
import uuid
import time
from typing import Any, Dict, List, Optional

import torch
import torch.distributed as dist
from torch._C._distributed_c10d import Backend as _CBackend
from torch._C._distributed_c10d import (Work as _CWork,
                                         _create_work_from_future,
                                         _register_work)

from core import frognet_tuples as T

#: The service and variable a collective's cells live under, so a run's
#: collective traffic is distinguishable from application state in the same
#: space. The tuple store's shape is (service, var, scope, value) -- one
#: variable per process group, one SCOPE per rank per operation per round.
SERVICE = os.environ.get("FROGNET_C10D_SERVICE", "c10d")

#: [ASK_ONCE_AND_WAIT_V1] Cap on how long the STORE may hold one read open.
#: The loop still re-evaluates its deadline every BLOCK_CAP seconds, and a
#: store that honours wait_s cannot pin a connection for the whole 300 s
#: timeout. Was a local inside _collect_descriptors, which left broadcast and
#: _await_consumers in psychedelic_backend with no value to reach for -- they
#: spun at a 2 ms ceiling instead. One knob, module level, so every waiting
#: loop in both backends uses the same bound.
BLOCK_CAP = float(os.environ.get("FROGNET_BLOCK_CAP_S", "5.0"))

#: [NEVER_TWO_THINGS_IN_ONE_CELL_V1] A cell name carries the operation, the
#: sequence number within the group and the contributing rank. Two collectives
#: in flight, or two rounds of the same collective, never share a cell -- so
#: there is nothing to detect and nothing to refuse.
def _scope(rank: int) -> str:
    """One scope per rank. The operation and round live in the VARIABLE, so
    a read can ask for exactly one operation instead of the whole group."""
    return "r%d" % rank


class _WaitWork(_CWork):
    """A Work that completes ON wait(), not before it.

    [P2P_01] The differential against gloo is the whole specification:

        backend.recv  is_completed   gloo False   frognet True
        pg.recv       is_completed   gloo False   frognet False
        pg.recv after wait()         gloo True    frognet False   <-- defect
        tensor correct               both True

    Both wrappers start incomplete -- that is normal, the operation is
    asynchronous. What c10d requires is that wait() TRANSITIONS it. A Work
    whose future was already set at construction never drives that
    transition, so the wrapper stays incomplete forever even though the
    payload arrived and the backend's own Work said "complete".

    That also kills the assumption that an already-completed future is fine
    for synchronous use. It is not equivalent to a work that completes
    through wait().

    WHERE THIS ENDED. Neither form fixes the wrapper:

        pre-completed future   backend True,  wrapper False after wait()
        completes on wait()    backend False, wrapper False after wait()
        _register_work(t, w)   no effect -- the registry is not in this path

    And the Python wait() IS reached: traced, called three times in the
    probe. So the wrapper delegates and then does not observe the result.
    Its completion appears to be C++ state that a Python-implemented backend
    cannot set -- Work exposes no finish()/finishAndThrow() to Python -- and
    the collectives never surface it because their tests call wait() and
    check values rather than is_completed().

    That is as far as this can be taken from Python. The next step is
    reading c10d's ProcessGroup::recv in the C++ source to see what it does
    with a Python backend's Work.

    So this reports incomplete until asked, and completes when it is --
    which is the externally observable state change, without emulating
    gloo's internals.
    """

    __slots__ = ("_done", "_res")

    def __init__(self, result=None):
        super().__init__()
        self._done = False
        self._res = result if result is not None else []

    def wait(self, timeout=None):
        # Traced: this IS called by the wrapper, three times in the p2p
        # probe. The wrapper delegates and then still reports incomplete, so
        # its state is not driven by the backend Work's Python methods. See
        # the P2P-01 note above.
        self._done = True
        return True

    def is_completed(self):
        return self._done

    def is_success(self):
        return self._done

    def result(self):
        return self._res

    def get_future(self):
        f = torch.futures.Future()
        f.set_result(self._res)
        return f


#: [P2P_01] The native shim, built on first use. Work::finish() and
#: finishAndThrow() are protected in Work.hpp, so no Python subclass can
#: perform the completion transition c10d reads -- gloo's RecvWork::wait()
#: calls finishAndThrow() and that call is simply not reachable from Python.
#: This is the smallest native piece that makes it reachable.
_NATIVE = None
_NATIVE_TRIED = False


def _native():
    global _NATIVE, _NATIVE_TRIED
    if _NATIVE_TRIED:
        return _NATIVE
    _NATIVE_TRIED = True
    if os.environ.get("FROGNET_C10D_NATIVE") == "0":
        return None
    try:
        from torch.utils.cpp_extension import load
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "frognet_work.cpp")
        _NATIVE = load(
            name="frognet_work", sources=[src],
            # C++20 because torch 2.14's own headers do not compile at 17:
            # at::symint::sizes<T> fails to resolve.
            extra_cflags=["-O2", "-std=c++20", "-DC10_NODEPRECATED"],
            build_directory=os.environ.get("FROGNET_C10D_BUILD_DIR",
                                           "/tmp/frognet_work_build"),
            verbose=False)
    except Exception:
        # Falls back to the Python Work, which is correct for collectives
        # and known-incomplete for point-to-point. Better than refusing to
        # run at all on a box with no compiler.
        _NATIVE = None
    return _NATIVE


def _Work(result=None, register_for=None, src=None):
    """The Work handed back to c10d.

    FROGNET_C10D_WORK=future restores the pre-completed-future form, so the
    two can be compared against the gloo control rather than argued about.
    """
    tensors = list(result) if result is not None else []
    tensors = [t for t in tensors if isinstance(t, torch.Tensor)]
    nat = _native()
    if nat is not None:
        # Finishes on wait(), which is gloo's observable shape. `src` is
        # carried for recv_anysource: distributed_c10d.recv() asks the Work
        # which rank it received from and returns that to the caller.
        return nat.wait_work(tensors, -1 if src is None else int(src))
    if os.environ.get("FROGNET_C10D_WORK") == "future":
        fut = torch.futures.Future()
        fut.set_result(tensors)
        return _create_work_from_future(fut)
    return _WaitWork(tensors)


class FrogNetBackend(_CBackend):
    """A c10d backend whose transport is shared state.

    Deliberately the simplest thing that can be correct: every collective
    publishes one cell per rank and reads the others back. No attempt to be
    fast -- the point is whether the semantics can be met at all, and a
    clever implementation would make a failure harder to attribute.
    """

    def __init__(self, store, rank: int, size: int,
                 timeout: datetime.timedelta = datetime.timedelta(seconds=300),
                 group: str = "default", dbhost: Optional[str] = None):
        super().__init__(rank, size)
        self._rank = rank
        self._size = size
        self._group = group
        self._timeout_s = timeout.total_seconds()
        # Falling through to None made every read fail with "Name or
        # service not known" -- None was passed to the store layer as if it
        # were an address. Default to the tuple layer's own default so an
        # unconfigured process behaves like every other FrogNet client.
        self._dbhost = (dbhost or os.environ.get("FROGNET_C10D_DBHOST")
                        or T.DEFAULT_DBHOST)
        # [THE_SEQUENCE_IS_THE_COLLECTIVE_S_NAME_V1] Every rank in a
        # collective must name the same cells, and the only thing making
        # that true is that all of them call the same operations in the same
        # order, so their counters advance together.
        #
        # Point to point breaks that: send and recv involve TWO ranks, not
        # all of them, so a shared counter leaves every uninvolved rank a
        # step behind and the NEXT collective deadlocks -- each side waiting
        # on cells the others will never write. It cost a three-rank run
        # hanging on a barrier after a send.
        #
        # So p2p counts separately. It is not a collective and must not
        # advance the collective clock.
        # [AN_INCARNATION_IS_NOT_A_NAME_V1] applied to process groups.
        #
        # Each test in the suite builds a fresh group, so `seq` restarts at 1
        # while the store persists for the whole run. Without this, rank 0
        # reads a descriptor left by a PREVIOUS group's rank 1 and dials a
        # server that no longer exists -- ConnectionRefusedError on the
        # tensor plane. The Stage I backend hit the same collision and did
        # not fail: its payload was inline, so it silently consumed a stale
        # value instead. That is worse.
        #
        # All ranks must agree on the value, so it comes from the c10d Store
        # -- which is what the Store is for. Rank 0 mints it, everyone else
        # blocks until it appears.
        # [ONE_AGREEMENT_NOT_TWO_V1]
        #
        # This was a SECOND agreement through the store, with its own key and
        # its own failure mode:
        #
        #     except Exception: return "nostore"
        #
        # A rank whose store.wait failed returned "nostore" while rank 0
        # returned a uuid, so the two ranks sat in different namespaces and
        # nothing said so. The key was fixed too, so a subgroup could read
        # the parent group's value and disagree that way instead.
        #
        # `_group_id` above already agrees one token per group through the
        # same store and has no fallback: rank 0 mints it, everyone else
        # blocks until it appears. That token IS an incarnation -- unique per
        # group, agreed by every participant, minted fresh each time. Taking
        # it removes the second agreement, and with it the second chance to
        # disagree.
        self._incarnation = self._group
        self._seq = 0
        self._p2p_seq = 0
        #: SensorIDs of the cells this rank wrote, harvested from the scoped
        #: read it already did, so a completed operation can drop its own
        #: without a second lookup.
        self._ids: Dict[str, Dict[int, Any]] = {}
        #: Made on first use, and only when overlap is worth having.
        self._exec = None
        #: [A_PAIR_KEEPS_ITS_OWN_CLOCK_V1] per (peer, tag), both directions
        self._p2p_out: Dict[Any, int] = {}
        self._p2p_in: Dict[Any, int] = {}
        self._lock = threading.Lock()

    # -- the shared-state primitives every collective is built from --------

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _next_p2p(self) -> int:
        with self._lock:
            self._p2p_seq += 1
            return self._p2p_seq

    @staticmethod
    def _agree_incarnation(store, rank: int) -> str:
        """NOT CALLED. Kept as the record of why it is not.

        [ONE_AGREEMENT_NOT_TWO_V1]
        A second agreement over a fixed key, with a fallback returning
        "nostore" on any error. Two ranks could leave this holding different
        values -- one a uuid, one the constant -- and every name derived from
        it disagreed silently. A subgroup reading the parent's key split the
        same way.

        The incarnation now comes from `_group_id`, which does this
        agreement once, per group, without a fallback.
        """
        raise NotImplementedError(
            "_agree_incarnation is superseded by the group token; see "
            "[ONE_AGREEMENT_NOT_TWO_V1].")

    def _service(self, op: str, seq: int) -> str:
        """[NEVER_TWO_THINGS_IN_ONE_CELL_V1] The collective instance names
        the SERVICE: group, operation and round. Its only contents are that
        operation's per-rank cells, which is what lets one query answer the
        whole poll."""
        return "%s.%s.%s.%s.%d" % (SERVICE, self._incarnation,
                                   self._group, op, seq)

    def _var(self, op: str, seq: int) -> str:
        """[NEVER_TWO_THINGS_IN_ONE_CELL_V1] group, operation and round.
        Two collectives in flight, or two rounds of one, never share."""
        return "%s.%s.%d" % (self._group, op, seq)

    def _put(self, op: str, seq: int, value) -> None:
        # service = the collective instance; var = the contributing rank.
        T.put(self._service(op, seq), _scope(self._rank), _scope(self._rank),
              value, dbhost=self._dbhost, own=False)

    def _overlap_pays(self) -> bool:
        """[OVERLAP_NEEDS_A_CORE_TO_OVERLAP_ONTO_V1] Is there hardware to
        overlap onto?

        Publishing on a thread while polling is 1.55 ms off a 3.7 ms
        operation -- when the two can actually run at once. On one core they
        take turns instead, and thread creation plus GIL hand-off made it
        WORSE: 3.7 -> 6.8 ms at world 1, 21.2 -> 24.6 at world 3.
        Measured, both directions.

        So it is a property of the machine, not a preference. The rule is
        the same one the studies use for wall clock: a box with fewer cores
        than participants is not a box where concurrency is available. Ranks
        here are separate processes, so the relevant count is cores against
        world size, plus one for the store.

        FROGNET_C10D_OVERLAP=1/0 forces it either way, because a benchmark
        needs to be able to hold this still.
        """
        env = os.environ.get("FROGNET_C10D_OVERLAP")
        if env is not None:
            return env not in ("0", "", "no", "false")
        return (os.cpu_count() or 1) > self._size

    def _pool(self):
        """One executor for this process group, made on first use.

        A thread per operation was part of what made the overlap lose: the
        cost was creation, not the work. A pool pays that once.
        """
        if self._exec is None:
            with self._lock:
                if self._exec is None:
                    from concurrent.futures import ThreadPoolExecutor
                    self._exec = ThreadPoolExecutor(
                        max_workers=max(2, self._size),
                        thread_name_prefix="frognet-c10d")
        return self._exec

    def _publish(self, op: str, seq: int, value):
        """Publish, on a thread when that is free, otherwise inline.

        Returns something to hand to `_settled`, which must be called before
        the collective returns: overlapping the WAIT is allowed, dropping
        the obligation is not.
        """
        if not self._overlap_pays():
            self._put(op, seq, value)
            return None
        return self._pool().submit(self._put, op, seq, value)

    def _settled(self, fut) -> None:
        if fut is None:
            return
        exc = fut.exception()
        if exc is not None:
            raise RuntimeError(
                "frognet backend: this rank's contribution was never "
                "published, so any result read here is not the collective's "
                "result: %r" % exc)

    def _put_async(self, op: str, seq: int, value) -> threading.Thread:
        """[PUBLISHING_IS_NOT_ON_THE_CRITICAL_PATH_V1] Write and look at the
        same time.

        A rank already holds its own contribution, so it has no reason to
        wait for its own write to be acknowledged before it starts watching
        for peers. The put was 1.55 ms of a 3.7 ms operation and all of it
        was serial in front of the first read.

        The thread is JOINED before the collective returns: the write must
        still have happened, and a failed write must still fail the
        operation. What is overlapped is the waiting, not the obligation.
        """
        err = []

        def go():
            try:
                self._put(op, seq, value)
            except BaseException as e:            # noqa: BLE001
                err.append(e)

        t = threading.Thread(target=go, daemon=True)
        t.start()
        t.err = err
        return t

    @staticmethod
    def _join_put(t: threading.Thread) -> None:
        t.join()
        if getattr(t, "err", None):
            raise RuntimeError(
                "frognet backend: this rank's contribution was never "
                "published, so any result read here is not the collective's "
                "result: %r" % t.err[0])

    def _read_all(self, op: str, seq: int, wait_s: float = 0.0,
                  min_rows: int = 0) -> Dict[int, Any]:
        """Every rank's cell for this operation and round, by rank.

        [A_SCOPED_READ_COSTS_WHAT_IT_READS_V1]
        Asks the STORE for one exact name per rank. `_values_raw` already
        passes SensorName through to api.php and returns SensorID;
        get()/get_all() simply never expose either, so reading through them
        fetched the whole service on every poll and the cost grew with the
        run. That is what made a timing loop unmeasurable while the
        one-call-per-collective matrix passed.
        """
        out: Dict[int, Any] = {}
        # [ONE_QUESTION_PER_POLL_V1] One request returns every rank's cell.
        #
        # This asked the store once PER RANK, so a poll cost W round trips
        # and an operation scaled with world size: 5.3 ms at world 1, 10.6
        # at 2, 20.4 at 3. That is read amplification in this backend, not
        # contention and not a property of the store.
        #
        # The fix is where the identity lives. The collective instance is
        # now the SERVICE, so `get_all(service)` returns exactly the cells
        # of this one operation -- all ranks, one question. The rank is the
        # variable. Nothing else is ever in that service, so there is
        # nothing to filter and nothing to scan past.
        # [ASK_ONCE_AND_WAIT_V1] applies here too, and did not reach it.
        # The blocking read was added for the rendezvous and every other
        # caller kept polling: this one asked "are they there yet" hundreds
        # of times a second per rank, when the store will hold the question
        # open until the rows exist. wait_s/min_rows are passed through; a
        # store that does not implement them ignores them and answers
        # immediately, so the caller's loop still works unchanged.
        for r in T.get_all(self._service(op, seq), dbhost=self._dbhost,
                           wait_s=wait_s, min_rows=min_rows):
            v = r.get("var", "")
            if v.startswith("r"):
                try:
                    out[int(v[1:])] = r.get("value")
                except ValueError:
                    continue
        return out

    def _release(self, op: str, seq: int) -> None:
        """NOT CALLED, deliberately.

        [A_CONTRIBUTION_OUTLIVES_ITS_LAST_READER_V1]
        This dropped a rank's cell as soon as THAT rank had its answer. A
        slower rank still polling the same round then saw the set fall below
        world size and could never complete it -- it span to the 300 s
        timeout. One rank finishing is not the collective finishing.

        A contribution must remain observable until every participant that
        can still require it has read it, and no rank knows when that is
        without being told -- which would be a message, and there are none.

        The reason it existed is also gone. It was there to stop the store
        accumulating and making polls expensive, and that was true while a
        poll fetched the whole service. A scoped read asks for one exact
        name and returns one row however much history sits beside it, so
        accumulation costs storage and not latency.

        Kept, unwired, as the record of a lifecycle rule that has to hold:
        release needs a safe point, and there is not one inside a single
        rank's view of a collective.
        """
        sid = self._ids.pop(self._var(op, seq), {}).get(self._rank)
        if sid is None:
            return
        try:
            T._delete_by_id(self._dbhost, sid)
        except Exception:
            pass

    def _gather_cells(self, op: str, seq: int) -> List[Any]:
        """Every rank's contribution for this operation and round.

        The barrier is recovered from ABSENCE: a rank that has not published
        is a cell that is not there. Nothing signals anyone; a reader simply
        does not see a complete set until it is complete.
        """
        deadline = time.time() + self._timeout_s
        want = set(range(self._size))
        out: Dict[int, Any] = {}
        # [POLL_AT_THE_SPEED_OF_THE_ANSWER_V1] A flat 2 ms sleep was the cost
        # of a collective, not the reads. Three scoped reads measure 1.76 ms
        # while an all_reduce took 24 ms -- about thirteen cycles, and the
        # sleeping was ~26 ms of it. Ranks publish within microseconds of
        # each other, so the first few waits should be short; the backoff
        # only grows for a genuinely late peer, where spinning would be
        # rude.
        wait = 0.00005
        # [SAY_WHO_IS_MISSING_V1 - John 2026-09-29] A barrier (and every other
        # gather) printed only how many rows came back -- "rows n=3" every
        # 5 s -- so a run stuck waiting for one rank could not say WHICH rank
        # without opening the other three logs. Every incomplete pass now
        # names the ranks present and the ranks missing, at most once a
        # second; a pass that completes after having waited says so.
        t0 = time.time()
        said = 0.0
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            # One request that BLOCKS until the rank set is complete,
            # instead of hundreds that each ask and hang up. Against an
            # updated store this is a single round trip per collective; the
            # backoff below only still runs against a store that ignores the
            # parameters, or when the server's own wait expired first.
            out = self._read_all(op, seq,
                                 wait_s=min(remaining, BLOCK_CAP),
                                 min_rows=self._size)
            # [COMPLETION_IS_A_RANK_SET_NOT_A_COUNT_V1] The condition is that
            # the expected rank set is contained in what was observed for
            # THIS collective identity. A count is only the efficient form of
            # that while the keys are guaranteed to be exactly these ranks --
            # and a future naming bug could satisfy a count without it.
            if want.issubset(out.keys()):
                if said:
                    _say_gather("GATHER_DONE", op, seq, self._rank,
                                sorted(out.keys()), [], time.time() - t0)
                return [out[r] for r in range(self._size)]
            if time.time() - said >= 1.0:
                said = time.time()
                _say_gather("GATHER_WAIT", op, seq, self._rank,
                            sorted(set(out.keys()) & want),
                            sorted(want - set(out.keys())), said - t0)
            time.sleep(wait)
            wait = min(wait * 2.0, 0.002)
        missing = sorted(want - set(out.keys()))
        _say_gather("GATHER_FAIL", op, seq, self._rank,
                    sorted(set(out.keys()) & want), missing, time.time() - t0)
        raise RuntimeError(
            "frognet backend: %s seq %d incomplete after %.0fs -- ranks %s "
            "never published. A collective requires every rank; absence is "
            "how this backend learns one is missing."
            % (op, seq, self._timeout_s, missing))

    @staticmethod
    def _enc(t: torch.Tensor):
        # [A_COMPLEX_TENSOR_IS_NOT_A_LIST_OF_FLOATS_V1] `.tolist()` on a
        # complex tensor yields Python complex objects, which do not survive
        # JSON -- and a naive float() of them silently drops the imaginary
        # part. PyTorch's own test_all_reduce_sum_complex catches exactly
        # that. Complex is carried as its real view: an interleaved real
        # tensor of twice the length, reconstructed on the far side.
        t = t.detach().to("cpu")
        if t.is_complex():
            return {"d": torch.view_as_real(t).flatten().tolist(),
                    "s": list(t.shape), "t": str(t.dtype), "c": True}
        return {"d": t.flatten().tolist(),
                "s": list(t.shape), "t": str(t.dtype), "c": False}

    @staticmethod
    def _dec(v, like: torch.Tensor) -> torch.Tensor:
        if v.get("c"):
            real = torch.tensor(
                v["d"], dtype=torch.float64 if like.dtype == torch.complex128
                else torch.float32).reshape(list(v["s"]) + [2])
            return torch.view_as_complex(real.contiguous()).to(like.dtype)
        return torch.tensor(v["d"], dtype=like.dtype).reshape(v["s"])

    #: A ReduceOp does not stringify usefully -- str() gives the repr of a
    #: RedOpType object. It compares equal to the enum members, so match on
    #: those rather than on text.
    @staticmethod
    def _op_name(op) -> str:
        if op is None:
            return "SUM"
        from torch._C._distributed_c10d import ReduceOp as _R
        for n in ("SUM", "PRODUCT", "MIN", "MAX", "AVG", "BAND", "BOR",
                  "BXOR", "PREMUL_SUM"):
            if op == getattr(_R.RedOpType, n, object()):
                return n
        return str(op)

    def _combine(self, parts: List[torch.Tensor], op) -> torch.Tensor:
        """[REDUCE_IN_PLACE_DO_NOT_STACK_V1]

        Accumulate pairwise. This began as torch.stack(parts) followed by
        sum(0) -- an allocation of W x n, a full pass to write it, and
        another full pass to read it back, to produce n. The bitwise ops
        already did it the other way in the same function, which is how the
        difference is visible without measuring anything.

        Pairwise is W-1 passes over n and no allocation beyond the first
        clone. The clone is needed because `parts[0]` came from a peer read
        and the caller may still want it -- accumulating into it would
        change a tensor this function was only lent.

        Every op here is associative and element-wise, which is what makes
        the order free. AVG is the one that is not, so it sums and divides
        at the end rather than averaging pairwise: a mean of means is not
        the mean unless the parts are equal.
        """
        name = self._op_name(op)
        if not parts:
            raise ValueError("nothing to combine")
        acc = parts[0].clone()
        rest = parts[1:]
        if name == "SUM":
            for p in rest:
                acc.add_(p)
            return acc
        if "AVG" in name:
            for p in rest:
                acc.add_(p)
            return acc.div_(len(parts))
        if "PRODUCT" in name:
            for p in rest:
                acc.mul_(p)
            return acc
        if "MIN" in name:
            for p in rest:
                torch.minimum(acc, p, out=acc)
            return acc
        if "MAX" in name:
            for p in rest:
                torch.maximum(acc, p, out=acc)
            return acc
        if "BAND" in name:
            for p in rest:
                acc &= p
            return acc
        if "BOR" in name:
            for p in rest:
                acc |= p
            return acc
        if "BXOR" in name:
            for p in rest:
                acc ^= p
            return acc
        raise NotImplementedError(
            "frognet backend does not implement ReduceOp %r. Saying so is "
            "better than silently summing." % name)

    # -- the collective contract ------------------------------------------

    def allreduce(self, tensors, opts=None):
        op = getattr(opts, "reduceOp", None)
        for t in tensors:
            seq = self._next_seq()
            # [OVERLAP_NEEDS_A_CORE_TO_OVERLAP_ONTO_V1] This published on
            # a thread and polled while the write was in flight -- 1.55 ms
            # of a 3.7 ms operation, off the critical path. On one core it
            # was SLOWER: 3.7 -> 6.8 ms at world 1, 21.2 -> 24.6 at world 3.
            # Thread creation and GIL hand-off cost more than the overlap
            # saves when the put thread and the poll loop take turns on the
            # same core instead of running together.
            #
            # _put_async and _join_put are kept below and unwired. The idea
            # is right and should be re-measured where there are more cores
            # than ranks; it is not shipped on the strength of a machine
            # that cannot show it.
            pending = self._publish("ar", seq, self._enc(t))
            parts = [self._dec(v, t) for v in self._gather_cells("ar", seq)]
            self._settled(pending)
            t.copy_(self._combine(parts, op))
        return _Work(tensors)

    def broadcast(self, tensors, opts=None):
        root = getattr(opts, "rootRank", 0)
        for t in tensors:
            seq = self._next_seq()
            if self._rank == root:
                self._put("bc", seq, self._enc(t))
            deadline = time.time() + self._timeout_s
            while time.time() < deadline:
                v = self._read_all("bc", seq).get(root)
                if v is not None:
                    t.copy_(self._dec(v, t))
                    break
                time.sleep(0.002)
            else:
                raise RuntimeError(
                    "frognet backend: broadcast seq %d -- root %d never "
                    "published" % (seq, root))
        return _Work(tensors)

    def allgather(self, output_tensors, input_tensors, opts=None):
        for outs, inp in zip(output_tensors, input_tensors):
            seq = self._next_seq()
            self._put("ag", seq, self._enc(inp))
            parts = self._gather_cells("ag", seq)
            for i, o in enumerate(outs):
                o.copy_(self._dec(parts[i], o))
        return _Work(output_tensors)

    def reduce(self, tensors, opts=None):
        """Like allreduce, but only the root keeps the result."""
        root = getattr(opts, "rootRank", 0)
        op = getattr(opts, "reduceOp", None)
        for t in tensors:
            seq = self._next_seq()
            self._put("rd", seq, self._enc(t))
            parts = [self._dec(v, t) for v in self._gather_cells("rd", seq)]
            combined = self._combine(parts, op)
            if self._rank == root:
                t.copy_(combined)
        return _Work(tensors)

    def gather(self, output_tensors, input_tensors, opts=None):
        """Every rank contributes; only the root receives."""
        root = getattr(opts, "rootRank", 0)
        for i, inp in enumerate(input_tensors):
            seq = self._next_seq()
            self._put("ga", seq, self._enc(inp))
            parts = self._gather_cells("ga", seq)
            if self._rank == root and output_tensors:
                outs = output_tensors[i] if i < len(output_tensors) \
                    else output_tensors[0]
                for j, o in enumerate(outs):
                    o.copy_(self._dec(parts[j], o))
        return _Work(output_tensors)

    def scatter(self, output_tensors, input_tensors, opts=None):
        """The root publishes one cell holding every rank's slice; each rank
        reads its own out. One cell, many readers -- the shape shared state
        is naturally good at."""
        root = getattr(opts, "rootRank", 0)
        for i, out in enumerate(output_tensors):
            seq = self._next_seq()
            if self._rank == root:
                ins = input_tensors[i] if i < len(input_tensors) \
                    else input_tensors[0]
                self._put("sc", seq, {"parts": [self._enc(t) for t in ins]})
            deadline = time.time() + self._timeout_s
            while time.time() < deadline:
                v = self._read_all("sc", seq).get(root)
                if v is not None:
                    out.copy_(self._dec(v["parts"][self._rank], out))
                    break
                time.sleep(0.002)
            else:
                raise RuntimeError(
                    "frognet backend: scatter seq %d -- root %d never "
                    "published" % (seq, root))
        return _Work(output_tensors)

    def alltoall_base(self, output_tensor, input_tensor, output_split_sizes,
                      input_split_sizes, opts=None):
        seq = self._next_seq()
        n = self._size
        chunk = input_tensor.numel() // n
        self._put("a2a", seq,
                  {"parts": [self._enc(input_tensor[i * chunk:(i + 1) * chunk])
                             for i in range(n)]})
        cells = self._gather_cells("a2a", seq)
        oc = output_tensor.numel() // n
        for src in range(n):
            piece = self._dec(cells[src]["parts"][self._rank],
                              output_tensor[:oc])
            output_tensor[src * oc:(src + 1) * oc].copy_(piece.flatten())
        return _Work([output_tensor])

    def alltoall(self, output_tensors, input_tensors, opts=None):
        seq = self._next_seq()
        self._put("a2al", seq,
                  {"parts": [self._enc(t) for t in input_tensors]})
        cells = self._gather_cells("a2al", seq)
        for src, o in enumerate(output_tensors):
            o.copy_(self._dec(cells[src]["parts"][self._rank], o))
        return _Work(output_tensors)

    def reduce_scatter(self, output_tensors, input_tensors, opts=None):
        op = getattr(opts, "reduceOp", None)
        for i, out in enumerate(output_tensors):
            ins = input_tensors[i] if i < len(input_tensors) \
                else input_tensors[0]
            seq = self._next_seq()
            self._put("rs", seq, {"parts": [self._enc(t) for t in ins]})
            cells = self._gather_cells("rs", seq)
            parts = [self._dec(c["parts"][self._rank], out) for c in cells]
            out.copy_(self._combine(parts, op))
        return _Work(output_tensors)

    def _allgather_base(self, output_tensor, input_tensor, opts=None):
        seq = self._next_seq()
        self._put("agb", seq, self._enc(input_tensor))
        parts = self._gather_cells("agb", seq)
        k = input_tensor.numel()
        for r in range(self._size):
            output_tensor[r * k:(r + 1) * k].copy_(
                self._dec(parts[r], input_tensor).flatten())
        return _Work([output_tensor])

    def all_gather_single(self, output_tensor, input_tensor, opts=None):
        """The name torch 2.14 actually dispatches to. `allgather_single`
        (no underscore after `all`) is NOT it -- I guessed the spelling and
        the call went nowhere, which is why the first three-rank run failed
        only on this one. Read the dispatch, do not infer it."""
        return self._allgather_base(output_tensor, input_tensor, opts)

    def allgather_single(self, output_tensor, input_tensor, opts=None):
        """torch 2.14 renamed all_gather_into_tensor to all_gather_single and
        dispatches to this name; the old one is deprecated but still routed
        here. Both spellings, one implementation."""
        return self._allgather_base(output_tensor, input_tensor, opts)

    def allgather_into_tensor_coalesced(self, outputs, inputs, opts=None):
        for o, i in zip(outputs, inputs):
            self._allgather_base(o, i, opts)
        return _Work(outputs)

    def allreduce_coalesced(self, tensors, opts=None):
        return self.allreduce(tensors, opts)

    def allgather_coalesced(self, output_lists, input_list, opts=None):
        """One cell carrying this rank's whole list, rather than one cell per
        tensor. The coalesced forms exist to amortise a per-call cost that a
        message transport has; here they mostly just mean fewer cells."""
        seq = self._next_seq()
        self._put("agc", seq, {"parts": [self._enc(t) for t in input_list]})
        cells = self._gather_cells("agc", seq)
        for r, outs in enumerate(output_lists):
            for i, o in enumerate(outs):
                o.copy_(self._dec(cells[r]["parts"][i], o))
        return _Work(output_lists)

    def send(self, tensors, dst_rank, tag=0):
        """Point to point, still without a message: the sender publishes a
        cell only the addressee reads.

        [A_PAIR_KEEPS_ITS_OWN_CLOCK_V1] Sender and receiver each count per
        (peer, tag), starting at 1, so both derive the same cell name
        without exchanging one. PyTorch orders point-to-point per pair, so
        the two counters stay in step.

        This is separate from the collective clock -- send and recv involve
        two ranks, not all of them, and advancing the collective clock here
        left uninvolved ranks a step behind and deadlocked the next
        collective.
        """
        for t in tensors:
            k = (dst_rank, tag)
            self._p2p_out[k] = self._p2p_out.get(k, 0) + 1
            self._put("p2p.%d.%d.%d" % (self._rank, dst_rank, tag),
                      self._p2p_out[k], self._enc(t))
        return _Work(tensors, register_for=tensors)

    def recv(self, tensors, src_rank, tag=0):
        for t in tensors:
            k = (src_rank, tag)
            self._p2p_in[k] = self._p2p_in.get(k, 0) + 1
            op = "p2p.%d.%d.%d" % (src_rank, self._rank, tag)
            seq = self._p2p_in[k]
            deadline = time.time() + self._timeout_s
            wait = 0.00005
            while time.time() < deadline:
                got = self._read_all(op, seq)
                if src_rank in got:
                    t.copy_(self._dec(got[src_rank], t))
                    break
                time.sleep(wait)
                wait = min(wait * 2.0, 0.002)
            else:
                raise RuntimeError(
                    "frognet backend: recv from rank %d tag %d (message %d) "
                    "timed out -- no cell was published for it"
                    % (src_rank, tag, seq))
        return _Work(tensors, register_for=tensors)

    def recv_anysource(self, tensors, tag=0):
        """[P2P_02] Receive from whichever rank has published.

        `dist.recv(tensor)` with no `src` routes here. The message model
        needs a wildcard match in the transport for this; shared state does
        not -- the receiver simply reads the cells addressed to it and takes
        one that exists. Any sender's cell is already visible; nothing has to
        be matched or steered.

        The rank that sent is recovered from the cell's own name and carried
        on the returned Work, because distributed_c10d.recv() asks it
        (`work._source_rank()`) and returns that to the caller. A
        receive-from-anyone is meaningless if the receiver cannot then say
        who it was.

        Ordering is per pair, as PyTorch specifies: each sender's messages
        are consumed in order, and the counter for that pair advances only
        when one of its messages is taken.
        """
        got_from = None
        for t in tensors:
            deadline = time.time() + self._timeout_s
            wait = 0.00005
            while time.time() < deadline:
                for src in range(self._size):
                    if src == self._rank:
                        continue
                    k = (src, tag)
                    seq = self._p2p_in.get(k, 0) + 1
                    op = "p2p.%d.%d.%d" % (src, self._rank, tag)
                    cell = self._read_all(op, seq)
                    if src in cell:
                        t.copy_(self._dec(cell[src], t))
                        self._p2p_in[k] = seq
                        got_from = src
                        break
                if got_from is not None:
                    break
                time.sleep(wait)
                wait = min(wait * 2.0, 0.002)
            else:
                raise RuntimeError(
                    "frognet backend: recv_anysource tag %d timed out -- no "
                    "rank published a cell addressed to rank %d"
                    % (tag, self._rank))
        return _Work(tensors, src=got_from)

    def barrier(self, opts=None):
        seq = self._next_seq()
        pending = self._publish("bar", seq, {"n": 1})
        self._gather_cells("bar", seq)
        self._settled(pending)
        return _Work()

    def getBackendName(self):
        return "frognet"


#: [NEVER_TWO_THINGS_IN_ONE_CELL_V1] Every process group needs its own cell
#: namespace. This named them all "default", so a second group created by
#: new_group() wrote into the first group's cells -- two things contending
#: for one piece of memory, which is exactly what a shared-memory model must
#: never allow. PyTorch's own test_scatter_full_group caught it.
#:
#: Ranks agree on the index because they create groups in the same order,
#: which is the same reason they agree on a collective's sequence number.
def _say_gather(event, op, seq, rank, have, missing, waited_s):
    """[SAY_WHO_IS_MISSING_V1] One line: which collective, who is here, who
    is not, and for how long. Always on -- a wait that names nobody is the
    line that cost a morning."""
    t = time.time()
    print("[%s.%03d] [DIAG-GATHER] %s op=%s seq=%d rank=%d have=%s missing=%s "
          "waited_s=%.1f" % (time.strftime("%H:%M:%S", time.localtime(t)),
                             int((t % 1) * 1000), event, op, seq, rank,
                             have, missing, waited_s),
          file=sys.stderr, flush=True)


def _group_id(store, size: int, rank: int) -> str:
    """A name every participant agrees on, agreed THROUGH the store.

    Two earlier attempts were wrong in instructive ways.

    A global counter incremented per backend creation desynchronises the
    moment a group is created by only SOME ranks, which new_group() does
    routinely -- the same shape as the point-to-point sequence bug.
    test_scatter_object_list passed alone and failed beside its siblings,
    which is what a desynchronised name looks like.

    repr(store) is distinct per group but contains the object address, so it
    is not stable across processes -- it would give every rank a different
    name for the same group.

    The store PyTorch hands each backend is already scoped to that group and
    is the mechanism ranks use to agree on anything. So rank 0 writes a
    token and the others read it. One key, one group, every participant
    reaching the same answer by asking rather than by counting.
    """
    key = "frognet.gid"
    if rank == 0:
        store.set(key, uuid.uuid4().hex[:12])
    store.wait([key])
    tok = store.get(key)
    if isinstance(tok, (bytes, bytearray)):
        tok = tok.decode()
    return "g%s.%d" % (tok, size)


def _create(store, rank, size, timeout):
    return FrogNetBackend(store, rank, size, timeout,
                          group=_group_id(store, size, rank))


def register(name: str = "frognet") -> None:
    """Make `init_process_group(backend='frognet')` work."""
    dist.Backend.register_backend(name, _create, devices=["cpu"])
