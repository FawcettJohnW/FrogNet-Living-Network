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
remote_test_daemon.py - PRIMER 1 mode 2 far end.

A real frognet-style daemon you can run on another box (or another process on
this one) so the proxy has something to reach over a TRUE interface, the way it
works in production:

    [driver] -> SimulatedProxyWorker --(FNW1 over eth0/wg0)--> [this daemon]
                                                                   |
                                                          HTTP --> [origin: echo.php]
                                                                   |
             <----------------- RESP_RAW ----------------------- echoed response

It speaks the exact FNW1 two-socket wire the proxy expects (HELLO + HELLO
RETURN, SEQ_RESET, seq-tagged replies) by reusing SimulatedDaemonServer /
SimulatedDaemonSession from transport_sim_tier - no parallel framing to drift.
The one thing the base tier deliberately omits - an upstream HTTP fetch - is
supplied here via an origin handler:

  - With --origin <url>: each REQ_RAW carries a raw HTTP request; the daemon
    POSTs it to the origin and returns the origin's real HTTP response as
    RESP_RAW. Point --origin at the bundled echo.php (run it with
    `php -S 0.0.0.0:8080 echo.php`) for a production-shaped echo.
  - Without --origin: the daemon self-echoes the request bytes back as the
    RESP_RAW body (pure transport test, no origin process needed).

Bind a routable interface so the proxy reaches it over the network:
    python3 remote_test_daemon.py --listen 0.0.0.0:19009 \
        --origin http://127.0.0.1:8080/echo.php

Port note: defaults OFF 9009 so it never collides with a live
frognet-daemon-v3 on the same box (FROGNET_TEST_DAEMON_PORT). The production
iptables DNAT-to-proxy-then-daemon flow is not modeled; this is the daemon end.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import urllib.request
from typing import Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_HERE), _HERE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from transport_sim_tier import (  # noqa: E402
    SimulatedDaemonServer, TemplateStore,
)
from transport_factories import SocketWireEndpoint  # noqa: E402

TEST_DAEMON_PORT = int(os.environ.get("FROGNET_TEST_DAEMON_PORT", "19009"))


# ----------------------------------------------------------------------------
# Origin handlers: callable(raw_http_request_bytes) -> (status, headers, body)
# ----------------------------------------------------------------------------

def self_echo_handler(http_request: bytes) -> Tuple[int, bytes, bytes]:
    """No origin process - echo the request bytes straight back."""
    import hashlib, time as _t, threading as _th
    sha = hashlib.sha256(http_request).hexdigest()
    print(f"[DIAG-DAEMON] ts={_t.time():.3f} thr={_th.current_thread().name} "
          f"op=REQ_RAW in_len={len(http_request)} in_sha256={sha} "
          f"origin=self-echo out_len={len(http_request)} status=200", flush=True)
    return 200, b"content-type: application/octet-stream", http_request


def make_origin_forwarder(origin_url: str, timeout: float = 10.0):
    """Forward the REQ_RAW payload to a real HTTP origin (e.g. echo.php) and
    return its (status, headers, body). The payload is treated as the request
    BODY here for simplicity; the echo origin reflects it back. This proves the
    full proxy->wire->daemon->origin->back path with real bytes end to end."""
    import hashlib, time as _t, threading as _th

    def handler(http_request: bytes) -> Tuple[int, bytes, bytes]:
        in_sha = hashlib.sha256(http_request).hexdigest()
        req = urllib.request.Request(origin_url, data=http_request, method="POST",
                                     headers={"Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
            hdrs = "\r\n".join(f"{k}: {v}" for k, v in resp.headers.items()).encode()
        out_sha = hashlib.sha256(body).hexdigest()
        print(f"[DIAG-DAEMON] ts={_t.time():.3f} thr={_th.current_thread().name} "
              f"op=REQ_RAW in_len={len(http_request)} in_sha256={in_sha} "
              f"origin={origin_url} out_len={len(body)} out_sha256={out_sha} "
              f"status={status}", flush=True)
        return status, hdrs, body
    return handler


# ----------------------------------------------------------------------------
# The daemon
# ----------------------------------------------------------------------------

class RemoteTestDaemon:
    """Accept-forever FNW1 daemon. Each peer opens two TCP connections (HELLO
    on the first, HELLO RETURN:<gw> on the second); we pair them into one
    SimulatedDaemonSession with the origin handler attached."""

    def __init__(self, host: str, port: int, origin_handler,
                 template_store: Optional[TemplateStore] = None):
        self.host = host
        self.port = int(port)
        self.origin_handler = origin_handler
        self.store = template_store or TemplateStore()
        self.server = SimulatedDaemonServer(self.store)
        self._lsock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self.sessions = []

    def start(self) -> "RemoteTestDaemon":
        self._lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._lsock.bind((self.host, self.port))
        self._lsock.listen(64)
        self.port = self._lsock.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True,
                         name="remote-daemon-accept").start()
        return self

    def _accept_loop(self):
        self._lsock.settimeout(1.0)
        # Pair every two accepted connections from the same peer as (send, return).
        pending = {}
        while not self._stop.is_set():
            try:
                conn, addr = self._lsock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            peer = addr[0]
            if peer not in pending:
                # First connection from this peer = its send channel (HELLO).
                recv_ep = SocketWireEndpoint(conn, label=f"daemon-recv-{peer}")
                sess = self.server.accept_send_sock(peer, recv_ep)
                sess.set_origin_handler(self.origin_handler)
                pending[peer] = sess
            else:
                # Second connection = its RETURN channel (HELLO RETURN:<gw>).
                send_ep = SocketWireEndpoint(conn, label=f"daemon-send-{peer}")
                sess = pending.pop(peer)
                self.server.accept_return_sock(peer, send_ep)
                sess.start()
                self.sessions.append(sess)

    def stop(self):
        self._stop.set()
        for s in self.sessions:
            try:
                s.stop()
            except Exception:
                pass
        try:
            if self._lsock:
                self._lsock.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="PRIMER 1 mode-2 remote test daemon")
    ap.add_argument("--listen", default=f"0.0.0.0:{TEST_DAEMON_PORT}",
                     help="host:port to bind on a true interface "
                          f"(default 0.0.0.0:{TEST_DAEMON_PORT}; keep OFF 9009).")
    ap.add_argument("--origin", default=None,
                     help="Upstream HTTP origin URL (e.g. "
                          "http://127.0.0.1:8080/echo.php). Omit to self-echo.")
    args = ap.parse_args()

    host, _, port_s = args.listen.rpartition(":")
    host = host or "0.0.0.0"
    port = int(port_s)

    if args.origin:
        handler = make_origin_forwarder(args.origin)
        origin_desc = f"forwarding to origin {args.origin}"
    else:
        handler = self_echo_handler
        origin_desc = "self-echo (no origin process)"

    d = RemoteTestDaemon(host, port, handler).start()
    print(f"[remote-test-daemon] listening on {host}:{d.port} - {origin_desc}",
          flush=True)
    print("[remote-test-daemon] proxy connects two TCP sockets (HELLO + RETURN); "
          "REQ_RAW is served from the origin as RESP_RAW.", flush=True)
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        print("\n[remote-test-daemon] shutting down", flush=True)
        d.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
