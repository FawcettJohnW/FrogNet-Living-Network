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
"""
daemon/upstream/client.py

Per-thread persistent HTTP client for daemon->Apache calls.

Each worker thread in the ThreadPoolExecutor gets its own persistent
HTTPConnection via threading.local().  This gives:
  - 8 threads = 8 concurrent Apache connections, zero lock contention
  - Connection reuse (keep-alive) eliminates TCP+Apache setup per call
  - Transparent reconnect on any error
  - SO_MARK for iptables bypass on port 80
"""

from __future__ import annotations

import os
import socket
import http.client
import threading
from typing import Dict, Tuple

HDR_ORIGIN_LOCAL = "X-FrogNet-Origin-Local"
HDR_CONTROL_PLANE = "X-FrogNet-Control-Plane"

_SO_MARK = getattr(socket, "SO_MARK", 36)
_MARK_VALUE = 1
_TIMEOUT_SEC = float(os.environ.get("FROGNET_DAEMON_UPSTREAM_TIMEOUT", "5"))
_DIAG = os.environ.get("FROGNET_DAEMON_HTTP_DIAG", "0") == "1"


def _log(msg: str):
    if _DIAG:
        print(f"[DAEMON HTTP DIAG] {msg}", flush=True)


class UpstreamSocketUnmarked(OSError):
    """SO_MARK could not be set, so this socket would be REDIRECTed."""


def _create_marked_tcp_socket(host: str, port: int, timeout: float) -> socket.socket:
    """[NO_FALLBACK_V1] SO_MARK is not decoration - it is the whole reason this
    class exists.

    The mark is what makes our own iptables REDIRECT rule skip this connection.
    With `except Exception: pass`, a refused setsockopt produced an unmarked
    socket that connected to 127.0.0.1:80 and got REDIRECTed straight back into
    the proxy. The proxy sees a destination that is not one of its local
    interface IPs and forwards it to the daemon again: an infinite
    proxy<->daemon loop that never replies and hangs every caller at the 60s
    safety cap. That failure mode is documented in proxy/decision.py and it is
    indistinguishable in the log from a slow peer.

    Fail here, named, before the connect.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.setsockopt(socket.SOL_SOCKET, _SO_MARK, _MARK_VALUE)
    except OSError as e:
        try:
            s.close()
        except OSError as ce:
            print(f"[DAEMON-HTTP] close failed while discarding unmarked "
                  f"socket to {host}:{port}: {ce!r} - fd leaked", flush=True)
        raise UpstreamSocketUnmarked(
            e.errno,
            f"SO_MARK={_MARK_VALUE} refused on socket to {host}:{port} "
            f"({type(e).__name__}: {e.strerror}) - an unmarked socket is "
            f"REDIRECTed back into the proxy and loops") from e
    s.connect((host, port))
    return s


class MarkedHTTPConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = _create_marked_tcp_socket(self.host, self.port, float(self.timeout or 10.0))


# -- Per-thread connection storage ----------------------------------
_thread_local = threading.local()


def _get_thread_conn(port: int) -> http.client.HTTPConnection:
    """Get or create a persistent HTTP connection for THIS thread."""
    store = getattr(_thread_local, "conns", None)
    if store is None:
        store = {}
        _thread_local.conns = store

    conn = store.get(port)
    if conn is not None:
        # [NO_FALLBACK_V1] `except Exception: pass` here silently discarded a
        # usable connection on any error and fell through to building a new
        # one, so a bug in this probe presented as connection churn rather than
        # as a bug. AttributeError is the only thing http.client can raise for
        # a missing .sock, and it means the object is not a connection at all.
        try:
            if conn.sock is not None:
                return conn
        except AttributeError as e:
            print(f"[DAEMON-HTTP] cached object for port {port} is not an "
                  f"HTTPConnection ({e!r}) - discarding", flush=True)
            store.pop(port, None)

    # Create new connection
    if port == 80:
        conn = MarkedHTTPConnection("127.0.0.1", port, timeout=_TIMEOUT_SEC)
    else:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=_TIMEOUT_SEC)
    store[port] = conn
    _log(f"[thread {threading.current_thread().name}] new connection to 127.0.0.1:{port}")
    return conn


def _drop_thread_conn(port: int) -> None:
    """Close and discard this thread's connection for the given port."""
    store = getattr(_thread_local, "conns", None)
    if store is None:
        return
    conn = store.pop(port, None)
    if conn is not None:
        # [NO_FALLBACK_V1] A connection that will not close is a leaked fd on a
        # per-thread pool, and a leaked fd is what makes every later request
        # from this thread fail for an unrelated-looking reason.
        try:
            conn.close()
        except OSError as e:
            print(f"[DAEMON-HTTP] close failed for 127.0.0.1:{port} on thread "
                  f"{threading.current_thread().name}: {e!r} - fd leaked",
                  flush=True)


class HttpClient:
    """
    Per-thread persistent HTTP client.

    No locks needed -- each thread has its own connection via
    threading.local().  8 worker threads = 8 concurrent Apache
    connections with full keep-alive reuse.
    """

    def request(
        self,
        *,
        peer_ip: str,
        upstream_port: int,
        method: str,
        path: str,
        body_text: str,
        headers: Dict[str, str],
        host_header: str,
    ) -> Tuple[int, Dict[str, str], bytes, str]:

        port = int(upstream_port)

        fwd = {}
        for k, v in (headers or {}).items():
            if k.lower() != "host":
                fwd[k] = v

        fwd["Host"] = host_header.strip()
        fwd["Accept-Encoding"] = "identity"
        fwd["Connection"] = "keep-alive"
        fwd[HDR_ORIGIN_LOCAL] = "1"

        # if path.startswith(("/api.php", "/getHosts.php", "/frognet_echo.php")):
            # fwd[HDR_CONTROL_PLANE] = "1"

        body_bytes = (body_text or "").encode("utf-8", "replace")

        # Try with existing connection, retry once with fresh connection
        for attempt in range(2):
            conn = _get_thread_conn(port)
            try:
                _log(f"REQ {method} {path} -> 127.0.0.1:{port} Host={fwd['Host']} attempt={attempt}")
                conn.request(method, path, body_bytes, fwd)
                resp = conn.getresponse()

                status = int(resp.status)
                hdrs = dict(resp.headers)

                # BOUNDED READ
                cl = hdrs.get("Content-Length")
                if cl is not None:
                    data = resp.read(int(cl))
                    _log(f"RESP {status} read Content-Length={cl}")
                else:
                    data = resp.read()
                    _log(f"RESP {status} read until EOF")

                # If server wants to close, respect it
                conn_hdr = hdrs.get("Connection", "").lower()
                if conn_hdr == "close":
                    _log(f"server sent Connection: close, dropping")
                    _drop_thread_conn(port)

                try:
                    text = data.decode("utf-8", "replace")
                except Exception:
                    text = ""

                return status, hdrs, data, text

            except Exception as e:
                _log(f"attempt {attempt} failed: {e!r}")
                _drop_thread_conn(port)
                if attempt == 1:
                    raise

        # Should never reach here
        raise RuntimeError("HttpClient: exhausted retries")
