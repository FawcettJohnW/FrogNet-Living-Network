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


# ---------------------------------------------------------------------------
# [STREAM_V1 - 2026-11-12] One permanent socket per (owner, reader). The
# reader subscribes ONCE; from then on the owner pushes every matching cell
# the moment it is published, in publish order, with no per-round request and
# no acknowledgement. Records on the wire are FNWP's data record:
#
#     control frame : [len:8][json {name, gen, inc, n, id, t_pub, t_q, ...}]
#     data records  : [len:4 BE][id:4][total:4][offset:4][bytes] ...
#
# SEG is a time/syscall tradeoff, not an MTU: TCP does the MTU. One sendmsg
# per record, header and payload gathered, nothing copied.
# ---------------------------------------------------------------------------
STREAM_SEG = int(os.environ.get("FROGNET_STREAM_SEG", str(256 << 10)))
STREAM_DIAG = os.environ.get("FROGNET_STREAM_DIAG", "1") not in ("0", "no")
# A cell whose send or landing takes longer than this is logged even with
# per-cell diagnostics off.
STREAM_SLOW_MS = float(os.environ.get("FROGNET_STREAM_SLOW_MS", "2000"))
_REC = struct.Struct(">IIII")
# [ONE_LINK_MANY_CELLS_V1 - 2026-11-12] Every record on a link names the
# delivery it belongs to, so the owner can interleave every cell that is ready
# -- the proxy's one-session-many-requests shape, with FNWP's round-robin of
# segments across payloads -- and a 2.5 MB cell no longer holds an 840 KB one
# behind it. kind 'H' carries the delivery's JSON header, kind 'D' its bytes.
#     [kind:1][id:4][total:4][offset:4][len:4][body]
_REC2 = struct.Struct(">cIIII")
# [SHORT_QUEUES_ON_THE_DATA_LINK_V1 - 2026-11-12] Congestion control for the
# sending side of every data link. ddp5434 measured the default (loss-based)
# control at 1.3-2.2 MB/s per WAN link with 400-1150 retransmits, and on the
# Seattle3B->Seattle7 path (relayed by Seattle5 on wlan1) rtt inflated to
# 1.6 s, rto to 15 s, and single records blocked 13-19 s. BBR paces to the
# measured bottleneck instead of filling the queue until it drops.
STREAM_CC = os.environ.get("FROGNET_STREAM_CC", "bbr")


def set_cc(sk) -> str:
    """Put STREAM_CC on this socket and return what the kernel actually uses,
    so the log says it rather than assuming it."""
    err = ""
    if STREAM_CC:
        try:
            sk.setsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION,
                          STREAM_CC.encode())
        except OSError as e:
            err = " (asked for %s: %s)" % (STREAM_CC, e.strerror or e)
    try:
        cur = sk.getsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, 16)
        cur = cur.split(b"\0", 1)[0].decode()
    except OSError:
        cur = "?"
    return cur + err


def _sdiag(line: str) -> None:
    sys.stderr.write("[DIAG-STREAM] " + line + "\n")
    sys.stderr.flush()


def tcp_info(sk) -> Dict[str, int]:
    """The kernel's view of the link: is it retransmitting, what is the RTT,
    how big is the congestion window. Linux only; {} elsewhere."""
    try:
        raw = sk.getsockopt(socket.IPPROTO_TCP, getattr(socket, "TCP_INFO", 11), 104)
        if len(raw) < 104:
            return {}
        u = struct.unpack("=8B24I", raw[:104])
        f = u[8:]
        return {"rto_us": f[0], "unacked": f[4], "lost": f[6], "retrans": f[7],
                "rtt_us": f[15], "rttvar_us": f[16], "ssthresh": f[17],
                "cwnd": f[18], "rcv_rtt_us": f[21], "rcv_space": f[22],
                "total_retrans": f[23]}
    except OSError:
        return {}


def _ti(sk) -> str:
    d = tcp_info(sk)
    return " ".join("%s=%s" % kv for kv in d.items()) if d else "tcp_info=-"


def _sendmsg_all(sk, parts) -> None:
    """sendmsg until everything is out. A partial send is normal on a full
    window; it is not an error."""
    bufs = [memoryview(p).cast("B") for p in parts]
    while bufs:
        k = sk.sendmsg(bufs)
        while k and bufs:
            if k >= len(bufs[0]):
                k -= len(bufs[0])
                bufs.pop(0)
            else:
                bufs[0] = bufs[0][k:]
                k = 0


def writable_bytes(t):
    """A writable byte view of a contiguous CPU tensor's own storage, so a
    socket can receive straight into it."""
    import torch as _t
    if not t.is_contiguous():
        raise ValueError("stream destination must be contiguous")
    if t.dtype == _t.bfloat16:
        return memoryview(t.view(_t.int16).numpy()).cast("B")
    return memoryview(t.numpy()).cast("B")


# Defaults are r8's link shape: one flow per peer, no unsent cap, records of at
# least 256 KB. r9 and r10, which defaulted to 3 flows, a 64 KB unsent cap and
# 20 ms records, ran about 2x slower to warmup on the 5-node mesh (ddp5761,
# ddp3499: 271 s against r7's 137 s). The mechanisms stay, behind these knobs.
STREAM_SETS = max(1, int(os.environ.get("FROGNET_STREAM_SETS", "1")))
# [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1 - from dataplane.hpp / fnav.py] One
# record owns its socket for at most this long: records are sized from the
# rate that socket is measured to achieve, times this.
STREAM_CEIL_S = float(os.environ.get("FROGNET_STREAM_CEIL_MS", "20")) / 1000.0
STREAM_SEG_MIN = int(os.environ.get("FROGNET_STREAM_SEG_MIN", str(256 << 10)))
STREAM_SEG_MAX = int(os.environ.get("FROGNET_STREAM_SEG_MAX", str(1 << 20)))
# [THE_BUFFER_IS_THE_LATENCY_V1 - from frogram.cpp / ram_host.cpp] Ribbit caps
# SO_SNDBUF at 16-32 KB. On a 100-150 ms WAN link that would cap the
# congestion window too (SO_SNDBUF holds unacknowledged bytes), so the cap is
# put on what the kernel holds UNSENT instead: TCP_NOTSENT_LOWAT. The queue in
# our own socket stays short, and the window still grows to the path.
STREAM_NOTSENT = int(os.environ.get("FROGNET_STREAM_NOTSENT", "0"))
_TCP_NOTSENT_LOWAT = getattr(socket, "TCP_NOTSENT_LOWAT", 25)


def _short_queue(sk) -> str:
    """Put the unsent-bytes cap on a data socket; return what the kernel took.
    FROGNET_STREAM_NOTSENT=0 leaves the socket as the kernel made it."""
    if STREAM_NOTSENT <= 0:
        return "off"
    try:
        sk.setsockopt(socket.IPPROTO_TCP, _TCP_NOTSENT_LOWAT, STREAM_NOTSENT)
        return str(sk.getsockopt(socket.IPPROTO_TCP, _TCP_NOTSENT_LOWAT))
    except OSError as e:
        return "refused(%s)" % (e.strerror or e)


# [LZ4_WHEN_IT_SHRINKS_V1] core/codec.py's _lz4_smart on the data link: a cell
# goes compressed only when that is smaller. Each cell family is probed and,
# if it does not shrink, left alone for LZ4_REPROBE cells before trying again,
# so incompressible data (dense float32 gradients measure 1.000) costs one
# compression in LZ4_REPROBE, not one per cell. A compressed cell travels as
# "<name>#z" with its original size, lands in a buffer, and is expanded into
# the registered destination on landing.
STREAM_LZ4 = os.environ.get("FROGNET_STREAM_LZ4", "1") not in ("0", "no")
LZ4_GAIN = float(os.environ.get("FROGNET_STREAM_LZ4_GAIN", "0.95"))
LZ4_REPROBE = int(os.environ.get("FROGNET_STREAM_LZ4_REPROBE", "64"))
_LZ4 = None


def _lz4():
    global _LZ4
    if _LZ4 is None:
        try:
            import lz4.block as _b
        except ImportError as e:
            raise RuntimeError(
                "FROGNET_STREAM_LZ4 is on and this python (%s) has no lz4 module: install "
                "python3-lz4, or set FROGNET_STREAM_LZ4=0" % sys.executable) from e
        _LZ4 = _b
    return _LZ4


class AdaptivePool:
    """[ADAPTIVE_POOL_V1] John's rule for every pool: a thread starts when one
    is needed and retires after IDLE_S idle; no fixed size, no cap at cores-1.
    Growth is triggered by wait time (starvation): a task that has sat queued
    for STARVE_S with no worker free gets a new thread. The first task never
    waits. Threads are daemons, so nothing here holds the interpreter at exit.
    submit() returns a concurrent.futures.Future."""

    def __init__(self, name: str, idle_s: float = None, starve_s: float = None, cap: int = 0):
        self.name = name
        self.idle_s = idle_s if idle_s is not None else float(os.environ.get("FROGNET_POOL_IDLE_S", "60"))
        self.starve_s = starve_s if starve_s is not None else float(os.environ.get("FROGNET_POOL_STARVE_MS", "2")) / 1000.0
        self.cap = cap
        self._cv = threading.Condition()
        self._q = []
        self._threads = 0
        self._idle = 0
        self._n = 0
        self._closed = False
        self.started = self.retired = self.peak = 0

    def submit(self, fn, *a, **k):
        from concurrent.futures import Future
        f = Future()
        with self._cv:
            if self._closed:
                raise RuntimeError("%s: pool is shut down" % self.name)
            self._q.append((f, fn, a, k, time.perf_counter()))
            if self._idle > len(self._q) - 1:
                self._cv.notify()
            elif self._threads == 0:
                self._start()
            else:
                threading.Timer(self.starve_s, self._starved).start()
        return f

    def _starved(self) -> None:
        with self._cv:
            if self._q and self._idle == 0 and not self._closed and \
                    time.perf_counter() - self._q[0][4] >= self.starve_s * 0.9 and \
                    (not self.cap or self._threads < self.cap):
                self._start()

    def _start(self) -> None:
        self._threads += 1
        self._n += 1
        self.started += 1
        self.peak = max(self.peak, self._threads)
        threading.Thread(target=self._work, daemon=True,
                         name="%s_%d" % (self.name, self._n)).start()

    def _work(self) -> None:
        while True:
            with self._cv:
                self._idle += 1
                t_idle = time.time()
                while not self._q and not self._closed:
                    left = self.idle_s - (time.time() - t_idle)
                    if left <= 0:
                        break
                    self._cv.wait(left)
                self._idle -= 1
                if not self._q:
                    self._threads -= 1
                    self.retired += 1
                    return
                f, fn, a, k, _t = self._q.pop(0)
            if not f.set_running_or_notify_cancel():
                continue
            try:
                f.set_result(fn(*a, **k))
            except BaseException as e:
                f.set_exception(e)

    def shutdown(self, wait: bool = False, cancel_futures: bool = False) -> None:
        with self._cv:
            self._closed = True
            if cancel_futures:
                for f, *_ in self._q:
                    f.cancel()
                self._q.clear()
            self._cv.notify_all()

    def stats(self) -> Dict[str, int]:
        return {"threads": self._threads, "idle": self._idle, "queued": len(self._q),
                "started": self.started, "retired": self.retired, "peak": self.peak}


# [SAME_DIFF_ON_THE_LINK_V1] The semantic engine's idea on the data link: a
# cell is differenced against the last cell of the SAME INSTANCE this link
# carried (fnwp_engine's INSTANCE_REFERENCES -- the family and the cell's
# size, never another instance that shares the template). Exact, lossless,
# in BLOCK-sized blocks: only blocks whose bytes changed go. No changed block
# is SAME. A family is probed (two cells kept and compared) and dropped for
# DIFF_REPROBE cells when more than DIFF_MAX_CHANGED of its blocks change --
# dense gradients change every block every step, so here it never fires;
# frozen layers, embeddings, parameters that did not move, and repeated
# control state are where it pays.
STREAM_DIFF = os.environ.get("FROGNET_STREAM_DIFF", "1") not in ("0", "no")
DIFF_BLOCK = int(os.environ.get("FROGNET_STREAM_DIFF_BLOCK", str(64 << 10)))
DIFF_MAX_CHANGED = float(os.environ.get("FROGNET_STREAM_DIFF_MAX_CHANGED", "0.5"))
DIFF_REPROBE = int(os.environ.get("FROGNET_STREAM_DIFF_REPROBE", "64"))


def _changed_blocks(new, old, bs: int):
    """Indices of the bs-byte blocks where `new` differs from `old` (same length)."""
    import numpy as _np
    a = _np.frombuffer(new, dtype=_np.uint8)
    b = _np.frombuffer(old, dtype=_np.uint8)
    n = len(a)
    full = n // bs
    idx = []
    if full:
        ne = (a[:full * bs].reshape(full, bs) != b[:full * bs].reshape(full, bs)).any(axis=1)
        idx = _np.nonzero(ne)[0].tolist()
    if n % bs and (a[full * bs:] != b[full * bs:]).any():
        idx.append(full)
    return idx, full + (1 if n % bs else 0)


def _family_key(name: str) -> str:
    import re as _re
    return _re.sub(r"\.\d+", "", name)


def _head_body(name: str, meta: Dict[str, Any]) -> bytes:
    """H record body: [name_len:2 BE][name][json]. The name is first so the
    native landing (frognet_link.cpp) can find the destination without
    parsing JSON."""
    nb = name.encode()
    return struct.pack(">H", len(nb)) + nb + json.dumps(meta).encode()


def _head_parse(body: bytes) -> Dict[str, Any]:
    (nl,) = struct.unpack(">H", body[:2])
    hdr = json.loads(body[2 + nl:].decode())
    hdr["name"] = body[2:2 + nl].decode()
    return hdr


def plane_view(buf, dtype, count: int):
    """A landed buffer as `count` elements of `dtype`, without a copy: a
    bytearray from the Python link or a uint8 tensor from the native one."""
    import torch as _t
    if isinstance(buf, _t.Tensor):
        return buf.view(dtype)[:count]
    return _t.frombuffer(buf, dtype=dtype, count=count)


def _addr(b) -> int:
    """Address of a buffer's first byte (bytes, bytearray or memoryview)."""
    import numpy as _np
    a = _np.frombuffer(b, dtype=_np.uint8)
    return int(a.ctypes.data)


# [NATIVE_LINK_V1] The byte-moving half of a link in C++ (frognet_link.cpp),
# built on first use like frognet_work.cpp. FROGNET_PLANE_NATIVE=1 selects it;
# a build that fails is then an error, not a quiet switch. OFF by default
# until one segfault seen in 1 of 11 local DDP runs (rank 0, before its first
# round, not reproduced since under the fault handler) is found: on loopback
# it measured no faster than the Python link (98-109 vs 96-107 ms/step).
PLANE_NATIVE = os.environ.get("FROGNET_PLANE_NATIVE", "0") not in ("0", "no")
_NATIVE_LINK = None
_NATIVE_LOCK = threading.Lock()
_NATIVE_KEEP = []          # Senders/Receivers live as long as the process: their threads are detached


def native_link():
    global _NATIVE_LINK
    with _NATIVE_LOCK:
        if _NATIVE_LINK is None:
            t0 = time.time()
            from torch.utils.cpp_extension import load
            src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frognet_link.cpp")
            os.makedirs(os.environ.get("FROGNET_LINK_BUILD_DIR",
                                       "/var/cache/frognet/frognet_link_build"), exist_ok=True)
            try:
                _NATIVE_LINK = load(name="frognet_link", sources=[src],
                                    extra_cflags=["-O2", "-std=c++20", "-DC10_NODEPRECATED"],
                                    build_directory=os.environ.get(
                                        "FROGNET_LINK_BUILD_DIR", "/var/cache/frognet/frognet_link_build"),
                                    verbose=False)
            except Exception as e:
                raise RuntimeError(
                    "frognet_link.cpp did not build on this machine (%s: %s). The native "
                    "link was asked for (FROGNET_PLANE_NATIVE=1); unset it to run the Python link."
                    % (type(e).__name__, str(e)[:400])) from e
            _sdiag("NATIVE %s loaded in %.1f s" % (_NATIVE_LINK.BUILD, time.time() - t0))
        return _NATIVE_LINK


class _Subscriber:
    """Owner side of one link: the reader's held reads (one-shot `wanted`,
    standing `family`), the cells that fired them, and one writer per channel
    set. [CHANNEL_SETS_V1] A link is STREAM_SETS TCP flows (proxy/
    channel_sets.py: each its own congestion window and stall domain). Every
    ready cell is cut into records; whichever flow is free takes the next
    record, round-robin across the cells in flight, so a flow that stalls
    holds only the record it has and the rest of the cell goes on the others."""

    def __init__(self, reader, who, sent_cb, family=(), token="", nsets=1):
        import re as _re
        self.reader, self.who, self.token, self.nsets = reader, who, token, nsets
        self.wanted: Dict[str, float] = {}
        self.family = list(family)
        self._rx = [_re.compile(f) for f in self.family]
        self.t_family = time.time()
        self._cv = threading.Condition()
        self._active = []            # deliveries in flight, in the order they became ready
        self._rr = 0
        self._id = 0
        self._sent_cb = sent_cb
        self.sent = self.bytes = self.requests = 0
        self.send_s = 0.0
        self.stop = threading.Event()
        self.socks: Dict[int, Any] = {}
        self._lz4_skip: Dict[str, int] = {}
        self._diff: Dict[str, Dict[str, Any]] = {}
        self.diff_ok = False          # the reader said it keeps instances (Python landing)
        self.diff_tries = self.diff_used = self.diff_saved = 0
        self.lz4_tries = self.lz4_used = self.lz4_saved = 0
        self.rate: Dict[int, float] = {}       # set -> bytes/s, EWMA
        self.set_bytes: Dict[int, int] = {}
        # [NATIVE_LINK_V1] The writers in C++: Python decides what fired,
        # frognet_link.cpp puts it on the flows.
        self._nat = None
        self._meta: Dict[int, Dict[str, Any]] = {}
        if PLANE_NATIVE:
            self._nat = native_link().Sender(self._on_native_done, STREAM_CEIL_S,
                                             STREAM_SEG_MIN, STREAM_SEG_MAX)
            _NATIVE_KEEP.append(self._nat)

    def wants(self, name: str) -> bool:
        return name in self.wanted or any(rx.fullmatch(name) for rx in self._rx)

    def fired(self, name: str):
        t = self.wanted.pop(name, None)
        if t is not None:
            return t
        if any(rx.fullmatch(name) for rx in self._rx):
            return self.t_family
        return None

    def _maybe_lz4(self, name, mv):
        """(compressed bytes, original size) when that is smaller, else None."""
        if not STREAM_LZ4 or len(mv) < 4096:
            return None
        fk = _family_key(name)
        skip = self._lz4_skip.get(fk, 0)
        if skip > 0:
            self._lz4_skip[fk] = skip - 1
            return None
        t = time.perf_counter()
        c = _lz4().compress(mv, store_size=False)
        dt = time.perf_counter() - t
        ratio = len(c) / max(1, len(mv))
        self.lz4_tries += 1
        if ratio <= LZ4_GAIN:
            self.lz4_used += 1
            self.lz4_saved += len(mv) - len(c)
            return c, len(mv)
        self._lz4_skip[fk] = LZ4_REPROBE
        if STREAM_DIAG or self.lz4_tries <= 2:
            _sdiag("LZ4 %s to=%s ratio=%.3f cost_ms=%.1f -> sending raw; family %s not "
                   "tried again for %d cells" % (name, self.reader, ratio, dt * 1000.0, fk,
                                                 LZ4_REPROBE))
        return None

    def _maybe_diff(self, name, mv):
        """(payload, meta) to send in place of `mv`, or None to send it whole.
        meta tells the reader what to keep and how to rebuild."""
        # One flow only: a difference is against the PREVIOUS cell of the
        # instance, so the two must land in the order they were sent, which
        # one flow with in-order interleaving guarantees and several do not.
        if (not (STREAM_DIFF and self.diff_ok) or self.nsets > 1
                or len(mv) < 2 * DIFF_BLOCK or self._nat is not None):
            return None, {}
        key = "%s/%d" % (_family_key(name), len(mv))
        st = self._diff.setdefault(key, {"prev": None, "skip": 0, "on": False})
        if st["skip"] > 0:
            st["skip"] -= 1
            return None, {}
        if st["prev"] is None:
            # Start a probe: keep this cell, and ask the reader to keep it too.
            st["prev"] = bytes(mv)
            st["pname"] = name
            return None, {"dkeep": key}
        idx, nb = _changed_blocks(mv, st["prev"], DIFF_BLOCK)
        frac = len(idx) / max(1, nb)
        self.diff_tries += 1
        if frac > DIFF_MAX_CHANGED:
            if STREAM_DIAG or self.diff_tries <= 2:
                _sdiag("DIFF %s to=%s changed=%d/%d blocks -> sending whole; instance %s "
                       "not differenced again for %d cells"
                       % (name, self.reader, len(idx), nb, key, DIFF_REPROBE))
            st["prev"], st["skip"], st["on"] = None, DIFF_REPROBE, False
            return None, {"ddrop": key}
        n = len(mv)
        parts = [bytes(mv[i * DIFF_BLOCK:min(n, (i + 1) * DIFF_BLOCK)]) for i in idx]
        base_name = st.get("pname")
        st["prev"], st["on"], st["pname"] = bytes(mv), True, name
        self.diff_used += 1
        self.diff_saved += n - sum(len(p) for p in parts)
        return b"".join(parts), {"dkey": key, "dblocks": idx, "dbs": DIFF_BLOCK, "dn": n,
                                 "dbase": base_name}

    def push(self, name, gen, inc, blob, keep, t_pub, t_req) -> None:
        orig = name
        mv = memoryview(blob).cast("B")
        zn = None
        dpay, dmeta = self._maybe_diff(name, mv)
        if dpay is not None:
            blob, keep = dpay, None
            mv = memoryview(blob).cast("B")
            name = name + "#d"
        z = self._maybe_lz4(name, mv)
        if z is not None:
            blob, zn = z
            keep = None
            mv = memoryview(blob).cast("B")
            name = name + "#z"
        if self._nat is not None:
            with self._cv:
                self._id += 1
                cid = self._id
                d = {"id": cid, "name": name, "orig": orig, "gen": gen, "n": len(mv),
                     "t_pub": t_pub, "t_req": t_req, "depth": len(self._meta),
                     "t_push": time.time(), "zn": zn, "dmeta": dmeta}
                self._meta[cid] = d
            head = _head_body(name, {"gen": gen, "inc": inc, "n": len(mv), "id": cid,
                                     "t_pub": t_pub, "t_req": t_req, "t_send": d["t_push"],
                                     "depth": d["depth"], "active": d["depth"], "zn": zn,
                                     **dmeta})
            self._nat.push(cid, head, _addr(mv) if len(mv) else 0, len(mv), (blob, keep))
            return
        with self._cv:
            self._id += 1
            self._active.append({
                "id": self._id, "name": name, "orig": orig, "zn": zn, "dmeta": dmeta,
                "gen": gen,
                "inc": inc, "mv": mv,
                "n": len(mv), "next": 0, "done": 0, "hdr": False, "keep": keep,
                "t_pub": t_pub, "t_req": t_req, "depth": len(self._active),
                "t0": None, "seg_max": 0.0, "sets": set(), "recs": 0})
            self._cv.notify_all()

    def _seg(self, si: int) -> int:
        r = self.rate.get(si)
        if not r:
            return 64 << 10
        return int(max(STREAM_SEG_MIN, min(STREAM_SEG_MAX, r * STREAM_CEIL_S)))

    def _take(self, si: int):
        """The next record for set `si`: a header for a cell not yet announced,
        else the next slice of a cell, round-robin over the cells in flight."""
        with self._cv:
            while not self.stop.is_set():
                act = self._active
                for k in range(len(act)):
                    d = act[(self._rr + k) % len(act)]
                    if not d["hdr"]:
                        d["hdr"] = True
                        d["t0"] = time.time()
                        self._rr = (self._rr + k + 1) % max(1, len(act))
                        return ("H", d, 0, 0)
                    if d["next"] < d["n"]:
                        off = d["next"]
                        ln = min(self._seg(si), d["n"] - off)
                        d["next"] = off + ln
                        self._rr = (self._rr + k + 1) % max(1, len(act))
                        return ("D", d, off, ln)
                self._cv.wait(1.0)
            return None

    def _finish(self, d, conn) -> None:
        """Every byte of `d` is in a kernel; drop it and say so. Under _cv."""
        if d in self._active:
            self._active.remove(d)
        t1 = time.time()
        self.sent += 1
        self.bytes += d["n"]
        self.send_s += t1 - d["t0"]
        ms = (t1 - d["t0"]) * 1000.0
        if STREAM_DIAG or ms >= STREAM_SLOW_MS:
            # held_ms: request -> publish ("family" for the standing read).
            # ready_ms: ready -> header offered. send_ms: header -> last byte
            # accepted by a kernel, shared with alongside= other cells.
            # sets: which flows carried its records; seg_max_ms: the longest
            # single record -- one huge value among small ones is a stall on
            # that flow, not a slow link.
            _sdiag("SENT %s gen=%d to=%s at=%s bytes=%d held_ms=%s ready_ms=%.1f "
                   "send_ms=%.1f seg_max_ms=%.1f MBps=%.1f depth=%d alongside=%d "
                   "records=%d sets=%s rates_MBps=%s %s"
                   % (d["name"], d["gen"], self.reader, self.who, d["n"],
                      ("family" if d["t_req"] == self.t_family
                       else "%.1f" % ((d["t_pub"] - d["t_req"]) * 1000.0)),
                      (d["t0"] - max(d["t_pub"], d["t_req"])) * 1000.0, ms,
                      d["seg_max"] * 1000.0, (d["n"] / 1e6) / max(t1 - d["t0"], 1e-6),
                      d["depth"], len(self._active), d["recs"], sorted(d["sets"]),
                      {k: round(v / 1e6, 2) for k, v in sorted(self.rate.items())},
                      _ti(conn)))
        name = d.get("orig") or d["name"]
        d["mv"] = d["keep"] = None
        self._cv.release()
        try:
            self._sent_cb(name)
        finally:
            self._cv.acquire()

    def _on_native_done(self, cid, st, err) -> None:
        """frognet_link.cpp's notifier, with the GIL: a cell is in the kernels,
        or a flow failed."""
        if err is not None:
            _sdiag("LINK DOWN (native sender) to=%s at=%s: %s" % (self.reader, self.who, err))
            self.stop.set()
            return
        with self._cv:
            d = self._meta.pop(cid, None)
        if d is None:
            return
        n = d["n"]
        self.sent += 1
        self.bytes += n
        ms = (st["t1"] - st["t0"]) * 1000.0
        self.send_s += ms / 1000.0
        if STREAM_DIAG or ms >= STREAM_SLOW_MS:
            sets = [i for i in range(32) if st["sets"] >> i & 1]
            _sdiag("SENT %s gen=%d to=%s at=%s bytes=%d held_ms=%s ready_ms=%.1f "
                   "send_ms=%.1f seg_max_ms=%.1f MBps=%.1f depth=%d alongside=%d "
                   "records=%d sets=%s rates_MBps=%s native=1 %s"
                   % (d["name"], d["gen"], self.reader, self.who, n,
                      ("family" if d["t_req"] == self.t_family
                       else "%.1f" % ((d["t_pub"] - d["t_req"]) * 1000.0)),
                      (st["t0"] - max(d["t_pub"], d["t_req"])) * 1000.0, ms,
                      st["seg_max"] * 1000.0, (n / 1e6) / max(ms / 1000.0, 1e-6),
                      d["depth"], st["alongside"], st["recs"], sets,
                      {k: round(v / 1e6, 2) for k, v in sorted(self._nat.rates().items())},
                      _ti(self.socks.get(0)) if self.socks.get(0) else ""))
        self._sent_cb(d.get("orig") or d["name"])

    def run_set(self, si: int, conn) -> None:
        """One flow's writer: takes records and sends them until the link stops."""
        conn.settimeout(None)
        with self._cv:
            self.socks[si] = conn
        if self._nat is not None:
            self._nat.add_set(conn.fileno())
            while not self.stop.wait(1.0):
                pass
            return
        while not self.stop.is_set():
            w = self._take(si)
            if w is None:
                return
            kind, d, off, ln = w
            if kind == "H":
                hdr = _head_body(d["name"], {
                    "gen": d["gen"], "inc": d["inc"], "n": d["n"], "id": d["id"],
                    "t_pub": d["t_pub"], "t_req": d["t_req"], "t_send": d["t0"],
                    "depth": d["depth"], "active": len(self._active), "set": si,
                    "zn": d.get("zn"), **(d.get("dmeta") or {})})
                _sendmsg_all(conn, [_REC2.pack(b"H", d["id"] & 0xFFFFFFFF, d["n"], 0,
                                               len(hdr)), hdr])
                if d["n"] == 0:
                    with self._cv:
                        self._finish(d, conn)
                continue
            ts = time.perf_counter()
            _sendmsg_all(conn, [_REC2.pack(b"D", d["id"] & 0xFFFFFFFF, d["n"], off, ln),
                                d["mv"][off:off + ln]])
            dt = time.perf_counter() - ts
            with self._cv:
                r = self.rate.get(si)
                inst = ln / max(dt, 1e-6)
                self.rate[si] = inst if r is None else 0.8 * r + 0.2 * inst
                self.set_bytes[si] = self.set_bytes.get(si, 0) + ln
                d["seg_max"] = max(d["seg_max"], dt)
                d["sets"].add(si)
                d["recs"] += 1
                d["done"] += ln
                if d["done"] >= d["n"]:
                    self._finish(d, conn)


class StreamSubscription:
    """Reader side of one link to one owner: STREAM_SETS permanent flows, one
    landing thread per flow. Set 0 carries the held reads (the family, made
    once; request() for one-shot cells). Records arrive on any flow in any
    order and are put where they belong by id -- straight into the caller's
    tensor if expect()/request() registered it before the header arrived,
    else into a buffer the caller gives back with release()."""

    def __init__(self, me: str, host: str, port: int, label: str,
                 connect_timeout: float = None, family=(), sets: int = None):
        import uuid
        self.me, self.host, self.port, self.label = me, host, int(port), label
        self.nsets = sets or STREAM_SETS
        self._cv = threading.Condition()
        self._send_lock = threading.Lock()
        self._landed: Dict[str, Dict[str, Any]] = {}
        self._dst: Dict[str, Any] = {}
        self._asked: Dict[str, float] = {}
        self._free: Dict[int, list] = {}
        self._open: Dict[int, list] = {}
        self.err = None
        self.cells = self.direct = self.buffered = self.bytes = self.requests = 0
        self.wire_ewma = 0.0
        self._dprev: Dict[str, Any] = {}
        self.diff_cells = 0
        self._takers: Dict[str, int] = {}
        self.coalesced = 0
        self._direct_views: Dict[str, Any] = {}
        self.family = list(family)
        token = uuid.uuid4().hex
        self.socks = []
        to = connect_timeout if connect_timeout else CONNECT_TIMEOUT_S
        t0 = time.time()
        for si in range(self.nsets):
            sk = socket.create_connection((host, self.port), timeout=to)
            sk.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sk.settimeout(None)
            _send_frame(sk, json.dumps({"op": "subscribe", "from": me, "token": token,
                                        "set": si, "sets": self.nsets,
                                        "diff": bool(STREAM_DIFF and not PLANE_NATIVE),
                                        "family": self.family if si == 0 else []}).encode())
            ack = json.loads(bytes(_recv_frame(sk)).decode())
            set_cc(sk)
            self.socks.append(sk)
            try:
                local = "%s:%s" % sk.getsockname()[:2]
            except OSError:
                local = "?"
            _sdiag("LINK %s set %d/%d to %s:%d local=%s connect_ms=%.1f owner_cc=%s "
                   "owner_notsent=%s family=%s%s"
                   % (label, si, self.nsets, host, self.port, local,
                      (time.time() - t0) * 1000.0, ack.get("cc"), ack.get("notsent"),
                      self.family if si == 0 else "-",
                      (" route=" + _route(host)) if si == 0 else ""))
        self.sock = self.socks[0]
        self._nat = None
        self._keep: Dict[str, Any] = {}
        if PLANE_NATIVE:
            # [NATIVE_LINK_V1] Landing in C++: records go straight into the
            # registered tensor, or into a buffer handed back as a tensor.
            self._nat = native_link().Receiver(self._on_native_land)
            _NATIVE_KEEP.append(self._nat)
            for sk in self.socks:
                self._nat.add_set(sk.fileno())
            return
        for si, sk in enumerate(self.socks):
            threading.Thread(target=self._run, args=(si, sk), daemon=True,
                             name="plane-land-%s-s%d" % (label, si)).start()

    def _register(self, name: str, dst) -> None:
        """Where `name` goes if it has not landed yet. Under _cv."""
        if name in self._landed:
            return
        self._dst[name] = dst
        if self._nat is not None and dst is not None:
            self._keep[name] = dst
            self._nat.expect(name, _addr(dst), len(dst))

    def _on_native_land(self, name, body, st, buf, err) -> None:
        """frognet_link.cpp's notifier, with the GIL: a cell landed, or a flow
        failed. The same bookkeeping and the same LANDED line as _land."""
        if err is not None:
            with self._cv:
                first = self.err is None
                if first:
                    self.err = "native: %s" % err
                self._cv.notify_all()
            if first:
                if "peer closed" in str(err):
                    _sdiag("LINK CLOSED %s %s:%d by owner after %d cells; still asked for: %s"
                           % (self.label, self.host, self.port, self.cells, sorted(self._asked)[:6]))
                else:
                    _sdiag("LINK DOWN %s %s:%d after %d cells (native): %s"
                           % (self.label, self.host, self.port, self.cells, err))
            return
        hdr = json.loads(bytes(body).decode())
        hdr["name"] = name
        n = int(st["n"])
        direct = bool(st["direct"])
        bad = int(st["bad"]) if int(st["bad"]) >= 0 else None
        with self._cv:
            self._dst.pop(name, None)
            self._keep.pop(name, None)
            e = {"hdr": hdr, "direct": direct, "target": buf, "bad": bad,
                 "t_first": st["t_first"] or None, "recs": st["recs"],
                 "sets": {i for i in range(32) if st["sets"] >> i & 1}}
            self._land(-1, e)

    def request(self, items) -> None:
        """Ask for cells by name (one-shot held reads), on set 0. Does not wait."""
        new = []
        now = time.time()
        with self._cv:
            for name, dst in items:
                if dst is not None:
                    self._register(name, dst)
                if name not in self._asked and name not in self._landed:
                    self._asked[name] = now
                    new.append(name)
        if new:
            self.requests += len(new)
            with self._send_lock:
                _send_frame(self.sock, json.dumps({"want": new}).encode())

    def _buf(self, n: int):
        lst = self._free.get(n)
        if lst:
            return lst.pop()
        return bytearray(n)

    def release(self, cell) -> None:
        b = cell.get("buf")
        if b is not None and not isinstance(b, (bytearray, bytes)):
            cell["buf"] = None       # a native landing's tensor: dropping it frees it
            return
        if b is not None and not cell.get("direct"):
            with self._cv:
                lst = self._free.setdefault(len(b), [])
                if len(lst) < 4:
                    lst.append(b)
            cell["buf"] = None

    def _land(self, rid: int, e) -> None:
        """Every byte of delivery `rid` is in place. Under _cv."""
        hdr, direct = e["hdr"], e["direct"]
        name, n = hdr["name"], int(hdr["n"])
        self._open.pop(rid, None)
        if name.endswith("#z") and hdr.get("zn") is not None:
            # [LZ4_WHEN_IT_SHRINKS_V1] Expand into the registered destination.
            base, zn = name[:-2], int(hdr["zn"])
            raw = _lz4().decompress(bytes(memoryview(e["target"]).cast("B")) if not hasattr(e["target"], "numpy")
                                    else e["target"].numpy().tobytes(), uncompressed_size=zn)
            dst = self._dst.pop(base, None)
            self._keep.pop(base, None)
            if self._nat is not None:
                self._nat.forget(base)
            if dst is not None and len(dst) == zn:
                memoryview(dst).cast("B")[:] = raw
                e["direct"], e["target"], e["bad"] = True, None, None
            else:
                e["direct"], e["target"] = False, bytearray(raw)
                e["bad"] = len(dst) if dst is not None else None
            hdr["name"], hdr["n"], hdr["zbytes"] = base, zn, n
            name, n, direct = base, zn, e["direct"]
        if name.endswith("#d") and hdr.get("dkey"):
            # [SAME_DIFF_ON_THE_LINK_V1] Rebuild from the last cell of this
            # instance, patched with the blocks that changed.
            base, key, bs, dn = name[:-2], hdr["dkey"], int(hdr["dbs"]), int(hdr["dn"])
            prev, pname = self._dprev.get(key, (None, None))
            if prev is None or len(prev) != dn or pname != hdr.get("dbase"):
                raise ConnectionError("%s: DIFF for %s against %s (instance %s); this reader "
                                      "holds %s -- link out of step"
                                      % (self.label, base, hdr.get("dbase"), key, pname))
            src = e["target"]
            pay = memoryview(src.numpy() if hasattr(src, "numpy") else src).cast("B")
            out = bytearray(prev)
            off = 0
            for i in hdr["dblocks"]:
                ln = min(dn, (i + 1) * bs) - i * bs
                out[i * bs:i * bs + ln] = pay[off:off + ln]
                off += ln
            self._dprev[key] = (bytes(out), base)
            dst = self._dst.pop(base, None)
            self._keep.pop(base, None)
            if dst is not None and len(dst) == dn:
                memoryview(dst).cast("B")[:] = out
                e["direct"], e["target"], e["bad"] = True, None, None
            else:
                e["direct"], e["target"] = False, out
                e["bad"] = len(dst) if dst is not None else None
            hdr["name"], hdr["n"], hdr["dbytes"] = base, dn, n
            name, n, direct = base, dn, e["direct"]
            self.diff_cells += 1
        elif hdr.get("dkeep"):
            # The owner is probing this instance: keep a copy to difference against.
            tgt = e.get("mv") if e["direct"] else e["target"]
            if tgt is not None:
                self._dprev[hdr["dkeep"]] = (bytes(memoryview(tgt.numpy() if hasattr(tgt, "numpy")
                                                              else tgt).cast("B")), name)
        if hdr.get("ddrop"):
            self._dprev.pop(hdr["ddrop"], None)
        t_land = time.time()
        t_ask = self._asked.pop(name, None)
        cell = {"name": name, "gen": hdr["gen"], "n": n, "direct": direct,
                "buf": None if direct else e["target"], "t_land": t_land, "hdr": hdr,
                "t_ask": t_ask, "bad_dst": e["bad"], "t_first": e["t_first"]}
        self._landed[name] = cell
        self.cells += 1
        self.bytes += n
        if direct:
            self.direct += 1
        else:
            self.buffered += 1
        self._cv.notify_all()
        wire = (t_land - (e["t_first"] or t_land)) * 1000.0
        # [PER_PEER_TIMING_V1] Slow for THIS link: several times its own EWMA.
        slow = wire >= max(500.0, 4.0 * self.wire_ewma) if self.wire_ewma else wire >= STREAM_SLOW_MS
        self.wire_ewma = wire if not self.wire_ewma else 0.8 * self.wire_ewma + 0.2 * min(wire, 60000.0)
        if STREAM_DIAG or slow:
            # wire_ms: first data record -> last byte, local clock, shared with
            # open= other cells. sets: which flows its records came on.
            _sdiag("LANDED %s gen=%s from=%s bytes=%d direct=%d wire_ms=%.1f "
                   "ask_to_land_ms=%s owner_held_ms=%s owner_ready_ms=%.1f "
                   "since_send_ms=%.1f depth=%s owner_alongside=%s open=%d records=%d "
                   "sets=%s %s"
                   % (name, hdr["gen"], self.label, n, int(direct), wire,
                      ("%.1f" % ((t_land - t_ask) * 1000.0)) if t_ask else "-",
                      ("family" if t_ask is None
                       else "%.1f" % ((hdr["t_pub"] - hdr["t_req"]) * 1000.0)),
                      (hdr["t_send"] - max(hdr["t_pub"], hdr["t_req"])) * 1000.0,
                      (t_land - hdr["t_send"]) * 1000.0, hdr.get("depth"),
                      hdr.get("active"), len(self._open), e["recs"], sorted(e["sets"]),
                      _ti(self.sock)))

    def _run(self, si: int, sk) -> None:
        try:
            while True:
                kind, rid, total, off, ln = _REC2.unpack(bytes(_recv_exact(sk, _REC2.size)))
                if kind == b"H":
                    hdr = _head_parse(bytes(_recv_exact(sk, ln)))
                    name, n = hdr["name"], int(hdr["n"])
                    with self._cv:
                        dst = self._dst.pop(name, None)
                        bad = dst is not None and len(dst) != n
                        if dst is not None and not bad:
                            target, direct = dst, True
                        else:
                            target, direct = self._buf(n), False
                        mv = target if isinstance(target, memoryview) else memoryview(target).cast("B")
                        e = {"hdr": hdr, "mv": mv, "target": target, "direct": direct,
                             "got": 0, "t_first": None, "bad": (len(dst) if bad else None),
                             "recs": 0, "sets": set()}
                        self._open[rid] = e
                        if n == 0:
                            self._land(rid, e)
                        self._cv.notify_all()
                    continue
                # A data record can arrive on one flow before its header has
                # arrived on another: wait for the header, it is on its way.
                with self._cv:
                    e = self._open.get(rid)
                    while e is None and self.err is None:
                        self._cv.wait(1.0)
                        e = self._open.get(rid)
                    if e is None:
                        return
                if total != int(e["hdr"]["n"]) or off + ln > total:
                    raise ConnectionError(
                        "%s set %d: record id=%d total=%d off=%d len=%d does not fit "
                        "delivery %s (n=%s) -- link out of step"
                        % (self.label, si, rid, total, off, ln, e["hdr"]["name"],
                           e["hdr"]["n"]))
                _recv_exact(sk, ln, into=e["mv"][off:off + ln])
                with self._cv:
                    if e["t_first"] is None:
                        e["t_first"] = time.time()
                    e["got"] += ln
                    e["recs"] += 1
                    e["sets"].add(si)
                    if e["got"] >= int(e["hdr"]["n"]):
                        self._land(rid, e)
        except BaseException as ex:
            import traceback
            with self._cv:
                first = self.err is None
                if first:
                    self.err = "set %d: %s: %s" % (si, type(ex).__name__, ex)
                self._cv.notify_all()
            for other in self.socks:
                try:
                    other.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            if not first:
                return
            if isinstance(ex, ConnectionError) and "after 0 of" in str(ex):
                _sdiag("LINK CLOSED %s %s:%d by owner after %d cells; still asked "
                       "for: %s" % (self.label, self.host, self.port, self.cells,
                                    sorted(self._asked)[:6]))
            else:
                _sdiag("LINK DOWN %s %s:%d set %d after %d cells: %s\n%s"
                       % (self.label, self.host, self.port, si, self.cells, self.err,
                          traceback.format_exc()))

    def expect(self, name: str, dst) -> None:
        """Say where `name` goes before it arrives, so it is received straight
        into `dst`. A cell that already landed is left where it is."""
        with self._cv:
            self._register(name, dst)

    def take(self, name: str, dst=None, timeout_s: float = 300.0):
        """The cell called `name`, once it has landed. `dst` is a writable
        byte view; if registered before the bytes arrive they are received
        straight into it. Returns the cell dict, with `waited_ms` and
        `early` (it had landed before anyone asked)."""
        t0 = time.time()
        with self._cv:
            # [COALESCE_TAKERS_V1] Every taker waiting on the same cell gets
            # the one landing (the proxy's _coalesced_rpc on the reader side):
            # the cell leaves _landed when the last of them has it.
            self._takers[name] = self._takers.get(name, 0) + 1
            cell = self._landed.get(name)
            early = cell is not None
            if cell is None and dst is not None:
                self._register(name, dst)
            deadline = t0 + timeout_s
            while cell is None:
                if self.err:
                    self._dst.pop(name, None)
                    self._takers.pop(name, None)
                    raise ConnectionError("%s: stream from %s:%d is down: %s"
                                          % (self.label, self.host, self.port,
                                             self.err))
                left = deadline - time.time()
                if left <= 0:
                    self._dst.pop(name, None)
                    self._takers.pop(name, None)
                    if self._nat is not None:
                        self._nat.forget(name)
                        self._keep.pop(name, None)
                    raise TimeoutError(
                        "%s: %s did not arrive from %s:%d in %.0f s "
                        "(landed and unclaimed: %s; cells=%d)"
                        % (self.label, name, self.host, self.port, timeout_s,
                           sorted(self._landed)[:8], self.cells))
                self._cv.wait(left)
                cell = self._landed.get(name)
            left_takers = self._takers.get(name, 1) - 1
            if left_takers <= 0:
                self._takers.pop(name, None)
                self._landed.pop(name, None)
            else:
                self._takers[name] = left_takers
                self.coalesced += 1
        cell["waited_ms"] = (time.time() - t0) * 1000.0
        cell["early"] = early
        if cell.get("bad_dst") is not None:
            raise RuntimeError(
                "%s: %s carries %d bytes, the caller's destination holds %d. "
                "The ranks do not agree on the bucket."
                % (self.label, name, cell["n"], cell["bad_dst"]))
        return cell

    def stats(self) -> Dict[str, Any]:
        return {"cells": self.cells, "direct": self.direct, "sets": self.nsets,
                "diff_cells": self.diff_cells, "coalesced": self.coalesced,
                "requests": self.requests, "asked_open": len(self._asked),
                "buffered": self.buffered, "mbytes": round(self.bytes / 1e6, 2),
                "unclaimed": len(self._landed), "err": self.err}


def _route(host: str) -> str:
    """`ip route get` for the peer, once per subscription: which interface
    and next hop the bytes actually take."""
    try:
        import subprocess
        out = subprocess.run(["ip", "route", "get", host], capture_output=True,
                             text=True, timeout=2).stdout
        return "'" + " ".join(out.split()) + "'"
    except Exception as e:
        return "unknown(%s)" % type(e).__name__


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
        #: Tensors whose storage a zero-copy publish points into. Held so
        #: the storage cannot be freed while a state names it.
        self._pinned = {}
        #: [A_SERVER_THAT_DIES_QUIETLY_IS_WORSE_THAN_ONE_THAT_CRASHES_V1]
        self.serve_faults = 0
        self.last_fault = ""
        self.last_fault_tb = ""
        #: [A_WAIT_THAT_EXPIRED_IS_NOT_AN_ANSWER_V1] Reads refused because
        #: the state they asked for never arrived. Nonzero means readers are
        #: outrunning producers, which is a real condition and not an error
        #: to hide.
        self.wait_expired = 0
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

        # [STREAM_V1] Readers that subscribed once and are pushed every
        # matching publish on their permanent connection. _sub_lock is a real
        # lock: publish and subscribe must agree on which cells a new
        # subscriber gets from the snapshot and which from the push.
        self._subs = []
        self._sub_lock = threading.Lock()
        self.stream_cells = 0
        self.retired = 0
        self._pub_t: Dict[str, float] = {}
        self._links: Dict[str, Any] = {}
        self._links_cv = threading.Condition(self._sub_lock)
        self._readers_left: Dict[str, int] = {}

        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True,
                                   name="tensor-server-%d" % self.port)
        self._t.start()
        diag("DIAG-TPLANE", "server up", port=self.port)

    def publish(self, name: str, generation: int, tensor,
                incarnation: Optional[str] = None, copy: bool = True,
                readers: Optional[int] = None) -> None:
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
        # [POINT_AT_THE_BYTES_DO_NOT_COPY_THEM_V1] copy=False stores a view
        # over the caller's storage instead of a frozen copy. Only for a
        # caller that can prove the range outlives every reader of it --
        # see tensor_view_bytes. The tensor is kept alive alongside the
        # view so the storage cannot be collected under it.
        if copy:
            blob = tensor_bytes(tensor)
            keep = None
        else:
            blob = tensor_view_bytes(tensor)
            keep = tensor
        with self._sub_lock:
            self._current[name] = (int(generation), blob, incarnation)
            if keep is not None:
                self._pinned[name] = keep
            self._materialised.pop(name, None)
            self.states_offered += 1
            # [STREAM_V1] Hand the cell to every subscriber that wants it.
            # The reference goes on its queue; its sender thread puts the
            # bytes on the wire. Nobody asked, nobody acks.
            now = time.time()
            self._pub_t[name] = now
            if readers:
                self._readers_left[name] = int(readers)
            for sub in self._subs:
                t_req = sub.fired(name)
                if t_req is not None:
                    sub.push(name, int(generation), incarnation, blob,
                             keep if keep is not None else tensor, now, t_req)
                    self.stream_cells += 1
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

    def _sent(self, name: str) -> None:
        """[PULL_LINK_V1] A cell published with `readers=k` is dropped after
        its k-th send: everyone who will ever ask for it has had it. Until
        then it is kept, so a request that arrives late is still answered."""
        with self._sub_lock:
            left = self._readers_left.get(name)
            if left is None:
                return
            left -= 1
            if left > 0:
                self._readers_left[name] = left
                return
            self._readers_left.pop(name, None)
            self._pub_t.pop(name, None)
            if self._current.pop(name, None) is not None:
                self.retired += 1
            self._pinned.pop(name, None)
            self._materialised.pop(name, None)

    def retire(self, name: str) -> bool:
        """[HOOK_ON_THE_FAMILY_LINK_V1] Drop a cell that nobody will ask for
        again, when the owner knows that and the reader count does not (a
        comm hook that skips peers): the cell is gone from the plane, and a
        request for it waits for a write that will never come."""
        with self._sub_lock:
            self._readers_left.pop(name, None)
            self._pub_t.pop(name, None)
            self._pinned.pop(name, None)
            self._materialised.pop(name, None)
            if self._current.pop(name, None) is None:
                return False
            self.retired += 1
            return True

    def _want(self, sub, names) -> None:
        now = time.time()
        with self._sub_lock:
            for n in names:
                sub.requests += 1
                cur = self._current.get(n)
                if cur is not None:
                    g, b, i = cur
                    sub.push(n, g, i, b, self._pinned.get(n),
                             self._pub_t.get(n, now), now)
                else:
                    sub.wanted[n] = now

    def _stream_to(self, conn, who, req) -> None:
        """[PULL_LINK_V1][CHANNEL_SETS_V1] This connection is now one flow of a
        reader's permanent link. Set 0 creates the link and carries its held
        reads; sets 1.. join it by token. Every flow runs a writer that takes
        records from the link's cells in flight."""
        si = int(req.get("set", 0))
        token = req.get("token") or ("%s#%s" % (who, time.time()))
        cc = set_cc(conn)
        ns = _short_queue(conn)
        if si == 0:
            sub = _Subscriber(req.get("from", who), who, self._sent,
                              req.get("family") or (), token, int(req.get("sets", 1)))
            sub.diff_ok = bool(req.get("diff"))
            with self._sub_lock:
                # Cells of the family already written fire the held read now,
                # in write order; later ones fire it as they are written.
                snap = sorted(((self._pub_t.get(n, 0.0), n, g, b, i)
                               for n, (g, b, i) in self._current.items()
                               if sub.wants(n)))
                for tp, n, g, b, i in snap:
                    sub.push(n, g, i, b, self._pinned.get(n), tp, sub.t_family)
                self._subs.append(sub)
                self._links[token] = sub
                self._links_cv.notify_all()
            _send_frame(conn, json.dumps({"ok": 1, "port": self.port, "already": len(snap),
                                          "cc": cc, "notsent": ns}).encode())
            _sdiag("LINK UP port=%d reader=%s at=%s set=0/%d family=%s already=%d cc=%s "
                   "notsent=%s" % (self.port, sub.reader, who, sub.nsets, sub.family,
                                   len(snap), cc, ns))
            if req.get("want"):
                self._want(sub, req["want"])

            def _requests():
                try:
                    while not self._stop.is_set():
                        r = json.loads(bytes(_recv_frame(conn)).decode())
                        self._want(sub, r.get("want") or [])
                except (ConnectionError, OSError, ValueError) as e:
                    _sdiag("LINK %s port=%d reader=%s: %s (sent %d cells, %.1f MB, per set "
                           "MB %s)" % ("CLOSED by reader" if "after 0 of" in str(e)
                                       else "DOWN", self.port, sub.reader, e, sub.sent,
                                       sub.bytes / 1e6,
                                       {k: round(v / 1e6, 1) for k, v in sorted(sub.set_bytes.items())}))
                    self._stop_sub(sub)
            threading.Thread(target=_requests, daemon=True,
                             name="plane-req-%s" % sub.reader).start()
        else:
            deadline = time.time() + CONNECT_TIMEOUT_S
            with self._links_cv:
                while token not in self._links and time.time() < deadline:
                    self._links_cv.wait(1.0)
                sub = self._links.get(token)
            if sub is None:
                raise ConnectionError("set %d from %s names link %s, which never opened"
                                      % (si, who, token))
            _send_frame(conn, json.dumps({"ok": 1, "port": self.port, "cc": cc,
                                          "notsent": ns}).encode())
            _sdiag("LINK UP port=%d reader=%s at=%s set=%d/%d cc=%s notsent=%s"
                   % (self.port, sub.reader, who, si, sub.nsets, cc, ns))
        try:
            sub.run_set(si, conn)
        finally:
            self._stop_sub(sub)

    def _stop_sub(self, sub) -> None:
        with self._sub_lock:
            if sub in self._subs:
                self._subs.remove(sub)
            self._links.pop(sub.token, None)
        sub.stop.set()
        if getattr(sub, "_nat", None) is not None:
            sub._nat.stop()
        for c in list(sub.socks.values()):
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        with sub._cv:
            sub._cv.notify_all()

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

    def _start_serving(self, conn, peer):
        """Set a freshly accepted connection up and hand it to a thread."""
        conn.setblocking(True)
        # [A_LINK_IS_CLOSED_WHEN_IT_IS_DONE_NOT_WHEN_IT_IS_QUIET_V1]
        # None means block forever waiting for the next request: the reader
        # decides when the conversation is over, not a timer here.
        conn.settimeout(IDLE_REAP_S if IDLE_REAP_S > 0 else None)
        # [FRAME_WITHOUT_COPYING_THE_PAYLOAD_V1] The reader sets this on its
        # end and the server never did. A reply written as a header then a
        # payload waits on the peer's delayed ACK: a SAME reply measured
        # 44 ms with it off and 0.03 ms with it on.
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=self._serve, args=(conn, peer),
                         daemon=True).start()

    def _loop(self):
        sel = selectors.DefaultSelector()
        sel.register(self.sock, selectors.EVENT_READ, None)
        try:
            while not self._stop.is_set():
                for key, _ in sel.select(0.2):
                    if key.data is not None:
                        continue
                    # [DRAIN_THE_QUEUE_EVERY_TIME_YOU_WAKE_V1]
                    #
                    # This accepted ONE connection per wakeup and went back
                    # to select. Peers arrive in bursts -- at world 4 every
                    # rank opens two links to every peer at once -- and this
                    # thread competes for the GIL with every _serve thread
                    # already moving megabytes. A connect that waits longer
                    # than connect_timeout for its turn fails as "timed
                    # out", which looks exactly like a dropped SYN and is
                    # nothing of the sort.
                    #
                    # Draining costs nothing when the queue is empty and
                    # everything when it is not.
                    while True:
                        try:
                            conn, peer = self.sock.accept()
                        except (BlockingIOError, OSError):
                            break
                        self._start_serving(conn, peer)
        finally:
            sel.close()

    def _serve(self, conn, peer):
        """One permanent connection from one reader."""
        who = "%s:%s" % peer
        try:
            while not self._stop.is_set():
                req = json.loads(bytes(_recv_frame(conn)).decode())
                if req.get("op") == "subscribe":
                    # [STREAM_V1] The connection stops being request/reply.
                    self._stream_to(conn, who, req)
                    return
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
                    # [A_WAIT_THAT_EXPIRED_IS_NOT_AN_ANSWER_V1] These two
                    # exits used to be the same `break`, and the code below
                    # could not tell them apart: both fell through to
                    # _materialise and served whatever was current. A reader
                    # that asked for generation 12, waited, and never got it
                    # was handed generation 9 with the reply announcing 9 as
                    # though that had been the question.
                    #
                    # ddp1622: three ranks, three different held-out losses,
                    # three different parameter hashes, from a hook whose
                    # definition is "compute exactly what all_reduce
                    # computes". Nothing failed. Three machines trained three
                    # different models and all three reported success.
                    _dl = time.time() + min(wait_s, MAX_SERVER_WAIT_S)
                    _satisfied = False
                    _c = None
                    _t_wait0 = time.time()
                    _t_wait_said = [0.0]
                    while True:
                        _c = self._current.get(name)
                        if _c is not None and (want_gen is None
                                               or int(_c[0]) >= int(want_gen)):
                            _satisfied = True
                            break
                        _left = _dl - time.time()
                        if _left <= 0:
                            break
                        # [DIAG_PSY_STALL_V1 - John 2026-09-14] The other half
                        # of a stall: a PEER is waiting on US. Without this the
                        # reader's side shows "waiting on peer" and the
                        # producer's side shows nothing at all, so a genuine
                        # cycle -- each rank waiting for the other -- is
                        # invisible from either end. Says what was asked for
                        # and what we actually hold.
                        if (_PLANE_DIAG
                                and (time.time() - _t_wait0) >= _PLANE_STALL_S
                                and (time.time() - _t_wait_said[0])
                                    >= _PLANE_STALL_S):
                            _t_wait_said[0] = time.time()
                            _have = self._current.get(name)
                            print("[DIAG-PLANE] SERVING_WAIT held_s=%.1f "
                                  "name=%s want_gen=%s have_gen=%s "
                                  "left_s=%.1f thread=%s"
                                  % (time.time() - _t_wait0, name,
                                     want_gen,
                                     (_have[0] if _have is not None else None),
                                     _left,
                                     threading.current_thread().name),
                                  flush=True)
                        with self._pub_cv:
                            self._pub_cv.wait(min(_left, 0.05))
                    if not _satisfied:
                        # [A_PEER_THAT_IS_BEHIND_IS_NOT_A_PEER_THAT_IS_GONE_V1]
                        #
                        # This answered NEVER_PUBLISHED when the cell was
                        # absent and WAIT_EXPIRED when it held an older
                        # generation -- and the client retries only the
                        # second. So a producer that had not reached this
                        # bucket YET got a non-retryable answer, while a
                        # producer that was behind on the same bucket got
                        # three more chances. One condition, two answers,
                        # decided by whether that cell had ever been written
                        # before.
                        #
                        # A reader that asked to WAIT has already said it
                        # expects the state not to be there yet. Absence at
                        # expiry is "not yet", and it is the same fact as a
                        # stale generation: the producer is behind.
                        # never_published stays for a reader that did not
                        # ask to wait, where it is simply true.
                        self.wait_expired += 1
                        _send_frame(conn, REPLY_GONE, json.dumps(
                            {"reason": GONE_WAIT_EXPIRED,
                             "gen": None if _c is None else _c[0],
                             "want_gen": want_gen,
                             "waited_s": round(min(wait_s,
                                                   MAX_SERVER_WAIT_S), 3)
                             }).encode())
                        continue
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
            import traceback                  # [IMPORT_GUARD_V2] stdlib; format_exc does not fail
            self.last_fault_tb = traceback.format_exc()
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
        if self.aborted:
            raise ConnectionError("%s: client aborted (process exiting); not "
                                  "opening %s:%s" % (self.me, host, port))
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
    WAIT_RETRIES = int(os.environ.get("FROGNET_PLANE_WAIT_RETRIES", "1"))  # [ASK_ONCE_V1] a held read is held once; asking again is messaging
    WAIT_RETRY_SLEEP_S = float(
        os.environ.get("FROGNET_PLANE_WAIT_RETRY_SLEEP_S", "5"))

    def _read_retry_wait(self, worker, host, port, name, generation,
                         shape, dtype, **kw):
        """Ask again for the same state. Never accept a different one.

        Only GONE_WAIT_EXPIRED is retried. never_published cannot be helped
        by waiting and superseded means the producer moved PAST what was
        asked for -- neither is a late peer, and retrying either would just
        delay a real failure.
        """
        last = None
        for attempt in range(self.WAIT_RETRIES):
            if self.aborted:
                # [EXIT_ENDS_THE_WAIT_V1] A retry outliving the program is
                # what held ddp5744 at exit for 3 x PEER_WAIT.
                raise ConnectionError("%s: aborted while waiting for %s from "
                                      "%s (process exiting)" % (self.me, name,
                                                                worker))
            try:
                return self.read(worker, host, port, name, generation,
                                 shape, dtype, **kw)
            except StateGone as e:
                if getattr(e, "reason", None) != GONE_WAIT_EXPIRED:
                    raise
                last = e
                if attempt + 1 < self.WAIT_RETRIES:
                    diag("DIAG-TPLANE", "wait_retry", peer=worker, name=name,
                         want_gen=generation, attempt=attempt + 1,
                         of=self.WAIT_RETRIES,
                         sleep_s=self.WAIT_RETRY_SLEEP_S)
                    time.sleep(self.WAIT_RETRY_SLEEP_S)
        raise last

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

    aborted = False

    def abort(self) -> int:
        """[EXIT_ENDS_THE_WAIT_V1] End every read in flight, now, from any
        thread. shutdown() -- not close() -- is what wakes a thread blocked in
        recv on Linux; the blocked read gets EOF and raises, and the retry
        loop sees `aborted` and stops. Takes no lock a reader might hold."""
        self.aborted = True
        n = 0
        for sk in list(self._links.values()):
            try:
                sk.shutdown(socket.SHUT_RDWR)
                n += 1
            except OSError:
                pass
        return n

    def close(self):
        with self._lock:
            for sk in self._links.values():
                try:
                    sk.close()
                except OSError:
                    pass
            self._links.clear()
