# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""ram_plane.py -- [THE_TENSORS_ARE_IN_THE_RAM_V1] PyTorch's tensors are cells in the FrogTorch RAM. No rank listens.

John, 17 November 2026: "The tensor should not be listening at all -- it should be using held reads, and when another
node needs a tensor it does not ask the other node for it. It gets it from tuple space"; "the bits are in tuple space
... Tuple space IS the RAM host"; "There should not be anything else talking between the nodes". And 12 November: no
ready flag; the owner's write of the data is the event, and the reader's held read on the family fires the transfer.

Until this file a rank served its tensors from its own process (tensor_plane.TensorServer listened on a port) and
every reader connected to every rank (TensorClient, StreamSubscription): direct sockets from rank to rank that no
transport setting reached. The three classes here stand where those three stood, with the same calls, so the
backends (psychedelic_backend.py, ddp_hook.py, meshfeed.py) changed only in which class they make. The reduction,
its order, the cell names, the generations and the read-ahead are the September code's, untouched.

    RamPlane          where TensorServer was. publish() writes the cell into the RAM, once (a tensor write on the
                      rank's session); nothing is kept or served here.
    RamStream         where StreamSubscription was. One read held in the RAM on the family of cells an owner writes
                      for this reader; the owner's write answers it and the bytes come down this reader's session.
    RamTensorClient   where TensorClient was. A read of one named cell, held in the RAM until its owner writes it.

Every session is a transport driver's connection to the RAM ([THE_RAM_SESSION_IS_A_DRIVERS_CONNECTION_V1],
frogtorch_ram_drivers.py): tcp or softhoma as the transport setting says for that RAM. Nothing here opens a socket.

[A_REFERENCE_SAYS_WHERE_V1] A rank's cells are named `<host>:<number>/<name>`: the owner's identity is part of the
cell's address in the memory, as it was part of the address a reader connected to. `host` and `number` are what the
rank writes in its rendezvous cell (the number is its process id: no rank has a port). A reader is handed them with
every read, exactly as before, so two groups, or two runs one after the other, cannot read each other's cells.

A rank's cells are held by its session (life="session"): they go when its process goes, as they did when they were
in the process. A cell written for k readers (publish(readers=k)) goes when the k-th has it.

The RAM is the one every rank agrees on: the FrogTorch RAM on the machine `databasehost.frognet` resolves to
(frogtorch_ram_client.Ram.control()).
"""
from __future__ import annotations

import json
import os
import re
import struct
import threading
import time
from typing import Any, Dict, Optional, Tuple

from . import frogtorch_ram_client as R
from . import frogtorch_ram_drivers as D
from .tensor_plane import (BULK_LEN_FMT, BULK_LEN_LEN, REPLY_DATA, STAGE_PLANE_PUBLISH, TensorClient, diag,
                           tensor_view_bytes)

BUILD = "ram-plane-v1-20261119a"


def cell_name(host: str, number: int, name: str) -> str:
    """[A_REFERENCE_SAYS_WHERE_V1]"""
    return "%s:%d/%s" % (host, int(number), name)


def _open(what: str):
    """A new session with the one RAM, for `what` alone: (driver end, (host, port))."""
    hp = R.ram().control()
    try:
        end = D.end_for(*hp)
        end.connect()
    except D.ConnectionGone as e:
        raise ConnectionError("%s: cannot reach the RAM host at %s:%d (%s)" % (what, hp[0], hp[1], e)) from e
    R._say_driver(hp[0], hp[1], end)
    return end, hp


class RamPlane:
    """A rank's side of its own cells: it writes them into the RAM. Where tensor_plane.TensorServer was."""

    def __init__(self, bind_ip: str = "0.0.0.0", port: int = 0, watch=None):
        from .tensor_plane import _NoWatch
        self.host = bind_ip
        self.port = os.getpid()          # this rank's number in its rendezvous cell; nothing listens on it
        self.watch = watch if watch is not None else _NoWatch()
        self._lock = threading.Lock()    # one writer at a time on the session: a cell's request and bytes go together
        self._end = None
        self._hp: Optional[Tuple[str, int]] = None
        self._n = 0
        self.states_offered = self.states_materialised = self.stream_cells = self.retired = 0
        self.bytes_offered = self.bytes_sent = 0
        self.up_ms_sum = self.up_ms_max = 0.0
        self._current: Dict[str, int] = {}     # cells written for no fixed number of readers: name -> generation

    def _put(self, frame) -> None:
        self._n += 2
        for unit in self._end.pack(self._n, frame):
            self._end.send(unit)

    def _write(self, head: Dict[str, Any], blob=None) -> None:
        with self._lock:
            if self._end is None:
                self._end, self._hp = _open("ram-plane %s:%d" % (self.host, self.port))
            try:
                self._put(json.dumps(head).encode())
                if blob is not None:
                    self._put(blob)
            except D.ConnectionGone as e:
                # [A_SESSION_ENDS_WITH_ITS_CONNECTION_V1] the cells this session held went with it
                self._end.close()
                raise ConnectionError("ram-plane %s:%d: the session with the RAM host at %s:%d has ended with its "
                                      "connection (%s); the cells this rank had written went with it"
                                      % (self.host, self.port, self._hp[0], self._hp[1], e)) from e

    def publish(self, name: str, generation: int, tensor, incarnation: Optional[str] = None, copy: bool = True,
                readers: Optional[int] = None) -> None:
        """Write the cell into the RAM. The write is the event: every read held on this name or on a family it is
        in is answered by it. The bytes are on the session when this returns, so the tensor is not kept and is not
        copied (`copy` is accepted for the callers that pass it; there is nothing left here to freeze)."""
        blob = tensor_view_bytes(tensor)
        t0 = time.perf_counter()
        self._write({"op": "tput", "name": cell_name(self.host, self.port, name), "gen": int(generation),
                     "inc": incarnation, "life": "session", "readers": int(readers) if readers else None,
                     "ack": False}, blob)
        ms = (time.perf_counter() - t0) * 1000.0
        self.up_ms_sum += ms
        self.up_ms_max = max(self.up_ms_max, ms)
        self.states_offered += 1
        self.states_materialised += 1
        self.stream_cells += 1
        self.bytes_offered += len(blob)
        self.bytes_sent += len(blob)
        if readers:
            self._current.pop(name, None)
        else:
            self._current[name] = int(generation)
        self.watch.observe(STAGE_PLANE_PUBLISH, step=generation, tensor=tensor, name=name)
        diag("DIAG-TPLANE", "offer", name=name, gen=generation, incarnation=incarnation)

    def retire(self, name: str) -> bool:
        """The owner drops a cell nobody will read again."""
        self._write({"op": "tdel", "name": cell_name(self.host, self.port, name)})
        self.retired += 1
        return self._current.pop(name, None) is not None

    def stats(self) -> Dict[str, Any]:
        n = self.states_offered
        return {"port": self.port, "bytes_offered": self.bytes_offered, "bytes_sent": self.bytes_sent,
                "same_replies": 0, "data_replies": n, "reduction": 1.0 if n else None,
                "in": "the FrogTorch RAM at %s:%d" % self._hp if self._hp else "the FrogTorch RAM (nothing written yet)",
                "driver": self._end.name if self._end is not None else None,
                "cells_written": n, "write_ms_mean": round(self.up_ms_sum / n, 3) if n else None,
                "write_ms_max": round(self.up_ms_max, 3)}

    def close(self):
        with self._lock:
            if self._end is not None:
                self._end.close()


class RamStream:
    """A reader's read held in the RAM on the family of cells one owner writes for it. Where
    tensor_plane.StreamSubscription was: the same calls, and a cell is the same dict (name, gen, n, direct, buf,
    t_land, waited_ms, early), with up_ms / held_ms / prev_send_ms from the RAM beside them.

    `host` and `port` say whose cells: the owner's, from its rendezvous cell. Nothing connects to them."""

    def __init__(self, me: str, host: str, port: int, label: str, connect_timeout: float = None, family=(),
                 sets: int = None):
        self.me, self.host, self.port, self.label = me, host, int(port), label
        self.nsets = 1
        self._cv = threading.Condition()
        self._send_lock = threading.Lock()
        self._landed: Dict[str, Dict[str, Any]] = {}
        self._dst: Dict[str, Any] = {}
        self._asked: Dict[str, float] = {}
        self._takers: Dict[str, int] = {}
        self.err = None
        self.cells = self.direct = self.buffered = self.bytes = self.requests = self.coalesced = 0
        self.up_ms_sum = self.held_ms_sum = self.send_ms_sum = 0.0
        self._pre = cell_name(host, port, "")
        self.family = list(family)
        self._n = 0
        self._end, self._hp = _open(label)
        # the held read on the family: made once. A family is a regular expression over the owner's names; the
        # owner's identity goes in front of it, as it goes in front of every name ([A_REFERENCE_SAYS_WHERE_V1]).
        self._send({"op": "tsub", "family": [re.escape(self._pre) + f for f in self.family], "from": me})
        threading.Thread(target=self._run, daemon=True, name="ram-stream-%s" % label).start()
        diag("DIAG-TPLANE", "held read on a family", label=label, owner="%s:%d" % (host, self.port),
             ram="%s:%d" % self._hp, driver=self._end.name, family=self.family)

    def _send(self, head: Dict[str, Any]) -> None:
        with self._send_lock:
            self._n += 2
            try:
                for unit in self._end.pack(self._n, json.dumps(head).encode()):
                    self._end.send(unit)
            except D.ConnectionGone as e:
                self._down(e)
                raise ConnectionError("%s: the session with the RAM host at %s:%d has ended with its connection (%s)"
                                      % (self.label, self._hp[0], self._hp[1], e)) from e

    def _down(self, e: BaseException) -> None:
        with self._cv:
            if self.err is None:
                self.err = "%s: %s" % (type(e).__name__, e)
            self._cv.notify_all()

    def _run(self) -> None:
        """Every cell that answers the held read, as it arrives: into the tensor that was named for it if one was
        (expect / request / take said where it goes), else kept until it is taken."""
        try:
            while True:
                got = self._end.unpack(self._end.receive())
                if got is None:
                    continue
                frame = got[1]
                if bytes(frame[:4]) != REPLY_DATA:
                    raise ConnectionError("the RAM answered a held read with %r" % bytes(frame[:24]))
                (hl,) = struct.unpack(BULK_LEN_FMT, bytes(frame[4:4 + BULK_LEN_LEN]))
                hdr = json.loads(bytes(frame[4 + BULK_LEN_LEN:4 + BULK_LEN_LEN + hl]).decode())
                blob = frame[4 + BULK_LEN_LEN + hl:]
                name, n = hdr["name"][len(self._pre):], int(hdr["n"])
                now = time.time()
                with self._cv:
                    dst = self._dst.pop(name, None)
                    cell = {"name": name, "gen": hdr.get("gen"), "inc": hdr.get("inc"), "n": n, "t_land": now,
                            "t_first": None, "up_ms": hdr.get("up_ms"), "held_ms": hdr.get("held_ms"),
                            "prev_send_ms": hdr.get("prev_send_ms"), "direct": False, "buf": None, "bad_dst": None}
                    if dst is not None and len(dst) == n:
                        memoryview(dst).cast("B")[:] = blob
                        cell["direct"] = True
                        self.direct += 1
                    else:
                        # torch.frombuffer wants a buffer it may write: the tcp driver's frame is one, bytes are not
                        cell["buf"] = blob if not memoryview(blob).readonly else bytearray(blob)
                        cell["bad_dst"] = len(dst) if dst is not None else None
                        self.buffered += 1
                    self.cells += 1
                    self.bytes += n
                    self.up_ms_sum += float(hdr.get("up_ms") or 0.0)
                    self.held_ms_sum += float(hdr.get("held_ms") or 0.0)
                    self.send_ms_sum += float(hdr.get("prev_send_ms") or 0.0)
                    self._asked.pop(name, None)
                    self._landed[name] = cell
                    self._cv.notify_all()
        except Exception as e:                                # the session has ended: every taker is told why
            self._down(e)

    def _register(self, name: str, dst) -> None:
        if name not in self._landed:
            self._dst[name] = dst

    def expect(self, name: str, dst) -> None:
        """Say where `name` goes before it arrives. A cell that already landed is left where it is."""
        with self._cv:
            self._register(name, dst)

    def request(self, items) -> None:
        """Hold a read on each named cell, once (cells outside the family). Does not wait."""
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
            self._send({"op": "twant", "names": [self._pre + n for n in new]})

    def release(self, cell) -> None:
        cell["buf"] = None

    def take(self, name: str, dst=None, timeout_s: float = 300.0):
        """The cell called `name`, once it has landed (tensor_plane.StreamSubscription.take's contract)."""
        t0 = time.time()
        with self._cv:
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
                    raise ConnectionError("%s: the read held in the RAM at %s:%d on %s:%d's cells is down: %s"
                                          % (self.label, self._hp[0], self._hp[1], self.host, self.port, self.err))
                left = deadline - time.time()
                if left <= 0:
                    self._dst.pop(name, None)
                    self._takers.pop(name, None)
                    raise TimeoutError("%s: %s was not written into the RAM at %s:%d by %s:%d in %.0f s (landed and "
                                       "unclaimed: %s; cells=%d)" % (self.label, name, self._hp[0], self._hp[1],
                                                                    self.host, self.port, timeout_s,
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
            raise RuntimeError("%s: %s carries %d bytes, the caller's destination holds %d. The ranks do not agree on "
                               "the bucket." % (self.label, name, cell["n"], cell["bad_dst"]))
        return cell

    def stats(self) -> Dict[str, Any]:
        c = self.cells or 1
        return {"cells": self.cells, "direct": self.direct, "sets": self.nsets, "diff_cells": 0,
                "coalesced": self.coalesced, "requests": self.requests, "asked_open": len(self._asked),
                "buffered": self.buffered, "mbytes": round(self.bytes / 1e6, 2), "unclaimed": len(self._landed),
                "err": self.err, "ram": "%s:%d" % self._hp, "driver": self._end.name,
                # where a cell's time went, per cell, each on one machine's clock: the owner's write into the RAM,
                # the cell waiting in the RAM for this session's sender, the RAM's send down to this reader
                "owner_to_ram_ms": round(self.up_ms_sum / c, 3), "in_ram_ms": round(self.held_ms_sum / c, 3),
                "ram_to_reader_ms": round(self.send_ms_sum / c, 3)}

    def close(self) -> None:
        self._end.close()


class RamTensorClient(TensorClient):
    """Reads of single named cells, held in the RAM until their owner writes them. Where tensor_plane.TensorClient
    was: read() and read_into() are its own, unchanged; only the link is replaced. `host` and `port` in a read say
    whose cell it is ([A_REFERENCE_SAYS_WHERE_V1]); the request goes to the RAM, on a session per owner so reads of
    different owners' cells overlap as they did."""

    #: [A_READ_IS_HELD_UNTIL_THE_WRITE_V1] How long a read that names no wait of its own is held in the RAM for its
    #: owner's write. A rank's cell and the descriptor that names it reach the RAM on different sessions, so a
    #: reader can hold the descriptor before the bytes have landed (found 19 Nov: DDP's first broadcast, "r0 does not
    #: publish 'bc.3' at all"). When the bytes were in the owner's process that order could not happen and a read
    #: asked without a wait was refused at once. Now the write is the event the read waits for; there is no ready
    #: flag. The backends set this to their group's timeout.
    hold_s = float(os.environ.get("FROGTORCH_READ_HOLD_S", "300"))

    def __init__(self, me: str, connect_timeout: float = None, watch=None):
        super().__init__(me, connect_timeout=connect_timeout, watch=watch)
        self._ends: Dict[str, list] = {}           # link key -> [driver end, requests sent on it]

    def _exchange(self, host: str, port: int, key: str, req: bytes, wait_s: float, into, mark):
        if self.aborted:
            raise ConnectionError("%s: client aborted (process exiting); not reading %s:%s's cells" % (self.me, host, port))
        with self._lock:
            e = self._ends.get(key)
        if e is None:
            end, _hp = _open("%s (cells of %s)" % (self.me, key))
            e = [end, 0]
            with self._lock:
                self._ends[key] = e
            diag("DIAG-TPLANE", "link opened", peer=key, host=_hp[0], port=_hp[1], driver=end.name)
        end = e[0]
        r = json.loads(req.decode())
        r["name"] = cell_name(host, port, r["name"])
        if not r.get("wait_s"):
            r["wait_s"] = self.hold_s              # [A_READ_IS_HELD_UNTIL_THE_WRITE_V1]
        e[1] += 2
        try:
            for unit in end.pack(e[1], json.dumps(r).encode()):
                end.send(unit)
            got = None
            while got is None:
                got = end.unpack(end.receive())
        except D.ConnectionGone:
            with self._lock:                       # [A_SESSION_ENDS_WITH_ITS_CONNECTION_V1]
                if self._ends.get(key) is e:
                    del self._ends[key]
            end.close()
            raise
        mark.append(time.perf_counter())
        frame = got[1]
        return frame if not memoryview(frame).readonly else memoryview(bytearray(frame))

    def abort(self) -> int:
        self.aborted = True
        with self._lock:
            ends = list(self._ends.values())
        for e in ends:
            e[0].close()
        return len(ends)

    def close(self):
        with self._lock:
            ends, self._ends = list(self._ends.values()), {}
        for e in ends:
            e[0].close()
