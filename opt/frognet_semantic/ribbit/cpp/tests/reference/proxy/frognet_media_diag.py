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
frognet_media_diag.py - instrumentation for the media planes.

This is NOT a new switch. The switch already exists: frognet_log / frognet_trace,
the shared "frognet.*" logger tree toggled by FROGNET_LOG_LEVEL or FROGNET_TRACE,
default WARNING (quiet). The media planes log through that same tree via
get_logger("media"), so the field operator flips the exact same knob they already
use for the rest of the stack - there is no second control to learn.

    FROGNET_LOG_LEVEL=ERROR    real faults only (and they always show: ERROR>WARNING)
    FROGNET_LOG_LEVEL=DEBUG    + lifecycle + drop-RATE samples
    FROGNET_LOG_LEVEL=TRACE    + per-frame wire events (the firehose; off by default)
    frognet_log.set_level('TRACE')   programmatic, e.g. from a signal handler

What this module ADDS, because the logging spine doesn't have it, is the
socket-failure *context vocabulary*: errno BY NAME (the name is the story, not the
number), the classification of "expected drop" vs "real fault", and a socket-state
snapshot that includes SIOCOUTQ unsent-bytes - the single most useful number when
the question is "was the kernel send buffer full?". None of it knows or asks what
the bytes mean: a media frame here is opaque payload moving in a direction. Content
lives above (the server's mix/screen/selection) and below (the codec). This layer
records WIRE and STATE events only; asserting media meaning it cannot know would be
false context, and false context is what sends you debugging the wrong thing.

Level discipline:
  - real fault (peer gone, desync, corruption, bring-up fail)  -> log.error  (always on)
  - notable/recoverable (reconnect, drop-storm threshold)      -> log.warning
  - lifecycle (plane open/close, role, handshake ok)           -> log.info
  - decisions + drop-RATE samples                              -> log.debug
  - per-frame wire events (per send/recv, per EWOULDBLOCK)     -> trace_event (TRACE)
EWOULDBLOCK is never an error and never a per-event ERROR line; it is counted and
surfaced as a rate. A genuine fault is always an ERROR with full context.
"""
from __future__ import annotations

import errno as _errno
import fcntl
import socket as _socket
import struct
import termios
import time
from collections import deque
from typing import Any, Deque, Tuple

# [NO_FALLBACK_V1] This was a try/except around the frognet_log and
# frognet_trace imports that fell back to a hand-rolled stdlib `logging` shim
# plus a re-implemented trace_event(). The comment justified it as "so tests
# still exercise it when run standalone" - but the effect on a node is that
# diagnostics silently leave the spine: no TRACE level wiring, no structured
# trace_event, different formatting, and a `log` object that answers to the
# same name while behaving differently. Nothing said it had happened, so a
# node whose media diagnostics were mute looked identical to a node with
# nothing to report.
#
# The spine is first-party. If it will not import, this module must not load.
# Running this file standalone means running it from the tree root, not
# silently swapping its logger.
from frognet_log import get_logger, TRACE          # type: ignore
from frognet_trace import trace_event               # type: ignore

log = get_logger("media")


# -- errno classification (the name is the story) ------------------------------
def errno_name(exc: BaseException) -> str:
    e = getattr(exc, "errno", None)
    if e is not None and e in _errno.errorcode:
        return _errno.errorcode[e]
    return type(exc).__name__


def is_would_block(exc: BaseException) -> bool:
    """Expected, non-error: the kernel send buffer is full right now. Drop, don't stall."""
    return getattr(exc, "errno", None) in (_errno.EWOULDBLOCK, _errno.EAGAIN)


def is_peer_gone(exc: BaseException) -> bool:
    """Real fault: the far end vanished. The link is dead and must be re-established."""
    return getattr(exc, "errno", None) in (
        _errno.EPIPE, _errno.ECONNRESET, _errno.ENOTCONN,
        _errno.ESHUTDOWN, _errno.ECONNABORTED, _errno.ETIMEDOUT,
    )


# -- socket-state snapshot for a failure record (content-blind) ----------------
def sock_ctx(sock) -> dict:
    """Everything worth knowing about a socket at a failure, assuming NOTHING about
    what flows through it: fd, blocking mode, peer/local, send-buffer size, and the
    gold - SIOCOUTQ unsent bytes (how backed up the wire is right now)."""
    out: dict = {}
    # A closed socket raises OSError for fileno()/getblocking(); that is the
    # state this snapshot exists to record, so it is named rather than blanked.
    # Narrowed from `except Exception` - anything other than OSError here is a
    # bug in the caller's object, not a fact about the socket.
    try:
        out["fd"] = sock.fileno()
    except OSError as e:
        out["fd"] = f"closed:{errno_name(e)}"
    try:
        out["blocking"] = sock.getblocking()
    except OSError as e:
        out["blocking"] = f"unavailable:{errno_name(e)}"
    for label, fn in (("peer", getattr(sock, "getpeername", None)),
                      ("local", getattr(sock, "getsockname", None))):
        if fn is None:
            continue
        # A closed or unconnected socket genuinely has no name; that is the
        # answer and the key is simply absent. Narrowed from `except Exception`
        # so a fault in the formatting above is not reported as "no peer".
        try:
            out[label] = "%s:%s" % fn()
        except OSError as e:
            out[label] = f"unavailable:{errno_name(e)}"
    out["sndbuf"] = sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_SNDBUF)
    out.update(send_queue(sock))
    return out


def send_queue(sock) -> dict:
    """Unsent bytes still in the kernel send buffer (Linux SIOCOUTQ==TIOCOUTQ 0x5411).

    [NO_FALLBACK_V1] This was `except Exception: return {}` with the docstring
    "best-effort; silent where the platform doesn't expose it". The imports moved
    to module scope - fcntl, struct and termios are stdlib and their absence is a
    broken interpreter, not a platform variation. What remains is the ioctl, and
    an ioctl that fails on Linux is a fault worth naming: sndq_unsent is the
    single most useful number when the question is "was the send buffer full?",
    and returning {} answered that question with silence.
    """
    buf = struct.pack("I", 0)
    try:
        q = struct.unpack("I", fcntl.ioctl(sock.fileno(), termios.TIOCOUTQ, buf))[0]
    except OSError as e:
        log.error("send_queue_ioctl_failed errno=%s fd=%s", errno_name(e),
                  getattr(sock, "fileno", lambda: "?")())
        return {"sndq_unsent_error": errno_name(e)}
    return {"sndq_unsent": q}


def send_buffer_free(sock) -> int:
    """Bytes the send buffer can still accept right now = SO_SNDBUF - SIOCOUTQ.
    Used to decide whether a whole frame fits BEFORE sending a byte, so a frame is
    either sent whole or dropped whole - never half-written (which would desync the
    length-prefixed stream).

    [NO_FALLBACK_V1] Returns -1 only when SIOCOUTQ genuinely could not be read -
    that sentinel has a documented caller contract (attempt-and-classify) and is
    kept. It no longer covers a failing getsockopt, which is a different fault
    and now raises.
    """
    total = sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_SNDBUF)
    q = send_queue(sock).get("sndq_unsent")
    if q is None:
        return -1
    # Linux reports SO_SNDBUF as ~2x the usable size; halve to approximate usable.
    return max(0, (total // 2) - q)


# -- drop meter: drops are a RATE, never a per-event firehose ------------------
class DropMeter:
    """Windowed sent/dropped accounting. The out-plane calls sent()/dropped() per
    frame (cheap counters); the rate is emitted at DEBUG on a cadence, and a
    sustained high rate is escalated to WARNING once (not every sample). This is how
    a drop storm is made visible without logging every EWOULDBLOCK."""

    def __init__(self, window_sec: float = 2.0, warn_rate: float = 0.25,
                 sample_every_sec: float = 1.0):
        self.window_sec = window_sec
        self.warn_rate = warn_rate
        self.sample_every_sec = sample_every_sec
        self._ev: Deque[Tuple[float, bool]] = deque()   # (t, dropped?)
        self._last_sample = 0.0
        self._warned = False

    def _trim(self, now: float) -> None:
        cut = now - self.window_sec
        while self._ev and self._ev[0][0] < cut:
            self._ev.popleft()

    def sent(self, now: float) -> None:
        self._ev.append((now, False)); self._trim(now); self._maybe_sample(now)

    def dropped(self, now: float) -> None:
        self._ev.append((now, True)); self._trim(now); self._maybe_sample(now)

    def rate(self) -> float:
        if not self._ev:
            return 0.0
        d = sum(1 for _, drp in self._ev if drp)
        return d / len(self._ev)

    def _maybe_sample(self, now: float) -> None:
        r = self.rate()
        # The storm WARNING must fire as soon as the rate crosses the threshold -
        # a sub-second burst would never land on the cadence tick. Gated by _warned
        # so it's one WARNING per storm, not a firehose; cleared when the rate recedes.
        if r >= self.warn_rate and not self._warned:
            self._warned = True
            log.warning("media_drop_storm rate=%.3f window_s=%.1f n=%d "
                        "(sustained drops - link saturated; ladder should be stepping down)",
                        r, self.window_sec, len(self._ev))
        elif r < self.warn_rate and self._warned:
            self._warned = False
        # The DEBUG rate sample is periodic (don't flood at frame rate).
        if now - self._last_sample >= self.sample_every_sec:
            self._last_sample = now
            log.debug("media_drop_rate rate=%.3f window_s=%.1f n=%d", r, self.window_sec, len(self._ev))
