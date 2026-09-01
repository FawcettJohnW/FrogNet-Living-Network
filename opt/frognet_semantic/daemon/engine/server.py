#!/opt/frognet_semantic/venv/bin/python3
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
from __future__ import annotations

import socket
import struct
import threading
import time  # [RETURN_ONLY_V1] for RETURN-session registration wait
from daemon.util.log import trace
from daemon.util.ip import local_ips
from daemon.engine.session import SemanticSession
from daemon.upstream.client import HttpClient
from core.semcache_wire import (
    try_parse, OP_HELLO,
    OP_RTT_PING, OP_RTT_PONG, wrap_rtt_pong,
    wrap_rtt_loop,
)

_MAX_FRAME = 4194304


def _recv_exact(conn, n):
    buf = bytearray()
    while len(buf) < n:
        blk = conn.recv(n - len(buf))
        if not blk:
            raise RuntimeError("socket closed")
        buf.extend(blk)
    return bytes(buf)


def _recv_frame(conn):
    hdr = _recv_exact(conn, 4)
    (n,) = struct.unpack("!I", hdr)
    if n <= 0 or n > _MAX_FRAME:
        raise RuntimeError(f"bad frame length {n}")
    return _recv_exact(conn, n)


def _send_frame(conn, payload: bytes) -> None:
    """[LINK_POTENTIAL_PING_V1 2026-05-25] Length-prefixed frame writer
    matching the proxy's _send_frame in transport_semantic.py.  Used
    for the OP_RTT_PONG reply during the connect-time probe sequence,
    before the SemanticSession (which has its own writer) is created.
    """
    hdr = struct.pack("!I", len(payload))
    conn.sendall(hdr + payload)


class DaemonServer:
    def __init__(self, host: str, port: int, resolver):
        self.host = host
        self.port = int(port)
        self.resolver = resolver
        self._http_client = HttpClient()
        # Sessions waiting for a RETURN connection, keyed by return_ip
        self._pending_sessions = {}
        self._pending_lock = threading.RLock()

    def run(self) -> None:
        """[RETURN_ONLY_V1] Non-blocking accept loop.

        The accept loop does nothing but accept() and spawn a handler
        thread per connection.  The handler reads HELLO, routes to
        RETURN-dispatch or session creation.  A slow HELLO on one
        connection can no longer stall acceptance of others.
        """
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(256)
        trace(f"[Daemon] [RETURN_ONLY_V1] listening on {self.host}:{self.port}")

        while True:
            try:
                conn, addr = srv.accept()
            except Exception as e:
                trace(f"[Daemon] accept error: {e!r}")
                continue
            t = threading.Thread(
                target=self._handle_connection,
                args=(conn, addr),
                daemon=True,
                name=f"daemon-accept-{addr[0]}",
            )
            t.start()

    def _handle_connection(self, conn, addr):
        """[RETURN_ONLY_V1] Per-connection handler thread.

        Reads HELLO, routes to RETURN-dispatch or session creation.
        Runs in its own thread so a slow HELLO does not block the
        accept loop.
        """
        peer_ip = addr[0]
        # [NO_FALLBACK_V1] These five setsockopt calls were in one try with
        # `except Exception: pass`, so any of them failing silently produced a
        # connection with no keepalive. Without keepalive the kernel cannot
        # detect a dead remote, the session survives in ESTABLISHED forever, and
        # the far side's RPCs rot until something else trips - which is the
        # exact condition [STUCK_SOCK_CEILING_V1] exists to paper over on the
        # proxy side. Each option is now applied and checked individually so the
        # log names which one the kernel refused, and the connection is closed
        # rather than served without the protection.
        for _lvl, _opt, _val, _name in (
            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1, "TCP_NODELAY"),
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1, "SO_KEEPALIVE"),
            (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10, "TCP_KEEPIDLE"),
            (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5, "TCP_KEEPINTVL"),
            (socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3, "TCP_KEEPCNT"),
        ):
            try:
                conn.setsockopt(_lvl, _opt, _val)
            except OSError as e:
                print(f"[DAEMON-SERVER] {_name}={_val} refused on connection "
                      f"from {peer_ip}: {type(e).__name__} errno={e.errno} "
                      f"({e.strerror}) - closing; a session without keepalive "
                      f"cannot detect a dead remote", flush=True)
                try:
                    conn.close()
                except OSError:
                    pass
                return
        try:
            conn.settimeout(5.0)
            frame = _recv_frame(conn)
            msg = try_parse(frame)
            conn.settimeout(None)
        except Exception as e:
            trace(f"[Daemon] HELLO read failed from {peer_ip}: {e!r}")
            try: conn.close()
            except Exception: pass
            return

        if msg is None or msg.op != OP_HELLO or not msg.body:
            trace(f"[Daemon] expected HELLO from {peer_ip}, "
                  f"got op={msg.op if msg else 'None'}")
            try: conn.close()
            except Exception: pass
            return

        body = msg.body.decode("ascii").strip()

        # ---- RETURN connection: proxy-initiated return channel ----
        # [RETURN_ONLY_V1] RETURN may arrive before the normal HELLO has
        # registered its session (kernel accept order is not connect()
        # order, and the two handlers now run concurrently in threads).
        # Poll for registration up to 10s instead of dropping.
        if body.startswith("RETURN:"):
            return_ip = body[7:]
            deadline = time.time() + 10.0
            session = None
            with self._pending_lock:
                session = self._pending_sessions.get(return_ip)
            while session is None and time.time() < deadline:
                time.sleep(0.05)
                with self._pending_lock:
                    session = self._pending_sessions.get(return_ip)
            if session is not None:
                trace(f"[Daemon] [RETURN_ONLY_V1] RETURN from {peer_ip} "
                      f"for session {return_ip}")
                session.set_send_sock(conn)
            else:
                trace(f"[Daemon] [RETURN_ONLY_V1] RETURN from {peer_ip} "
                      f"for {return_ip} - no pending session after 10s, dropping")
                try: conn.close()
                except Exception: pass
            return

        # ---- Normal HELLO: create new session ----
        return_ip = body
        trace(f"[Daemon] HELLO from {peer_ip} -> return_ip={return_ip}")

        # [LOOP_DETECT_9009_V1] If the HELLO's return_ip is one of OUR local IPs,
        # this alive connection rode a candidate route that hairpinned back
        # through us - the candidate loops. Short-circuit with an explicit LOOP
        # frame (never silence/timeout) so the prober drops the candidate and
        # never installs it. Re-evaluated every merge; nothing is remembered, so
        # a reassigned .1/.2 gets a fresh verdict next pass. Proxy sessions carry
        # a token body (not an IP) and return channels take the RETURN: branch
        # above, so a real local IP in a normal HELLO is unambiguously a looped
        # alive probe.
        if return_ip and return_ip in local_ips():
            trace(f"[Daemon] HELLO return_ip={return_ip} is local -> candidate "
                  f"route loops through us; emit RTT_LOOP")
            # [NO_FALLBACK_V1] `except OSError: pass` on this send meant that
            # when the LOOP frame failed to go out, the prober got exactly the
            # silence-then-timeout that this whole block was written to replace
            # - and it got it with no record on either side, so the looping
            # candidate stayed eligible and was installed. If the verdict cannot
            # be delivered, that is the event worth logging.
            try:
                _send_frame(conn, wrap_rtt_loop())
            except OSError as e:
                print(f"[DAEMON-SERVER] [LOOP_DETECT_9009_V1] could not deliver "
                      f"RTT_LOOP to {peer_ip} (return_ip={return_ip}): "
                      f"{type(e).__name__} errno={e.errno} ({e.strerror}) - "
                      f"the prober will see silence and may install a looping "
                      f"candidate", flush=True)
            try:
                conn.close()
            except OSError as e:
                print(f"[DAEMON-SERVER] close failed after RTT_LOOP to "
                      f"{peer_ip}: {e!r}", flush=True)
            return

        # [LINK_POTENTIAL_PING_BUG_FIX_V1 2026-05-25] Construct and
        # REGISTER the SemanticSession BEFORE running the ping loop.
        # The proxy's _open_send_sock returns only after the ladder
        # completes (up to 45s); _open_return_channel then runs and
        # opens a RETURN connection.  The daemon's RETURN handler
        # polls _pending_sessions for up to 10s waiting for THIS
        # session to be registered.  If we delay registration until
        # after the ping loop (which can take 90s), the RETURN poll
        # times out, the daemon closes the RETURN socket, the proxy
        # tears down the send sock, and the worker reports
        # "reader dead" on the next RPC.
        #
        # Solution: register the session NOW (so RETURN can find it
        # and call set_send_sock immediately when conn #2 arrives),
        # run the ping loop on this conn afterward, then start the
        # session thread.  set_send_sock just stores a socket and
        # signals an Event - it doesn't require the session thread
        # to be running.
        session = SemanticSession(
            recv_sock=conn,
            peer_ip=peer_ip,
            resolver=self.resolver,
            http_client=self._http_client,
            return_ip=return_ip,
        )
        with self._pending_lock:
            self._pending_sessions[return_ip] = session

        # [LINK_POTENTIAL_PING_V1 2026-05-25] Connect-time probe phase.
        # After HELLO and BEFORE starting the SemanticSession's read
        # loop, the proxy may send a sequence of OP_RTT_PING frames
        # to measure link potential.  We answer each with OP_RTT_PONG
        # carrying daemon-side monotonic timestamps so the proxy can
        # decompose total RTT into (network) + (daemon processing).
        #
        # Termination: as soon as we read a frame that is NOT
        # OP_RTT_PING, we have the first session frame.  We pass it
        # into the SemanticSession via a one-shot pre-buffer mechanism
        # so the session's read loop processes it instead of
        # discarding it.  See SemanticSession.__init__'s
        # set_preread_frame method.
        #
        # Safety bounds:
        #   - per-frame read timeout: 45s (covers HF radio worst case)
        #   - total budget: 90s (proxy bails its ladder at ~45s; we
        #     accept some headroom)
        #   - max pings: 200 (12 ladder stages * 3 pings = 36 nominal;
        #     200 leaves room for retries and is well below DoS scale)
        #
        # Backward compatibility: if the peer is on OLD code that
        # doesn't send pings, the FIRST frame after HELLO will be a
        # session frame (REQ_FULL etc).  We detect that on the first
        # parse attempt and bail to session-creation immediately -
        # net effect: pre-V1 peers experience one extra parse() call
        # and one extra try_parse() at session start, which is
        # negligible.
        preread_frame = None
        ping_count = 0
        _MAX_PINGS = 200
        _BUDGET_SEC = 90.0
        _budget_deadline = time.monotonic() + _BUDGET_SEC
        # [NO_FALLBACK_V1] The ping phase used to end four different ways
        # (per-frame timeout, first app frame, budget, ping cap) and report
        # none of them; the timeout branch's own comment named two opposite
        # causes - "the proxy is done probing" and "the link silently broke" -
        # and took the same silent `break` for both. The phase now names how it
        # ended, so a link that died during bring-up is distinguishable from a
        # peer that simply had nothing to send.
        _phase_end = "unset"
        try:
            conn.settimeout(45.0)
            while ping_count < _MAX_PINGS and time.monotonic() < _budget_deadline:
                try:
                    frame = _recv_frame(conn)
                except socket.timeout:
                    # No frame within the per-frame timeout. Either the proxy is
                    # done probing and is waiting on app traffic that has not
                    # been generated yet, or the link broke. We cannot tell them
                    # apart from here - but we can record that we could not, and
                    # the session read loop's next outcome resolves it.
                    _phase_end = "per_frame_timeout_45s"
                    break
                pmsg = try_parse(frame)
                if pmsg is None or pmsg.op != OP_RTT_PING:
                    # First non-ping frame after HELLO is the first
                    # session frame.  Hand it to the session via
                    # preread.
                    preread_frame = frame
                    _phase_end = "first_app_frame"
                    break
                # Stamp t_recv and t_reply on the same monotonic clock
                # we'll use throughout the daemon's process.
                t_recv_ns = time.monotonic_ns()
                t_reply_ns = time.monotonic_ns()
                pong = wrap_rtt_pong(
                    ping_id=pmsg.ping_id,
                    proxy_t_send_ns_echo=pmsg.proxy_t_send_ns,
                    daemon_t_recv_ns=t_recv_ns,
                    daemon_t_reply_ns=t_reply_ns,
                )
                _send_frame(conn, pong)
                ping_count += 1
            else:
                _phase_end = ("ping_cap" if ping_count >= _MAX_PINGS
                              else "budget_90s")
            conn.settimeout(None)
        except Exception as e:
            _phase_end = f"exception:{type(e).__name__}"
            trace(f"[Daemon] RTT_PING phase aborted for {peer_ip}: {e!r} "
                  f"after {ping_count} ping(s) - proceeding to session")
            try:
                conn.settimeout(None)
            except OSError as se:
                # The socket refusing settimeout after an abort is not noise:
                # it means the fd is already gone and the session about to be
                # built on it will fail on its first read.
                print(f"[DAEMON-SERVER] settimeout(None) failed for {peer_ip} "
                      f"after ping-phase abort: {se!r}", flush=True)

        print(f"[DAEMON-SERVER] RTT_PING phase for {peer_ip} ended: "
              f"reason={_phase_end} pings={ping_count} "
              f"preread={'yes' if preread_frame else 'no'}", flush=True)

        if ping_count > 0:
            trace(f"[Daemon] RTT_PING phase complete for {peer_ip}: "
                  f"answered {ping_count} ping(s), "
                  f"preread={'yes' if preread_frame else 'no'}")

        # [LINK_POTENTIAL_PING_V1] Hand the session the first
        # non-ping frame we read above so its read loop processes
        # it instead of going to recv() and missing it.  None if
        # we hit timeout/budget before any app frame arrived.
        if preread_frame is not None:
            session.set_preread_frame(preread_frame)

        # [LINK_POTENTIAL_PING_BUG_FIX_V1] _pending_sessions registration
        # was moved BEFORE the ping loop (see above) so the RETURN
        # handler can find this session even while we're still
        # answering pings.  No second registration needed here.

        t = threading.Thread(
            target=self._run_session,
            args=(session, return_ip),
            daemon=True,
            name=f"daemon-session-{peer_ip}",
        )
        t.start()

    def _run_session(self, session: SemanticSession, return_ip: str) -> None:
        """Run session and clean up pending registration when done."""
        try:
            session.run()
        finally:
            with self._pending_lock:
                if self._pending_sessions.get(return_ip) is session:
                    self._pending_sessions.pop(return_ip, None)
