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
proxy/transport_real.py

REAL HTTP transports:
- local upstream (127.0.0.1:8080)
- remote next-hop proxy (connect_ip:80) while preserving destination identity in Host header

CRITICAL (FrogNet):
- /etc/setup_iptables relies on nat OUTPUT bypass:
      -m mark --mark 1 ... -j RETURN
  Therefore proxy-originated upstream connections to tcp/80 MUST have SO_MARK=1
  BEFORE connect(), or they will be REDIRECTed back into frognet-proxy (recursion storm).

TIMEOUT POLICY:
- Allow slow semantic/ham links up to 45 seconds for remote upstream operations.
"""

from __future__ import annotations

import os
import threading
import time
import socket
import http.client
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

from proxy.constants import HDR_CONTROL_PLANE, HDR_ORIGIN_LOCAL

_SO_MARK = getattr(socket, "SO_MARK", 36)
_MARK_VALUE = 1

_LOCAL_TIMEOUT_SEC = float(os.environ.get("FROGNET_REAL_LOCAL_TIMEOUT", "10"))
_REMOTE_TIMEOUT_SEC = float(os.environ.get("FROGNET_REAL_REMOTE_TIMEOUT", "45"))
# [CONNECT_TIMEOUT_SPLIT_V1] The remote timeout (45s) is the budget for a SLOW
# semantic/ham request+response, NOT for the TCP connect. A dead role-host (e.g. a
# databasehost.frognet elected to an unreachable node) must be detected in seconds,
# not hold a proxy handler thread for the full 45s - under a burst that pileup
# starves every other request. So connect uses a short timeout; the full operation
# budget is restored only AFTER the connect succeeds.
_CONNECT_TIMEOUT_SEC = float(os.environ.get("FROGNET_REAL_CONNECT_TIMEOUT", "3"))
_STRICT_MARK = os.environ.get("FROGNET_STRICT_SOMARK", "1").strip().lower() in ("1", "true", "yes", "on")


def _hosts_only_ip(host: str) -> str:
    """[HOSTS_ONLY_V1] IPv4 for `host` from /etc/hosts. Raises if absent.

    An IPv4 literal passes through unchanged -- most callers here already hold one,
    since decision.py works in addresses.
    """
    from core.hosts_only import resolve as _r
    return _r(str(host).strip())


def _create_marked_tcp_socket(host: str, port: int, timeout: float) -> socket.socket:
    """
    Create a TCP socket and set SO_MARK=1 BEFORE connect(), then connect().
    """
    # [HOSTS_ONLY_V1] Resolve from /etc/hosts, never the resolver. getaddrinfo goes to
    # nsswitch and then resolv.conf -- `nameserver 127.0.0.1` on a node -- and its
    # answer can differ from the file the kernel and every other component route by.
    # Measured: file said 10.250.250.1, gethostbyname said 10.130.130.1. This function
    # is the proxy's actual connect, so a divergence here forwards traffic to a machine
    # nobody named. A name absent from the file raises rather than falling through.
    ip = _hosts_only_ip(host)
    infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, int(port)))]

    # [DIAG-MARK-RESOLVE-V1] What did getaddrinfo turn (host, port) into?
    # Confirms whether the proxy is connecting to the IP we think it is.
    try:
        print(f"[DIAG-MARK-RESOLVE-V1] tid={threading.get_ident()} "
              f"host={host!r} port={port} timeout={timeout} "
              f"resolved={[(socket.AddressFamily(a[0]).name, a[4]) for a in infos]}",
              flush=True)
    except Exception:
        pass

    last_err: Optional[BaseException] = None

    for af, socktype, proto, _canon, sa in infos:
        s: Optional[socket.socket] = None
        try:
            s = socket.socket(af, socktype, proto)
            # [CONNECT_TIMEOUT_SPLIT_V1] short timeout for the connect only.
            connect_to = min(timeout, _CONNECT_TIMEOUT_SEC) if timeout else _CONNECT_TIMEOUT_SEC
            s.settimeout(connect_to)

            # MUST set mark BEFORE connect
            try:
                s.setsockopt(socket.SOL_SOCKET, _SO_MARK, _MARK_VALUE)
            except Exception as e:
                if _STRICT_MARK:
                    raise RuntimeError(f"SO_MARK failed (recursion risk): {e!r}") from e
                # best-effort: continue without mark (not recommended)
                pass

            # [DIAG-MARK-CONNECT-V1] About to connect with mark already set.
            # Captures the exact socket-address tuple, AF, local-bind state.
            # If this prints but the next line (return s) doesn't, the
            # connect itself failed - see DIAG-MARK-CONNECT-FAIL-V1.
            try:
                print(f"[DIAG-MARK-CONNECT-V1] tid={threading.get_ident()} "
                      f"af={socket.AddressFamily(af).name} sa={sa} "
                      f"timeout={timeout} mark_value={_MARK_VALUE} "
                      f"local_before_connect={s.getsockname()}",
                      flush=True)
            except Exception:
                pass

            s.connect(sa)

            # [CONNECT_TIMEOUT_SPLIT_V1] connected - restore the full operation
            # budget for the (possibly slow, multi-hop/ham) request+response.
            s.settimeout(timeout)

            # Best-effort set again post-connect
            try:
                s.setsockopt(socket.SOL_SOCKET, _SO_MARK, _MARK_VALUE)
            except Exception:
                pass

            return s

        except Exception as e:
            last_err = e
            # [DIAG-MARK-CONNECT-FAIL-V1] connect() or earlier setup raised.
            # exc_type + errno distinguish "connection refused" (no listener)
            # from "no route to host" (kernel routing problem) from "timed
            # out" (peer ignored SYN) - three completely different bugs.
            try:
                print(f"[DIAG-MARK-CONNECT-FAIL-V1] tid={threading.get_ident()} "
                      f"af={socket.AddressFamily(af).name} sa={sa} "
                      f"exc_type={type(e).__name__} exc={e!r} "
                      f"errno={getattr(e, 'errno', None)}",
                      flush=True)
            except Exception:
                pass
            try:
                if s:
                    s.close()
            except Exception:
                pass

    if last_err:
        raise last_err
    raise OSError("Unable to connect (no address candidates)")


class MarkedHTTPConnection(http.client.HTTPConnection):
    """
    HTTPConnection that uses a pre-marked socket (SO_MARK=1) set BEFORE connect().
    """
    def connect(self) -> None:
        self.sock = _create_marked_tcp_socket(self.host, self.port, float(self.timeout or 10.0))


def _add_origin_headers(fwd: Dict[str, str], *, origin_local: bool, control_plane: bool) -> None:
    if origin_local:
        fwd[HDR_ORIGIN_LOCAL] = "1"
    if control_plane:
        fwd[HDR_CONTROL_PLANE] = "1"


def real_upstream_local(
    method: str,
    raw_path: str,
    body: bytes,
    headers: Dict[str, str],
    upstream_host: str,
    upstream_port: int,
    *,
    origin_local: bool,
    control_plane: bool,
) -> Tuple[Dict[str, Any], float]:
    """
    Local upstream (Apache on :8080). Not subject to nat OUTPUT tcp/80 redirect.
    """
    conn = http.client.HTTPConnection(upstream_host, int(upstream_port), timeout=_LOCAL_TIMEOUT_SEC)

    fwd: Dict[str, str] = {}
    for k, v in (headers or {}).items():
        if not k:
            continue
        if k.lower() == "host":
            continue
        fwd[k] = v

    fwd["Host"] = f"{upstream_host}:{int(upstream_port)}"
    fwd["Accept-Encoding"] = "identity"
    _add_origin_headers(fwd, origin_local=origin_local, control_plane=control_plane)

    t0 = time.time()
    try:
        conn.request(method, raw_path, body, fwd)
        resp = conn.getresponse()
        data = resp.read()
        status = int(resp.status)
        hdrs = dict(resp.headers)
        conn.close()
        return ({"status": status, "headers": hdrs, "body": data}, (time.time() - t0) * 1000.0)
    except Exception as e:
        elapsed_ms = (time.time() - t0) * 1000.0
        try:
            conn.close()
        except Exception:
            pass
        try:
            print(f"[PROXY-ERR] status=502 where=real_local_exc "
                  f"tid={threading.get_ident()} "
                  f"upstream={upstream_host}:{int(upstream_port)} "
                  f"method={method} raw_path={raw_path!r} "
                  f"origin_local={origin_local} control_plane={control_plane} "
                  f"body_len={len(body) if body else 0} "
                  f"elapsed_ms={elapsed_ms:.1f} "
                  f"timeout_s={_LOCAL_TIMEOUT_SEC} "
                  f"exc={e!r}", flush=True)
        except Exception:
            pass
        body_err = f"real_upstream_local failed: {repr(e)}".encode("utf-8", "replace")
        return ({"status": 502, "headers": {"Content-Type": "text/plain; charset=utf-8"}, "body": body_err}, elapsed_ms)


def real_upstream_remote_80(
    method: str,
    raw_path: str,
    body: bytes,
    headers: Dict[str, str],
    target_host: str,
    target_ip: str,
    *,
    origin_local: bool,
    control_plane: bool,
    connect_ip: Optional[str] = None,
    host_override: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    allow_encoding: bool = False,
) -> Tuple[Dict[str, Any], float]:
    """
    Remote next-hop proxy fetch on tcp/80 with SO_MARK=1 BEFORE connect().

    allow_encoding: when False (default) the fetch forces Accept-Encoding: identity,
    which the semantic/local/next-hop path REQUIRES so BLDC-1 can template the body.
    The HAM (terminal external) call site passes True: that body is written straight
    back to the client and never templated, so letting the origin negotiate gzip/br
    is the one compression lever that survives the forwarded/NAT'd IP path back to the
    requesting node (see [HAM_ACCEPT_ENCODING_V1] below).
    """
    conn_ip = (connect_ip or target_ip).strip()
    # [DIAG-RU80-ENTRY-V1] Every input the caller handed us, plus the
    # IP we'll actually connect to.  If conn_ip is "the wrong thing"
    # this line proves it before the connect attempt fires.
    try:
        _resolved_final_host = (host_override or target_host or target_ip).strip() or target_ip
        print(f"[DIAG-RU80-ENTRY-V1] tid={threading.get_ident()} "
              f"method={method} raw_path={raw_path!r} "
              f"target_host={target_host!r} target_ip={target_ip} "
              f"connect_ip={connect_ip!r} host_override={host_override!r} "
              f"origin_local={origin_local} control_plane={control_plane} "
              f"headers_keys={list((headers or {}).keys())} "
              f"host_header={(headers or {}).get('Host')!r} "
              f"body_len={len(body) if body else 0} "
              f"resolved_conn_ip={conn_ip} "
              f"resolved_final_host={_resolved_final_host!r}",
              flush=True)
    except Exception:
        pass
    conn = MarkedHTTPConnection(conn_ip, 80, timeout=_REMOTE_TIMEOUT_SEC)

    split = urlsplit(raw_path)
    path = split.path or "/"
    if split.query:
        path = f"{path}?{split.query}"

    fwd: Dict[str, str] = {}
    for k, v in (headers or {}).items():
        if not k:
            continue
        if k.lower() == "host":
            continue
        fwd[k] = v

    final_host = (host_override or target_host or target_ip).strip() or target_ip
    fwd["Host"] = final_host
    # [HAM_ACCEPT_ENCODING_V1] Identity is REQUIRED on the semantic/local/next-hop
    # path: BLDC-1 templates the body, and a gzipped body is un-templatable. A terminal
    # external (HAM) fetch is never templated -- its body is relayed straight back to
    # the client -- so forcing identity there only suppresses the origin's own gzip/br,
    # the one lever that survives the forwarded/NAT'd IP path back to the requester.
    # Drop any case-variant the copy loop carried, then set exactly one header.
    for _aek in [k for k in list(fwd) if k.lower() == "accept-encoding"]:
        fwd.pop(_aek, None)
    if allow_encoding:
        _client_ae = ""
        for _k, _v in (headers or {}).items():
            if _k and _k.lower() == "accept-encoding":
                _client_ae = (_v or "").strip()
                break
        fwd["Accept-Encoding"] = _client_ae if _client_ae else "gzip, br"
    else:
        fwd["Accept-Encoding"] = "identity"
    _add_origin_headers(fwd, origin_local=origin_local, control_plane=control_plane)

    if extra_headers:
        for k, v in extra_headers.items():
            if k and v is not None:
                fwd[k] = v

    t0 = time.time()
    try:
        conn.request(method, path, body, fwd)
        resp = conn.getresponse()
        data = resp.read()
        status = int(resp.status)
        hdrs = dict(resp.headers)
        conn.close()
        return ({"status": status, "headers": hdrs, "body": data}, (time.time() - t0) * 1000.0)
    except Exception as e:
        elapsed_ms = (time.time() - t0) * 1000.0
        try:
            conn.close()
        except Exception:
            pass
        try:
            print(f"[PROXY-ERR] status=502 where=real_remote80_exc "
                  f"tid={threading.get_ident()} "
                  f"conn_ip={conn_ip} target_host={target_host!r} target_ip={target_ip} "
                  f"final_host={final_host!r} "
                  f"method={method} path={path!r} "
                  f"origin_local={origin_local} control_plane={control_plane} "
                  f"body_len={len(body) if body else 0} "
                  f"elapsed_ms={elapsed_ms:.1f} "
                  f"timeout_s={_REMOTE_TIMEOUT_SEC} "
                  f"exc={e!r}", flush=True)
        except Exception:
            pass
        body_err = f"real_upstream_remote_80 failed: {repr(e)}".encode("utf-8", "replace")
        return ({"status": 502, "headers": {"Content-Type": "text/plain; charset=utf-8"}, "body": body_err}, elapsed_ms)
