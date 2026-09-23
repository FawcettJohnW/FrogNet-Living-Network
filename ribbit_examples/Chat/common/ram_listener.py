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
################################################################
"""ram_listener.py -- the comms for ONE service's FrogNet RAM, and nothing else.

    python3 ram_listener.py --listen 0.0.0.0:8788 --origin 127.0.0.1:8080

This is not a FrogNet node and not a general proxy/daemon. It resolves no
destinations, forwards to no other machine, elects nothing, publishes nothing,
learns nothing and needs no database of its own. It is the far end of the wire
for this service: it speaks FNW1 to clients and hands their requests to this
service's memory API on loopback. One file, standard library only.

THE WIRE (core/semcache_wire.py's layouts; frames are [len:4 BE][FNW1][op]...)
  HELLO <token>            request socket; the session is parked under the token
  HELLO RETURN:<token>     return socket; replies to the session go out here,
                           after this end's own HELLO
  REQ_RAW    0x03  [hash:16][len:4][serialized HTTP request]
  REQ_REPEAT 0x02  [hash:16]                          21 bytes
  RESP_RAW   0x14  [same_id:16][len:4][status:2][hlen:2][headers][body]
  RESP_SAME  0x13  [same_id:16]                       21 bytes
  REQ_MISS   0x21  [hash:16]        "I do not hold that request; send it whole"
  ERROR      0x30  [status:2][message]
Request frames carry no sequence number. This end counts the frames it receives
on a session from 0 and tags every reply [seq:4 BE].

REQUESTS ON A SESSION ARE EXECUTED IN THE ORDER THEY ARRIVE. A client that does
not wait for acknowledgements sends write after write down one connection, and
"writing replaces" only means something if the later write is applied later.
(Found by tools/mesh.cpp: executing each request on its own thread let an older
write land after a newer one -- 636 regressions in 7,290 writes.) The one
exception is a read that asks to be HELD (wait_s): it is parked on its own
thread so it does not hold up the writes behind it, and its reply leaves
whenever it finishes. Order among writes is the contract; a held read has no
order to keep.

REPEAT always re-executes -- "repeat" describes the request, not the work. The
new body is hashed; equal to last time is RESP_SAME, 21 bytes, otherwise the
whole reply. The request cache is memory only and bounded; a restart makes
every REPEAT a MISS until the client re-sends, which is the designed correction.
An error reply is never remembered ([NO_SAME_FROM_AN_EMPTY_ARRAY_V1]'s reason:
a remembered failure becomes the permanent answer).

PINNED ORIGIN. The host and port a client names in its frame are ignored: every
request goes to --origin, and only to paths under --path-prefix. A client on
the Internet does not choose where this machine connects.

NO FALLBACKS. If the origin cannot be reached the client is told 502; nothing
is substituted and nothing is served from memory in its place.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import socket
import struct
import sys
import threading
import time
from collections import OrderedDict
from typing import Dict, Optional, Tuple

MAGIC = b"FNW1"
OP_REQ_REPEAT, OP_REQ_RAW = 0x02, 0x03
OP_RESP_SAME, OP_RESP_RAW = 0x13, 0x14
OP_REQ_MISS, OP_ERROR, OP_HELLO = 0x21, 0x30, 0x50
HASH_LEN = 16
MAX_FRAME = 1 << 20          # a public port does not read unbounded lengths
CACHE_MAX = 4096
ORIGIN_TIMEOUT_S = 40.0      # the memory may hold a read open for 30
RETURN_WAIT_S = 10.0


def log(msg: str) -> None:
    print("[RAM-LISTENER] %s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def rx(s: socket.socket, n: int) -> bytes:
    d = b""
    while len(d) < n:
        c = s.recv(n - len(d))
        if not c:
            raise ConnectionError("peer closed")
        d += c
    return d


def recv_frame(s: socket.socket) -> bytes:
    n = struct.unpack("!I", rx(s, 4))[0]
    if n < 5 or n > MAX_FRAME:
        raise ValueError("frame length %d out of bounds" % n)
    f = rx(s, n)
    if f[:4] != MAGIC:
        raise ValueError("not FNW1")
    return f


def send_frame(s: socket.socket, payload: bytes) -> None:
    s.sendall(struct.pack("!I", len(payload)) + payload)


def hello(text: str) -> bytes:
    b = text.encode("ascii")
    return MAGIC + bytes([OP_HELLO, len(b)]) + b


def err(status: int, msg: str) -> bytes:
    return MAGIC + bytes([OP_ERROR]) + struct.pack("!H", status) + msg.encode("utf-8", "replace")


class Listener:
    def __init__(self, origin: Tuple[str, int], prefix: str):
        self.origin, self.prefix = origin, prefix
        self.pending: Dict[str, "Session"] = {}
        self.pending_lock = threading.Lock()
        # req_hash -> (serialized request, sha256 of last body, same_id)
        self.cache: "OrderedDict[bytes, Tuple[bytes, bytes, bytes]]" = OrderedDict()
        self.cache_lock = threading.Lock()
        self.local = threading.local()

    # -- the one place this machine connects to ------------------------------
    def execute(self, http_req: bytes) -> Tuple[int, bytes, bytes]:
        q = json.loads(http_req.decode("utf-8"))
        method, path = str(q.get("method", "GET")), str(q.get("path", "/"))
        if not path.startswith(self.prefix):
            return 403, b"{}", json.dumps({"ok": False, "error": "not this service's API"}).encode()
        body = str(q.get("body") or "").encode("utf-8")
        hdrs = {k: v for k, v in (q.get("headers") or {}).items() if k.lower() != "host"}
        for attempt in (0, 1):
            c = getattr(self.local, "conn", None)
            reused = c is not None
            if c is None:
                c = self.local.conn = http.client.HTTPConnection(*self.origin, timeout=ORIGIN_TIMEOUT_S)
            try:
                c.request(method, path, body=body or None, headers=hdrs)
                r = c.getresponse()
                data = r.read()
                return r.status, json.dumps(dict(r.getheaders())).encode(), data
            except (OSError, http.client.HTTPException) as e:
                try:
                    c.close()
                finally:
                    self.local.conn = None
                if attempt == 0 and reused:
                    continue                     # a kept connection the origin closed: reopen once
                return 502, b"{}", json.dumps({"ok": False, "error": "memory API unreachable: %s" % type(e).__name__}).encode()
        raise AssertionError("unreachable")

    def is_held_read(self, frame: bytes) -> bool:
        """Does this request ask the memory to hold it open?"""
        if frame[4] == OP_REQ_RAW:
            return b"wait_s=" in frame[25:]
        if frame[4] == OP_REQ_REPEAT:
            with self.cache_lock:
                old = self.cache.get(frame[5:5 + HASH_LEN])
            return old is not None and b"wait_s=" in old[0]
        return False

    def answer(self, frame: bytes) -> bytes:
        op, h = frame[4], frame[5:5 + HASH_LEN]
        if len(h) != HASH_LEN:
            return err(400, "short frame")
        if op == OP_REQ_RAW:
            n = struct.unpack("!I", frame[21:25])[0]
            http_req = frame[25:25 + n]
            if len(http_req) != n:
                return err(400, "short REQ_RAW")
            old = None
        elif op == OP_REQ_REPEAT:
            with self.cache_lock:
                old = self.cache.get(h)
                if old is not None:
                    self.cache.move_to_end(h)
            if old is None:
                return MAGIC + bytes([OP_REQ_MISS]) + h
            http_req = old[0]
        else:
            return err(400, "op %#x is not spoken here" % op)
        try:
            status, hdrs, body = self.execute(http_req)
        except (ValueError, KeyError) as e:
            return err(400, "bad request: %s" % e)
        body_hash = hashlib.sha256(struct.pack("!H", status) + body).digest()
        if old is not None and old[1] == body_hash:
            return MAGIC + bytes([OP_RESP_SAME]) + old[2]
        same_id = hashlib.sha256(h + body_hash).digest()[:16]
        with self.cache_lock:
            if status < 400:
                self.cache[h] = (http_req, body_hash, same_id)
                self.cache.move_to_end(h)
                while len(self.cache) > CACHE_MAX:
                    self.cache.popitem(last=False)
            else:
                self.cache.pop(h, None)
        payload = struct.pack("!HH", status, len(hdrs)) + hdrs + body
        return MAGIC + bytes([OP_RESP_RAW]) + same_id + struct.pack("!I", len(payload)) + payload

    # -- connections ---------------------------------------------------------
    def handle(self, conn: socket.socket, addr) -> None:
        try:
            for opt in ((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1), (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)):
                conn.setsockopt(*opt)
            conn.settimeout(5.0)
            f = recv_frame(conn)
            conn.settimeout(None)
            if f[4] != OP_HELLO:
                raise ValueError("first frame is not HELLO")
            token = f[6:6 + f[5]].decode("ascii")
        except Exception as e:
            log("refused %s: %s" % (addr[0], e))
            conn.close()
            return
        if token.startswith("RETURN:"):
            token = token[7:]
            deadline = time.time() + RETURN_WAIT_S
            while True:
                with self.pending_lock:
                    sess = self.pending.pop(token, None)
                if sess or time.time() > deadline:
                    break
                time.sleep(0.05)
            if not sess:
                log("RETURN for an unknown session from %s" % addr[0])
                conn.close()
                return
            sess.attach(conn)
            return
        sess = Session(self, conn, addr[0], token)
        with self.pending_lock:
            self.pending[token] = sess
        sess.run()
        with self.pending_lock:
            self.pending.pop(token, None)


class Session:
    def __init__(self, lst: Listener, req: socket.socket, peer: str, token: str):
        self.lst, self.req, self.peer, self.token = lst, req, peer, token
        self.ret: Optional[socket.socket] = None
        self.ready = threading.Event()
        self.send_lock = threading.Lock()

    def attach(self, ret: socket.socket) -> None:
        self.ret = ret
        send_frame(ret, hello("ram"))
        self.ready.set()

    def reply(self, seq: int, frame: bytes) -> None:
        tagged = struct.pack("!I", seq) + self.lst.answer(frame)
        try:
            with self.send_lock:
                send_frame(self.ret, tagged)
        except OSError:
            pass                                  # the reader loop will see the session end

    def run(self) -> None:
        if not self.ready.wait(RETURN_WAIT_S):
            log("session %s from %s never opened its return channel" % (self.token, self.peer))
            self.req.close()
            return
        log("session up   %s %s" % (self.peer, self.token))
        seq = 0
        try:
            while True:
                frame = recv_frame(self.req)
                if self.lst.is_held_read(frame):
                    threading.Thread(target=self.reply, args=(seq, frame), daemon=True).start()
                else:
                    self.reply(seq, frame)            # in arrival order, on this thread
                seq += 1
        except Exception as e:
            log("session down %s %s after %d requests (%s)" % (self.peer, self.token, seq, type(e).__name__))
        finally:
            for s in (self.req, self.ret):
                try:
                    s.close()
                except OSError:
                    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="FNW1 listener for one service's FrogNet RAM")
    ap.add_argument("--listen", default="0.0.0.0:8788")
    ap.add_argument("--origin", default="127.0.0.1:8080", help="this service's memory API (loopback)")
    ap.add_argument("--path-prefix", default="/ram.php")
    a = ap.parse_args()
    lh, lp = a.listen.rsplit(":", 1)
    oh, op_ = a.origin.rsplit(":", 1)
    lst = Listener((oh, int(op_)), a.path_prefix)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # The buffer is the latency: accepted sockets inherit this. A writer that does not
    # wait for acknowledgements queues its writes HERE when it outruns the memory; a
    # small buffer turns that into back-pressure on the writer instead of a backlog.
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024)
    srv.bind((lh, int(lp)))
    srv.listen(1024)
    log("listening on %s, memory API at %s%s" % (a.listen, a.origin, a.path_prefix))
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=lst.handle, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
