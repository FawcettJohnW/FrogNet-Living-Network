#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""plane_client.py -- reference client for plane_listener.py. Standard library only.

    p = Plane("host:8789")
    p.publish("race42/kart/3", tick, body_bytes)     # send-or-drop: True if it went, False if shed
    rows, after = p.read("race42/kart/", after, wait_ms=100)   # [(name, gen, bytes)], and where I now stand

PUBLISH IS SEND-OR-DROP, as in the Communicator: the socket's send buffer is
small and a state that does not fit RIGHT NOW is dropped, not queued -- the next
one is newer anyway. Nothing is ever stockpiled, so what arrives is current.
A frame that has started is always finished, so framing cannot be lost.
"""
import select, socket, struct, threading

OP_PUB, OP_READ, OP_DROP, OP_DATA, OP_NONE = 0x01, 0x02, 0x03, 0x11, 0x12
WAIT_EXPIRED, NEVER_PUBLISHED = 1, 2
SNDBUF = 16 * 1024            # the buffer IS the latency: keep it small


class PlaneGone(RuntimeError):
    pass


class Plane:
    def __init__(self, where, timeout=10.0):
        h, p = where.rsplit(":", 1)
        self.s = socket.create_connection((h, int(p)), timeout)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SNDBUF)
        self.s.settimeout(None)
        self.wlock, self.plock = threading.Lock(), threading.Lock()
        self.pending, self.rid, self.dead = {}, 0, None
        self.sent = self.shed = 0
        threading.Thread(target=self._reader, daemon=True).start()

    def _rx(self, n):
        d = b""
        while len(d) < n:
            c = self.s.recv(n - len(d))
            if not c:
                raise ConnectionError("plane closed the connection")
            d += c
        return d

    def _reader(self):
        try:
            while True:
                f = self._rx(struct.unpack("!I", self._rx(4))[0])
                rid = struct.unpack("!I", f[1:5])[0]
                with self.plock:
                    slot = self.pending.pop(rid, None)
                if slot:
                    slot[1] = f; slot[0].set()
        except Exception as e:
            self.dead = PlaneGone("%s: %s" % (type(e).__name__, e))
            with self.plock:
                for slot in self.pending.values():
                    slot[0].set()

    def publish(self, name, gen, data):
        if self.dead:
            raise self.dead
        nb = name.encode()
        body = bytes([OP_PUB]) + struct.pack("!QH", gen, len(nb)) + nb + data
        frame = struct.pack("!I", len(body)) + body
        with self.wlock:
            if not select.select([], [self.s], [], 0)[1]:       # not writable NOW: shed it
                self.shed += 1
                return False
            self.s.sendall(frame)                                # started, so finished
            self.sent += 1
            return True

    def drop(self, name):
        nb = name.encode(); body = bytes([OP_DROP]) + struct.pack("!H", len(nb)) + nb
        with self.wlock:
            self.s.sendall(struct.pack("!I", len(body)) + body)

    def read(self, prefix, after=0, wait_ms=0):
        """(rows, new_after). rows: every name under prefix published since
        `after`, in the plane's arrival order, as (name, gen, bytes). `after` is
        the plane's own sequence -- pass back what this returned. ([], after)
        means not yet. Raises KeyError if nothing has ever been published under
        the prefix and no wait was asked for."""
        if self.dead:
            raise self.dead
        nb = prefix.encode()
        slot = [threading.Event(), None]
        with self.plock:
            self.rid = (self.rid + 1) & 0xFFFFFFFF; rid = self.rid
            self.pending[rid] = slot
        body = bytes([OP_READ]) + struct.pack("!IQIH", rid, after, wait_ms, len(nb)) + nb
        with self.wlock:
            self.s.sendall(struct.pack("!I", len(body)) + body)
        if not slot[0].wait(wait_ms / 1000.0 + 10.0) or slot[1] is None:
            raise self.dead or PlaneGone("no reply to read %d" % rid)
        f = slot[1]
        if f[0] == OP_NONE:
            if f[5] == NEVER_PUBLISHED:
                raise KeyError("nothing has been published under %r" % prefix)
            return [], after
        count = struct.unpack("!H", f[5:7])[0]; off = 7; out = []
        for _ in range(count):
            seq, gen, nl = struct.unpack("!QQH", f[off:off + 18]); off += 18
            after = max(after, seq)
            name = f[off:off + nl].decode(); off += nl
            bl = struct.unpack("!I", f[off:off + 4])[0]; off += 4
            out.append((name, gen, f[off:off + bl])); off += bl
        return out, after
