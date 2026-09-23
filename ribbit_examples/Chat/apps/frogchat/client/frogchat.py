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
"""frogchat.py -- P2P chat through shared memory. Applied FrogNet RAM.

    frogchat --me Dave --to Bob                      (installed: memory address from store.json)
    python frogchat.py --store HOST:8788 --me Dave --to Bob

There is no chat server and nobody sends anybody anything.

  SEND   waits for me to hit Return, then WRITES the line to
             ChatServer.<recipient>.<me>       one cell per recipient
  READ   sits in a BLOCKING READ on my own buffer,
             ChatServer.<me>.*
         and wakes when anyone writes into it.
  HERE   I am present because my cell ChatPresence.here.<me> is fresh. The
         reader rewrites it each time its read returns (at most every 25 s), so
         presence needs no thread of its own. /list is one read of
         ChatPresence.here.*; someone who vanishes simply goes stale, and
         someone who leaves removes their own cell.

Each sender has their own cell in my buffer, so two people writing to me at
the same moment do not overwrite each other. The JSON in a cell says who it is
from, who else it went to, when, and what: {from, to, ts, text}.

  /list                    who is here
  /Dave hello              say it to Dave
  /Dave /Alice hello       say it to Dave and to Alice
  hello again              say it to whoever I last addressed
  /help   /quit

Two threads: one reads the terminal and writes; one reads the buffer and prints.

The memory is reached the production way and no other: FNW1 to the service's
listener (two outbound connections, HELLO and HELLO RETURN:<token>, replies
tagged with the implicit sequence number and matched by it, so a read held
open for 25 seconds does not stop a write going out and coming back). There is
no HTTP port to the memory and this program does not look for one.

NO FALLBACKS. One store. If it cannot be reached or says no, that is raised.

Pure standard library. Python 3.8+. Windows and Linux.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.parse
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional

MAGIC = b"FNW1"
OP_REQ_REPEAT, OP_REQ_RAW = 0x02, 0x03
OP_RESP_SAME, OP_RESP_RAW, OP_REQ_MISS, OP_ERROR, OP_HELLO = 0x13, 0x14, 0x21, 0x30, 0x50
SAME_MAX = 256                                   # bodies kept to answer RESP_SAME from
ORIGIN_HOST, ORIGIN_PORT = "127.0.0.1", 8080     # the memory, on the daemon's loopback
WAIT_S = 25.0                                    # the memory caps a held read at 30
_DIAG = bool(os.environ.get("FROGCHAT_DIAG"))


def _diag(msg: str) -> None:
    if _DIAG:
        print("[DIAG-FROGCHAT] t=%.3f thr=%s %s"
              % (time.time(), threading.current_thread().name, msg), file=sys.stderr, flush=True)


class StoreUnreachable(RuntimeError):
    """No daemon answered, or the connection to it was lost."""


class StoreRefused(RuntimeError):
    """The daemon or the memory answered, and said no."""


# --------------------------------------------------------------------------
# Session: the client end of the wire.
# --------------------------------------------------------------------------
class _Pending:
    __slots__ = ("done", "frame", "err")

    def __init__(self):
        self.done = threading.Event()
        self.frame: Optional[bytes] = None
        self.err: Optional[BaseException] = None


class Session:
    def __init__(self, store: str, connect_timeout: float = 10.0):
        host, _, port = store.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError("store must be HOST:PORT")
        self.host, self.port = host, int(port)
        self.token = "frogchat-" + uuid.uuid4().hex[:16]      # an identity, never an address
        self._send_lock = threading.Lock()
        self._pending: Dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._seq = 0
        self._dead: Optional[BaseException] = None
        # The semantic cache, memory only: which requests the far end holds,
        # and the body each same_id names. Errors are never kept.
        self._seen: Dict[bytes, bytes] = {}                  # req_hash -> same_id
        self._bodies: "OrderedDict[bytes, Dict[str, Any]]" = OrderedDict()
        self._cache_lock = threading.Lock()
        self.stats = {"raw": 0, "repeat": 0, "same": 0, "miss": 0, "bytes_out": 0, "bytes_in": 0}
        try:
            self._req = socket.create_connection((self.host, self.port), connect_timeout)
            self._send(self._req, self._hello(self.token))
            self._ret = socket.create_connection((self.host, self.port), connect_timeout)
            self._send(self._ret, self._hello("RETURN:" + self.token))
            self._ret.settimeout(connect_timeout)
            first = self._recv(self._ret)
        except OSError as e:
            raise StoreUnreachable("%s:%d %s: %s" % (self.host, self.port, type(e).__name__, e))
        if first[:4] != MAGIC or first[4] != OP_HELLO:
            raise StoreRefused("expected the daemon's HELLO, got %r" % first[:16])
        self._req.settimeout(None)
        self._ret.settimeout(None)
        for s in (self._req, self._ret):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=self._read_loop, name="fnw1-reader", daemon=True).start()
        _diag("session up token=%s" % self.token)

    # -- framing -------------------------------------------------------------
    @staticmethod
    def _hello(text: str) -> bytes:
        b = text.encode("ascii")
        return MAGIC + bytes([OP_HELLO, len(b)]) + b

    @staticmethod
    def _send(s: socket.socket, frame: bytes) -> None:
        s.sendall(struct.pack("!I", len(frame)) + frame)

    @staticmethod
    def _recv(s: socket.socket) -> bytes:
        def rx(n: int) -> bytes:
            d = b""
            while len(d) < n:
                c = s.recv(n - len(d))
                if not c:
                    raise ConnectionError("daemon closed the connection")
                d += c
            return d
        return rx(struct.unpack("!I", rx(4))[0])

    # -- replies arrive in any order, tagged [seq:4] ---------------------------
    def _read_loop(self) -> None:
        try:
            while True:
                f = self._recv(self._ret)
                seq = struct.unpack("!I", f[:4])[0]
                with self._pending_lock:
                    p = self._pending.pop(seq, None)
                if p is None:
                    raise StoreRefused("reply tagged seq %d, which was never sent" % seq)
                p.frame = f[4:]
                p.done.set()
        except BaseException as e:
            self._dead = e if isinstance(e, StoreRefused) else StoreUnreachable(
                "connection to %s:%d lost: %s: %s" % (self.host, self.port, type(e).__name__, e))
            with self._pending_lock:
                waiting, self._pending = list(self._pending.values()), {}
            for p in waiting:
                p.err = self._dead
                p.done.set()

    def _exchange(self, frame: bytes, timeout: float) -> bytes:
        if self._dead:
            raise self._dead
        p = _Pending()
        with self._send_lock:                 # the seq IS the order frames leave in
            seq = self._seq
            self._seq += 1
            with self._pending_lock:
                self._pending[seq] = p
            try:
                self._send(self._req, frame)
            except OSError as e:
                raise StoreUnreachable("send to %s:%d failed: %s" % (self.host, self.port, e))
        if not p.done.wait(timeout):
            raise StoreUnreachable("no reply to seq %d within %.0f s" % (seq, timeout))
        if p.err:
            raise p.err
        self.stats["bytes_out"] += len(frame)
        self.stats["bytes_in"] += len(p.frame)
        if p.frame[:4] != MAGIC:
            raise StoreRefused("reply is not FNW1: %r" % p.frame[:16])
        return p.frame

    def call(self, method: str, path: str, body: str = "", timeout: float = 15.0) -> Dict[str, Any]:
        """One request to the memory; blocks this thread only.

        A request the far end already holds goes as REQ_REPEAT, 21 bytes. If
        the answer has not changed it comes back as RESP_SAME, 21 bytes, and is
        served from the body kept here. REQ_MISS (the far end forgot) and a
        RESP_SAME naming a body no longer kept are both corrected the same way:
        ask the same source again, in full."""
        http = json.dumps({"method": method, "path": path,
                           "headers": {"Content-Type": "application/json"} if body else {},
                           "body": body, "host": ORIGIN_HOST, "port": ORIGIN_PORT},
                          separators=(",", ":")).encode("utf-8")
        h = hashlib.sha256(http).digest()[:16]
        with self._cache_lock:
            repeat = h in self._seen and self._seen[h] in self._bodies
        for _ in (0, 1):
            if repeat:
                self.stats["repeat"] += 1
                f = self._exchange(MAGIC + bytes([OP_REQ_REPEAT]) + h, timeout)
            else:
                self.stats["raw"] += 1
                f = self._exchange(MAGIC + bytes([OP_REQ_RAW]) + h + struct.pack("!I", len(http)) + http, timeout)
            op = f[4]
            if op == OP_REQ_MISS:
                self.stats["miss"] += 1
                with self._cache_lock:
                    self._seen.pop(h, None)
                repeat = False
                continue
            if op == OP_RESP_SAME:
                with self._cache_lock:
                    kept = self._bodies.get(f[5:21])
                    if kept is not None:
                        self._bodies.move_to_end(f[5:21])
                if kept is None:
                    with self._cache_lock:
                        self._seen.pop(h, None)
                    repeat = False
                    continue
                self.stats["same"] += 1
                return kept
            break
        if op == OP_ERROR:
            raise StoreRefused("far end: %d %s" % (struct.unpack("!H", f[5:7])[0], f[7:200].decode("utf-8", "replace")))
        if op != OP_RESP_RAW:
            raise StoreRefused("far end answered op %#x" % op)
        same_id = f[5:21]
        plen = struct.unpack("!I", f[21:25])[0]
        payload = f[25:25 + plen]
        status, hlen = struct.unpack("!HH", payload[:4])
        raw = payload[4 + hlen:]
        try:
            obj = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise StoreRefused("HTTP %d, body is not JSON: %r" % (status, raw[:200]))
        if status >= 400 or obj.get("ok") is not True:
            raise StoreRefused("HTTP %d: %s" % (status, obj.get("error", raw[:200])))
        with self._cache_lock:
            self._seen[h] = same_id
            self._bodies[same_id] = obj
            self._bodies.move_to_end(same_id)
            while len(self._bodies) > SAME_MAX:
                self._bodies.popitem(last=False)
        return obj


# --------------------------------------------------------------------------
# Memory: cells addressed by (service, variable, instance).
# --------------------------------------------------------------------------
class Memory:
    def __init__(self, session: Session):
        self.s = session

    def write(self, service: str, variable: str, instance: str, bag: Dict[str, Any]) -> int:
        o = self.s.call("POST", "/ram.php?op=write",
                        json.dumps({"service": service, "variable": variable,
                                    "instance": instance, "bag": bag}))
        return int(o["id"])

    def read(self, service: str, variable: str, instance: Optional[str] = None, *,
             after: Optional[int] = None, wait_s: float = 0.0) -> List[Dict[str, Any]]:
        """What is there now, in the memory's write order. instance=None leaves
        the third coordinate open: every instance of the variable. With `after`
        and `wait_s` the read is held open by the memory until something has
        been written since `after`; an empty list means not yet."""
        q = "/ram.php?op=read&service=%s&variable=%s" % (
            urllib.parse.quote(service, safe=""), urllib.parse.quote(variable, safe=""))
        if instance is not None:
            q += "&instance=" + urllib.parse.quote(instance, safe="")
        if after is not None:
            q += "&after=%d" % after
        if wait_s > 0:
            q += "&wait_s=%.3f" % wait_s
        return self.s.call("GET", q, timeout=15.0 + wait_s)["rows"]


    def remove(self, cell_id: int) -> None:
        self.s.call("DELETE", "/ram.php?op=remove&id=%d" % int(cell_id))


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------
SERVICE = "ChatServer"
PRESENCE, HERE = "ChatPresence", "here"
PRESENCE_FRESH_S = 60                    # two missed rewrites and I am gone
COMMANDS = ("list", "quit", "help")


def parse_line(line: str):
    """'/Dave /Alice hi there' -> (['Dave', 'Alice'], 'hi there').
    '/list' -> ('list', ''). 'plain text' -> ([], 'plain text')."""
    words = line.strip().split(" ")
    names: List[str] = []
    while words and words[0].startswith("/") and len(words[0]) > 1:
        names.append(words.pop(0)[1:])
    text = " ".join(words).strip()
    if len(names) == 1 and names[0].lower() in COMMANDS and not text:
        return names[0].lower(), ""
    return names, text


class Chat:
    def __init__(self, mem: Memory, me: str, to: Optional[List[str]] = None):
        if me.lower() in COMMANDS or not me or "/" in me or " " in me:
            raise ValueError("%r cannot be used as a name" % me)
        self.mem, self.me = mem, me
        self.to: List[str] = list(to or [])          # who a plain line goes to
        self.stop = threading.Event()
        # Where my buffer stands now. Whatever was written before I arrived is
        # not a new message to me.
        rows = mem.read(SERVICE, me)
        self.after = max((int(r["id"]) for r in rows), default=0)
        self.announce()

    # -- write what I know -------------------------------------------------
    def announce(self) -> None:
        self.mem.write(PRESENCE, HERE, self.me, {"name": self.me})

    def say(self, text: str, to: Optional[List[str]] = None) -> List[str]:
        """Write the line into each recipient's buffer. Returns who it went to."""
        if to:
            self.to = list(to)
        if not self.to:
            raise ValueError("nobody addressed yet: start the line with /Name")
        bag = {"from": self.me, "to": self.to, "ts": time.time(), "text": text}
        for name in self.to:
            self.mem.write(SERVICE, name, self.me, bag)
        return self.to

    # -- read what is there ------------------------------------------------
    def who(self) -> List[str]:
        rows = self.mem.s.call(
            "GET", "/ram.php?op=read&service=%s&variable=%s&fresh_s=%d" % (PRESENCE, HERE, PRESENCE_FRESH_S))["rows"]
        return sorted((str(r["bag"].get("name", r["instance"])) for r in rows), key=str.lower)

    def next_messages(self, wait_s: float = WAIT_S) -> List[Dict[str, Any]]:
        """Block on ChatServer.<me>.* until anyone writes into it, or wait_s passes."""
        rows = self.mem.read(SERVICE, self.me, after=self.after, wait_s=wait_s)
        if rows:
            self.after = max(int(r["id"]) for r in rows)
        return [r["bag"] for r in rows]

    def read_loop(self, show) -> None:
        while not self.stop.is_set():
            for m in self.next_messages():
                show(m)
            self.announce()                   # the read came back: I am still here

    def leave(self) -> None:
        """Remove my own presence cell. My cell, my cleanup."""
        self.stop.set()
        for r in self.mem.read(PRESENCE, HERE, self.me):
            self.mem.remove(r["id"])


HELP = "/list   /Name message   /Name /Other message   plain line = same people as last time   /quit"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="P2P chat through shared memory")
    ap.add_argument("--store", help="HOST:PORT of this application's memory "
                                    "(default: the address install.py recorded in store.json)")
    ap.add_argument("--me", required=True)
    ap.add_argument("--to", action="append", default=[], help="who a plain line goes to (repeatable)")
    a = ap.parse_args(argv)
    if not a.store:
        conf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "store.json")
        try:
            a.store = json.load(open(conf))["store"]
        except (OSError, ValueError, KeyError) as e:
            print("no --store given and %s is not usable: %s" % (conf, e), file=sys.stderr)
            return 2
    try:
        chat = Chat(Memory(Session(a.store)), a.me, a.to)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    except (StoreUnreachable, StoreRefused) as e:
        print("cannot use the memory at %s: %s" % (a.store, e), file=sys.stderr)
        return 2

    def show(m: Dict[str, Any]) -> None:
        when = time.strftime("%H:%M:%S", time.localtime(float(m.get("ts", 0))))
        others = [n for n in (m.get("to") or []) if n.lower() != chat.me.lower()]
        also = (" +" + ",".join(others)) if others else ""
        print("[%s%s %s] %s" % (m.get("from", "?"), also, when, m.get("text", "")), flush=True)

    fatal: List[BaseException] = []

    def reader():
        try:
            chat.read_loop(show)
        except BaseException as e:
            fatal.append(e)

    threading.Thread(target=reader, name="reader", daemon=True).start()
    print("%s on %s.  %s" % (a.me, a.store, HELP), flush=True)
    try:
        while not fatal:
            try:
                line = input()
            except EOFError:
                break
            if not line.strip():
                continue
            names, text = parse_line(line)
            if names == "quit":
                break
            if names == "help":
                print(HELP, flush=True)
            elif names == "list":
                here = chat.who()
                print("here: " + ", ".join(n + (" (you)" if n.lower() == chat.me.lower() else "") for n in here), flush=True)
            elif not text:
                chat.to = list(names)
                print("now talking to " + ", ".join(chat.to), flush=True)
            else:
                try:
                    chat.say(text, names)
                except ValueError as e:
                    print(str(e), flush=True)
                    continue
                if names:
                    absent = [n for n in names if n.lower() not in [h.lower() for h in chat.who()]]
                    if absent:
                        print("(%s not here right now; it is in their buffer)" % ", ".join(absent), flush=True)
    except KeyboardInterrupt:
        pass
    except (StoreUnreachable, StoreRefused) as e:
        fatal.append(e)
    rc = 0
    if fatal:
        print("memory failure: %s: %s" % (type(fatal[0]).__name__, fatal[0]), file=sys.stderr)
        rc = 1
    else:
        try:
            chat.leave()
        except (StoreUnreachable, StoreRefused) as e:
            print("could not remove my presence cell: %s" % e, file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
