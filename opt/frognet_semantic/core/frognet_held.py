#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
frognet_held.py - [HELD_READ_IN_API_V1 - John 2026-11-07] the holder's side of a held read.

A held read is a core function of api.php. This process holds a read on a tuple or a
family at the role's address; api.php registers it and answers with the current rows.
The next completed update that matches fires it, once, to this node's port 80
(held_fire.php), which hands it to this process's datagram socket,
/run/frognet/held/<hid>.sock. Then this process holds again, and that reply is the
current value. A cell holds one value: there is nothing to catch up on.

One thread per process waits for every fire and for the /etc/hosts event (rule: one
thread waits for all triggers). When the merge rewrites /etc/hosts the roles may have
moved, and every hold on the old address ended with it: each pending wait is woken
and returns what it has, and the caller holds again at the role's new address. A fire that never comes (the role host
went away without a hosts event) is not chased: the wait ends at the caller's own
bound, and the next event re-registers. No polling anywhere, no fallback.

    rows = wait_rows("communicator", "databasehost.frognet", name_like="caps.%",
                     min_rows=3, wait_s=30)

Holding needs a node: this node's port 80 must serve held_fire.php, and Linux
datagram sockets. A machine without them cannot hold, and Holder() raises.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import re
import select
import socket
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

BUILD = "frognet_held 20261107-held1 [HELD_READ_IN_API_V1]"
HELD_DIR = os.environ.get("FROGNET_HELD_DIR", "/run/frognet/held")
HOSTS = os.environ.get("FROGNET_HOSTS", "/etc/hosts")


class HoldError(Exception):
    """The role host would not take the hold (HTTP error, bad reply, unreachable)."""


def _hid() -> str:
    prog = os.path.basename(sys.argv[0] or "python") or "python"
    return re.sub(r"[^A-Za-z0-9._-]", "_", prog)[:48] + "." + str(os.getpid())


def _source_ip_toward(host: str) -> str:
    """The address this node reaches `host` from -- the one its port 80 answers on."""
    ip = socket.gethostbyname(host.rsplit(":", 1)[0] if host.count(":") == 1 else host)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((ip, 80))                     # no packet is sent: this only picks the route
        return s.getsockname()[0]
    finally:
        s.close()


class _Inotify:
    """inotify on one directory (libc, no module)."""
    _MASK = 0x8 | 0x80 | 0x100 | 0x200 | 0x40   # CLOSE_WRITE MOVED_TO CREATE DELETE MOVED_FROM

    def __init__(self, d: str):
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        self.fd = libc.inotify_init1(0o2000000)
        if self.fd < 0 or libc.inotify_add_watch(self.fd, d.encode(), self._MASK) < 0:
            raise OSError(ctypes.get_errno(), "inotify on " + d)

    def names(self) -> List[str]:
        data, out, i = os.read(self.fd, 65536), [], 0
        while i + 16 <= len(data):
            _wd, _mask, _ck, ln = struct.unpack_from("iIII", data, i)
            out.append(data[i + 16:i + 16 + ln].rstrip(b"\0").decode(errors="replace"))
            i += 16 + ln
        return out


class _Wait:
    def __init__(self):
        self.ev = threading.Event()
        self.fire: Optional[Dict[str, Any]] = None
        self.moved = False


class Holder:
    """This process's holds. One socket, one thread, for all of them."""

    def __init__(self):
        if not hasattr(socket, "AF_UNIX"):
            raise HoldError("held reads need Linux datagram sockets; this machine has none")
        self.hid = _hid()
        if not os.path.isdir(HELD_DIR):         # etc/tmpfiles.d/frognet-held.conf makes it at boot
            os.makedirs(HELD_DIR, exist_ok=True)
            os.chmod(HELD_DIR, 0o1777)          # every holder (root's daemons, the web server's door) binds here
        self.path = os.path.join(HELD_DIR, self.hid + ".sock")
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(self.path)
        os.chmod(self.path, 0o666)              # held_fire.php runs as the web server's user
        self.ino = _Inotify(os.path.dirname(HOSTS) or "/")
        self.hosts_name = os.path.basename(HOSTS)
        self.lock = threading.Lock()
        self.waits: Dict[int, List[_Wait]] = {}  # hold id -> the waits it wakes (one row per holder+family)
        self.early: Dict[int, Dict[str, Any]] = {}   # a fire that beat its hold's reply here
        self.pending: List[_Wait] = []          # every wait in progress (woken on the hosts event)
        print("[HELD] %s holder=%s socket=%s" % (BUILD, self.hid, self.path), file=sys.stderr, flush=True)
        threading.Thread(target=self._run, name="frognet-held", daemon=True).start()

    def _run(self):
        while True:
            r, _, _ = select.select([self.sock, self.ino.fd], [], [])
            if self.sock in r:
                try:
                    msg = json.loads(self.sock.recv(65536))
                except (OSError, ValueError):
                    continue
                with self.lock:
                    hid = int(msg.get("hold") or 0)
                    ws = self.waits.pop(hid, None)
                    if ws is None:
                        self.early[hid] = msg          # its wait has not registered yet
                        while len(self.early) > 256:   # holds nobody waits on any more
                            self.early.pop(next(iter(self.early)))
                for w in ws or []:
                    w.fire = msg
                    w.ev.set()
            if self.ino.fd in r and self.hosts_name in self.ino.names():
                # [HELD_READ_IN_API_V1] rule 6: the merge rewrote /etc/hosts. Every hold
                # ended with the old addresses; each pending wait holds again.
                with self.lock:
                    self.waits.clear(); self.early.clear()
                    for w in self.pending:
                        w.moved = True
                        w.ev.set()

    def hold(self, service: str, dbhost: str, name_like: Optional[str] = None,
             name: Optional[str] = None, timeout: float = 10.0):
        """Register a hold at dbhost's api.php; returns (hold id, current rows)."""
        q = "http://%s/api.php?entity=held&action=hold&SensorType=%s" % (dbhost, urllib.parse.quote(service))
        if name is not None:
            q += "&SensorName=" + urllib.parse.quote(name)
        if name_like is not None:
            q += "&SensorName__like=" + urllib.parse.quote(name_like)
        q += "&holder=%s&hid=%s" % (_source_ip_toward(dbhost), self.hid)
        try:
            with urllib.request.urlopen(q, timeout=timeout) as r:
                d = json.loads(r.read().decode())
        except Exception as e:
            raise HoldError("hold %s at %s: %s" % (service, dbhost, e)) from e
        if not d.get("ok") or "hold" not in d:
            raise HoldError("hold %s at %s: %s" % (service, dbhost, str(d)[:200]))
        return int(d["hold"]), d.get("rows", [])

    def wait_rows(self, service: str, dbhost: str, name_like: Optional[str] = None,
                  min_rows: int = 1, wait_s: float = 30.0, name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Hold until at least min_rows rows match, or wait_s (the caller's bound) has passed."""
        return self.wait_until(service, dbhost, lambda rows: len(rows) >= min_rows,
                               name_like=name_like, name=name, wait_s=wait_s)

    def wait_change(self, service: str, dbhost: str, seen: Optional[str], name_like: Optional[str] = None,
                    name: Optional[str] = None, wait_s: float = 30.0) -> List[Dict[str, Any]]:
        """Hold until the matching rows are not what the caller last saw (signature(rows) != seen),
        or wait_s has passed. The caller holds its own current value; this compares against it."""
        return self.wait_until(service, dbhost, lambda rows: signature(rows) != seen,
                               name_like=name_like, name=name, wait_s=wait_s)

    def wait_until(self, service: str, dbhost: str, done, name_like: Optional[str] = None,
                   name: Optional[str] = None, wait_s: float = 30.0) -> List[Dict[str, Any]]:
        """Hold; return the current rows as soon as done(rows), or when wait_s has passed.
        Each wake is a fire or the hosts event; each hold's reply is the current rows."""
        deadline = time.monotonic() + wait_s
        w = _Wait()
        with self.lock:
            self.pending.append(w)
        try:
            while True:
                w.ev.clear(); w.moved = False
                hid, rows = self.hold(service, dbhost, name_like=name_like, name=name)
                if done(rows):
                    return rows
                with self.lock:
                    if self.early.pop(hid, None) is not None:
                        continue                         # it fired already: hold again
                    self.waits.setdefault(hid, []).append(w)
                left = deadline - time.monotonic()
                woke = left > 0 and w.ev.wait(left)
                if woke and w.moved:
                    # /etc/hosts was rewritten: the role may be at another address now. The caller
                    # says which one it means (a name is resolved again on its next hold).
                    return rows
                if not woke:
                    with self.lock:
                        ws = self.waits.get(hid, [])
                        if w in ws:
                            ws.remove(w)
                        if not ws:
                            self.waits.pop(hid, None)
                    return rows
        finally:
            with self.lock:
                self.pending.remove(w)


def signature(rows: List[Dict[str, Any]]) -> str:
    """What a set of rows holds: each tuple and its value. Two reads of the same memory sign the same."""
    return json.dumps(sorted((str(r.get("SensorID")), str(r.get("SensorName")), str(r.get("SensorAddress")),
                              str(r.get("jsonData"))) for r in rows))


_holder: Optional[Holder] = None
_holder_lock = threading.Lock()


def holder() -> Holder:
    global _holder
    with _holder_lock:
        if _holder is None:
            _holder = Holder()
        return _holder


def wait_rows(service: str, dbhost: str, name_like: Optional[str] = None, min_rows: int = 1,
              wait_s: float = 30.0, name: Optional[str] = None) -> List[Dict[str, Any]]:
    return holder().wait_rows(service, dbhost, name_like=name_like, min_rows=min_rows, wait_s=wait_s, name=name)


def wait_change(service: str, dbhost: str, seen: Optional[str], name_like: Optional[str] = None,
                name: Optional[str] = None, wait_s: float = 30.0) -> List[Dict[str, Any]]:
    return holder().wait_change(service, dbhost, seen, name_like=name_like, name=name, wait_s=wait_s)
