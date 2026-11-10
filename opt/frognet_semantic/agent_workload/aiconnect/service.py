#!/usr/bin/env python3
"""
Copyright (C) 2016-2026 Fawcett Innovations LLC
SPDX-License-Identifier: GPL-2.0-only

This program is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation; version 2 of the License, and no other version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.  See the GNU General Public License for details.

See COPYRIGHT and LICENSE at the root of this tree.

------------------------------------------------------------------------------
AIConnect — the resident service. One per host.

It does two things and nothing else:

  1. Asserts its own truth into RAM and keeps it fresh:
         (AIConnect, MachineReady, <machine>) = {available, av_port, ...}
     Nobody is told. Anyone who wants to know reads the set.

  2. Watches for a connection request addressed to it:
         (AIConnect, ConnectionRequest, <machine>)
     and when it sees one, binds a receiving socket for BULK, then asserts
         (AIConnect, BulkReady, <machine>) = {port, ...}
     which is the producer's signal that this machine is primed.

     When the transfer ends it sets BulkReady to 0 -- the producer's signal
     that the receiver is finished.

WHY IT IS SHAPED THIS WAY

No agent addresses another agent. A machine publishes what is true of itself
and reads what is true of others; the rendezvous is a consequence of what is in
memory, not a message anybody sent. That is why the request is a TUPLE and not
an RPC: the producer does not need the consumer to be listening at the moment
it decides, and the consumer does not need to know who asked.

CONTROL AND DATA ARE DIFFERENT PLANES. Readiness, the request, the port and the
completion flag are small facts and live in RAM. The bulk itself never goes in
a tuple -- it goes over a socket the receiver opened for it. Putting megabytes
through the control plane defeats the template cache and costs two orders of
magnitude; that has been measured.

THERE IS NO NOTIFICATION. This polls, and the interval is the service's own
choice, recorded in its readiness tuple so a producer can reason about how
stale a view of this machine might be.
"""

from __future__ import annotations

import argparse
import errno
import json
import multiprocessing
import os
import resource
import selectors
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

_TREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _TREE not in sys.path:
    sys.path.insert(0, _TREE)

from core.frognet_tuples import get_all, put  # noqa: E402
from core.frognet_diag import diag, diag_exc  # noqa: E402

SERVICE = "AIConnect"
VAR_READY = "MachineReady"
VAR_REQUEST = "ConnectionRequest"
VAR_BULK = "BulkReady"

# [SCOPE_MUST_CARRY_THE_VAR_V1]
# The write template is keyed on SensorType PLUS the metric name, and
# templates._extract_metric_name() takes the LAST dot-separated segment of
# SensorName, only when the name has four or more of them:
#
#     SD:MachineReady.10.170.170.1   -> parts [SD:MachineReady,10,170,170,1] -> "1"
#     SD:BulkReady.10.170.170.1      -> ...                                  -> "1"
#
# A bare IP as the scope makes every var collapse onto the last octet, so all
# three vars keyed to the SAME template. Whichever shape arrived first won, and
# the others were encoded against it: BulkReady reached the database as
#     {"available":null,"cores":null,"machine":"10.170.170.1","poll_interval_s":null}
# with `port` simply gone, which is why a primed peer never looked primed.
#
# The tree's working writers put a distinguishing segment LAST --
# SD:capability.host:10.250.250.1:databasehost -> "1:databasehost",
# SD:presence.host:10.250.250.20:presence:john-542c -> "20:presence:john-542c".
# So the scope ends with the var name and each var gets its own template, which
# is what host_scope/node_scope's "host:<ip>" convention is shaped for.
def scope_for(machine: str, var: str) -> str:
    """host:<ip>:<var> -- ends in the var, so the metric name distinguishes it."""
    return "host:%s:%s" % (machine, var)

#: The ELECTED DATA host. Never databasehost_control.frognet -- that is the
#: coordination plane and no application may address it.
DATA_PLANE_DBHOST = "databasehost.frognet"


def machine_name() -> str:
    """This host's scope key: the address the rest of the mesh knows it by.

    [IDENTITY_COMES_FROM_getEth0Address_V1]
    /usr/local/bin/getEth0Address is what every other component in the tree
    uses -- addHost.bash, getFrogNet.bash, setup_lillypad_v4.bash,
    frognet_diag.sh -- so it is the node's identity by definition, and using
    anything else means this service keys itself differently from everything
    around it.

    my_ip() was used here and got it wrong: on Seattle3 it returned
    10.250.250.191, a transit-segment address, where the node's identity is
    10.130.130.1. It excludes 10.253 and 10.254 but not 10.250, so the service
    asserted MachineReady under a scope nothing recognises.

    There is no fallback. If getEth0Address cannot say what this machine is,
    the service must not start: a node running under the wrong identity is
    worse than one that is absent, because it appears in the roster and then
    nothing can reach it.
    """
    r = subprocess.run(["/usr/local/bin/getEth0Address"],
                       capture_output=True, text=True, timeout=10)
    ip = (r.stdout or "").strip()
    if r.returncode != 0 or not ip:
        raise RuntimeError(
            "getEth0Address gave no address (rc=%s, stderr=%r). This machine "
            "cannot assert an identity and must not start."
            % (r.returncode, (r.stderr or "").strip()[:200]))
    return ip


#: Listen backlog. [BACKLOG_MUST_NOT_BE_THE_LIMIT_V1] This was 16, and at 256
#: concurrent senders the queue overflowed and the kernel reset connections --
#: reported as "Connection reset by peer" and read as a network limit when it
#: was a constant in this file. It is now larger than any concurrency the ramp
#: will reach, so when the ramp stops it stops on something real.
LISTEN_BACKLOG = 4096

#: select()'s old descriptor ceiling, kept only so the failure line can say
#: what this receiver is NO LONGER bounded by. [SELECTOR_NOT_SELECT_V1]
_FD_SETSIZE = 1024


def _nofile():
    try:
        return "%d/%d" % resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception:
        return "?"

#: Receiver threads. One thread per connection meant 256 senders created 256
#: threads here, so the receiver's scheduling cost grew with the sender count
#: and the measurement included it. A fixed pool drains the accept queue at a
#: cost that does not scale with load.
#: Fixed-width sender identifier at the head of every bulk stream:
#: "<sender-ip>|<thread-id>" padded with NULs. Fixed so the receiver never
#: parses ahead of the payload.
BULK_HDR_LEN = 32


#: [PERSISTENT_LINK_NEEDS_FRAMING_V1]
#: The stream used to carry ONE object and end at the close, so the close WAS
#: the object boundary and the receiver needed no framing at all. That made
#: every object a fresh TCP connection: at 64 KB objects and 454 transfers/s,
#: 454 connects and 454 accepts a second, which is connection setup being
#: measured instead of data movement. The link is meant to be permanent.
#:
#: A permanent link carries many objects, so the boundary has to be on the
#: wire. Each object is preceded by an unsigned 64-bit big-endian length. The
#: identity header is still sent exactly once, when the link opens.
BULK_LEN_FMT = "!Q"
BULK_LEN_LEN = struct.calcsize(BULK_LEN_FMT)


def bulk_header(sender: str, thread_id: int) -> bytes:
    tag = ("%s|%d" % (sender, thread_id)).encode("ascii", "replace")[:BULK_HDR_LEN]
    return tag + b"\x00" * (BULK_HDR_LEN - len(tag))


def bulk_frame(n: int) -> bytes:
    """Length prefix for one object of n bytes."""
    return struct.pack(BULK_LEN_FMT, int(n))


#: [REUSEPORT_SHARDS_V1] How many independent receiver processes share the
#: bulk port. One selector loop on one thread did every accept() and every
#: recv() inline, so the receiver was one core no matter how many the box had
#: -- measured at 28.42 MB/s with the ramp still climbing when it hit the
#: driver ceiling. SO_REUSEPORT lets N processes bind the SAME port and lets
#: the KERNEL balance connections across them, which is the only one of the
#: available shapes that escapes the GIL rather than working around it.
BULK_SHARDS = int(os.environ.get("FROGNET_BULK_SHARDS", "0")) or (
    os.cpu_count() or 1)

#: Per-shard slots in the shared counter array: bytes, transfers, open conns,
#: accepts. Each shard owns its own slots and is the only writer, so no lock is
#: needed on the hot path; the parent sums them on read.
_SLOT_BYTES, _SLOT_XFERS, _SLOT_CONNS, _SLOT_ACCEPTS = 0, 1, 2, 3
_SLOTS_PER_SHARD = 4

_ERRBUF_LEN = 512

#: [DRAIN_MUST_BE_FAIR_V1] Bytes one connection may take from a shard in a
#: single readiness pass before the shard moves on to the others.
#:
#: The read loop drained each ready socket "until it would block", which is
#: correct for a sender that sends one object and stops and catastrophic for a
#: PERMANENT link that never stops: recv never returns EWOULDBLOCK, so the
#: first connection in a pass holds the shard for as long as its sender keeps
#: pumping and every other connection on that shard waits.
#:
#: Measured on the node, 2026-09-05: bulk peaked at 17.36 MB/s -- WORSE than
#: the 28.42 MB/s of the connection-per-object version it replaced -- and
#: links to one peer failed with TimeoutError after 30s having moved 2 to 8
#: objects. Those links were not slow, they were never read.
#:
#: The descriptor stays readable, so epoll hands it straight back on the next
#: pass; the budget costs one extra epoll_wait per busy connection and buys
#: round-robin service across all of them.
DRAIN_BUDGET_BYTES = int(os.environ.get("FROGNET_BULK_DRAIN_BUDGET",
                                        str(1 << 20)))


def _bulk_listener(bind_ip: str, port: int) -> socket.socket:
    """A listening socket that shares its port with the other shards."""
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # SO_REUSEPORT is what makes the sharding work. Without it the second bind
    # fails with EADDRINUSE and there is no sharing, so it is not optional and
    # not caught: a receiver that silently ran one shard would look like this
    # change had simply not helped. [NO FALLBACKS]
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sk.bind((bind_ip, port))
    sk.listen(LISTEN_BACKLOG)
    sk.setblocking(False)
    return sk


def _shard_main(bind_ip, port, shard_id, counters, errbuf, ready, stop_evt):
    """One receiver shard: its own listener, its own selector, its own process.

    [REUSEPORT_SHARDS_V1] Started with the 'spawn' context, NOT forked. A fork
    would inherit this tree's atexit handlers -- frognet_tuples registers
    cleanup_owned() -- so a shard exiting would delete tuples the PARENT wrote.
    A spawned child has none of that.

    The loop's only job is to take work off the wire. accept() is drained until
    it would block rather than taking one connection per readiness pass, and
    recv() likewise, so a burst is absorbed in one pass instead of one pass per
    connection.
    """
    base = shard_id * _SLOTS_PER_SHARD

    def fail(msg):
        try:
            raw = msg.encode("utf-8", "replace")[:_ERRBUF_LEN - 1]
            errbuf.value = raw
        except Exception:
            pass
        print("[AIConnect] BULK SHARD %d FAILED on port %d: %s"
              % (shard_id, port, msg), file=sys.stderr, flush=True)

    try:
        srv = _bulk_listener(bind_ip, port)
    except OSError as e:
        fail("cannot bind shard: %s: %s (errno=%s)"
             % (type(e).__name__, e, e.errno))
        ready.set()
        os._exit(1)

    sel = selectors.DefaultSelector()
    sel.register(srv, selectors.EVENT_READ, None)
    conns = {}
    n_bytes = n_xfers = n_accepts = 0
    ready.set()

    def close_conn(sk, st):
        try:
            sel.unregister(sk)
        except (KeyError, ValueError, OSError):
            pass
        conns.pop(sk.fileno(), None)
        try:
            sk.close()
        except OSError:
            pass

    try:
        while not stop_evt.is_set():
            for key, _mask in sel.select(0.5):
                if key.data is None:
                    # Drain the accept queue. One accept per readiness pass
                    # made the accept rate a function of the poll rate.
                    while True:
                        try:
                            conn, peer = srv.accept()
                        except BlockingIOError:
                            break
                        except OSError as e:
                            if e.errno in (errno.EMFILE, errno.ENFILE):
                                fail("accept() hit the descriptor limit: %s" % e)
                                os._exit(1)
                            break
                        conn.setblocking(False)
                        conn.setsockopt(socket.IPPROTO_TCP,
                                        socket.TCP_NODELAY, 1)
                        st = {"peer": peer[0], "who": None, "buf": b"",
                              "need": 0, "phase": "hdr", "bytes": 0,
                              "objects": 0, "started": time.time()}
                        conns[conn.fileno()] = st
                        sel.register(conn, selectors.EVENT_READ, st)
                        n_accepts += 1
                        counters[base + _SLOT_ACCEPTS] = n_accepts
                        counters[base + _SLOT_CONNS] = len(conns)
                    continue

                sk, st = key.fileobj, key.data
                dead = False
                drained = 0
                while drained < DRAIN_BUDGET_BYTES:
                    try:
                        b = sk.recv(1 << 18)
                    except (BlockingIOError, InterruptedError):
                        break
                    except OSError:
                        dead = True
                        break
                    if not b:
                        dead = True
                        break
                    drained += len(b)

                    # [FRAME_WITHOUT_COPYING_THE_PAYLOAD_V1]
                    # Walk the received block in place. The first cut of this
                    # did `buf += b` and then re-sliced `buf` after every
                    # field, so each payload byte was copied at least twice
                    # through Python -- on a permanent link that is every byte
                    # of the run, and it is the shard's whole budget. Body
                    # bytes are now never copied: they are counted and the
                    # offset moves. Only a partial header or length prefix is
                    # carried, and those are 32 and 8 bytes.
                    pos, blen = 0, len(b)
                    while pos < blen:
                        ph = st["phase"]
                        if ph == "body":
                            take = st["need"]
                            if take > blen - pos:
                                take = blen - pos
                            pos += take
                            st["need"] -= take
                            n_bytes += take
                            st["bytes"] += take
                            if st["need"] == 0:
                                st["objects"] += 1
                                n_xfers += 1
                                st["phase"] = "len"
                            continue

                        want = BULK_HDR_LEN if ph == "hdr" else BULK_LEN_LEN
                        carry = st["buf"]
                        avail = blen - pos
                        need = want - len(carry)
                        if avail < need:
                            st["buf"] = carry + b[pos:]
                            pos = blen
                            break
                        field = carry + b[pos:pos + need]
                        pos += need
                        st["buf"] = b""
                        if ph == "hdr":
                            st["who"] = (field.decode("ascii", "replace")
                                         .rstrip("\x00 ") or st["peer"])
                            st["phase"] = "len"
                        else:
                            (st["need"],) = struct.unpack(BULK_LEN_FMT, field)
                            st["phase"] = "body"

                    counters[base + _SLOT_BYTES] = n_bytes
                    counters[base + _SLOT_XFERS] = n_xfers

                if dead:
                    close_conn(sk, st)
                    counters[base + _SLOT_CONNS] = len(conns)
    except BaseException as e:
        fail("receive loop raised %s: %s" % (type(e).__name__, e))
        os._exit(1)
    finally:
        try:
            sel.close()
            srv.close()
        except Exception:
            pass
    # os._exit, never a normal return: this process must not run interpreter
    # shutdown, whose atexit handlers belong to the tree, not to a shard.
    os._exit(0)


class BulkReceiver:
    """The machine's bulk socket: N SO_REUSEPORT shards in, counters out.

    [REUSEPORT_SHARDS_V1]
    One port, N processes, each with its own listening socket bound with
    SO_REUSEPORT and its own selector loop. The kernel picks which shard gets
    each incoming connection, so there is no shared acceptor to be the
    bottleneck and no GIL between the shards. Adding a shard adds a core.

    What this replaced: one thread running one selector loop that did every
    accept() and every recv() inline, one of each per readiness pass. That
    receiver was a single core regardless of the machine, and the accept rate
    was bounded by how fast one thread could go round its own poll.

    The control plane still sees one port and one set of totals; `transfers`
    and `bytes_received` sum the shards' own counters, each of which has
    exactly one writer.
    """

    def __init__(self, bind_ip: str, port: int = 0, shards: int = 0):
        self.bind_ip = bind_ip
        self.shards = int(shards) or BULK_SHARDS

        # Bind once to learn the port, then hand that port to every shard.
        # This socket is closed as soon as the shards are listening on it;
        # SO_REUSEPORT is set here too, or the shards could not join it.
        probe = _bulk_listener(bind_ip, port)
        self.port = probe.getsockname()[1]

        self._ctx = multiprocessing.get_context("spawn")
        self._counters = self._ctx.Array(
            "q", self.shards * _SLOTS_PER_SHARD, lock=False)
        self._errbuf = self._ctx.Array("c", _ERRBUF_LEN)
        self._stop_evt = self._ctx.Event()
        self._procs = []
        readies = []
        for i in range(self.shards):
            r = self._ctx.Event()
            p = self._ctx.Process(
                target=_shard_main,
                args=(bind_ip, self.port, i, self._counters, self._errbuf,
                      r, self._stop_evt),
                daemon=True, name="bulk-shard-%d-%d" % (self.port, i))
            p.start()
            self._procs.append(p)
            readies.append(r)

        # Hold the probe socket until every shard is listening, so the port
        # cannot be taken by anything else in the gap.
        deadline = time.time() + 30.0
        for i, r in enumerate(readies):
            if not r.wait(max(0.0, deadline - time.time())):
                probe.close()
                raise RuntimeError(
                    "bulk shard %d did not come up within 30s on port %d"
                    % (i, self.port))
        probe.close()

        err = self.error
        if err is not None:
            raise err

        diag("DIAG-RECV", "receiver up", port=self.port, shards=self.shards,
             backlog=LISTEN_BACKLOG)

    # -- totals -----------------------------------------------------------

    def _sum(self, slot: int) -> int:
        base = 0
        total = 0
        for _ in range(self.shards):
            total += self._counters[base + slot]
            base += _SLOTS_PER_SHARD
        return total

    @property
    def bytes_received(self) -> int:
        return self._sum(_SLOT_BYTES)

    @property
    def transfers(self) -> int:
        return self._sum(_SLOT_XFERS)

    @property
    def open_conns(self) -> int:
        return self._sum(_SLOT_CONNS)

    @property
    def accepts(self) -> int:
        return self._sum(_SLOT_ACCEPTS)

    @property
    def error(self):
        """Not None means a shard has died and why. A dead shard is a real
        failure even though the others keep serving: the kernel goes on
        handing it connections until its socket is gone, and a receiver that
        is quietly running at a fraction of its width is the thing this whole
        change exists to stop."""
        raw = bytes(self._errbuf.value)
        if raw:
            return RuntimeError(raw.decode("utf-8", "replace"))
        dead = [p.name for p in self._procs if not p.is_alive()]
        if dead and not self._stop_evt.is_set():
            return RuntimeError("bulk shard(s) exited: %s" % ", ".join(dead))
        return None

    def live_shards(self) -> int:
        return sum(1 for p in self._procs if p.is_alive())

    def stats(self) -> Dict[str, Any]:
        return {"port": self.port, "shards": self.shards,
                "live_shards": self.live_shards(),
                "transfers": self.transfers, "bytes": self.bytes_received,
                "open_conns": self.open_conns, "accepts": self.accepts,
                "error": (str(self.error) if self.error else None)}

    def close(self) -> None:
        self._stop_evt.set()
        for p in self._procs:
            p.join(timeout=3.0)
        for p in self._procs:
            if p.is_alive():
                p.terminate()
                p.join(timeout=2.0)


class AIConnectService:
    def __init__(self, dbhost: str = DATA_PLANE_DBHOST, interval: float = 1.0,
                 bind_ip: Optional[str] = None, timeout_s: float = 30.0):
        if dbhost.startswith("databasehost_control"):
            raise ValueError("refusing the coordination plane %r as an "
                             "application store" % dbhost)
        self.dbhost = dbhost
        self.interval = float(interval)
        self.machine = machine_name()
        self.bind_ip = bind_ip or self.machine
        self.timeout_s = float(timeout_s)
        self.receiver: Optional[BulkReceiver] = None
        self._stop = threading.Event()

    # -- truths this machine asserts --------------------------------------

    def assert_ready(self) -> None:
        ok = put(SERVICE, VAR_READY, scope_for(self.machine, VAR_READY),
                 {"available": 1,
                  "machine": self.machine,
                  "poll_interval_s": self.interval,
                  "cores": os.cpu_count(),
                  "ts": int(time.time())},
                 dbhost=self.dbhost, timeout=self.timeout_s, own=False)
        if not ok:
            raise RuntimeError("could not assert MachineReady for %s to %s"
                               % (self.machine, self.dbhost))

    def assert_bulk_ready(self, port: int) -> None:
        ok = put(SERVICE, VAR_BULK, scope_for(self.machine, VAR_BULK),
                 {"BulkReady": 1 if port else 0, "port": port,
                  "machine": self.machine, "ts": int(time.time())},
                 dbhost=self.dbhost, timeout=self.timeout_s, own=False)
        if not ok:
            raise RuntimeError("could not assert BulkReady for %s" % self.machine)

    def clear_request(self) -> None:
        """Consume the request by zeroing it. There is no take, so the
        consumer marks it handled rather than removing it."""
        put(SERVICE, VAR_REQUEST, scope_for(self.machine, VAR_REQUEST),
            {"want": 0, "ts": int(time.time())},
            dbhost=self.dbhost, timeout=self.timeout_s, own=False)

    # -- what it reads ----------------------------------------------------

    def my_request(self) -> Optional[Dict[str, Any]]:
        """Is anyone asking this machine for a connection?

        [DIAG-SVC] Every row is logged, matched or not, with the scope it
        carries beside the scope being looked for. A scope that disagrees by
        one character produces exactly the same silence as no request at all,
        and the loop above cannot tell those apart.
        """
        want_scope = scope_for(self.machine, VAR_REQUEST)
        rows = get_all(SERVICE, dbhost=self.dbhost, timeout=self.timeout_s)
        seen = 0
        for row in rows:
            seen += 1
            var, scope = row.get("var"), row.get("scope")
            if var != VAR_REQUEST or scope != want_scope:
                diag("DIAG-SVC", "request row SKIPPED", var=var, scope=scope,
                     want_var=VAR_REQUEST, want_scope=want_scope,
                     addr=row.get("addr"), age_s=row.get("age_s"))
                continue
            v = row.get("value")
            inner = v.get("v", v) if isinstance(v, dict) else None
            diag("DIAG-SVC", "request row MATCHED", scope=scope,
                 value=v, want=(inner or {}).get("want"),
                 frm=(inner or {}).get("from"), age_s=row.get("age_s"),
                 ts_env=row.get("ts_env"))
            if isinstance(v, dict) and inner and inner.get("want"):
                return inner
        diag("DIAG-SVC", "no request", rows_seen=seen, want_scope=want_scope)
        return None

    # -- the loop ---------------------------------------------------------

    def run(self) -> int:
        print("[AIConnect] %s: asserting MachineReady, store=%s, interval=%.2fs"
              % (self.machine, self.dbhost, self.interval), flush=True)
        diag("DIAG-SVC", "service start", machine=self.machine,
             dbhost=self.dbhost, bind_ip=self.bind_ip, interval=self.interval,
             timeout_s=self.timeout_s, argv=sys.argv,
             rlimit_nofile=_nofile(), py=sys.version.split()[0])
        last_ready = 0.0
        _it = 0
        while not self._stop.is_set():
            now = time.time()
            _it += 1
            rx = self.receiver
            diag("DIAG-SVC", "loop", it=_it,
                 receiver=("none" if rx is None else "port=%d" % rx.port),
                 rx_transfers=(None if rx is None else rx.transfers),
                 rx_bytes=(None if rx is None else rx.bytes_received),
                 rx_conns=(None if rx is None else rx.open_conns),
                 rx_accepts=(None if rx is None else rx.accepts),
                 rx_shards=(None if rx is None else "%d/%d"
                            % (rx.live_shards(), rx.shards)),
                 rx_error=(None if rx is None else repr(rx.error)),
                 since_ready=round(now - last_ready, 2))

            # Re-assert readiness periodically. A tuple is current state, not an
            # event: if this service dies the row simply stops being refreshed,
            # and a reader judges staleness from the envelope's own clock.
            if now - last_ready >= max(1.0, self.interval * 5):
                try:
                    self.assert_ready()
                    diag("DIAG-SVC", "MachineReady asserted", it=_it)
                except Exception as e:
                    diag_exc("DIAG-SVC", "MachineReady assert FAILED", e,
                             it=_it, dbhost=self.dbhost)
                    raise
                last_ready = now

            req = self.my_request()
            if req and self.receiver is None:
                self.receiver = BulkReceiver(self.bind_ip)
                diag("DIAG-SVC", "receiver opened", port=self.receiver.port,
                     shards=self.receiver.shards, bind_ip=self.bind_ip,
                     requested_by=req.get("from"), req_ts=req.get("ts"))
                self.assert_bulk_ready(self.receiver.port)
                diag("DIAG-SVC", "BulkReady written", port=self.receiver.port,
                     scope=scope_for(self.machine, VAR_BULK))
                self.clear_request()
                diag("DIAG-SVC", "request cleared", it=_it)
            elif req:
                # A request arriving while a receiver already exists is normal
                # (the receiver stays open) -- but it means the requester is
                # about to use the port ALREADY advertised, so what that row
                # says right now is the thing worth seeing.
                diag("DIAG-SVC", "request while receiver open",
                     port=self.receiver.port, requested_by=req.get("from"),
                     bulk_flag_in_store=self._bulk_flag_set())
                print("[AIConnect] %s: primed, bulk port %d (requested by %s)"
                      % (self.machine, self.receiver.port, req.get("from", "?")),
                      flush=True)

            # [THE_RECEIVER_STAYS_OPEN_V1]
            # This used to close the socket and drop the receiver as soon as
            # BulkReady went to 0, on the assumption that a transfer owned it.
            # It does not: BulkReady is a property of the MACHINE. One receiver
            # serves every sender that wants it, for as long as this service
            # runs.
            #
            # Closing per transfer was also a race. Several senders read the
            # same port, one finished and signalled done, the socket went away,
            # and the rest got ConnectionRefused -- which showed up the moment
            # concurrency passed one thread per peer.
            #
            # Nothing here closes it. The port is opened once, advertised once,
            # and stays. The service exiting is what ends it.

            time.sleep(self.interval)
        return 0

    def _bulk_flag_set(self) -> bool:
        want_scope = scope_for(self.machine, VAR_BULK)
        for row in get_all(SERVICE, dbhost=self.dbhost, timeout=self.timeout_s):
            if row.get("var") != VAR_BULK or row.get("scope") != want_scope:
                continue
            v = row.get("value") or {}
            v = v.get("v", v)
            return bool(v.get("BulkReady"))
        return False

    def stop(self) -> None:
        self._stop.set()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="AIConnect resident service")
    p.add_argument("--dbhost", default=DATA_PLANE_DBHOST)
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--bind-ip", default=None)
    p.add_argument("--store-timeout", type=float, default=30.0)
    a = p.parse_args(argv)
    svc = AIConnectService(dbhost=a.dbhost, interval=a.interval,
                           bind_ip=a.bind_ip, timeout_s=a.store_timeout)
    try:
        return svc.run()
    except KeyboardInterrupt:
        svc.stop()
        return 0
    except BaseException as e:
        # [DIAG-SVC] This process exiting takes the bulk receiver with it, and
        # the BulkReady row it wrote survives -- so every producer keeps
        # dialling a port that no longer exists and gets ECONNREFUSED. Say so
        # on the way out, with the state at the moment of death, then re-raise
        # unchanged. [NO FALLBACKS] -- this logs, it does not recover.
        rx = svc.receiver
        diag_exc("DIAG-SVC", "SERVICE DYING -- advertised port dies with it", e,
                 machine=svc.machine, dbhost=svc.dbhost,
                 advertised_port=(None if rx is None else rx.port),
                 rx_transfers=(None if rx is None else rx.transfers))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
