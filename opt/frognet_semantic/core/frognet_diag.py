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
[DIAG] One-shot diagnostic tracing.

Everything potentially relevant, not just what the current hypothesis says
matters. A second instrumented run costs a reproduction that may not come; a
noisy journal costs nothing.

Every line carries wall clock to the millisecond, a monotonic clock (immune to
NTP steps, which is what you correlate across hosts by delta), pid, thread
name and native thread id, and a tag. Lines go to stderr unbuffered, so under
systemd they land in the journal interleaved with the traceback that follows
them.

  FROGNET_DIAG=0     off entirely
  FROGNET_DIAG=1     default: everything
  FROGNET_DIAG_MAXBODY=N   bytes of HTTP body to record (default 4096)

Tags in use:
  [DIAG-STORE]   every HTTP call to the tuple store: url, resolved host,
                 status, elapsed, body on any non-2xx
  [DIAG-SVC]     the AIConnect service loop, every iteration
  [DIAG-RECV]    bulk receiver lifecycle: bind, accept, read, close
  [DIAG-RDV]     producer rendezvous: what was written, every row considered
  [DIAG-PROD]    producer ramp: per-rung failure breakdown, port probes
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from typing import Any, Optional

_ON = os.environ.get("FROGNET_DIAG", "1") not in ("0", "", "no", "off")
try:
    MAXBODY = int(os.environ.get("FROGNET_DIAG_MAXBODY", "4096"))
except ValueError:
    MAXBODY = 4096

_T0 = time.monotonic()
_LOCK = threading.Lock()


def on() -> bool:
    return _ON


def diag(tag: str, msg: str, **kw: Any) -> None:
    """Emit one diagnostic line. Never raises -- diagnostics must not become
    the failure they were added to explain."""
    if not _ON:
        return
    try:
        extra = " ".join("%s=%s" % (k, _short(v)) for k, v in kw.items())
        t = time.time()
        line = ("[%s.%03d] [%9.3f] [%s] pid=%d thr=%s/%s %s%s"
                % (time.strftime("%H:%M:%S", time.localtime(t)),
                   int((t % 1) * 1000), time.monotonic() - _T0, tag,
                   os.getpid(), threading.current_thread().name,
                   threading.get_native_id(), msg,
                   (" " + extra) if extra else ""))
        with _LOCK:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
    except Exception:
        pass


def diag_exc(tag: str, msg: str, exc: BaseException, **kw: Any) -> None:
    """Emit a failure with its type, str, errno if any, and full traceback.
    The traceback goes out even when the caller intends to continue -- an
    exception that was handled is still evidence."""
    if not _ON:
        return
    diag(tag, msg, exc_type=type(exc).__name__, exc=str(exc),
         errno=getattr(exc, "errno", None), **kw)
    try:
        with _LOCK:
            sys.stderr.write("".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)))
            sys.stderr.flush()
    except Exception:
        pass


def body_of(exc: BaseException) -> str:
    """An HTTPError IS the response. Its body is normally the only place the
    server says WHY, and it is discarded by every caller that only prints the
    status line. Read it once and keep it."""
    try:
        read = getattr(exc, "read", None)
        if read is None:
            return "<no body>"
        raw = read(MAXBODY + 1)
        if not raw:
            return "<empty body>"
        txt = raw.decode("utf-8", "replace")
        if len(raw) > MAXBODY:
            txt = txt[:MAXBODY] + "...<truncated>"
        return txt.replace("\n", "\\n")
    except Exception as e:
        return "<body unreadable: %s>" % type(e).__name__


def headers_of(exc_or_resp: Any) -> str:
    try:
        h = getattr(exc_or_resp, "headers", None)
        if h is None:
            return "<none>"
        keep = ("Server", "Content-Type", "Content-Length", "Retry-After",
                "Connection", "X-Powered-By", "Date")
        return ";".join("%s=%s" % (k, h.get(k)) for k in keep if h.get(k))
    except Exception:
        return "<unreadable>"


_RESOLVED: dict = {}


def resolve(host: str) -> str:
    """Resolve and REPORT changes. dbhost is an elected role, so the name can
    point somewhere new between one call and the next; a run that fails after
    an election looks identical to one that fails for any other reason unless
    the address is on every line.

    [HOSTS_ONLY_V1] /etc/hosts, never the resolver. gethostbyname goes to
    resolv.conf (nameserver 127.0.0.1 on a FrogNet node) and answers with a
    different address than the file every other part of the system is using,
    so a diagnostic built on it accuses the wrong machine."""
    try:
        from core.hosts_only import resolve as _hres
        ip = _hres(host.split(":")[0])
    except Exception as e:
        ip = "UNRESOLVED(%s:%s)" % (type(e).__name__, e)
    prev = _RESOLVED.get(host)
    if prev is not None and prev != ip:
        diag("DIAG-STORE", "DBHOST MOVED", host=host, was=prev, now=ip)
    _RESOLVED[host] = ip
    return ip


def _short(v: Any) -> str:
    try:
        s = v if isinstance(v, str) else repr(v)
    except Exception:
        return "<unreprable>"
    s = s.replace("\n", "\\n")
    return s if len(s) <= 400 else s[:400] + "...<+%d>" % (len(s) - 400)


class Timer:
    """Elapsed milliseconds around a call, so a slow store and a broken store
    are distinguishable in the log."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()

    @property
    def ms(self) -> float:
        return (time.monotonic() - self.t0) * 1000.0
