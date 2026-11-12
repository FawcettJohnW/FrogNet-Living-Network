################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""memory_plane.py -- a rank's own memory, readable by reference ([THE_BITS_STAY_WHERE_THEY_ARE_V1]).

John, 2026-11-12: "You do not copy bits from one machine to another. You leave them where they were and use
references." and "One RAM host for all PyTorch work."

So a tensor stays in the memory of the rank that produced it. The one RAM (the region on the machine
`databasehost.frognet` resolves to) holds membership and the reference -- where each rank's memory is, written once
at join (Placement). A reader holds a read directly on the owner's memory for (name, generation); the read is held
here until the owner stores that generation, and the bytes then go from the owner's memory onto the link and from
the link into the reader's tensor (TensorClient.read_into). Nothing is staged in any host in between.

  publish(name, gen, tensor, copy=True)   store. copy=True: one copy into a buffer this plane owns -- the freeze, so
                                          the caller may change its tensor at once. copy=False: the cell IS the
                                          caller's tensor; only for a caller that can show the range does not change
                                          until every reader has it (the shard all-reduce's phase-1 slices).
  the wire                                a read is the frame TensorClient sends: {name, gen, mode, have, want, inc,
                                          wait_s, verify} -> SAME | DATA {gen, digest, inc} + bytes | GONE {reason}

A buffer is filled again only when no cell and no send refers to it. A held read is held for the reader's own
deadline. Nothing is answered that was not asked, and nothing asked is ever re-asked here.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from typing import Any, Dict, List, Optional

from .tensor_plane import (BULK_LEN_FMT, GONE_NEVER_PUBLISHED, GONE_SUPERSEDED, GONE_WAIT_EXPIRED,
                           READ_BY_IDENTITY, REPLY_DATA, REPLY_GONE, REPLY_SAME, _digest, _recv_frame, _send_frame,
                           tensor_view_bytes)


class _Buf:
    """A buffer this plane owns. `refs`: 1 while it is a cell's value, +1 per send from it."""
    __slots__ = ("t", "refs")

    def __init__(self, nbytes: int):
        import torch
        self.t = torch.empty(nbytes, dtype=torch.uint8)
        self.refs = 0


class _Cell:
    __slots__ = ("cond", "value")

    def __init__(self, lock):
        self.cond = threading.Condition(lock)
        self.value = None              # (gen, bytes-view, inc, digest-or-None, _Buf-or-None)


class MemoryPlane:
    """This rank's memory: its tensors by name, served by reference to whoever holds a read on them."""

    def __init__(self, owner, bind: str = "0.0.0.0", port: int = 0):
        self.owner = owner
        self._lock = threading.Lock()
        self._cells: Dict[str, _Cell] = {}
        self._bufs: List[_Buf] = []
        self.states_offered = 0
        self.states_materialised = 0
        self.bytes_sent = 0
        self.data_replies = self.same_replies = self.wait_expired = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind, port))
        self.sock.listen(1024)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True, name="memory-plane-%d" % self.port).start()

    @staticmethod
    def key(name: str, owner) -> str:
        """A cell's address: `<name>@<owner>` -- names repeat across ranks, the owner makes them unique."""
        return "%s@%s" % (name, owner)

    def cell_name(self, name: str) -> str:
        return self.key(name, self.owner)

    # ---- store --------------------------------------------------------------------------------------------
    def _cell(self, name: str) -> _Cell:
        c = self._cells.get(name)
        if c is None:
            c = self._cells[name] = _Cell(self._lock)
        return c

    def _unref(self, buf: Optional[_Buf]) -> None:
        if buf is not None:
            buf.refs -= 1

    def _buffer(self, n: int) -> _Buf:
        """A buffer of at least n bytes that nothing refers to (lock held); a new one if none is."""
        best = None
        for b in self._bufs:
            if b.refs == 0 and b.t.numel() >= n and (best is None or b.t.numel() < best.t.numel()):
                best = b
        if best is None:
            best = _Buf(1 << max(16, (max(1, n) - 1).bit_length()))
            self._bufs.append(best)
        return best

    def publish(self, name: str, generation: int, tensor, incarnation=None, copy: bool = True, region=None) -> None:
        import torch
        src = tensor.detach()
        if src.device.type != "cpu":
            src = src.to("cpu")
        src = src.contiguous().reshape(-1)
        if src.dtype == torch.bool:
            src = src.to(torch.uint8)
        key = self.key(name, self.owner)
        if copy:
            raw = src.view(torch.uint8)
            n = raw.numel()
            with self._lock:
                buf = self._buffer(n)
                buf.refs += 1                     # taken: the cell's reference, from before it is filled
            if n:
                buf.t[:n].copy_(raw)              # the one copy
            view = memoryview(buf.t[:n].numpy()) if n else memoryview(b"")
        else:
            buf, view = None, tensor_view_bytes(src)
        with self._lock:
            c = self._cell(key)
            if c.value is not None:
                self._unref(c.value[4])
            c.value = (int(generation), view, incarnation, None, buf)
            c.cond.notify_all()
        self.states_offered += 1

    # ---- serve --------------------------------------------------------------------------------------------
    def _accept(self):
        while True:
            try:
                conn, peer = self.sock.accept()
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            while True:
                req = json.loads(bytes(_recv_frame(conn)).decode())
                self._read(conn, req)
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _get(self, name: str, want_gen, wait_s: float):
        """The cell once it has reached want_gen, held until then (up to the reader's own wait_s); PINNED when
        returned with ok, so its buffer is not filled again under the send."""
        deadline = time.monotonic() + wait_s
        with self._lock:
            c = self._cell(name)
            while True:
                v = c.value
                if v is not None and (want_gen is None or v[0] >= int(want_gen)):
                    if v[4] is not None:
                        v[4].refs += 1
                    return v, True
                left = deadline - time.monotonic()
                if left <= 0:
                    return v, False
                c.cond.wait(left)

    def _read(self, conn, req):
        name, want_gen, mode = req.get("name"), req.get("gen"), req.get("mode", READ_BY_IDENTITY)
        have, want_digest, want_inc = req.get("have"), req.get("want"), req.get("inc")
        wait_s = float(req.get("wait_s") or 0.0)
        t0 = time.monotonic()
        v, ok = self._get(name, want_gen, wait_s)
        if not ok:
            if v is None and wait_s <= 0:
                _send_frame(conn, REPLY_GONE, json.dumps({"reason": GONE_NEVER_PUBLISHED}).encode())
            else:
                self.wait_expired += 1
                _send_frame(conn, REPLY_GONE, json.dumps({"reason": GONE_WAIT_EXPIRED,
                                                          "gen": None if v is None else v[0], "want_gen": want_gen,
                                                          "waited_s": time.monotonic() - t0}).encode())
            return
        try:
            gen, blob, inc, digest, _buf = v
            if want_digest is not None or have is not None or bool(req.get("verify", False)):
                digest = digest or _digest(blob)
            if mode == READ_BY_IDENTITY and ((want_inc is not None and inc is not None and want_inc != inc)
                                             or (want_digest is not None and want_digest != digest)
                                             or (want_gen is not None and int(want_gen) != int(gen))):
                _send_frame(conn, REPLY_GONE, json.dumps({"reason": GONE_SUPERSEDED, "gen": gen,
                                                          "digest": digest, "inc": inc}).encode())
                return
            if have is not None and have == digest:
                self.same_replies += 1
                _send_frame(conn, REPLY_SAME, json.dumps({"gen": gen, "digest": digest, "inc": inc}).encode())
                return
            head = json.dumps({"gen": gen, "digest": digest, "inc": inc}).encode()
            # from the owner's memory onto the link: the kernel's copy is the only one on this side
            _send_frame(conn, REPLY_DATA, struct.pack(BULK_LEN_FMT, len(head)), head, blob)
            self.data_replies += 1
            self.states_materialised += 1
            self.bytes_sent += len(blob)
        finally:
            if v[4] is not None:
                with self._lock:
                    v[4].refs -= 1

    def stats(self) -> Dict[str, Any]:
        return {"port": self.port, "cells": len(self._cells), "buffers": len(self._bufs),
                "buffer_bytes": sum(b.t.numel() for b in self._bufs), "data_replies": self.data_replies,
                "same_replies": self.same_replies, "wait_expired": self.wait_expired, "bytes_sent": self.bytes_sent,
                "states_offered": self.states_offered}

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
