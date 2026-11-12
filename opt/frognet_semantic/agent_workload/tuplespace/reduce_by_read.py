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
agent_workload/tuplespace/reduce_by_read.py -- reduction without a collective.

[REDUCE_BY_READ_V1]

An allreduce is a barrier with arithmetic attached. Every rank must arrive
before any rank leaves, because none of them can see the others' memory and
the only way to agree is to synchronise. Given memory they can all see, that
is not a cheaper allreduce -- it is not an allreduce.

    each worker publishes    torch.grad.<worker>  = {generation, ...}
    each worker reads        the current set
    each worker reduces      locally, from whatever is there
    the OPTIMIZER decides    how much staleness it will accept

Nobody waits for anybody. A worker that is slow contributes an older
generation. A worker that stopped contributes nothing and the others notice
because they are reading, not because a collective threw.

TWO THINGS WERE UNDECIDED AND ARE THEREFORE SWITCHES
----------------------------------------------------
Both are logged on every reduce, so a run says which way it was configured
rather than requiring a second run to find out.

1. WHERE THE TENSOR LIVES -- FROGNET_REDUCE_PAYLOAD
   value       the tensor goes in the cell. Honest for small tensors, and
               the only mode that works before a data plane is wired.
   descriptor  the cell carries shape/dtype/checksum and a locator; the
               bytes ride the permanent path the control plane set up.
               This is the arrangement bulk already uses -- control plane in
               the tuple space, data plane on the socket it arranged.
   auto        (default) descriptor above --reduce-inline-bytes, value below.
               Logged per tensor, so the split point is visible.

   Only `value` is fully implemented here. `descriptor` writes the descriptor
   and raises on READ with the locator it would have used, because inventing
   a transport that is not yet wired would be a fallback pretending to be a
   feature. [NO FALLBACKS]

2. WHAT A GENERATION MEANS -- FROGNET_REDUCE_GENERATION
   step    the optimizer step number. Staleness is "N steps behind", which
           is what an optimizer can reason about.
   tick    a counter the writer advances. Staleness is "N publications
           behind", which is what a writer can guarantee.
   Default step, because the accept/reject decision belongs to the optimizer
   and steps are its units.

The staleness policy is a function, so a caller can supply its own:
    accept(my_generation, their_generation, age_s) -> bool
"""

from __future__ import annotations

import os
import socket
import uuid
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import frogtorch_ram_client as T  # [ALL_IN_THE_REGION_V1] FrogTorch's own RAM
from core import stat_schema

from .finite import (STAGE_PEER_READ, STAGE_PUBLISH, STAGE_REDUCE_OUT,
                     STAGE_SELF_CONTRIB, FiniteWatch)
from .tensor_plane import READ_CURRENT, StateGone

# [IMPORT_GUARD_V2] 2026-09-25: diag is first-party and the tree is always on the path; a stub hid a broken install.
from core.frognet_diag import diag, diag_exc

SERVICE = os.environ.get("FROGNET_TORCH_SERVICE", "torch")

PAYLOAD_MODE = os.environ.get("FROGNET_REDUCE_PAYLOAD", "auto").lower()
GENERATION_MODE = os.environ.get("FROGNET_REDUCE_GENERATION", "step").lower()

#: [THE_THRESHOLD_WAS_WHERE_THE_PENALTY_IS_WORST_V1]
#: Above this many bytes, `auto` uses a descriptor.
#:
#: This was 65536, and the crossover is at about 1024. Measured, publish
#: cost per call:
#:
#:      elements    bytes    value ms   descriptor ms   winner
#:             1        4       0.139         0.239     value
#:           256     1024       0.188         0.212     value      <- crossover
#:          1024     4096       0.365         0.254     descriptor
#:          4096    16384       1.078         0.337     descriptor  3.2x
#:         16384    65536       3.888         0.733     descriptor  5.3x
#:
#: Inline wins below a kilobyte, where the plane's round trip dominates a
#: payload that is barely there. Above it the JSON encoding of a float list
#: takes over and never gives the lead back. The old value put every tensor
#: from 1 KB to 64 KB on the slow path, and the penalty was worst exactly at
#: the boundary -- 5.3x at 65536 bytes.
INLINE_MAX = int(os.environ.get("FROGNET_REDUCE_INLINE_BYTES", "1024"))

#: Default staleness window, in whatever GENERATION_MODE counts.
MAX_LAG = int(os.environ.get("FROGNET_REDUCE_MAX_LAG", "2"))

#: [PRESENCE_IS_NOT_USABILITY_V1] How long a peer's cell may go unrefreshed
#: before its PRESENCE is in doubt. This is not max_lag and must never be
#: confused with it: max_lag is a numerical policy about whether a peer's
#: state is worth mixing in, measured in generations. This is about whether
#: the peer is there at all, measured in seconds, on the store's clock.
PRESENCE_STALE_S = float(os.environ.get("FROGNET_PRESENCE_STALE_S", "30"))


class PayloadNotWired(RuntimeError):
    """descriptor mode asked for bytes and no data plane is attached."""


def _describe(t) -> Tuple[List[int], str, int]:
    """[THE_DESCRIPTOR_DOES_NOT_NEED_THE_VALUES_V1] shape, dtype, numel.

    publish() called _to_list unconditionally and then, in descriptor mode,
    used only these three facts and threw the list away. Building a Python
    float object per element to learn a number numel() already knows is the
    largest single cost in the step:

        n_elem      to_list ms
          1249            0.02
        100000            1.89
       1000000           25.35
       4000000          126.06

    against a store put of 0.85 ms and a read of 0.68 ms at every one of
    those sizes. It is removed from the descriptor path entirely.
    """
    t = t.detach().to("cpu").contiguous()
    return list(t.shape), str(t.dtype), t.numel()


def _to_list(t) -> Tuple[List[float], List[int], str]:
    """Only for `value` mode, where the tensor really does go in the cell."""
    import torch
    t = t.detach().to("cpu").contiguous()
    return t.flatten().tolist(), list(t.shape), str(t.dtype)


def _from_list(vals, shape, dtype):
    import torch
    dt = getattr(torch, dtype.replace("torch.", ""))
    return torch.tensor(vals, dtype=dt).reshape(shape)


def default_accept(mine: int, theirs: int, age_s, max_lag: int = MAX_LAG):
    """Accept a contribution within `max_lag` generations of mine, either way.

    [STALENESS_IS_A_DISTANCE_NOT_A_DIRECTION_V1]
    This was `(mine - theirs) <= max_lag`, which bounds only how far BEHIND a
    contribution is and places no bound at all on one that has run AHEAD. A
    worker at step 10 accepted a peer's step-300 gradient without complaint.
    Measured: mean lag across four workers came out NEGATIVE -- -6.17 in
    threads and -30.02 with the data plane -- which is the report saying that
    the typical accepted contribution was computed six to thirty steps in the
    future, and nothing rejected it.
    
    What the optimizer tolerates is a gradient computed at parameters some
    DISTANCE from its own. The distance has no sign. A peer thirty steps ahead
    is exactly as wrong to mix in as one thirty steps behind, and on a
    contended host the slow worker is the one that gets hurt -- which is why
    the process arms drifted while the thread arms, serialised by the GIL,
    did not.

    Age is passed but not used by default: the envelope says how long ago a
    cell was written, and generation says how far apart the work is. Those
    differ -- a fast worker that published five seconds ago may be one step
    away while a slow one that published a second ago is ten. The optimizer
    cares about the second.
    """
    if theirs is None:
        return False
    return abs(mine - theirs) <= max_lag


class Contribution:
    """One worker's published cell, as read.

    `plane` is the reader's TensorClient when a data plane is attached. A
    descriptor without one still raises, and says what it would have read.
    [THE_DESCRIPTOR_HAS_A_DATA_PLANE_V1]
    """

    __slots__ = ("worker", "generation", "shape", "dtype", "age_s",
                 "payload_mode", "locator", "values", "n_elem", "retired",
                 "incarnation",
                 "plane", "wire_bytes", "was_same", "_t",
                 # [TWO_READS_NOT_ONE_PRETENDING_V1]
                 "served_generation", "served_digest", "superseded_in_flight")

    def __init__(self, worker, value, age_s, plane=None):
        self.plane = plane
        self.worker = worker
        #: A retired worker published a current state saying it has stopped.
        #: That is not a stale contribution -- it is an absent one, and the
        #: two must not be counted together or "everyone fell behind" and
        #: "half the group left" produce the same report.
        self.retired = bool(value.get("retired"))
        #: [AN_INCARNATION_IS_NOT_A_NAME_V1] Which run of that worker
        #: published this. None for a cell written before this existed.
        self.incarnation = value.get("inc")
        #: [TWO_READS_NOT_ONE_PRETENDING_V1] What the plane actually served,
        #: filled in by tensor(). Until then it is None: the descriptor is
        #: what was advertised, not what was received.
        self.served_generation = None
        self.served_digest = None
        self.superseded_in_flight = False
        self.generation = value.get("g")
        self.shape = value.get("shape")
        self.dtype = value.get("dtype")
        self.n_elem = value.get("n")
        self.payload_mode = value.get("p", "value")
        self.locator = value.get("loc")
        self.values = value.get("v")
        self.age_s = age_s
        self.wire_bytes = None
        self.was_same = None
        self._t = None

    def tensor(self):
        # [A_PEER_THAT_VANISHED_IS_ABSENT_NOT_AN_ERROR_V1] reduce() reads each
        # accepted contribution once, up front, so a transport failure can be
        # dropped rather than raised. Memoised so the later assembly does not
        # read the peer a second time for the same reduction.
        if self._t is not None:
            return self._t
        if self.payload_mode == "value":
            self._t = _from_list(self.values, self.shape, self.dtype)
            return self._t
        loc = self.locator or {}
        if self.plane is not None and loc.get("host") and loc.get("port"):
            # [A_STATE_IS_WHAT_IT_SAYS_IT_IS_V1] Ask for the identity this
            # descriptor advertised, not merely for "whatever that peer has
            # under this name". If the producer has moved on, the read raises
            # StateGone and the caller decides -- PROPAGATE_GONE is the
            # default on purpose. Silently taking the newer state would be
            # the substitution this tag exists to stop, and it would leave
            # the consumer's accounting describing a generation it never
            # used.
            t, nbytes, same = self.plane.read(
                self.worker, loc["host"], int(loc["port"]),
                loc.get("name"), self.generation, self.shape, self.dtype,
                mode=READ_CURRENT)
            # [TWO_READS_NOT_ONE_PRETENDING_V1] reduce() selected the PRODUCER,
            # not a state identity. So it asks for current and books what
            # arrives. The descriptor's generation was a guess that went stale
            # the moment it was read -- publish() writes the plane before the
            # store, so the identity in the space can already be behind. The
            # measured 95% "supersession" at a descriptor age of 0.05 ms was
            # this, not a scheduling queue.
            served = getattr(self.plane, "last_identity", (None, None, None))
            if served[0] is not None:
                self.served_generation = served[0]
                self.served_digest = served[1]
                if served[0] != self.generation:
                    self.superseded_in_flight = True
                    self.generation = served[0]
            self.wire_bytes, self.was_same = nbytes, same
            self._t = t
            return t
        raise PayloadNotWired(
            "contribution from %s is a DESCRIPTOR (%d elems, %s, locator=%r) "
            "and no data plane is attached to read it. The control plane did "
            "its job -- shape, dtype and locator are published. Wire a bulk "
            "link to that locator, or run with "
            "FROGNET_REDUCE_PAYLOAD=value to keep tensors in the cell."
            % (self.worker, self.n_elem or -1, self.dtype, self.locator))


class ReduceGroup:
    """A reduction that nobody has to join.

    There is no world size, no rank ordering and no construction barrier.
    `workers()` answers who is publishing right now.
    """

    def __init__(self, name: str, worker: str,
                 dbhost: Optional[str] = None,
                 accept: Optional[Callable] = None,
                 payload_mode: Optional[str] = None,
                 generation_mode: Optional[str] = None,
                 server=None, client=None, server_host: str = "127.0.0.1",
                 watch=None):
        #: [FIRST_NON_FINITE_V1] Shared with the data plane and the training
        #: loop, so the stages are ordered against each other.
        self.watch = watch if watch is not None else FiniteWatch(worker)
        #: [DEPARTURE_IS_INTENT_NOT_PRESENCE_V1] The last descriptor written,
        #: so retire() can add its advisory flag without erasing it.
        self._last_published: Optional[Dict[str, Any]] = None
        #: [A_DEPARTURE_IS_AN_OBSERVATION_V1] What THIS worker saw, the first
        #: time it saw it, per peer. Nothing here is published: it is one
        #: observer's account, and the analysis wants several accounts that
        #: may disagree, not one consensus written by whoever noticed last.
        #: PROVISIONAL until the run ends: a peer that becomes usable again
        #: is removed, because it was a gap and not a departure.
        self.departures: Dict[str, Dict[str, Any]] = {}
        #: [PRESENCE_IS_NOT_USABILITY_V1] Two different last-somethings, kept
        #: apart because collapsing them is what made a healthy peer look
        #: dead. `_last_observed` is the newest generation seen on a cell
        #: that was THERE -- it advances whatever the acceptance policy
        #: decides. `_last_accepted` is the newest generation this worker was
        #: willing to reduce. A peer drifting outside max_lag stops the
        #: second and not the first.
        #: [AN_INCARNATION_IS_NOT_A_NAME_V1] Which RUN of this worker is
        #: publishing. Captured once here and never recomputed, never derived
        #: from a clock. A worker that dies and restarts keeps its logical
        #: name and its optimizer step counter restarts, so (w3, 42) before
        #: the restart and (w3, 42) after are different states with the same
        #: key. The digest hides that today; it stops hiding it the moment
        #: identity becomes (worker, incarnation, generation).
        #:
        #: host_scope() is host:<ip>:<pid> and is already unique per running
        #: instance -- but a group destroyed and recreated inside ONE process
        #: has the same pid, and that is a new publisher with a fresh
        #: generation counter. So a per-construction token is appended. Not
        #: a timestamp: two constructions inside the same clock tick must
        #: still differ, and clocks move backwards.
        self.incarnation = "%s:%s" % (T.host_scope(), uuid.uuid4().hex[:12])
        self._last_observed: Dict[str, int] = {}
        self._last_accepted: Dict[str, int] = {}
        #: [PROVENANCE_BEFORE_CLEVERNESS_V1] Per producer, the round in which
        #: it was last among those incorporated and what that ROUND did to
        #: this worker's state. Not per-producer consequence: a mean over
        #: several producers cannot be decomposed into their contributions.
        self._incorporation_history: Dict[str, Dict[str, Any]] = {}
        self.name = name
        self.worker = str(worker)
        self.dbhost = dbhost or T.DEFAULT_DBHOST
        self.accept = accept or default_accept
        self.payload_mode = (payload_mode or PAYLOAD_MODE).lower()
        self.generation_mode = (generation_mode or GENERATION_MODE).lower()
        #: [THE_DESCRIPTOR_HAS_A_DATA_PLANE_V1] The data plane, if attached.
        #: server serves this worker's tensors; the reader reads peers'.
        self.server = server
        self.client = client
        self.server_host = server_host
        self._tick = 0
        self._lock = threading.RLock()
        # [PARTICIPATION_IS_PEERS_NOT_A_HEADCOUNT_V1] `accepted` counts this
        # worker's own contribution, because include_self defaults True and a
        # worker's own gradient is never stale to it. Reporting
        # accepted/reduced as participation therefore overstates it by exactly
        # one: 3.20 of a four-worker group is 2.20 of 3 PEERS, not 80% of 4.
        # Every threshold applied to that column was numerically wrong.
        # `accepted_peers` is the quantity anybody actually means.
        # [A_STAT_DECLARES_ITS_OWN_AGGREGATION_V1] The counters and how they
        # combine are declared together in core.stat_schema, so a study
        # cannot be left guessing -- which is how `superseded` was counted
        # correctly and reported as None.
        #
        # [PARTICIPATION_IS_PEERS_NOT_A_HEADCOUNT_V1] `accepted` counts this
        # worker's own contribution; `accepted_peers` is what anybody reading
        # a participation number means.
        self.stats = stat_schema.initial(stat_schema.REDUCE_STATS)
        diag("DIAG-REDUCE", "group open", name=name, worker=self.worker,
             payload_mode=self.payload_mode,
             generation_mode=self.generation_mode,
             data_plane=("port=%d" % server.port) if server else "none",
             inline_max=INLINE_MAX, max_lag=MAX_LAG)

    # -- publishing -------------------------------------------------------

    def generation(self, step: Optional[int] = None) -> int:
        """[REDUCE_BY_READ_V1] Which counter this run is using, per the
        switch. `step` mode requires the caller to supply it -- an optimizer
        step this module invented would not be the optimizer's."""
        if self.generation_mode == "tick":
            return self._tick
        if step is None:
            raise ValueError(
                "FROGNET_REDUCE_GENERATION=step: publish(step=...) is "
                "required. Staleness is measured in optimizer steps and this "
                "module does not own the optimizer. Use "
                "FROGNET_REDUCE_GENERATION=tick for a writer-side counter.")
        return int(step)

    def publish(self, tensor, step: Optional[int] = None) -> Dict[str, Any]:
        """Write this worker's current contribution. Returns what was written.

        Nothing is sent to anybody. The cell is the publication.
        """
        # [THE_DESCRIPTOR_DOES_NOT_NEED_THE_VALUES_V1] Decide the mode from
        # facts that cost nothing, and materialise the values ONLY if they
        # are going into the cell.
        shape, dtype, n = _describe(tensor)
        nbytes = n * tensor.element_size()
        gen = self.generation(step)

        mode = self.payload_mode
        if mode == "auto":
            mode = "descriptor" if nbytes > INLINE_MAX else "value"

        value: Dict[str, Any] = {"g": gen, "shape": shape, "dtype": dtype,
                                 "n": n, "p": mode,
                                 # [AN_INCARNATION_IS_NOT_A_NAME_V1]
                                 "inc": self.incarnation}
        if mode == "value":
            value["v"] = _to_list(tensor)[0]
        else:
            # The control plane's half: everything needed to read it, and where.
            value["loc"] = {"worker": self.worker, "name": self.name,
                            "gen": gen, "bytes": nbytes}
            if self.server is not None:
                # [MATERIALISE_ON_DEMAND_V1] publish() no longer returns a
                # digest, because it no longer makes the bytes. The descriptor
                # advertises identity -- (worker, incarnation, generation) --
                # and where to fetch it. Content equivalence across producers
                # was never consumed and is not advertised.
                self.server.publish(self.name, gen, tensor,
                                    incarnation=self.incarnation)
                value["loc"].update(host=self.server_host,
                                    port=self.server.port,
                                    name=self.server.cell_name(self.name),   # the owner is in the address
                                    inc=self.incarnation)

        with self._lock:
            ok = T.put(SERVICE, self.name,
                       "%s:%s" % (self.name, self.worker), value,
                       dbhost=self.dbhost, own=False)
            # [DEPARTURE_IS_INTENT_NOT_PRESENCE_V1] Kept so retire() can flag
            # this descriptor rather than overwrite it. Recorded only on a
            # write that succeeded -- a descriptor nobody can read is not the
            # last thing this worker published.
            if ok:
                self._last_published = dict(value)
            self._tick += 1
            self.stats["published"] += 1

        # [FIRST_NON_FINITE_V1] What this worker offered, measured on the
        # producer side of every boundary. A tensor that is already not finite
        # here never reached the network, so nothing downstream can be blamed
        # for it.
        self.watch.observe(STAGE_PUBLISH, step=gen, tensor=tensor,
                           name=self.name, mode=mode, bytes=nbytes)

        diag("DIAG-REDUCE", "publish", name=self.name, worker=self.worker,
             generation=gen, mode=mode, n_elem=n, bytes=nbytes, ok=ok)
        if not ok:
            raise RuntimeError("publish failed for %s" % self.name)
        return value

    # -- reading ----------------------------------------------------------

    def contributions(self) -> List[Contribution]:
        """Everything currently published for this reduction, by anyone."""
        out = []
        for r in T.get(SERVICE, self.name, dbhost=self.dbhost):
            scope = r.get("scope", "")
            _, _, w = scope.rpartition(":")
            v = r.get("value") or {}
            if "g" not in v:
                continue
            out.append(Contribution(w, v, r.get("age_s"),
                                    plane=self.client))
        return out

    def workers(self) -> List[str]:
        """Who is publishing. This is world size, observed rather than
        configured -- and it is why a worker leaving needs no announcement.

        [PRESENCE_HAS_ONE_AUTHORITY_V1] A worker is here because its cell is
        here. A worker that flagged departure and whose cell is still present
        is still present -- it said what it intends, not what is true. The
        flag is reported beside the roster, never subtracted from it.
        """
        return sorted(c.worker for c in self.contributions())

    def departing(self) -> List[str]:
        """Who has said they intend to leave. Advisory: these workers are in
        workers() too, because they are still publishing."""
        return sorted(c.worker for c in self.contributions() if c.retired)

    def reduce(self, step: Optional[int] = None, op: str = "mean",
               include_self: bool = True, my_tensor=None):
        """Read the current set, keep what the policy accepts, reduce locally.

        Returns (tensor, report). The report is not decoration: it is which
        workers were used, which were dropped and why, and how far behind
        each accepted one was. A run that converges differently from the
        baseline is explained by that report and not by a second run.
        """
        import torch
        mine = self.generation(step)
        kept, dropped = [], []

        # [ONE_READ_PER_REDUCE_V1] The current set is read ONCE and everything
        # -- acceptance, the counts, the error message -- is derived from that
        # one read. A second call here would be a second store request per
        # reduction, which is the exact quantity plane_separation.py measures
        # as constant at 3. Instrumentation that changes the number it is
        # sitting next to is worse than none.
        all_contribs = self.contributions()
        self._sweep_presence(all_contribs, mine)
        for c in all_contribs:
            if c.worker == self.worker and not include_self:
                continue
            if c.payload_mode != "value":
                self.stats["descriptors_seen"] += 1
            # [PRESENCE_IS_NOT_USABILITY_V1] The cell is here, so the peer is
            # here. Recorded BEFORE the policy runs and regardless of what it
            # decides: this is the observation, not the verdict.
            if c.worker != self.worker and c.generation is not None:
                self._last_observed[c.worker] = max(
                    self._last_observed.get(c.worker, c.generation),
                    c.generation)
            # [PRESENCE_HAS_ONE_AUTHORITY_V1] `retired` used to exclude a
            # contribution outright, which made departure a second liveness
            # mechanism beside the one that actually governs: the cell going
            # stale, or going away when its owner does. Two representations
            # of one fact, and only one of them exists when a worker is
            # killed -- so the killed case, the case that matters, was
            # already relying on presence alone.
            #
            # Departure is now advisory. It is recorded and counted, because
            # "meant to leave" is information that cannot be derived, and it
            # tells a policy whether waiting could ever help. It does not
            # decide whether the state is usable. A departing worker's last
            # published parameters are as good as any peer's at the same
            # generation, and they stop being usable when they go stale,
            # like everyone else's.
            if c.retired:
                self.stats["retired_seen"] += 1
            if not self.accept(mine, c.generation, c.age_s):
                dropped.append((c.worker, c.generation, "stale"))
                self.stats["rejected_stale"] += 1
                # [PRESENCE_IS_NOT_USABILITY_V1] NOT a departure. This peer's
                # cell is in front of us; it is publishing. What happened is
                # that THIS worker's acceptance policy will not use it right
                # now. Recording that as "peer gone at generation N" is a
                # statement about the observer's own threshold wearing the
                # costume of a liveness fact -- and at a tight max_lag it
                # fires constantly on a healthy mesh, which is exactly what
                # it did: one observer reported a peer departed at
                # generation 66 while the peer published on to 199.
                continue
            if c.worker != self.worker and c.generation is not None:
                self._last_accepted[c.worker] = c.generation
            # [A_PEER_THAT_CAME_BACK_NEVER_LEFT_V1] A peer outside the lag
            # window for one reduction has not departed, it is behind. The
            # first version of this recorded a departure on the first stale
            # rejection and reported every worker in a healthy run as gone.
            # A provisional record stands only until the peer is usable
            # again; then it was a gap, and gaps are counted, not reported
            # as departures.
            kept.append(c)

        # [A_PEER_THAT_VANISHED_IS_ABSENT_NOT_AN_ERROR_V1]
        # Read the accepted contributions HERE, so a peer whose data plane has
        # gone is dropped like any other absent contribution instead of
        # killing this worker.
        #
        # Measured: with workers as PROCESSES and the data plane attached, one
        # worker killed outright left the survivors holding permanent links to
        # a socket that no longer existed. The next read got
        # ConnectionResetError, the rebuild could not connect, and the
        # exception propagated out of reduce() -- 1 of 3 survivors finished,
        # against 3 of 3 for every arm whose tensors were in the cells. The
        # control plane had already done its job: the dead worker's cell was
        # simply not being refreshed. The data plane turned an absence into a
        # fault.
        #
        # ONLY transport failures are absorbed. A dtype the descriptor names
        # and this build cannot map, or a payload that does not match its
        # shape, is a real error and still raises -- that is a disagreement
        # about content, not a peer that left.
        ready = []
        for c in kept:
            if c.worker == self.worker and my_tensor is not None:
                # [FIRST_NON_FINITE_V1] Own contribution, taken from memory
                # rather than read back. Measured anyway: if this is not
                # finite the producer is this worker and no peer is implicated.
                self.watch.observe(STAGE_SELF_CONTRIB, step=mine,
                                   tensor=my_tensor, name=self.name,
                                   peer=c.worker, generation=c.generation)
                ready.append(c)
                continue
            try:
                t = c.tensor()
                # [FIRST_NON_FINITE_V1] The transport boundary. This is the
                # peer's tensor as this worker now holds it, before anything
                # is done with it. Non-finite HERE and finite at the peer's
                # publish means the wire or this code, not the training.
                self.watch.observe(STAGE_PEER_READ, step=mine, tensor=t,
                                   name=self.name, peer=c.worker,
                                   generation=c.generation,
                                   lag=(mine - c.generation)
                                   if c.generation is not None else None,
                                   age_s=c.age_s,
                                   payload_mode=c.payload_mode,
                                   wire_bytes=c.wire_bytes,
                                   was_same=c.was_same)
                ready.append(c)
            except PayloadNotWired:
                self._note_departure(c.worker, "unreachable", mine, c)
                raise
            except StateGone as e:
                # [A_STATE_IS_WHAT_IT_SAYS_IT_IS_V1] NOT unreachable. The peer
                # answered, promptly and correctly: the state this consumer
                # selected is not what it holds any more. StateGone subclasses
                # KeyError, so before this branch existed it fell into the
                # handler below and a healthy producer that had simply
                # advanced was reported as a data-plane failure -- the same
                # mislabel as reporting an acceptance decision as a
                # departure. Counted on its own, dropped with its own reason,
                # and no departure recorded.
                dropped.append((c.worker, c.generation, "superseded"))
                self.stats["superseded"] = self.stats.get("superseded", 0) + 1
                diag("DIAG-REDUCE", "selected state superseded",
                     name=self.name, worker=self.worker, peer=c.worker,
                     wanted_generation=c.generation, reason=e.reason,
                     current_generation=e.current_gen)
                continue
            except (ConnectionError, TimeoutError, socket.timeout,
                    OSError, KeyError) as e:
                dropped.append((c.worker, c.generation, "unreachable"))
                self.stats["unreachable"] += 1
                diag("DIAG-REDUCE", "peer data plane unreachable",
                     name=self.name, worker=self.worker, peer=c.worker,
                     generation=c.generation,
                     exc="%s: %s" % (type(e).__name__, e))
        kept = ready

        if my_tensor is not None and include_self and \
                not any(c.worker == self.worker for c in kept):
            # We have not published this step yet, or our own cell was
            # dropped. Our own gradient is never stale to us.
            self.stats["rejected_missing"] += 1
            diag("DIAG-REDUCE", "own contribution absent from the set",
                 name=self.name, worker=self.worker, generation=mine)

        tensors = []
        for c in kept:
            if c.worker == self.worker and my_tensor is not None:
                tensors.append(my_tensor.detach().to("cpu"))
            else:
                # Already read above; this is the cached object for a
                # descriptor and a rebuild of the list for a value.
                tensors.append(c.tensor())
        if my_tensor is not None and include_self and \
                not any(c.worker == self.worker for c in kept):
            tensors.append(my_tensor.detach().to("cpu"))

        if not tensors:
            # [THE_ERROR_MUST_NAME_THE_ACTUAL_REASON_V1] This blamed
            # staleness unconditionally. When every contribution had in fact
            # been dropped because the peer's data plane was gone, it
            # reported "all rejected as stale ... the policy is too tight"
            # and sent the reader to tune max_lag over a network fault.
            # Report what actually happened, counted.
            why = {}
            for d in dropped:
                why[d[2]] = why.get(d[2], 0) + 1
            reasons = ", ".join("%d %s" % (v, k)
                                for k, v in sorted(why.items())) or "none"
            hint = ""
            if why.get("unreachable"):
                hint = (" %d peer(s) were dropped because their DATA PLANE "
                        "could not be read -- that is a transport fault, not "
                        "a staleness policy, and max_lag will not fix it."
                        % why["unreachable"])
            elif why.get("stale"):
                hint = (" Every contribution was outside the max_lag=%d "
                        "window; either the policy is too tight or the peers "
                        "have drifted." % MAX_LAG)

            raise RuntimeError(
                "reduce(%s) has nothing to reduce at generation %s: %d "
                "contribution(s) published, dropped as %s.%s"
                % (self.name, mine, len(all_contribs), reasons, hint))

        stacked = torch.stack(tensors)
        if op == "mean":
            out = stacked.mean(dim=0)
        elif op == "sum":
            out = stacked.sum(dim=0)
        elif op == "max":
            out = stacked.max(dim=0).values
        elif op == "min":
            out = stacked.min(dim=0).values
        else:
            raise ValueError("unknown op %r" % op)

        # [FIRST_NON_FINITE_V1] The reconciliation boundary. Every tensor in
        # `tensors` was measured as it arrived. If they were all finite and
        # this is not, the reduction of stale contributions produced it, and
        # that is a statement about the coordination model rather than about
        # the transport or the optimizer.
        self.watch.observe(STAGE_REDUCE_OUT, step=mine, tensor=out,
                           name=self.name, op=op, n_used=len(tensors),
                           used=[c.worker for c in kept],
                           lags=[mine - c.generation for c in kept
                                 if c.generation is not None],
                           inputs_all_finite=all(
                               t2.isfinite().all().item() for t2 in tensors))

        self.stats["reduced"] += 1
        self.stats["accepted"] += len(kept)
        # [PARTICIPATION_IS_PEERS_NOT_A_HEADCOUNT_V1]
        self.stats["accepted_peers"] += sum(1 for c in kept
                                            if c.worker != self.worker)
        # [PRESENCE_HAS_ONE_AUTHORITY_V1] Every peer whose cell is there is a
        # peer that could have contributed. Subtracting the ones that flagged
        # departure would flatter participation by shrinking the denominator
        # on the strength of an advisory.
        self.stats["peers_possible"] += sum(
            1 for c in all_contribs if c.worker != self.worker)
        # [STALENESS_IS_A_DISTANCE_NOT_A_DIRECTION_V1] Accumulate the
        # ABSOLUTE distance, and count the directions separately. A mean of
        # signed lags cancels a peer six behind against one six ahead and
        # reports zero, which is how an unbounded direction stayed invisible.
        _lags = [mine - c.generation for c in kept
                 if c.generation is not None]
        if _lags:
            self.stats["lag_sum"] += sum(abs(x) for x in _lags)
            self.stats["lag_n"] += len(_lags)
            self.stats["lag_max"] = max(self.stats["lag_max"],
                                        max(abs(x) for x in _lags))
            self.stats["ahead"] += sum(1 for x in _lags if x < 0)
            self.stats["behind"] += sum(1 for x in _lags if x > 0)
        wire = [c.wire_bytes for c in kept if c.wire_bytes is not None]
        sames = [c.was_same for c in kept if c.was_same is not None]
        report = {
            "wire_bytes": sum(wire) if wire else None,
            "same_replies": sum(1 for s in sames if s) if sames else None,
            "generation": mine,
            "op": op,
            "used": [(c.worker, c.generation, c.age_s) for c in kept],
            "dropped": dropped,
            "n_used": len(tensors),
            "lag": [mine - c.generation for c in kept
                    if c.generation is not None],
        }
        diag("DIAG-REDUCE", "reduce", name=self.name, worker=self.worker,
             generation=mine, op=op, n_used=len(tensors),
             used=[u[0] for u in kept and report["used"]],
             dropped=dropped,
             max_lag_used=max(report["lag"]) if report["lag"] else 0)
        return out, report

    def _note_departure(self, worker: str, how: str, mine,
                        c=None) -> None:
        """[A_DEPARTURE_IS_AN_OBSERVATION_V1] Record the FIRST time this
        worker judged a peer to be GONE, and what was true then.

        [PRESENCE_IS_NOT_USABILITY_V1] Gone means its cell stopped being
        there or stopped being refreshed. It does not mean this worker's
        acceptance policy declined to use it -- that is `peer_gaps`, and
        conflating the two produced a departure record for a peer that was
        publishing normally the whole time.

        Both generations are carried, because their difference is the
        finding: "reachable through 199, not accepted after 66" is a
        statement about the threshold, and it is useful. "Departed at 66" is
        the same data reported as a fault.
        """
        if worker == self.worker or worker in self.departures:
            return
        if worker not in self._last_observed:
            return                      # never seen; it did not leave
        self.departures[worker] = {
            "peer": worker,
            "observer": self.worker,
            "group": self.name,
            "how": how,
            "peer_generation": getattr(c, "generation", None),
            "last_observed_generation": self._last_observed.get(worker),
            "last_accepted_generation": self._last_accepted.get(worker),
            "observer_generation": mine,
            "cell_age_s": getattr(c, "age_s", None),
            "presence_stale_s": PRESENCE_STALE_S,
            "reductions_before": self.stats["reduced"],
        }
        diag("DIAG-DEPART", "peer gone", **self.departures[worker])

    def _sweep_presence(self, all_contribs, mine) -> None:
        """[PRESENCE_IS_NOT_USABILITY_V1] Presence, judged on presence.

        Three ways a peer stops being present, none of which is a policy
        decision: its cell is no longer in the space at all, or the cell is
        there and nobody has refreshed it for longer than PRESENCE_STALE_S,
        or its data plane could not be reached (recorded at the read).
        """
        here = {}
        for c in all_contribs:
            if c.worker != self.worker:
                here[c.worker] = c
        for worker in list(self._last_observed):
            c = here.get(worker)
            if c is None:
                self._note_departure(worker, "absent", mine)
                continue
            if c.age_s is not None and c.age_s > PRESENCE_STALE_S:
                self._note_departure(worker, "stale-presence", mine, c)
            elif worker in self.departures:
                # It is back, and present. A departure record only stands if
                # it was never resolved.
                gap = self.departures.pop(worker)
                self.stats["peer_gaps"] = self.stats.get("peer_gaps", 0) + 1
                diag("DIAG-DEPART", "peer present again", peer=worker,
                     observer=self.worker, was=gap["how"])

    def departure_report(self) -> List[Dict[str, Any]]:
        """Peers that became unusable and never came back. Read at the end of
        a run: a record still standing here was never resolved."""
        return [dict(v) for v in self.departures.values()]

    def retire(self) -> None:
        """Stop publishing, and say so without destroying what was published.

        [DEPARTURE_IS_INTENT_NOT_PRESENCE_V1]
        This wrote {"g": None, "retired": True} over the cell, which did two
        things it should not.

        It destroyed state. The worker's last published descriptor -- a real
        generation, a real tensor still sitting on its data plane -- was
        replaced by an empty one. A peer that wanted that state lost it at the
        moment the producer announced it was leaving, which is the one moment
        the state is guaranteed not to change again.

        And it made departure a second authority on presence. A cell with
        g=None is rejected by default_accept anyway, because `theirs is None`
        returns False, so a retired worker was excluded twice by two
        unrelated mechanisms -- and neither of them is the one that actually
        governs, which is the envelope going stale. The kill arm proves the
        envelope suffices: a killed worker never reaches this method and the
        survivors carry on regardless.

        What is left is the part that is genuinely information and cannot be
        derived: this worker MEANT to leave, as opposed to having fallen
        behind or died. That distinction is advisory -- it tells a policy
        whether waiting could ever help -- and it now rides alongside the last
        descriptor instead of replacing it.
        """
        value = dict(self._last_published or {"g": None})
        value["retired"] = True
        T.put(SERVICE, self.name, "%s:%s" % (self.name, self.worker),
              value, dbhost=self.dbhost, own=False)
        diag("DIAG-REDUCE", "retire", name=self.name, worker=self.worker,
             stats=dict(self.stats))
