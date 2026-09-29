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
sotf_media_backing.py -- the REAL backings the SotF media codex binds to on hardware.

Two seams, each behind the interface the codex already takes, so the codex itself does
NOT change between sim and box:

  1. TupleTransient / TuplePerm  -- control plane on :80.
     The codex's self.t / self.p calls become frognet_tuples.put/get_all against
     databasehost.frognet (api.php on port 80). The LiveStream control bag and the
     FfmpegFlag tuple live in the real transient. SAME/DIFF is earned because these
     are ordinary converged requests through the proxy.

  2. MediaSocket  -- the private frame vector.
     A length-prefixed raw TCP socket carrying pack_frame() bytes, fire-and-forget.
     This is NOT the proxy and NOT :80 -- it's the direct media vector to mediahost.
     send() never blocks the capture loop: a bounded queue + a writer thread drain it,
     and when the link can't keep up the queue SHEDS (drops the oldest droppable frame)
     instead of stalling -- the backlog depth is what the auto-scaler reads.

These are written to run on the box. The sim swaps in-memory equivalents with the same
methods; the codex can't tell the difference.
"""
from __future__ import annotations

import os
import sys
import time
import socket
import struct
import threading
import queue
from typing import Any, Dict, List, Optional, Callable

# control plane target: SotF call control lives on the DATA host, not the control/SD host
DATABASEHOST = os.environ.get("FROGNET_DBHOST", "databasehost.frognet")

# bring the real frognet_tuples into scope when on the box; degrade clearly if absent.
try:
    import frognet_tuples as FT
    _HAVE_FT = True
except Exception:                                  # pragma: no cover - sim path
    FT = None
    _HAVE_FT = False


# ===========================================================================
# CONTROL PLANE -- tuple store over :80 (frognet_tuples -> api.php)
# ===========================================================================
# The codex calls a TransientStore/PermStore with get(name)/upsert(name,value)/drop(name)
# and (for the space) all_by_type(type). We map those onto frognet_tuples, whose key is
# (SensorName=var.scope, SensorType=service). We encode the codex's flat string key
# "LiveStream.<session>.<who>" as var="LiveStream", scope="<session>.<who>", service=the
# SotF media service; the value dict carries "type" so all_by_type can filter.

SERVICE = "sotf_media"                              # SensorType for all our control tuples


def _split_key(name: str) -> (str, str):
    """codex key 'Type.session.who' -> (var='Type', scope='session.who')."""
    var, _, scope = name.partition(".")
    return var, scope


class TupleTransient:
    """Live transient over frognet_tuples on databasehost.frognet (:80)."""
    def __init__(self, dbhost: str = DATABASEHOST, service: str = SERVICE):
        self.dbhost = dbhost
        self.service = service

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        var, scope = _split_key(name)
        for r in FT.get(self.service, var, dbhost=self.dbhost):
            if r["scope"] == scope:
                return r["value"]
        return None

    def upsert(self, name: str, value: Dict[str, Any]) -> None:
        var, scope = _split_key(name)
        FT.put(self.service, var, scope, value, dbhost=self.dbhost)

    def drop(self, name: str) -> None:
        # transient tuples are reaped when un-reasserted; an explicit drop is a no-op
        # against api.php here (no per-name delete needed for the call's correctness).
        return

    def all_by_type(self, typ: str) -> List[Dict[str, Any]]:
        return [r["value"] for r in FT.get_all(self.service, dbhost=self.dbhost)
                if isinstance(r.get("value"), dict) and r["value"].get("type") == typ]


class TuplePerm:
    """Perm authority. On the box this is the resumable store; for the SotF call the
    transient IS the live copy and perm-load refaults from it (the call has no durable
    state beyond what's in the space). Kept as a thin pass-through so WorkingMemory's
    _live/_commit/hostReset paths work unchanged."""
    def __init__(self, transient: TupleTransient):
        self.t = transient
        self._local: Dict[str, Any] = {}

    def load(self, key: str) -> Optional[Dict[str, Any]]:
        v = self.t.get(key)
        return v if v is not None else self._local.get(key)

    def save(self, key: str, value: Dict[str, Any]) -> None:
        self._local[key] = value                   # local authority mirror
        self.t.upsert(key, value)                   # and assert into the space


# ===========================================================================
# DATA PLANE -- the private raw frame socket (the media vector)
# ===========================================================================
_LEN = struct.Struct("!I")                          # length prefix per frame


class MediaSocketSender:
    """Fire-and-forget raw frame vector to mediahost. send() enqueues; a writer thread
    drains to the socket. Bounded queue: when the link is slow the queue fills and we
    SHED the oldest non-key frame (droppable) rather than block capture. The live queue
    depth is the congestion signal the auto-scaler reads."""
    def __init__(self, host: str, port: int, maxq: int = 120,
                 on_drop: Optional[Callable[[], None]] = None):
        self.host = host; self.port = port
        self.q: "queue.Queue" = queue.Queue(maxsize=maxq)
        self.on_drop = on_drop
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._depth = 0
        self._lock = threading.Lock()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _connect(self):
        s = socket.create_connection((self.host, self.port), timeout=5)
        s.settimeout(None)                          # blocking writer thread; never the connect timeout
        self._sock = s

    def send(self, frame: bytes, is_key: bool = False) -> None:
        """Never blocks. If the queue is full, shed the oldest droppable frame to make
        room (keep keyframes). The shed is the backpressure the scaler watches."""
        try:
            self.q.put_nowait((frame, is_key))
        except queue.Full:
            try:
                old, oldkey = self.q.get_nowait()
                if oldkey:                          # don't drop a keyframe; put it back, drop the new non-key
                    try: self.q.put_nowait((old, oldkey))
                    except queue.Full: pass
                if self.on_drop: self.on_drop()
            except queue.Empty:
                pass
            try: self.q.put_nowait((frame, is_key))
            except queue.Full:
                if self.on_drop: self.on_drop()
        with self._lock:
            self._depth = self.q.qsize()

    def depth(self) -> int:
        with self._lock:
            return self._depth

    def _run(self):
        while not self._stop.is_set():
            try:
                frame, _ = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            for attempt in range(2):
                try:
                    if self._sock is None:
                        self._connect()
                    self._sock.sendall(_LEN.pack(len(frame)) + frame)
                    break
                except OSError:
                    try:
                        if self._sock: self._sock.close()
                    except OSError: pass
                    self._sock = None
                    time.sleep(0.2)
            with self._lock:
                self._depth = self.q.qsize()

    def close(self):
        self._stop.set()
        try:
            if self._sock: self._sock.close()
        except OSError: pass


class MediaSocketReceiver:
    """Server side of the media vector: accept one sender, read length-prefixed frames,
    hand each to a callback (the consumer's recv_av)."""
    def __init__(self, host: str, port: int, on_frame: Callable[[bytes], None]):
        self.host = host; self.port = port; self.on_frame = on_frame
        self._stop = threading.Event()
        self._srv: Optional[socket.socket] = None
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port)); self._srv.listen(4)
        self._t.start()

    def _recv_exact(self, sock, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk: return None
            buf.extend(chunk)
        return bytes(buf)

    def _run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            try:
                while not self._stop.is_set():
                    head = self._recv_exact(conn, _LEN.size)
                    if head is None: break
                    (n,) = _LEN.unpack(head)
                    frame = self._recv_exact(conn, n)
                    if frame is None: break
                    self.on_frame(frame)
            finally:
                try: conn.close()
                except OSError: pass

    def close(self):
        self._stop.set()
        try:
            if self._srv: self._srv.close()
        except OSError: pass


# ===========================================================================
# DATABASEHOST FLOAT DETECTOR -- drives datastore regeneration on reassignment
# ===========================================================================
import socket as _sock

def resolve_databasehost(name: str = DATABASEHOST) -> Optional[str]:
    """Resolve the elected databasehost to an IP. The float IS this IP changing.

    [HOSTS_ONLY_V1] From /etc/hosts, never the resolver. The float detector compares
    this against its previous answer; if the source is DNS it can disagree with the
    file the rest of the node routes by, and then a float is detected that did not
    happen -- or a real one is missed.
    """
    try:
        from core.hosts_only import try_resolve
        return try_resolve(name)
    except Exception:
        return None


class DatabasehostWatcher:
    """Per-process float detector for the SotF media codex, matching the bundle's
    HostResetWatcher contract: resolve databasehost.frognet each tick; on an IP delta
    (the elected data host was reassigned), drive every registered codex's hostReset()
    -- which regenerates the datastore (drop the stale transient cache, consistency-check
    perm, re-assert owned LiveStream instances into the NEW host, drop convergence
    references so the next emit re-FULLs against peers that moved).

    First tick primes the baseline (no reconcile -- the process just started). Reconcile
    fires only on subsequent deltas. Never raises: a watcher must not break its loop."""
    def __init__(self, codices, resolve=resolve_databasehost,
                 logger: Callable[[str], Any] = lambda s: None):
        self._codices = codices                    # list of SotFMediaCodex (live modules)
        self._resolve = resolve
        self._log = logger
        self._last: Optional[str] = None

    def tick(self) -> Optional[Dict[str, Any]]:
        try:
            ip = self._resolve()
        except Exception as e:
            self._log(f"DBHOST_WATCH resolve_err={e}")
            return None
        if not ip or ip == self._last:
            return None
        first = self._last is None
        self._last = ip
        if first:
            self._log(f"DBHOST_WATCH baseline host={ip}")
            return None
        self._log(f"DBHOST_WATCH float -> {ip}; regenerating datastore")
        reports = []
        for cx in self._codices:
            try:
                reports.append(cx.hostReset())     # the datastore regeneration
            except Exception as e:                 # never break the loop
                self._log(f"DBHOST_WATCH reset_err module={type(cx).__name__} {e}")
        return {"floated_to": ip, "modules": len(reports), "reports": reports}


# ===========================================================================
# ELECTION-DRIVEN REGENERATION -- the REAL trigger: SelectNewDatabaseHost
# ===========================================================================
# Canonically, databasehost.frognet is assigned by merge/selectNewDatabaseHost; the
# elector (frognet_dbhost_elector) is the SINGLE writer that commits the new host --
# writes /etc/hosts, HUPs dnsmasq -- and only on a real change. Datastore regeneration
# hangs off THAT commit, synchronously, not off a process later noticing a DNS delta.
# DatabasehostWatcher above is the dumb fallback for processes that are NOT the elector;
# this is the authoritative path for the one that is.

class DatabasehostElectionRegen:
    """Registered with the elector. on_new_databasehost(new_ip) is called by the election
    the instant it commits a new host (the if-changed branch that writes /etc/hosts), and
    it drives hostReset() -- the datastore regeneration -- across every live codex. One
    writer (the elector), one synchronous reset, no race, no poll."""
    def __init__(self, codices, logger: Callable[[str], Any] = lambda s: None):
        self._codices = codices
        self._log = logger
        self.last_ip: Optional[str] = None

    def on_new_databasehost(self, new_ip: str) -> Dict[str, Any]:
        """THE TRIGGER. Call this from selectNewDatabaseHost's commit point."""
        self.last_ip = new_ip
        self._log(f"SELECT_NEW_DBHOST -> {new_ip}; regenerating datastore")
        reports = []
        for cx in self._codices:
            try:
                reports.append(cx.hostReset())
            except Exception as e:
                self._log(f"SELECT_NEW_DBHOST reset_err module={type(cx).__name__} {e}")
        return {"trigger": "SelectNewDatabaseHost", "new_databasehost": new_ip,
                "modules": len(reports), "reports": reports}
