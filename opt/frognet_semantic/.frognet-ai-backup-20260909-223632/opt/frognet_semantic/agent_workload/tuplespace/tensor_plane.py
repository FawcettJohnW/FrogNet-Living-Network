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
agent_workload/tuplespace/tensor_plane.py -- the data plane for descriptors.

[THE_DESCRIPTOR_HAS_A_DATA_PLANE_V1]

reduce_by_read publishes a descriptor -- shape, dtype, generation, locator --
into the tuple space and raised on read, because nothing was attached to
read through. This attaches it, on the arrangement the rest of FrogNet already
uses: the control plane sets the path up and holds current state, the bytes
ride a PERMANENT socket, and what crosses the wire is SAME or the payload.

    control plane   torch.grad.<worker> = {gen, shape, dtype, loc:{port,...}}
    data plane      a permanent link per (reader, writer), opened once

A peer's tensor is READ, not fetched. The transport underneath is a request
over a socket, exactly as reading the tuple space is a request over HTTP, and
neither makes the operation a fetch: what the caller is doing is reading a
segment of memory that happens to live on another machine. The verb matters
because the next person writes to the verb they see, and `fetch` invites the
retry-and-dependency thinking this model exists to remove.

The publisher does not know who reads it and must not have to. Readers
arrive, leave and re-read at their own pace. A reader opens one link per peer
and keeps it.

SAME, honestly
--------------
Successive generations from one worker are frequently identical -- a worker
that has not stepped republishes the same tensor, and a reader polling faster
than the writer publishes asks for the same generation repeatedly.

The READER states the digest it already holds, on every request. The server
compares it against what is current and answers SAME or sends the tensor. The
server keeps no record of any reader: what a reader has is the reader's fact,
and a server that remembers it is holding a copy that goes stale the moment
that reader restarts. [THE_SERVER_DOES_NOT_MODEL_THE_READER_V1]

That is real and it is measured: `stats()` reports bytes offered against
bytes sent. What is NOT implemented is a delta for a tensor that changed --
a byte-level diff of float32 gradients is not meaningful and inventing a
lossy one would be a compression scheme wearing SAME/DIFF's name. When the
tensor differs, the tensor goes. [NO FALLBACKS]

Wire format, reusing aiconnect's framing rather than inventing a second:
    request   <!Q len><json {name, gen, have}>
    reply     <!Q len><SAME|DATA><payload>
"""

from __future__ import annotations

import hashlib
import json
import selectors
import socket
import os
import struct
import zlib
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple

from agent_workload.aiconnect.service import (BULK_LEN_FMT, BULK_LEN_LEN,
                                              bulk_frame)

from .finite import STAGE_PLANE_PUBLISH, STAGE_PLANE_RECV, FiniteWatch

try:
    from core.frognet_diag import diag, diag_exc
except ImportError:                                    # pragma: no cover
    def diag(*a, **k):
        pass

    def diag_exc(*a, **k):
        pass

REPLY_SAME = b"SAME"
REPLY_DATA = b"DATA"
REPLY_GONE = b"GONE"

#: [A_STATE_IS_WHAT_IT_SAYS_IT_IS_V1] Why a state could not be served. A
#: policy has to tell these apart: SUPERSEDED means the producer moved on and
#: a newer state exists to re-select; NEVER_PUBLISHED means there is nothing
#: there and re-selecting will not help.
GONE_NEVER_PUBLISHED = "never_published"
GONE_SUPERSEDED = "superseded"

#: [TWO_READS_NOT_ONE_PRETENDING_V1] Two different questions, kept apart
#: because merging them is what produced silent substitution.
#:
#: BY_IDENTITY  "give me exactly this state." The consumer reasoned about a
#:              particular identity -- lineage, a delta base, a policy that
#:              named it. Answer it exactly or answer GONE.
#: CURRENT      "give me what you hold now, and tell me what it is." The
#:              consumer selected the PRODUCER, not a state. There is nothing
#:              to race: the reply is authoritative and the consumer books
#:              what arrives.
#:
#: The old read did the second while claiming the first -- it returned current
#: bytes and left the descriptor's older generation in the consumer's
#: accounting. Requesting current is not substitution; requesting 42 and
#: booking 42 while holding 43 is.
READ_BY_IDENTITY = "by_identity"
READ_CURRENT = "current"


class StateGone(KeyError):
    """The identity that was asked for is not what the producer holds.

    Carries `reason` (one of the GONE_* constants above), the requested
    generation and digest, and what is current if anything is -- so a policy
    can re-select without another round trip, and a diagnostic can say what
    happened rather than that something did.
    """

    def __init__(self, message, reason, worker=None, name=None,
                 want_gen=None, want_digest=None, current_gen=None,
                 current_digest=None):
        super().__init__(message)
        self.reason = reason
        self.worker = worker
        self.name = name
        self.want_gen = want_gen
        self.want_digest = want_digest
        self.current_gen = current_gen
        self.current_digest = current_digest
_TAG_LEN = 4

#: A tail this size or smaller is joined with the header rather than written
#: separately. Copying a few hundred bytes is free; copying a tensor is the
#: thing this file is avoiding.
_SMALL_TAIL = 4096


def _recv_exact(sk: socket.socket, n: int, into=None) -> memoryview:
    """[FRAME_WITHOUT_COPYING_THE_PAYLOAD_V1] Read n bytes into ONE buffer.

    This was `buf = b""` and `buf += b` per read, which reallocates and
    copies everything received so far on every chunk. aiconnect's bulk
    receiver had exactly this defect and this file repeated it.

    [THE_RATIONALE_MUST_MATCH_THE_MEASUREMENT_V1] The 4.4x first written here
    -- 39.4 ms against 8.9 ms for 16 MB -- came from a probe whose sender
    dribbled the payload, so the concatenating loop ran hundreds of times.
    With the frames this code actually sends, loopback delivers the payload
    in a handful of large chunks, the loop runs a handful of times, and the
    difference is not measurable: the shipped tree reads 16 MB at ~650 MB/s
    either way. The change is kept because it is the right shape and holds up
    when the chunks ARE small -- a congested link, a small receive buffer, a
    real NIC under load -- but it is not a measured win here and must not be
    quoted as one.

    Returns a memoryview over the buffer rather than bytes, so the caller
    slices without copying. `tensor_from` and `json.loads` both take one.

    [ALLOCATE_ONCE_NOT_ONCE_PER_FRAME_V1] `into` supplies a buffer the
    caller already owns, so a steady stream of same-sized frames stops
    allocating a fresh bytearray per read. It is NOT cleared first: the
    receive fills exactly n bytes before anything reads them, so a memset
    would add a full pass over the buffer to no purpose -- the opposite of
    why the buffer is being reused.

    A reused buffer must not ESCAPE. The returned memoryview aliases it, so
    the next read on the same buffer rewrites data a previous caller may
    still hold. Only pass `into` when the value is consumed before the next
    read on that buffer -- which is what read_into below guarantees and what
    a caller holding the result across reads does not.
    """
    if into is not None:
        # `into` may be a buffer or a CALLABLE that returns one of at least
        # n bytes. It has to allow the callable form: the frame length is
        # not known until the header has been read, so a caller choosing a
        # fixed buffer beforehand can only guess -- and guessing 4096 for an
        # 87 KB frame is how the first version of this failed.
        buf = into(n) if callable(into) else into
        if len(buf) < n:
            raise ValueError("scratch buffer holds %d, frame needs %d"
                             % (len(buf), n))
        mv = memoryview(buf)[:n]
    else:
        buf = bytearray(n)
        mv = memoryview(buf)
    got = 0
    while got < n:
        k = sk.recv_into(mv[got:], n - got)
        if not k:
            raise ConnectionError("peer closed after %d of %d bytes"
                                  % (got, n))
        got += k
    return mv


#: [A_LENGTH_IS_A_CLAIM_NOT_A_FACT_V1] Largest frame this plane will ever
#: allocate for. A length header is bytes off a socket: if the socket is
#: desynced -- two threads sharing it, a reader abandoned mid-frame -- the
#: next "length" is read out of the middle of someone else's payload and can
#: be any 64-bit number. `bytearray(n)` then raises MemoryError from inside
#: the read, which names neither the socket nor the cause, and looks like the
#: machine running out of memory rather than a protocol fault.
#: 1 GiB is far above any real tensor frame and far below a number that
#: cannot be allocated.
MAX_FRAME = 1 << 30

#: Ceiling on how long one server thread will hold a connection waiting for
#: a state to appear. A reader may ask for less; asking for more is capped
#: here so a client cannot pin a server thread indefinitely.
MAX_SERVER_WAIT_S = float(os.environ.get("FROGNET_PLANE_MAX_WAIT_S", "300"))


def _recv_frame(sk: socket.socket, into=None) -> memoryview:
    (n,) = struct.unpack(BULK_LEN_FMT, bytes(_recv_exact(sk, BULK_LEN_LEN)))
    if n < 0 or n > MAX_FRAME:
        try:
            who = sk.getpeername()
        except OSError:
            who = "?"
        raise ConnectionError(
            "frame length %d from %s is not a frame: this socket is out of "
            "step with its peer, which happens when two readers share it or "
            "one is abandoned mid-frame. Not a memory problem." % (n, who))
    return _recv_exact(sk, n, into=into)


def _send_frame(sk: socket.socket, *parts) -> None:
    """[FRAME_WITHOUT_COPYING_THE_PAYLOAD_V1] One header write, one payload
    write.

    This was `sk.sendall(bulk_frame(len(payload)) + payload)`, and that `+`
    is a full copy of the tensor to prepend eight bytes to it.

    The first attempt at the fix sent every part with its own sendall, which
    is correct and was 1400x slower on a SAME reply: 0.03 ms became 44 ms,
    the shape of a delayed ACK. `_serve` never set TCP_NODELAY on the
    connection it accepted -- only the client set it on its end -- so a small
    write followed by another small write waited for the peer. That is fixed
    where it belongs, on accept, and this still does not split gratuitously:
    the small parts are joined (copying tens of bytes) and the payload is
    sent on its own (copying none of it).
    """
    n = sum(len(p) for p in parts)
    head = bulk_frame(n) + b"".join(bytes(p) for p in parts[:-1])
    tail = parts[-1] if parts else b""
    if len(tail) <= _SMALL_TAIL:
        sk.sendall(head + bytes(tail))
        return
    sk.sendall(head)
    sk.sendall(tail)



#: [A_DIGEST_IS_AN_INTEGRITY_CHECK_NOT_A_SIGNATURE_V1]
#:
#: The digest does two jobs here: it tells a reader that the bytes it already
#: holds are still the peer's current state (SAME), and it proves the bytes
#: that crossed are the bytes that were sent. Neither is adversarial. Nothing
#: here defends against a forged tensor -- a peer that wanted to lie would
#: publish a different tensor and hash it honestly.
#:
#: sha256 costs 0.79 ms per megabyte and is paid TWICE per read, once when
#: the producer materialises and once when the reader verifies. Profiled at 1
#: MB it was 9% of a read. crc32 is 0.24 ms for the same buffer -- 3.3x
#: cheaper -- and catches the corruption this is actually looking for: a
#: truncated frame, a flipped bit, a state served under the wrong name.
#:
#: FROGNET_PLANE_DIGEST=sha256 restores the old one. The digest is written
#: into the descriptor and compared across peers, so every participant in a
#: run must agree; the algorithm is therefore process-wide and not per-call.
_DIGEST_ALGO = os.environ.get("FROGNET_PLANE_DIGEST", "crc32").lower()


def _digest(b) -> str:
    """16 hex characters, whichever algorithm this run agreed on."""
    if _DIGEST_ALGO == "crc32":
        # Two crc32 passes over halves, so the value is 16 hex wide like the
        # truncated sha256 it replaces and a stale descriptor cannot be
        # mistaken for a fresh one by width alone.
        mv = memoryview(b)
        h = len(mv) // 2
        return "%08x%08x" % (zlib.crc32(mv[:h]) & 0xFFFFFFFF,
                             zlib.crc32(mv[h:]) & 0xFFFFFFFF)
    return hashlib.sha256(b).hexdigest()[:16]


def tensor_bytes(t) -> bytes:
    # [THE_TABLE_MUST_KNOW_EVERY_DTYPE_V1] numpy cannot see bfloat16 at all,
    # so .numpy() raises on the ENCODE side as well. View the same storage as
    # int16 and take those bytes: the pattern is carried exactly, where
    # converting through float32 would change what was sent.
    t = t.detach().to("cpu").contiguous()
    import torch as _t
    if t.dtype == _t.bfloat16:
        return t.view(_t.int16).numpy().tobytes()
    return t.numpy().tobytes()


def tensor_from(buf: bytes, shape, dtype):
    import numpy as np
    import torch
    # [THE_TABLE_MUST_KNOW_EVERY_DTYPE_V1] PyTorch's own suite exercises the
    # whole set, not just the float types a hand-written matrix happens to
    # use. Five entries passed 14 of 14 by hand and failed the suite on
    # torch.int8. Guessing is still refused below -- the fix is to know
    # more, never to assume.
    np_dt = {"torch.float32": np.float32, "torch.float64": np.float64,
             "torch.float16": np.float16, "torch.bfloat16": None,
             "torch.int64": np.int64, "torch.int32": np.int32,
             "torch.int16": np.int16, "torch.int8": np.int8,
             "torch.uint8": np.uint8, "torch.bool": np.bool_,
             "torch.complex64": np.complex64,
             "torch.complex128": np.complex128}.get(dtype)
    if dtype == "torch.bfloat16":
        # numpy has no bfloat16. Carry the raw 2-byte pattern and let torch
        # reinterpret it, which is exact -- unlike converting through
        # float32, which would change what was sent.
        import torch as _t
        return _t.frombuffer(bytearray(buf), dtype=_t.bfloat16).reshape(
            shape).clone()
    if np_dt is None:
        raise ValueError(
            "no numpy mapping for %r. The descriptor carries the dtype and "
            "this table has to know it -- guessing would silently reinterpret "
            "the bytes." % dtype)
    return torch.from_numpy(
        np.frombuffer(buf, dtype=np_dt).reshape(shape).copy())


def tensor_view(buf, shape, dtype):
    """[COPY_ONCE_NOT_TWICE_V1] A tensor over `buf` WITHOUT copying it.

    tensor_from copies because the caller keeps the result and the buffer
    may be written again underneath it. A caller that copies the value
    somewhere else immediately -- read_into does exactly that -- pays two
    passes over the payload to end up with one.

    The returned tensor ALIASES buf and must not outlive it. read_into is
    the only caller for which that holds.
    """
    import numpy as np
    import torch
    if dtype == "torch.bfloat16":
        import torch as _t
        return _t.frombuffer(buf, dtype=_t.bfloat16).reshape(shape)
    np_dt = {"torch.float32": np.float32, "torch.float64": np.float64,
             "torch.float16": np.float16,
             "torch.int64": np.int64, "torch.int32": np.int32,
             "torch.int16": np.int16, "torch.int8": np.int8,
             "torch.uint8": np.uint8, "torch.bool": np.bool_,
             "torch.complex64": np.complex64,
             "torch.complex128": np.complex128}.get(dtype)
    if np_dt is None:
        raise ValueError(
            "no numpy mapping for %r; tensor_view refuses to guess for the "
            "same reason tensor_from does." % dtype)
    return torch.from_numpy(np.frombuffer(buf, dtype=np_dt).reshape(shape))


class _NoWatch:
    """[THE_INSTRUMENT_IS_NOT_THE_INSTRUMENTED_V1] A watch that looks at
    nothing, so the call sites keep their shape and no caller has to know
    whether one is present."""

    __slots__ = ()

    def observe(self, *a, **k):
        return None

    def first(self, *a, **k):
        return None

    def report(self, *a, **k):
        return {}


class _NoLock:
    """[NOTHING_IS_OVERWRITTEN_SO_NOTHING_NEEDS_A_LOCK_V1]

    A `with` that does nothing, so the call sites keep their shape and the
    reason lives in one place rather than as a scatter of deletions.

    The server's lock guarded a mutation that no longer happens. It dates
    from when publish REPLACED a name's state; with the generation in the
    key every publish is a new entry, nothing is ever overwritten, and
    CPython's dict get and set are atomic. There is no compound update left
    to protect.

    `_materialise` still does check-compute-store, and two threads racing
    there compute identical bytes from the same immutable snapshot and store
    the same value. Idempotent -- and far cheaper than serialising every
    reader behind the one doing the encode, which is what the lock did.
    Putting the publish condition on it took a 2 MB all_reduce from 28 ms to
    100 ms; that was the lock, not the condition.

    A lock is a kernel object on the path of every read. This one was
    protecting nothing.
    """

    __slots__ = ()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TensorServer:
    """Serves this worker's published tensors to whoever asks.

    One thread, a selector, permanent connections. It holds only what its
    owner most recently published for each name -- current state, no history,
    the same rule as the space.
    """

    def __init__(self, bind_ip: str = "0.0.0.0", port: int = 0, watch=None):
        #: [FIRST_NON_FINITE_V1] The producer side of the transport boundary.
        # [THE_INSTRUMENT_IS_NOT_THE_INSTRUMENTED_V1] The watch is off on
        # the plane unless a caller asks for it.
        #
        # FiniteWatch runs isfinite, isnan, isinf, sum, max and double over
        # every tensor that crosses. It was 65% of a 1 MB read -- 8.01 ms
        # against 2.04 with it off, and 1.8x on a 128 KB all_reduce. That is
        # a debugging instrument on the data path, and it was there by
        # default on every read this tree has ever measured.
        #
        # It EARNS that in a training study: catching the first non-finite
        # value at its first appearance is the point, and a run that hides a
        # nan for a hundred steps is worth far more than the milliseconds.
        # It earns nothing in a collective, which does not care whether the
        # values are finite and whose PyTorch equivalents do not check.
        #
        # So the study passes one in and the plane does not make one. This is
        # not FROGNET_FINITE_WATCH=0 -- that switch turns the watch off
        # everywhere, including the stages where it is the whole point.
        self.watch = watch if watch is not None else _NoWatch()
        #: [MATERIALISE_ON_DEMAND_V1] Bytes+digest per name, made on demand
        #: and dropped when the state they describe is replaced.
        self._materialised = {}
        #: [A_SERVER_THAT_DIES_QUIETLY_IS_WORSE_THAN_ONE_THAT_CRASHES_V1]
        self.serve_faults = 0
        self.last_fault = ""
        self.last_fault_tb = ""
        self.states_offered = 0
        self.states_materialised = 0
        self.materialise_s = 0.0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind_ip, port))
        self.sock.listen(1024)
        self.sock.setblocking(False)
        self.port = self.sock.getsockname()[1]

        self._lock = _NoLock()
        #: [WE_OWN_BOTH_ENDS_OF_THIS_V1] publish wakes the waiting readers.
        #:
        #: Its OWN lock, not self._lock. Sharing that one put waiting
        #: readers in contention with whichever thread was materialising --
        #: the expensive path -- and cost more than the polling it replaced:
        #: 2 MB all_reduce went 28.4 ms to 100.5 ms. A waiter should block
        #: on "has anything been published", never on "is somebody encoding
        #: right now".
        #:
        #: The re-check on each turn closes the narrow race where a publish
        #: lands between a waiter's check and its wait; the bounded wait is
        #: the safety net for it, not the mechanism.
        self._pub_cv = threading.Condition()
        self._current: Dict[str, Tuple[int, bytes, str]] = {}
        # [THE_SERVER_DOES_NOT_MODEL_THE_READER_V1]
        # There was a _peer_seen map here: per peer, per name, the digest this
        # server last SENT. SAME was answered from it. That is one node
        # holding state about another node's contents, and it is wrong in the
        # ordinary case, not the exotic one -- a reader that restarts, drops
        # its cache, or reconnects leaves the server believing something the
        # reader no longer has, and the reader then raises on a SAME it cannot
        # satisfy. The error message this produced was written by hand and
        # should have been the tell.
        #
        # The reader states what it holds, in `have`, on every request. It is
        # the only authority on that. The server compares and answers. It
        # keeps no model of anybody.
        self.bytes_offered = 0
        self.bytes_sent = 0
        self.same_replies = 0
        self.data_replies = 0

        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True,
                                   name="tensor-server-%d" % self.port)
        self._t.start()
        diag("DIAG-TPLANE", "server up", port=self.port)

    def publish(self, name: str, generation: int, tensor,
                incarnation: Optional[str] = None) -> None:
        """Offer an immutable state. Serialisation and hashing are deferred.

        [MATERIALISE_ON_DEMAND_V1]
        This used to serialise the tensor and hash it on every publish,
        whether or not any consumer ever asked. Measured on the real depth-1
        path at 265,729 parameters: 1.22 ms per publish, of which sha256 was
        0.83 ms -- 68% of the cost of advertising a state, spent on a
        representation that might never leave the machine. At 16M parameters
        the pair costs 86.8 ms.

        What CANNOT be deferred is freezing the state. An advertised state
        must not change underneath its identity, and the eager serialisation
        was the only thing providing that. So publish still pays one copy --
        clone measured at roughly the same cost as serialisation, since both
        are one pass over the buffer -- and everything derived from those
        bytes waits for demand.

        Returns None. The descriptor no longer advertises a digest: identity
        is (worker, incarnation, generation), and content equivalence across
        producers is not a property anything currently consumes.
        """
        # [FREEZE_ONCE_NOT_TWICE_V1]
        #
        # This froze the state with detach().clone() -- one pass over the
        # buffer -- and then _materialise serialised that clone with
        # tobytes() on first read, a second pass over the same data. Two
        # passes to serve one state.
        #
        # Serialising IS a freeze: tobytes() produces an independent buffer
        # that no later write to the tensor can reach. So do it once, here,
        # and the wire form and the snapshot are the same object.
        #
        # MATERIALISE_ON_DEMAND above is unaffected in what it protects
        # against: its point was that a state nobody reads should not pay
        # for the reader's representation. It still does not -- the cost is
        # the one pass publish was already paying to freeze. The digest,
        # which is the part that was genuinely extra, stays deferred.
        blob = tensor_bytes(tensor)
        with self._lock:
            self._current[name] = (int(generation), blob, incarnation)
            self._materialised.pop(name, None)
            self.states_offered += 1
        # [WE_OWN_BOTH_ENDS_OF_THIS_V1] Tell the waiters, rather than making
        # them look again.
        with self._pub_cv:
            self._pub_cv.notify_all()
        # [FIRST_NON_FINITE_V1] Measured on the tensor rather than on bytes
        # that no longer exist at this point.
        self.watch.observe(STAGE_PLANE_PUBLISH, step=generation, tensor=tensor,
                           name=name)
        diag("DIAG-TPLANE", "offer", name=name, gen=generation,
             incarnation=incarnation)
        return None

    def _materialise(self, name, need_digest: bool = True):
        """[A_DIGEST_IS_COMPUTED_WHEN_IT_IS_ASKED_FOR_V1]

        The digest was computed on every materialise, whether anyone wanted
        it or not. It answers two questions and neither is always asked: is
        the reader's copy still current (SAME), and did the bytes survive
        the wire (integrity). A reader doing neither paid for both.

        Measured here: crc32 is 0.21 ms/MB, so a 4 MB tensor costs 0.86 ms
        on the server and again on the client -- 1.7 ms a read, on a path
        where an entire loopback collective is 40 ms. Small on the mesh,
        not small on one box.

        The bytes are still cached; only the digest is deferred. A later
        request that DOES need one upgrades the cache entry in place rather
        than re-serialising.
        """
        """[MATERIALISE_ON_DEMAND_V1] Bytes and digest, made once per state.

        Cached against the identity it was made from, so repeated reads of
        one state do not re-serialise -- and so deferral does not simply move
        allocation churn from the publish path to the read path. Publishing a
        new state drops the cache entry, which frees the previous buffer for
        the allocator to hand straight back: the same-name replacement that
        measured 1.22 ms against 6.13 ms for accumulating fresh names.
        """
        with self._lock:
            cur = self._current.get(name)
            if cur is None:
                return None
            gen, blob, inc = cur
            hit = self._materialised.get(name)
            if hit is not None and hit[0] == gen and hit[3] == inc:
                if hit[2] is not None or not need_digest:
                    return hit
                # Cached bytes, digest deferred, and now someone wants it:
                # compute it once and upgrade the entry rather than
                # serialising the tensor again.
                t0 = time.perf_counter()
                out = (hit[0], hit[1], _digest(hit[1]), hit[3])
                self.materialise_s += time.perf_counter() - t0
                self._materialised[name] = out
                return out
            t0 = time.perf_counter()
            b = blob                      # already the wire form; see publish
            d = _digest(b) if need_digest else None
            self.materialise_s += time.perf_counter() - t0
            self.states_materialised += 1
            out = (gen, b, d, inc)
            self._materialised[name] = out
            diag("DIAG-TPLANE", "materialise", name=name, gen=gen,
                 bytes=len(b), digest=d)
            return out

    def _loop(self):
        sel = selectors.DefaultSelector()
        sel.register(self.sock, selectors.EVENT_READ, None)
        try:
            while not self._stop.is_set():
                for key, _ in sel.select(0.2):
                    if key.data is None:
                        try:
                            conn, peer = self.sock.accept()
                        except (BlockingIOError, OSError):
                            continue
                        conn.setblocking(True)
                        conn.settimeout(30.0)
                        # [FRAME_WITHOUT_COPYING_THE_PAYLOAD_V1] The reader
                        # sets this on its end and the server never did. A
                        # reply written as a header then a payload then waits
                        # on the peer's delayed ACK: a SAME reply measured
                        # 44 ms with it off and 0.03 ms with it on.
                        conn.setsockopt(socket.IPPROTO_TCP,
                                        socket.TCP_NODELAY, 1)
                        threading.Thread(target=self._serve, args=(conn, peer),
                                         daemon=True).start()
        finally:
            sel.close()

    def _serve(self, conn, peer):
        """One permanent connection from one reader."""
        who = "%s:%s" % peer
        try:
            while not self._stop.is_set():
                req = json.loads(bytes(_recv_frame(conn)).decode())
                name = req.get("name")
                want_gen = req.get("gen")
                # [A_STATE_IS_WHAT_IT_SAYS_IT_IS_V1] The identity the consumer
                # selected. Digest, because a generation is only unique per
                # producer while a digest names the bytes.
                want_digest = req.get("want")
                want_inc = req.get("inc")
                mode = req.get("mode", READ_BY_IDENTITY)
                have = req.get("have")
                reader = req.get("from", who)

                # [THE_LOCK_PROTECTS_THE_CELL_NOT_THE_WIRE_V1] The lock used
                # to be held across the whole send. The premise of the pull
                # model is N readers proceeding independently; holding a
                # server-wide lock for the duration of a 16 MB transmission
                # turns them into a queue, and the slowest reader's link sets
                # the rate for everyone. What the lock actually protects is
                # the _current dict, which publish() rewrites. Take the
                # triple out under the lock and send outside it: `blob` is an
                # immutable bytes and publish() REPLACES the entry rather
                # than mutating it, so a send in flight keeps sending the
                # generation it announced. That is the correct answer anyway
                # -- the reply must describe the bytes it carries, not a
                # generation that arrived halfway through.
                # [MATERIALISE_ON_DEMAND_V1] A request IS the demand. Bytes
                # and digest are made here if they have not been made yet,
                # and cached against this state's identity so a second reader
                # pays nothing. The saving is on states nobody asks for.
                # [READ_WAIT_V1] A read that waits for the state it wants.
                #
                # A reader that knows where a peer is does not need the
                # control plane to tell it the peer has published: it can
                # ask the peer and wait. That removes a write and a store
                # read from every collective -- the only per-round fact in
                # the descriptor was "rank r reached generation g", and this
                # read establishes that by returning.
                #
                # Bounded by the requester, answered by the holder. Nothing
                # is registered and nobody is remembered: a waiter that dies
                # is a closed socket, which is what makes this a read and
                # not a subscription.
                wait_s = float(req.get("wait_s") or 0.0)
                if wait_s > 0:
                    # [WE_OWN_BOTH_ENDS_OF_THIS_V1] Waiting for a LOCAL
                    # publish is not a network question and must not be
                    # polled. This napped on a dict -- 0.2 to 4 ms a turn --
                    # and showed up as 21% of a collective spent in
                    # time.sleep, the largest single entry in the profile.
                    #
                    # publish() notifies. A server thread waiting for a
                    # generation blocks on a condition and wakes when it
                    # exists, so the only thing between a producer's publish
                    # and a waiting reader's frame is the kernel.
                    # [A_WAIT_CAP_MUST_FOLLOW_THE_DEADLINE_V1] The server
                    # capped every wait at 30 s regardless of what the reader
                    # asked for, so a reader with a 300 s deadline was told
                    # the state was gone at 30. The cap belongs to the
                    # server as a ceiling on how long ONE connection may be
                    # held, not as a second opinion about the caller's
                    # deadline -- so it is generous and configurable, and the
                    # caller's own value wins below it.
                    _dl = time.time() + min(wait_s, MAX_SERVER_WAIT_S)
                    while True:
                        _c = self._current.get(name)
                        if _c is not None and (want_gen is None
                                               or int(_c[0]) >= int(want_gen)):
                            break
                        _left = _dl - time.time()
                        if _left <= 0:
                            break
                        with self._pub_cv:
                            self._pub_cv.wait(min(_left, 0.05))
                # A digest is needed only if the reader is comparing
                # against one (SAME), or asked for one to verify with.
                cur = self._materialise(
                    name,
                    need_digest=(want_digest is not None
                                 or have is not None
                                 or bool(req.get("verify", True))))
                with self._lock:
                    if cur is not None:
                        gen, blob, digest, inc = cur
                        self.bytes_offered += len(blob)
                        is_same = (have == digest)
                        if is_same:
                            self.same_replies += 1
                        else:
                            self.data_replies += 1
                            self.bytes_sent += len(blob)

                if cur is None:
                    _send_frame(conn, REPLY_GONE, json.dumps(
                        {"reason": GONE_NEVER_PUBLISHED}).encode())
                    continue
                # [A_STATE_IS_WHAT_IT_SAYS_IT_IS_V1] The request has always
                # carried the generation it wants and this server never read
                # it: it answered from _current[name] whatever that had
                # become, so a consumer that selected 42 and asked one moment
                # too late received 43's bytes under 42's name. The digest
                # check cannot catch that -- it compares the blob to the
                # digest in the same reply, so both ends agree perfectly
                # about the wrong tensor. Answer GONE instead, and say that
                # it was superseded so the caller can re-select if its policy
                # allows. Requests that name no identity (want is None) are
                # asking for whatever is current and are served as before.
                # [AN_INCARNATION_IS_NOT_A_NAME_V1] Checked before the
                # generation, because a restarted worker reuses generations:
                # (w3, incarnation-A, 42) and (w3, incarnation-B, 42) are
                # different states, and matching on generation alone would
                # hand back the wrong one with everything else agreeing.
                if mode == READ_BY_IDENTITY and want_inc is not None \
                        and inc is not None and want_inc != inc:
                    _send_frame(conn, REPLY_GONE, json.dumps(
                        {"reason": GONE_SUPERSEDED, "gen": gen,
                         "digest": digest, "inc": inc}).encode())
                    continue
                if mode == READ_BY_IDENTITY and want_digest is not None \
                        and want_digest != digest:
                    _send_frame(conn, REPLY_GONE, json.dumps(
                        {"reason": GONE_SUPERSEDED, "gen": gen,
                         "digest": digest, "inc": inc}).encode())
                    continue
                if mode == READ_BY_IDENTITY and want_gen is not None \
                        and int(want_gen) != int(gen):
                    _send_frame(conn, REPLY_GONE, json.dumps(
                        {"reason": GONE_SUPERSEDED, "gen": gen,
                         "digest": digest}).encode())
                    continue
                # [THE_SERVER_DOES_NOT_MODEL_THE_READER_V1] The ONLY test.
                # What the reader says it holds against what is current.
                if is_same:
                    _send_frame(conn, REPLY_SAME,
                                json.dumps({"gen": gen, "digest": digest,
                                            "inc": inc}).encode())
                    continue
                head = json.dumps({"gen": gen, "digest": digest,
                                   "inc": inc}).encode()
                _send_frame(conn, REPLY_DATA,
                            struct.pack(BULK_LEN_FMT, len(head)), head, blob)
        except (ConnectionError, OSError, ValueError) as e:
            # A reader that went away, or a half-written frame. Expected.
            diag("DIAG-TPLANE", "reader gone", peer=who, err=repr(e)[:200])
        except BaseException as e:
            # [A_SERVER_THAT_DIES_QUIETLY_IS_WORSE_THAN_ONE_THAT_CRASHES_V1]
            # Anything else is a fault in THIS code, and it used to be
            # swallowed by the tuple above catching nothing and the thread
            # simply ending. The reader then saw "peer closed after 0 of 8
            # bytes" on every request, forever, with no trace anywhere -- one
            # run lost an entire arm to that: 5,884 unreachable reads, zero
            # bytes moved, and nothing in any log naming a cause.
            #
            # Never silent again. The reader still fails, because it must,
            # but the server says what happened and counts it.
            self.serve_faults += 1
            self.last_fault = "%s: %s" % (type(e).__name__, e)
            try:
                import traceback
                self.last_fault_tb = traceback.format_exc()
            except Exception:
                self.last_fault_tb = ""
            sys.stderr.write("[TPLANE] TENSOR SERVER FAULT serving %s: %s\n%s\n"
                             % (who, self.last_fault, self.last_fault_tb))
            sys.stderr.flush()
            diag("DIAG-TPLANE", "server fault", peer=who,
                 err=self.last_fault, tb=self.last_fault_tb)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            off, sent = self.bytes_offered, self.bytes_sent
            return {"port": self.port, "bytes_offered": off,
                    "bytes_sent": sent,
                    "same_replies": self.same_replies,
                    "data_replies": self.data_replies,
                    "reduction": (off / sent) if sent else None}

    def close(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


class TensorClient:
    """Permanent links out to peers' servers, one per peer.

    [THE_LINK_IS_PERMANENT_V1] The link is opened once and reused. It also
    caches the last tensor received per (peer, name), which is what makes a
    SAME reply usable rather than merely small.
    """

    def __init__(self, me: str, connect_timeout: float = 10.0, watch=None):
        #: [FIRST_NON_FINITE_V1] The reader side of the transport boundary.
        # [THE_INSTRUMENT_IS_NOT_THE_INSTRUMENTED_V1] see TensorServer. The
        # reader's half of the scan was the larger one -- it ran on every
        # tensor received, not only on those published.
        self.watch = watch if watch is not None else _NoWatch()
        self.me = me
        self._links: Dict[str, socket.socket] = {}
        self._cache: Dict[Tuple[str, str], Tuple[str, Any]] = {}
        self._lock = threading.RLock()
        self.connect_timeout = connect_timeout
        self.reads = 0
        self.same_hits = 0
        self.bytes_received = 0

    def _link(self, host: str, port: int, key: str) -> socket.socket:
        with self._lock:
            sk = self._links.get(key)
            if sk is not None:
                return sk
            sk = socket.create_connection((host, port),
                                          timeout=self.connect_timeout)
            sk.settimeout(60.0)
            sk.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._links[key] = sk
            diag("DIAG-TPLANE", "link opened", peer=key, host=host, port=port)
            return sk

    def _scratch(self, n: int) -> bytearray:
        """A receive buffer owned by this client, doubled as needed, never
        shrunk and never cleared. psychedelic keys clients by (peer, thread),
        so one client is used by one thread and this needs no lock."""
        b = getattr(self, "_scratch_buf", None)
        if b is None or len(b) < n:
            size = 1 << max(12, (max(n, 1) - 1).bit_length())
            b = self._scratch_buf = bytearray(size)
        return b

    def read_into(self, dst, worker: str, host: str, port: int, name: str,
                  generation: int, mode: str = READ_CURRENT,
                  wait_s: float = 0.0, verify: bool = True):
        """Read a state straight into `dst`. Allocates nothing per call.

        read() allocates a receive buffer, builds a tensor over it and
        returns that; the caller then copies it where it belongs. Two passes
        over the payload and one allocation per read. This reuses a scratch
        buffer and copies once, into the destination.

        Reusing the buffer is only safe because nothing aliasing it escapes:
        the copy into `dst` happens here, and the digest cache -- which
        stores the tensor, and would therefore store a view over a buffer
        about to be overwritten -- is not populated on this path. SAME
        suppression is unavailable here for that reason, which costs nothing
        for a caller whose content changes every round.
        """
        t, nbytes, same = self.read(
            worker, host, port, name, generation,
            [dst.numel()], str(dst.dtype), mode=mode, wait_s=wait_s,
            into=self._scratch, cache=False, verify=verify, _no_copy=True)
        # The only pass over the payload on this side.
        dst.reshape(-1).copy_(t.reshape(-1))
        return nbytes, same

    def read(self, worker: str, host: str, port: int, name: str,
              generation: int, shape, dtype,
              want_digest: Optional[str] = None,
              mode: str = READ_BY_IDENTITY,
              want_incarnation: Optional[str] = None,
              into=None, cache: bool = True, verify: bool = True,
              _no_copy: bool = False,
              wait_s: float = 0.0):
        """Read a peer's tensor. Returns (tensor, wire_bytes, was_same).

        [IT_IS_A_READ_NOT_A_FETCH_V1] Named for what it is."""
        key = "%s@%s:%d" % (worker, host, port)
        ck = (key, name)
        have = self._cache.get(ck, (None, None))[0]
        req = json.dumps({"name": name, "gen": generation, "mode": mode,
                          "wait_s": wait_s,
                          "want": want_digest, "inc": want_incarnation,
                          "verify": bool(verify or want_digest is not None
                                         or have is not None),
                          "have": have,
                          "from": self.me}).encode()

        for attempt in (1, 2):
            try:
                sk = self._link(host, port, key)
                _send_frame(sk, req)
                reply = _recv_frame(sk, into=into)
                break
            except (ConnectionError, OSError) as e:
                # [THE_LINK_IS_PERMANENT_V1] A broken link is dropped so the
                # next attempt builds a new one. Not a retry loop: one rebuild,
                # then the failure is the caller's.
                with self._lock:
                    old = self._links.pop(key, None)
                if old is not None:
                    try:
                        old.close()
                    except OSError:
                        pass
                diag_exc("DIAG-TPLANE", "link dropped", e, peer=key,
                         attempt=attempt)
                if attempt == 2:
                    raise
        self.reads += 1

        tag, rest = bytes(reply[:_TAG_LEN]), reply[_TAG_LEN:]
        if tag == REPLY_GONE:
            try:
                info = json.loads(bytes(rest).decode()) if len(rest) else {}
            except ValueError:
                info = {}
            reason = info.get("reason", GONE_NEVER_PUBLISHED)
            if reason == GONE_SUPERSEDED:
                msg = ("%s has moved past %r generation %s (%s); it now holds "
                       "generation %s (%s). The state you selected is not "
                       "available and the current one is NOT a substitute for "
                       "it." % (worker, name, generation, want_digest,
                                info.get("gen"), info.get("digest")))
            else:
                msg = ("%s does not publish %r at all. The descriptor in the "
                       "space is stale relative to its own data plane -- read "
                       "the space again." % (worker, name))
            raise StateGone(msg, reason, worker=worker, name=name,
                            want_gen=generation, want_digest=want_digest,
                            current_gen=info.get("gen"),
                            current_digest=info.get("digest"))
        if tag == REPLY_SAME:
            meta = json.loads(bytes(rest).decode())
            cached = self._cache.get(ck)
            if cached is None or cached[0] != meta["digest"]:
                # [THE_SERVER_DOES_NOT_MODEL_THE_READER_V1] With the server
                # answering only against the `have` this reader just sent,
                # this is now unreachable by any peer-state divergence -- it
                # can only mean the reply did not match the request that
                # produced it. Kept as an assertion, not a recovery.
                raise RuntimeError(
                    "%s replied SAME for %r digest %s but this reader sent "
                    "have=%r. The reply does not answer the request."
                    % (worker, name, meta["digest"], have))
            self.same_hits += 1
            self.bytes_received += len(reply)
            return cached[1], len(reply), True

        (hlen,) = struct.unpack(BULK_LEN_FMT,
                                bytes(rest[:BULK_LEN_LEN]))
        meta = json.loads(bytes(rest[BULK_LEN_LEN:BULK_LEN_LEN
                                     + hlen]).decode())
        # A view, not a copy: tensor_from and hashlib both accept one.
        blob = rest[BULK_LEN_LEN + hlen:]

        # [THE_DIGEST_WAS_CARRIED_AND_NEVER_CHECKED_V1] The server computes a
        # sha256 over exactly the bytes it sends and puts it in the reply. The
        # reader stored it to answer the next SAME and never compared it to
        # what arrived, so a corrupted or truncated payload became a tensor of
        # plausible shape and wrong values, indistinguishable from a training
        # result. Check it. A mismatch is not a peer that left and is not
        # recoverable by asking again -- it means the bytes are not the bytes.
        # [A_STATE_IS_WHAT_IT_SAYS_IT_IS_V1] Three checks, three different
        # things. Generation match catches producer ordering and bookkeeping
        # errors and makes a fault legible. Digest match proves this is the
        # immutable identity that was REQUESTED. The blob hash proves the
        # bytes survived the wire. Verifying the blob against the digest in
        # the same reply -- which is all this used to do -- proves only that
        # the reply is self-consistent.
        if mode == READ_BY_IDENTITY and want_incarnation is not None \
                and meta.get("inc") is not None \
                and meta.get("inc") != want_incarnation:
            raise StateGone(
                "%s answered DATA for %r from incarnation %s but %s was "
                "requested. Same worker, same generation, different run -- "
                "these are different states."
                % (worker, name, meta.get("inc"), want_incarnation),
                GONE_SUPERSEDED, worker=worker, name=name,
                want_gen=generation, want_digest=want_digest,
                current_gen=meta.get("gen"), current_digest=meta.get("digest"))
        if mode == READ_BY_IDENTITY and want_digest is not None \
                and meta.get("digest") != want_digest:
            raise StateGone(
                "%s answered DATA for %r with digest %s but %s was requested. "
                "A reply that is internally consistent can still be the wrong "
                "state." % (worker, name, meta.get("digest"), want_digest),
                GONE_SUPERSEDED, worker=worker, name=name,
                want_gen=generation, want_digest=want_digest,
                current_gen=meta.get("gen"), current_digest=meta.get("digest"))
        if mode == READ_BY_IDENTITY and meta.get("gen") is not None \
                and int(meta["gen"]) != int(generation):
            raise StateGone(
                "%s answered DATA for %r at generation %s but generation %s "
                "was requested; the digest matched, so this is a producer "
                "bookkeeping error rather than a substitution."
                % (worker, name, meta.get("gen"), generation),
                GONE_SUPERSEDED, worker=worker, name=name,
                want_gen=generation, want_digest=want_digest,
                current_gen=meta.get("gen"), current_digest=meta.get("digest"))
        # [A_DIGEST_IS_COMPUTED_WHEN_IT_IS_ASKED_FOR_V1] The reader pays for
        # this check too, and it is the reader's to decline. Declining it
        # means a corrupted payload becomes a wrong answer rather than an
        # error, so it is opt-out and not the default: only a caller that
        # knows what it is trading should pass verify=False.
        if not verify and want_digest is None and have is None:
            got = None
        else:
            got = _digest(blob)
        if got is not None and got != meta.get("digest"):
            raise RuntimeError(
                "%s sent %r gen %s: %d payload bytes digest %s, reply claims "
                "%s. The tensor on the wire is not the tensor that was "
                "published."
                % (worker, name, meta.get("gen"), len(blob), got,
                   meta.get("digest")))

        # A view is only safe where the caller consumes it before the
        # buffer can be written again. read_into is the one place that holds.
        t = (tensor_view(blob, shape, dtype) if _no_copy
             else tensor_from(blob, shape, dtype))
        # [FIRST_NON_FINITE_V1] The tensor as this reader now holds it. The
        # digest above proves the BYTES are intact, so a non-finite value here
        # with a finite plane.publish for the same digest is a defect in the
        # dtype/shape interpretation rather than in the transfer.
        self.watch.observe(STAGE_PLANE_RECV, step=meta.get("gen"), tensor=t,
                           name=name, peer=worker, digest=got,
                           bytes=len(blob), shape=list(shape), dtype=dtype)
        # [A_CACHE_OF_VIEWS_CANNOT_OUTLIVE_ITS_BUFFER_V1] `t` is a view over
        # the receive buffer. That is fine while every read allocates its
        # own, and fatal once one is reused: the next read rewrites the
        # bytes a cached tensor points at. So a scratch read does not cache,
        # and the two are refused together rather than left to whoever
        # combines them.
        if into is not None and cache:
            raise ValueError(
                "read(into=...) reuses a buffer and read(cache=True) stores a "
                "view over it. Pass cache=False, or do not pass into.")
        if cache:
            with self._lock:
                self._cache[ck] = (meta["digest"], t)
        self.bytes_received += len(reply)
        # [TWO_READS_NOT_ONE_PRETENDING_V1] The identity actually served, so a
        # CURRENT reader books what it got instead of what it guessed.
        self.last_identity = (meta.get("gen"), meta.get("digest"),
                              meta.get("inc"))
        return t, len(reply), False

    def stats(self) -> Dict[str, Any]:
        return {"reads": self.reads, "same_hits": self.same_hits,
                "bytes_received": self.bytes_received,
                "same_ratio": (self.same_hits / self.reads)
                if self.reads else None,
                "links": len(self._links)}

    def close(self):
        with self._lock:
            for sk in self._links.values():
                try:
                    sk.close()
                except OSError:
                    pass
            self._links.clear()
