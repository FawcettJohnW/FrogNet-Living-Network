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
agent_workload/tuplespace/ddp_hook.py -- PyTorch's interface, FrogNet's
contract underneath.

[THE_HOOK_IS_WHERE_THE_CONTRACT_CHANGES_V1]

Everything measured so far lives under `all_reduce`, and `all_reduce` owes
the exact sum over every rank. That is why the wins were all about moving
the same bytes more cheaply, and why they stopped at roughly 3.8x of gloo:

    lossless delta compression   1.27x on real training tensors, and 21 ms
                                 of work to save 0.6 ms of transfer
    selective consumption        changes the sum -- forbidden
    quantised gradients          changes the sum -- forbidden

There is no route to beating a collective inside the collective's contract.
Once every byte must arrive exactly, the best available is to move it well.

`DistributedDataParallel.register_comm_hook` is PyTorch's own boundary for
this. The hook is handed a gradient bucket and returns a
`Future[torch.Tensor]`; what happens in between is the implementer's.
PyTorch's documentation names gradient compression and GossipGrad as the
intended uses -- neither of which is an all-reduce.

So DDP keeps its interface, its reducer, its bucketing and its optimizer
step, and underneath it we are free to answer the question FrogNet is
actually good at: given what every peer has published, what is worth
incorporating?

WHAT THE HOOK OWES, AND WHAT IT DOES NOT

It owes a tensor of the bucket's shape that training can proceed with. It
does NOT owe the arithmetic mean of every rank's gradient -- DDP does not
inspect how the number was reached, and PyTorch ships hooks that quantise,
that compress, and that talk to a subset of peers.

That is the whole difference. `all_reduce` is a specification of a
computation. A comm hook is a specification of a role.

THE HOOKS HERE

    faithful      publish, read every peer, average. Identical arithmetic to
                  all_reduce, so it is the control: any difference the other
                  hooks show is theirs, not the plumbing's.

    fresh_only    skip a peer whose generation has not moved since this rank
                  last incorporated from it. In the native study this
                  avoided 38.4% of consumption with no measured degradation.
                  It is meaningless inside all_reduce and legitimate here.
"""

# NO `from __future__ import annotations` in this module.
#
# DDP compares the hook's return annotation against the TYPE OBJECT
# torch.futures.Future[torch.Tensor] (distributed.py _check_comm_hook). With
# postponed evaluation the annotation is the string
# "torch.futures.Future[torch.Tensor]", which is not equal to the type, and
# a correctly written hook is refused before it runs. The bucket annotation
# is checked the same way against dist.GradBucket.

import os
import threading
import time
from typing import Any, Dict, List, Optional

# DDP inspects the hook's return ANNOTATION, not only what it returns:
# _check_comm_hook rejects anything that is not
# torch.futures.Future[torch.Tensor]. A hook that behaves correctly and is
# annotated loosely is refused before it ever runs.

import torch
import torch.distributed as dist

from . import frogtorch_ram_client as T  # [ALL_IN_THE_REGION_V1] FrogTorch's own RAM
from .frogtorch_ram_client import Placement, RegionPlane

from .tensor_plane import READ_CURRENT, TensorClient

SERVICE = os.environ.get("FROGNET_HOOK_SERVICE", "ddphook")


class FrogNetHookState:
    """What the hook needs to know, and nothing about PyTorch.

    Rendezvous happens once: every rank publishes where its plane is and
    reads the set back. After that no collective operation consults the
    store, and the hook talks to peers directly.
    """

    def __init__(self, rank: int, world: int, dbhost: Optional[str] = None,
                 group: str = "ddp", timeout_s: float = 300.0):
        self.rank = rank
        self.world = world
        self.group = group
        self.timeout_s = timeout_s
        self.dbhost = (dbhost or os.environ.get("FROGNET_C10D_DBHOST")
                       or T.DEFAULT_DBHOST)
        # [ALL_IN_THE_REGION_V1] Buckets go into this rank's region; peers read them there, not from this process.
        self.ram = T.ram()
        self.server = RegionPlane(self.ram, rank, where=self._my_region)
        self.clients: Dict[int, TensorClient] = {}
        self.peers: Optional[Dict[int, Any]] = None
        self.seq = 0
        self.gen = 0
        #: [THE_GENERATION_IS_PER_CELL_V1] How many times this rank has
        #: published each bucket. Every hook publishes every bucket exactly
        #: once per step, before any skip decision, so this count IS the
        #: step number for that bucket -- and it is the same number on every
        #: rank. That makes it the generation a reader can wait on.
        self.pub_n: Dict[int, int] = {}
        #: what this rank last incorporated from each peer, for fresh_only
        self.last_taken: Dict[int, int] = {}
        #: [COUNT_THE_WORK_NOT_DONE_V1] one run's own saving, not a
        #: difference between two runs
        self.offered = 0
        self.taken = 0
        self.skipped = 0
        #: [ONE_ASSEMBLY_NOT_N_QUESTIONS_V1] This rank's step counter, and
        #: the last bag read from every peer. A step here is one call of the
        #: hook for bucket 0 -- DDP hands buckets out once per step, so the
        #: first bucket's publish count is the step number.
        self.step = 0
        self.bags: Dict[int, Any] = {}
        #: How many assemblies were answered entirely from SAME. The bag is
        #: nearly constant between steps, so this should approach 1.0 and is
        #: the measurement that says the control plane is affordable.
        self.bag_reads = 0
        self.bag_same = 0
        #: [A_SKIP_IS_A_DECISION_ABOUT_THE_FUTURE_V1] per-state so a test can
        #: sweep it without touching the environment
        self.FRESH_MAX_DRIFT = MAX_DRIFT

    def client(self, r: int) -> TensorClient:
        c = self.clients.get(r)
        if c is None:
            c = TensorClient("hook-r%d-to-r%d" % (self.rank, r))
            self.clients[r] = c
        return c

    def rendezvous(self) -> Dict[int, Any]:
        """[ALL_IN_THE_REGION_V1] Where each rank's buckets live: every rank writes its member cell and holds one
        read until all are there. Derived, not exchanged."""
        if self.peers is not None:
            return self.peers
        pl = Placement(self.ram, "hook.%s" % self.group, self.rank, self.world, self.timeout_s)
        self.peers = {r: {"host": pl.region(r)[0], "port": pl.region(r)[1]} for r in range(self.world)}
        self.server.region = pl.region(self.rank)
        return self.peers

    def _my_region(self):
        d = self.rendezvous()[self.rank]
        return (d["host"], int(d["port"]))

    def bag_publish(self, step: int, done: bool = False) -> None:
        """[ONE_ASSEMBLY_NOT_N_QUESTIONS_V1] This rank's progress, in one
        cell, for everybody.

        Flat, constant-sized, no payload -- what a control plane is for. The
        bucket set does not change for the life of a run, so from the second
        step onward the only thing that moves is the counters.
        """
        T.put("%s.bag.%s" % (SERVICE, self.group),
              "r%d" % self.rank, "r%d" % self.rank,
              {"step": step, "pub_n": dict(self.pub_n),
               "taken": self.taken, "skipped": self.skipped,
               "done": bool(done), "t": time.time()},
              dbhost=self.dbhost, own=False)

    def bag_read(self, wait_s: float = 0.0, min_rows: int = 0) -> Dict[int, Any]:
        """Every rank's bag, in one round trip.

        `min_rows` lets the store hold the question open until that many
        ranks have published -- so this is a barrier when a policy wants one
        and is not when it does not. Reading N peers individually would be N
        conversations, each blocking on its own link, to discover which
        links are slow.
        """
        self.bag_reads += 1
        out: Dict[int, Any] = {}
        for row in T.get_all("%s.bag.%s" % (SERVICE, self.group),
                             dbhost=self.dbhost, wait_s=wait_s,
                             min_rows=min_rows or 0):
            v = row.get("var", "")
            d = row.get("value")
            if v.startswith("r") and isinstance(d, dict):
                try:
                    out[int(v[1:])] = d
                except ValueError:
                    pass
        if out:
            self.bags = out
        return self.bags

    def drift(self, r: int, key: int) -> Optional[int]:
        """How many steps peer `r` is behind this rank on bucket `key`.

        Positive means this rank is ahead. None means no bag from that peer
        yet. Computed locally from the last assembly -- no read of that
        peer's plane, which is the point: finding out that a peer is slow
        must not cost a conversation with the slow peer.
        """
        bag = self.bags.get(r)
        if not bag:
            return None
        # [AN_OBJECT_IS_NOT_A_LIST_V1 - John 2026-09-14] pub_n is a map from
        # bucket index to publish count, and it arrives with STRING keys
        # because it arrives as JSON. It briefly arrived as a list instead --
        # api.php decoded the body with json_decode($raw, true) and re-encoded
        # it, and PHP's associative array cannot tell {"0":5,"1":5} from
        # [5,5]. Bucket indices are 0..N-1, so this payload hit the one case
        # that does not survive, and fresh_only died on its first step every
        # time. That is fixed in api.php, which is the only place it was ever
        # broken. Nothing here checks for it.
        theirs = (bag.get("pub_n") or {}).get(str(key))
        if theirs is None:
            return None
        return int(self.pub_n.get(key, 0)) - int(theirs)

    def stats(self) -> Dict[str, Any]:
        """[COUNT_THE_WORK_NOT_DONE_V1] What this run actually skipped.

        Both skipping policies have been measured as wall-clock and
        held-out loss with no denominator: "less work incorporated" was a
        claim, not a number. This is the number.
        """
        return {"rank": self.rank, "world": self.world,
                "offered": self.offered, "taken": self.taken,
                "skipped": self.skipped,
                "skipped_pct": (100.0 * self.skipped / self.offered)
                               if self.offered else None,
                "steps": self.step,
                "max_drift": self.FRESH_MAX_DRIFT,
                "drift_seen": {str(r): self.drift(r, 0)
                               for r in sorted(self.bags) if r != self.rank},
                "bag_reads": self.bag_reads,
                "bag_same": self.bag_same,
                "bag_same_pct": (100.0 * self.bag_same / self.bag_reads)
                                if self.bag_reads else None,
                "plane": self.server.stats()
                         if hasattr(self.server, "stats") else {}}

    def linger(self, logger=print) -> bool:
        """[ALL_IN_THE_REGION_V1] Nothing to wait for. A rank's buckets are in its region, not in its process, so a
        rank that finishes first can leave while its peers are still reading them. Kept so callers need not
        change; it publishes the final bag (done) and returns."""
        self.bag_publish(self.step, done=True)
        return True

    def cell(self, rank: int, key: int) -> str:
        """[THE_NAME_MUST_MEAN_THE_SAME_THING_ON_BOTH_ENDS_V1]

        `key` is DDP's own bucket index, not this rank's local counter.

        It used to be the counter, which never resets across steps. A rank
        on step 3 is around seq 3N while a peer on step 2 is at 2N, so it
        asked for a name that peer will not write for another whole step:

            StateGone: r2 does not publish 'h2.b.13' at all

        Never existed, because the reader made the name up out of its own
        position. bucket.index() is assigned by DDP's reducer and is the
        same value on every rank for the same bucket, so the name says which
        bucket rather than how far along the reader happens to be.
        """
        return "h%d.b.%d" % (rank, key)

    # [A_SHADOWED_DEFINITION_IS_NOT_A_DEFINITION_V1] A second stats() stood
    # here and shadowed the one above -- Python keeps the last definition,
    # so the fuller one never ran. Its three fields are all present there.


#: [A_PEER_THAT_IS_BEHIND_IS_NOT_A_PEER_THAT_IS_GONE_V1] How long one
#: peer read may block, in seconds. Empty means the process group's own
#: timeout, which is what the caller actually chose.
#:
#: This was a hardcoded min(30.0, timeout_s). psychedelic_backend removed
#: that literal from seven places for this exact reason: a peer more than
#: 30 s behind is still declared gone, and on a fleet spanning 5x in speed
#: that is a normal amount of behind, not a fault. Measured on ddp1692,
#: first step: rank 0 17.4 s, rank 2 32.9 s. The gap compounds every step,
#: and at 90 s steps a 30 s cap is not a margin, it is a guarantee.
#:
#: The cap exists so one read cannot consume the whole deadline. It should
#: be a fraction of the deadline, not a constant beside it.
HOOK_PEER_WAIT_S = os.environ.get("FROGNET_HOOK_PEER_WAIT_S", "")

#: [A_SKIP_IS_A_DECISION_ABOUT_THE_FUTURE_V1] How many steps a peer may fall
#: behind before fresh_only stops skipping it. 0 means never skip, which
#: makes fresh_only degenerate to faithful and is the control that says how
#: much of the difference is the skipping and how much is everything else. A
#: very large value restores the unbounded behaviour that produced the
#: runaway.
MAX_DRIFT = int(os.environ.get("FROGNET_HOOK_MAX_DRIFT", "2"))


def _peer_wait(state) -> float:
    if HOOK_PEER_WAIT_S:
        return max(1.0, min(float(HOOK_PEER_WAIT_S), state.timeout_s))
    return max(1.0, state.timeout_s)


def _bucket_key(state: "FrogNetHookState", bucket) -> int:
    """[THE_NAME_MUST_MEAN_THE_SAME_THING_ON_BOTH_ENDS_V1] DDP's bucket
    index, which every rank assigns identically for the same bucket.

    Falls back to this rank's local counter only if the reducer does not
    expose an index -- which restores the old failure, so it says so once
    rather than failing silently somewhere else later.
    """
    # [NO_FALLBACK_V1] A per-rank counter is only right when every rank sees buckets in the same order, and is
    # silently wrong otherwise. A DDP without GradBucket.index() is refused, not guessed at.
    return int(bucket.index())


def _publish(state: FrogNetHookState, key: int, t: torch.Tensor) -> int:
    """[THE_GENERATION_IS_PER_CELL_V1] Publish bucket `key`, generation =
    how many times this rank has published THAT bucket.

    A server-wide counter was wrong here in both directions. Passed as the
    generation it meant nothing to a reader, because the peer's count
    depends on how many buckets it happens to have. Left out of the read
    entirely it meant a reader could take the PREVIOUS step's value, since
    `h2.b.13` is the same name on step 1 and step 40.

    Per cell it is exactly the step number for that bucket, identical on
    every rank, and the server's existing wait does the rest.
    """
    n = state.pub_n.get(key, 0) + 1
    state.pub_n[key] = n
    state.gen += 1          # kept: fresh_only's own progress bookkeeping
    # [ONE_ASSEMBLY_NOT_N_QUESTIONS_V1] One bag per step, not per bucket.
    # DDP hands out bucket 0 once per step, so its publish count IS the step
    # number -- nothing has to be counted separately or agreed with anyone.
    if key == 0:
        state.step = n
        state.bag_publish(n)          # [NO_FALLBACK_V1] the bag is in memory now; a failed write is a failure
        # [A_MECHANISM_NOBODY_CALLS_IS_NOT_A_MECHANISM_V1] And read the whole set back: every peer's progress in one
        # read. No min_rows: a rank must not be made to wait in order to find out who is making it wait.
        state.bag_read()
    state.server.publish(state.cell(state.rank, key), n, t,
                         incarnation=str(state.rank))
    return n


def _read(state: FrogNetHookState, r: int, key: int, want_n: int,
          like: torch.Tensor) -> torch.Tensor:
    """Read peer `r`'s bucket `key` as of this step.

    `key` names the cell. `want_n` is the per-cell generation -- this
    bucket's step number -- and the two are different things. Passing the
    bucket index into the generation slot demanded the peer's publish clock
    reach 13 when it was at 4; the wait expired and the reader took whatever
    was current, which was a different bucket of a different size:

        RuntimeError: shape '[1050625]' is invalid for input of size 4216833
    """
    d = state.rendezvous()[r]
    got, _n, _same = state.client(r).read(
        "r%d" % r, d["host"], int(d["port"]), RegionPlane.key(state.cell(r, key), r), want_n,
        list(like.shape), str(like.dtype), mode=READ_CURRENT,
        wait_s=_peer_wait(state))
    return got.reshape(like.shape).to(like.dtype)


def faithful_hook(state: FrogNetHookState,
                  bucket: dist.GradBucket
                  ) -> torch.futures.Future[torch.Tensor]:
    """Every peer, averaged. The control.

    Arithmetically identical to what DDP's default all_reduce produces, so a
    difference in training between this and the stock backend is a defect,
    not a policy.
    """
    t = bucket.buffer()
    key = _bucket_key(state, bucket)
    n = _publish(state, key, t)
    # [REDUCTION_ORDER_IS_RANK_ORDER_V1 - John 2026-09-14] Sum in rank order,
    # the same order on every rank.
    #
    # This built `parts` as [self, then every OTHER rank ascending], so rank 0
    # summed [0,1,2,3], rank 1 summed [1,0,2,3], rank 2 [2,0,1,3]. Float
    # addition is not associative: torch.stack(...).mean(0) reduces the stack
    # in order, so four orders gave four results differing in the last bits.
    # Each rank then took a slightly different optimizer step and the weights
    # diverged over a run.
    #
    # Measured, ddp17xx: gloo's four ranks all reported param_sha
    # 3622a2291d8b592a / heldout 0.4940, while the hook arms came back
    # 73a583f7ab59285d / 0.4991 and 46a94a32156cfc45 / 0.5014 -- different
    # from gloo and from each other. gloo's ring all_reduce is bit-identical
    # across ranks by construction; this was not, and the docstring promising
    # arithmetic identity made that a defect rather than a policy.
    #
    # Indexing by rank fixes it without changing what is summed: the stack is
    # built in slots, so a skipped peer leaves a hole that is dropped, and the
    # survivors keep ascending rank order on every rank.
    slots: List[Optional[torch.Tensor]] = [None] * state.world
    slots[state.rank] = t.clone()
    for r in range(state.world):
        if r == state.rank:
            continue
        state.offered += 1
        slots[r] = _read(state, r, key, n, t)
        state.taken += 1
    parts = [x for x in slots if x is not None]
    out = torch.stack(parts).mean(0)
    fut = torch.futures.Future()
    fut.set_result(out)
    return fut


def fresh_only_hook(state: FrogNetHookState,
                    bucket: dist.GradBucket
                  ) -> torch.futures.Future[torch.Tensor]:
    """[THE_HOOK_IS_WHERE_THE_CONTRACT_CHANGES_V1] Skip a peer that has not
    moved since this rank last took from it.

    Inside `all_reduce` this is forbidden: omitting a contribution changes
    the sum. Here it is a decision about what is worth incorporating, which
    is what a comm hook is for -- and it is the same policy that avoided
    38.4% of consumption in the native study with no measured degradation.

    A peer whose generation has not advanced is offering the state this rank
    already folded in. Reading it again costs a round trip to learn nothing.
    """
    t = bucket.buffer()
    key = _bucket_key(state, bucket)
    n = _publish(state, key, t)
    # [REDUCTION_ORDER_IS_RANK_ORDER_V1 - John 2026-09-14] Sum in rank order,
    # the same order on every rank.
    #
    # This built `parts` as [self, then every OTHER rank ascending], so rank 0
    # summed [0,1,2,3], rank 1 summed [1,0,2,3], rank 2 [2,0,1,3]. Float
    # addition is not associative: torch.stack(...).mean(0) reduces the stack
    # in order, so four orders gave four results differing in the last bits.
    # Each rank then took a slightly different optimizer step and the weights
    # diverged over a run.
    #
    # Measured, ddp17xx: gloo's four ranks all reported param_sha
    # 3622a2291d8b592a / heldout 0.4940, while the hook arms came back
    # 73a583f7ab59285d / 0.4991 and 46a94a32156cfc45 / 0.5014 -- different
    # from gloo and from each other. gloo's ring all_reduce is bit-identical
    # across ranks by construction; this was not, and the docstring promising
    # arithmetic identity made that a defect rather than a policy.
    #
    # Indexing by rank fixes it without changing what is summed: the stack is
    # built in slots, so a skipped peer leaves a hole that is dropped, and the
    # survivors keep ascending rank order on every rank.
    slots: List[Optional[torch.Tensor]] = [None] * state.world
    slots[state.rank] = t.clone()
    for r in range(state.world):
        if r == state.rank:
            continue
        state.offered += 1
        # [A_SKIP_IS_A_DECISION_ABOUT_THE_FUTURE_V1]
        #
        # The old test was "has this peer moved since I last took from it".
        # That is a statement about the past, and on a fleet with one
        # distant member it compounds: Seattle skips New York, Seattle's
        # steps get cheaper, Seattle pulls further ahead, Seattle skips New
        # York more. Measured, same arm, same run -- rank 0 best step
        # 2177 ms against rank 3's 16337 ms, and held-out losses that no
        # longer agree. The policy had a term for "not moved" and none for
        # "falling behind because I keep ignoring it".
        #
        # Drift comes from the assembly, so learning that a peer is slow
        # costs nothing from that peer. Skip while it is within the bound;
        # read when it is not.
        _d = state.drift(r, key)
        if (_d is not None and _d > 0 and _d <= state.FRESH_MAX_DRIFT
                and state.gen > 1):
            state.skipped += 1
            continue
        slots[r] = _read(state, r, key, n, t)
        state.last_taken[r] = state.gen
        state.taken += 1
    parts = [x for x in slots if x is not None]
    out = torch.stack(parts).mean(0)
    fut = torch.futures.Future()
    fut.set_result(out)
    return fut


def every_k_hook(state: FrogNetHookState,
                 bucket: dist.GradBucket) -> torch.futures.Future[torch.Tensor]:
    """[IS_THE_FACT_WORTH_ANYTHING_V1] Consume on every kth bucket, blind.

    The control for `fresh_only`. That policy skips a peer because it KNOWS
    the peer has not moved -- a fact that exists only because the state is
    observable without asking for it. This one skips by counting, which
    needs no shared state and could be written against a collective.

    If the two match at the same participation, freshness carries no
    information and the saving is just "less is fine". In the native study
    the content-based policy won: 0.220 against 0.244 at comparable volume.
    """
    k = int(os.environ.get("FROGNET_HOOK_EVERY_K", "2"))
    t = bucket.buffer()
    key = _bucket_key(state, bucket)
    n = _publish(state, key, t)
    # [REDUCTION_ORDER_IS_RANK_ORDER_V1 - John 2026-09-14] Sum in rank order,
    # the same order on every rank.
    #
    # This built `parts` as [self, then every OTHER rank ascending], so rank 0
    # summed [0,1,2,3], rank 1 summed [1,0,2,3], rank 2 [2,0,1,3]. Float
    # addition is not associative: torch.stack(...).mean(0) reduces the stack
    # in order, so four orders gave four results differing in the last bits.
    # Each rank then took a slightly different optimizer step and the weights
    # diverged over a run.
    #
    # Measured, ddp17xx: gloo's four ranks all reported param_sha
    # 3622a2291d8b592a / heldout 0.4940, while the hook arms came back
    # 73a583f7ab59285d / 0.4991 and 46a94a32156cfc45 / 0.5014 -- different
    # from gloo and from each other. gloo's ring all_reduce is bit-identical
    # across ranks by construction; this was not, and the docstring promising
    # arithmetic identity made that a defect rather than a policy.
    #
    # Indexing by rank fixes it without changing what is summed: the stack is
    # built in slots, so a skipped peer leaves a hole that is dropped, and the
    # survivors keep ascending rank order on every rank.
    slots: List[Optional[torch.Tensor]] = [None] * state.world
    slots[state.rank] = t.clone()
    state.seq += 1
    take = (state.seq % k) == 0
    for r in range(state.world):
        if r == state.rank:
            continue
        state.offered += 1
        if not take:
            state.skipped += 1
            continue
        slots[r] = _read(state, r, key, n, t)
        state.taken += 1
    parts = [x for x in slots if x is not None]
    out = torch.stack(parts).mean(0)
    fut = torch.futures.Future()
    fut.set_result(out)
    return fut


#: [A_MECHANISM_NOBODY_CALLS_IS_NOT_A_MECHANISM_V1] The most recently
#: attached state, for a harness that wants its counters. One per process,
#: which is what DDP allows anyway.
_STATE = None

HOOKS = {"faithful": faithful_hook, "fresh_only": fresh_only_hook,
         "every_k": every_k_hook}


def attach(model, rank: int, world: int, which: str = "faithful",
           **kw) -> FrogNetHookState:
    """Put FrogNet under a DDP model without changing the model."""
    state = FrogNetHookState(rank, world, **kw)
    # [A_MECHANISM_NOBODY_CALLS_IS_NOT_A_MECHANISM_V1] The harness has no
    # other handle on the state -- DDP keeps it privately -- so the stats it
    # needs to record would be unreachable. One module-level reference,
    # written once at attach.
    global _STATE
    _STATE = state
    model.register_comm_hook(state, HOOKS[which])
    return state
