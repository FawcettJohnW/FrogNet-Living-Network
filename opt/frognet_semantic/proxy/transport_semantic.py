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
# [INSTRUMENTATION_V2_APPLIED]
#!/opt/frognet_semantic/venv/bin/python3
"""
proxy/transport_semantic.py

Semantic transport with SAME/DIFF caching protocol.

v4.1 fixes:
- BUG 1: reference=None sentinel - REQ_REPEAT now fires for zero-field
  requests (echo, getHosts) after the first request.
- BUG 4: req_hash truncated to REQ_HASH_LEN (16 bytes) on the wire.
  Hash computed once per (path, opcode, reference) and cached.
- BUG 5: In-memory LRU for RESP_SAME responses.  Avoids MySQL round-trip
  for hot, unchanging data (echo responses looked up every 6 seconds).
- BUG 6: Pipelining-ready _DaemonWorker.  Drains all queued requests in
  a single send burst before reading replies in order, amortizing TCP
  overhead when multiple requests target the same daemon.
- Telemetry redesign: every bump_cache call now carries req_type/resp_type
  so the story of REQ_FULL vs REQ_REPEAT and RESP_SAME vs RESP_DIFF is
  visible in the reports.

v4.2 fixes:
- BUG 7: (REVERTED in v4.6) Originally bypassed coalescing for echo/discovery
  probes.  This caused unbounded pending queue growth when a downstream node
  was unreachable - each probe got its own 50s timeout slot, inflating pending
  to 400+ and cascading failures across the mesh.  Echo probes now coalesce
  like all other RPCs.

v4.5 fixes:
- BUG 8: _DaemonWorker batch model caused head-of-line blocking.  All
  queued RPCs were sent in a burst, then the thread blocked reading ALL
  replies before touching the queue again.  One slow reply (e.g. multi-hop
  relay through a saturated SilverBox) blocked every caller in the batch
  for the full timeout.  Fix: split into writer thread + reader thread.
  Writer sends one frame at a time and stores _Rpc in pending map by seq.
  Reader pulls replies, matches by seq, completes individual caller's
  Event.  No batching, no head-of-line blocking.
"""

from __future__ import annotations

from frognet_trace import trace_enter, trace_event
# [NO_FALLBACK_V1] No degrade-to-no-op. These decide what the transport is
# allowed to dial; without them it dials everything forever.
#
# There WAS a `except Exception: def _nf_retire(...): return False` block here.
# core.not_frognet had no retire() at all, so the import raised ImportError, the
# block caught it, and ALL the names silently became no-ops returning False.
# Nothing was ever marked. The transport announced "retiring for this runMerge"
# 720 times an hour per target while re-dialing stray DHCP clients on :9009
# every 5 seconds, and the log gave no hint -- the print sits outside the call.
#
# A transport that cannot tell FrogNet from a laptop must not run. Fail here.
#
# [ONE_STATE_V1] is_retired/retire are gone. One question -- did it answer --
# so one set, disk-backed, cleared once per merge. See core/not_frognet.py.
from core.not_frognet import (                      # noqa: E402
    is_marked as _nf_is_marked,
    mark as _nf_mark,
    mark_if_definitive as _nf_mark_if_definitive,
    clear_strikes as _nf_clear_strikes,
)

import hashlib
import json
import os
import errno
import select
import socket
import struct
import subprocess
import time
import threading
import queue
from collections import OrderedDict, deque  # [ADAPTIVE_TIMEOUT_V1]
from contextlib import contextmanager
from typing import Any, Dict, List, Tuple, Optional

from proxy.origin import inject_origin_into_semantic_request
from proxy.templates import get_template_store, learn_templates_from_real, extract_dynamic_query_vals
from proxy.proxy_metrics import bump_sem
from proxy.proxy_metrics import bump_cache
from proxy.proxy_metrics import bump_coalesce, bump_lru, bump_link, bump_template
from proxy.proxy_metrics import bump_link_quality
# [SAME_IS_BACK_V1] the response cache is consulted again; see _serve_from_same.
from proxy.cache import semcache_db

from core.codec import SemanticCodec, REQ_HASH_LEN
from core.tokens import TokenStore
from core.semcache_wire import (
    try_parse, wrap_req_full, wrap_req_repeat, wrap_req_raw,
    wrap_req_diff,
    wrap_hello,                                              # ---- ADDED ----
    OP_RESP_SAME, OP_RESP_DIFF, OP_RESP_RAW, OP_REQ_MISS, OP_ERROR,
    # [LINK_POTENTIAL_PING_V1 2026-05-25]
    OP_RTT_PONG, wrap_rtt_ping,
    op_name
)

_TRANSPORT_VERSION = "v4.11-no-lock-gymnastics"

_RPC_TIMEOUT_SEC = float(os.environ.get("FROGNET_SEM_RPC_TIMEOUT", "10"))

# [STUCK_SOCK_CEILING_V1] Hard ceiling on send sock age regardless of
# _reader_alive flag.  A wedged reader (blocked in recv() on a stuck
# remote whose kernel keepalive isn't firing because of noise or
# partial frames) leaves _reader_alive=True forever; without this
# ceiling, send sock age grows unbounded and pending RPCs rot.  Logs
# from the fleet show 800+ second send-sock ages correlated with the
# worst error cascades.  120s is well above any normal long-lived
# idle and well below the observed failure ages.
_STUCK_SOCK_CEILING_SEC = float(os.environ.get(
    "FROGNET_STUCK_SOCK_CEILING_SEC", "120"))

# --- EDIT 1 -----------------------------------------------------------
# Around line 76-84.  Replace the existing _BUDGET_CEILING / _BUDGET_FLOOR
# block with this.  Adds _BUDGET_MULT and bumps floor 5->15, ceiling 8->30.
#
# Rationale: with the old floor=5 ceiling=8 mult=3, an ewma of 0.5s
# (typical for cached echo) pinned budget to the 5s floor.  Real RTTs
# under congestion crested 5-6s, producing reaper timeouts as the dominant
# 503 cause across multiple peers.  Bumping floor to 15 absorbs normal
# congestion spikes; raising mult to 8 lets the budget grow with sustained
# higher RTTs before hitting the new 30s ceiling.
#
# Both env-overridable.  Matches existing pattern.
 
_BUDGET_CEILING = float(os.environ.get("FROGNET_RPC_BUDGET_CEILING", "30.0"))
# [BUDGET_FLOOR_FIX] Reap fires at age > attempts*budget.  With
# _TRANSPORT_MAX_ATTEMPTS=1 (default) and budget pinned to floor by
# echo's small EWMA, getHosts (uncached, larger payload) reaped at
# ~1.5s before it could complete.  Floor bumped to 5s and made
# env-overridable to match _BUDGET_CEILING's pattern.  This is a
# floor on the timeout, not the RTT; echo still completes in its
# normal RTT regardless.
# [BUDGET_FLOOR_FIX_V2] Floor raised 5->15.  Real RTTs under brief
# transit congestion crest 5-6s; the 5s floor was producing visible
# 503s as the dominant proxy error class.  15s gives headroom for
# normal jitter without being long enough to feel like a hang.
_BUDGET_FLOOR = float(os.environ.get("FROGNET_RPC_BUDGET_FLOOR", "15.0"))
# [BUDGET_MULT_V1] Per-RTT growth factor.  At default 8, the budget
# tracks ewma above the floor: ewma=2s -> budget=16s; ewma=4s -> ceiling.
# Old default of 3 was too low to ever pull budget off the floor for
# typical inter-pond RTTs.
_BUDGET_MULT = float(os.environ.get("FROGNET_RPC_BUDGET_MULT", "8.0"))

# [PROBE-RULES-V1] Transport retry switch.
#   FROGNET_TRANSPORT_RETRIES unset / "" / "0" / "off" / "false" / "no" -> 1 attempt
#   FROGNET_TRANSPORT_RETRIES "1" / "on" / "true" / "yes"               -> 3 attempts
# The default is 1 attempt (switch OFF). Flip on with:
#   systemctl edit <proxy-unit>   # Environment=FROGNET_TRANSPORT_RETRIES=on
def _compute_transport_max_attempts() -> int:
    trace_enter('transport_semantic._compute_transport_max_attempts')
    v = os.environ.get("FROGNET_TRANSPORT_RETRIES", "").strip().lower()
    on = v in ("1", "on", "true", "yes")
    n = 3 if on else 1
    print(f"[TRANSPORT_RETRIES] switch={'on' if on else 'off'} max_attempts={n} "
          f"(env FROGNET_TRANSPORT_RETRIES={v!r})", flush=True)
    return n

_TRANSPORT_MAX_ATTEMPTS = _compute_transport_max_attempts()
_MAX_FRAME = int(os.environ.get("FROGNET_SEM_MAX_FRAME", "4194304"))
_DEBUG = True  # [INSTRUMENTATION_V2] always-on
_DAEMON_PORT = int(os.environ.get("FROGNET_DAEMON_PORT", "9009"))
# [CONNECT_TRIES_RETIRE_V1] Attempts to establish a :9009 session before a peer is
# retired for the rest of this runMerge. 3 x ~10s covers a slow downstream radio; a
# peer that still has not answered on 9009 by then does not belong in the mix.
_CONNECT_MAX_TRIES = int(os.environ.get("FROGNET_CONNECT_MAX_TRIES", "3"))
# [CONNECT_DIAL_BUDGET_V1] How long ONE dial may stay in progress before it
# counts as a failed try. This is wall-clock across cleanup cycles, NOT time
# spent holding the cleanup thread - see the note in _cleanup_loop Job 3.
#
# It was effectively 0.25s and nobody meant it to be. [NONBLOCK_CONNECT_V1]
# replaced a blocking 10s connect with a 0.25s poll and CLOSED the socket on
# every deferral, so each cycle opened a fresh socket and sent a fresh SYN that
# got 0.25s and was thrown away. A dial never accumulated more than 0.25s no
# matter how many cycles passed, so any peer needing more than one 250ms round
# trip to complete a handshake could never connect at all - it just collected
# three "failures" in ~10s and was marked non-FrogNet. Over WireGuard between
# coasts that is an ordinary RTT, not a fault.
_CONNECT_DIAL_BUDGET_S = float(os.environ.get(
    "FROGNET_CONNECT_DIAL_BUDGET_S", "3.0"))
# [RETURN_ONLY_V1] _PROXY_LISTEN_PORT removed - proxy no longer listens for daemon callbacks.
_WORKER_THREADS = int(os.environ.get("FROGNET_WORKER_THREADS", "256"))

# Bug 5: in-memory LRU size for RESP_SAME cache
_SAME_LRU_MAX = int(os.environ.get("FROGNET_SAME_LRU_MAX", "256"))


# ---- Resolve local FrogNet IP for HELLO frames ----
#
# Lazy + cached-only-when-real.  The previous implementation called
# _get_local_gw() once at module-load time and froze the result; if
# the served interface wasn't up yet (proxy started before
# NetworkManager finished), "0.0.0.0" was baked into every HELLO
# emitted for the life of the process, breaking return-channel
# routing on the peer.  Now each call re-polls until a real address
# is observed, then sticks.
import threading
_LOCAL_GW_LOCK = threading.Lock()
_LOCAL_GW_CACHED = ""
_LOCAL_GW_LAST_REPORT = 0.0
_LOCAL_GW_REPORT_EVERY_SEC = 30.0

def _get_local_gw() -> str:
    """Get this node's FrogNet subnet IP for HELLO frames.

    Honors $FROGNET_LOCAL_GW; falls back to `getEth0Address`.  Caches
    on first real result.  Returns "0.0.0.0" only while no real
    address is yet observable.
    """
    trace_enter('transport_semantic._get_local_gw')
    global _LOCAL_GW_CACHED
    if _LOCAL_GW_CACHED and _LOCAL_GW_CACHED != "0.0.0.0":
        return _LOCAL_GW_CACHED
    with _LOCAL_GW_LOCK:
        if _LOCAL_GW_CACHED and _LOCAL_GW_CACHED != "0.0.0.0":
            return _LOCAL_GW_CACHED
        val = os.environ.get("FROGNET_LOCAL_GW", "").strip()
        if val:
            _LOCAL_GW_CACHED = val
            return _LOCAL_GW_CACHED
        # [NO_FALLBACK_V1] The old body was `except Exception: pass` around a
        # check=False subprocess, then `return "0.0.0.0"` - three ways to reach
        # the sentinel (command missing, command failed, command silent) all
        # collapsed into one indistinguishable value, emitted with no log line.
        #
        # This value is not cosmetic: it becomes the HELLO body on the RETURN
        # channel (_open_return_channel), so a peer receiving 0.0.0.0 has no
        # usable return address and the reply path is dead. The sentinel is
        # retained ONLY for the legitimate startup case (address not up yet -
        # this function re-polls until it is), but every path to it now names
        # itself, and repeats are rate-limited so a boot race does not become a
        # log flood.
        global _LOCAL_GW_LAST_REPORT
        try:
            r = subprocess.run(
                ["getEth0Address"], capture_output=True, text=True,
                check=False, timeout=5.0
            )
            if r.returncode == 0 and r.stdout.strip():
                _LOCAL_GW_CACHED = r.stdout.strip()
                return _LOCAL_GW_CACHED
            reason = (f"getEth0Address rc={r.returncode} "
                      f"stdout={r.stdout.strip()[:80]!r} "
                      f"stderr={r.stderr.strip()[:120]!r}")
        except (OSError, subprocess.SubprocessError) as e:
            reason = f"getEth0Address did not run: {type(e).__name__}: {e}"
        now = time.time()
        if (now - _LOCAL_GW_LAST_REPORT) >= _LOCAL_GW_REPORT_EVERY_SEC:
            _LOCAL_GW_LAST_REPORT = now
            print(f"[SEM-TRANSPORT] LOCAL GW UNKNOWN: {reason} - emitting "
                  f"0.0.0.0 in HELLO/RETURN frames; peers have no return "
                  f"address for us until this resolves", flush=True)
        return "0.0.0.0"
# ---- END ----


def _debug(msg: str) -> None:
    trace_enter('transport_semantic._debug', msg=repr(msg))
    if _DEBUG:
        print(f"[SEM-TRANSPORT] {msg}", flush=True)


# [INSTRUMENTATION_V2] structured RPC tracing - always on.
def _rpc_trace(event: str, **kv) -> None:
    parts = [f"[RPC-TRACE] event={event}"]
    for k, v in kv.items():
        if isinstance(v, str):
            v = v.replace("\n", "\\n").replace("\r", "\\r")
        parts.append(f"{k}={v}")
    print(" ".join(parts), flush=True)


@contextmanager
def _client_write(handler, label: str):
    """Catch BrokenPipeError when writing response to client.

    The client (monitor, browser, etc.) may close the socket before
    the proxy finishes writing - especially on slow shaped links.
    This is normal, not an error.  Suppress the traceback, log once.
    """
    trace_enter('transport_semantic._client_write', handler=repr(handler), label=repr(label))
    try:
        yield
        handler.close_connection = True
    except BrokenPipeError:
        _debug(f"BrokenPipe writing {label} to client (client closed)")
        handler.close_connection = True


# ====================================================================
# REFERENCE VALUE CACHE (for diff encoding)
# ====================================================================
# BUG 1 FIX: default is None (never sent), not {} (sent with zero fields).
# Key: (target_ip, opcode) -> Optional[Dict[str, Any]]
_request_references: Dict[Tuple[str, int], Optional[Dict[str, Any]]] = {}
_response_references: Dict[Tuple[str, int], Optional[Dict[str, Any]]] = {}
_ref_lock = threading.RLock()


def _get_request_reference(target_ip: str, opcode: int) -> Optional[Dict[str, Any]]:
    """Return None if never sent, or the last-sent reference dict."""
    trace_enter('transport_semantic._get_request_reference', target_ip=repr(target_ip), opcode=repr(opcode))
    with _ref_lock:
        ref = _request_references.get((target_ip, opcode))
        return ref.copy() if ref is not None else None


def _set_request_reference(target_ip: str, opcode: int, ref: Dict[str, Any]) -> None:
    trace_enter('transport_semantic._set_request_reference', target_ip=repr(target_ip), opcode=repr(opcode), ref=repr(ref))
    with _ref_lock:
        _request_references[(target_ip, opcode)] = ref.copy()


def _clear_request_reference(target_ip: str, opcode: int) -> None:
    """Clear reference on REQ_MISS so next request sends full data."""
    trace_enter('transport_semantic._clear_request_reference', target_ip=repr(target_ip), opcode=repr(opcode))
    with _ref_lock:
        _request_references.pop((target_ip, opcode), None)


def _get_response_reference(target_ip: str, opcode: int) -> Optional[Dict[str, Any]]:
    trace_enter('transport_semantic._get_response_reference', target_ip=repr(target_ip), opcode=repr(opcode))
    with _ref_lock:
        ref = _response_references.get((target_ip, opcode))
        return ref.copy() if ref is not None else None


def _set_response_reference(target_ip: str, opcode: int, ref: Dict[str, Any]) -> None:
    trace_enter('transport_semantic._set_response_reference', target_ip=repr(target_ip), opcode=repr(opcode), ref=repr(ref))
    with _ref_lock:
        _response_references[(target_ip, opcode)] = ref.copy()


# ====================================================================
# BUG 5: IN-MEMORY LRU CACHE for RESP_SAME
# ====================================================================
# Keyed by same_id (bytes), value is (body, ctype, is_raw, status, headers_bytes).
# Avoids MySQL round-trip for hot, unchanging responses.
# [SAME_IS_BACK_V1] RESP_SAME body cache, restored.
#
# Removed by [NO_CACHES_V1] on 2026-08-02 as collateral in a purge aimed at
# unbounded memo dicts. This one was never unbounded: _SAME_LRU_MAX caps it and
# _same_lru_put evicts oldest-first. Removing it left the proxy 502ing every
# RESP_SAME with "peer is running pre-[NO_CACHES_V1] code" -- blaming the nodes
# that were still correct.
_SAME_LRU_MAX = int(os.environ.get("FROGNET_SAME_LRU_MAX", "256"))
_same_lru: OrderedDict = OrderedDict()
_same_lru_lock = threading.RLock()


def _same_lru_get(same_id: bytes):
    """LRU get.  Returns cached tuple or None."""
    trace_enter('transport_semantic._same_lru_get', same_id=repr(same_id))
    with _same_lru_lock:
        val = _same_lru.get(same_id)
        if val is not None:
            _same_lru.move_to_end(same_id)
    if val is not None:
        bump_lru(hit=True)
    else:
        bump_lru(hit=False)
    return val


def _same_lru_put(same_id: bytes, val):
    """LRU put.  Evicts oldest if over capacity."""
    trace_enter('transport_semantic._same_lru_put', same_id=repr(same_id), val=repr(val))
    with _same_lru_lock:
        _same_lru[same_id] = val
        _same_lru.move_to_end(same_id)
        while len(_same_lru) > _SAME_LRU_MAX:
            _same_lru.popitem(last=False)


# ====================================================================
# REQUEST COALESCING
# ====================================================================
# One in-flight RPC per (target_ip, req_hash).  If a second request
# arrives for the same (target, hash) while the first is still in
# flight, the second caller blocks until the first completes, then
# both use the same wire_reply.
#
# This prevents duplicate RPCs from saturating slow links.

class _InflightRPC:
    __slots__ = ("event", "wire_reply", "error")
    def __init__(self):
        self.event = threading.Event()
        self.wire_reply: Optional[bytes] = None
        self.error: Optional[Exception] = None

_inflight: Dict[Tuple[str, bytes], _InflightRPC] = {}
_inflight_lock = threading.RLock()


def _coalesced_rpc(target_ip: str, req_hash: bytes, worker, wire_req: bytes) -> Tuple[bytes, bool]:
    """
    Send wire_req to target via worker, coalescing duplicate in-flight requests.

    Returns (wire_reply, was_coalesced).
    Raises on RPC failure.
    """
    trace_enter('transport_semantic._coalesced_rpc', target_ip=repr(target_ip), req_hash=repr(req_hash), worker=repr(worker), wire_req=repr(wire_req))
    key = (target_ip, req_hash)

    with _inflight_lock:
        existing = _inflight.get(key)
        if existing is not None:
            _debug(f"COALESCE: waiting on in-flight RPC to {target_ip} hash={req_hash.hex()}")
        else:
            entry = _InflightRPC()
            _inflight[key] = entry

    # Second caller - wait for the in-flight RPC
    if existing is not None:
        # [COALESCE_TIMEOUT_FIX] These two lines previously referenced
        # `self._peer_latency` and `rpc.max_attempts` - both wrong: this
        # is a module-level function, not a method, and there is no
        # local `rpc`.  Result was a NameError on every coalesce hit
        # (i.e. every REQ_REPEAT to /frognet_echo.php during the
        # in-flight window), wrapped as "Semantic RPC failed:
        # NameError(...)" 503s.  Use worker._peer_latency (the same
        # tracker the worker uses for its own retry budget) and the
        # module-level _TRANSPORT_MAX_ATTEMPTS constant.
        budget = worker._peer_latency.retry_budget()
        wait_timeout = (budget * _TRANSPORT_MAX_ATTEMPTS) + 1.0   # +1s slop so reaper signals first
        if not existing.event.wait(timeout=wait_timeout):
            raise TimeoutError(f"coalesced RPC timed out waiting for in-flight to {target_ip}")
        if existing.error is not None:
            raise existing.error
        if existing.wire_reply is None:
            raise RuntimeError(f"coalesced RPC: in-flight completed but no reply from {target_ip}")
        bump_coalesce(peer_ip=target_ip)
        return existing.wire_reply, True

    # First caller - owns the entry, executes the RPC
    try:
        wire_reply = worker.call(wire_req)
        entry.wire_reply = wire_reply
        return wire_reply, False
    except Exception as e:
        entry.error = e
        raise
    finally:
        entry.event.set()
        with _inflight_lock:
            _inflight.pop(key, None)


# NH semaphore removed: the two-socket architecture keeps send and
# receive fully decoupled.  Worker threads enqueue onto the send
# socket freely; the writer thread drains it as fast as the link
# allows.  No artificial concurrency cap needed.


# ====================================================================
# REQ_HASH CACHE - avoid recomputing for static requests
# ====================================================================
# Key: (path, opcode, frozenset(reference.items())) -> truncated hash
# [NO_CACHES_V1] req-hash memo removed; the hash is recomputed per request.


def _compute_req_hash(target_ip: str, path: str, opcode: int, reference: Dict[str, Any]) -> bytes:
    """Truncated request hash. [NO_CACHES_V1] recomputed every call."""
    trace_enter('transport_semantic._compute_req_hash', target_ip=repr(target_ip), path=repr(path), opcode=repr(opcode), reference=repr(reference))
    full = hashlib.sha256(
        json.dumps({"_dst": target_ip, "_path": path, "_op": opcode, **reference}, sort_keys=True, default=str).encode()
    ).digest()
    return full[:REQ_HASH_LEN]


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    trace_enter('transport_semantic._recv_exact', sock=repr(sock), n=repr(n))
    buf = bytearray()
    while len(buf) < n:
        blk = sock.recv(n - len(buf))
        if not blk:
            raise RuntimeError("socket closed")
        buf.extend(blk)
    return bytes(buf)


def _recv_frame(sock: socket.socket) -> bytes:
    trace_enter('transport_semantic._recv_frame', sock=repr(sock))
    hdr = _recv_exact(sock, 4)
    (n,) = struct.unpack("!I", hdr)
    if n <= 0 or n > _MAX_FRAME:
        raise RuntimeError(f"bad frame length {n}")
    return _recv_exact(sock, n)


def _send_frame(sock: socket.socket, payload: bytes) -> None:
    trace_enter('transport_semantic._send_frame', sock=repr(sock), payload=repr(payload))
    n = len(payload)
    if n <= 0 or n > _MAX_FRAME:
        raise RuntimeError(f"invalid frame size {n}")
    sock.sendall(struct.pack("!I", n))
    sock.sendall(payload)


class _PeerLatency:
    """EWMA of observed RTT for a peer, used to compute adaptive
    per-request retry budgets.

    EWMA tracks SUCCESSFUL RTTs only.  Reap timeouts are NOT fed back
    into EWMA - that creates a feedback loop where slow peer -> long
    timeout -> reap age fed in -> EWMA grows -> longer timeout next
    time -> congestive collapse.

    budget_ceiling is a hard cap on retry_budget independent of EWMA,
    so even a permanently-pegged EWMA cannot produce minute-long
    timeouts.  At max_attempts=3, total wait per RPC is bounded at
    3 * budget_ceiling.
    """
    __slots__ = ("ewma", "floor", "ceiling", "alpha",
                 "budget_floor", "budget_mult", "budget_ceiling",
                 "_lock")

    def __init__(self, floor_sec: float = 0.5, ceiling_sec: float = 30.0,
                 alpha: float = 0.2, initial_sec: float = 10.0,
                 budget_floor: float = 1.5, budget_mult: float = 3.0,
                 budget_ceiling: float = 8.0):
        self.ewma = initial_sec
        self.floor = floor_sec
        self.ceiling = ceiling_sec
        self.alpha = alpha
        self.budget_floor = budget_floor
        self.budget_mult = budget_mult
        self.budget_ceiling = budget_ceiling
        self._lock = threading.Lock()

    def observe(self, rtt_sec: float) -> None:
        """Observe a successful RTT.  Do NOT call from reap path."""
        trace_enter('transport_semantic._PeerLatency.observe', rtt_sec=repr(rtt_sec))
        rtt_sec = max(self.floor, min(self.ceiling, rtt_sec))
        with self._lock:
            self.ewma = (1.0 - self.alpha) * self.ewma + self.alpha * rtt_sec

    def retry_budget(self) -> float:
        trace_enter('transport_semantic._PeerLatency.retry_budget')
        with self._lock:
            b = max(self.budget_floor, self.budget_mult * self.ewma)
            return min(self.budget_ceiling, b)
    def current_ewma(self) -> float:
        trace_enter('transport_semantic._PeerLatency.current_ewma')
        with self._lock:
            return self.ewma


class _Rpc:
    # [ADAPTIVE_TIMEOUT_V1] One _Rpc may be sent multiple times
    # (original + up to (max_attempts-1) retries).  Each attempt gets
    # a new seq.  Whichever seq's reply arrives first wins; all other
    # seqs become "recently reaped" and their late replies are dropped
    # silently by the reader.
    __slots__ = ("packet", "reply", "err", "done", "submitted_at",
                 "seqs", "sent_at", "attempts", "max_attempts",
                 "retry_pending")

    def __init__(self, packet: bytes, max_attempts: int = 3):
        self.packet = packet
        self.reply: Optional[bytes] = None
        self.err: Optional[Exception] = None
        self.done = threading.Event()
        self.submitted_at = time.time()
        # Retry state:
        self.seqs: list = []            # list[int], seqs used so far
        self.sent_at: Dict[int, float] = {}   # seq -> send timestamp (for RTT)
        self.attempts = 0               # incremented by writer on each send
        self.max_attempts = max_attempts
        self.retry_pending = True       # True = on self.q, waiting for writer


class _DaemonWorker:
    """
    Two-socket architecture with cleanup thread.

      _send_sock: proxy connects OUT to daemon:9009.  Writer thread drains
                  the send queue onto this socket.
      _recv_sock: proxy listens on 9010; daemon connects BACK here.  Reader
                  thread reads replies, matches by seq, signals caller's Event.

    Threads:
      writer:   queue.get() -> assign seq -> _send_frame on _send_sock.
      reader:   _recv_frame on _recv_sock -> match seq -> rpc.done.set().
      cleanup:  every 1s, scans _pending for stale RPCs, signals them.
                Detects dead workers (no reader callback) and reconnects.

    Callers block on rpc.done.wait() - a pure semaphore wait.
    The cleanup thread is the sole timeout mechanism.
    """

    def __init__(self, host: str, port: int, set_id: int = 0):
        self.host = host
        self.port = port
        self.q: queue.Queue[_Rpc] = queue.Queue()
        self._send_sock: Optional[socket.socket] = None
        self._recv_sock: Optional[socket.socket] = None
        self._seq = 0
        self._unknown_streak = 0
        self._pending: Dict[int, _Rpc] = {}
        self._pending_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._connect_lock = threading.RLock()
        self._reader_alive = False
        self._send_sock_at = 0.0         # epoch when send sock opened
        # [FIRST_CONNECT_GRACE_V1] _ever_had_reader: True after the
        # daemon has connected back at least once.  Used to give a
        # longer grace period (_RPC_TIMEOUT_SEC) to first-time peer
        # connections - the WG return path takes time to establish.
        # Once we've proven the peer is real, drop to 5s for ongoing
        # health checks.  Previously removed by [RETURN_ONLY_V1],
        # which collapsed the threshold to a hardcoded 5s and broke
        # first-connection RPCs that needed longer than that.
        self._ever_had_reader = False
        self._reader_gen = 0             # incremented on each _register_recv_sock
        # [SESSION_TOKEN_V1] Unique per-session key.  The daemon keys
        # _pending_sessions by the HELLO body; sending the bare gw for
        # every session made concurrent sessions from this proxy collide
        # on one slot, so a RETURN matched the wrong session and the
        # loser blocked 15s then died "no RETURN connection", churning
        # the reader.  A monotonic suffix makes each session's key unique.
        self._session_token: Optional[str] = None
        self._session_token_seq = 0
        self._last_reconnect_at = 0.0    # rate-limit reconnection attempts
        # [CONNECT_DIAL_BUDGET_V1] A non-blocking connect that has not landed
        # yet, carried across cleanup cycles so it accumulates its full budget
        # instead of being discarded and re-SYNed every 0.25s.
        self._dialing: Optional[socket.socket] = None
        self._dial_started_at: float = 0.0
        self._connect_fails = 0          # [CONNECT_TRIES_RETIRE_V1] consecutive :9009 connect failures
        self._consecutive_stale = 0       # consecutive stale-RPC timeouts
        # [ADAPTIVE_TIMEOUT_V1] Adaptive timeout / retry state.
        # [BUDGET_FLOOR_FIX] Pass env-overridable floor and ceiling.
        # Previously called _PeerLatency() with no args, so the
        # FROGNET_RPC_BUDGET_CEILING env var was silently ignored.
        self._peer_latency = _PeerLatency(
            budget_floor=_BUDGET_FLOOR,
            budget_mult=_BUDGET_MULT,
            budget_ceiling=_BUDGET_CEILING,
        )
 
        self._recent_reaped: deque = deque(maxlen=1024)

        # [CHANNEL_SETS_V1] Per-set stop flag. A long-lived (set 0) worker
        # never sets this; a transient socket set sets it on reap so this
        # set's own writer + cleanup threads exit (each set owns its threads
        # and working set - termination must be per-set too).
        #
        # [WORKER_EXIT_REASON_V1] The flag alone was the Tier 0 defect. Both
        # loops gated on it and returned - no except, no log line, nothing.
        # The worker object stayed registered in _WORKERS, _get_worker kept
        # handing it out, and every subsequent call() put an _Rpc onto a queue
        # with no consumer and blocked the full 60s safety cap. Observed on
        # New-York-2 2026-08-08: 180 safety-cap timeouts in five minutes with
        # zero [DIAG-WRITER], [DIAG-READER], [RPC-TRACE] or [CLEANUP] lines
        # from the process, because the threads that emit them had exited.
        #
        # The flag now carries WHY, and setting it is no longer sufficient on
        # its own - see _retire().
        self._stopped = threading.Event()
        self._stopped_reason: str = ""
        self._stopped_at: float = 0.0
        self._set_id = set_id

        self._writer = threading.Thread(target=self._write_loop, daemon=True,
                                        name=f"sem-writer-{host}")
        self._writer.start()
        self._cleaner = threading.Thread(target=self._cleanup_loop, daemon=True,
                                         name=f"sem-cleanup-{host}")
        self._cleaner.start()

    # -- Connection establishment --------------------------------------

    # [LINK_POTENTIAL_PING_V1 2026-05-25] Ladder configuration.
    #
    # Ladder of pad sizes for the connect-time probe.  Sequential, not
    # back-to-back: send PING(n), wait for PONG(n), send PING(n+1),
    # etc.  3 frames per stage.
    #
    # The numbers come from "small enough to measure latency floor at
    # the bottom, large enough to expose serialization cost at the
    # top."  Final two values (10240, 12000) push past one MTU so the
    # ladder catches any sub-MTU fragmentation surprise.
    _LADDER_PAD_SIZES = (64, 128, 256, 512, 768, 1024,
                         2048, 4096, 6144, 8192, 10240, 12000)
    _LADDER_FRAMES_PER_STAGE = 3
    _LADDER_PER_FRAME_TIMEOUT_SEC = 45.0   # generous for HF radio
    _LADDER_TOTAL_BUDGET_SEC = 45.0        # bail with partial data

    def _run_link_potential_ladder(self, s: socket.socket) -> None:
        """Run the connect-time probe ladder on freshly-handshook socket
        `s` (HELLO already sent, no SemanticSession yet).  Stores
        (slope_us_per_byte, intercept_us, num_points, sample_age_sec)
        into proxy_metrics._CACHE_STATS[self.host] via record_link_baseline.

        Approach:
          - Sequential pings of increasing pad size; 3 per stage.
          - Per frame: capture proxy_t_send and proxy_t_pongback;
            extract daemon_t_recv and daemon_t_reply from the PONG.
          - Compute network_rtt = (pongback - send) - (daemon_reply - daemon_recv).
          - Compute frame_round_trip_bytes = ping_size + pong_size.
          - Fit a line through (frame_round_trip_bytes, network_rtt_us).
            slope = serialization cost per byte (us/byte).
            intercept = unloaded round-trip latency (us).

        Failure handling: every exception is caught and logged; the
        socket is NOT closed (caller still needs it).  Worst case the
        baseline is missing and the snapshot falls back to absolute
        thresholds - that's a degraded mode, not a fatal one.
        """
        # Imported lazily to avoid circular import at module load.
        trace_enter('transport_semantic._DaemonWorker._run_link_potential_ladder', s=repr(s))
        # [NO_FALLBACK_V1] The import is lazy for a real reason (circular at
        # module load), but `except Exception: print; return` turned a broken
        # proxy_metrics into a ladder that ran, measured nothing, and returned
        # as though it had succeeded. Lazy is not the same as optional.
        #
        # NOTE: per [LINK_POTENTIAL_PING_REMOVED 2026-05-25] below, this method
        # has no call site left in the tree - it is dead and should be deleted
        # outright in a separate rev. It is fixed rather than left carrying a
        # fallback, but deleting it is the better change.
        from proxy.proxy_metrics import record_link_baseline

        # (frame_size_bytes, network_rtt_us) points for the fit.
        points: List[Tuple[int, float]] = []
        # Diagnostics
        per_stage_diag: List[str] = []
        t_budget_deadline = time.monotonic() + self._LADDER_TOTAL_BUDGET_SEC
        next_ping_id = 1

        # PONG is always 33 bytes on the wire (4 magic + 1 op + 28 fields)
        # plus the 4-byte length prefix _recv_frame strips.  We count the
        # FRAMED size - what the kernel and link actually moved.
        _PONG_FRAMED = 4 + 33     # length prefix + payload
        # PING framed size = 4 (len prefix) + 4 (magic) + 1 (op) + 4 (ping_id)
        # + 8 (t_send) + 4 (pad_len) + pad
        def _ping_framed_size(pad_len: int) -> int:
            trace_enter('transport_semantic._DaemonWorker._ping_framed_size', pad_len=repr(pad_len))
            return 4 + 4 + 1 + 4 + 8 + 4 + pad_len

        try:
            s.settimeout(self._LADDER_PER_FRAME_TIMEOUT_SEC)
            for pad_size in self._LADDER_PAD_SIZES:
                if time.monotonic() >= t_budget_deadline:
                    per_stage_diag.append(f"pad={pad_size}:BUDGET_EXPIRED")
                    break
                stage_rtts: List[float] = []
                for _frame_idx in range(self._LADDER_FRAMES_PER_STAGE):
                    if time.monotonic() >= t_budget_deadline:
                        break
                    proxy_t_send_ns = time.monotonic_ns()
                    ping = wrap_rtt_ping(
                        ping_id=next_ping_id,
                        proxy_t_send_ns=proxy_t_send_ns,
                        pad_len=pad_size,
                    )
                    next_ping_id += 1
                    try:
                        _send_frame(s, ping)
                        reply = _recv_frame(s)
                    except socket.timeout:
                        per_stage_diag.append(f"pad={pad_size}:TIMEOUT")
                        # One timeout at this size - abandon this stage,
                        # try the next.  Don't bail the whole ladder
                        # because a single dropped frame.
                        break
                    proxy_t_pongback_ns = time.monotonic_ns()

                    pmsg = try_parse(reply)
                    if pmsg is None or pmsg.op != OP_RTT_PONG:
                        per_stage_diag.append(
                            f"pad={pad_size}:UNEXPECTED_OP={pmsg.op if pmsg else 'None'}")
                        # If we got something else, treat the ladder
                        # as compromised - the daemon is in a state we
                        # don't understand.  Bail.
                        return
                    if pmsg.ping_id != next_ping_id - 1:
                        per_stage_diag.append(
                            f"pad={pad_size}:PING_ID_MISMATCH "
                            f"expected={next_ping_id-1} got={pmsg.ping_id}")
                        return

                    # network_rtt = total_round_trip - daemon_processing
                    total_rt_ns = proxy_t_pongback_ns - proxy_t_send_ns
                    daemon_proc_ns = (pmsg.daemon_t_reply_ns
                                      - pmsg.daemon_t_recv_ns)
                    # daemon_proc may be 0 (same nanosecond capture)
                    # or even negative if monotonic_ns has limited
                    # resolution on this platform - clamp to >= 0.
                    if daemon_proc_ns < 0:
                        daemon_proc_ns = 0
                    network_rtt_ns = total_rt_ns - daemon_proc_ns
                    if network_rtt_ns < 0:
                        # Clock weirdness; skip this frame.
                        continue
                    network_rtt_us = network_rtt_ns / 1000.0
                    stage_rtts.append(network_rtt_us)
                    # x = total bytes that crossed the wire this round
                    # trip = framed PING size + framed PONG size.
                    x_bytes = _ping_framed_size(pad_size) + _PONG_FRAMED
                    points.append((x_bytes, network_rtt_us))

                if stage_rtts:
                    per_stage_diag.append(
                        f"pad={pad_size}:n={len(stage_rtts)} "
                        f"min={min(stage_rtts):.0f}us "
                        f"med={sorted(stage_rtts)[len(stage_rtts)//2]:.0f}us "
                        f"max={max(stage_rtts):.0f}us")
            s.settimeout(15.0)   # restore to caller's expectation
        except Exception as e:
            try: s.settimeout(15.0)
            except Exception: pass
            print(f"[LADDER] {self.host}: aborted: {e!r}", flush=True)
            # Fall through to record whatever points we got.

        # Need at least 2 points to fit a line.
        if len(points) < 2:
            print(f"[LADDER] {self.host}: INSUFFICIENT_POINTS={len(points)} "
                  f"diag={per_stage_diag}", flush=True)
            return

        # Linear regression: y = slope*x + intercept
        # Closed form.  No numpy dependency.
        n = float(len(points))
        sum_x = sum(p[0] for p in points)
        sum_y = sum(p[1] for p in points)
        sum_xx = sum(p[0]*p[0] for p in points)
        sum_xy = sum(p[0]*p[1] for p in points)
        denom = n * sum_xx - sum_x * sum_x
        if denom == 0:
            # All x values identical - shouldn't happen with our
            # ladder, but defensive.
            print(f"[LADDER] {self.host}: DEGENERATE_FIT denom=0", flush=True)
            return
        slope_us_per_byte = (n * sum_xy - sum_x * sum_y) / denom
        intercept_us = (sum_y - slope_us_per_byte * sum_x) / n

        # Sanity: slope cannot be negative on a real link.  Negative
        # slope would mean larger frames somehow get faster RTT than
        # smaller ones - a measurement artifact (jitter), not a real
        # link property.  Clamp to 0 and emit a warning.
        slope_clamped = False
        if slope_us_per_byte < 0:
            slope_clamped = True
            slope_us_per_byte = 0.0

        # Implied bandwidth: 1 byte takes slope_us_per_byte microseconds
        # to serialize, so capacity = 1e6 / slope bytes/sec.
        if slope_us_per_byte > 0:
            bandwidth_bps = (1_000_000.0 / slope_us_per_byte) * 8.0
        else:
            bandwidth_bps = float("inf")   # effectively bandwidth-unlimited

        record_link_baseline(
            peer_ip=self.host,
            slope_us_per_byte=slope_us_per_byte,
            intercept_us=intercept_us,
            num_points=len(points),
            bandwidth_bps=bandwidth_bps,
            ladder_diag="|".join(per_stage_diag),
        )

        print(f"[LADDER] {self.host}: n={len(points)} "
              f"slope={slope_us_per_byte:.3f}us/B "
              f"intercept={intercept_us:.0f}us "
              f"bw={bandwidth_bps/1e6:.2f}Mbps"
              f"{' SLOPE_CLAMPED' if slope_clamped else ''}",
              flush=True)

    def _mint_session_token(self) -> str:
        """[SESSION_TOKEN_V1] Mint a unique token for one send-sock +
        return-channel pair.  Both the send HELLO and the matching
        RETURN HELLO carry this exact string so the daemon routes the
        RETURN to THIS session, never another from the same gw."""
        trace_enter('transport_semantic._DaemonWorker._mint_session_token')
        with self._state_lock:
            self._session_token_seq += 1
            self._session_token = f"{_get_local_gw()}#{self._session_token_seq}"
            return self._session_token

    def _open_send_sock(self) -> socket.socket:
        """Connect outbound to daemon:9009 and send HELLO."""
        trace_enter('transport_semantic._DaemonWorker._open_send_sock')
        last_exc = None
        for attempt in range(4):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                s.settimeout(5.0)
                _t0 = time.monotonic()
                _rpc_trace("connect_begin", host=self.host, port=self.port,
                           attempt=attempt + 1)
                s.connect((self.host, self.port))
                _connect_ms = int((time.monotonic() - _t0) * 1000)
                # [SESSION_TOKEN_V1] Unique HELLO body; _open_return_channel
                # reuses self._session_token for the matching RETURN.
                _tok = self._mint_session_token()
                hello = wrap_hello(_tok)
                _send_frame(s, hello)
                _rpc_trace("connect_ok", host=self.host, port=self.port,
                           attempt=attempt + 1, connect_ms=_connect_ms,
                           fd=s.fileno(), session_token=_tok)
                print(f"[DIAG-WORKER] send_sock connected to {self.host}:{self.port} "
                      f"fd={s.fileno()} sent HELLO return_ip={_tok}", flush=True)
                # [LINK_POTENTIAL_PING_REMOVED 2026-06-04] The connect-time
                # ladder probe is gone. It was the only thing this path put on
                # the wire, and it fed the saturation_ratio - a link-only model
                # (it subtracted far-side processing) divided against full-RPC
                # observed time (which includes processing), so the ratio
                # conflated link saturation with remote-endpoint slowness.
                # Link quality is now the true end-to-end RTT (processing
                # included) measured passively from real RPCs over the sliding
                # window. _run_link_potential_ladder is dead and may be deleted.
                self._connect_fails = 0   # [CONNECT_TRIES_RETIRE_V1] answered 9009 -> streak clear
                _nf_clear_strikes(self.host)   # [THREE_STRIKES_V1] it answered
                s.settimeout(15.0)
                return s
            except ConnectionResetError as e:
                last_exc = e
                _rpc_trace("connect_fail", host=self.host, port=self.port,
                           attempt=attempt + 1,
                           exc_type="ConnectionResetError",
                           errno=getattr(e, "errno", "n/a"), msg=str(e))
                try:
                    s.close()
                except Exception:
                    pass
                if attempt < 3:
                    time.sleep(0.5)
                    continue
                raise
            except Exception as e:
                _rpc_trace("connect_fail", host=self.host, port=self.port,
                           attempt=attempt + 1, exc_type=type(e).__name__,
                           errno=getattr(e, "errno", "n/a"), msg=str(e))
                try:
                    s.close()
                except Exception:
                    pass
                raise
        raise last_exc

    def _open_return_channel(self):
        """Open second connection to daemon:9009 as the return channel.

        Sends HELLO with body 'RETURN:<local_gw>'.  The daemon's server
        routes this to the existing session (matched by return_ip) and
        calls session.set_send_sock(), giving the daemon a socket to
        write replies on.

        This eliminates the need for the daemon to connect BACK to
        proxy:9010, which fails under WiFi client isolation.
        """
        trace_enter('transport_semantic._DaemonWorker._open_return_channel')
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        s.settimeout(5.0)
        _t0 = time.time()
        _phase = "connect"
        try:
            s.connect((self.host, self.port))
            _phase = "send_hello"
            hello = wrap_hello(f"RETURN:{self._session_token or _get_local_gw()}")
            _send_frame(s, hello)
            _phase = "recv_hello_reply"
            # Read daemon's HELLO reply confirms return channel is live
            frame = _recv_frame(s)
            s.settimeout(5.0)  # reader uses 5s timeout for gen checks
            print(f"[DIAG-RETURN] {self.host}:{self.port} OK fd={s.fileno()} "
                  f"elapsed={time.time()-_t0:.2f}s "
                  f"token={self._session_token or 'gw'}", flush=True)
            return s
        except Exception as e:
            # [RETURN_DIAG_V1] The phase says exactly which side stalled, so a
            # persistent .2 failure can be triaged without guessing:
            #   connect          -> daemon not accepting on :9009 (down/filtered)
            #   send_hello       -> connection dropped mid-HELLO
            #   recv_hello_reply -> daemon ACCEPTED and got our HELLO but never
            #                       replied (daemon-side: session match / return
            #                       routing wedged) - this is the silent-daemon
            #                       case behind "no RETURN connection".
            print(f"[DIAG-RETURN] {self.host}:{self.port} FAIL phase={_phase} "
                  f"elapsed={time.time()-_t0:.2f}s timeout=5.0s "
                  f"token={self._session_token or 'gw'} "
                  f"ever_had_reader={self._ever_had_reader} err={e!r}", flush=True)
            try:
                s.close()
            except Exception:
                pass
            raise

    def _get_or_connect(self) -> socket.socket:
        """Get existing send socket or connect a new one.

        Uses only scoped `with` blocks - no manual release/acquire.
        _connect_lock serializes connection attempts.
        _state_lock is held only briefly to read/write socket pointers."""
        trace_enter('transport_semantic._DaemonWorker._get_or_connect')
        with self._state_lock:
            if self._send_sock is not None:
                return self._send_sock

        with self._connect_lock:
            # Double-check after acquiring connect_lock
            with self._state_lock:
                if self._send_sock is not None:
                    return self._send_sock

            # Connect OUTSIDE all locks - this can take seconds
            new_sock = self._open_send_sock()

            # Swap in under state_lock
            with self._state_lock:
                self._send_sock = new_sock
                self._send_sock_at = time.time()
                self._seq = 0
                self._unknown_streak = 0

            # [RETURN_ONLY_V1] Open proxy-initiated return channel.
            # This is the ONLY reply-channel path.  Failure means the send
            # sock is useless - tear it down so the caller gets a clean
            # error and the next call triggers a full reconnect.
            try:
                ret_sock = self._open_return_channel()
                self._register_recv_sock(ret_sock)
            except Exception as e:
                print(f"[DIAG-WORKER] [RETURN_ONLY_V1] RETURN channel to "
                      f"{self.host} failed: {e!r} - tearing down send sock",
                      flush=True)
                self._close(err=e)
                raise

            return new_sock

    def _register_recv_sock(self, sock: socket.socket) -> None:
        """Called by proxy listener (port 9010) when daemon connects back.
        Proof this is a real FrogNet daemon - clear all failure state.
        Increments _reader_gen so stale readers self-evict."""
        trace_enter('transport_semantic._DaemonWorker._register_recv_sock', sock=repr(sock))
        with self._state_lock:
            old = self._recv_sock
            self._recv_sock = sock
            self._reader_alive = True
            # [FIRST_CONNECT_GRACE_V1] Daemon has connected back -
            # peer is proven real.  Future dead-worker checks use
            # the 5s threshold instead of _RPC_TIMEOUT_SEC.
            self._ever_had_reader = True
            self._reader_gen += 1
            gen = self._reader_gen
        # [ROTATE_QUIET_V1] Wake the old reader cleanly via shutdown
        # so its blocked recv() returns EOF (-> "socket closed"
        # RuntimeError caught by the reader's normal exit path).
        # Do NOT close() here: close() races with the blocked recv()
        # and produces OSError(9, 'Bad file descriptor') ~once per
        # rotation, polluting the log without changing behavior.
        # The old reader closes the socket itself on exit (line ~990).
        if old is not None:
            try:
                old.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
        fd = sock.fileno()
        print(f"[DIAG-WORKER] recv_sock registered from {self.host} "
              f"fd={fd} gen={gen}", flush=True)
        sock.settimeout(5.0)  # reader wakes every 5s to check gen
        t = threading.Thread(target=self._read_loop, args=(sock, gen),
                             daemon=True, name=f"sem-reader-{self.host}")
        t.start()

    # -- Failure handling ----------------------------------------------

    def _fail_all_pending(self, err: Exception) -> None:
        # [ADAPTIVE_TIMEOUT_V1] One _Rpc may occupy multiple seqs
        # (retries); dedupe so we signal done exactly once per RPC.
        trace_enter('transport_semantic._DaemonWorker._fail_all_pending', err=repr(err))
        with self._pending_lock:
            seen = set()
            for rpc in self._pending.values():
                rid = id(rpc)
                if rid in seen:
                    continue
                seen.add(rid)
                if rpc.reply is None and rpc.err is None:
                    rpc.err = err
                rpc.done.set()
            self._pending.clear()

    def _close(self, err: Optional[Exception] = None) -> None:
        trace_enter('transport_semantic._DaemonWorker._close', err=repr(err))
        with self._state_lock:
            self._reader_alive = False
            old_send = self._send_sock
            old_recv = self._recv_sock
            # [CONNECT_DIAL_BUDGET_V1] A dial in flight belongs to the session
            # being torn down. Dropping the reference without closing the fd
            # would leak it, and leaving it set would let the next cycle poll a
            # socket that no longer relates to anything.
            old_dial = self._dialing
            self._dialing = None
            self._dial_started_at = 0.0
            self._send_sock = None
            self._recv_sock = None
            self._send_sock_at = 0.0
        for s in (old_send, old_recv, old_dial):
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
        if old_send is not None or old_recv is not None:
            print(f"[DIAG-WORKER] CLOSE sockets to {self.host}", flush=True)
        if err:
            self._fail_all_pending(err)

    # -- Termination ---------------------------------------------------

    def is_stopped(self) -> bool:
        return self._stopped.is_set()

    def stopped_reason(self) -> str:
        return self._stopped_reason or "unset"

    def _retire(self, reason: str, err: Optional[Exception] = None) -> None:
        """[WORKER_EXIT_REASON_V1] The ONLY way this worker stops.

        Setting _stopped was previously enough to kill both loops, and two
        places did it without doing anything else. A stopped worker that is
        still in _WORKERS is a queue with no consumer: call() enqueues, nothing
        drains, and the caller blocks the full safety cap. Compare
        channel_sets._reap_worker, which has always popped from _WORKERS BEFORE
        calling stop() - that path was correct and this one was not.

        Four things must happen together, so they happen in one place:
          1. record WHY, before anything else observes the flag
          2. de-register, so no new work can be handed to this object
          3. drop sockets
          4. fail every pending RPC, so nobody waits on a corpse
        """
        first = not self._stopped.is_set()
        if first:
            self._stopped_reason = reason
            self._stopped_at = time.time()
        self._stopped.set()

        # De-register under the registry lock. Idempotent: a worker reaped by
        # channel_sets is already gone from _WORKERS and pop() is a no-op.
        with _WORKERS_LOCK:
            if _WORKERS.get((self.host, self.port, self._set_id)) is self:
                _WORKERS.pop((self.host, self.port, self._set_id), None)

        if err is None:
            err = RuntimeError(
                f"worker for {self.host}:{self.port} set={self._set_id} "
                f"retired: {reason}")
        self._close(err=err)
        # _close only fails pending when err is set; it is always set here.
        self._fail_all_pending(err)

        if first:
            with self._pending_lock:
                _n = len(self._pending)
            print(f"[SEM-TRANSPORT] [WORKER_EXIT_REASON_V1] RETIRED "
                  f"{self.host}:{self.port} set={self._set_id} "
                  f"reason={reason!r} err={err!r} "
                  f"de-registered=yes pending_failed={_n} "
                  f"reader_alive={self._reader_alive} "
                  f"ever_had_reader={self._ever_had_reader} "
                  f"qsize={self.q.qsize()}", flush=True)

    def stop(self, err: Optional[Exception] = None) -> None:
        """[CHANNEL_SETS_V1] Terminate THIS set: signal its writer + cleanup
        loops to exit (within ~1s - both wake on a 1s tick), then drop sockets
        and fail pending. Used by the channel-set reaper when a transient set's
        last protocol is released, so a set's threads die with the set rather
        than leaking. Idempotent; a never-stopped worker is unaffected."""
        self._retire("channel_set_reaped", err=err)

    # -- Cleanup thread ------------------------------------------------

    def _cleanup_loop(self) -> None:
        """[ADAPTIVE_TIMEOUT_V1] Runs every second.  Three jobs:
        1. For each _Rpc in _pending, retry if age exceeds
           attempts x retry_budget; reap if max_attempts already done.
        2. If send sock is up but no reader callback, close it.
        3. If both sockets are down on a known peer, try to reconnect.
        """
        trace_enter('transport_semantic._DaemonWorker._cleanup_loop')
        # [WORKER_EXIT_REASON_V1] Every path out of this thread names itself.
        # It had three silent exits and the log showed none of them.
        _exit_reason = "unset"
        _started_at = time.time()
        _ticks = 0
        try:
            while not self._stopped.is_set():
                time.sleep(1.0)
                _ticks += 1
                if self._stopped.is_set():
                    _exit_reason = f"stopped:{self.stopped_reason()}"
                    break          # reaped: exit before any reconnect/retry work
                now = time.time()

                # [NOT_FROGNET_V1] If this peer was proven non-FrogNet this
                # session (refused/no-route, or no-echo at discovery), stop
                # maintaining a connection to it.  _dispatch_rpc already
                # refuses new RPCs; this retires the per-host worker so its
                # reconnect loop stops re-dialing a dead end (the source of the
                # refused-connection storm).  The next merge's flush clears the
                # mark and allows one fresh probe.
                #
                # [WORKER_EXIT_REASON_V1] This was `_close(); _stopped.set();
                # break` - which killed both threads and LEFT THE WORKER IN
                # _WORKERS. When the next merge flushed the mark, _dispatch_rpc
                # stopped fast-failing, _get_worker handed back this dead
                # object, and every RPC to the peer rotted for 60s with no log
                # line. That is the New-York-2 wedge. _retire() de-registers.
                if _nf_is_marked(self.host):
                    print(f"[CLEANUP] {self.host}: cached non-FrogNet this "
                          f"session - retiring worker, no reconnects until "
                          f"next merge", flush=True)
                    self._retire(
                        "not_frognet_marked",
                        err=RuntimeError(f"{self.host} cached non-FrogNet"))
                    _exit_reason = "not_frognet_marked"
                    break

                # --- Job 1: adaptive retry/reap driven by per-peer EWMA ---
                budget = self._peer_latency.retry_budget()
                to_retry = []
                to_reap = []

                with self._pending_lock:
                    seen = set()
                    for seq, rpc in list(self._pending.items()):
                        rid = id(rpc)
                        if rid in seen:
                            continue
                        seen.add(rid)
                        if rpc.retry_pending or rpc.done.is_set():
                            continue
                        age = now - rpc.submitted_at
                        # Reap if max attempts made AND last budget window elapsed.
                        if rpc.attempts >= rpc.max_attempts:
                            if age > rpc.max_attempts * budget:
                                to_reap.append(rpc)
                        elif age > rpc.attempts * budget:
                            rpc.retry_pending = True
                            to_retry.append(rpc)

                    # Clear reaped seqs from _pending under the same lock.
                    for rpc in to_reap:
                        for sq in rpc.seqs:
                            self._pending.pop(sq, None)
                            self._recent_reaped.append(sq)

                for rpc in to_retry:
                    age = now - rpc.submitted_at
                    print(f"[CLEANUP] RETRY {self.host} "
                          f"attempt={rpc.attempts + 1}/{rpc.max_attempts} "
                          f"age={age:.1f}s budget={budget:.1f}s "
                          f"ewma={self._peer_latency.current_ewma():.2f}s",
                          flush=True)
                    self.q.put(rpc)

                # [NO_SESSION_KILL_V1] Request-level timeouts are NOT
                # evidence that the TCP socket is dead.  Previous code
                # here closed the socket after 3 consecutive reaps,
                # which caused the peer daemon to accumulate stale
                # sessions on every reconnect.  We now fail the
                # individual RPCs and leave the transport up.
                #
                # [BUDGET_FEEDBACK_FIX] We do NOT feed reap ages back
                # into EWMA.  V2 did, on the theory that EWMA needed
                # to grow to accommodate slow peers.  In practice this
                # creates a feedback loop: slow peer -> reap -> EWMA
                # grows -> bigger budget -> longer reap -> EWMA grows
                # more -> 30s peg -> 90s budget -> congestive collapse.
                # The hard budget_ceiling in _PeerLatency now bounds
                # the timeout regardless of EWMA, so the V2 motivation
                # ("budget can grow when peer is slower than floor")
                # no longer applies.  EWMA tracks success RTT only.
                if to_reap:
                    for rpc in to_reap:
                        age = now - rpc.submitted_at
                        rpc.err = TimeoutError(
                            f"RPC to {self.host} failed after "
                            f"{rpc.attempts} attempts "
                            f"({age:.1f}s, budget {budget:.1f}s)")
                        rpc.done.set()
                        _rpc_trace("rpc_reaped", host=self.host,
                                   attempts=rpc.attempts, age_s=f"{age:.1f}",
                                   budget_s=f"{budget:.1f}",
                                   ewma_s=f"{self._peer_latency.current_ewma():.2f}",
                                   reader_alive=self._reader_alive,
                                   ever_had_reader=self._ever_had_reader,
                                   pending_count=len(self._pending))
                    print(f"[CLEANUP] {self.host}: reaped {len(to_reap)} "
                          f"RPCs (ewma now "
                          f"{self._peer_latency.current_ewma():.2f}s, "
                          f"budget {budget:.1f}s)",
                          flush=True)
                # --- Job 2: dead worker detection ---
                # [FIRST_CONNECT_GRACE_V1] First-time peer: give it
                # _RPC_TIMEOUT_SEC for the daemon callback to arrive
                # (the WG return path may take that long to establish).
                # Once the peer has proven real (daemon has connected
                # back at least once), drop to 5s for ongoing health
                # checks so a transient stall is caught fast.
                dead_thresh = 5.0 if self._ever_had_reader else _RPC_TIMEOUT_SEC
                sock_age = (now - self._send_sock_at) if self._send_sock_at > 0 else 0
                if (self._send_sock is not None
                        and not self._reader_alive
                        and sock_age > dead_thresh):
                    _rpc_trace("no_daemon_callback", host=self.host,
                               sock_age_s=f"{sock_age:.0f}",
                               dead_thresh_s=f"{dead_thresh:.0f}",
                               ever_had_reader=self._ever_had_reader,
                               pending_count=len(self._pending),
                               session_token=self._session_token or "none")
                    print(f"[CLEANUP] {self.host}: send sock up {sock_age:.0f}s, "
                          f"no reader (ever_had_reader={self._ever_had_reader}, "
                          f"thresh={dead_thresh:.0f}s) - closing", flush=True)
                    self._close(err=RuntimeError(
                        f"peer {self.host}: no daemon callback after "
                        f"{sock_age:.0f}s"))

                # [STUCK_SOCK_CEILING_V1] Safety net: the path above only
                # fires when _reader_alive is False.  If the reader thread
                # is wedged in recv() on a stuck remote (TCP_KEEPALIVE may
                # be reset by partial frames or noise), _reader_alive stays
                # True forever, sock_age grows unbounded, and pending RPCs
                # rot until something else (e.g. a writer error path) trips.
                # Logs from the fleet show send-sock ages of 800+ seconds
                # correlated with the worst error cascades.
                #
                # Only force-close if BOTH (a) the sock has been open beyond
                # the hard ceiling AND (b) there is at least one pending RPC
                # whose age vastly exceeds the budget (i.e. the reader is
                # not making progress).  A healthy long-lived idle connection
                # has no pending RPCs, so this never trips for it.
                if (self._send_sock is not None
                        and sock_age > _STUCK_SOCK_CEILING_SEC):
                    stuck_pending_age = 0.0
                    with self._pending_lock:
                        for rpc in self._pending.values():
                            a = now - rpc.submitted_at
                            if a > stuck_pending_age:
                                stuck_pending_age = a
                    if stuck_pending_age > _STUCK_SOCK_CEILING_SEC:
                        print(f"[CLEANUP] {self.host}: send sock up "
                              f"{sock_age:.0f}s exceeds ceiling "
                              f"({_STUCK_SOCK_CEILING_SEC:.0f}s) AND oldest "
                              f"pending RPC age {stuck_pending_age:.0f}s - "
                              f"force-closing as wedged "
                              f"(reader_alive={self._reader_alive})",
                              flush=True)
                        self._close(err=RuntimeError(
                            f"peer {self.host}: send sock wedged for "
                            f"{sock_age:.0f}s, pending age "
                            f"{stuck_pending_age:.0f}s"))

                # --- Job 3: reconnect ---
                # Both sockets dead on a known peer.  Open send socket and
                # send HELLO so the daemon can connect back.  Rate-limited
                # to one attempt every 5 seconds.
                # [RECONNECT_SERIALIZE_V1] Acquire _connect_lock and re-check
                # under it, mirroring _get_send_sock.  Without this, a worker
                # in _get_send_sock and this cleanup thread could both open a
                # send sock at once -> two daemon sessions for this peer.  With
                # [SESSION_TOKEN_V1] they no longer collide, but the loser's
                # session/reader still leaks and churns; serializing avoids
                # opening a second session at all.
                # [CONNECT_DIAL_BUDGET_V1] A dial already in flight is polled on
                # EVERY cycle (~1s), not once per 5s: the 5s throttle exists to
                # rate-limit new SYNs, and applying it to an in-progress dial
                # would waste most of the budget waiting to look at it.
                _dial_in_flight = self._dialing
                if (self._send_sock is None
                        and not self._reader_alive
                        and (_dial_in_flight is not None
                             or (now - self._last_reconnect_at) >= 5.0)):
                    if _dial_in_flight is None:
                        self._last_reconnect_at = now
                    got_lock = self._connect_lock.acquire(blocking=False)
                    if not got_lock:
                        # _get_send_sock is already reconnecting - let it win.
                        continue
                    try:
                        # Re-check under the lock: _get_send_sock may have
                        # established the sock between our gate and the lock.
                        with self._state_lock:
                            already = (self._send_sock is not None
                                       or self._reader_alive)
                        if already:
                            continue
                        if _dial_in_flight is not None:
                            # [CONNECT_DIAL_BUDGET_V1] Same socket, same SYN,
                            # more time. Options were set when it was opened.
                            s = _dial_in_flight
                        else:
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        # [KEEPALIVE_PARITY_V1] Match _open_send_sock /
                        # _open_return_channel.  Without keepalive the kernel
                        # cannot detect a dead remote on this send socket; a
                        # wedged daemon survives in ESTABLISHED forever and
                        # writes only fail at the 15s settimeout below.  All
                        # three socket-creation paths in this module now use
                        # the same keepalive shape.
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                        # [NONBLOCK_CONNECT_V1] Non-blocking connect, polled.
                        #
                        # This was `s.settimeout(10.0); s.connect(...)` -- a
                        # BLOCKING call on the thread that also resolves every RPC
                        # for this peer (Job 1 above).  One unreachable peer parked
                        # cleanup for a full 10s, plus up to 15s more on the HELLO
                        # write below, and every RPC queued for this worker waited
                        # behind it.  call() then hit its 60s safety cap and raised
                        # "cleanup thread appears stalled" -- literally true, and
                        # why a peer answering ping fine was unreachable on :9009.
                        #
                        # The thread that owns RPC resolution must never block.
                        #
                        # [CONNECT_DIAL_BUDGET_V1] ...but it must also not throw
                        # the dial away. The V1 code did `s.close(); continue` on
                        # every deferral, so each cycle sent a NEW SYN that got
                        # 0.25s and was discarded: the dial never accumulated. A
                        # peer needing more than one 250ms round trip could never
                        # connect, collected three "failures" in ~10s, and was
                        # marked non-FrogNet -- which is the "cached non-FrogNet"
                        # storm, on peers that were up the whole time.
                        #
                        # Now the socket is CARRIED across cleanup cycles. The
                        # thread is still held for at most _CONNECT_POLL_S per
                        # cycle; the dial gets _CONNECT_DIAL_BUDGET_S of wall
                        # clock before it counts as a failed try. Those two
                        # numbers are independent and both are honoured.
                        _CONNECT_POLL_S = 0.25      # max time we hold this thread
                        if _dial_in_flight is None:
                            s.setblocking(False)
                            _err = s.connect_ex((self.host, self.port))
                            if _err not in (0, errno.EINPROGRESS, errno.EALREADY,
                                            errno.EWOULDBLOCK):
                                raise OSError(_err, os.strerror(_err))
                            if _err != 0:
                                with self._state_lock:
                                    self._dialing = s
                                    self._dial_started_at = now
                        if self._dialing is s:
                            _r, _w, _x = select.select([], [s], [s], _CONNECT_POLL_S)
                            if not _w and not _x:
                                _dial_age = time.time() - self._dial_started_at
                                if _dial_age < _CONNECT_DIAL_BUDGET_S:
                                    # Still connecting: not an error, not a
                                    # failure. Keep the socket, release the
                                    # thread, poll the SAME dial next cycle.
                                    # Deliberately NOT counted in _connect_fails
                                    # -- an in-progress dial is not a failed one.
                                    _debug(f"[CLEANUP] {self.host}: connect still "
                                           f"in progress after {_dial_age:.2f}s of "
                                           f"{_CONNECT_DIAL_BUDGET_S:.1f}s budget "
                                           f"- carrying to next cycle")
                                    continue
                                # Budget spent. NOW it is a failed try.
                                with self._state_lock:
                                    self._dialing = None
                                    self._dial_started_at = 0.0
                                raise OSError(
                                    errno.ETIMEDOUT,
                                    "no :9009 handshake in %.1fs "
                                    "(FROGNET_CONNECT_DIAL_BUDGET_S)"
                                    % _CONNECT_DIAL_BUDGET_S)
                            with self._state_lock:
                                self._dialing = None
                                self._dial_started_at = 0.0
                            _so_err = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                            if _so_err != 0:
                                raise OSError(_so_err, os.strerror(_so_err))
                        s.setblocking(True)
                        # [SESSION_TOKEN_V1] Unique HELLO body; the RETURN
                        # opened just below reuses self._session_token.
                        hello = wrap_hello(self._mint_session_token())
                        _send_frame(s, hello)
                        s.settimeout(15.0)
                        with self._state_lock:
                            self._send_sock = s
                            self._send_sock_at = now
                            self._seq = 0
                            self._unknown_streak = 0
                            self._connect_fails = 0   # [CONNECT_TRIES_RETIRE_V1] answered 9009 -> streak clear
                            _nf_clear_strikes(self.host)   # [THREE_STRIKES_V1]
                        print(f"[CLEANUP] {self.host}: reconnect HELLO sent",
                              flush=True)
                        # [RETURN_ONLY_V1] RETURN is the ONLY reply path.
                        # Failure means this reconnect attempt is useless;
                        # tear down so the next loop can try fresh.
                        try:
                            ret = self._open_return_channel()
                            self._register_recv_sock(ret)
                        except Exception as re:
                            _debug(f"[CLEANUP] [RETURN_ONLY_V1] {self.host}: "
                                   f"RETURN failed: {re!r} - closing")
                            self._close(err=re)
                    except Exception as e:
                        # [CONNECT_DIAL_BUDGET_V1] Whatever went wrong, this dial
                        # is over: drop the carried reference before closing the
                        # fd, or the next cycle polls a closed socket.
                        with self._state_lock:
                            if self._dialing is not None:
                                self._dialing = None
                                self._dial_started_at = 0.0
                        try:
                            s.close()
                        except OSError as ce:
                            print(f"[CLEANUP] {self.host}: close of failed dial "
                                  f"socket failed: {ce!r}", flush=True)
                        self._connect_fails += 1
                        # [CONNECT_TRIES_RETIRE_V1] A peer that fails to establish a :9009
                        # session in _CONNECT_MAX_TRIES tries is not answering on 9009 and
                        # does not belong in
                        #
                        # [CONNECT_DIAL_BUDGET_V1] The old text here claimed "~30s,
                        # enough for a slow downstream radio". It was describing the
                        # blocking 10s connect that [NONBLOCK_CONNECT_V1] had already
                        # replaced with a 0.25s poll, so the real window was ~10s of
                        # throttle containing 0.75s of actual dialling. The arithmetic
                        # now matches the words: _CONNECT_MAX_TRIES dials, each given
                        # _CONNECT_DIAL_BUDGET_S of wall clock, separated by the 5s
                        # throttle -> 3 x 3s of dialling inside a ~24s window.
                        # the mix. Retire it for this runMerge. Unlike the definitive-only
                        # rule below, ANY cause counts here -- timeouts and resets included --
                        # so a gone client (e.g. a stray .20 whose connects only ever time
                        # out) stops re-dialing every 5s. mark() still spares protected .1/.2
                        # roles (real members with transient downtime); the next merge's
                        # flush clears the mark and allows one fresh probe.
                        # == not >=: announce the retirement ONCE. On >= this
                        # re-fired on every subsequent failure, turning one event
                        # into 6087 log lines from a single PID in an hour.
                        if self._connect_fails == _CONNECT_MAX_TRIES:
                            # [CONNECT_TRIES_RETIRE_V2] retire() -- NOT mark(). A down .1/.2 is
                            # still FrogNet, so it must never enter the non-FrogNet cache; but
                            # after 3 failed :9009 connects the transport must still stop
                            # dialing it for this merge. retire() covers any address and any
                            # cause and clears at the next merge's flush.
                            _nf_mark(self.host,
                                     reason=f"{self._connect_fails} failed :9009 connects")
                            print(f"[CLEANUP] {self.host}: {self._connect_fails} failed :9009 "
                                  f"connects - retiring for this runMerge ({e!r})", flush=True)
                        # [NOT_FROGNET_V1] A refused/no-route reconnect to a non-role
                        # address means nothing FrogNet is there - cache it so the
                        # top-of-loop check retires this worker on the next pass
                        # instead of re-dialing forever.  Protected roles (.1/.2)
                        # are never cached (mark_if_definitive returns False); for
                        # them this is downtime and we keep retrying.
                        elif _nf_mark_if_definitive(self.host, e):
                            print(f"[CLEANUP] {self.host}: reconnect refused/no-route "
                                  f"({e!r}) - cached non-FrogNet, retiring", flush=True)
                        else:
                            _debug(f"[CLEANUP] {self.host}: reconnect failed: {e!r}")
                    finally:
                        # [RECONNECT_SERIALIZE_V1] Always release; the
                        # `if already: continue` path exits through here too.
                        self._connect_lock.release()
            else:
                _exit_reason = "stopped_gate"
        except Exception as e:
            _exit_reason = f"exception:{type(e).__name__}"
            print(f"[CLEANUP] {self.host}: cleanup thread DIED on "
                  f"{type(e).__name__}: {e!r} - no RPC to this peer can be "
                  f"retried or reaped from here on", flush=True)
            raise
        finally:
            # [WORKER_EXIT_REASON_V1] The cleanup thread is the sole timeout
            # mechanism for this peer. If it is gone, call() can only fail at
            # the safety cap. That must never be a silent event.
            with self._pending_lock:
                _pending_n = len(self._pending)
            print(f"[CLEANUP] [WORKER_EXIT_REASON_V1] cleanup thread exited "
                  f"for {self.host}:{self.port} set={self._set_id} "
                  f"reason={_exit_reason} "
                  f"lifetime_s={time.time() - _started_at:.1f} ticks={_ticks} "
                  f"stopped={self._stopped.is_set()} "
                  f"stopped_reason={self.stopped_reason()!r} "
                  f"pending={_pending_n} qsize={self.q.qsize()} "
                  f"reader_alive={self._reader_alive} "
                  f"tid={threading.get_ident()}", flush=True)

    # -- Writer thread -------------------------------------------------

    def _write_loop(self) -> None:
        """[ADAPTIVE_TIMEOUT_V1] Queue drain.

        No queue-age timeout: time-based resolution lives in the
        cleanup thread, driven by per-peer EWMA.  If a retry's parent
        _Rpc was already resolved (reply arrived via an earlier seq or
        cleanup reaped after max_attempts) we skip sending.
        """
        trace_enter('transport_semantic._DaemonWorker._write_loop')
        # [WORKER_EXIT_REASON_V1] The writer is the only consumer of self.q.
        # When it returned on the _stopped gate it did so in silence, and every
        # later call() enqueued into a queue nobody was draining. Name the exit.
        _exit_reason = "unset"
        _started_at = time.time()
        _sent = 0
        try:
            while not self._stopped.is_set():
                try:
                    rpc = self.q.get(timeout=1.0)
                except queue.Empty:
                    continue

                # Parent already resolved (winner arrived, or reaped)?  Skip.
                if rpc.err is not None or rpc.done.is_set():
                    rpc.retry_pending = False
                    continue

                try:
                    sock = self._get_or_connect()
                    with self._state_lock:
                        seq = self._seq
                        self._seq += 1
                    rpc.attempts += 1
                    rpc.seqs.append(seq)
                    rpc.sent_at[seq] = time.time()
                    rpc.retry_pending = False
                    with self._pending_lock:
                        self._pending[seq] = rpc
                    _send_frame(sock, rpc.packet)
                    print(f"[DIAG-WRITER] SENT seq={seq} {len(rpc.packet)}B "
                          f"to {self.host} attempt={rpc.attempts}/"
                          f"{rpc.max_attempts} pending={len(self._pending)}",
                          flush=True)
                    _sent += 1
                except Exception as e:
                    print(f"[DIAG-WRITER] SEND ERROR {e!r} to {self.host}",
                          flush=True)
                    rpc.err = e
                    rpc.done.set()
                    self._close(err=e)
            else:
                _exit_reason = f"stopped:{self.stopped_reason()}"
        except Exception as e:
            _exit_reason = f"exception:{type(e).__name__}"
            raise
        finally:
            # A writer that is gone means self.q has no consumer. Anything
            # still queued, and anything enqueued after this, can only end at
            # call()'s safety cap - so say so here, once, with the count.
            print(f"[DIAG-WRITER] [WORKER_EXIT_REASON_V1] writer exited for "
                  f"{self.host}:{self.port} set={self._set_id} "
                  f"reason={_exit_reason} "
                  f"lifetime_s={time.time() - _started_at:.1f} sent={_sent} "
                  f"qsize_abandoned={self.q.qsize()} "
                  f"stopped_reason={self.stopped_reason()!r} "
                  f"tid={threading.get_ident()}", flush=True)

    # -- Reader thread -------------------------------------------------

    def _read_loop(self, my_sock: socket.socket, my_gen: int) -> None:
        """[ADAPTIVE_TIMEOUT_V1] Read replies, match by seq, signal callers.

        On a match, mark all sibling seqs (from other retries of the
        same _Rpc) as recently-reaped and drop their entries from
        _pending.  Their late replies will hit _recent_reaped in a
        future iteration and be silently dropped rather than counted
        as "unknown."

        Only the winning attempt's RTT is observed for EWMA.
        Three-strikes seq-desync check remains, but now only fires
        on genuine framing corruption - late replies no longer trigger
        it, which was the original bug.
        """
        trace_enter('transport_semantic._DaemonWorker._read_loop', my_sock=repr(my_sock), my_gen=repr(my_gen))
        print(f"[DIAG-READER] started for {self.host} gen={my_gen}",
              flush=True)
        # [READER_EXIT_REASON_V1] Every path out of this thread names itself.
        # There is exactly one silent exit (rotation), and it says so.
        _exit_reason = "unset"
        _exit_exc = None
        _started_at = time.time()
        _frames = 0
        _nbytes = 0
        _last_seq = -1
        try:
            while True:
                if self._reader_gen != my_gen:
                    _exit_reason = "rotated_out"
                    print(f"[DIAG-READER] stale gen={my_gen} "
                          f"(current={self._reader_gen}) for {self.host}",
                          flush=True)
                    break
                try:
                    frame = _recv_frame(my_sock)
                except socket.timeout:
                    continue
                _frames += 1
                _nbytes += len(frame)
                if len(frame) < 4:
                    raise RuntimeError(f"reply frame too short: {len(frame)}B")
                seq = struct.unpack("!I", frame[:4])[0]
                _last_seq = seq
                reply_data = frame[4:]

                with self._pending_lock:
                    rpc = self._pending.pop(seq, None)

                if rpc is None:
                    # Known-abandoned seq?  Drop silently, don't desync.
                    if seq in self._recent_reaped:
                        print(f"[DIAG-READER] LATE seq={seq} from "
                              f"{self.host} (abandoned, dropping)",
                              flush=True)
                        continue
                    self._unknown_streak += 1
                    print(f"[DIAG-READER] UNKNOWN seq={seq} from {self.host} "
                          f"streak={self._unknown_streak}", flush=True)
                    if self._unknown_streak >= 3:
                        raise RuntimeError(
                            f"seq desync: {self._unknown_streak} unknown")
                    continue

                self._unknown_streak = 0

                # Sibling retry already won?  Drop silently.
                if rpc.done.is_set():
                    self._recent_reaped.append(seq)
                    print(f"[DIAG-READER] DUP seq={seq} from {self.host} "
                          f"(sibling already won)", flush=True)
                    continue

                # Winning reply.  Observe RTT, drop siblings, signal caller.
                sent = rpc.sent_at.get(seq)
                if sent is not None:
                    self._peer_latency.observe(time.time() - sent)

                rpc.reply = reply_data
                with self._pending_lock:
                    for sib in rpc.seqs:
                        if sib != seq:
                            self._pending.pop(sib, None)
                            self._recent_reaped.append(sib)
                rpc.done.set()
                print(f"[DIAG-READER] RECV seq={seq} {len(reply_data)}B "
                      f"from {self.host} attempt={rpc.attempts}/"
                      f"{rpc.max_attempts} ewma={self._peer_latency.current_ewma():.2f}s "
                      f"pending={len(self._pending)}", flush=True)
        except Exception as e:
            # [READER_EXIT_REASON_V1] This was a fallback: `except Exception`
            # printed one of two bland lines and let the thread end, and the
            # exit line below carried NO reason at all.  The channel then died
            # quietly and the cleanup thread papered over it with a reconnect,
            # so a reader dying every few minutes looked like normal churn
            # while every RPC in flight ate the 60s safety cap.  22 exits in an
            # hour with nothing recorded about any of them.
            #
            # The ONLY non-error exit is rotation: our generation was
            # superseded, so the exception IS the rotation unblocking us
            # (EOF from shutdown, or EBADF if a stray close raced).  That case
            # is named and quiet.  Everything else is a genuine failure of the
            # reply path and says so, with the exception, loudly.
            _exit_exc = e
            if self._reader_gen != my_gen:
                _exit_reason = "rotated_out_via_exc"
                print(f"[DIAG-READER] stale gen={my_gen} "
                      f"(current={self._reader_gen}, exit={e!r}) "
                      f"for {self.host}", flush=True)
            else:
                _exit_reason = f"exception:{type(e).__name__}"
                print(f"[DIAG-READER] ERROR {e!r} from {self.host} "
                      f"gen={my_gen}", flush=True)
        else:
            # Fell out of `while True` without an exception and without the
            # rotation break: there is no such path today.  If one ever
            # appears, it is named rather than silent.
            if _exit_reason == "unset":
                _exit_reason = "loop_returned_no_reason"
        # [READER_EXIT_SIGNAL_V1] If we were the current-gen reader (not
        # a rotated-out predecessor), signal cleanup that the reader is
        # gone.  Cleanup thread's reconnect gate (Job 3) and dead-worker
        # detection (Job 2) both require _reader_alive=False; without
        # this, an unexpected reader exit leaves _reader_alive=True
        # forever and the channel wedges (no reconnect, RPCs time out
        # against a dead recv path indefinitely).  Done under state_lock
        # to avoid racing with _register_recv_sock setting it back True.
        # [READER_EXIT_REASON_V1] `except Exception: pass` here hid close()
        # failures entirely.  A close that raises means the fd was already
        # gone or is in a state we did not expect, which is exactly the kind
        # of thing that precedes a wedged channel.  Name it.
        try:
            my_sock.close()
        except Exception as ce:
            print(f"[DIAG-READER] close failed for {self.host} gen={my_gen}: "
                  f"{ce!r}", flush=True)
        with self._state_lock:
            if self._reader_gen == my_gen:
                self._reader_alive = False
        # Everything the exit needs to be explained without a second run.
        # Deliberately not a curated subset: the hypothesis is what might be
        # wrong, so log all of the in-scope state.
        with self._pending_lock:
            _pending_n = len(self._pending)
        print(f"[DIAG-READER] exited for {self.host} gen={my_gen} "
              f"reason={_exit_reason} exc={_exit_exc!r} "
              f"reader_gen_now={self._reader_gen} "
              f"lifetime_s={time.time() - _started_at:.1f} "
              f"frames={_frames} bytes={_nbytes} last_seq={_last_seq} "
              f"session_token={self._session_token or 'none'} "
              f"reader_alive={self._reader_alive} "
              f"ever_had_reader={self._ever_had_reader} "
              f"pending={_pending_n} "
              f"send_sock={'up' if self._send_sock is not None else 'down'} "
              f"unknown_streak={self._unknown_streak} "
              f"tid={threading.get_ident()}", flush=True)

    # -- Public API ----------------------------------------------------

    def call(self, packet: bytes) -> bytes:
        """[ADAPTIVE_TIMEOUT_V1] Enqueue and block until cleanup resolves.

        Cleanup thread owns the time-based resolution: it will either
        signal rpc.done with a reply (reader) or with TimeoutError
        (after max_attempts x retry_budget elapsed).  call()'s own
        wait has a generous safety cap so a dead cleanup thread does
        not hang us forever.
        """
        trace_enter('transport_semantic._DaemonWorker.call', packet=repr(packet))

        # [WORKER_EXIT_REASON_V1] Refuse immediately if this worker is retired.
        # Nothing drains self.q once the writer has exited, so without this the
        # caller waits the full safety cap for a reply that cannot arrive. This
        # check is on _stopped, NOT on _send_sock_at: _close() zeroes
        # _send_sock_at, which is exactly what disabled the reader-dead guard
        # below and made a dead worker fail in the slowest possible way.
        if self._stopped.is_set():
            _rpc_trace("stopped_worker_refuse", host=self.host,
                       set_id=self._set_id,
                       stopped_reason=self.stopped_reason(),
                       stopped_age_s=f"{time.time() - self._stopped_at:.0f}",
                       qsize=self.q.qsize())
            raise RuntimeError(
                f"peer {self.host}:{self.port} set={self._set_id}: worker "
                f"retired ({self.stopped_reason()}) "
                f"{time.time() - self._stopped_at:.0f}s ago - not enqueueing")

        if not self._reader_alive and self._send_sock_at > 0:
            _rpc_trace("reader_dead_refuse", host=self.host,
                       send_sock_at=f"{self._send_sock_at:.0f}",
                       sock_age_s=f"{time.time() - self._send_sock_at:.0f}",
                       ever_had_reader=self._ever_had_reader)
            raise RuntimeError(
                f"peer {self.host}: reader dead")

        rpc = _Rpc(packet, max_attempts=_TRANSPORT_MAX_ATTEMPTS)
        self.q.put(rpc)

        # Safety cap only: cleanup normally resolves well before this.
        # max_attempts (3) * ceiling budget (3 * 30s = 90s) = 270s.
        # Double it for slop.
        _SAFETY_CAP = 60.0
        if not rpc.done.wait(timeout=_SAFETY_CAP):
            with self._pending_lock:
                for sq in list(rpc.seqs):
                    self._pending.pop(sq, None)
                    self._recent_reaped.append(sq)
            # [WORKER_EXIT_REASON_V1] This message used to assert
            # "cleanup thread appears stalled" unconditionally. On New-York-2
            # the cleanup thread did not exist - it had exited - and a thread
            # that is gone and a thread that is wedged are different faults
            # needing different fixes. Ask the threads whether they are alive
            # and report what is actually true, with the state that explains
            # which one it is.
            _cleaner_alive = self._cleaner.is_alive()
            _writer_alive = self._writer.is_alive()
            if not _cleaner_alive or not _writer_alive:
                _diag = (f"worker threads have EXITED "
                         f"(cleanup_alive={_cleaner_alive} "
                         f"writer_alive={_writer_alive} "
                         f"stopped={self._stopped.is_set()} "
                         f"stopped_reason={self.stopped_reason()!r}); "
                         f"nothing was draining the queue")
            else:
                _diag = (f"both worker threads are alive "
                         f"(attempts={rpc.attempts}/{rpc.max_attempts} "
                         f"retry_pending={rpc.retry_pending} "
                         f"seqs={list(rpc.seqs)}) - cleanup did not resolve "
                         f"this RPC in time")
            raise TimeoutError(
                f"RPC to {self.host}:{self.port} set={self._set_id}: safety "
                f"cap ({_SAFETY_CAP:.0f}s) hit; {_diag}")

        if rpc.err:
            raise rpc.err
        if rpc.reply is None:
            raise RuntimeError(f"RPC to {self.host}: signaled but no reply")

        return rpc.reply


# ====================================================================
# PROXY CALLBACK LISTENER (port 9010) - accepts daemon return connections
# ====================================================================

_WORKERS: Dict[Tuple[str, int, int], _DaemonWorker] = {}  # (host, port, set_id)
_WORKERS_LOCK = threading.RLock()

# Fix 3: per-target concurrency cap - one dead daemon cannot consume
# all 256 _ACTIVE slots.  32 is enough for legitimate coalesced bursts
# but prevents a single unreachable target from starving everything else.
_PER_TARGET_MAX = int(os.environ.get("FROGNET_PER_TARGET_MAX", "32"))
_per_target_sems: Dict[str, threading.BoundedSemaphore] = {}
_per_target_sems_lock = threading.Lock()

# [RETURN_ONLY_V1] _proxy_listener_started/lock removed.


def _is_frognet_ip(ip: str) -> bool:
    """Return True if ip is 10/8 except 10.253/16 (transit overlay).

    [PROBE-RULES-V2] V1 additionally required 10.x.x.1 gateway form, which
    wrongly rejected peer LAN IPs used as next-hops (e.g. a peer's address
    on the local /24). The actual rule is: no 10.253.*. Nothing else.
    Non-peer 10.x hosts (LAN devices, tunnel .2 far-ends) may still end up
    as worker targets; they'll fail with ConnectionRefused at TCP level,
    which is cheap and the correct failure mode.
    """
    trace_enter('transport_semantic._is_frognet_ip', ip=repr(ip))
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b, _c, _d = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return False
    if a != 10:
        return False
    if b == 253:          # transit overlay - never probe (rule)
        return False
    return True


# [RETURN_ONLY_V1] _proxy_listener_loop removed - proxy no longer accepts daemon callbacks.


# [RETURN_ONLY_V1] _ensure_proxy_listener removed - proxy no longer accepts daemon callbacks.


def _get_worker(host: str, port: int, set_id: int = 0) -> _DaemonWorker:

    trace_enter('transport_semantic._get_worker', host=repr(host), port=repr(port))
    if not _is_frognet_ip(host):
        raise RuntimeError(
            f"rejecting worker for {host}: not a frognet 10/8 host")
    # [RETURN_ONLY_V1] _ensure_proxy_listener() call removed.
    # [CHANNEL_SETS_V1] set_id selects an independent socket set for this peer;
    # set_id=0 is the shared default every legacy caller uses unchanged.
    # [NO_WORKER_FOR_A_MARKED_HOST_V1] Refuse before constructing anything.
    #
    # _get_worker is called at both dispatch sites BEFORE _dispatch_rpc checks
    # the mark, so every RPC to a marked host built a fresh _DaemonWorker -- two
    # threads -- whose cleanup loop saw the mark on its first tick and retired it
    # a second later. Measured: 28 retirements of 10.250.250.1 in four minutes,
    # which read as 28 discoveries and was one.
    #
    # Before [WORKER_EXIT_REASON_V1] this was invisible rather than absent: the
    # retired worker stayed in _WORKERS and _get_worker handed back the same
    # corpse, so nothing new was built or logged. De-registering the corpse
    # turned a silent leak into a loud churn; this removes the churn.
    if _nf_is_marked(host):
        raise ConnectionError(
            f"{host} cached non-FrogNet this session; no worker built")

    with _WORKERS_LOCK:
        key = (host, port, set_id)
        w = _WORKERS.get(key)
        if w is not None and w.is_stopped():
            # [WORKER_EXIT_REASON_V1] This is the Tier 0 wedge. _WORKERS was a
            # pure cache with no liveness check, so once a worker's threads had
            # exited it was handed out forever. Every call() then enqueued onto
            # a queue with no consumer and blocked the full 60s safety cap, and
            # because both loops had already returned there was no [CLEANUP],
            # [DIAG-WRITER] or [RPC-TRACE] line to say why.
            #
            # _retire() now de-registers, so reaching this branch means a stop
            # happened by some other path. Say so, drop the corpse, and build a
            # fresh worker rather than serving from a dead one.
            print(f"[SEM-TRANSPORT] [WORKER_EXIT_REASON_V1] {host}:{port} "
                  f"set={set_id}: cached worker is stopped "
                  f"(reason={w.stopped_reason()!r}, "
                  f"{time.time() - w._stopped_at:.0f}s ago) and was still "
                  f"registered - replacing", flush=True)
            _WORKERS.pop(key, None)
            w = None
        if w is None:
            w = _DaemonWorker(host, port, set_id)
            _WORKERS[key] = w
        return w


def _serialize_http_request(method: str, path: str, headers: Dict[str, str], body: str, host: str, port: int) -> bytes:
    """Serialize HTTP request for REQ_RAW."""
    trace_enter('transport_semantic._serialize_http_request', method=repr(method), path=repr(path), headers=repr(headers), body=repr(body), host=repr(host), port=repr(port))
    return json.dumps({
        "method": method,
        "path": path,
        "headers": headers,
        "body": body,
        "host": host,
        "port": port,
    }, separators=(",", ":")).encode("utf-8")


def _estimate_http_request_wire(method: str, path: str, headers: Dict[str, str], body: bytes) -> int:
    """
    Estimate the actual wire bytes for an HTTP/1.1 request over WireGuard.

    Without FrogNet, every HTTP request opens a NEW TCP connection over WG.
    Cost breakdown:
      TCP handshake (SYN, SYN-ACK, ACK):  3 pkts x (60 + 60 WG) = 360
      HTTP request data:                   request_line + headers + body
      WG encap on data packets:            ceil(data / 1420) x 60
      Receiver ACKs for data:              ceil(data / 1420) x 120
    TCP teardown is counted on the response side.
    """
    trace_enter('transport_semantic._estimate_http_request_wire', method=repr(method), path=repr(path), headers=repr(headers), body=repr(body))
    WG_PER_PKT = 60       # WG header 32 + UDP 8 + outer IP 20
    INNER_MTU = 1420       # typical WG inner MTU
    TCP_HANDSHAKE_WG = 360 # 3 control packets with WG encap
    TCP_ACK_WG = 120       # bare ACK (60) + WG encap (60)

    # HTTP layer bytes
    n = len(method) + 1 + len(path) + len(" HTTP/1.1\r\n")
    for k, v in (headers or {}).items():
        n += len(str(k)) + 2 + len(str(v)) + 2
    n += 2  # \r\n
    n += len(body) if body else 0

    pkts = max(1, (n + INNER_MTU - 1) // INNER_MTU)
    return TCP_HANDSHAKE_WG + n + (pkts * WG_PER_PKT) + (pkts * TCP_ACK_WG)


def _estimate_http_response_wire(body_size: int, content_type: str = "text/plain") -> int:
    """
    Estimate wire bytes for an HTTP/1.1 response over WireGuard.

    Includes response data, WG encap per packet, receiver ACKs, and TCP teardown.
    """
    trace_enter('transport_semantic._estimate_http_response_wire', body_size=repr(body_size), content_type=repr(content_type))
    WG_PER_PKT = 60
    INNER_MTU = 1420
    TCP_TEARDOWN_WG = 480  # 4 control packets (FIN-ACK x 2) with WG encap
    TCP_ACK_WG = 120
    RESP_HEADERS = 194     # status line + typical response headers

    total = RESP_HEADERS + body_size
    pkts = max(1, (total + INNER_MTU - 1) // INNER_MTU)
    return TCP_TEARDOWN_WG + total + (pkts * WG_PER_PKT) + (pkts * TCP_ACK_WG)


class HeaderBlockCorrupt(RuntimeError):
    """A header block was present on the wire and did not decode."""


def _deserialize_headers(data: bytes) -> Dict[str, str]:
    """[NO_FALLBACK_V1] Empty input means no headers, and {} is the right
    answer for that. A non-empty block that will not decode is a framing or
    codec fault and now raises.

    This was a BARE `except:` returning {} - so a truncated or corrupt header
    block was silently indistinguishable from a peer that sent no headers, and
    the reply was assembled and served without Content-Type, caching hints, or
    anything else the origin actually set. A bare except additionally swallowed
    KeyboardInterrupt and SystemExit, so this function could eat a shutdown.

    Raising is correct here: the frame is already known to be damaged, and the
    RPC's caller has an error path that reports which peer and which request.
    """
    trace_enter('transport_semantic._deserialize_headers', data=repr(data))
    if not data:
        return {}
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise HeaderBlockCorrupt(
            f"{len(data)}B header block did not decode: {type(e).__name__}: {e} "
            f"(first 64B: {data[:64]!r})") from e
    if not isinstance(obj, dict):
        raise HeaderBlockCorrupt(
            f"{len(data)}B header block decoded to {type(obj).__name__}, "
            f"expected object")
    return obj


# ====================================================================
# RPC DISPATCH - coalesce or direct based on path
# ====================================================================

def _get_target_sem(target_ip: str) -> threading.BoundedSemaphore:
    """Get or create per-target semaphore."""
    trace_enter('transport_semantic._get_target_sem', target_ip=repr(target_ip))
    with _per_target_sems_lock:
        sem = _per_target_sems.get(target_ip)
        if sem is None:
            sem = threading.BoundedSemaphore(_PER_TARGET_MAX)
            _per_target_sems[target_ip] = sem
        return sem


def _dispatch_rpc(target_ip: str, req_hash: bytes, worker, wire_req: bytes, path: str) -> Tuple[bytes, bool]:
    """
    All RPCs go through coalescing.  Duplicate in-flight requests to the
    same (target_ip, req_hash) wait on the first caller's result.  This
    prevents unbounded pending growth when a downstream node is slow or
    unreachable - probes included, since echo probes are among the most
    frequent messages and their answers rarely change.

    [PER_TARGET_CAP_RESTORED_V1] The per-target semaphore is REQUIRED and was
    wrongly removed.  Every :80 request holds one of the proxy's 256 global
    _ACTIVE slots (proxy_main.py) for the whole RPC - up to the 15s budget.
    Without a per-target cap, one stuck target (e.g. a .2 admin that hangs the
    full budget) piles up in-flight RPCs that consume all 256 slots, so the
    proxy can no longer answer /frognet_echo.php within curl's 5s and the tunnel
    health probe sees code=000 and condemns the tunnel - fleet-wide.  The cap
    bounds any single target to _PER_TARGET_MAX (32) of the 256 slots.  The
    earlier 503-storm this cap was blamed for came from the 1859 bootstrap
    amplification (now fixed) and dead-target slot-hogging (now retired by the
    not_frognet cache below), not from the cap itself.

    [NOT_FROGNET_V1] Hosts proven non-FrogNet this session (refused / no-route /
    no-echo) are skipped BEFORE acquiring a slot, so they never hold one; the
    set is cleared at the next merge so a host that joins later gets one fresh
    probe.
    """
    trace_enter('transport_semantic._dispatch_rpc', target_ip=repr(target_ip), req_hash=repr(req_hash), worker=repr(worker), wire_req=repr(wire_req), path=repr(path))
    if _nf_is_marked(target_ip):
        # fast-fail, no socket, no slot, no budget burned - cleared at next merge
        raise ConnectionError(f"{target_ip} cached non-FrogNet this session")
    sem = _get_target_sem(target_ip)
    if not sem.acquire(blocking=True, timeout=5.0):
        raise RuntimeError(
            f"per-target limit ({_PER_TARGET_MAX}) exceeded for {target_ip}")
    try:
        _r = _coalesced_rpc(target_ip, req_hash, worker, wire_req)
        # [THREE_STRIKES_V1] A completed RPC is the strongest evidence there is
        # that this host is fine. Reset the streak here as well as on connect --
        # a peer that connects but whose RPCs fail, or one that answers this
        # request after two refusals, must not carry strikes forward.
        _nf_clear_strikes(target_ip)
        return _r
    except BaseException as exc:
        # Only refused / no-route / net-unreachable count a strike; timeouts and
        # resets do not. [THREE_STRIKES_V1] and three CONSECUTIVE strikes are
        # needed before the mark lands.
        _nf_mark_if_definitive(target_ip, exc)
        raise
    finally:
        # [NO_FALLBACK_V1] A BoundedSemaphore raises ValueError only on release
        # past its initial value - i.e. this target's slot accounting is already
        # wrong. Swallowing it guarantees _PER_TARGET_MAX drifts undetected,
        # which ends as either a phantom "per-target limit exceeded" on a
        # healthy peer or an unbounded slot count on a sick one. Neither is
        # diagnosable after the fact if the first symptom is never logged.
        try:
            sem.release()
        except ValueError as e:
            print(f"[SEM-TRANSPORT] SLOT ACCOUNTING BROKEN for {target_ip}: "
                  f"release past bound ({e}) - per-target concurrency cap "
                  f"({_PER_TARGET_MAX}) is no longer enforced for this target",
                  flush=True)


# ====================================================================
# MAIN ENTRY POINT
# ====================================================================

def handle_request(
    handler,
    ctx: Dict[str, Any],
    *,
    target_ip: str,
    method: str,
    path: str,
    headers: Dict[str, str],
    body: bytes,
    is_fast_path: bool = True,
) -> None:
    """
    Handle HTTP request via daemon with SAME/DIFF caching.
    """
    trace_enter('transport_semantic.handle_request', handler=repr(handler), ctx=repr(ctx))
    from proxy.proxy_main import send_error_reply

    store = get_template_store()
    codec = SemanticCodec()

    semantic_path = ctx.get("semantic_path", path)
    req_row, resp_row = store.lookup_by_path(method, semantic_path)

    if req_row and resp_row:
        bump_template(hit=True)
        result = _handle_semantic_request(
            handler, ctx, store, codec,
            target_ip=target_ip,
            method=method,
            path=path,
            body=body,
            req_row=req_row,
            resp_row=resp_row,
        )
        if result is not False:
            return
        # Daemon doesn't have template for this opcode - fall through to raw
        _debug(f"TEMPLATE MISMATCH fallback: {method} {semantic_path} to {target_ip}")

    # [BOOTSTRAP_ON_MISSING_TEMPLATE_V1]
    # No template found for this (method, semantic_path).  The original
    # logic fail-closed-503'd the request whenever is_fast_path=False,
    # on the theory "slow link + no template would waste bandwidth."
    # In practice that made template learning impossible for any peer
    # routed via SEMANTIC policy (semantic_hosts override) or via
    # measured-slow RTT: the very first request 503'd, no REQ_RAW ever
    # went out, no template ever got learned, every subsequent request
    # 503'd identically - forever.
    #
    # Correct behavior: ONE RAW bootstrap per (peer, path) costs O(template
    # size), then every subsequent request on that path is compressed.
    # Fail-closed-503 is reserved for the case where bootstrap itself
    # has been tried and failed - never as a first response to a
    # template miss.  bump_template(hit=False) already counts the miss
    # in metrics so the operator can see how often bootstrap fires.
    _debug(f"BOOTSTRAP: no template for {method} {semantic_path}, "
           f"sending REQ_RAW to {target_ip} (is_fast_path={is_fast_path})")
    bump_template(hit=False)
    return _handle_raw_and_learn(
        handler, ctx, store,
        target_ip=target_ip,
        method=method,
        path=path,
        headers=headers,
        body=body,
    )


# ====================================================================
# SEMANTIC PATH (has templates)
# ====================================================================

def _handle_semantic_request(
    handler,
    ctx: Dict[str, Any],
    store,
    codec: SemanticCodec,
    *,
    target_ip: str,
    method: str,
    path: str,
    body: bytes,
    req_row,
    resp_row,
) -> None:
    trace_enter('transport_semantic._handle_semantic_request', handler=repr(handler), ctx=repr(ctx), store=repr(store), codec=repr(codec))
    from proxy.proxy_main import send_error_reply

    req_headers = ctx.get("headers") or {}
    http_req_would = _estimate_http_request_wire(method, path, req_headers, body)

    req_tpl = store.build_request_template(req_row)
    resp_tpl = store.build_reply_template(resp_row)
    opcode = req_tpl.opcode

    body_text = body.decode("utf-8", "replace") if body else ""
    extracted = req_tpl.extract_dynamic(body_text)
    pairs = [(k, v) for k, v in (extracted or [])]

    # [URL_VALS_FIX] Extract URL query values matching the template's
    # url_query_keys, in order. The encoder packs url_vals + json_vals
    # contiguously; if we send url_vals=[] when the template expects
    # url_query_keys=['SensorID'], the daemon decoder reads our body's
    # first field as the SensorID URL parameter - producing URLs like
    # ?SensorID=<jsonData_blob> and triggering "Data too long for
    # column 'actionUrl'" / "Incorrect integer value" downstream.
    url_keys = list(getattr(req_tpl, "url_query_keys", []) or [])
    url_vals = extract_dynamic_query_vals(path, url_keys) if url_keys else []

    next_hop = target_ip
    worker = _get_worker(next_hop, _DAEMON_PORT)
    _MAX_ATTEMPTS = 2
    reference = _get_request_reference(target_ip, opcode)

    for attempt in range(_MAX_ATTEMPTS):
        sem_req, new_reference, is_identical = codec.encode_request_diff(
            opcode=opcode,
            url_vals=url_vals,
            json_vals=pairs,
            type_map=req_tpl.type_map or {},
            reference=reference,
            tokens=TokenStore(req_tpl.tokens),
            compress=True,
        )

        req_hash = _compute_req_hash(target_ip, path, opcode, new_reference)

        # [SAME_IS_BACK_V1] Not on a REPEAT: it carries no fields to inject into,
        # and the daemon serves it from the reference it already holds.
        if not is_identical:
            sem_req = inject_origin_into_semantic_request(
                sem_req,
                ctx.get("client_ip", ""),
                ctx.get("target_host") or target_ip,
            )

        # ---- Three-way request type decision ----
        # [SAME_IS_BACK_V1] is_identical restored as the FIRST branch.
        #
        # The purge left a two-way decision -- REQ_FULL when there is no
        # reference, REQ_DIFF otherwise -- and dropped the case the codec exists
        # to signal. core/codec.py v4.1's own header records why it matters:
        # "BUG 1 (REQ_REPEAT never fires): ... encode_*_diff now correctly
        # returns is_identical=True for repeated zero-field requests (echo,
        # getHosts)". Those endpoints have NO dynamic fields, so their reference
        # never changes and there is never anything to diff -- they went out as
        # REQ_FULL forever, and _handle_req_full has no RESP_SAME branch in this
        # code or in the pre-purge original. Measured on New-York-1: 100% DIFF
        # on /frognet_echo.php, req_type=REQ_FULL on every line.
        #
        # REQ_REPEAT is the only op that can produce RESP_SAME for a request
        # whose fields never move, which is exactly what a static endpoint is.
        if is_identical:
            wire_req = wrap_req_repeat(req_hash)
            req_type = "REQ_REPEAT"
            _debug(f"REQ_REPEAT to {target_ip} hash={req_hash.hex()} (identical)")
        elif reference is None:
            wire_req = wrap_req_full(req_hash, sem_req)
            req_type = "REQ_FULL"
            _debug(f"REQ_FULL to {target_ip} hash={req_hash.hex()} ({len(sem_req)}B sem)")
        else:
            wire_req = wrap_req_diff(req_hash, sem_req)
            req_type = "REQ_DIFF"
            _debug(f"REQ_DIFF to {target_ip} hash={req_hash.hex()} ({len(sem_req)}B diff)")

        t0 = time.time()
        try:
            wire_reply, was_coalesced = _dispatch_rpc(target_ip, req_hash, worker, wire_req, path)
        except Exception as e:
            _debug(f"RPC failed to {target_ip}: {e!r}")
            # [PEER_LINK_QUALITY_V1 2026-05-25] Record the failure as a
            # link-quality sample.  rtt_ms here is the elapsed wait
            # before the exception was raised - useful for the UI to
            # distinguish fast-fail (refused / reset) from slow-fail
            # (timeout / no daemon callback).  bytes_out is whatever
            # we wrote to the socket before the failure; bytes_in is
            # 0 since we got no reply.
            _fail_rtt_ms = (time.time() - t0) * 1000.0
            bump_link_quality(
                peer_ip=target_ip, rtt_ms=_fail_rtt_ms,
                bytes_out=len(wire_req), bytes_in=0, status="fail")
            return send_error_reply(handler, 503, f"Semantic RPC failed: {e!r}", ctx=ctx, where="sem_rpc_exc", extras={"exc": repr(e), "target_ip": target_ip, "attempt": attempt, "req_type": req_type, "wire_req_len": len(wire_req), "path": path})

        rtt_ms = (time.time() - t0) * 1000.0
        bump_sem(peer_ip=target_ip, wire_req=len(wire_req), wire_resp=len(wire_reply))
        bump_link(next_hop=next_hop, wire_out=len(wire_req), wire_in=len(wire_reply))
        # [PEER_LINK_QUALITY_V1] Successful semantic RPC round trip.
        bump_link_quality(
            peer_ip=target_ip, rtt_ms=rtt_ms,
            bytes_out=len(wire_req), bytes_in=len(wire_reply),
            status="ok")

        msg = try_parse(wire_reply)

        # ---- REQ_MISS: clear reference and retry as REQ_FULL ----
        if msg is not None and msg.op == OP_REQ_MISS:
            _clear_request_reference(target_ip, opcode)
            bump_cache(
                peer_ip=target_ip, req_type=req_type, resp_type="REQ_MISS",
                endpoint=path,
                bytes_would=http_req_would,
                bytes_actual=len(wire_req) + len(wire_reply),
                rtt_ms=rtt_ms,
            )
            if attempt < _MAX_ATTEMPTS - 1:
                _debug(f"REQ_MISS from {target_ip}, retrying as REQ_FULL (attempt {attempt+1})")
                reference = None   # force REQ_FULL on next iteration
                continue
            else:
                _debug(f"REQ_MISS from {target_ip}, no retries left")
                return send_error_reply(handler, 503, "REQ_MISS from daemon; exhausted retries", ctx=ctx, where="sem_req_miss_exhausted", extras={"target_ip": target_ip, "attempt": attempt, "max_attempts": _MAX_ATTEMPTS, "opcode": opcode, "rtt_ms": round(rtt_ms, 1)})

        # Not a REQ_MISS - break out of retry loop and process response
        break

    # ---- Commit reference AFTER daemon accepted the request ----
    # This prevents desync: if daemon didn't cache (e.g. 500 from Apache),
    # we don't advance our reference either.
    if msg is not None and msg.op in (OP_RESP_SAME, OP_RESP_DIFF, OP_RESP_RAW):
        _set_request_reference(target_ip, opcode, new_reference)

    # Legacy daemon (no FNW1 header) - should not happen with current nodes
    if msg is None:
        _debug(f"non-FNW1 response from {target_ip}, {len(wire_reply)} bytes - rejecting")
        return send_error_reply(handler, 502, "Non-FNW1 response from daemon", ctx=ctx, where="sem_non_fnw1", extras={"target_ip": target_ip, "wire_reply_len": len(wire_reply), "wire_reply_prefix": wire_reply[:32].hex()})

    # ---- OP_ERROR: daemon processing error ----
    if msg.op == OP_ERROR:
        error_msg = msg.payload.decode("utf-8", "replace") if msg.payload else ""
        error_status = msg.status or 503
        _debug(f"OP_ERROR from {target_ip}: status={error_status} msg={error_msg}")
        # [ENGINE_ERROR_NOT_TEMPLATE_MISS_V1] Only a genuine template-absence
        # (the 503 "no templates for opcode") warrants RAW re-bootstrap+learn.
        # A 502 "engine error" is TRANSIENT (http-exec timeout, resolve/encode
        # failure) - re-bootstrapping it just re-hits the same fault and storms
        # (the permanent re-bootstrap loop execution.py warns about).  Surface
        # it as an error instead; the caller retries on its own heartbeat.
        if "no templates for opcode" in error_msg:
            _debug(f"TEMPLATE MISMATCH: signaling raw fallback for opcode={opcode}")
            return False
        bump_cache(
            peer_ip=target_ip, req_type=req_type, resp_type="ERROR",
            endpoint=path,
            bytes_would=http_req_would,
            bytes_actual=len(wire_req) + len(wire_reply),
            rtt_ms=rtt_ms,
        )
        return send_error_reply(handler, error_status, f"Daemon: {error_msg}", ctx=ctx, where="sem_op_error", extras={"target_ip": target_ip, "error_status": error_status, "error_msg": error_msg, "opcode": opcode, "req_type": req_type})

    resp_type = op_name(msg.op)
    _debug(f"received {resp_type} from {target_ip}")

    # ---- RESP_SAME ----
    # [NO_CACHES_V1] A RESP_SAME names a body the proxy is expected to have kept.
    # Nothing is kept, so nothing can resolve it. A peer still sending one is
    # running pre-[NO_CACHES_V1] code; say so rather than invent a body.
    if msg.op == OP_RESP_SAME:
        cached_body = _serve_from_same(handler, msg.same_id, target_ip, is_raw=False)
        if cached_body is None:
            # [SAME_MISS_AUTORETRY_V1] LRU + DB miss. Drop both
            # references so the next RPC encodes as REQ_FULL and the
            # daemon's response encoder will see a fresh request hash
            # -> won't be able to RESP_SAME-compress against state we
            # no longer have. Then fall through to the raw bootstrap
            # path, which fetches the body via plain HTTP and primes
            # both caches. Net effect from the client's POV: one
            # extra round-trip on the rare cache-miss path, no 503.
            _debug(f"SAME cache miss - auto-retrying via raw path for "
                   f"{target_ip} same_id={msg.same_id.hex()}")
            _clear_request_reference(target_ip, opcode)
            try:
                _response_references.pop((target_ip, opcode), None)
            except Exception:
                pass
            bump_cache(
                peer_ip=target_ip, req_type=req_type, resp_type="RESP_SAME",
                endpoint=path,
                bytes_would=http_req_would,
                bytes_actual=len(wire_req) + len(wire_reply),
                rtt_ms=rtt_ms,
            )
            return _handle_raw_and_learn(
                handler, ctx, store,
                target_ip=target_ip, method=method, path=path,
                headers=req_headers, body=body,
            )
        cached_resp_size = len(cached_body)
        http_resp_would = _estimate_http_response_wire(cached_resp_size)
        bump_cache(
            peer_ip=target_ip, req_type=req_type, resp_type="RESP_SAME",
            endpoint=path,
            bytes_would=http_req_would + http_resp_would,
            bytes_actual=len(wire_req) + len(wire_reply),
            rtt_ms=rtt_ms,
        )
        return

    # ---- RESP_DIFF ----
    if msg.op == OP_RESP_DIFF:
        reconstructed_size = _handle_resp_diff(handler, codec, resp_tpl, msg.same_id, msg.payload, req_hash, target_ip, opcode)
        http_resp_would = _estimate_http_response_wire(reconstructed_size)
        bump_cache(
            peer_ip=target_ip, req_type=req_type, resp_type="RESP_DIFF",
            endpoint=path,
            bytes_would=http_req_would + http_resp_would,
            bytes_actual=len(wire_req) + len(wire_reply),
            rtt_ms=rtt_ms,
        )
        return

    # ---- RESP_RAW ----
    if msg.op == OP_RESP_RAW:
        resp_body_size = len(msg.body) if msg.body else 0
        http_resp_would = _estimate_http_response_wire(resp_body_size)
        bump_cache(
            peer_ip=target_ip, req_type=req_type, resp_type="RESP_RAW",
            endpoint=path,
            bytes_would=http_req_would + http_resp_would,
            bytes_actual=len(wire_req) + len(wire_reply),
            rtt_ms=rtt_ms,
        )
        return _handle_resp_raw_and_learn(
            handler, ctx, store, msg.same_id, msg.status, msg.headers, msg.body,
            req_hash, target_ip, method, path, headers={}, req_body=b"",
        )

    return send_error_reply(handler, 503, f"Unknown wire op: {resp_type}", ctx=ctx, where="sem_unknown_op", extras={"target_ip": target_ip, "resp_type": resp_type, "msg_op": getattr(msg, "op", None), "wire_reply_len": len(wire_reply)})


# ====================================================================
# RAW BOOTSTRAP PATH (FAST link, no template)
# ====================================================================

def _handle_raw_and_learn(
    handler,
    ctx: Dict[str, Any],
    store,
    *,
    target_ip: str,
    method: str,
    path: str,
    headers: Dict[str, str],
    body: bytes,
) -> None:
    trace_enter('transport_semantic._handle_raw_and_learn', handler=repr(handler), ctx=repr(ctx), store=repr(store))
    from proxy.proxy_main import send_error_reply

    host = ctx.get("target_host") or target_ip
    body_text = body.decode("utf-8", "replace") if body else ""
    http_req = _serialize_http_request(method, path, headers, body_text, host, 80)
    http_req_would = _estimate_http_request_wire(method, path, headers, body)
    req_hash = hashlib.sha256(http_req).digest()[:REQ_HASH_LEN]
    next_hop = target_ip
    worker = _get_worker(next_hop, _DAEMON_PORT)

    # [SAME_IS_BACK_V1] REQ_REPEAT restored. It asks the daemon to re-execute a
    # request it has SEEN BEFORE, identified by hash alone -- the wire carries 16
    # bytes instead of the whole HTTP request. The daemon executes it fresh and
    # answers RESP_SAME when the result is unchanged. Nothing is replayed from a
    # store on either side; the store holds hashes and a bounded body cache.
    #
    # REQ_REPEAT is what makes RESP_SAME reachable at all. Removing it and then
    # asking why SAME never appeared was the shape of this whole detour.
    _MAX_ATTEMPTS = 1

    use_repeat = semcache_db.is_req_seen(target_ip, req_hash)

    for attempt in range(_MAX_ATTEMPTS):
        if use_repeat:
            wire_req = wrap_req_repeat(req_hash)
            req_type = "REQ_REPEAT"
            _debug(f"BOOTSTRAP REQ_REPEAT to {target_ip} (raw, seen before)")
        else:
            wire_req = wrap_req_raw(req_hash, http_req)
            req_type = "REQ_RAW"
        _debug(f"BOOTSTRAP REQ_RAW to {target_ip} ({len(http_req)}B)")

        t0 = time.time()
        try:
            wire_reply, was_coalesced = _dispatch_rpc(target_ip, req_hash, worker, wire_req, path)
        except Exception as e:
            _debug(f"BOOTSTRAP: RPC failed to {target_ip}: {e!r}")
            # [PEER_LINK_QUALITY_V1 2026-05-25] Same shape as the
            # semantic path: record the failed RPC with elapsed-
            # before-failure and bytes_out written.
            _fail_rtt_ms = (time.time() - t0) * 1000.0
            bump_link_quality(
                peer_ip=target_ip, rtt_ms=_fail_rtt_ms,
                bytes_out=len(wire_req), bytes_in=0, status="fail")
            return send_error_reply(handler, 503, f"RAW RPC failed: {e!r}", ctx=ctx, where="bootstrap_rpc_exc", extras={"exc": repr(e), "target_ip": target_ip, "attempt": attempt, "req_type": req_type, "wire_req_len": len(wire_req), "path": path})

        rtt_ms = (time.time() - t0) * 1000.0
        bump_sem(peer_ip=target_ip, wire_req=len(wire_req), wire_resp=len(wire_reply))
        bump_link(next_hop=next_hop, wire_out=len(wire_req), wire_in=len(wire_reply))
        # [PEER_LINK_QUALITY_V1] Successful bootstrap RPC round trip.
        bump_link_quality(
            peer_ip=target_ip, rtt_ms=rtt_ms,
            bytes_out=len(wire_req), bytes_in=len(wire_reply),
            status="ok")

        msg = try_parse(wire_reply)
        if msg is None:
            _debug(f"BOOTSTRAP: non-FNW1 response from {target_ip}")
            return send_error_reply(handler, 502, "Invalid response from daemon (not FNW1)", ctx=ctx, where="bootstrap_non_fnw1", extras={"target_ip": target_ip, "wire_reply_len": len(wire_reply), "wire_reply_prefix": wire_reply[:32].hex()})

        # ---- REQ_MISS ----
        # [NO_CACHES_V1] Nothing to clear: we always send REQ_RAW here, so a
        # REQ_MISS in reply means the daemon rejected the frame itself.
        if msg.op == OP_REQ_MISS:
            bump_cache(
                peer_ip=target_ip, req_type=req_type, resp_type="REQ_MISS",
                endpoint=path,
                bytes_would=http_req_would,
                bytes_actual=len(wire_req) + len(wire_reply),
                rtt_ms=rtt_ms,
            )
            if attempt < _MAX_ATTEMPTS - 1:
                _debug(f"BOOTSTRAP: REQ_MISS from {target_ip}, retrying as REQ_RAW (attempt {attempt+1})")
                continue
            else:
                _debug(f"BOOTSTRAP: REQ_MISS from {target_ip}, no retries left")
                return send_error_reply(handler, 503, "REQ_MISS from daemon; exhausted retries", ctx=ctx, where="bootstrap_req_miss_exhausted", extras={"target_ip": target_ip, "attempt": attempt, "max_attempts": _MAX_ATTEMPTS})

        # Not a REQ_MISS - break out and process response
        break

    resp_type = op_name(msg.op)
    _debug(f"BOOTSTRAP: received {resp_type} from {target_ip}")

    # ---- OP_ERROR ----
    if msg.op == OP_ERROR:
        error_msg = msg.payload.decode("utf-8", "replace") if msg.payload else ""
        error_status = msg.status or 503
        _debug(f"BOOTSTRAP: OP_ERROR from {target_ip}: status={error_status} msg={error_msg}")
        bump_cache(
            peer_ip=target_ip, req_type=req_type, resp_type="ERROR",
            endpoint=path,
            bytes_would=http_req_would,
            bytes_actual=len(wire_req) + len(wire_reply),
            rtt_ms=rtt_ms,
        )
        return send_error_reply(handler, error_status, f"Daemon: {error_msg}", ctx=ctx, where="bootstrap_op_error", extras={"target_ip": target_ip, "error_status": error_status, "error_msg": error_msg, "req_type": req_type})

    if msg.op == OP_RESP_SAME:
        cached_body = _serve_from_same(handler, msg.same_id, target_ip, is_raw=True)
        if cached_body is None:
            # [SAME_MISS_AUTORETRY_V1] LRU + DB miss on the RAW bootstrap
            # path. This branch sits outside the REQ_REPEAT/REQ_RAW
            # retry loop above, so we can't cleanly `continue` here.
            # The semantic path falls back to this function on its own
            # cache miss - if THIS function ALSO cache-misses, both
            # caches are inconsistent with the daemon and a third
            # auto-retry from here would loop forever in the worst
            # case. Clear req_seen so a fresh user request starts
            # clean, then send the 503 that _serve_from_same no
            # longer writes for us.
            _debug(f"RAW SAME cache miss (bootstrap path, no inline "
                   f"retry available) for {target_ip} "
                   f"same_id={msg.same_id.hex()}")
            semcache_db.clear_req_seen(target_ip, req_hash)
            bump_cache(
                peer_ip=target_ip, req_type=req_type, resp_type="RESP_SAME",
                endpoint=path,
                bytes_would=http_req_would,
                bytes_actual=len(wire_req) + len(wire_reply),
                rtt_ms=rtt_ms,
            )
            return send_error_reply(
                handler, 503, "SAME cache miss (proxy bootstrap)",
                ctx=ctx, where="bootstrap_same_miss",
                extras={"target_ip": target_ip,
                        "same_id": msg.same_id.hex()})
        cached_resp_size = len(cached_body)
        http_resp_would = _estimate_http_response_wire(cached_resp_size)
        bump_cache(
            peer_ip=target_ip, req_type=req_type, resp_type="RESP_SAME",
            endpoint=path,
            bytes_would=http_req_would + http_resp_would,
            bytes_actual=len(wire_req) + len(wire_reply),
            rtt_ms=rtt_ms,
        )
        return

    if msg.op == OP_RESP_RAW:
        resp_body_size = len(msg.body) if msg.body else 0
        http_resp_would = _estimate_http_response_wire(resp_body_size)
        bump_cache(
            peer_ip=target_ip, req_type=req_type, resp_type="RESP_RAW",
            endpoint=path,
            bytes_would=http_req_would + http_resp_would,
            bytes_actual=len(wire_req) + len(wire_reply),
            rtt_ms=rtt_ms,
        )
        return _handle_resp_raw_and_learn(
            handler, ctx, store, msg.same_id, msg.status, msg.headers, msg.body,
            req_hash, target_ip, method, path, headers, body,
        )

    return send_error_reply(handler, 503, f"Unexpected op in bootstrap: {resp_type}", ctx=ctx, where="bootstrap_unexpected_op", extras={"target_ip": target_ip, "resp_type": resp_type, "msg_op": getattr(msg, "op", None)})


# ====================================================================
# RESPONSE HANDLERS
# ====================================================================

def _is_empty_collection_payload(body, ctype):
    """Paths of every EMPTY array in a JSON response body, at any depth.

    [NO_SAME_FROM_AN_EMPTY_ARRAY_V1] Refusing to LEARN A TEMPLATE from an empty
    array is not enough on its own -- proxy/templates._empty_arrays_in covers
    that path. With no template stored, every request takes the RAW path
    instead, so an empty body cached HERE under same_id becomes the answer every
    later RESP_SAME resolves to and the empty answer is replayed for the life of
    the process.

    `status >= 400 or not body` does NOT catch it: {"ok":true,"rows":[]} is a
    200 with a non-empty body. That is exactly the shape the sensors/values bug
    produced on Seattle3 (2026-08-02) -- a full database clear, then the first
    read went out while there were no matching tuples.

    Returns [] for anything that is not JSON, and for a JSON body with no empty
    array anywhere -- an empty OBJECT is not an empty array, and a scalar or
    null is not a collection. Path format matches templates._empty_arrays_in
    ("rows", "rows[0].tags", "<root>") so the two read alike in a log.

    Self-contained on purpose: discovery/test_empty_array_template_oracle.py
    execs this function out of the source with only `json` in scope, so it must
    not call a helper defined elsewhere in this module.
    """
    if not body:
        return []
    if "json" not in (ctype or "").lower():
        return []
    try:
        parsed = json.loads(body.decode("utf-8", "replace")
                            if isinstance(body, (bytes, bytearray)) else body)
    except Exception:
        # Undecodable is not empty. Say nothing rather than refuse a body we
        # could not read -- the caller has its own status/length guard.
        return []

    def _walk(obj, path=""):
        out = []
        if isinstance(obj, list):
            if not obj:
                out.append(path or "<root>")
            else:
                for i, v in enumerate(obj):
                    out.extend(_walk(v, "%s[%d]" % (path, i)))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                out.extend(_walk(v, ("%s.%s" % (path, k)) if path else str(k)))
        return out

    return _walk(parsed)


def _handle_resp_raw_and_learn(
    handler,
    ctx: Dict[str, Any],
    store,
    same_id: bytes,
    status: int,
    headers_bytes: bytes,
    body: bytes,
    req_hash: bytes,
    target_ip: str,
    method: str,
    path: str,
    headers: Dict[str, str],
    req_body: bytes,
) -> None:
    trace_enter('transport_semantic._handle_resp_raw_and_learn', handler=repr(handler), ctx=repr(ctx), store=repr(store), same_id=repr(same_id), status=repr(status), headers_bytes=repr(headers_bytes))
    from proxy.proxy_main import send_error_reply

    if not same_id or len(same_id) != 16:
        return send_error_reply(handler, 503, "Invalid same_id in RESP_RAW")

    try:
        resp_headers = _deserialize_headers(headers_bytes)
    except HeaderBlockCorrupt as e:
        # The frame is damaged. Previously this returned {} and the body was
        # served anyway as application/octet-stream, so a codec/framing fault
        # presented as a successful reply with the wrong content type.
        return send_error_reply(
            handler, 502, f"RESP_RAW header block corrupt from {target_ip}: {e}",
            ctx=ctx, where="resp_raw_headers_corrupt",
            extras={"target_ip": target_ip, "status": status,
                    "headers_len": len(headers_bytes or b""),
                    "body_len": len(body or b""), "path": path})

    ctype = resp_headers.get("Content-Type", "application/octet-stream")

    # [SAME_IS_BACK_V1] Store the body and mark the request seen.
    #
    # This is the OTHER writer. mark_req_seen is what makes is_req_seen true, so
    # without it the bootstrap path can never send a REQ_REPEAT afterwards, and
    # upsert_raw is what lets a later RESP_SAME resolve to a body instead of
    # falling through to another bootstrap.
    #
    # [NO_SAME_FROM_AN_EMPTY_ARRAY_V1] restored as a GUARD, which the pre-purge
    # original did NOT have -- it cached unconditionally. An error or an empty
    # body stored here becomes the permanent answer every later RESP_SAME
    # resolves to, and an empty array is exactly what the sensors/values bug
    # produced in August. Same rule as the daemon's writers.
    # [SAME_CACHE_IS_MEMORY_ONLY_V1] LRU only. No MySQL write on this path.
    #
    # The disk copy was the fallback tier UNDER the LRU: _serve_from_same reads
    # _same_lru_get first and only touches MySQL on a miss. So the write bought
    # exactly two things -- surviving a proxy restart, and a working set larger
    # than _SAME_LRU_MAX -- at the cost of a synchronous MySQL round-trip on the
    # response path of EVERY reply, on the request thread.
    #
    # That is a disk write in a time-critical thread. With echo, all concurrent
    # requests share one req_hash and coalesce onto a single in-flight RPC, so
    # the whole burst moves at the speed of that one reply -- and I had just put
    # a database round-trip inside it.
    #
    # A restart is a restart. The LRU re-warms on the first request per same_id
    # and the cost of a cold cache is one bootstrap, which is what a bootstrap
    # is for.
    #
    # mark_req_seen STAYS: it is an in-memory dict under a brief lock, no I/O,
    # and is_req_seen is consulted on the very next request to decide REQ_REPEAT.
    _empty_at = _is_empty_collection_payload(body, ctype)
    if status >= 400 or not body:
        # [NO_SAME_FROM_AN_EMPTY_ARRAY_V1] an error or empty body must not
        # become the answer every later RESP_SAME resolves to.
        _debug(f"BOOTSTRAP: status={status} len={len(body or b'')} - NOT cached")
    elif _empty_at:
        # [NO_SAME_FROM_AN_EMPTY_ARRAY_V1] a 200 whose collections are empty is
        # the poison this rule exists for, and the status/length guard above
        # cannot see it. mark_req_seen is skipped too, deliberately: marking the
        # request seen is what turns the NEXT one into REQ_REPEAT, which is what
        # gets served RESP_SAME from a body we just declined to keep.
        _debug(f"BOOTSTRAP: empty collection at {_empty_at} - NOT cached")
    else:
        semcache_db.mark_req_seen(target_ip, req_hash)
        _same_lru_put(same_id, (body, ctype, True, status, headers_bytes))
        _debug(f"BOOTSTRAP: cached {len(body)}B for same_id={same_id.hex()}")

    if status < 400 and body:
        semantic_path = ctx.get("semantic_path", path)
        try:
            upstream = {
                "status": status,
                "body": body,
                "headers": resp_headers,
            }
            learn_templates_from_real(
                method=method,
                semantic_path=semantic_path,
                req_headers=headers,
                req_body=req_body,
                upstream=upstream,
                store=store,
                raw_path=path,
            )
            _debug(f"BOOTSTRAP: LEARNED template for {method} {semantic_path}")
        except Exception as e:
            _debug(f"BOOTSTRAP: template learning failed (non-fatal): {e!r}")

    with _client_write(handler, "BOOTSTRAP"):
        handler.send_response(status)
        for k, v in resp_headers.items():
            if k and k.lower() not in {"transfer-encoding", "connection", "content-length"}:
                handler.send_header(k, v)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("X-FrogNet-Cache", "BOOTSTRAP")
        handler.send_header("Connection", "close")
        handler.end_headers()
        if body:
            handler.wfile.write(body)


def _serve_from_same(handler, same_id: bytes, target_ip: str, is_raw: bool) -> bytes:
    """
    BUG 5: Serve RESP_SAME from in-memory LRU first, MySQL fallback.
    Returns the response body bytes (for telemetry sizing).
    """
    trace_enter('transport_semantic._serve_from_same', handler=repr(handler), same_id=repr(same_id), target_ip=repr(target_ip), is_raw=repr(is_raw))
    from proxy.proxy_main import send_error_reply

    if not same_id or len(same_id) != 16:
        send_error_reply(handler, 503, "Invalid same_id in RESP_SAME")
        return b""

    # Try LRU first
    lru_hit = _same_lru_get(same_id)

    if lru_hit is not None:
        body, ctype, cached_is_raw, status, hdrs_bytes = lru_hit
        _debug(f"SAME LRU hit, {len(body)}B same_id={same_id.hex()}")
    else:
        # [SAME_CACHE_IS_MEMORY_ONLY_V1] These MySQL reads are now always a
        # miss -- nothing writes those tables from this process any more. Kept
        # for a pond still running a peer that does write them; when the last
        # one is gone this whole branch can go with it.
        if is_raw:
            hit = semcache_db.get_raw_by_sameid(same_id)
            if not hit:
                # [SAME_MISS_AUTORETRY_V1] No 503 here - let the caller
                # in _handle_semantic_request retry the RPC with cleared
                # references. send_error_reply on cache miss aborted the
                # client connection before the retry could fire, which
                # is what forced John to curl twice. Return None so the
                # caller can distinguish miss from empty body.
                _debug(f"RAW SAME cache miss for same_id={same_id.hex()}")
                return None
            body, status, hdrs_bytes = hit
            ctype = "application/octet-stream"
            cached_is_raw = True
        else:
            hit = semcache_db.get_by_sameid(same_id)
            if not hit:
                # [SAME_MISS_AUTORETRY_V1] No 503 here - same reason as
                # the raw-side miss above. Return None so the caller
                # retries the RPC instead of aborting the client.
                _debug(f"SAME cache miss for same_id={same_id.hex()}")
                return None
            body, ctype = hit
            status = 200
            hdrs_bytes = b""
            cached_is_raw = False

        # Populate LRU for next time
        _same_lru_put(same_id, (body, ctype, cached_is_raw, status, hdrs_bytes))
        _debug(f"SAME MySQL hit -> LRU, {len(body)}B same_id={same_id.hex()}")

    # Serve response
    with _client_write(handler, f"SAME same_id={same_id.hex()}"):
        if cached_is_raw:
            resp_headers = _deserialize_headers(hdrs_bytes) if hdrs_bytes else {}
            handler.send_response(status)
            for k, v in resp_headers.items():
                handler.send_header(k, v)
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("X-FrogNet-Cache", "SAME-RAW")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(body)
        else:
            handler.send_response(200)
            handler.send_header("Content-Type", ctype)
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("X-FrogNet-Cache", "SAME")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(body)

    return body


def _handle_resp_diff(handler, codec, resp_tpl, same_id: bytes, sem_blob: bytes, req_hash: bytes, target_ip: str, opcode: int) -> int:
    """Handle RESP_DIFF. Returns reconstructed body size (0 on error)."""
    trace_enter('transport_semantic._handle_resp_diff', handler=repr(handler), codec=repr(codec), resp_tpl=repr(resp_tpl), same_id=repr(same_id), sem_blob=repr(sem_blob), req_hash=repr(req_hash))
    from proxy.proxy_main import send_error_reply

    if not same_id or len(same_id) != 16:
        send_error_reply(handler, 503, "Invalid same_id in RESP_DIFF")
        return 0
    if not sem_blob:
        send_error_reply(handler, 503, "Empty sem_blob in RESP_DIFF")
        return 0

    try:
        reference = _get_response_reference(target_ip, opcode)
        fields = codec.decode_reply(sem_blob, resp_tpl, TokenStore(resp_tpl.tokens), reference)
        new_ref = {k: v for k, v in fields}
        _set_response_reference(target_ip, opcode, new_ref)

        body_out = resp_tpl.rebuild([v for _, v in fields])
        data = body_out.encode("utf-8", "replace")
        ctype = resp_tpl.content_type()

        # [SAME_IS_BACK_V1] STORE the reconstructed body under this same_id.
        #
        # This is the writer that makes RESP_SAME resolvable. Without it the LRU
        # is never populated, every same_id misses, [SAME_MISS_AUTORETRY_V1]
        # falls through to the raw path, and every SAME the daemon sends becomes
        # a full bootstrap. Measured: Seattle6 received 159 RESP_SAME and logged
        # 861 BOOTSTRAP -- the mechanism working end to end and being thrown away
        # on arrival because nothing here had kept a body to resolve it with.
        #
        # I ported _serve_from_same (the READER) in the first pass and neither of
        # the two WRITERS. A cache with a reader and no writer is not a cache.
        # [SAME_CACHE_IS_MEMORY_ONLY_V1] LRU only; see the raw path.
        _same_lru_put(same_id, (data, ctype, False, 200, b""))
        _debug(f"DIFF: {len(data)}B reconstructed and cached")

        with _client_write(handler, "DIFF"):
            handler.send_response(200)
            handler.send_header("Content-Type", ctype)
            handler.send_header("Content-Length", str(len(data)))
            handler.send_header("X-FrogNet-Cache", "DIFF")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(data)

        return len(data)

    except BrokenPipeError:
        _debug("BrokenPipe in DIFF path (client closed)")
        return 0
    except Exception as e:
        send_error_reply(handler, 502, f"DIFF decode failed: {e!r}")
        return 0


# -----------------------------------------------------------------
# [STUB] proxy_metrics imports these for periodic emission.  The
# real implementations were removed (or never written); the imports
# in proxy_metrics._emit_all_sensors raise ImportError every flush
# cycle, spamming the log.  Empty stubs make emission a silent no-op
# until real telemetry is wired up.
# -----------------------------------------------------------------

def pipeline_stats() -> List[Dict[str, Any]]:
    """Per-peer pipeline/concurrency stats.  Stub: no data exposed yet."""
    trace_enter('transport_semantic.pipeline_stats')
    return []


def routing_resilience_snapshot() -> Dict[str, Any]:
    """Routing fallback/merge/failure counters.  Stub: returns {} so
    proxy_metrics treats it as 'nothing to emit' (its truthy check)."""
    trace_enter('transport_semantic.routing_resilience_snapshot')
    return {}
