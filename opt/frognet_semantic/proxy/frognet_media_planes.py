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
frognet_media_planes.py - the SotF media transport: two directional planes.

PROTOCOL (as specified):
  - A standing socket per direction, FNWP-style framing: 4-byte !I length prefix,
    then the frame body. Reuses the control link's framing discipline; it is its own
    socket so a high-rate media flow never head-of-line-blocks coordination traffic.
  - The OUT-plane is the client's send direction (client -> media server). It is
    NON-BLOCKING and drops WHOLE frames when the kernel send buffer can't take them
    (drop-don't-stall: a frame that can't go now is gone, never half-written).
  - The IN-plane is the client's receive direction (media server -> client). What it
    carries is whatever the SERVER emits - a single stream, a shared screen, a mix,
    a selected source. The transport does not know and must not assume.
  - ROLE = which planes you hold. A participant opens BOTH (sends its own up the
    out-plane, receives the server's output on the in-plane). A watcher opens the
    IN-plane ONLY - receive, never send - which is why watchers are free and can
    never inject into a session. A watcher with no out-plane is EXPECTED, not a fault.

CONTENT-BLIND: a frame is (seq, level_idx, opaque payload). The transport never
interprets the payload - not audio vs video, not keyframe vs delta, not single vs
mixed. seq and level_idx ride as lightweight envelope context (gap detection, which
rung is current); the ladder controller decides what level_idx MEANS, above this.
The codec encodes/decodes the opaque payload, below this. level_idx propagates; it
is not interpreted here.

Instrumentation rides frognet_log via frognet_media_diag (no separate switch):
faults -> ERROR (always on), drop RATE -> DEBUG, per-frame -> TRACE.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from enum import Enum
from typing import Callable, List, Optional, Tuple

from frognet_media_diag import (
    log, trace_event, errno_name, is_would_block, is_peer_gone,
    sock_ctx, send_buffer_free, DropMeter,
)

# -- wire constants ------------------------------------------------------------
_LEN = struct.Struct("!I")                 # outer length prefix (FNWP-style)
_HDR = struct.Struct("!IB")                # media frame header: seq(uint32), level_idx(uint8)
_MAX_FRAME = 8 * 1024 * 1024               # 8 MiB hard ceiling; a larger "frame" is corruption
_HELLO_MAGIC = b"FNMP1"                    # FrogNet Media Plane v1 handshake
_KEEPALIVE = ((socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPIDLE", None), 10),
              (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPINTVL", None), 5),
              (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPCNT", None), 3))


class Role(Enum):
    PARTICIPANT = "participant"
    WATCHER = "watcher"


class SendResult(Enum):
    SENT = "sent"
    DROPPED = "dropped"        # EWOULDBLOCK / no buffer space - expected, not a fault
    FAILED = "failed"          # peer gone / oversize - link is dead or caller erred


# -- socket setup (mirrors the control link's options) -------------------------
def _setup_sock(s: socket.socket) -> None:
    try:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for level, opt, val in _KEEPALIVE:
            if opt is not None:
                try:
                    s.setsockopt(level, opt, val)
                except OSError:
                    pass
    except OSError as e:
        log.warning("media_sock_opt_failed err=%s %s", errno_name(e),
                    " ".join("%s=%s" % kv for kv in sock_ctx(s).items()))


def _recv_exact(s: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly n bytes (blocking socket). None on clean EOF; raises on error."""
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def frame_bytes(seq: int, level_idx: int, payload: bytes) -> bytes:
    body = _HDR.pack(seq & 0xFFFFFFFF, level_idx & 0xFF) + payload
    return _LEN.pack(len(body)) + body


# -- handshake (content-blind: role + session token only) ----------------------
def _send_hello(s: socket.socket, role: Role, session: str, direction: str = "out") -> None:
    # direction tells a single-port server which plane this connection is:
    #   "out" = client is sending (server receives + fans out)
    #   "in"  = client is receiving (server registers it as a viewer)
    body = b"%s:%s:%s:%s" % (_HELLO_MAGIC, role.value.encode(), session.encode(),
                             direction.encode())
    s.sendall(_LEN.pack(len(body)) + body)


def _recv_hello(s: socket.socket) -> Tuple[Role, str, str]:
    head = _recv_exact(s, _LEN.size)
    if head is None:
        raise ConnectionError("peer closed before HELLO")
    n = _LEN.unpack(head)[0]
    if n <= 0 or n > 4096:
        raise ValueError("bad HELLO length %d (stream desync at handshake)" % n)
    body = _recv_exact(s, n)
    if body is None:
        raise ConnectionError("peer closed mid-HELLO")
    parts = body.split(b":", 3)
    if len(parts) < 3 or parts[0] != _HELLO_MAGIC:
        raise ValueError("malformed HELLO: %r" % body[:32])
    direction = parts[3].decode() if len(parts) >= 4 else "out"   # tolerate legacy 3-field
    return Role(parts[1].decode()), parts[2].decode(), direction


# ==============================================================================
# OUT-PLANE - client sends; non-blocking; drops whole frames; never stalls.
# ==============================================================================
class OutPlane:
    def __init__(self, sock: socket.socket, session: str, who: str = "?"):
        self.sock = sock
        self.session = session
        self.who = who
        self.alive = True
        self.meter = DropMeter()
        self._sent = 0
        self._dropped = 0
        self._failed = False
        sock.setblocking(False)
        log.info("media_outplane_open session=%s who=%s %s",
                 session, who, _ctxstr(sock))

    def send(self, seq: int, level_idx: int, payload: bytes) -> SendResult:
        now = time.monotonic()
        if not self.alive:
            return SendResult.FAILED
        framed = frame_bytes(seq, level_idx, payload)

        # oversize is a CALLER error (or corruption), not a wire drop - fault it loudly.
        if len(framed) > _MAX_FRAME:
            log.error("media_frame_oversize session=%s seq=%d level=%d size=%d max=%d %s",
                      self.session, seq, level_idx, len(framed), _MAX_FRAME, _ctxstr(self.sock))
            return SendResult.FAILED

        # [NO_FALLBACK_V1] whole-frame-or-nothing, decided BEFORE a byte moves.
        # The stream is length-prefixed: a half-written frame desyncs the
        # receiver permanently, and there is no resync.  So the send buffer is
        # asked in advance whether the WHOLE frame fits.
        free = send_buffer_free(self.sock)
        if free < 0:
            # Cannot determine free space -> cannot guarantee whole-or-nothing.
            # There is no fallback path: attempt-and-classify can only discover
            # it was wrong AFTER bytes are on the wire, which is the desync it
            # was supposed to prevent.  This is a fault, and it is fatal to the
            # plane, not a frame-level drop.
            self.alive = False
            self._failed = True
            log.error("media_outplane_no_sndbuf_query session=%s seq=%d %s "
                      "(SIOCOUTQ unavailable - cannot guarantee whole-frame "
                      "send; plane is not usable)",
                      self.session, seq, _ctxstr(self.sock))
            return SendResult.FAILED
        if len(framed) > free:
            self._dropped += 1
            self.meter.dropped(now)
            trace_event("media_drop_no_buffer", session=self.session, seq=seq,
                        level=level_idx, size=len(framed), free=free)
            return SendResult.DROPPED

        try:
            self.sock.sendall(framed)                  # room confirmed -> completes
        except (BlockingIOError, InterruptedError) as e:
            # [NO_FALLBACK_V1] The pre-check said the frame fits and the write
            # blocked anyway, so send_buffer_free()'s estimate was wrong.
            # sendall() on a non-blocking socket may have written PART of the
            # frame before raising, and it does not report how much -- so the
            # stream may already carry a half frame.  That is a desync, not a
            # drop, and it is unrecoverable: toss the plane.  Counting it as
            # DROPPED (the old behaviour) would leave the partial frame on the
            # wire and the receiver would desync on the next length prefix.
            self.alive = False
            self._failed = True
            log.error("media_outplane_partial_write session=%s seq=%d size=%d "
                      "free_estimate=%d errno=%s %s (pre-check said it fits; "
                      "frame may be half-written - plane tossed)",
                      self.session, seq, len(framed), free, errno_name(e),
                      _ctxstr(self.sock))
            return SendResult.FAILED
        except OSError as e:
            if is_would_block(e):
                self.alive = False
                self._failed = True
                log.error("media_outplane_partial_write session=%s seq=%d "
                          "size=%d free_estimate=%d errno=%s %s (pre-check "
                          "said it fits; frame may be half-written - plane "
                          "tossed)", self.session, seq, len(framed), free,
                          errno_name(e), _ctxstr(self.sock))
                return SendResult.FAILED
            if is_peer_gone(e):
                self.alive = False
                self._failed = True
                log.error("media_outplane_peer_gone session=%s seq=%d errno=%s %s",
                          self.session, seq, errno_name(e), _ctxstr(self.sock))
                return SendResult.FAILED
            # unclassified OSError: full context, treat as fatal for this plane.
            self.alive = False
            log.error("media_outplane_send_error session=%s seq=%d errno=%s %s",
                      self.session, seq, errno_name(e), _ctxstr(self.sock))
            return SendResult.FAILED

        self._sent += 1
        self.meter.sent(now)
        trace_event("media_sent", session=self.session, seq=seq, level=level_idx,
                    size=len(framed))
        return SendResult.SENT

    def rate(self) -> float:
        return self.meter.rate()

    def close(self) -> None:
        try:
            self.sock.close()
        finally:
            log.info("media_outplane_close session=%s who=%s sent=%d dropped=%d",
                     self.session, self.who, self._sent, self._dropped)


# ==============================================================================
# IN-PLANE - client receives the server's output. Content unknown by design.
# ==============================================================================
class Frame:
    __slots__ = ("seq", "level_idx", "payload")

    def __init__(self, seq: int, level_idx: int, payload: bytes):
        self.seq = seq
        self.level_idx = level_idx
        self.payload = payload


class RecvResult(Enum):
    CLOSED = "closed"          # clean EOF
    DESYNC = "desync"          # corrupt length / closed mid-frame - stream unrecoverable


class InPlane:
    def __init__(self, sock: socket.socket, session: str, who: str = "?"):
        self.sock = sock
        self.session = session
        self.who = who
        self._last_seq: Optional[int] = None
        self._recv = 0
        sock.setblocking(True)                         # receive side blocks on read
        log.info("media_inplane_open session=%s who=%s %s",
                 session, who, _ctxstr(sock))

    def recv(self):
        """Return a Frame, or RecvResult.CLOSED (clean EOF), or RecvResult.DESYNC
        (corruption/half-frame - caller must drop the link). Lands payload bytes in
        memory unchanged; makes NO claim about what they are."""
        head = _recv_exact(self.sock, _LEN.size)
        if head is None:
            log.info("media_inplane_closed session=%s who=%s recv=%d", self.session, self.who, self._recv)
            return RecvResult.CLOSED
        n = _LEN.unpack(head)[0]
        if n < _HDR.size or n > _MAX_FRAME:
            log.error("media_inplane_desync session=%s who=%s declared_len=%d min=%d max=%d %s "
                      "(corrupt length prefix - stream cannot be resynced)",
                      self.session, self.who, n, _HDR.size, _MAX_FRAME, _ctxstr(self.sock))
            return RecvResult.DESYNC
        body = _recv_exact(self.sock, n)
        if body is None:
            log.error("media_inplane_closed_midframe session=%s who=%s declared=%d %s",
                      self.session, self.who, n, _ctxstr(self.sock))
            return RecvResult.DESYNC
        seq, level_idx = _HDR.unpack_from(body, 0)
        payload = body[_HDR.size:]
        # seq-gap is EXPECTED whenever the sender dropped frames - NOT an error.
        if self._last_seq is not None and seq > self._last_seq + 1:
            gap = seq - self._last_seq - 1
            trace_event("media_seq_gap", session=self.session, who=self.who,
                        prev=self._last_seq, seq=seq, gap=gap)
        self._last_seq = seq
        self._recv += 1
        trace_event("media_recv", session=self.session, who=self.who, seq=seq,
                    level=level_idx, size=len(payload))
        return Frame(seq, level_idx, payload)

    def close(self) -> None:
        try:
            self.sock.close()
        finally:
            log.info("media_inplane_close session=%s who=%s recv=%d",
                     self.session, self.who, self._recv)


# ==============================================================================
# BRING-UP - connect a plane, handshake, with full failure context.
# ==============================================================================
def _connect(host: str, port: int, session: str, role: Role,
             timeout: float, label: str) -> socket.socket:
    t0 = time.monotonic()
    trace_event("media_connect_begin", label=label, host=host, port=port, session=session)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect((host, port))
        _setup_sock(s)
        _send_hello(s, role, session, label)
        ms = int((time.monotonic() - t0) * 1000)
        log.info("media_connect_ok label=%s host=%s port=%d role=%s session=%s connect_ms=%d %s",
                 label, host, port, role.value, session, ms, _ctxstr(s))
        return s
    except OSError as e:
        log.error("media_connect_fail label=%s host=%s port=%d role=%s session=%s "
                  "errno=%s elapsed_ms=%d %s",
                  label, host, port, role.value, session, errno_name(e),
                  int((time.monotonic() - t0) * 1000), _ctxstr(s))
        try:
            s.close()
        except OSError:
            pass
        raise


class MediaSession:
    """What a client holds. role decides which planes exist. A watcher's missing
    out-plane is EXPECTED (logged as role=watcher), never a fault."""

    def __init__(self, role: Role, session: str, out: Optional[OutPlane], inn: InPlane):
        self.role = role
        self.session = session
        self.out = out
        self.inn = inn
        if role is Role.WATCHER and out is None:
            log.info("media_session watcher session=%s in-plane-only (no out-plane is correct for role)",
                     session)

    def close(self) -> None:
        if self.out is not None:
            self.out.close()
        self.inn.close()


def open_participant(host: str, out_port: int, in_port: int, session: str,
                     who: str = "?", timeout: float = 5.0) -> MediaSession:
    """Participant: open BOTH planes. Sends its own up the out-plane; receives the
    server's output on the in-plane."""
    out_sock = _connect(host, out_port, session, Role.PARTICIPANT, timeout, "out")
    try:
        in_sock = _connect(host, in_port, session, Role.PARTICIPANT, timeout, "in")
    except OSError:
        try:
            out_sock.close()
        except OSError:
            pass
        raise
    return MediaSession(Role.PARTICIPANT, session,
                        OutPlane(out_sock, session, who), InPlane(in_sock, session, who))


def open_watcher(host: str, in_port: int, session: str,
                 who: str = "?", timeout: float = 5.0) -> MediaSession:
    """Watcher: open the IN-plane ONLY. Receive, never send. Cannot inject."""
    in_sock = _connect(host, in_port, session, Role.WATCHER, timeout, "in")
    return MediaSession(Role.WATCHER, session, None, InPlane(in_sock, session, who))


# -- helpers --------------------------------------------------------------------
def _ctxstr(sock) -> str:
    return " ".join("%s=%s" % kv for kv in sock_ctx(sock).items())


# ==============================================================================
# SINGLE-PORT opens - both planes to one advertised (addr, port); the server
# demuxes by the HELLO direction. This matches the elected media-host tuple, which
# carries one address:port.
# ==============================================================================
def open_participant_to(addr: str, port: int, session: str,
                        who: str = "?", timeout: float = 5.0) -> MediaSession:
    out_sock = _connect(addr, port, session, Role.PARTICIPANT, timeout, "out")
    try:
        in_sock = _connect(addr, port, session, Role.PARTICIPANT, timeout, "in")
    except OSError:
        try: out_sock.close()
        except OSError: pass
        raise
    return MediaSession(Role.PARTICIPANT, session,
                        OutPlane(out_sock, session, who), InPlane(in_sock, session, who))


def open_watcher_to(addr: str, port: int, session: str,
                    who: str = "?", timeout: float = 5.0) -> MediaSession:
    in_sock = _connect(addr, port, session, Role.WATCHER, timeout, "in")
    return MediaSession(Role.WATCHER, session, None, InPlane(in_sock, session, who))


# -- automatic discovery: resolve the elected media host from the tuple ---------
def resolve_media_host(resolve=None, dbhost: Optional[str] = None) -> Tuple[str, int]:
    """Return (addr, port) of the elected media server, read from the
    communicator/avhost tuple via frognet_avhost.resolve_host(). `resolve` is
    injectable for tests; default lazily imports the real resolver so this module
    stays decoupled. Raises MediaHostUnavailable (instrumented) if none is elected
    yet - a clean, named state, not a crash."""
    trace_event("media_resolve_begin", dbhost=dbhost or "default")
    if resolve is None:
        try:
            from frognet_avhost import resolve_host as resolve  # type: ignore
        except Exception as e:
            log.error("media_resolve_no_resolver errno=%s "
                      "(frognet_avhost not importable; pass an explicit host)", errno_name(e))
            raise MediaHostUnavailable("frognet_avhost.resolve_host unavailable") from e
    try:
        res = resolve(dbhost) if dbhost else resolve()
    except Exception as e:
        log.error("media_resolve_error errno=%s", errno_name(e))
        raise MediaHostUnavailable("resolve_host raised") from e
    if not res or not res[0]:
        log.warning("media_resolve_empty (no media host elected yet - is a mediahost "
                    "registered and has the election run? tuple communicator/avhost is empty)")
        raise MediaHostUnavailable("no media host elected")
    addr, port = res[0], int(res[1])
    log.info("media_resolve_ok addr=%s port=%d (from communicator/avhost tuple)", addr, port)
    return addr, port


class MediaHostUnavailable(RuntimeError):
    pass


def open_participant_auto(session: str, who: str = "?", resolve=None,
                          dbhost: Optional[str] = None, timeout: float = 5.0) -> MediaSession:
    """Participant, automatic: resolve the media host from the tuple and connect
    BOTH planes. No host argument - the codex finds the server itself."""
    addr, port = resolve_media_host(resolve=resolve, dbhost=dbhost)
    return open_participant_to(addr, port, session, who=who, timeout=timeout)


def open_watcher_auto(session: str, who: str = "?", resolve=None,
                      dbhost: Optional[str] = None, timeout: float = 5.0) -> MediaSession:
    """Watcher, automatic: resolve the media host from the tuple, open in-plane only."""
    addr, port = resolve_media_host(resolve=resolve, dbhost=dbhost)
    return open_watcher_to(addr, port, session, who=who, timeout=timeout)


# ==============================================================================
# MediaServer - the planes-speaking server a mediahost auto-starts. ONE listen
# port; each connection's HELLO says (role, session, direction). It is CONTENT-
# BLIND: it relays framed bytes from each session's senders (direction=out) to
# that session's viewers (direction=in). What those bytes ARE - single stream,
# screen, mix - is decided above this; the relay is the default emit policy and
# the seam where a host composition (mixer/selector) would replace fan-all.
# ==============================================================================
class _Viewer:
    __slots__ = ("plane", "who")

    def __init__(self, plane: OutPlane, who: str):
        self.plane = plane
        self.who = who


class MediaServer:
    def __init__(self, bind: str = "0.0.0.0", port: int = 9000):
        self.bind = bind
        self.port = port
        self._sessions: dict = {}                  # session -> list[_Viewer]
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._lsn: Optional[socket.socket] = None

    def serve_forever(self) -> None:
        self._lsn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._lsn.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._lsn.bind((self.bind, self.port))
        self._lsn.listen(64)
        log.info("media_server_listen bind=%s port=%d (FNMP1 planes; content-blind relay)",
                 self.bind, self.port)
        try:
            while not self._stop.is_set():
                try:
                    conn, addr = self._lsn.accept()
                except OSError:
                    break
                threading.Thread(target=self._on_conn, args=(conn, addr), daemon=True).start()
        finally:
            try: self._lsn.close()
            except OSError: pass

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._lsn: self._lsn.close()
        except OSError:
            pass

    def _on_conn(self, conn: socket.socket, addr) -> None:
        _setup_sock(conn)
        try:
            role, session, direction = _recv_hello(conn)
        except (OSError, ValueError, ConnectionError) as e:
            log.error("media_server_hello_fail peer=%s:%s errno=%s",
                      addr[0], addr[1], errno_name(e))
            try: conn.close()
            except OSError: pass
            return
        log.info("media_server_accept peer=%s:%s role=%s session=%s direction=%s",
                 addr[0], addr[1], role.value, session, direction)
        if direction == "in":
            self._add_viewer(session, conn, who=f"{addr[0]}:{addr[1]}")
        else:
            self._relay_sender(session, conn, who=f"{addr[0]}:{addr[1]}")

    def _add_viewer(self, session: str, conn: socket.socket, who: str) -> None:
        plane = OutPlane(conn, session, who=who)     # server writes to viewer; non-blocking drop
        with self._lock:
            self._sessions.setdefault(session, []).append(_Viewer(plane, who))
        log.info("media_server_viewer_add session=%s who=%s viewers=%d",
                 session, who, len(self._sessions.get(session, [])))
        # Hold the connection open; the viewer receives via fan-out.
        #
        # [OUTPLANE_NO_PEEK_V1] Do NOT read this socket and do NOT touch its
        # blocking mode.  Nothing needs to read it: a viewer only receives.
        # The old EOF check did recv(1, MSG_PEEK), which needs blocking mode,
        # so it called conn.setblocking(True) on the SAME fd OutPlane() had
        # just set non-blocking.  Blocking is a property of the file
        # descriptor, not of a direction -- that made OutPlane.send()'s
        # sendall() block, so the fan-out stalled on the slowest viewer.
        #
        # send() already detects the peer leaving, authoritatively:
        # is_peer_gone() sets plane.alive = False.  A failed write PROVES the
        # peer is gone; a readable socket is only a hint.  Park on that.
        # It also closes a leak: alive was set and consumed by nobody, so a
        # plane that died on write stayed in _sessions and _fan_out kept
        # calling send() on it every frame.
        try:
            while not self._stop.is_set() and plane.alive:
                self._stop.wait(1.0)          # sleeps; wakes early on shutdown
        finally:
            self._drop_viewer(session, plane)

    def _drop_viewer(self, session: str, plane: OutPlane) -> None:
        with self._lock:
            vs = self._sessions.get(session, [])
            self._sessions[session] = [v for v in vs if v.plane is not plane]
        plane.close()
        log.info("media_server_viewer_drop session=%s viewers=%d",
                 session, len(self._sessions.get(session, [])))

    def _relay_sender(self, session: str, conn: socket.socket, who: str) -> None:
        inp = InPlane(conn, session, who=who)
        log.info("media_server_sender_open session=%s who=%s", session, who)
        try:
            while not self._stop.is_set():
                r = inp.recv()
                if r is RecvResult.CLOSED:
                    break
                if r is RecvResult.DESYNC:
                    log.error("media_server_sender_desync session=%s who=%s", session, who)
                    break
                self._fan_out(session, r, exclude_who=who)
        finally:
            inp.close()
            log.info("media_server_sender_close session=%s who=%s", session, who)

    def _fan_out(self, session: str, frame: Frame, exclude_who: str) -> None:
        with self._lock:
            viewers = list(self._sessions.get(session, []))
        for v in viewers:
            if v.who == exclude_who:          # don't echo a participant's own frames back
                continue
            v.plane.send(frame.seq, frame.level_idx, frame.payload)   # non-blocking drop


# ==============================================================================
# CLI - hand-runnable. serve / participant / watcher.
# ==============================================================================
def _cli(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="frognet_media_planes",
                                 description="FrogNet SotF media planes (FNMP1).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the media server (the new planes server)")
    s.add_argument("--bind", default="0.0.0.0")
    s.add_argument("--port", type=int, default=9000)

    def _add_common(p):
        p.add_argument("--session", required=True)
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--auto", action="store_true", help="resolve media host from the avhost tuple")
        g.add_argument("--host", help="explicit ADDR:PORT (bypass discovery)")
        p.add_argument("--who", default="cli")
        p.add_argument("--dbhost", default=None, help="override databasehost for resolve")

    p = sub.add_parser("participant", help="open BOTH planes; send N test frames, print received")
    _add_common(p)
    p.add_argument("--frames", type=int, default=50)
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--size", type=int, default=800, help="test payload bytes per frame")
    p.add_argument("--level", type=int, default=4)

    w = sub.add_parser("watcher", help="open IN-plane only; print received frames")
    _add_common(w)
    w.add_argument("--seconds", type=float, default=10.0)

    args = ap.parse_args(argv)

    if args.cmd == "serve":
        srv = MediaServer(bind=args.bind, port=args.port)
        print(f"[media] serving FNMP1 planes on {args.bind}:{args.port}  (Ctrl-C to stop)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            srv.stop()
        return 0

    def _resolve_session():
        if args.host:
            a, _, p = args.host.partition(":")
            return open_participant_to if args.cmd == "participant" else open_watcher_to, (a, int(p or 9000))
        return None, None

    if args.cmd == "participant":
        if args.host:
            a, _, p = args.host.partition(":")
            sess = open_participant_to(a, int(p or 9000), args.session, who=args.who)
        else:
            sess = open_participant_auto(args.session, who=args.who, dbhost=args.dbhost)
        # reader thread: print frames arriving on the in-plane
        stop = threading.Event()
        def _reader():
            while not stop.is_set():
                r = sess.inn.recv()
                if r in (RecvResult.CLOSED, RecvResult.DESYNC):
                    print(f"[in] {r.value}"); return
                print(f"[in] seq={r.seq} level={r.level_idx} bytes={len(r.payload)}")
        threading.Thread(target=_reader, daemon=True).start()
        # send N test frames at the requested rate
        import time as _t
        period = 1.0 / args.fps if args.fps > 0 else 0
        payload = bytes(args.size)
        for i in range(args.frames):
            res = sess.out.send(i, args.level, payload)
            print(f"[out] seq={i} -> {res.value}")
            if period: _t.sleep(period)
        _t.sleep(0.5); stop.set(); sess.close()
        return 0

    if args.cmd == "watcher":
        if args.host:
            a, _, p = args.host.partition(":")
            sess = open_watcher_to(a, int(p or 9000), args.session, who=args.who)
        else:
            sess = open_watcher_auto(args.session, who=args.who, dbhost=args.dbhost)
        import time as _t
        deadline = _t.monotonic() + args.seconds
        while _t.monotonic() < deadline:
            r = sess.inn.recv()
            if r in (RecvResult.CLOSED, RecvResult.DESYNC):
                print(f"[in] {r.value}"); break
            print(f"[in] seq={r.seq} level={r.level_idx} bytes={len(r.payload)}")
        sess.close()
        return 0
    return 2


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_cli())
