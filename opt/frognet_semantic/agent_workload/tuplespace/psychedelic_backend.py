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

from . import frogtorch_ram_client as T  # [ONE_RAM_V1] the control cells: the one RAM on databasehost.frognet; the September code, otherwise unchanged

from . import torch_backend as _base
from .tensor_plane import (READ_CURRENT, StreamSubscription, TensorClient,
                           TensorServer, plane_view, tcp_info, writable_bytes)


# ---------------------------------------------------------------------------
# [DIAG_PSY_STALL_V1 - John 2026-09-14] Say who is waiting on whom, without
# anyone having to catch it live with py-spy.
#
# A stalled collective looks identical from one rank whether the peer is slow,
# the peer is waiting on THIS rank, or the read pool is queued -- and sampling
# four nodes by hand to tell those apart is not a diagnostic procedure. So each
# blocking wait registers what it is waiting for, and a watchdog thread prints
# the whole picture once the wait passes a threshold and every interval after.
#
# Everything in scope is printed, not just what a hypothesis says matters:
# rank, seq, phase, peer, cell name, generation, bytes, elapsed, the executor's
# queue depth and busy count, and every other outstanding wait on this rank. A
# second instrumented run costs an hour and may not reproduce; a noisy line
# costs nothing.
#
# stdout, unbuffered, tagged [DIAG-PSY] -- the harness already captures rank
# logs and journalctl already carries the proxy's own tags.
# ---------------------------------------------------------------------------
_PSY_DIAG = os.environ.get("FROGNET_PSY_DIAG", "1") not in ("0", "no")
_PSY_STALL_S = float(os.environ.get("FROGNET_PSY_STALL_S", "20"))
_PSY_STALL_EVERY_S = float(os.environ.get("FROGNET_PSY_STALL_EVERY_S", "20"))

_psy_waits: Dict[int, Dict[str, Any]] = {}
_psy_waits_lock = threading.Lock()
_psy_wait_seq = [0]
_psy_watchdog_started = [False]


def _psy(_event: str, **kw) -> None:
    # [NO_COLLIDING_PARAM_NAMES_V1 - John 2026-09-14] The positional was named
    # `msg`, and WAIT_END passes msg= as a FIELD -- "_psy() got multiple values
    # for argument 'msg'", which killed ddp5001 at the first exception it tried
    # to report. A diagnostic that crashes the run it is diagnosing is worse
    # than no diagnostic. Underscore-prefixed so no field name can reach it.
    if not _PSY_DIAG:
        return
    parts = " ".join("%s=%s" % (k, v) for k, v in kw.items())
    print("[DIAG-PSY] %s %s" % (_event, parts), flush=True)


class _PsyWait:
    """Register a blocking wait so the watchdog can name it."""

    def __init__(self, **fields):
        self.fields = fields
        self.id = None

    def __enter__(self):
        with _psy_waits_lock:
            _psy_wait_seq[0] += 1
            self.id = _psy_wait_seq[0]
            self.fields["t0"] = time.time()
            self.fields["thread"] = threading.current_thread().name
            _psy_waits[self.id] = self.fields
        _psy_start_watchdog()
        return self

    def __exit__(self, *exc):
        with _psy_waits_lock:
            f = _psy_waits.pop(self.id, None)
        if f is not None:
            el = time.time() - f["t0"]
            if el >= _PSY_STALL_S or exc[0] is not None:
                _psy("WAIT_END", elapsed_s="%.1f" % el,
                     exc=(exc[0].__name__ if exc[0] else "-"),
                     msg=(repr(str(exc[1]))[:400] if exc[1] else "-"),
                     **{k: v for k, v in f.items()
                        if k not in ("t0", "_be")})
        return False


class _PeerTiming:
    """[PER_PEER_TIMING_V1] The proxy's _PeerLatency shape
    (proxy/transport_semantic.py): an EWMA of what a peer's cells actually
    take, fed only by successful landings, clamped so one pathological cell
    cannot teach it to wait forever. Here it sets when a wait is reported as a
    stall; nothing in this path retries, so it never changes what is waited
    for -- only how soon a slow peer is named."""

    def __init__(self, alpha: float = 0.2, floor_s: float = 0.05,
                 ceiling_s: float = 60.0, mult: float = 4.0, min_stall_s: float = 2.0):
        self.ewma = None
        self.alpha, self.floor, self.ceiling = alpha, floor_s, ceiling_s
        self.mult, self.min_stall = mult, min_stall_s
        self.n = 0

    def observe(self, s: float) -> None:
        s = max(self.floor, min(self.ceiling, s))
        self.ewma = s if self.ewma is None else (1 - self.alpha) * self.ewma + self.alpha * s
        self.n += 1

    def stall_s(self) -> float:
        if self.ewma is None:
            return _PSY_STALL_S
        return max(self.min_stall, min(_PSY_STALL_S, self.mult * self.ewma))


def _psy_pool_state(be) -> str:
    """Executor depth and busy count. A queued read and a slow peer look the
    same from the outside and are not the same problem."""
    try:
        ex = getattr(be, "_exec", None)
        parts = []
        for nm, pool in (("read", ex), ("round", getattr(be, "_coll", None))):
            if pool is not None and hasattr(pool, "stats"):
                st = pool.stats()
                parts.append("%s_pool=%d/%didle queued=%d peak=%d" % (
                    nm, st["threads"], st["idle"], st["queued"], st["peak"]))
        return " ".join(parts) or "pool=none"
    except Exception as e:
        return "pool=unreadable(%s)" % type(e).__name__


def _psy_start_watchdog() -> None:
    if _psy_watchdog_started[0] or not _PSY_DIAG:
        return
    _psy_watchdog_started[0] = True

    def _loop():
        reported: Dict[int, float] = {}
        while True:
            time.sleep(1.0)
            now = time.time()
            with _psy_waits_lock:
                snap = {k: dict(v) for k, v in _psy_waits.items()}
            for wid, f in snap.items():
                el = now - f["t0"]
                # [PER_PEER_TIMING_V1] A wait is a stall when it is long for
                # THAT peer -- several times what its cells normally take --
                # not when it passes one number for every link.
                if el < float(f.get("stall_s") or _PSY_STALL_S):
                    continue
                last = reported.get(wid, 0.0)
                if now - last < _PSY_STALL_EVERY_S:
                    continue
                reported[wid] = now
                be = f.get("_be")
                others = sorted(
                    ("%s:%s/%s@%.0fs" % (o.get("phase", "?"),
                                         o.get("peer", "?"),
                                         o.get("seq", "?"),
                                         now - o["t0"])
                     for k, o in snap.items() if k != wid))
                _psy("STALL", waiting_s="%.1f" % el,
                     **{k: v for k, v in f.items()
                        if k not in ("t0", "_be")})
                _psy("STALL_CONTEXT", waiting_s="%.1f" % el,
                     pool=(_psy_pool_state(be) if be is not None else "n/a"),
                     outstanding=len(snap),
                     others=("|".join(others) if others else "none"))
            for wid in list(reported):
                if wid not in snap:
                    reported.pop(wid, None)

    threading.Thread(target=_loop, name="psy-watchdog", daemon=True).start()

SERVICE = os.environ.get("FROGNET_PSY_SERVICE", "psyc10d")


def _scope(rank: int) -> str:
    return "r%d" % rank


def op_of(name: str) -> str:
    """`<incarnation>.<op>.<seq>` -> `<op>`. The content a peer holds is
    per-operation, not per-round."""
    parts = name.split(".")
    return parts[-2] if len(parts) >= 2 else name


#: [THE_PLANE_OUTLIVES_THE_GROUP_V1]
#:
#: One plane per PROCESS, not one per process group.
#:
#: Each backend used to build its own TensorServer, so the listening socket
#: died when the backend did -- which is when the process group is
#: destroyed. A reader still fetching a state at that moment gets
#: ConnectionRefused, and the rank serving it never learns why.
#:
#: That is not an edge case, it is the ordinary shape of the operations that
#: do not end in a rendezvous. `gather`'s non-roots publish and return
#: without waiting for the root, exactly as designed; `scatter`'s root
#: publishes and returns without waiting for its readers. Both are correct
#: about the model and both leave bytes that must remain reachable after the
#: publisher has moved on. PyTorch's own suite catches it: the *_checks
#: tests do two collectives back to back and hang.
#:
#: Residence belongs to the plane, not to the operation that filled it. A
#: worker leaving means "this worker stopped publishing", not "everything it
#: published vanished". Making the plane a process-level resource is the
#: smallest version of that: state stays reachable while the process is
#: alive, and names are incarnation-scoped so two groups cannot collide.
#:
#: It does NOT fix the general case -- a departed PROCESS still takes its
#: bytes -- and it deliberately adds no acknowledgement, so nobody waits for
#: anybody.
_PLANES = {}
_PLANES_LOCK = threading.Lock()


def _shared_plane(host: str) -> TensorServer:
    with _PLANES_LOCK:
        srv = _PLANES.get(host)
        if srv is None:
            srv = _PLANES[host] = TensorServer(host)
        return srv


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
        self._server = _shared_plane(host)
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
        #: [TIME_EACH_PEER_SEPARATELY_V1]
        #: A collective's cost is one number; where it comes from is per
        #: peer. On a fleet with one node across a continent those are not
        #: the same distribution, and an aggregate only says the fleet is
        #: slow. Per-peer says which link costs what -- and because these
        #: reads are issued concurrently, whether the far one is overlapped
        #: or serialised. List append is atomic in CPython, so the pool
        #: threads need no lock here.
        #: In-flight speculative phase-1 reads: (seq, dtype) -> (futs, buf)
        self._ahead = {}
        #: [THE_LENGTH_IS_A_CONSEQUENCE_NOT_A_GUESS_V1 - John 2026-09-14] The
        #: largest phase-1 slice length this rank has seen, per dtype. Both
        #: landing areas are sized from it and neither is sized from the round
        #: they are about to serve, so a bucket bigger than its predecessor
        #: costs one reallocation for the life of the job instead of a wrong
        #: answer every time the bucket size alternates.
        self._ln_hwm = {}
        self._peer_ms = {}
        self._phase_ms = {"publish": [], "phase1": [], "phase2": []}
        self._pf_epoch = 0
        self._pf_live = 0
        self._stop = threading.Event()
        self._coll_lock = threading.RLock()
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
               "inc": self._incarnation, "build": BUILD.split(" (")[0],
               "node": __import__("socket").gethostname()},
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
                # [ONE_BUILD_PER_GROUP_V1] Every rank says what it runs, and
                # a group whose ranks run different code does not start: it
                # says which rank, at which address, runs what. A rank too
                # old to say has no "build" and is named as such.
                mine = BUILD.split(" (")[0]
                seen = {r: (d.get("build") or "unknown (a build before 20261112)")
                        for r, d in sorted(found.items())}
                print("[DIAG-PSY] RANKS " + " ".join(
                    "r%d=%s:%s/%s" % (r, d.get("host"), d.get("port"), seen[r])
                    for r, d in sorted(found.items())), flush=True)
                odd = {r: b for r, b in seen.items() if b != mine}
                if odd:
                    raise RuntimeError(
                        "psychedelic: rank %d runs %s, and %s. Install the same "
                        "build on every node."
                        % (self._rank, mine, "; ".join(
                            "rank %d at %s runs %s" % (r, found[r].get("host"), b)
                            for r, b in sorted(odd.items()))))
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
            from .tensor_plane import AdaptivePool
            self._exec = AdaptivePool("psy-read")
            # [ADAPTIVE_POOL_V1] Was a fixed max(4, 2W) ThreadPoolExecutor: now a
            # read gets a worker when it needs one, and an idle worker retires.
            # [EXIT_ENDS_THE_WAIT_V1] concurrent.futures joins its workers at
            # interpreter exit. A worker blocked in a read-ahead for a round
            # that will never be published held ddp5744 there for up to
            # 3 x PEER_WAIT. threading's exit hooks run newest-first, so this
            # one -- registered after the executor's -- runs before the join
            # and ends the reads it would otherwise wait on.
            try:
                threading._register_atexit(lambda be=self: be._abort_io("exit"))
            except Exception as e:
                _psy("EXIT_HOOK_FAILED", err=repr(e))
        return self._exec

    def _abort_io(self, why: str) -> None:
        """End all outstanding reads and speculative work: on shutdown and
        at interpreter exit."""
        self._stop.set()
        n = 0
        for c in list(self._clients.values()):
            n += c.abort()
        for s_ in list(getattr(self, "_streams", {}).values()):
            for sk in getattr(s_, "socks", [s_.sock]):
                try:
                    sk.shutdown(__import__("socket").SHUT_RDWR)
                except OSError:
                    pass
        ex = self._exec
        if ex is not None:
            ex.shutdown(wait=False, cancel_futures=True)
        print("[DIAG-PSY] ABORT_IO why=%s rank=%d links_shut=%d" % (why, self._rank, n),
              flush=True)

    def shutdown(self) -> None:
        """destroy_process_group calls this."""
        self._abort_io("shutdown")

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
    #: Default: shard. Measured, repeatedly, at both world sizes:
    #:
    #:   world 4   shard 2573 ns/el (4 runs)   stack 4299 (3 runs)
    #:   world 3   shard  476            stack 2132
    #:
    #: stack moves (W-1)n per rank where shard moves 2(W-1)n/W -- ring's
    #: count -- so stack's disadvantage is W/2 and grows with the fleet.
    #: It was the default because it came first, not because it won
    #: anything, and it has lost every arm it has ever been measured in.
    #:
    #: stack remains available and remains correct. It is the simpler
    #: formulation and the one that shows the model plainly, which is worth
    #: keeping for exactly that.
    REDUCE = os.environ.get("FROGNET_PSY_REDUCE", "shard")

    #: [ASK_BEFORE_YOU_NEED_IT_V1] Read-ahead on the sharded path.
    #:
    #: A peer read on a 97 ms link at 0.7 MB/s is mostly waiting. This path
    #: issued round N's reads during round N and blocked on them, so every
    #: round paid the full latency again.
    #:
    #: Everything about round N+1 is known before it starts -- same peers,
    #: same slice index, seq+1 -- and the plane's read already BLOCKS until
    #: the state exists. So the request can be in flight a whole round
    #: early, parked on the peer's server, and the answer arrives the
    #: instant the peer publishes.
    #:
    #: A speculative read that is never used costs a held socket and a
    #: parked thread, both of which the link already has. A wrong guess
    #: costs the round it would have waited anyway.
    # [THE_LENGTH_IS_A_CONSEQUENCE_NOT_A_GUESS_V1 - John 2026-09-14] Read-ahead
    # does not guess a length at all. It cannot know the next round's slice
    # length -- DDP buckets are not uniform and the backend does not see the
    # bucket order -- and it no longer has to: the read states a name and a
    # generation, the frame states its own size, and the landing area is the
    # largest slice seen so far. What was left of GUESS_WIDE_THEN_CORRECT_V1 --
    # a 4x multiplier and a retry on ValueError -- was correcting for a
    # question nothing needed to ask.
    # On by default (John, 2026-09-14: "Read-ahead stays on"; measured on the five-machine fleet 2026-11-12: ddp5744
    # with it 11.7 s first step / 181 s to warmup / 79 s at step 5 / 12773 ms best, against ddp5340 without it
    # 44.1 / 256 / 147 / 17919, and the gloo control 28.3 / 162 / 81 / 14493). FROGNET_PSY_READAHEAD=0 turns it off.
    READAHEAD = os.environ.get("FROGNET_PSY_READAHEAD", "1") not in ("0", "no")

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

    def _drop_ahead(self) -> None:
        """Abandon speculative reads.

        Their futures are left to finish on their own: cancelling a read
        mid-frame would desynchronise the link, and a completed read nobody
        wants costs only bytes already asked for.

        [A_WRONG_GUESS_MUST_COST_NOTHING_V1 - John 2026-09-14] But their
        EXCEPTIONS have to be collected, or the interpreter reports them when
        the future is garbage collected and -- worse -- the retry machinery
        inside the read spends the full budget on a question that is already
        known to be wrong.
        """
        for futs, _buf in self._ahead.values():
            for f in futs.values():
                f.cancel()
                if f.done():
                    # A guess that failed is not an error. Consume the
                    # exception so it is neither raised nor reported later.
                    f.exception()
        self._ahead.clear()

    def _read_ahead(self, seq, like, peers, r, wait, pool) -> None:
        """Issue the NEXT round's phase-1 reads now.

        Speculative reads need their own landing area -- the current round
        is still reading into `_stage` -- so this has a second buffer. Only
        one round is ever guessed ahead, so two are enough.

        [THE_LENGTH_IS_A_CONSEQUENCE_NOT_A_GUESS_V1 - John 2026-09-14]

        There is nothing left in here to guess. The cell name is
        `<peer-inc>.ars.<seq>.<r>`: peer, round and this rank's slice index,
        all of them known a round early. What the previous version ALSO had
        to state was how many elements that cell would hold, because the read
        asserted `dst.numel()` against the frame -- and it stated it from the
        round it was standing in. DDP hands out buckets of 262657 and 791552
        elements alternately, so it was wrong on every round, the guess missed
        every time (prefetch_hit=False in every log of every ddp54xx arm) and
        read-ahead bought nothing at all while still holding a socket and a
        thread per peer.

        So the length comes out of both places it was stated. The dict is
        keyed on the round and the dtype -- peer is inside `futs`, and the
        slice index cannot change within a run -- and the read is issued
        `exact=False` into a row sized from the high-water mark, which is
        large enough for any bucket seen so far. The FIRST round at a new
        maximum still fails, once, and is discarded exactly as a wrong guess
        always was; every round after it hits.
        """
        key = (seq, str(like.dtype))
        if key in self._ahead:
            return
        self._drop_ahead()
        buf = self._ahead_stage(len(peers), like)
        futs = {}
        for i, p in enumerate(peers):
            futs[p] = pool.submit(
                self._read_slice_into, buf[i], p,
                # [READ_AHEAD_IS_A_THIRD_CONVERSATION_V1 - John 2026-09-14]
                # "pf", not "p1". A link is keyed host:port#channel and carries
                # one request/reply at a time under its lock -- which is why
                # phase 1 and phase 2 have separate channels. Issuing the
                # speculative round on "p1" made it a THIRD conversation on the
                # live phase-1 socket: it holds that link for its full wait
                # asking a peer for state it has not produced, so the live read
                # queues behind it (observed: 65-73 s per peer on ddp2201), and
                # when two frames interleave the next length header is read out
                # of the middle of the other's payload -- the garbage length is
                # what failed the receive buffer size check with ValueError.
                "%s.ars.%d.%d" % (self._peer_inc(p), seq, r), seq, wait, "pf",
                False)
        self._ahead[key] = (futs, buf)

    def _hwm(self, ln: int, like) -> int:
        """[THE_LENGTH_IS_A_CONSEQUENCE_NOT_A_GUESS_V1 - John 2026-09-14] The
        largest phase-1 slice length seen for this dtype.

        This replaces AHEAD_HEADROOM, which multiplied the previous round's
        length by four and hoped. A multiplier is an arbitrary number that is
        either wasteful or wrong; the largest length actually observed is
        neither, costs one reallocation the first time a bigger bucket
        appears, and is then right for the rest of the run.
        """
        k = str(like.dtype)
        cur = self._ln_hwm.get(k, 0)
        if ln > cur:
            self._ln_hwm[k] = cur = ln
        return cur

    def _ahead_stage(self, rows: int, like):
        """Landing area for speculative reads, separate from _stage.

        Sized to the high-water mark, so the speculative read never has to
        state a length. Reallocated only when a bigger bucket appears.
        """
        wide = max(1, self._hwm(0, like))
        k = (rows, wide, str(like.dtype))
        buf = getattr(self, "_ahead_buf", None)
        if buf is None or getattr(self, "_ahead_key", None) != k:
            buf = torch.empty(rows * wide, dtype=like.dtype)
            self._ahead_buf, self._ahead_key = buf, k
        return [buf[i * wide:(i + 1) * wide] for i in range(rows)]

    def _stage(self, rows: int, ln: int, like):
        """[ALLOCATE_ONCE_NOT_ONCE_PER_FRAME_V1] Landing area for the peer
        slices of one phase, reused across rounds.

        The reads run concurrently, so each needs its own destination -- one
        row per peer. Held on the backend and reallocated only when the shape
        or dtype changes, which for a fixed bucket size is never. Not
        cleared: read_into overwrites every element before anything reads it,
        so clearing would add a pass to no purpose.

        [THE_LENGTH_IS_A_CONSEQUENCE_NOT_A_GUESS_V1 - John 2026-09-14] "which
        for a fixed bucket size is never" was the whole assumption, and DDP's
        buckets are not fixed: alternating 262657 / 791552 elements
        reallocated this buffer on EVERY round, three megabytes at a time, in
        the middle of the phase it is meant to make free. Sized to the
        high-water mark it is allocated twice for the life of the job. The
        rows handed back are still exactly `ln` long, so nothing downstream
        sees the slack.
        """
        wide = self._hwm(ln, like)
        key = (rows, wide, str(like.dtype))
        buf = getattr(self, "_stage_buf", None)
        if buf is None or getattr(self, "_stage_key", None) != key:
            buf = torch.empty(rows * wide, dtype=like.dtype)
            self._stage_buf, self._stage_key = buf, key
        return [buf[i * wide:i * wide + ln] for i in range(rows)]

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


    #: [READ_WHERE_THE_ANSWER_GOES_V1] on the returning read path.
    READ_INTO = os.environ.get("FROGNET_PSY_READ_INTO", "1") not in ("0", "no")

    def _recv_slot(self, r: int, like, channel: str):
        """A destination for _read_peer, owned here and reused per channel.

        _read_peer returns a tensor, so something has to own it. Allocating
        one per read is a fresh buffer every time; one per (shape, channel)
        is reused for the life of the job.

        Per CHANNEL, because two channels to one peer are two conversations
        in flight at once -- the same reason the plane's receive buffers are
        per link and not per client, which took a digest failure on a live
        four-node run to find.
        """
        # Keyed by PEER as well as shape and channel. _combine collects one
        # tensor per peer into a list and reduces across them: if two peers
        # shared a slot, parts[0] and parts[1] would be the same memory and
        # the reduction would fold a value into itself. Same aliasing that
        # made two channels share a receive buffer and failed a digest on a
        # live four-node run.
        k = (r, tuple(like.shape), str(like.dtype), channel)
        slots = getattr(self, "_recv_slots", None)
        if slots is None:
            slots = self._recv_slots = {}
        t = slots.get(k)
        if t is None:
            t = slots[k] = torch.empty(like.shape, dtype=like.dtype)
        return t

    def _read_slice_into(self, dst, r: int, name: str, gen: int,
                         wait_s: float, channel: str = "d",
                         exact: bool = True) -> int:
        """[ALLOCATE_ONCE_NOT_ONCE_PER_FRAME_V1] The same read, writing where
        the answer goes.

        _read_slice allocates a receive buffer per call, builds a tensor over
        it, and the caller copies that into place -- an allocation and two
        passes over the payload. read_into reuses one buffer per client and
        copies once, into `dst`.
        """
        d = self._rendezvous()[r]
        _t0 = time.perf_counter()
        try:
            # [DIAG_PSY_STALL_V1] One registered wait per slice read. A read
            # that never starts and a read that starts and hangs are different
            # failures; without this they are the same silence.
            with _PsyWait(phase=channel, rank=self._rank, seq=gen, peer=r,
                          cell=name, gen=gen, host=d["host"], port=d["port"],
                          bytes=(dst.numel() * dst.element_size()),
                          wait_s=wait_s, _be=self):
                _cli = self._peer_client(r)
                # [THE_LENGTH_IS_A_CONSEQUENCE_NOT_A_GUESS_V1 - John
                # 2026-09-14] A ValueError here used to mean "the receive
                # buffer was sized from a speculative guess", and this caught
                # it, dropped the link's scratch buffer and asked once more.
                # Nothing guesses a length any more -- the frame states its
                # own size and the provider is grown from the frame -- so that
                # retry was correcting for a question that is no longer asked.
                # It comes out rather than staying as insurance: a second ask
                # that cannot be distinguished from the first is how 65-84 s
                # per collective went missing in the first place.
                return _cli.read_into(
                    dst, "r%d" % r, d["host"], int(d["port"]), name, gen,
                    mode=READ_CURRENT, wait_s=wait_s, verify=self.VERIFY,
                    channel=channel, exact=exact)[2]
        except Exception as e:
            # [SAY_WHO_REFUSED_V1] Whatever went wrong, say which rank, at
            # which address, for which state. Four machines failing produce
            # four tracebacks and they are only useful if they name the same
            # node.
            #
            # Add a NOTE; do not rebuild the exception. `type(e)(msg)`
            # assumes every exception takes one string, and StateGone takes
            # (msg, reason, ...) -- so re-raising it that way replaced the
            # real failure with "TypeError: missing 1 required positional
            # argument" on three of four ranks, and destroyed the only
            # evidence of what actually went wrong.
            note = ("rank %d reading %s from rank %d at %s:%s"
                    % (self._rank, name, r, d.get("host"), d.get("port")))
            if hasattr(e, "add_note"):
                e.add_note(note)
            raise
        finally:
            self._peer_ms.setdefault(r, []).append(
                (time.perf_counter() - _t0) * 1000.0)

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
        if self.STREAM:
            return self._allreduce_streamed(t, seq, acc_fn)
        _t_pub = time.perf_counter()
        W, r = self._size, self._rank
        flat = t.reshape(-1)
        parts = self._slices(flat.numel())
        inc = self._peer_inc(r)
        wait = self._peer_wait()
        pool = self._pool()

        # Phase 1. Publish this rank's contribution as W named slices, so a
        # peer can take the one it owns and nothing else.
        # [POINT_AT_THE_BYTES_DO_NOT_COPY_THEM_V1]
        #
        # Published without copying. Serialising every slice was a full
        # pass over the tensor before the first byte was even asked for,
        # and the copy exists to freeze a state against changing under its
        # own name. Here nothing can:
        #
        #   range k is read by exactly one peer, peer k
        #   range k is overwritten by exactly one thing, peer k's reduced
        #     slice arriving in phase 2
        #   peer k cannot produce that slice without first reading this one
        #
        # so the arrival that overwrites the range is the receipt that the
        # range was taken. The algorithm's own dependency does the work an
        # acknowledgement would, and no copy or handshake is needed.
        #
        # Slice r is skipped: this rank reads its own range from peers and
        # never serves it. Publishing it was W slices of work to serve W-1.
        for k, (st, ln) in enumerate(parts):
            if ln and k != r:
                self._server.publish("%s.ars.%d.%d" % (inc, seq, k), seq,
                                     flat[st:st + ln], incarnation=str(r),
                                     copy=False)
        self._phase_ms["publish"].append((time.perf_counter()-_t_pub)*1000.0)
        _t_p1 = time.perf_counter()
        st_r, ln_r = parts[r]
        own = flat[st_r:st_r + ln_r]
        peers = [q for q in range(W) if q != r]

        # [ASK_FOR_IT_BEFORE_YOU_NEED_IT_V1]
        #
        # Both phases read from every peer, so a slow link is paid TWICE per
        # collective. Measured at world 4 with a transcontinental member:
        # every rank's median read of that peer was ~2.1 s, phase 1 median
        # 2293 ms, phase 2 median 2685 ms. Each phase is essentially one
        # read of the slow link, and shard came out 1.53x slower than gloo.
        #
        # The second read does not have to WAIT for the first. A phase-2
        # read asks for a peer's REDUCED slice, which depends on that peer
        # finishing its own phase 1 -- not on anything this rank does. So
        # issue it now, right after publishing, and leave it blocked at the
        # peer's plane on wait_s. It returns the moment that peer publishes,
        # and its round trip overlaps this rank's own reduce instead of
        # following it.
        #
        # Nothing about the model changes: a reader asks for a state and the
        # plane holds the question open until it exists. What changes is
        # when the asking happens.
        #
        # The destinations are disjoint -- phase 2 writes the slices this
        # rank does NOT own, phase 1 accumulates into the one it does -- so
        # the two sets of reads cannot touch the same elements.
        p2 = {}
        for p in peers:
            st_p, ln_p = parts[p]
            if ln_p:
                p2[p] = pool.submit(
                    self._read_slice_into, flat[st_p:st_p + ln_p], p,
                    "%s.arr.%d" % (self._peer_inc(p), seq), seq, wait, "p2")

        if ln_r:
            # Peer slices land in a staging buffer allocated once, not one
            # tensor per peer per round. They cannot be accumulated straight
            # into `own` because the reads run concurrently and add_ is not
            # a place two threads may meet.
            # [ASK_BEFORE_YOU_NEED_IT_V1] Take whatever the previous round
            # already asked for. A hit is a read that has been sitting on
            # the peer's server since before this round began, so its
            # latency was paid during the last round's arithmetic.
            # [THE_LENGTH_IS_A_CONSEQUENCE_NOT_A_GUESS_V1 - John 2026-09-14]
            # The guess is keyed on the round and the dtype. It used to carry
            # `ln_r` as well, which made every bucket-size change a miss --
            # and DDP alternates two bucket sizes, so it was EVERY round.
            self._hwm(ln_r, own)
            key = (seq, str(own.dtype))
            pre = self._ahead.pop(key, None)
            hit, futs = False, None
            if pre is not None:
                futs, wide = pre
                # A speculative read that could not be served is a wrong guess
                # and costs the round it would have had anyway -- but it is
                # not silent. The only way to be here is a bucket larger than
                # any seen when the guess was issued, which happens once per
                # distinct bucket size and never again.
                got = {}
                for p in peers:
                    try:
                        got[p] = futs[p].result()
                    except Exception as e:
                        _psy("AHEAD_REFUSED", rank=self._rank, peer=p, seq=seq,
                             ln=ln_r, err=repr(str(e))[:200])
                        got = None
                        break
                if got is not None:
                    # Every rank derives its slices from the same bucket
                    # numel, and the cell name pins peer, round and slice
                    # index -- so a different element count is not a wrong
                    # guess, it is two ranks holding different buckets under
                    # one name. That is a fault and is raised, not retried
                    # into a normal-looking round.
                    bad = {p: v for p, v in got.items() if v != ln_r}
                    if bad:
                        raise RuntimeError(
                            "rank %d round %d: own slice is %d elements but "
                            "%s answered with %s for the same round and slice "
                            "index. The ranks do not agree on the bucket."
                            % (self._rank, seq, ln_r,
                               ", ".join("r%d" % p for p in sorted(bad)),
                               sorted(bad.values())))
                    hit = True
                    stage = [wide[i][:ln_r] for i in range(len(peers))]
                    futs = None
            if not hit:
                if futs:
                    # [A_WRONG_GUESS_MUST_COST_NOTHING_V1] Collect the
                    # abandoned guess's exceptions here. Breaking out of the
                    # loop above leaves the remaining futures unread, and an
                    # unretrieved exception is reported by the interpreter at
                    # collection time -- from a thread, with no round attached.
                    for f in (futs or {}).values():
                        f.cancel()
                        if f.done():
                            f.exception()
                self._drop_ahead()      # a miss voids the rest of the guess
                stage = self._stage(W - 1, ln_r, own)
                futs = {}
                for i, p in enumerate(peers):
                    futs[p] = pool.submit(
                        self._read_slice_into, stage[i], p,
                        "%s.ars.%d.%d" % (self._peer_inc(p), seq, r), seq,
                        wait, "p1")
            for i, p in enumerate(peers):
                # [DIAG_PSY_STALL_V1] This is where the round blocks. Register
                # the wait so the watchdog can say which peer, which cell and
                # which generation -- and whether the read is even running yet
                # or still queued behind the speculative round.
                with _PsyWait(phase="p1", rank=self._rank, seq=seq, peer=p,
                              cell="%s.ars.%d.%d" % (self._peer_inc(p), seq, r),
                              gen=seq, ln=ln_r, wait_s=wait,
                              prefetch_hit=hit, _be=self):
                    if futs is not None:
                        futs[p].result()
                acc_fn(own, stage[i])
            # Ask for the next round now, while this one still has work.
            if self.READAHEAD:
                self._read_ahead(seq + 1, own, peers, r, wait, pool)

        self._phase_ms["phase1"].append((time.perf_counter()-_t_p1)*1000.0)
        _t_p2 = time.perf_counter()
        # Publishing this rank's reduced slice releases every peer that is
        # already blocked reading it.
        #
        # [WRITE_WHERE_THE_ANSWER_GOES_V1] `flat` is a view of `t`, so the
        # phase-2 reads above wrote the answer directly. Safe only in this
        # order: every byte a peer can still read was published as a CLONE,
        # so mutating `t` cannot change what anyone else sees.
        # The reduced slice is what every peer is waiting on, so this one
        # is published immediately rather than queued behind anything.
        self._server.publish("%s.arr.%d" % (inc, seq), seq, own,
                             incarnation=str(r))
        for p, f in p2.items():
            f.result()
        self._phase_ms["phase2"].append((time.perf_counter()-_t_p2)*1000.0)

    # [HELD_READ_ON_THE_FAMILY_V1 - 2026-11-12] The September shard
    # all-reduce on one permanent link per peer. Once, at the first round,
    # this rank makes a held read on each peer's family -- the cells that
    # peer writes for it: <peer>.ars.<seq>.<me> and <peer>.arr.<seq>, every
    # seq. The peer writing a cell into its own space fires the held read and
    # the transfer starts on the link at once. No flag, no per-round request,
    # no ack. Read-ahead is inherent: round N+1's write fires while round N
    # is still finishing. Nothing blocks a thread at exit.
    STREAM = os.environ.get("FROGNET_PSY_STREAM", "1") not in ("0", "no")

    # [CALLERS_FAN_IN_V1 - 2026-11-12] all_reduce returns at once; its rounds
    # run on collective workers, up to ROUNDS_IN_FLIGHT at a time, so DDP's
    # buckets are in flight together and alongside the rest of backward --
    # every bucket's cells multiplexed on the same per-peer links instead of
    # one round waiting for the last. ROUNDS_IN_FLIGHT=1 keeps rounds in call
    # order but still off the caller's thread.
    # [ADAPTIVE_POOL_V1] 0 (the default) is no cap: a round gets a worker
    # when it needs one. A number caps rounds in flight.
    ROUNDS_IN_FLIGHT = max(0, int(os.environ.get("FROGNET_PSY_ROUNDS_IN_FLIGHT", "0")))

    def _allreduce_async(self, tensors, op):
        from .torch_backend import _create_work_from_future
        with self._lock:
            seqs = []
            for _ in tensors:
                self._ar_seq += 1
                seqs.append(self._ar_seq)
        fut = torch.futures.Future()
        t_call = time.time()

        def run():
            try:
                for t, seq in zip(tensors, seqs):
                    self._allreduce_sharded(t, seq, op)
            except BaseException as e:
                _psy("ROUND_FAILED", rank=self._rank, seqs=seqs, err=repr(e)[:300])
                fut.set_exception(e)
                raise
            fut.set_result(list(tensors))
            if _PSY_DIAG:
                _psy("ROUND_DONE", rank=self._rank, seqs=seqs,
                     call_to_done_ms="%.1f" % ((time.time() - t_call) * 1000.0))
        with self._coll_lock:
            if getattr(self, "_coll", None) is None:
                from .tensor_plane import AdaptivePool
                self._coll = AdaptivePool("psy-round", cap=self.ROUNDS_IN_FLIGHT)
                self._inflight = []
            self._inflight = [f for f in self._inflight if not f.done()]
            self._inflight.append(self._coll.submit(run))
        return _create_work_from_future(fut)

    def _drain(self) -> None:
        """Every all_reduce already called has finished before any other
        collective starts, so each rank runs collectives in the order it
        called them; a failure in one is raised here."""
        with self._coll_lock:
            pending = list(getattr(self, "_inflight", None) or [])
        for f in pending:
            f.result()

    def _round_stage(self, seq: int, rows: int, ln: int, like):
        """Staging rows for one round, checked out for that round alone and
        handed back by _round_stage_done. Rounds finish out of order, so a
        buffer chosen by seq number would be shared by two live rounds; a
        checkout cannot be. Freed buffers are reused, so steady state
        allocates nothing."""
        wide = self._hwm(ln, like)
        key = (rows, wide, str(like.dtype))
        with self._coll_lock:
            free = getattr(self, "_stage_free", None)
            if free is None:
                free = self._stage_free = {}
            lst = free.get(key)
            b = lst.pop() if lst else None
            if not hasattr(self, "_stage_out"):
                self._stage_out = {}
        if b is None:
            b = torch.empty(rows * wide, dtype=like.dtype)
        with self._coll_lock:
            self._stage_out[seq] = (key, b)
        return [b[i * wide:i * wide + ln] for i in range(rows)]

    def _round_stage_done(self, seq: int) -> None:
        with self._coll_lock:
            got = getattr(self, "_stage_out", {}).pop(seq, None)
            if got is not None:
                self._stage_free.setdefault(got[0], []).append(got[1])

    def _stream(self, p: int) -> StreamSubscription:
        subs = getattr(self, "_streams", None)
        if subs is None:
            subs = self._streams = {}
        s = subs.get(p)
        if s is not None:
            return s
        import re as _re
        with self._coll_lock:
            s = subs.get(p)
            if s is None:
                d = self._rendezvous()[p]
                pi = _re.escape(self._peer_inc(p))
                s = subs[p] = StreamSubscription(
                    "r%d" % self._rank, d["host"], int(d["port"]),
                    "psy-r%d-from-r%d" % (self._rank, p),
                    family=[pi + r"\.ars\.\d+\." + str(self._rank),
                            pi + r"\.arr\.\d+"])
        return s

    def _timing(self, p: int) -> "_PeerTiming":
        tm = getattr(self, "_peer_timing", None)
        if tm is None:
            tm = self._peer_timing = {}
        t = tm.get(p)
        if t is None:
            t = tm[p] = _PeerTiming()
        return t

    def _take(self, p: int, name: str, seq: int, phase: str, dst=None):
        sub = self._stream(p)
        d = self._rendezvous()[p]
        tm = self._timing(p)
        with _PsyWait(phase=phase, rank=self._rank, seq=seq, peer=p, cell=name,
                      gen=seq, host=d["host"], port=d["port"], stream=1,
                      stall_s="%.2f" % tm.stall_s(),
                      peer_ewma_s=("%.3f" % tm.ewma) if tm.ewma is not None else "-",
                      _be=self):
            try:
                c = sub.take(name, dst, timeout_s=self._timeout_s)
                # What the peer's cell took on the wire, first data record to
                # last byte, on this machine's clock only (no skew), and not
                # how long this rank waited for the peer to write it.
                tm.observe(max(0.0, c["t_land"] - (c.get("t_first") or c["t_land"])))
                return c
            except Exception as e:
                if hasattr(e, "add_note"):
                    e.add_note("rank %d waiting for %s pushed by rank %d at "
                               "%s:%s; stream %s"
                               % (self._rank, name, p, d.get("host"),
                                  d.get("port"), sub.stats()))
                raise

    def _allreduce_streamed(self, t: torch.Tensor, seq: int, acc_fn) -> None:
        _t_pub = time.perf_counter()
        W, r = self._size, self._rank
        flat = t.reshape(-1)
        parts = self._slices(flat.numel())
        inc = self._peer_inc(r)
        peers = [q for q in range(W) if q != r]
        for p in peers:
            self._stream(p)
        st_r, ln_r = parts[r]
        own = flat[st_r:st_r + ln_r]
        esz = flat.element_size()

        # [HELD_READ_ON_THE_FAMILY_V1] Nothing is requested per round: the
        # held read on each peer's family, made once, fires on every write.
        # What this does is say WHERE a cell goes if it has not landed yet,
        # so it is received straight into place -- phase-1 slices into the
        # staging rows, reduced slices into the answer. The phase-2 ranges
        # are this rank's own phase-1 slices for those peers, on their way
        # out now; peer p cannot write its reduced slice until it has all of
        # this rank's slice, so the overwrite comes after the send -- the
        # September invariant, unchanged. A cell that came early (the peer
        # was a round ahead) is already in a buffer and is taken from there.
        stage = self._round_stage(seq, W - 1, ln_r, own) if ln_r else []
        for i, p in enumerate(peers):
            pi = self._peer_inc(p)
            st_p, ln_p = parts[p]
            if ln_r:
                self._streams[p].expect("%s.ars.%d.%d" % (pi, seq, r),
                                        writable_bytes(stage[i]))
            if ln_p:
                self._streams[p].expect("%s.arr.%d" % (pi, seq),
                                        writable_bytes(flat[st_p:st_p + ln_p]))

        for k, (st, ln) in enumerate(parts):
            if ln and k != r:
                nm = "%s.ars.%d.%d" % (inc, seq, k)
                self._server.publish(nm, seq, flat[st:st + ln],
                                     incarnation=str(r), copy=False, readers=1)
        pub_ms = (time.perf_counter() - _t_pub) * 1000.0
        self._phase_ms["publish"].append(pub_ms)

        _t_p1 = time.perf_counter()
        w1 = {}
        if ln_r:
            for i, p in enumerate(peers):
                nm = "%s.ars.%d.%d" % (self._peer_inc(p), seq, r)
                c = self._take(p, nm, seq, "s1")
                if c["n"] != ln_r * esz:
                    raise RuntimeError(
                        "rank %d round %d: own slice is %d bytes, r%d pushed %d "
                        "for %s. The ranks do not agree on the bucket."
                        % (r, seq, ln_r * esz, p, c["n"], nm))
                if c["direct"]:
                    acc_fn(own, stage[i])
                else:
                    acc_fn(own, plane_view(c["buf"], own.dtype, ln_r))
                    self._streams[p].release(c)
                w1[p] = c
        p1_ms = (time.perf_counter() - _t_p1) * 1000.0
        self._phase_ms["phase1"].append(p1_ms)

        _t_p2 = time.perf_counter()
        if ln_r:
            nm = "%s.arr.%d" % (inc, seq)
            self._server.publish(nm, seq, own, incarnation=str(r),
                                 readers=W - 1)
        w2 = {}
        for p in peers:
            st_p, ln_p = parts[p]
            if not ln_p:
                continue
            c = self._take(p, "%s.arr.%d" % (self._peer_inc(p), seq), seq, "s2")
            if c["n"] != ln_p * esz:
                raise RuntimeError(
                    "rank %d round %d: r%d's reduced slice is %d bytes, expected "
                    "%d" % (r, seq, p, c["n"], ln_p * esz))
            if not c["direct"]:
                flat[st_p:st_p + ln_p].copy_(plane_view(c["buf"], flat.dtype, ln_p))
                self._streams[p].release(c)
            w2[p] = c
        p2_ms = (time.perf_counter() - _t_p2) * 1000.0
        self._round_stage_done(seq)
        self._phase_ms["phase2"].append(p2_ms)

        for p, c in list(w1.items()) + list(w2.items()):
            self._peer_ms.setdefault(p, []).append(c["waited_ms"])
        if _PSY_DIAG:
            # One line per round. wait = how long this rank sat on that cell;
            # early = it had landed before this rank asked (the stream was
            # ahead); direct = received straight into place. The slowest
            # peer in each phase is named.
            def _w(ws):
                return ",".join("r%d:%.0f%s%s" % (p, c["waited_ms"],
                                                  "e" if c["early"] else "",
                                                  "d" if c["direct"] else "")
                                for p, c in sorted(ws.items()))
            s1 = max(w1.items(), key=lambda kv: kv[1]["waited_ms"])[0] if w1 else -1
            s2 = max(w2.items(), key=lambda kv: kv[1]["waited_ms"])[0] if w2 else -1
            _psy("ROUND", rank=r, seq=seq, numel=flat.numel(), slice=ln_r,
                 pub_ms="%.1f" % pub_ms, p1_ms="%.1f" % p1_ms,
                 p2_ms="%.1f" % p2_ms, p1_wait=_w(w1), p1_slowest="r%d" % s1,
                 p2_wait=_w(w2), p2_slowest="r%d" % s2,
                 t="%.3f" % time.time())

    def stream_stats(self) -> Dict[str, Any]:
        out = {"server": {"cells_pushed": self._server.stream_cells,
                          "retired": self._server.retired,
                          "held": len(self._server._current)}}
        for p, s in sorted(getattr(self, "_streams", {}).items()):
            st = s.stats()
            st["tcp"] = tcp_info(s.sock)
            out["from_r%d" % p] = st
        return out


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
                        like, wait_s=self._peer_wait(), channel="pf",
                        retain=True)
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
        """[ONE_SOCKET_PER_PEER_ONE_READER_AT_A_TIME_V1] One client per peer.

        Reads to different peers share nothing -- different clients,
        different sockets, different locks -- so they all overlap. That is
        why this is fast.

        It was briefly keyed by (peer, THREAD) instead, to stop two pool
        threads interleaving frames on one socket. That works and it costs a
        socket per thread per peer: at world 4, twelve links per rank, each
        used a quarter as often, and the server reaps a link idle past its
        timeout. The failure appears only at world 4 and only at the largest
        size -- "peer closed after 0 of 8 bytes", the server having closed a
        link its peer was about to reuse.

        TensorClient now takes a lock per peer across the request/reply, so
        one socket per peer is safe again and stays warm.
        """
        c = self._clients.get(r)
        if c is None:
            c = TensorClient("psy-r%d-to-r%d" % (self._rank, r))
            self._clients[r] = c
        return c


    def _read_peer(self, r: int, name: str, gen: int,
                   like: torch.Tensor, wait_s: float = 0.0,
                   channel: str = "d", retain: bool = False) -> torch.Tensor:
        """Ask rank r for this state and wait for it. No store involved.

        [A_CONVERSATION_NEEDS_ITS_OWN_LINK_V1] `channel` picks which link to
        this peer the request travels on. The server serves every connection
        on its own thread and sends outside its lock, so N readers of one
        machine run in parallel -- but two threads sharing ONE link must not
        interleave their frames, so they take a lock and queue.
        Two conversations that are genuinely concurrent therefore want two
        links. The prefetcher and the collective are exactly that: one
        reading generation seq+1 while the other reads seq, from the same
        peer, at the same time, and on one channel they serialised for no
        reason the server imposed.
        """
        # [READ_WHERE_THE_ANSWER_GOES_V1] One pass, not two.
        #
        # read() allocates a receive buffer, builds a tensor over it and
        # copies that out; the caller then copies the result where it
        # belongs. read_into reuses a per-link buffer and copies straight
        # into the destination.
        #
        # This keeps the returning form because five collectives call it
        # and expect a tensor back -- but it fills a buffer it owns rather
        # than allocating one per read, so the allocation goes and one of
        # the two passes with it.
        #
        # SAME suppression is unavailable on the read_into path, since its
        # digest cache would hold views over a buffer about to be reused.
        # That costs nothing for a caller whose content changes every
        # round, and every caller here is one.
        d = self._rendezvous()[r]
        peer = "r%d" % r
        cli = self._peer_client(r)
        # `retain` says the caller keeps the tensor past its next read on
        # this peer and channel -- the prefetcher parks it in the inbox for
        # a later round. A reused slot would be overwritten under it, so
        # those get a fresh buffer.
        if self.READ_INTO and not retain:
            dst = self._recv_slot(r, like, channel)
            cli.read_into(dst.reshape(-1), peer, d["host"], int(d["port"]),
                          name, gen, mode=READ_CURRENT, wait_s=wait_s,
                          verify=self.VERIFY, channel=channel)
            return dst
        self._seed_have(peer, d["host"], int(d["port"]), name, cli,
                        channel=channel)
        t, _n, _same = cli.read(
            peer, d["host"], int(d["port"]), name, gen,
            list(like.shape), str(like.dtype), mode=READ_CURRENT,
            wait_s=wait_s, channel=channel)
        got = cli._cache.get(
            cli.cache_key(d["host"], int(d["port"]), channel, name))
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
        # [ONE_NUMBER_IS_THE_IDENTITY_V1] The name carries `seq`; the
        # reader asks for generation `seq`. Publishing under `_gen`
        # -- a counter only publishes advance, while `seq` comes from
        # _next_seq() which barriers advance too -- makes the reader
        # wait for a generation this name will never reach. Same
        # defect that cost all_reduce 30 s per collective.
        name = "%s.%s.%d" % (self._incarnation, op, seq)
        self._server.publish(name, seq, t, incarnation=str(self._rank))
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
                            # Same number the plane was published under.
                            "name": name, "gen": seq,
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
        seen = set()
        while time.time() < deadline:
            # [ASK_ONCE_AND_WAIT_V1 - John 2026-09-14] Same spin as broadcast
            # had: a 2 ms ceiling asking whether the readers are done yet.
            #
            # min_rows is len(want) -- every rank EXCEPT this one publishes a
            # consumed-marker, so the producer is waiting for W-1 rows, not W.
            # Asking for W would never be satisfied and the read would hold for
            # the full wait on every pass.
            _left = deadline - time.time()
            # [DIAG_PSY_STALL_V1 - John 2026-09-14] This wait was NOT
            # registered, which is why a rank sitting here printed nothing at
            # all while every reader and server thread showed idle. A producer
            # waiting on its readers is exactly what a deadlock looks like, and
            # it was the one state with no line.
            with _PsyWait(phase="await_consumers", rank=self._rank, seq=seq,
                          op=op, want=sorted(want), have=sorted(seen),
                          missing=sorted(want - seen),
                          min_rows=len(want), _be=self):
                seen = set(self._read_all(
                    op + ".done", seq,
                    wait_s=min(_left, _base.BLOCK_CAP) if _left > 0 else 0.0,
                    min_rows=len(want)).keys())
            if want.issubset(seen):
                return
            time.sleep(wait)
            wait = min(wait * 2.0, 0.002)
        raise RuntimeError(
            "psychedelic backend: %s seq %d -- rank %d still holds bytes "
            "nobody collected; readers %s never reported consuming them"
            % (op, seq, self._rank, sorted(want - seen)))

    def _seed_have(self, peer: str, host: str, port: int, name: str,
                   cli=None, channel: str = "d") -> None:
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
        c = cli if cli is not None else self._client
        # The cache's key belongs to the cache. Building it here by hand is
        # how the two formats drifted apart and SAME stopped firing.
        ck = c.cache_key(host, port, channel, name)
        if ck not in c._cache:
            c._cache[ck] = prev

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
        if self.STREAM and self.REDUCE == "shard":
            return self._allreduce_async(tensors, op)
        self._drain()
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
            # [NOBODY_READS_IT_SO_DO_NOT_WRITE_IT_V1 - 2026-11-12] This
            # published the WHOLE tensor -- a full copy -- before every
            # all-reduce, and the shard path never reads an ".ar." cell. A
            # profile of a loopback DDP rank put it at the top of the main
            # thread, above backward. It was never dropped either: one copy
            # of every bucket of every step, held for the life of the job.
            # Only the stack path reads it, so only the stack path writes it.
            if self.REDUCE != "shard":
                self._server.publish(name, seq, t,
                                     incarnation=str(self._rank))


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
                # [ASK_ONCE_AND_WAIT_V1 - John 2026-09-14] Let the store hold
                # the question open instead of asking again every 2 ms.
                #
                # This loop called _read_all() with no wait_s, so it spun at up
                # to 500 asks/second for a descriptor exactly one rank writes.
                # Measured on Seattle5 during ddp1921: 649 requests in 11.5 s
                # for a single key, max 1 in flight, so the proxy's coalescing
                # had nothing to join -- serial polls cannot coalesce. The
                # blocking read reached the collective path (torch_backend:559)
                # and never reached broadcast.
                #
                # min_rows is 1, NOT self._size: a broadcast has one producer,
                # the root. Waiting for W rows on a key only the root ever
                # writes is a condition that can never be satisfied, so the
                # store would hold for the full wait and return empty every
                # time -- slower than the spin it replaced.
                #
                # A store that does not implement waiting answers immediately
                # and the backoff below still runs, so this degrades to polling
                # rather than breaking.
                _left = deadline - time.time()
                # [DIAG_PSY_STALL_V1] Registered for the same reason as
                # _await_consumers: a rank waiting on the root's descriptor
                # otherwise sits silent with every thread idle.
                with _PsyWait(phase="broadcast", rank=self._rank, seq=seq,
                              op="bc", root=root, min_rows=1, _be=self):
                    d = self._read_all(
                        "bc", seq,
                        wait_s=min(_left, _base.BLOCK_CAP) if _left > 0 else 0.0,
                        min_rows=1).get(root)
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
            # [ONE_NUMBER_IS_THE_IDENTITY_V1] The name carries `seq`; the
            # reader asks for generation `seq`. Publishing under `_gen`
            # -- a counter only publishes advance, while `seq` comes from
            # _next_seq() which barriers advance too -- makes the reader
            # wait for a generation this name will never reach. Same
            # defect that cost all_reduce 30 s per collective.
            self._server.publish(name, seq, inp,
                                 incarnation=str(self._rank))
            for r in range(self._size):
                if r == self._rank:
                    outs[r].copy_(inp)
                else:
                    # [READ_WHERE_THE_ANSWER_GOES_V1] _read_peer allocates a
                    # tensor, fills it, and the caller copies that into the
                    # output: two passes over every peer's contribution.
                    # read_into writes the destination directly.
                    peer_name = "%s.%s.%d" % (self._peer_inc(r), "ag", seq)
                    self._read_slice_into(outs[r].reshape(-1), r, peer_name,
                                          seq, self._peer_wait(), "ag")
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
            # [ONE_NUMBER_IS_THE_IDENTITY_V1] The name carries `seq`; the
            # reader asks for generation `seq`. Publishing under `_gen`
            # -- a counter only publishes advance, while `seq` comes from
            # _next_seq() which barriers advance too -- makes the reader
            # wait for a generation this name will never reach. Same
            # defect that cost all_reduce 30 s per collective.
            self._server.publish(name, seq, inp,
                                 incarnation=str(self._rank))
            if self._rank != root or not output_tensors:
                continue
            outs = output_tensors[i] if i < len(output_tensors) \
                else output_tensors[0]
            for r in range(self._size):
                if r == self._rank:
                    outs[r].copy_(inp)
                else:
                    # [READ_WHERE_THE_ANSWER_GOES_V1] Straight into the
                    # output rather than into a temporary and then copied.
                    peer_name = "%s.%s.%d" % (self._peer_inc(r), "ga", seq)
                    self._read_slice_into(outs[r].reshape(-1), r, peer_name,
                                          seq, self._peer_wait(), "ga")
        return _base._Work(output_tensors)

    def scatter(self, output_tensors, input_tensors, opts=None):
        """[SEND_EACH_READER_ONLY_WHAT_IT_ASKED_FOR_V1] One named state per
        destination; each rank reads its own and nothing else.

        Stage I put every rank's slice into one control-plane structure as
        float text: 344 ms at 256 KB. Moving it to the plane fixed the
        encoding but kept the shape -- one concatenated state that every
        reader fetched in full to keep 1/W of it. W times the bytes per
        reader, plus a full copy on the root to build the concatenation.

        A name per destination costs the root nothing extra (the same bytes
        are offered, just named separately, and as views rather than a
        copy) and costs each reader exactly its own slice.
        """
        root = getattr(opts, "rootRank", 0)
        for i, out in enumerate(output_tensors):
            seq = self._next_seq()
            if self._rank == root:
                ins = input_tensors[i] if i < len(input_tensors) \
                    else input_tensors[0]
                # [SEND_EACH_READER_ONLY_WHAT_IT_ASKED_FOR_V1]
                #
                # This concatenated every rank's slice into ONE state and
                # published that. Each reader then fetched the WHOLE thing
                # and kept its own piece -- W times the bytes it needed,
                # per reader, and a full copy on the root to build the
                # concatenation in the first place.
                #
                # One named state per destination instead. Reader k asks
                # for slice k and nothing else, and the root publishes
                # views of the tensors it was handed, so no copy is made
                # at all.
                for k, t in enumerate(ins):
                    self._server.publish(
                        "%s.sc.%d.%d" % (self._peer_inc(root), seq, k), seq,
                        t.detach().reshape(-1), incarnation=str(root))
                out.copy_(ins[self._rank].reshape(out.shape).to(out.dtype))
                continue
            name = "%s.sc.%d.%d" % (self._peer_inc(root), seq, self._rank)
            # Straight into the destination: no intermediate tensor, one
            # pass over the payload.
            self._read_slice_into(out.reshape(-1), root, name, seq,
                                  self._peer_wait(), "sc")
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
            # [ONE_NUMBER_IS_THE_IDENTITY_V1] The name carries `seq`; the
            # reader asks for generation `seq`. Publishing under `_gen`
            # -- a counter only publishes advance, while `seq` comes from
            # _next_seq() which barriers advance too -- makes the reader
            # wait for a generation this name will never reach. Same
            # defect that cost all_reduce 30 s per collective.
            self._server.publish(name, seq, whole,
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

    def link_stats(self) -> Dict[str, Any]:
        """[WAITING_IS_NOT_TRANSFERRING_V1] Per-read wait/transfer/bytes,
        from every client, for every path -- shard AND stack.

        peer_ms recorded only the total, and only for the sharded path, so
        the arm that was 1.2x slower could not be broken down by link at
        all. This covers every read the backend makes, and splits each one
        into the part that was the peer not being ready and the part that
        was the pipe.

        Summarised per (peer, channel): the raw samples are on the client
        if a caller wants them, but a run makes thousands and the shape is
        what matters -- median wait, median transfer, and the bandwidth the
        two of them imply.
        """
        import statistics as _st
        out = {}
        for r, cli in sorted(self._clients.items()):
            for smp in getattr(cli, "samples", []):
                k = "r%d|%s" % (r, smp["peer"].split("#")[-1])
                d = out.setdefault(k, {"n": 0, "wait": [], "xfer": [],
                                       "bytes": 0})
                d["n"] += 1
                d["wait"].append(smp["wait_ms"])
                d["xfer"].append(smp["xfer_ms"])
                d["bytes"] += smp["bytes"]
        res = {}
        for k, d in out.items():
            w = sorted(d["wait"]); x = sorted(d["xfer"])
            xs = sum(d["xfer"]) / 1000.0
            res[k] = {
                "reads": d["n"],
                "wait_ms_med": round(_st.median(w), 2),
                "wait_ms_p90": round(w[int(len(w) * .9)], 2),
                "xfer_ms_med": round(_st.median(x), 2),
                "xfer_ms_p90": round(x[int(len(x) * .9)], 2),
                "mbytes": round(d["bytes"] / 1e6, 2),
                # Bytes over the time they were actually moving, not over
                # the time the caller was blocked. This is the link, with
                # the peer's readiness taken out of it.
                "MB_per_s": round((d["bytes"] / 1e6) / xs, 2) if xs else None,
            }
        return res

    def timing_stats(self) -> Dict[str, Any]:
        """Raw per-peer read times and per-phase times, in milliseconds.

        Samples, not summaries: a median hides the thing worth graphing,
        which is the shape of the distribution per link.
        """
        return {"peer_ms": {str(k): v for k, v in self._peer_ms.items()},
                "phase_ms": dict(self._phase_ms),
                "rank": self._rank, "world": self._size}

    def plane_stats(self) -> Dict[str, Any]:
        """What the split bought: bytes a reader asked for against bytes that
        crossed. The gap is SAME -- state the reader already held."""
        s = dict(getattr(self._server, "stats", lambda: {})() or {})
        s["polls"] = self.polls
        s["poll_ms"] = round(self.poll_s * 1000, 2)
        s["states_offered"] = self._server.states_offered
        s["states_materialised"] = self._server.states_materialised
        return s


def _drained(name):
    orig = getattr(PsychedelicBackend, name)

    def wrapper(self, *a, **k):
        self._drain()
        return orig(self, *a, **k)
    wrapper.__name__ = name
    wrapper.__doc__ = orig.__doc__
    return wrapper


# [CALLERS_FAN_IN_V1] Any collective other than all_reduce waits for the
# all_reduces called before it, so call order is execution order on every rank.
for _n in ("broadcast", "reduce", "allgather", "gather", "scatter", "reduce_scatter",
           "_allgather_base", "allgather_into_tensor_coalesced", "allreduce_coalesced",
           "alltoall", "alltoall_base", "all_to_all_single", "send", "recv",
           "recv_anysource", "barrier", "_reduce_scatter_base"):
    if hasattr(PsychedelicBackend, _n):
        setattr(PsychedelicBackend, _n, _drained(_n))


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


#: Said once per process, so a run's own log shows which code is running.
BUILD = ("psychedelic 20261112-september-family-link-r11 (the September tensor path; "
         "one held read per peer on the family it writes for this rank; the write fires it; every ready cell interleaved on the link; channel sets, 20 ms records and unsent cap available (off by default); per-peer stall timing; LZ4 when it shrinks; exact block DIFF/SAME per instance; coalesced takers; adaptive pools; native link optional; all_reduce returns at once, rounds in flight together "
         "and the bytes go on one permanent link, without pause; "
         "control cells in the one RAM; PyTorch 2.15 port)")


def register(name: str = "psychedelic") -> None:
    from . import tensor_plane as _tp
    print("[psychedelic] link: sets=%d native=%s lz4=%s notsent=%d ceil_ms=%.0f rounds_in_flight=%d"
          % (_tp.STREAM_SETS, "on" if _tp.PLANE_NATIVE else "off", "on" if _tp.STREAM_LZ4 else "off",
             _tp.STREAM_NOTSENT, _tp.STREAM_CEIL_S * 1000, PsychedelicBackend.ROUNDS_IN_FLIGHT), flush=True)
    print("[psychedelic] %s; family link %s; read-ahead %s" % (
        BUILD, "on" if PsychedelicBackend.STREAM else "OFF",
        "inherent (held read on the family)" if PsychedelicBackend.STREAM else
        ("on" if PsychedelicBackend.READAHEAD else "OFF")), flush=True)
    # [NO_FLIGHT_RECORDER_ON_A_PYTHON_BACKEND_V1] PyTorch 2.15 attaches its flight recorder to every group whose
    # backend does not record itself, and attaching it to a Python-subclassed backend segfaults. 0 is its own off
    # switch; a caller who set it keeps their value. (2.15 port; not in the September code.)
    os.environ.setdefault("TORCH_FR_BUFFER_SIZE", "0")
    _sw = os.environ.get("FROGNET_PSY_SWITCH_S")
    if _sw:
        import sys as _sys
        _sys.setswitchinterval(float(_sw))
    dist.Backend.register_backend(name, _create, devices=["cpu"])
