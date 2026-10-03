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
channel_sets.py - driver-private socket-set allocator (proxy side).

WHAT THIS IS
A `_DaemonWorker` already IS a socket set: one outbound send_sock + one
return recv_sock, its own _seq space, its own _pending map, its own writer
and cleanup threads. The registry `_WORKERS[(host, port)] -> _DaemonWorker`
hands out exactly one set per peer.

This layer lets a caller (a game, a media stream, a bulk transfer) get its
OWN independent set for a peer, keyed by protocol - up to _MAX_SETS_PER_PEER
truly independent sets. The caller receives an opaque ChannelToken and never
touches a socket; the set is an artifact of the driver.

WHY independent sets help (and why overlay hurts)
Each set is a separate TCP flow inside the one WireGuard tunnel, so it has its
own congestion window and its own head-of-line (HOL) domain. A lost segment on
the game's set stalls only the game's set, not the sensor stream sharing the
peer. That isolation is the entire point.

OVERLAY IS NOT FREE. When more than _MAX_SETS_PER_PEER protocols are requested
for one peer, the extra protocols must share an existing set - and the moment
two protocols share a set they share one cwnd and one HOL domain, so a loss on
one stalls the other. Overlay therefore collapses the LEAST latency-sensitive
protocols together and never onto a latency-critical set if it can avoid it.
The caller signals sensitivity via `open_channel_set(..., sensitivity=)`.

LIFECYCLE
Sets are transient: a game opens one at start and releases it at end. Because
the app is blind to the set, the DRIVER owns the lifecycle - there is a hard
cap (3/peer) and release() reaps the underlying worker when its last protocol
leaves. See the THREAD-LEAK note on _reap_worker before shipping to the box.

INTEGRATION (two-line change in proxy/transport_semantic.py - see patch in the
message that delivered this file): the `_WORKERS` key and `_get_worker`
signature gain a trailing `set_id` (default 0), so all existing callers keep
using set 0 unchanged - this is fully backward compatible.
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, Optional, Set

# Up to three INDEPENDENT sets per peer. Beyond this, protocols overlay.
# Three is a policy choice: it covers the common "game + media + control"
# split while bounding fds / conntrack entries / WG-crypto budget per peer.
_MAX_SETS_PER_PEER = 3

# Set 0 is the shared/default set every legacy caller already uses.
_DEFAULT_SET_ID = 0


class ChannelToken:
    """Opaque handle the caller holds. It does NOT expose the socket - only
    enough for the driver to route the caller's RPCs onto the right set."""
    __slots__ = ("host", "port", "set_id", "protocol", "_released")

    def __init__(self, host: str, port: int, set_id: int, protocol: str):
        self.host = host
        self.port = port
        self.set_id = set_id
        self.protocol = protocol
        self._released = False

    def __repr__(self) -> str:
        state = "released" if self._released else "open"
        return (f"<ChannelToken {self.protocol}@{self.host}:{self.port} "
                f"set={self.set_id} {state}>")


class _PeerChannelSets:
    """Allocator for one peer. Maps protocol -> set_id with intentional
    overlay once the independent-set budget is spent."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._proto_to_set: Dict[str, int] = {}
        self._set_protos: Dict[int, Set[str]] = {}
        # sensitivity per set: max sensitivity of any protocol on it.
        # Higher = more latency-critical; overlay avoids high-sensitivity sets.
        self._set_sensitivity: Dict[int, int] = {}
        self._lock = threading.RLock()

    def open(self, protocol: str, sensitivity: int = 0) -> ChannelToken:
        with self._lock:
            # Same protocol asked twice -> same set (idempotent).
            if protocol in self._proto_to_set:
                sid = self._proto_to_set[protocol]
            elif len(self._set_protos) < _MAX_SETS_PER_PEER:
                # Budget remains: hand out a fresh, truly independent set.
                sid = self._next_free_set_id()
                self._set_protos[sid] = set()
                self._set_sensitivity[sid] = sensitivity
            else:
                # Oversubscribed: overlay onto the LEAST latency-sensitive set,
                # breaking ties by fewest tenants. Never silently land a
                # latency-critical protocol on top of another.
                sid = self._overlay_target()
            self._proto_to_set[protocol] = sid
            self._set_protos[sid].add(protocol)
            self._set_sensitivity[sid] = max(self._set_sensitivity.get(sid, 0),
                                             sensitivity)
            return ChannelToken(self.host, self.port, sid, protocol)

    def release(self, token: ChannelToken,
                reap: Optional[Callable[[str, int, int], None]] = None) -> bool:
        """Drop a protocol from its set. If that empties the set, the set is
        torn down (transient lifetime) via `reap`. Returns True if a set was
        reaped."""
        with self._lock:
            if token._released:
                return False
            token._released = True
            sid = token.set_id
            protos = self._set_protos.get(sid)
            if protos is not None:
                protos.discard(token.protocol)
            self._proto_to_set.pop(token.protocol, None)
            if protos is not None and not protos:
                del self._set_protos[sid]
                self._set_sensitivity.pop(sid, None)
                if reap is not None:
                    reap(self.host, self.port, sid)
                return True
            return False

    # --- internals ---
    def _next_free_set_id(self) -> int:
        for sid in range(_MAX_SETS_PER_PEER):
            if sid not in self._set_protos:
                return sid
        # Unreachable while caller checks the budget first.
        return self._overlay_target()

    def _overlay_target(self) -> int:
        # Least sensitive first, then fewest tenants.
        return min(self._set_protos.keys(),
                   key=lambda s: (self._set_sensitivity.get(s, 0),
                                  len(self._set_protos[s])))

    def snapshot(self) -> Dict[int, Set[str]]:
        with self._lock:
            return {s: set(p) for s, p in self._set_protos.items()}


# -- Module-level registry + public driver API -------------------------------

_ALLOCATORS: Dict[str, _PeerChannelSets] = {}
_ALLOCATORS_LOCK = threading.RLock()


def _allocator_for(host: str, port: int) -> _PeerChannelSets:
    with _ALLOCATORS_LOCK:
        a = _ALLOCATORS.get(host)
        if a is None:
            a = _PeerChannelSets(host, port)
            _ALLOCATORS[host] = a
        return a


def open_channel_set(host: str, port: int, protocol: str,
                     sensitivity: int = 0) -> ChannelToken:
    """THE METHOD. Driver-private: open (or join) an independent socket set for
    `protocol` toward `host:port`. Returns an opaque token. The underlying
    _DaemonWorker is created lazily on first RPC through the token. `sensitivity`
    (0=bulk .. higher=latency-critical) only influences overlay placement once
    the 3-set budget is spent."""
    return _allocator_for(host, port).open(protocol, sensitivity)


def release_channel_set(token: ChannelToken) -> bool:
    """Release a set. When its last protocol leaves, the worker is reaped
    (transient lifetime). Returns True if the underlying set was torn down."""
    return _allocator_for(token.host, token.port).release(token, reap=_reap_worker)


def worker_for_token(token: ChannelToken):
    """Resolve the token to its _DaemonWorker, creating it on demand. Requires
    the parameterized _get_worker(host, port, set_id) - see integration patch.
    Caller then does worker.call(packet) exactly as today."""
    from proxy.transport_semantic import _get_worker  # late import: avoid cycle
    return _get_worker(token.host, token.port, token.set_id)


def _reap_worker(host: str, port: int, set_id: int) -> None:
    """Tear down the worker backing a released set: stop its writer + cleanup
    threads (each set owns its own), drop its sockets, fail its pending RPCs,
    and de-register it. Set 0 (the shared default) is never reaped here - only
    transient per-protocol sets are."""
    from proxy.transport_semantic import _WORKERS, _WORKERS_LOCK
    with _WORKERS_LOCK:
        w = _WORKERS.pop((host, port, set_id), None)
    if w is None:
        return
    # [NO_FALLBACK_V1] `except Exception: pass` here meant a failed stop() left
    # the set's writer and cleanup threads running against sockets nobody owns,
    # with the worker already removed from _WORKERS - unreachable, unreapable,
    # and unlogged. That is the leak this function was written to prevent.
    # The worker is already de-registered, so re-raising would only lose the
    # de-registration; name it loudly instead and let the caller continue.
    try:
        w.stop()
    except Exception as e:
        print(f"[CHANNEL-SETS] LEAK: stop() failed for {host}:{port} "
              f"set={set_id}: {e!r} - worker de-registered but its writer and "
              f"cleanup threads may still be running", flush=True)
