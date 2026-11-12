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

#: [DIAG_PSY_STALL_V1] Report a serving wait that outlives this many seconds.
_PLANE_DIAG = __import__("os").environ.get(
    "FROGNET_PLANE_DIAG", "1") not in ("0", "no")
_PLANE_STALL_S = float(__import__("os").environ.get(
    "FROGNET_PLANE_STALL_S", "20"))
import time
from typing import Any, Dict, Optional, Tuple

from agent_workload.aiconnect.service import (BULK_LEN_FMT, BULK_LEN_LEN,
                                              bulk_frame)

from .finite import STAGE_PLANE_PUBLISH, STAGE_PLANE_RECV, FiniteWatch

# [IMPORT_GUARD_V2] 2026-09-25: diag is first-party and the tree is always on the path; a stub hid a broken install.
from core.frognet_diag import diag, diag_exc

REPLY_SAME = b"SAME"
REPLY_DATA = b"DATA"
REPLY_GONE = b"GONE"

#: [A_STATE_IS_WHAT_IT_SAYS_IT_IS_V1] Why a state could not be served. A
#: policy has to tell these apart: SUPERSEDED means the producer moved on and
#: a newer state exists to re-select; NEVER_PUBLISHED means there is nothing
#: there and re-selecting will not help.
GONE_NEVER_PUBLISHED = "never_published"
GONE_SUPERSEDED = "superseded"
#: [A_WAIT_THAT_EXPIRED_IS_NOT_AN_ANSWER_V1] The reader asked to wait for a
#: generation and the producer never reached it. Distinct from superseded:
#: the state is not behind the request, the PRODUCER is. Retrying is the
#: right response to this one and is wrong for the other two.
GONE_WAIT_EXPIRED = "wait_expired"

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


def _recv_exact(sk: socket.socket, n: int, into=None, mark=None) -> memoryview:
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
            # [SAY_WHICH_BUFFER_V1 - John 2026-09-14] "holds X, needs Y" does
            # not say WHOSE buffer, so the same message fits a provider that
            # failed to grow and a caller that handed in a fixed one. Those are
            # different bugs. Name the provider, the key it was built from, and
            # whether it was even growable.
            raise ValueError(
                "scratch buffer holds %d, frame needs %d "
                "(into=%s growable=%s key=%s)"
                % (len(buf), n,
                   type(into).__name__,
                   callable(into),
                   getattr(into, "__defaults__", None)))
        mv = memoryview(buf)[:n]
    else:
        buf = bytearray(n)
        mv = memoryview(buf)
    got = 0
    while got < n:
        k = sk.recv_into(mv[got:], n - got)
        # [WAITING_IS_NOT_TRANSFERRING_V1] The instant the first byte
        # arrives. Everything before it is the peer deciding it has an
        # answer -- it had not published yet, or the request was queued
        # behind others. Everything after is the pipe. One number for both
        # cannot tell a slow link from a late peer, and every diagnosis in
        # this project has turned on that distinction.
        if mark is not None and not mark:
            mark.append(time.perf_counter())
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

#: How long to wait for a TCP connection to complete. Must exceed the
#: kernel's SYN retransmission schedule (~1s, 3s, 7s, 15s) or a single lost
#: SYN fails the connect while TCP is still trying.
CONNECT_TIMEOUT_S = float(os.environ.get("FROGNET_PLANE_CONNECT_TIMEOUT_S",
                                         "60"))

#: A link's socket deadline when no wait has been requested.
DEFAULT_SOCKET_TIMEOUT_S = float(
    os.environ.get("FROGNET_PLANE_SOCKET_TIMEOUT_S", "60"))

#: Added to a requested wait to get the socket deadline: the server may take
#: the whole wait and then still has to send the answer.
SOCKET_MARGIN_S = float(os.environ.get("FROGNET_PLANE_SOCKET_MARGIN_S", "60"))

#: [A_LINK_IS_CLOSED_WHEN_IT_IS_DONE_NOT_WHEN_IT_IS_QUIET_V1]
#:
#: How long a served connection may sit idle before the server closes it.
#: 0 means never, and never is the right answer here.
#:
#: This was 30 s, then 600. Both are guesses at "how long might a peer
#: legitimately pause", and both were wrong in the same direction: 30
#: closed links between collectives at world 4, and 600 is simply a longer
#: gap away from doing the same. There is no interval that is safely
#: shorter than "the peer might still want this", because a peer is idle
#: exactly as long as its own work takes -- a straggler, a big barrier, a
#: slow uplink -- and none of those mean it is gone.
#:
#: A link is closed when the peer closes it, which the reader does on
#: shutdown, or when the process ends. An idle connection costs a socket
#: and a parked thread; a wrongly-closed one costs a reconnect in the
#: middle of a collective and, when it lands badly, the run.
#:
#: Set a number of seconds if a plane ever needs to reclaim threads from
#: peers that vanished without closing.
IDLE_REAP_S = float(os.environ.get("FROGNET_PLANE_IDLE_REAP_S", "0"))


def _recv_frame(sk: socket.socket, into=None, mark=None) -> memoryview:
    (n,) = struct.unpack(BULK_LEN_FMT,
                         bytes(_recv_exact(sk, BULK_LEN_LEN, mark=mark)))
    if n < 0 or n > MAX_FRAME:
        try:
            who = sk.getpeername()
        except OSError:
            who = "?"
        raise ConnectionError(
            "frame length %d from %s is not a frame: this socket is out of "
            "step with its peer, which happens when two readers share it or "
            "one is abandoned mid-frame. Not a memory problem." % (n, who))
    return _recv_exact(sk, n, into=into, mark=mark)


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


def tensor_view_bytes(t):
    """[POINT_AT_THE_BYTES_DO_NOT_COPY_THEM_V1] The tensor's own storage,
    as a memoryview. No copy.

    tensor_bytes() exists to FREEZE a state -- a copy nothing can change
    underneath the name it was advertised under. That is the right default
    and it costs a full pass over the payload.

    A caller that can prove the range will not change while a reader might
    want it does not need the freeze, and for those the copy is pure waste.
    The sharded all-reduce is exactly that case: range k of a rank's tensor
    is read by exactly one peer, peer k, and is overwritten by exactly one
    thing, peer k's reduced slice arriving. Peer k cannot produce that
    slice without having first read this one, so the arrival that
    overwrites the range is itself the receipt that the range was taken.
    The algorithm's own dependencies do the work an acknowledgement would.

    Returns a memoryview over live storage. If the caller is wrong about
    the lifetime, a reader gets whatever is there now -- which the digest
    check catches, but only after the bytes have gone.
    """
    t = t.detach().to("cpu").contiguous()
    import torch as _t
    if t.dtype == _t.bfloat16:
        return memoryview(t.view(_t.int16).numpy()).cast("B")
    return memoryview(t.numpy()).cast("B")


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


# [ALL_IN_THE_REGION_V1] TensorServer -- a rank serving its own tensors from its own process, waiting on its own
# condition in 50 ms slices -- is gone. Tensors live in the machine's FrogTorch RAM (frogtorch_ram.py), published
# through frogtorch_ram_client.RegionPlane; TensorClient reads them there with the same SAME/DATA/GONE contract.


class TensorClient:
    """Permanent links out to peers' servers, one per peer.

    [THE_LINK_IS_PERMANENT_V1] The link is opened once and reused. It also
    caches the last tensor received per (peer, name), which is what makes a
    SAME reply usable rather than merely small.
    """

    def __init__(self, me: str, connect_timeout: float = None, watch=None):
        #: [FIRST_NON_FINITE_V1] The reader side of the transport boundary.
        # [THE_INSTRUMENT_IS_NOT_THE_INSTRUMENTED_V1] see TensorServer. The
        # reader's half of the scan was the larger one -- it ran on every
        # tensor received, not only on those published.
        self.watch = watch if watch is not None else _NoWatch()
        self.me = me
        self._links: Dict[str, socket.socket] = {}
        self._cache: Dict[Tuple[str, str], Tuple[str, Any]] = {}
        self._lock = threading.RLock()
        self._keylocks = {}
        #: Per-read timing. Capped so a long run cannot grow it without
        #: bound; when it fills, the older half goes.
        self.samples = []
        self.SAMPLE_CAP = 20000
        # [A_CONNECT_MUST_OUTLAST_TCP_S_OWN_RETRIES_V1]
        #
        # 10 s was shorter than the stack's own SYN retransmission schedule.
        # Linux retries a lost SYN at roughly 1 s, 3 s, 7 s, 15 s: one lost
        # SYN and one lost retry already put the next attempt past 10 s, so
        # the connect gave up while TCP was still trying. On a
        # transcontinental tunnel that is an ordinary event, and it
        # presented as "the host did not answer" -- indistinguishable from
        # a firewall, which is what it got blamed on.
        #
        # 60 s covers the first four attempts with room to spare. A peer
        # that is genuinely absent still fails; it just takes longer to say
        # so, and being slow to declare a peer dead is the right bias for a
        # transport whose whole point is tolerating slow peers.
        self.connect_timeout = (CONNECT_TIMEOUT_S if connect_timeout is None
                                else connect_timeout)
        self.reads = 0
        self.same_hits = 0
        self.bytes_received = 0

    @staticmethod
    def cache_key(host: str, port: int, channel: str, name: str):
        """[ONE_PLACE_KNOWS_THE_KEY_V1] The digest cache's key, in one place.

        Callers that want SAME suppression have to seed this cache, which
        means they have to build its key -- and psychedelic_backend built it
        as "%s@%s:%d" % (peer, host, port) while read() looked it up as
        "%s:%d#%s" % (host, port, channel). Two formats for one key: every
        seeded digest went into a slot nothing read, and every reply was
        DATA even when the bytes were identical.

        That is why same_replies was 0 in every measured run. It was read as
        evidence that gradients never repeat. It was evidence that the
        question was never asked.
        """
        return ("%s:%d#%s" % (host, port, channel), name)

    def _keylock(self, key: str):
        """[ONE_SOCKET_PER_PEER_ONE_READER_AT_A_TIME_V1]

        A link carries framed request/reply pairs, so two threads using one
        socket interleave their frames and the next length header is read
        out of the middle of the other's payload.

        The first fix for that was a socket per (peer, THREAD). It works,
        and it multiplies sockets by the pool size: at world 4 that is 3
        peers x 4 threads = 12 links per rank, each used a quarter as often.
        The server reaps a connection idle for 30 s, so at large sizes the
        idle gaps cross that reaper and reads fail with "peer closed after
        0 of 8 bytes". It only shows at world 4 and only at the biggest
        size, which is exactly where it was found.

        A lock per peer is the smaller answer. Reads to DIFFERENT peers are
        what must overlap, and those are different links with different
        locks. Reads to the SAME peer were already sequential within a
        phase, so the lock costs nothing and the link stays warm.
        """
        with self._lock:
            lk = self._keylocks.get(key)
            if lk is None:
                lk = self._keylocks[key] = threading.Lock()
            return lk

    def _link(self, host: str, port: int, key: str) -> socket.socket:
        with self._lock:
            sk = self._links.get(key)
            if sk is not None:
                return sk
            try:
                sk = socket.create_connection((host, port),
                                              timeout=self.connect_timeout)
            except OSError as e:
                # [SAY_WHO_REFUSED_V1] The bare socket error names neither
                # the peer nor the address, so a ConnectionRefused deep in a
                # collective says only "something, somewhere, is not
                # listening" -- and three separate diagnoses of this failure
                # have been guesses about which node it was.
                #
                # ECONNREFUSED specifically means the host answered with a
                # RST: it is up, and nothing is bound to that port. That is
                # a process that has gone, not a network that is down --
                # worth distinguishing, because they have different causes
                # and the message should not make the reader choose.
                raise ConnectionError(
                    "%s: cannot reach %s's plane at %s:%d (%s). %s"
                    % (self.me, key, host, port, e.strerror or e,
                       "The host answered but nothing is listening on that "
                       "port -- that peer's process is gone, not its network."
                       if isinstance(e, ConnectionRefusedError) else
                       "The host did not answer within %.0fs. That is a "
                       "dropped SYN, OR a loaded peer whose accept queue "
                       "this connect waited out -- from here the two look "
                       "identical, and a busy machine is the commoner of "
                       "the two." % self.connect_timeout)
                ) from e
            # [THE_SOCKET_MUST_OUTLAST_THE_WAIT_IT_ASKED_FOR_V1]
            #
            # This was a fixed 60 s while the wait the caller asks the
            # SERVER to hold the request open is a variable. Ask for more
            # than 60 and the client's own socket gives up before the server
            # has answered:
            #
            #     k = sk.recv_into(mv[got:], n - got)
            #     TimeoutError: timed out
            #
            # which reads as a dead peer and is nothing of the sort. The two
            # deadlines belong to one exchange and must not be set in
            # different places by different rules. read() raises this per
            # request, below, to cover the wait it is about to request.
            sk.settimeout(DEFAULT_SOCKET_TIMEOUT_S)
            sk.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._links[key] = sk
            diag("DIAG-TPLANE", "link opened", peer=key, host=host, port=port)
            return sk

    def _scratch_for(self, key: str):
        """[A_BUFFER_BELONGS_TO_A_LINK_NOT_A_PEER_V1]

        One receive buffer per LINK, not per client.

        Channels gave each concurrent conversation with a peer its own
        socket and its own lock, and both kept using the client's single
        scratch buffer. Two reads to one peer on channels p1 and p2 then
        wrote into the same memory at the same time and clobbered each
        other mid-payload.

        The digest check is what caught it, on a real four-node run:

            r1 sent 'c1.ars.1.3' gen 1: 65536 payload bytes digest
            214e007fe77a46fb, reply claims b5680239b7293631.
            The tensor on the wire is not the tensor that was published.

        A link has a socket, a lock and a buffer. They are one thing and
        have to be keyed as one.

        Returns a PROVIDER rather than a buffer: the frame length is not
        known until the header has been read.
        """
        bufs = getattr(self, "_scratch_bufs", None)
        if bufs is None:
            bufs = self._scratch_bufs = {}

        def provider(n, _key=key, _bufs=bufs):
            b = _bufs.get(_key)
            if b is None or len(b) < n:
                b = _bufs[_key] = bytearray(
                    1 << max(12, (max(n, 1) - 1).bit_length()))
            return b
        return provider

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
                  wait_s: float = 0.0, verify: bool = True,
                  channel: str = "d", exact: bool = True):
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

        Returns (wire_bytes, was_same, elements) -- the element count is what
        ARRIVED, which is not `dst.numel()` when `exact` is False.

        [THE_DESTINATION_IS_NOT_THE_QUESTION_V1 - John 2026-09-14]

        `exact=True` (the default, and what every non-speculative caller
        wants) asserts the frame holds exactly `dst.numel()` elements: the
        caller knows the length, and a payload of any other size is a fault
        it wants to hear about.

        `exact=False` says the caller knows WHICH state it wants and not how
        big it is. That is the read-ahead's case exactly. The length was the
        only thing the speculative read had to guess, and it guessed from the
        previous round -- so with DDP's buckets alternating 262657 / 791552
        elements it was wrong on every round, and the guess was refused by
        this line rather than by anything on the wire:

            RuntimeError: shape '[1050625]' is invalid for input of size
            4216833

        Nothing in the protocol needs the length. The request carries a name,
        a generation and a mode; the frame carries its own size; `shape` is
        used on this side only, to interpret the bytes. So a reader that
        supplies a destination LARGE ENOUGH does not have to know the size at
        all, and the speculative read stops being a guess about anything.
        """
        _key = self.cache_key(host, port, channel, name)[0]
        t, nbytes, same = self._read_retry_wait(
            worker, host, port, name, generation,
            [dst.numel()] if exact else [-1], str(dst.dtype), mode=mode,
            wait_s=wait_s, into=self._scratch_for(_key),
            cache=False, verify=verify, _no_copy=True,
            channel=channel)
        flat = t.reshape(-1)
        n = flat.numel()
        if n > dst.numel():
            # [SAY_WHICH_BUFFER_V1] Not "shape is invalid": say which cell,
            # how many arrived and how many the destination holds, so a
            # too-small landing area is distinguishable from a wrong name.
            raise ValueError(
                "%s sent %r gen %s: %d elements, destination holds %d. The "
                "receiving buffer is too small for the state that was asked "
                "for -- this is a sizing fault on this side, not a bad frame."
                % (worker, name, generation, n, dst.numel()))
        # The only pass over the payload on this side.
        dst.reshape(-1)[:n].copy_(flat)
        return nbytes, same, n

    #: [RETRY_IS_NOT_A_FALLBACK_V1] A wait that expired may be asked
    #: again -- the producer is behind, not wrong, and asking again is
    #: asking the SAME question. What must never happen is being handed a
    #: different answer. Bounded, because a producer that has not arrived in
    #: this long is not late, it is gone.
    WAIT_RETRIES = int(os.environ.get("FROGNET_PLANE_WAIT_RETRIES", "3"))
    WAIT_RETRY_SLEEP_S = float(
        os.environ.get("FROGNET_PLANE_WAIT_RETRY_SLEEP_S", "5"))

    def _read_retry_wait(self, worker, host, port, name, generation,
                         shape, dtype, **kw):
        """[NO_FALLBACK_V1 - 2026-11-11] One held read. The region holds it until the owner writes or the caller's
        deadline passes; an expired wait is the answer, and it is raised, not retried after a sleep."""
        return self.read(worker, host, port, name, generation, shape, dtype, **kw)

    def read(self, worker: str, host: str, port: int, name: str,
              generation: int, shape, dtype, channel: str = "d",
              want_digest: Optional[str] = None,
              mode: str = READ_BY_IDENTITY,
              want_incarnation: Optional[str] = None,
              into=None, cache: bool = True, verify: bool = True,
              _no_copy: bool = False,
              wait_s: float = 0.0):
        """Read a peer's tensor. Returns (tensor, wire_bytes, was_same).

        [IT_IS_A_READ_NOT_A_FETCH_V1] Named for what it is."""
        # [AN_ADDRESS_IS_THE_IDENTITY_OF_A_LINK_V1]
        #
        # This was keyed "%s@%s:%d" % (worker, host, port). `worker` is a
        # label -- "r2" -- invented for this call and carried through every
        # read. host:port already identifies the peer, so the label added
        # nothing to the key and could only do harm: two labels for one
        # address would open two links to the same peer.
        #
        # It stays as a prefix on messages, where a human reading a failure
        # wants to know which rank was talking. It is not identity.
        # [TWO_QUESTIONS_AT_ONCE_NEED_TWO_LINKS_V1]
        #
        # A link carries one request/reply at a time, under a lock. Fine
        # while a rank asks a peer one thing at a time -- and untrue the
        # moment both phases' reads are issued together: phase 2 takes the
        # lock and blocks for its full wait, phase 1 queues behind it, the
        # peer never receives this rank's contribution, and both sides wait
        # out the timeout.
        #
        # `channel` gives each concurrent conversation with a peer its own
        # link. Bounded and explicit -- one per phase, two per peer -- not
        # one per thread, which is the version that multiplied sockets until
        # the server's idle reaper started closing them.
        ck = self.cache_key(host, port, channel, name)
        key = ck[0]
        have = self._cache.get(ck, (None, None))[0]
        req = json.dumps({"name": name, "gen": generation, "mode": mode,
                          "wait_s": wait_s,
                          "want": want_digest, "inc": want_incarnation,
                          "verify": bool(verify or want_digest is not None
                                         or have is not None),
                          "have": have,
                          "from": self.me}).encode()

        _kl = self._keylock(key)
        _mark, _t0, _t2 = [], 0.0, 0.0
        for attempt in (1, 2):
            try:
                with _kl:
                    sk = self._link(host, port, key)
                    # The socket must outlast the wait this request asks the
                    # server to hold, plus time to send the answer back. A
                    # slow link makes that second part matter.
                    sk.settimeout(max(DEFAULT_SOCKET_TIMEOUT_S,
                                      wait_s + SOCKET_MARGIN_S))
                    _t0 = time.perf_counter()
                    _send_frame(sk, req)
                    del _mark[:]
                    reply = _recv_frame(sk, into=into, mark=_mark)
                    _t2 = time.perf_counter()
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
        # [WAITING_IS_NOT_TRANSFERRING_V1] One sample per read: how long the
        # peer took to start answering, how long the bytes took once they
        # started, and how many there were. Effective bandwidth per link
        # falls straight out of the last two, and a late peer is no longer
        # indistinguishable from a slow one.
        if _t2 and _mark:
            self.samples.append({
                "peer": key, "name": name, "bytes": len(reply),
                "wait_ms": (_mark[0] - _t0) * 1000.0,
                "xfer_ms": (_t2 - _mark[0]) * 1000.0,
                "t": time.time()})
            if len(self.samples) > self.SAMPLE_CAP:
                del self.samples[:len(self.samples) // 2]

        tag, rest = bytes(reply[:_TAG_LEN]), reply[_TAG_LEN:]
        if tag == REPLY_GONE:
            try:
                info = json.loads(bytes(rest).decode()) if len(rest) else {}
            except ValueError:
                info = {}
            reason = info.get("reason", GONE_NEVER_PUBLISHED)
            if reason == GONE_WAIT_EXPIRED:
                _at = info.get("gen")
                msg = ("%s never reached %r generation %s after waiting "
                       "%.1fs; %s. The read is refused rather than served a "
                       "different generation, because a substitution you did "
                       "not ask for and cannot see is worse than a failure."
                       % (worker, name, generation,
                          float(info.get("waited_s") or 0.0),
                          "it has not published that cell at all yet"
                          if _at is None else
                          "it is still at generation %s" % _at))
            elif reason == GONE_SUPERSEDED:
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
