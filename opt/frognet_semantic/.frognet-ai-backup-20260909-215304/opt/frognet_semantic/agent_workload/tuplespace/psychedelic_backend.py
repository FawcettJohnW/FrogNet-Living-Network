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
agent_workload/tuplespace/psychedelic_backend.py -- the same PyTorch
operations, executed the way FrogNet actually works.

[ONE_RUNTIME_NOT_TWO_V1]

Stage I's backend proved FrogNet can implement the collective contract. It
did so in the most literal way available: every rank put its whole tensor
into the control store as a JSON list of floats, and everybody read
everybody's back.

That is not how FrogTorch has ever moved a tensor, and the measurement says
why it matters. For a 262,144-element tensor:

    tolist()                 25.3 ms
    json.dumps()            136.4 ms
    put (total)             312.1 ms
    the same bytes raw        0.13 ms      <- tobytes()

Two milliseconds of arithmetic wrapped in three hundred of text. The
control plane was carrying payload, which is the one thing the FrogNet
architecture says it should never do -- and `plane_separation` passes 13 of
13 precisely by keeping them apart.

So this backend is the same operations over the split FrogTorch has used all
along:

    the space          a DESCRIPTOR per rank: where its bytes are, what
                       shape and dtype, which generation. Small, flat,
                       constant-sized, exactly what a control plane is for.
    the tensor plane   the bytes themselves, on a permanent socket, framed
                       and digest-checked, never serialised to text.

WHAT DOES NOT CHANGE

The contract. `all_reduce(SUM)` still owes the sum over every rank in the
group, and completion is still the full rank set:

    Psychedelic's selection policies -- fresh_only, by_participation -- are
    NOT permitted inside a collective. Declining to consume a peer changes
    the sum. That is a different mathematical operation and it belongs to
    the native interface, not here.

What Psychedelic contributes here is how the state moves, not which state
counts.

WHAT FROGNET GETS FOR FREE, WITHOUT TOUCHING THE RESULT

  * SAME suppression. A reader that already holds a peer's exact bytes is
    told so and no payload crosses. In the bakeoff this was 3.1-4.3x fewer
    bytes on the wire. The reduction is identical either way.
  * Demand-driven materialisation. A producer publishes a descriptor and
    serialises only when somebody asks -- [MATERIALISE_ON_DEMAND_V1]. On a
    broadcast, the ranks that already hold the value never make the sender
    do the work.
  * State outlives the operation. A slow reader does not stall a producer
    and does not make it retransmit; the cell is simply still there.
"""

from __future__ import annotations

import datetime
import os
import threading
import time
from typing import Any, Dict, List, Optional

import torch
import torch.distributed as dist

from core import frognet_tuples as T

from . import torch_backend as _base
from .tensor_plane import READ_CURRENT, TensorClient, TensorServer

SERVICE = os.environ.get("FROGNET_PSY_SERVICE", "psyc10d")


def _scope(rank: int) -> str:
    return "r%d" % rank


def op_of(name: str) -> str:
    """`<incarnation>.<op>.<seq>` -> `<op>`. The content a peer holds is
    per-operation, not per-round."""
    parts = name.split(".")
    return parts[-2] if len(parts) >= 2 else name


class PsychedelicBackend(_base.FrogNetBackend):
    """Same collectives, same results, FrogNet's own transport.

    Inherits every operation from the Stage I backend and replaces only how
    a contribution is published and read. Anything not overridden below runs
    the Stage I path unchanged, so a difference in behaviour can only come
    from what is overridden -- which is the point of subclassing rather than
    writing a second engine.
    """

    def __init__(self, store, rank: int, size: int,
                 timeout: datetime.timedelta = datetime.timedelta(seconds=300),
                 group: str = "default", dbhost: Optional[str] = None):
        super().__init__(store, rank, size, timeout, group, dbhost)
        host = os.environ.get("FROGNET_PSY_HOST", "127.0.0.1")
        # The plane's cell names carry the group incarnation too, so a new
        # group cannot read a dead group's tensors.
        self._server = TensorServer(host)
        self._server_host = host
        self._client = TensorClient("psy-r%d" % rank)
        self._gen = 0
        #: [ONE_NUMBER_IS_THE_IDENTITY_V1, second half]
        #: all_reduce names its states `<inc>.ar.<n>`, and the prefetcher
        #: walks n forward one at a time to read the round ahead. That only
        #: works if the n's are CONTIGUOUS. Taking them from `_next_seq()`,
        #: which `barrier()` also advances, leaves gaps: after one barrier
        #: the next all_reduce is ar.17 while the prefetcher is asking for
        #: ar.16, a name nobody will ever publish. It blocks the full 30 s
        #: read timeout, raises, and takes the epoch down with it -- once
        #: per barrier, which measured as exactly 30 s per size point.
        #: Counting all_reduces alone makes the sequence contiguous, and
        #: makes name, generation and prefetch cursor the same number.
        self._ar_seq = 0
        #: [SAME_IS_ABOUT_CONTENT_NOT_ABOUT_NAMES_V1] last digest held per
        #: (peer, operation), carried across generations.
        self._have: Dict[Any, Any] = {}
        #: [WHEN_IS_PART_OF_WHAT_V1] when each round began, per operation
        self._round_started: Dict[Any, float] = {}
        #: [THE_STORE_IS_FOR_FINDING_NOT_FOR_TELLING_V1] found once
        self._peers: Optional[Dict[int, Any]] = None
        #: [BLAST_IT_ALL_AT_RAM_V1] one client per peer, so reads overlap
        self._clients: Dict[int, TensorClient] = {}
        #: [THE_STATE_SHOULD_ALREADY_BE_HERE_V1]
        self._inbox: Dict[Any, torch.Tensor] = {}
        self._inbox_cv = threading.Condition()
        self._prefetching = False
        self._pf_threads: List[threading.Thread] = []
        #: [A_PREFETCHER_IS_BOUND_TO_A_SHAPE_V1] Which generation of
        #: prefetchers is current, and how many of them are still alive.
        #: Both are needed because a prefetcher cannot outlive a change in
        #: the tensor it was started for, and because readers must be told
        #: when there is no longer anyone fetching.
        self._rv_lock = threading.Lock()
        self._pf_epoch = 0
        self._pf_live = 0
        self._stop = threading.Event()
        self._want = 0
        self._shape = None
        self._dtype = None
        #: How much of a collective is spent asking "is it there yet".
        self.polls = 0
        self.poll_s = 0.0

    # -- publish a descriptor, serve the bytes --------------------------

    def _put(self, op: str, seq: int, value) -> None:
        """Non-tensor payloads (barrier tokens, scatter slices) still go in
        the space, unchanged. Only tensors take the plane."""
        T.put(self._service(op, seq), _scope(self._rank), _scope(self._rank),
              value, dbhost=self._dbhost, own=False)

    # -- "done yet" ------------------------------------------------------
    #
    # [ASK_THE_CHEAP_QUESTION_V1] Completion detection is not the payload.
    #
    # A rank polls repeatedly until every contribution is present, and each
    # poll used to drag back every descriptor -- host, port, name, gen,
    # shape, dtype, 106 bytes each -- when all it needed to know was whether
    # the set was complete. The descriptors are needed ONCE, at the end; the
    # question is asked many times.
    #
    # So the answer lives in its own service, one 8-byte marker per rank,
    # written when that rank has published. Measured, four ranks:
    #
    #     poll returning 4 descriptors   1.111 ms
    #     poll returning 4 markers       0.813 ms      27% cheaper
    #
    # The store has no increment -- only upsert_by_name -- so this is not a
    # counter. It does not need to be: put is idempotent per
    # (service, var, scope), so each rank writing its own marker is
    # naturally once-only, and the count is however many exist. No
    # read-modify-write, nothing to race.




    # -- rendezvous once, then talk to peers -----------------------------
    #
    # [THE_STORE_IS_FOR_FINDING_NOT_FOR_TELLING_V1]
    #
    # Every collective used to write a descriptor and read the set back --
    # a store round trip per operation, per rank. Almost all of it was
    # already known: a rank's plane address does not change for the life of
    # the run, and in a collective the shape and dtype are the caller's own.
    # The only per-round fact was "rank r reached generation g", and a
    # waiting read establishes that by returning.
    #
    # gloo touches its store exactly once, at rendezvous -- measured: four
    # keys for three ranks, unchanged after twenty all_reduces. It then owns
    # direct connections and a fixed membership.
    #
    # So: find each other once, then read each other directly. The store is
    # consulted when the set of participants might have CHANGED, not to be
    # told a peer has done something the reader could simply ask about.

    def _peer_inc(self, rank: int) -> str:
        """The name a peer publishes under, WITHIN THIS INCARNATION.

        [AN_ADDRESS_MEANS_NOTHING_WITHOUT_AN_INCARNATION_V1]

        This returned "c%d" % rank, so every process group in every process
        that ever ran published under the same names. Rank 0's first
        all_reduce is `c0.ar.1` today, was `c0.ar.1` in the previous test,
        and will be `c0.ar.1` in the next run.

        That is fine while a name only has to be unique among live peers. It
        stops being fine the moment a stale name can be FOUND: the rendezvous
        cell is upserted per (rank, group), it outlives the process that
        wrote it, and a reader that arrives before the current rank 0 has
        overwritten its row gets the previous process's port. The address is
        still perfectly well-formed. The bytes it names are gone, and the
        reader learns that as ConnectionRefused from inside a collective.

        PyTorch's own suite finds this: test_all_reduce_sum_async passes
        alone and fails beside its siblings, which is what a stale address
        looks like.

        The base class already agrees one incarnation per process group
        through the c10d store. Using it here makes an address meaningful
        only within the group that created it -- so a stale cell is
        recognisably stale rather than merely wrong, and a reader waits for
        the current one instead of dialling a corpse.

        Every rank derives this the same way from a value they all agreed,
        so nobody has to be told.
        """
        return "%s.c%d" % (self._incarnation, rank)

    def _rendezvous(self) -> Dict[int, Any]:
        """[PUBLISH_YOUR_ADDRESS_ONCE_V1]

        The check and the publish were not under a lock, and this is called
        from every read path -- `_read_peer`, `_read_slice`,
        `_read_slice_into` -- all of which run on pool threads. On the first
        collective several of them arrive together, each sees `_peers is
        None`, and each writes the same rendezvous key.

        The store's upsert is SELECT-then-INSERT and not atomic, so
        concurrent writes of one key both miss the SELECT and both INSERT:

            Duplicate entry 'SD:r1.r1-nb1.mesh4.psyc10d.rv.default-'
            for key 'uniq_tuple'

        On loopback the writes finished before the next thread started and
        it almost never showed. Over the mesh a store round trip is long
        enough that they overlap every time.

        One lock, held across the check, the publish and the wait. A rank
        publishes its address exactly once.
        """
        if self._peers is not None:
            return self._peers
        with self._rv_lock:
            if self._peers is not None:
                return self._peers
            return self._rendezvous_locked()

    def _rendezvous_locked(self) -> Dict[int, Any]:
        # The descriptor says WHICH incarnation this address belongs to. A
        # cell left by a previous group is then visibly not ours.
        T.put("%s.rv.%s" % (SERVICE, self._group), _scope(self._rank),
              _scope(self._rank),
              {"host": self._server_host, "port": self._server.port,
               "inc": self._incarnation},
              dbhost=self._dbhost, own=False)
        deadline = time.time() + self._timeout_s
        found: Dict[int, Any] = {}
        while time.time() < deadline:
            for row in T.get_all("%s.rv.%s" % (SERVICE, self._group),
                                 dbhost=self._dbhost,
                                 wait_s=min(5.0, self._timeout_s),
                                 min_rows=self._size):
                v = row.get("var", "")
                d = row.get("value")
                if not (v.startswith("r") and isinstance(d, dict)):
                    continue
                # [AN_ADDRESS_MEANS_NOTHING_WITHOUT_AN_INCARNATION_V1]
                # A cell from an earlier group carries an earlier
                # incarnation. Skipping it means this rank keeps waiting for
                # the peer that is actually here, rather than completing the
                # rendezvous against an address that will refuse the
                # connection. A cell with no incarnation at all predates this
                # and is also not ours.
                if d.get("inc") != self._incarnation:
                    continue
                try:
                    found[int(v[1:])] = d
                except ValueError:
                    pass
            if set(range(self._size)).issubset(found):
                self._peers = found
                return found
        raise RuntimeError(
            "psychedelic backend: rendezvous incomplete after %.0fs -- "
            "ranks %s never published an address"
            % (self._timeout_s,
               sorted(set(range(self._size)) - set(found))))

    def _pool(self):
        """[BLAST_IT_ALL_AT_RAM_V1] Made once, on first use.

        Not gated on core count, unlike the publish overlap: these threads
        block on recv rather than compete for the interpreter, so they
        overlap on one core as readily as on many.
        """
        if self._exec is None:
            from concurrent.futures import ThreadPoolExecutor
            self._exec = ThreadPoolExecutor(
                max_workers=max(2, self._size),
                thread_name_prefix="psy-read")
        return self._exec

    # -- prefetch --------------------------------------------------------
    #
    # [THE_STATE_SHOULD_ALREADY_BE_HERE_V1]
    #
    # Issuing the reads when the collective asks for them means the
    # collective pays for them. But every rank's cell name for the next
    # round is derivable -- `c<rank>.<op>.<seq>` -- so a reader does not
    # have to be told what to want next, and the plane read already waits.
    #
    # So one thread per peer sits on a waiting read for the generation after
    # the one in hand, and puts what comes back in local RAM. By the time
    # the application calls all_reduce, the peer's contribution is usually
    # already here: the read overlaps the application's own compute rather
    # than following it.
    #
    # Bounded by LOOKAHEAD so a fast rank cannot run arbitrarily far ahead
    # of a slow one and hold generations it will not need for a while.
    # Nothing is registered anywhere: a prefetcher is a reader like any
    # other, and a peer that never publishes simply leaves it waiting.

    #: [LOOKAHEAD_COSTS_MEMORY_AND_BANDWIDTH_V1] How many generations ahead
    #: a prefetcher may run. Each one holds a whole tensor per peer, so at 2
    #: MB and world 3 a lookahead of 2 keeps 8 MB in flight and fetches it
    #: while the collective is doing its own work -- which on one core is
    #: contention, not overlap. Small tensors are latency-bound and want the
    #: lookahead; large ones are bandwidth-bound and do not.
    #: Measured at 2 MB, world 3: none 77.7 ms, one 25.8, two 28.2. One
    #: generation ahead is enough to hide the latency and does not put a
    #: second whole tensor per peer in flight against the collective's own
    #: work.
    LOOKAHEAD = int(os.environ.get("FROGNET_PSY_LOOKAHEAD", "1"))

    #: [MOVE_RING_BYTES_WITHOUT_BECOMING_A_RING_V1]
    #:
    #: "stack" is the original: every rank reads every peer's WHOLE tensor
    #: and reduces the stack locally. Each rank takes in (W-1)n and each
    #: producer serves its tensor W-1 times. Ring all_reduce moves
    #: 2(W-1)n/W per rank, so the byte ratio is W/2 -- 1.5x at world 3,
    #: 4x at world 8 -- and it grows with the fleet. No implementation
    #: quality closes a gap that scales.
    #:
    #: "shard" moves ring's byte count while staying read-shaped. Rank r
    #: owns slice r. Every rank publishes its tensor as W separately named
    #: slices; rank r reads only slice r from each peer and reduces it
    #: ((W-1)n/W in), publishes the reduced slice, then reads the W-1
    #: reduced slices it does not own ((W-1)n/W in). Total 2(W-1)n/W --
    #: ring's number, exactly.
    #:
    #: Nothing about the model changes. There is still no rendezvous, no
    #: message, no schedule: a rank publishes what it has and reads what is
    #: there. What changes is WHICH bytes each rank is responsible for, and
    #: that no producer serves the same byte twice.
    #:
    #: The trade is round trips. Two phases instead of one, so 2(W-1) reads
    #: per collective against W-1, and W+1 publishes against 1. That lands
    #: in the fixed cost `a`, which the intercept profile measured at
    #: 1.1-1.5 ms and which is irrelevant at production tensor sizes. It
    #: buys the per-element cost `b`, which is the only term that survives
    #: to a 100M-parameter bucket.
    REDUCE = os.environ.get("FROGNET_PSY_REDUCE", "stack")

    #: [A_WAIT_CAP_MUST_FOLLOW_THE_DEADLINE_V1]
    #:
    #: Every peer read capped its wait at a hardcoded 30 s, in seven places,
    #: while the collective's own deadline came from the process group's
    #: timeout. Set the timeout to 300 s and a peer more than 30 s behind is
    #: still declared gone -- which on a fleet spanning 5x in speed is a
    #: normal amount of behind, not a fault.
    #:
    #: The cap exists so one read cannot consume the whole deadline and
    #: leave nothing for the rest; it should be a FRACTION of the deadline,
    #: not a constant beside it.
    PEER_WAIT_S = float(os.environ.get("FROGNET_PSY_PEER_WAIT_S", "30"))

    def _peer_wait(self) -> float:
        """How long one peer read may block.

        [A_WAIT_CAP_MUST_FOLLOW_THE_DEADLINE_V1], corrected by measurement.

        The first version returned timeout_s * 0.5 -- 900 s against the
        default 1800 s process-group timeout, where the constant it replaced
        was 30. That is not a longer wait, it is a different regime: a
        prefetcher whose peer never publishes used to die at 30 s, clear
        `_prefetching`, and let the collective read directly. At 900 s it
        holds the thread instead, and PyTorch's suite went from passing to
        ConnectionRefused across three families.

        So the cap stays 30 s by default, which is what every passing
        measurement was taken with. It is now a knob rather than seven
        literals, for the mesh case where a peer legitimately falls further
        behind than that -- but raising it changes teardown behaviour and
        should be done with a measurement, not an inference. Mine was an
        inference and it was wrong.
        """
        return max(1.0, min(self.PEER_WAIT_S, self._timeout_s))

    #: [A_DIGEST_IS_COMPUTED_WHEN_IT_IS_ASKED_FOR_V1] Verify slice payloads?
    #:
    #: The digest costs crc32 at 0.21 ms/MB on BOTH ends -- 1.7 ms for a 4 MB
    #: tensor -- and it buys two things this path does not use. SAME never
    #: fires here: gradients differ every round, and every measured run on
    #: this workload reports same_replies 0 against data_replies in the
    #: hundreds. What it does still buy is integrity: proof the bytes on the
    #: wire are the bytes that were published.
    #:
    #: Default ON. A corrupted payload with verification off is a wrong
    #: answer rather than an error, and this transport has already produced
    #: one framing desync. FROGNET_PSY_VERIFY=0 trades that for the time.
    VERIFY = os.environ.get("FROGNET_PSY_VERIFY", "1") not in ("0", "no")

    def _stage(self, rows: int, ln: int, like):
        """[ALLOCATE_ONCE_NOT_ONCE_PER_FRAME_V1] Landing area for the peer
        slices of one phase, reused across rounds.

        The reads run concurrently, so each needs its own destination -- one
        row per peer. Held on the backend and reallocated only when the shape
        or dtype changes, which for a fixed bucket size is never. Not
        cleared: read_into overwrites every element before anything reads it,
        so clearing would add a pass to no purpose.
        """
        key = (rows, ln, str(like.dtype))
        buf = getattr(self, "_stage_buf", None)
        if buf is None or getattr(self, "_stage_key", None) != key:
            buf = torch.empty(rows * ln, dtype=like.dtype)
            self._stage_buf, self._stage_key = buf, key
        return [buf[i * ln:(i + 1) * ln] for i in range(rows)]

    def _slices(self, n: int):
        """(start, length) per rank. Remainder to the low ranks, so every
        rank computes the identical split from n and W alone -- nothing is
        agreed, it is derived."""
        base, rem = divmod(n, self._size)
        out, start = [], 0
        for k in range(self._size):
            ln = base + (1 if k < rem else 0)
            out.append((start, ln))
            start += ln
        return out


    def _read_slice_into(self, dst, r: int, name: str, gen: int,
                         wait_s: float) -> None:
        """[ALLOCATE_ONCE_NOT_ONCE_PER_FRAME_V1] The same read, writing where
        the answer goes.

        _read_slice allocates a receive buffer per call, builds a tensor over
        it, and the caller copies that into place -- an allocation and two
        passes over the payload. read_into reuses one buffer per client and
        copies once, into `dst`.
        """
        d = self._rendezvous()[r]
        self._peer_client(r).read_into(
            dst, "r%d" % r, d["host"], int(d["port"]), name, gen,
            mode=READ_CURRENT, wait_s=wait_s, verify=self.VERIFY)

    def _allreduce_sharded(self, t: torch.Tensor, seq: int, op) -> None:
        """Reduce-scatter by read, then all-gather by read."""
        # [ASK_THE_CODE_THAT_ALREADY_KNOWS_V1] _op_name exists on the base
        # backend and resolves a ReduceOp against RedOpType. Writing a
        # second identifier here that matched on str(op) got the answer
        # wrong for every op including SUM, because a ReduceOp's str() is
        # its repr.
        #
        # Accumulation here is pairwise rather than one reduction over a
        # stack, so the op must be associative and element-wise. All of
        # these are; AVG is not, because a mean of means is not the mean
        # unless the slices are equal, and the remainder makes them
        # unequal. Saying so beats a wrong answer delivered quickly.
        name = self._op_name(op)
        _ACC = {
            "SUM": lambda a, b: a.add_(b),
            "PRODUCT": lambda a, b: a.mul_(b),
            "MIN": lambda a, b: torch.minimum(a, b, out=a),
            "MAX": lambda a, b: torch.maximum(a, b, out=a),
            "BAND": lambda a, b: a.bitwise_and_(b),
            "BOR": lambda a, b: a.bitwise_or_(b),
            "BXOR": lambda a, b: a.bitwise_xor_(b),
        }
        acc_fn = _ACC.get(name)
        if acc_fn is None:
            raise NotImplementedError(
                "FROGNET_PSY_REDUCE=shard cannot express ReduceOp %r "
                "pairwise. Use FROGNET_PSY_REDUCE=stack for it." % name)
        W, r = self._size, self._rank
        flat = t.reshape(-1)
        parts = self._slices(flat.numel())
        inc = self._peer_inc(r)
        wait = self._peer_wait()
        pool = self._pool()

        # Phase 1. Publish this rank's contribution as W named slices, so a
        # peer can take the one it owns and nothing else.
        for k, (st, ln) in enumerate(parts):
            if ln:
                self._server.publish("%s.ars.%d.%d" % (inc, seq, k), seq,
                                     flat[st:st + ln], incarnation=str(r))

        # [REDUCE_WHERE_THE_ANSWER_GOES_V1] The owned slice is reduced IN
        # PLACE, in the output tensor.
        #
        # This cloned it into `acc`, accumulated there, and copied `acc`
        # back into `flat` in phase 2 -- two passes over n/W to avoid
        # touching a tensor that was already safe to touch. Every byte a
        # peer can still read was published as a clone above, so `flat` is
        # this rank's to write from here on.
        #
        # `flat[st:st+ln]` is a view, so add_ on it writes through to `t`.
        st_r, ln_r = parts[r]
        own = flat[st_r:st_r + ln_r]
        if ln_r:
            # Peer slices land in a staging buffer allocated once, not one
            # tensor per peer per round. They cannot be accumulated straight
            # into `own` because the reads run concurrently and add_ is not
            # a place two threads may meet.
            stage = self._stage(W - 1, ln_r, own)
            futs = {}
            for i, p in enumerate([q for q in range(W) if q != r]):
                futs[p] = pool.submit(
                    self._read_slice_into, stage[i], p,
                    "%s.ars.%d.%d" % (self._peer_inc(p), seq, r), seq, wait)
            for i, p in enumerate([q for q in range(W) if q != r]):
                futs[p].result()
                acc_fn(own, stage[i])

        # Phase 2. The reduced slice is this rank's to serve. Everyone else
        # reads it; this rank reads theirs.
        #
        # [WRITE_WHERE_THE_ANSWER_GOES_V1] This built `out = flat.clone()`,
        # scattered into it, then `t.copy_(out)` -- two full passes over the
        # tensor to produce a result that could have been written where it
        # belongs. `flat` is a view of `t`, so assigning into it IS writing
        # the answer.
        #
        # Safe only in this order: every byte a peer can still read was
        # published as a CLONE above, so mutating `t` now cannot change what
        # anyone else sees. Publishing views instead of clones would break
        # exactly this, which is why they stay clones.
        self._server.publish("%s.arr.%d" % (inc, seq), seq, own,
                             incarnation=str(r))
        futs = {}
        for p in range(W):
            if p == r:
                continue
            st_p, ln_p = parts[p]
            if not ln_p:
                continue
            # Each peer's reduced slice is written straight into the
            # destination, so no two threads touch the same elements and no
            # buffer is shared between them: one client per (peer, thread),
            # one scratch per client, disjoint slices of `flat`.
            futs[p] = pool.submit(
                self._read_slice_into, flat[st_p:st_p + ln_p], p,
                "%s.arr.%d" % (self._peer_inc(p), seq), seq, wait)
        for p, f in futs.items():
            f.result()

    def _inbox_get(self, r: int, seq: int, like: torch.Tensor,
                   timeout_s: float) -> torch.Tensor:
        """The peer's contribution for this round, from local RAM if the
        prefetcher already has it, otherwise read here and now."""
        deadline = time.time() + timeout_s
        with self._inbox_cv:
            self._want = max(self._want, seq)
            self._inbox_cv.notify_all()
            while time.time() < deadline:
                got = self._inbox.pop((r, seq), None)
                if got is not None:
                    return got.reshape(like.shape).to(like.dtype)
                if not self._prefetching:
                    break
                # [A_SAFETY_NET_IS_NOT_A_SCHEDULE_V1] This waited 0.05 s.
                # The prefetcher notifies on every arrival, so the timeout is
                # only there in case a notify is missed -- but a 50 ms
                # granularity turned it into the schedule: profiled at 16 K
                # elements, time.sleep was 21% of an all_reduce, the single
                # largest line, above the socket read and the arithmetic.
                self._inbox_cv.wait(0.0005)
        # No prefetcher, or it has not got there: do it directly.
        return self._read_peer(
            r, "%s.%s.%d" % (self._peer_inc(r), "ar", seq), seq, like,
            wait_s=self._peer_wait())

    def _start_prefetch(self, like: torch.Tensor, seq: int) -> None:
        """[A_PREFETCHER_IS_BOUND_TO_A_SHAPE_V1]

        A prefetcher reads the NEXT generation, which means it must state a
        shape before that generation exists. It can only do that by assuming
        the next tensor looks like this one -- true within a run of equal
        buckets, false the moment the shape changes.

        This used to bind the shape on the first call and return early on
        every call after it. When the shape then changed, every prefetcher
        kept asking for the old one, the read raised, the thread returned,
        and `_prefetching` stayed True with nothing left to fetch: readers
        sat in `_inbox_get` waiting on an inbox that no longer had an author,
        for the full 300 s timeout, per peer, per round. DDP hits this on its
        second differently-sized bucket; a size sweep hits it on its second
        size.

        So the shape is part of what a prefetcher IS. A different shape ends
        the current set and starts another, and the inbox is dropped with it
        because entries fetched under the old shape cannot answer the new
        one.
        """
        shape = list(like.shape)
        dtype = str(like.dtype)
        with self._inbox_cv:
            if self._prefetching and (shape != self._shape
                                      or dtype != self._dtype):
                # End the current epoch. Threads blocked in a socket read
                # notice when it returns; they check the epoch before
                # publishing anything, so a late arrival cannot land in the
                # new epoch's inbox.
                self._pf_epoch += 1
                self._inbox.clear()
                self._prefetching = False
                self._inbox_cv.notify_all()
            if self._prefetching:
                return
            self._pf_epoch += 1
            epoch = self._pf_epoch
            self._shape = shape
            self._dtype = dtype
            self._prefetching = True
            self._pf_live = self._size - 1
            if self._pf_live <= 0:
                # World of one: nothing to fetch, and saying otherwise would
                # make every reader wait for an author that cannot exist.
                self._prefetching = False
                return
        for r in range(self._size):
            if r == self._rank:
                continue
            t = threading.Thread(target=self._prefetch_peer,
                                 args=(r, epoch, seq),
                                 daemon=True,
                                 name="psy-pf-r%d-from-r%d-e%d"
                                      % (self._rank, r, epoch))
            t.start()
            self._pf_threads.append(t)

    def _prefetch_peer(self, r: int, epoch: int, start_seq: int) -> None:
        """One peer's prefetcher, for one shape.

        `start_seq` is the collective this epoch began at, and the cursor
        starts one BEFORE it so the first read is start_seq itself -- the
        round whose caller is about to ask. Starting at start_seq reads
        start_seq+1 and skips the one that is needed, which costs a full
        timeout before the reader gives up and fetches it directly; starting
        at zero re-reads generations published under the OLD shape and dies
        on the first of them.
        """
        seq = start_seq - 1
        shape, dtype = self._shape, self._dtype
        like = torch.zeros(shape, dtype=getattr(
            torch, dtype.replace("torch.", "")))
        try:
            while not self._stop.is_set():
                with self._inbox_cv:
                    if epoch != self._pf_epoch:
                        return
                    while (not self._stop.is_set()
                           and epoch == self._pf_epoch
                           and seq >= self._want + self.LOOKAHEAD):
                        # Woken by _inbox_get when the application advances.
                        self._inbox_cv.wait(0.002)
                    if epoch != self._pf_epoch:
                        return
                if self._stop.is_set():
                    return
                seq += 1
                try:
                    got = self._read_peer(
                        r, "%s.%s.%d" % (self._peer_inc(r), "ar", seq), seq,
                        like, wait_s=self._peer_wait())
                except Exception:
                    # A peer that stops publishing ends this prefetcher. The
                    # collective path reads directly and reports the real
                    # failure there, where a caller is waiting to hear it --
                    # which only works if `_prefetching` is cleared, and it
                    # is, in the finally below.
                    return
                with self._inbox_cv:
                    # The shape may have changed while this read was in
                    # flight. A tensor fetched for the old shape cannot
                    # answer the new one, so it is dropped rather than
                    # handed to a reader who would fail to reshape it.
                    if epoch != self._pf_epoch:
                        return
                    self._inbox[(r, seq)] = got
                    self._inbox_cv.notify_all()
        finally:
            # This thread's sockets go with it. Left open, an epoch change
            # would leak one per peer per epoch, and a later thread reusing
            # the same OS thread id would inherit a socket mid-frame.
            self._drop_peer_clients()
            # [A_DEAD_PREFETCHER_MUST_SAY_SO_V1] `_inbox_get` waits while
            # `_prefetching` is true and reads directly when it is not. A
            # prefetcher that exits without clearing it leaves every reader
            # waiting out the full timeout for state nobody is fetching --
            # which is indistinguishable, from the outside, from a hang.
            with self._inbox_cv:
                if epoch == self._pf_epoch:
                    self._pf_live -= 1
                    if self._pf_live <= 0:
                        self._prefetching = False
                self._inbox_cv.notify_all()

    def _peer_client(self, r: int) -> TensorClient:
        """[BLAST_IT_ALL_AT_RAM_V1] One client per peer, PER THREAD.

        Reads to different peers share nothing -- no link table, no cache,
        no lock -- so they can all be in flight at once. That is why this is
        fast, and it is also why a client cannot be shared between threads:
        with no lock, two threads on one socket interleave their frames, and
        the next length header is read out of the middle of the other's
        payload. The symptom is a MemoryError on `bytearray(n)` inside
        `_recv_exact` -- an allocation for a length that was never a length.
        
        Keyed by peer alone, this was latent: the only reader was the
        prefetcher, because `_inbox_get`'s direct-read path was unreachable
        while `_prefetching` stayed true forever. Fixing that made the main
        thread a second reader on the same socket, and the desync was
        immediate.

        Keyed by thread as well, a prefetcher and the collective never touch
        the same socket. Prefetch threads close theirs on the way out, so
        an epoch change does not leak one per peer per epoch.
        """
        key = (r, threading.get_ident())
        c = self._clients.get(key)
        if c is None:
            c = TensorClient("psy-r%d-to-r%d-t%d"
                             % (self._rank, r, threading.get_ident()))
            self._clients[key] = c
        return c

    def _drop_peer_clients(self) -> None:
        """Close this thread's clients. Called by a prefetcher as it exits."""
        me = threading.get_ident()
        for key in [k for k in self._clients if k[1] == me]:
            c = self._clients.pop(key, None)
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass

    def _read_peer(self, r: int, name: str, gen: int,
                   like: torch.Tensor, wait_s: float = 0.0) -> torch.Tensor:
        """Ask rank r for this state and wait for it. No store involved."""
        d = self._rendezvous()[r]
        peer = "r%d" % r
        cli = self._peer_client(r)
        self._seed_have(peer, d["host"], int(d["port"]), name, cli)
        t, _n, _same = cli.read(
            peer, d["host"], int(d["port"]), name, gen,
            list(like.shape), str(like.dtype), mode=READ_CURRENT,
            wait_s=wait_s)
        key = "%s@%s:%d" % (peer, d["host"], int(d["port"]))
        got = cli._cache.get((key, name))
        if got is not None:
            self._have[(peer, op_of(name))] = got
        return t.reshape(like.shape).to(like.dtype)

    def _publish_tensor(self, op: str, seq: int, t: torch.Tensor) -> None:
        """[ONE_RUNTIME_NOT_TWO_V1] The bytes stay on the plane; the space
        gets only where to find them."""
        # [A_STABLE_NAME_IS_WHAT_MAKES_SAME_POSSIBLE_V1]
        # This named the cell per ROUND, so a reader's digest cache -- keyed
        # by (worker, name) -- could never hit and every reply was DATA.
        # Measured: same_replies 0 across 5 rounds of an all_reduce whose
        # input never changed, 3.87 MB on the wire for 2 MB of distinct
        # tensor.
        #
        # One name per rank per operation. The generation still moves, so
        # identity stays unambiguous, but a reader holding these exact bytes
        # says so and gets a 21-byte SAME instead of the tensor.
        # [THE_DEPTH_IS_THE_KEY_V1] Write your truth and move on.
        #
        # A stable name made SAME fire -- 4x suppression, 196 KB sent for
        # 786 KB offered -- and crashed three of four ranks, because depth-1
        # retention meant the producer replaced the state a peer was reading.
        # The instinct was then to make the producer WAIT until its readers
        # were done. That is a synchronisation, and it is the thing this
        # whole model exists to avoid.
        #
        # The round is the key. Every generation is its own cell, nothing is
        # ever overwritten, and there is nobody to race with: a producer
        # publishes and carries on, and a slow reader finds what it was
        # promised because that cell was never touched again.
        #
        # SAME then has to be about CONTENT rather than about the name --
        # see _seed_have below. Those are separable, and conflating them is
        # what made the stable name look necessary.
        self._round_started.setdefault((op, seq), time.time())
        self._gen += 1
        name = "%s.%s.%d" % (self._incarnation, op, seq)
        self._server.publish(name, self._gen, t, incarnation=str(self._rank))
        # [WHEN_IS_PART_OF_WHAT_V1] The write carries its own timestamp.
        #
        # Cells are per-generation and are never overwritten or reclaimed,
        # so the space accumulates every round a run ever did. Matching on
        # PRESENCE alone means a reader eventually mistakes an old cell for
        # a contribution -- the same presence-versus-currency confusion that
        # produced a departure report for a peer that had merely been
        # declined.
        #
        # Two sources, and both are used below: `t` here, written by the
        # producer, and the store's own envelope (`ts_env`, `age_s`), which
        # costs nothing because get_all already returns it.
        self._put(op, seq, {"host": self._server_host,
                            "port": self._server.port,
                            "name": name, "gen": self._gen,
                            "t": time.time(),
                            "shape": list(t.shape), "dtype": str(t.dtype)})
        # [DO_NOT_WRITE_WHAT_NOBODY_READS_V1] `self._mark_done(op, seq)`
        # was here. It wrote a store cell per rank per round and the only
        # reader, `_wait_done`, is never called -- the done-markers
        # experiment, kept after it was rejected. It measured 32.1 -> 50.4 ms
        # when it was tried, and every broadcast and reduce has been paying
        # a store round trip for it since.

    def _consumed(self, op: str, seq: int) -> None:
        """Mark that this rank has taken what it needed from the plane."""
        T.put(self._service(op + ".done", seq), _scope(self._rank),
              _scope(self._rank), {"n": 1}, dbhost=self._dbhost, own=False)

    def _await_consumers(self, op: str, seq: int) -> None:
        """[A_PRODUCER_ON_A_PLANE_OUTLIVES_ITS_READERS_V1]
        Do not leave until everyone has taken it.

        With payload in the SPACE, a contribution outlived its producer --
        the cell was simply still there. With payload on the PLANE it lives
        in the producer's process, so a root that published and returned
        could be torn down while a peer was still fetching:

            ConnectionRefusedError: [Errno 111] Connection refused
              tensor_plane.read -> _link -> create_connection

        That is lifecycle invariant 4 in a new form, and it is the price of
        taking payload off the control plane. It is paid here, once, by the
        producer waiting for presence of a small consumed-marker per reader
        -- flat cells, one read per poll, no payload. Not an acknowledgement
        protocol: the reader publishes that it is done, and absence is what
        the producer waits on, as everywhere else.
        """
        want = set(r for r in range(self._size) if r != self._rank)
        if not want:
            return
        deadline = time.time() + self._timeout_s
        wait = 0.00005
        while time.time() < deadline:
            seen = set(self._read_all(op + ".done", seq).keys())
            if want.issubset(seen):
                return
            time.sleep(wait)
            wait = min(wait * 2.0, 0.002)
        raise RuntimeError(
            "psychedelic backend: %s seq %d -- rank %d still holds bytes "
            "nobody collected; readers %s never reported consuming them"
            % (op, seq, self._rank, sorted(want - seen)))

    def _seed_have(self, peer: str, host: str, port: int, name: str,
                   cli=None) -> None:
        """[SAME_IS_ABOUT_CONTENT_NOT_ABOUT_NAMES_V1]

        TensorClient offers `have=<digest>` from a cache keyed by (peer,
        cell name). With one cell per generation the name is new every
        round, so the lookup always missed and every reply was DATA even
        when the bytes were identical -- measured, same_replies 0 across
        five rounds of an all_reduce whose input never changed.

        The digest a reader holds for a PEER does not stop being true
        because the peer wrote it into a new cell. So carry it forward:
        whatever we last got from this peer for this operation is offered
        against the new cell. If the peer's bytes are unchanged the server
        answers SAME -- 21 bytes -- and nothing crosses.

        Nothing about identity is weakened. The server still compares the
        digest of the state actually requested; SAME is only ever sent when
        the reader demonstrably holds those exact bytes.
        """
        # Both halves: the digest we offer AND the bytes it stands for. A
        # SAME reply returns the cached tensor, so seeding the digest alone
        # hands back None -- all four ranks died in reshape().
        prev = self._have.get((peer, op_of(name)))
        if prev is None or prev[1] is None:
            return
        key = "%s@%s:%d" % (peer, host, port)
        c = cli if cli is not None else self._client
        if (key, name) not in c._cache:
            c._cache[(key, name)] = prev

    def _read_fresh(self, op: str, seq: int, since: float,
                    wait_s: float = 0.0) -> Dict[int, Any]:
        """[WHEN_IS_PART_OF_WHAT_V1] Cells for this round, written since it
        began -- not merely cells that exist.

        Checks both clocks and takes the stricter. The producer's own `t` is
        authoritative about when it published; the envelope's `ts_env` is
        the store's, at one-second resolution, and is there whether or not a
        producer bothered to stamp its value. A cell that fails either is
        not this round's.
        """
        out: Dict[int, Any] = {}
        # [ASK_ONCE_AND_WAIT_V1] Let the store hold the question open until
        # every rank has published, instead of asking again every couple of
        # milliseconds. Measured before: 2-5 polls per collective, 5-10 ms,
        # a third of a small all_reduce spent on the question rather than
        # the work.
        #
        # `min_rows` is the world size because a collective needs every
        # rank. A store that does not implement waiting answers immediately
        # and the caller's loop below still runs, so this degrades to
        # polling rather than breaking.
        for r in T.get_all(self._service(op, seq), dbhost=self._dbhost,
                           wait_s=wait_s, min_rows=self._size):
            v = r.get("var", "")
            if not v.startswith("r"):
                continue
            val = r.get("value")
            if not isinstance(val, dict):
                continue
            # [WHEN_IS_PART_OF_WHAT_V1] The producer's own clock is not
            # evidence. `wrote` is time.time() on the machine that published;
            # `since` is time.time() here. Comparing them makes a peer whose
            # clock runs a second slow look stale to everyone, and the
            # collective then waits out its timeout reporting peers that
            # never published -- which is not what happened.
            #
            # frognet_tuples.get_all already fixed exactly this by moving to
            # the store's envelope, which is one clock for all writers. That
            # is the timestamp to judge by, and it is already applied above.
            # This second check re-imported the defect on the reduce path.
            ts = r.get("ts_env")
            if ts is not None and ts + 1 < int(since):
                continue                      # store agrees it is older
            try:
                out[int(v[1:])] = val
            except ValueError:
                continue
        return out

    def _gather_tensors(self, op: str, seq: int,
                        like: torch.Tensor) -> List[torch.Tensor]:
        """Descriptors first -- that is the barrier, and it costs a flat
        read. Bytes second, and only then."""
        # [WHEN_IS_PART_OF_WHAT_V1] Wait for every rank to have published
        # THIS round, judged by when the cell was written rather than by
        # whether one exists. `since` is taken before this rank publishes,
        # so its own contribution is inside the window too.
        since = self._round_started.pop((op, seq), time.time())
        deadline = time.time() + self._timeout_s
        wait = 0.00005
        seen: Dict[int, Any] = {}
        want = set(range(self._size))
        while time.time() < deadline:
            self.polls += 1
            _t0 = time.perf_counter()
            # First ask blocks until the set is complete; if the store does
            # not wait, the loop falls back to asking again.
            _blk = os.environ.get("FROGNET_PSY_BLOCKREAD", "1") != "0"
            seen = self._read_fresh(
                op, seq, since,
                wait_s=min(2.0, self._timeout_s) if _blk else 0.0)
            self.poll_s += time.perf_counter() - _t0
            # [COMPLETION_IS_A_RANK_SET_NOT_A_COUNT_V1]
            if want.issubset(seen.keys()):
                break
            time.sleep(wait)
            wait = min(wait * 2.0, 0.002)
        else:
            raise RuntimeError(
                "psychedelic backend: %s seq %d incomplete after %.0fs -- "
                "ranks %s published nothing since this round began. A "
                "collective requires every rank; absence is how this "
                "backend learns one is missing."
                % (op, seq, self._timeout_s, sorted(want - set(seen))))
        descs = [seen[r] for r in range(self._size)]
        out = []
        for r, d in enumerate(descs):
            if r == self._rank:
                out.append(like.new_tensor(0) if False else None)
                continue
            peer = "r%d" % r
            self._seed_have(peer, d["host"], int(d["port"]), d["name"])
            t, _n, _same = self._client.read(
                peer, d["host"], int(d["port"]), d["name"],
                int(d["gen"]), d["shape"], d["dtype"], mode=READ_CURRENT)
            key = "%s@%s:%d" % (peer, d["host"], int(d["port"]))
            got = self._client._cache.get((key, d["name"]))
            if got is not None:
                self._have[(peer, op)] = got
            out.append(t.reshape(d["shape"]).to(like.dtype))
        return descs, out

    # -- the collectives that carry a tensor ----------------------------

    def allreduce(self, tensors, opts=None):
        """[ONE_MULTIPLEXED_QUESTION_BEATS_N_SERIAL_ONES_V1]

        The store read is not a useless check. It is ONE request that comes
        back with every rank's state, multiplexed by the web server, and in
        its blocking form it is a barrier: one round trip that saves N
        waits.

        Removing it and asking each peer directly cost more than it saved at
        size, because the reads then serialise -- rank 0 waits for rank 1 to
        publish, then waits for rank 2, and at 128 KB the waiting compounds:

             elements   barrier   direct+wait
                1,024   17.5 ms       11.7 ms
               16,384   16.0          15.5
              131,072   29.2          40.4

        So the barrier stays. What rendezvous removed for good is the part
        of the descriptor that never changes: a rank's plane address is
        published once at join, not restated every round. The cell now
        carries only the generation -- the one thing that is new.
        """
        op = getattr(opts, "reduceOp", None)
        for t in tensors:
            # Not _next_seq(): see _ar_seq. Every rank calls all_reduce the
            # same number of times in the same order, which is the same
            # reason they agree on _next_seq -- so counting only all_reduces
            # keeps them in step AND keeps the sequence gapless.
            with self._lock:
                self._ar_seq += 1
                seq = self._ar_seq
            name = "%s.%s.%d" % (self._peer_inc(self._rank), "ar", seq)
            # [ONE_NUMBER_IS_THE_IDENTITY_V1]
            #
            # This published under generation `self._gen` while the NAME
            # carries `seq`, and the reader -- which has no descriptor for
            # this op and so derives everything from the name -- asks for
            # generation `seq`. Two counters, used as one identity.
            #
            # They drift because they count different things: `seq` comes
            # from `_next_seq()`, which `barrier()` also advances, while
            # `_gen` advances only here. One barrier is enough. After it,
            # every reader asks for a generation one higher than the
            # publisher will ever reach under that name, the plane's wait
            # can never be satisfied, and every read burns its full
            # `wait_s` before returning the data anyway.
            #
            # Measured: 30013, 30015, 30002, 29999 ms per all_reduce at
            # 1024 elements -- the 30 s read timeout, once per collective,
            # exactly. The data was correct throughout, because the name is
            # the identity. Only the freshness check was wrong.
            #
            # The name is per-seq, so the generation of that name IS seq.
            # Deriving both from the same number makes them agree by
            # construction rather than by two counters happening to stay in
            # step. `_gen` remains for `_publish_tensor`, whose readers get
            # the generation from the store descriptor and so never had
            # this problem.
            self._server.publish(name, seq, t,
                                 incarnation=str(self._rank))
            # The generation, and nothing the reader already knows.


            # [BLAST_IT_ALL_AT_RAM_V1] All of them, at once.
            #
            # Reading peers one at a time meant waiting for each in turn,
            # and at 128 KB that serialising cost more than the barrier it
            # replaced -- 40.4 ms against 29.2. These are socket waits, not
            # arithmetic: they overlap even on one core, because a thread
            # blocked on recv is not holding the interpreter.
            # [THE_STATE_SHOULD_ALREADY_BE_HERE_V1] The prefetchers have
            # been reading the next round while this rank was computing, so
            # this is usually a dictionary lookup rather than a round trip.
            if self.REDUCE == "shard":
                self._allreduce_sharded(t, seq, op)
                continue
            self._start_prefetch(t, seq)
            parts = [None] * self._size
            parts[self._rank] = t.clone()
            for r in range(self._size):
                if r != self._rank:
                    parts[r] = self._inbox_get(r, seq, t, self._timeout_s)
            t.copy_(self._combine(parts, op))
        return _base._Work(tensors)


    def broadcast(self, tensors, opts=None):
        root = getattr(opts, "rootRank", 0)
        for t in tensors:
            seq = self._next_seq()
            if self._rank == root:
                self._publish_tensor("bc", seq, t)
            deadline = time.time() + self._timeout_s
            wait = 0.00005
            while time.time() < deadline:
                d = self._read_all("bc", seq).get(root)
                if d is not None:
                    if self._rank != root:
                        got, _n, _same = self._client.read(
                            "r%d" % root, d["host"], int(d["port"]),
                            d["name"], int(d["gen"]), d["shape"], d["dtype"],
                            mode=READ_CURRENT)
                        t.copy_(got.reshape(t.shape).to(t.dtype))
                        self._consumed("bc", seq)
                    break
                time.sleep(wait)
                wait = min(wait * 2.0, 0.002)
            else:
                raise RuntimeError(
                    "psychedelic backend: broadcast seq %d -- root %d never "
                    "published a descriptor" % (seq, root))
            if self._rank == root:
                # The bytes are in THIS process. Leaving now can pull them
                # out from under a peer mid-fetch.
                self._await_consumers("bc", seq)
        return _base._Work(tensors)

    def reduce(self, tensors, opts=None):
        root = getattr(opts, "rootRank", 0)
        op = getattr(opts, "reduceOp", None)
        for t in tensors:
            seq = self._next_seq()
            self._publish_tensor("rd", seq, t)
            _descs, parts = self._gather_tensors("rd", seq, t)
            parts[self._rank] = t.clone()
            combined = self._combine([p.reshape(t.shape).to(t.dtype)
                                      for p in parts], op)
            if self._rank == root:
                t.copy_(combined)
        return _base._Work(tensors)

    # [A_SHADOWED_DEFINITION_IS_NOT_A_DEFINITION_V1]
    # An earlier `allgather` stood here and was shadowed by the one below --
    # Python keeps the last definition, so it never ran. It was the only
    # remaining caller of `_read_slice`, which is why that goes too.

    def allgather(self, output_tensors, input_tensors, opts=None):
        """[ONE_RUNTIME_NOT_TWO_V1] Every rank's contribution, on the plane.

        This inherited Stage I's path and put whole tensors through the
        control store as float text -- measured at 256 KB against the
        converted operations on the same run:

            all_reduce   21.7 ms   plane
            broadcast    19.0 ms   plane
            all_gather   14.4 ms   STORE
            scatter     344.0 ms   STORE

        Only three collectives had been converted. The rest were still
        paying the very cost this backend exists to remove.
        """
        for outs, inp in zip(output_tensors, input_tensors):
            seq = self._next_seq()
            name = "%s.%s.%d" % (self._peer_inc(self._rank), "ag", seq)
            self._gen += 1
            self._server.publish(name, self._gen, inp,
                                 incarnation=str(self._rank))
            for r in range(self._size):
                if r == self._rank:
                    outs[r].copy_(inp)
                else:
                    peer_name = "%s.%s.%d" % (self._peer_inc(r), "ag", seq)
                    outs[r].copy_(self._read_peer(
                        r, peer_name, seq, inp,
                        wait_s=self._peer_wait()).reshape(
                            outs[r].shape).to(outs[r].dtype))
        return _base._Work(output_tensors)

    def gather(self, output_tensors, input_tensors, opts=None):
        """Everyone publishes; only the root reads.

        A non-root rank publishes and returns -- it does not wait for the
        root to have read, because the cell outlives the operation and the
        root will find it whenever it gets there. That is the shape a
        message system cannot have: the sender would have to block until
        the receiver was ready.
        """
        root = getattr(opts, "rootRank", 0)
        for i, inp in enumerate(input_tensors):
            seq = self._next_seq()
            name = "%s.%s.%d" % (self._peer_inc(self._rank), "ga", seq)
            self._gen += 1
            self._server.publish(name, self._gen, inp,
                                 incarnation=str(self._rank))
            if self._rank != root or not output_tensors:
                continue
            outs = output_tensors[i] if i < len(output_tensors) \
                else output_tensors[0]
            for r in range(self._size):
                if r == self._rank:
                    outs[r].copy_(inp)
                else:
                    peer_name = "%s.%s.%d" % (self._peer_inc(r), "ga", seq)
                    outs[r].copy_(self._read_peer(
                        r, peer_name, seq, inp,
                        wait_s=self._peer_wait()).reshape(
                            outs[r].shape).to(outs[r].dtype))
        return _base._Work(output_tensors)

    def scatter(self, output_tensors, input_tensors, opts=None):
        """[ONE_CELL_MANY_READERS_V1] The root publishes ONCE; each rank
        reads it and takes its own slice.

        Stage I had the root put every rank's slice into one control-plane
        structure -- N tensors as float text, and 344 ms at 256 KB. Here the
        root concatenates them into a single state on the plane, publishes
        it once, and each reader takes the piece addressed to it. One
        materialise serves the whole group, and a reader that already holds
        those bytes gets SAME.

        The root does not send N times, and adding ranks adds no root-side
        sends -- which is the shape a store-and-read model gets for free and
        a send-per-peer model does not.
        """
        root = getattr(opts, "rootRank", 0)
        for i, out in enumerate(output_tensors):
            seq = self._next_seq()
            name = "%s.%s.%d" % (self._peer_inc(root), "sc", seq)
            if self._rank == root:
                ins = input_tensors[i] if i < len(input_tensors) \
                    else input_tensors[0]
                whole = torch.cat([t.detach().reshape(-1) for t in ins])
                self._gen += 1
                self._server.publish(name, self._gen, whole,
                                     incarnation=str(root))
                out.copy_(ins[self._rank].reshape(out.shape).to(out.dtype))
                continue
            k = out.numel()
            like = out.new_empty(k * self._size)
            whole = self._read_peer(root, name, seq, like,
                                    wait_s=self._peer_wait())
            out.copy_(whole[self._rank * k:(self._rank + 1) * k]
                      .reshape(out.shape).to(out.dtype))
        return _base._Work(output_tensors)

    def reduce_scatter(self, output_tensors, input_tensors, opts=None):
        """Every rank publishes its whole input list; each keeps only the
        slice addressed to it, combined across peers."""
        op = getattr(opts, "reduceOp", None)
        for i, out in enumerate(output_tensors):
            ins = input_tensors[i] if i < len(input_tensors) \
                else input_tensors[0]
            seq = self._next_seq()
            whole = torch.cat([t.detach().reshape(-1) for t in ins])
            name = "%s.%s.%d" % (self._peer_inc(self._rank), "rs", seq)
            self._gen += 1
            self._server.publish(name, self._gen, whole,
                                 incarnation=str(self._rank))
            k = out.numel()
            parts = []
            for r in range(self._size):
                if r == self._rank:
                    src = whole
                else:
                    peer_name = "%s.%s.%d" % (self._peer_inc(r), "rs", seq)
                    src = self._read_peer(r, peer_name, seq, whole,
                                          wait_s=self._peer_wait())
                parts.append(src[self._rank * k:(self._rank + 1) * k]
                             .reshape(out.shape).to(out.dtype))
            out.copy_(self._combine(parts, op))
        return _base._Work(output_tensors)

    def getBackendName(self):
        return "psychedelic"

    def plane_stats(self) -> Dict[str, Any]:
        """What the split bought: bytes a reader asked for against bytes that
        crossed. The gap is SAME -- state the reader already held."""
        s = dict(getattr(self._server, "stats", lambda: {})() or {})
        s["polls"] = self.polls
        s["poll_ms"] = round(self.poll_s * 1000, 2)
        s["states_offered"] = self._server.states_offered
        s["states_materialised"] = self._server.states_materialised
        return s


def _create(store, rank, size, timeout):
    # [A_GROUP_MUST_BE_NAMED_BY_ITS_MEMBERS_V1]
    #
    # torch_backend's _create passes group=_group_id(...), a token rank 0
    # mints and every member reads through the store -- unique per process
    # group. This did not, so `self._group` fell to the class default
    # "default" for every group in every process that ever ran. The
    # rendezvous service was `psyc10d.rv.default` always, plane names were
    # `default.c0.ar.1` always, and a cell from a dead process satisfied a
    # live rank's rendezvous.
    #
    # It is also what made the incarnation scoping added above do nothing:
    # scoping by a constant is not scoping. Same token, same agreement,
    # same mechanism as the Stage I backend -- there was never a reason for
    # these two to differ.
    return PsychedelicBackend(store, rank, size, timeout,
                              group=_base._group_id(store, size, rank))


def register(name: str = "psychedelic") -> None:
    dist.Backend.register_backend(name, _create, devices=["cpu"])
