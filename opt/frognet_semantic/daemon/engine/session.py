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
daemon/engine/session.py

Semantic session: two-socket architecture.

  _recv_sock: the connection the proxy opened TO daemon:9009.
              Reader thread reads inbound request frames from this socket only.
  _send_sock: the connection the daemon opened BACK to proxy:9010.
              Writer thread drains the reply queue onto this socket only.

Worker pool (256 threads) executes requests and posts (seq, reply) tuples
onto the reply queue.  Writer thread drains that queue in arrival order.
No shared send lock, no _flush_responses polling loop.

CRITICAL: REQ_REPEAT ALWAYS re-executes against the backend.
The "REPEAT" means the REQUEST is the same - not that we skip execution.
We compare the NEW response to the CACHED response to decide SAME vs DIFF.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import socket
import struct
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple, Optional, Dict, Any, List

from daemon.daemon_metrics import bump_daemon_cache, bump_semantic, bump_daemon_coalesce
from daemon.daemon_metrics import bump_link_quality

from daemon.engine.packet import (
    SEM_HDR_V1_LEN,
    extract_origin_dest_and_normalize_packet,
)
from daemon.engine.execution import ExecutionEngine, has_request_ref, get_request_ref
from daemon.util.log import debug, trace
# [SAME_IS_BACK_V1] the response cache is consulted again.
from daemon.cache import semcache_db
from daemon.upstream.client import HttpClient

from core.codec import SemanticCodec, REQ_HASH_LEN
from core.tokens import TokenStore
from core.semcache_wire import (
    try_parse,
    wrap_resp_same, wrap_resp_diff, wrap_resp_raw, wrap_req_miss, wrap_error,
    OP_REQ_FULL, OP_REQ_REPEAT, OP_REQ_RAW, OP_REQ_DIFF, OP_ERROR,
    OP_HELLO,                                                # ---- ADDED ----
    op_name
)
from core.semcache_id import same_id as compute_same_id
from core.semcache_wire import wrap_hello

import subprocess
import threading

_LOCAL_GW_LOCK = threading.Lock()
_LOCAL_GW_CACHED = ""   # empty until first successful resolution
_LOCAL_GW_LAST_REPORT = 0.0
_LOCAL_GW_REPORT_EVERY_SEC = 30.0

def _get_local_gw() -> str:
    """Resolve this daemon's local FrogNet IP for HELLO frames.

    Cached lazily - populated on first call that finds a real address
    (i.e. anything other than "0.0.0.0" or empty), then reused.  Until
    that point each call re-polls so the daemon doesn't bake
    "0.0.0.0" into HELLOs when started before the served interface
    is up.  Honors $FROGNET_LOCAL_GW as an operator override.
    """
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
        # [NO_FALLBACK_V1] See the matching block in
        # proxy/transport_semantic.py. This value is the HELLO body the daemon
        # sends back on the RETURN channel (set_send_sock), so 0.0.0.0 here
        # means the proxy is told the return path belongs to nobody. The
        # sentinel stays for the genuine startup race - this function re-polls
        # until a real address appears - but each of the three ways to reach it
        # now names itself, rate-limited.
        global _LOCAL_GW_LAST_REPORT
        try:
            r = subprocess.run(
                ["getEth0Address"], capture_output=True, text=True,
                check=False, timeout=5.0)
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
            print(f"[DAEMON-SESSION] LOCAL GW UNKNOWN: {reason} - emitting "
                  f"0.0.0.0 in the RETURN HELLO; the proxy has no return "
                  f"address for us until this resolves", flush=True)
        return "0.0.0.0"

_MAX_FRAME = int(os.environ.get("FROGNET_SEM_MAX_FRAME", "4194304"))
_DEBUG = os.environ.get("FROGNET_DEBUG", "0").strip() == "1"
_POOL_SIZE = int(os.environ.get("FROGNET_DAEMON_POOL_SIZE", "256"))

# Database-bound executor: sized to what MySQL can sustain on this hardware.
# Default = 4 x CPU cores. On a 4-core SD card box, 16 concurrent DB queries
# is the sweet spot -- more just thrashes the disk and starves echo.
def _default_db_pool():
    """[NO_FALLBACK_V1] Sized to the hardware. If the hardware cannot be
    measured, say so - do not invent 16.

    `except Exception: return 16` meant a node that could not read its own CPU
    count ran a DB executor sized for a 4-core box regardless of what it
    actually was, and every capacity question asked about that node afterwards
    got an answer derived from a number nobody measured. On an under-sized box
    that is disk thrash; on an over-sized one it is idle capacity. Either way
    the log said nothing.

    FROGNET_DAEMON_DB_POOL_SIZE remains the operator override and is the
    documented answer when the count genuinely cannot be taken.
    """
    try:
        import multiprocessing
        n = multiprocessing.cpu_count()
    except (ImportError, NotImplementedError, OSError) as e:
        raise RuntimeError(
            f"cannot determine CPU count ({type(e).__name__}: {e}); the DB "
            f"executor cannot be sized. Set FROGNET_DAEMON_DB_POOL_SIZE "
            f"explicitly.") from e
    return n * 4

_DB_POOL_SIZE = int(os.environ.get("FROGNET_DAEMON_DB_POOL_SIZE", str(_default_db_pool())))
_COALESCE_WAIT_SEC = float(os.environ.get("FROGNET_DAEMON_COALESCE_WAIT", "0.0"))
# [RETURN_ONLY_V1] _PROXY_CALLBACK_PORT removed - daemon no longer dials proxy.
_RPC_TIMEOUT_SEC = float(os.environ.get("FROGNET_SEM_RPC_TIMEOUT", "45"))

# Health check magic hash - size matches REQ_HASH_LEN
HEALTH_CHECK_HASH = b'\xff' * REQ_HASH_LEN
HEALTH_CHECK_SAME_ID = b'\xff' * 16


# ====================================================================
# RESPONSE REFERENCE CACHE (for diff encoding outgoing responses)
# ====================================================================
# None = never sent, {} = sent with zero fields.
_response_references: Dict[Tuple[str, int], Optional[Dict[str, Any]]] = {}
_ref_lock = threading.RLock()


def _get_response_reference(peer_ip: str, opcode: int) -> Optional[Dict[str, Any]]:
    with _ref_lock:
        ref = _response_references.get((peer_ip, opcode))
        return ref.copy() if ref is not None else None


def _set_response_reference(peer_ip: str, opcode: int, ref: Dict[str, Any]) -> None:
    with _ref_lock:
        _response_references[(peer_ip, opcode)] = ref.copy()


def _clear_response_reference(peer_ip: str, opcode: int) -> None:
    """Clear response ref on REQ_FULL so diff-encode sends all fields."""
    with _ref_lock:
        _response_references.pop((peer_ip, opcode), None)


# ====================================================================
# REQUEST COALESCING (cross-session)
# ====================================================================
# One upstream execution per req_hash.  If two proxies send the same
# req_hash while the first is still executing against Apache, the
# second waits and gets the same reply bytes.

class _DaemonInflight:
    __slots__ = ("event", "reply", "error")
    def __init__(self):
        self.event = threading.Event()
        self.reply: Optional[bytes] = None
        self.error: Optional[Exception] = None

_daemon_inflight: Dict[bytes, _DaemonInflight] = {}   # keyed on req_hash
_daemon_inflight_lock = threading.RLock()


def _debug(msg: str) -> None:
    if _DEBUG:
        print(f"[DAEMON-SESSION] {msg}", flush=True)


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        blk = conn.recv(n - len(buf))
        if not blk:
            raise RuntimeError("socket closed")
        buf.extend(blk)
    return bytes(buf)


def _recv_frame(conn: socket.socket) -> bytes:
    hdr = _recv_exact(conn, 4)
    (n,) = struct.unpack("!I", hdr)
    if n <= 0 or n > _MAX_FRAME:
        raise RuntimeError(f"bad frame length {n}")
    return _recv_exact(conn, n)


def _send_frame(conn: socket.socket, payload: bytes) -> None:
    n = len(payload)
    if n <= 0 or n > _MAX_FRAME:
        raise RuntimeError(f"invalid frame size {n}")
    conn.sendall(struct.pack("!I", n))
    conn.sendall(payload)


# Shared executor - one pool for all sessions
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.RLock()

_http_client: Optional[HttpClient] = None
_http_client_lock = threading.RLock()

_codec: Optional[SemanticCodec] = None


def _get_codec() -> SemanticCodec:
    global _codec
    if _codec is None:
        _codec = SemanticCodec()
    return _codec


def _get_executor() -> ThreadPoolExecutor:
    """Fast executor for echo/discovery -- essentially unlimited."""
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=_POOL_SIZE)
                print(f"[DAEMON-SESSION] FAST executor: {_POOL_SIZE} threads "
                      f"(echo, discovery, local)", flush=True)
    return _executor


# -- Database executor: limited to what MySQL can handle --
_db_executor: Optional[ThreadPoolExecutor] = None
_db_executor_lock = threading.RLock()

# Opcodes that NEVER hit the database -- instant local responses
_FAST_OPCODES = frozenset({
    61080,   # frognet_echo.php
    # Add getHosts, discovery opcodes as identified
})

# Hash->opcode cache for REQ_REPEAT routing
_hash_opcode: Dict[bytes, int] = {}
_hash_opcode_lock = threading.RLock()


def _get_db_executor() -> ThreadPoolExecutor:
    """Database-bound executor -- sized to MySQL capacity, not thread count."""
    global _db_executor
    if _db_executor is None:
        with _db_executor_lock:
            if _db_executor is None:
                _db_executor = ThreadPoolExecutor(max_workers=_DB_POOL_SIZE)
                print(f"[DAEMON-SESSION] DB executor: {_DB_POOL_SIZE} threads "
                      f"(sensor data, api.php)", flush=True)
    return _db_executor


def _remember_opcode(req_hash: bytes, opcode: int) -> None:
    """Cache hash->opcode so future REQ_REPEAT routes correctly."""
    if not req_hash or not opcode:
        return
    with _hash_opcode_lock:
        _hash_opcode[req_hash] = opcode
        if len(_hash_opcode) > 8192:
            for k in list(_hash_opcode.keys())[:2048]:
                _hash_opcode.pop(k, None)


class FrameMalformed(RuntimeError):
    """A frame parsed as FNWP but its fields do not hold together."""


def _is_fast_frame(frame: bytes) -> bool:
    """Peek at a frame to decide: fast executor or db executor.
    Returns True for echo/discovery (no database).
    Returns False for sensor data (hits MySQL).
    Unknown -> False (safe default on databasehost)."""
    msg = try_parse(frame)
    if msg is None:
        return False

    # REQ_FULL / REQ_DIFF: opcode in payload
    if msg.payload and len(msg.payload) >= 4:
        # [NO_FALLBACK_V1] `except (struct.error, IndexError): pass` fell
        # through to the REQ_REPEAT branch below and then to "unknown -> db
        # executor". A malformed FNWP payload was therefore reclassified as an
        # ordinary slow-path request and executed, instead of being reported as
        # the framing fault it is. The length guard is the real check and it is
        # wrong: the unpack reads payload[2:6], which needs 6 bytes, not 4.
        # That off-by-two is what made the handler necessary in the first place.
        if len(msg.payload) >= 6:
            opcode = struct.unpack("<I", msg.payload[2:6])[0]
            if msg.req_hash:
                _remember_opcode(msg.req_hash, opcode)
            return opcode in _FAST_OPCODES
        raise FrameMalformed(
            f"payload is {len(msg.payload)}B, need >=6 to read the opcode at "
            f"[2:6] (op={getattr(msg, 'op', '?')} "
            f"req_hash={(msg.req_hash or b'').hex() or 'none'})")

    # REQ_REPEAT: check cache
    if msg.req_hash:
        with _hash_opcode_lock:
            opcode = _hash_opcode.get(msg.req_hash)
        if opcode is not None:
            return opcode in _FAST_OPCODES

    # Unknown hash on first REQ_REPEAT -> db executor (safe default)
    return False


def _get_http_client() -> HttpClient:
    global _http_client
    if _http_client is None:
        with _http_client_lock:
            if _http_client is None:
                _http_client = HttpClient()
    return _http_client


def _serialize_headers(headers: Dict[str, str]) -> bytes:
    return json.dumps(headers, separators=(",", ":")).encode("utf-8")


def _deserialize_headers(data: bytes) -> Dict[str, str]:
    """[NO_FALLBACK_V1] Mirror of the proxy-side function in
    proxy/transport_semantic.py. Empty means no headers; a non-empty block that
    will not decode is a framing or codec fault and raises.

    Both used to return {}, on both ends of the wire, so the two sides silently
    agreed that a corrupt header block meant the peer had sent nothing.
    """
    if not data:
        return {}
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise FrameMalformed(
            f"{len(data)}B header block did not decode: {type(e).__name__}: {e} "
            f"(first 64B: {data[:64]!r})") from e
    if not isinstance(obj, dict):
        raise FrameMalformed(
            f"{len(data)}B header block decoded to {type(obj).__name__}, "
            f"expected object")
    return obj


def response_body_hash(dyn_vals, status: int) -> bytes:
    """[SAME_COMPARES_THE_BODY_V1] Hash of what the response IS, not how it was
    encoded this time.

    The pre-[NO_CACHES_V1] code hashed the wrong thing:

        new_raw_hash = hashlib.sha256(sem_resp).digest()     # session.py:818

    sem_resp is the SEMANTIC REPLY BYTES -- execute()'s own docstring calls it
    that. The semantic encoding is diff-encoded against the reply reference, so
    the SAME response body produces DIFFERENT bytes depending on what the
    reference held at that instant: after a reference update, after a REQ_MISS,
    after a reconnect, or for a different peer. The hash then differed although
    the response had not changed, RESP_SAME was missed, and a full DIFF went out
    instead.

    That is why SAME survived at a low steady rate and vanished under a burst: a
    burst churns the reference, so the encoding -- and the hash -- moved on
    almost every request.

    The raw path in the same file always did it correctly:

        new_raw_hash = hashlib.sha256(resp_body_bytes).digest()   # session.py:915

    Both wrote the same `raw_hash` column and compared against the same
    old_raw_hash, so the two paths disagreed about what the column meant.

    dyn_vals is the engine's extracted field values -- the response itself,
    before any encoding. Hashed in a CANONICAL form: sorted by field name, with
    the value serialised by repr so 1 and "1" and 1.0 do not collide, and the
    status folded in so a 200 and a 500 carrying the same text are not "the
    same response". Sorting matters -- field ORDER is a property of the template
    and can shift when a template is re-learned, which would otherwise
    reintroduce exactly the false-difference this fixes.
    """
    h = hashlib.sha256()
    h.update(b"status:%d\n" % int(status))
    for k, v in sorted(dyn_vals or [], key=lambda kv: str(kv[0])):
        h.update(("%s=%r\n" % (k, v)).encode("utf-8", "surrogateescape"))
    return h.digest()


def _diff_encode_response(
    peer_ip: str,
    opcode: int,
    dynamic_vals: List[Tuple[str, Any]],
    type_map: Dict[str, str],
) -> bytes:
    """
    Diff-encode a response against stored reference for this (peer_ip, opcode).

    Falls back to full encoding if diff encoding fails or returns empty.
    """
    codec = _get_codec()
    ref = _get_response_reference(peer_ip, opcode)

    try:
        diff_bytes, new_ref, is_identical = codec.encode_reply_diff(
            opcode=opcode,
            dynamic_vals=dynamic_vals,
            type_map=type_map,
            reference=ref,
            tokens=TokenStore([]),
        )
        _set_response_reference(peer_ip, opcode, new_ref)

        if is_identical or not diff_bytes:
            _debug(f"diff_encode_response: identical or empty for opcode={opcode}, using full encoding")
            return codec.encode_reply(
                opcode=opcode,
                dynamic_vals=dynamic_vals,
                type_map=type_map,
                tokens=TokenStore([]),
            )

        return diff_bytes

    except Exception as e:
        _debug(f"diff_encode_response failed for opcode={opcode}: {e!r}, falling back to full")
        new_ref = {k: v for k, v in (dynamic_vals or [])}
        _set_response_reference(peer_ip, opcode, new_ref)
        return codec.encode_reply(
            opcode=opcode,
            dynamic_vals=dynamic_vals,
            type_map=type_map,
            tokens=TokenStore([]),
        )


def _make_full_req_blob(
    engine: "ExecutionEngine",
    opcode: int,
    peer_ip: str,
    trailer: bytes,
) -> Optional[bytes]:
    """Re-encode the fully-merged request as a FLAG_DIFF=0 (full) packet.
    Stored as req_blob for REQ_DIFF so a future REQ_REPEAT can decode it
    without depending on any reference state."""
    req_tpl, _ = engine.loader.lookup_by_opcode(opcode)
    if req_tpl is None:
        return None

    merged = get_request_ref(peer_ip, opcode)
    if merged is None:
        return None

    url_keys = getattr(req_tpl, "url_query_keys", []) or []
    json_fields = (getattr(req_tpl, "fragment", {}) or {}).get("field_order", []) or []

    url_vals  = [(k, merged.get(k)) for k in url_keys]
    json_vals = [(f, merged.get(f)) for f in json_fields]

    codec = _get_codec()
    full_packet = codec.encode_request(
        opcode=opcode,
        url_vals=url_vals,
        json_vals=json_vals,
        type_map=req_tpl.type_map or {},
        tokens=TokenStore(req_tpl.tokens),
        compress=True,
    )
    return full_packet + trailer


# ====================================================================
# SemanticSession - two-socket, dedicated reader + writer threads
# ====================================================================

class SemanticSession:
    """
    _recv_sock: accepted connection from proxy (proxy->daemon).
                Reader thread reads from this exclusively.
    _send_sock: connection daemon opens back to proxy:9010.
                Writer thread drains reply queue onto this exclusively.

    Worker pool submits (seq, reply, wire_in) tuples to _reply_q.
    Writer thread: _reply_q.get() -> _send_frame on _send_sock.
    Reader thread: _recv_frame on _recv_sock -> executor.submit(_process_frame).
    """

    def __init__(self, recv_sock: socket.socket, peer_ip: str, resolver, http_client=None, return_ip=None):
        self.peer_ip = peer_ip
        self.resolver = resolver
        self._recv_sock = recv_sock
        self._send_sock: Optional[socket.socket] = None
        self._return_ip = return_ip
        self._send_sock_event = threading.Event()
        self._http_client = http_client or _get_http_client()
        self.engine = ExecutionEngine(resolver=self.resolver, http_client=self._http_client)
        self._request_seq = 0
        self._reply_q: queue.Queue[Tuple[int, bytes, int]] = queue.Queue()
        self._alive = True
        # [LINK_POTENTIAL_PING_V1 2026-05-25] The server reads one
        # extra frame past the ping phase (the first non-ping frame
        # = first session frame) before constructing this session.
        # That frame is handed in via set_preread_frame() so the
        # read_loop processes it before pulling more frames from the
        # socket.  Stays None when the proxy sent no app traffic
        # before the read timeout fired in the server.
        self._preread_frame: Optional[bytes] = None

        # [NO_FALLBACK_V1] Same class as the keepalive block in
        # daemon/engine/server.py. A refused TCP_NODELAY means every reply on
        # this session is subject to Nagle - measurable latency on small FNWP
        # frames, attributed to the link rather than to a socket option nobody
        # knows was refused.
        try:
            self._recv_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as e:
            print(f"[DAEMON-SESSION] TCP_NODELAY refused on recv sock for "
                  f"{self.peer_ip}: {type(e).__name__} errno={e.errno} "
                  f"({e.strerror}) - small frames on this session will be "
                  f"delayed by Nagle", flush=True)

    def set_preread_frame(self, frame: bytes) -> None:
        """[LINK_POTENTIAL_PING_V1] Hand the session a frame that the
        server already read off the socket during the ping phase.  The
        read_loop will consume this frame as if it had just been read,
        BEFORE calling _recv_frame() for the first time.

        Must be called BEFORE session.run() starts the read_loop -
        currently that means between __init__ and the thread start in
        DaemonServer._run_session, which is the case at the only call
        site."""
        self._preread_frame = frame

    # ---- two-socket setup: callback with proxy-initiated fallback ----

    def set_send_sock(self, sock):
        """Called by DaemonServer when proxy opens a RETURN connection.

        The proxy opens a second TCP connection to daemon:9009 with
        HELLO body 'RETURN:<ip>'.  The server routes it here.  This
        eliminates the need for the daemon to connect BACK to the proxy,
        which fails under WiFi client isolation.
        """
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as e:
            print(f"[DAEMON-SESSION] TCP_NODELAY refused on RETURN send sock "
                  f"for {self._return_ip}: {type(e).__name__} errno={e.errno} "
                  f"({e.strerror}) - every reply on this return channel will "
                  f"be delayed by Nagle", flush=True)
        self._send_sock = sock
        # Send our HELLO so the proxy knows the return channel is live
        hello = wrap_hello(_get_local_gw())
        _send_frame(sock, hello)
        self._send_sock_event.set()
        print(f"[DAEMON-SESSION] send_sock delivered by proxy for "
              f"{self._return_ip} fd={sock.fileno()}", flush=True)

    def _open_send_sock(self):
        """[RETURN_ONLY_V1] Wait for proxy to deliver a RETURN channel.

        The proxy opens a second TCP connection to daemon:9009 with
        HELLO body 'RETURN:<gw>'.  DaemonServer routes it here via
        set_send_sock().  No outbound callback - that path fails
        through firewalls / WiFi client isolation.
        """
        connect_ip = self._return_ip or self.peer_ip
        trace(f"[Daemon] [RETURN_ONLY_V1] waiting for RETURN from {connect_ip}")
        if self._send_sock_event.wait(timeout=15.0) and self._send_sock is not None:
            return self._send_sock
        raise RuntimeError(
            f"no RETURN connection from {connect_ip} after 15s")
    # ---- END two-socket setup ----

    def run(self) -> None:
        start = time.time()
        trace(f"[Daemon] session start peer={self.peer_ip}")

        # [SAME_IS_BACK_V1] The semcache table must exist before the first
        # upsert. Not fatal if it fails -- the node still serves, it just never
        # answers RESP_SAME -- but it says so rather than failing every write in
        # silence.
        try:
            semcache_db.ensure_table()
        except Exception as e:
            print(f"[Daemon] semcache ensure_table failed: {e!r} -- this node "
                  f"will answer RESP_DIFF only", flush=True)

        # [RETURN_ONLY_V1] Wait for RETURN channel before starting threads.
        try:
            self._send_sock = self._open_send_sock()
        except Exception as e:
            trace(f"[Daemon] [RETURN_ONLY_V1] no RETURN from {self.peer_ip}: {e!r}")
            try:
                self._recv_sock.close()
            except Exception:
                pass
            return

        # Start writer thread
        writer = threading.Thread(target=self._write_loop, daemon=True,
                                  name=f"daemon-writer-{self.peer_ip}")
        writer.start()

        # Reader loop runs in this thread
        try:
            self._read_loop()
        finally:
            self._alive = False
            # Sentinel to unblock writer
            self._reply_q.put((-1, b"", 0))
            try:
                self._recv_sock.close()
            except Exception:
                pass
            try:
                if self._send_sock:
                    self._send_sock.close()
            except Exception:
                pass
            trace(
                f"[Daemon] session closed peer={self.peer_ip} "
                f"dt_ms={(time.time()-start)*1000.0:.1f}"
            )

    def _submit_frame(self, frame: bytes, seq: int, wire_in: int,
                      fast_exec, db_exec) -> None:
        """Classify and dispatch one frame.

        [NO_FALLBACK_V1] _is_fast_frame() now raises FrameMalformed instead of
        silently reclassifying a broken payload as an ordinary slow-path
        request. A malformed frame is a fact about the peer, not about this
        session, so it is named and dropped - the session keeps reading. It is
        NOT executed on either pool: running a request whose opcode could not be
        read is how a framing fault turns into a database query nobody asked
        for.
        """
        try:
            fast = _is_fast_frame(frame)
        except FrameMalformed as e:
            print(f"[DAEMON-SESSION] MALFORMED FRAME from {self.peer_ip} "
                  f"seq={seq} {wire_in}B dropped: {e} "
                  f"(first 32B: {frame[:32]!r})", flush=True)
            return
        (fast_exec if fast else db_exec).submit(
            self._process_frame, frame, seq, wire_in)

    def _read_loop(self) -> None:
        """Reads request frames from _recv_sock, routes to executor.

        Two pools:
          fast_exec (256 threads): echo, discovery -- no database, instant.
          db_exec   (4*cores threads): sensor data -- MySQL-bound, limited
                                   to what the hardware can sustain.

        Dashboard stampede puts 200 sensor queries into db_exec where they
        queue behind each other instead of saturating all 256 threads.
        Echo arrives, goes to fast_exec, responds in milliseconds."""
        fast_exec = _get_executor()
        db_exec = _get_db_executor()
        try:
            # [LINK_POTENTIAL_PING_V1 2026-05-25] If the server pre-
            # read a frame for us (the first non-ping frame after
            # HELLO + ping phase), process it before reading more.
            if self._preread_frame is not None and self._alive:
                frame = self._preread_frame
                self._preread_frame = None
                wire_in = len(frame)
                seq = self._request_seq
                self._request_seq += 1
                self._submit_frame(frame, seq, wire_in, fast_exec, db_exec)

            while self._alive:
                try:
                    frame = _recv_frame(self._recv_sock)
                except Exception as e:
                    _debug(f"recv error from {self.peer_ip}: {e!r}")
                    break

                wire_in = len(frame)
                seq = self._request_seq
                self._request_seq += 1

                self._submit_frame(frame, seq, wire_in, fast_exec, db_exec)

        except Exception as e:
            # [READER_EXIT_REASON_V1] This was `_debug(...)`, which is gated on
            # FROGNET_DEBUG and therefore invisible on every production node.
            # A read loop that dies takes the whole session's request path with
            # it; that must never be a silent event.
            print(f"[DAEMON-SESSION] read_loop for {self.peer_ip} exited on "
                  f"exception: {type(e).__name__}: {e!r} "
                  f"seq={self._request_seq} alive={self._alive}", flush=True)

    def _write_loop(self) -> None:
        """Drains _reply_q, sends tagged frames on _send_sock."""
        print(f"[DAEMON-WRITER] started for {self.peer_ip}", flush=True)
        try:
            while True:
                try:
                    seq, reply, wire_in = self._reply_q.get(timeout=1.0)
                except queue.Empty:
                    if not self._alive:
                        break
                    continue

                # Sentinel
                if seq == -1:
                    break

                try:
                    tagged = struct.pack("!I", seq) + reply
                    _send_frame(self._send_sock, tagged)
                    bump_semantic(peer_ip=self.peer_ip, wire_in=wire_in, wire_out=len(reply))
                    print(f"[DAEMON-WRITER] SENT seq={seq} {len(reply)}B to {self.peer_ip}", flush=True)
                except Exception as e:
                    _debug(f"write_loop send error seq={seq}: {e!r}")
                    self._alive = False
                    break
        except Exception as e:
            _debug(f"write_loop error: {e!r}")
        print(f"[DAEMON-WRITER] exited for {self.peer_ip}", flush=True)

    def _process_frame(self, frame: bytes, seq: int, wire_in: int) -> None:
        """Called in executor thread.  Posts result to _reply_q - never raises."""
        # [PEER_LINK_QUALITY_V1 2026-05-25] Measure the full process
        # time for this frame and record one sample.  This is the
        # universal funnel for incoming frames: both success and
        # exception paths come through here.  exec_ms here is
        # daemon-side processing only - NOT network RTT (that's
        # measured on the proxy side where t0 happens before the
        # send and t1 after the reply).
        _t0_link = time.monotonic()
        _status = "ok"
        try:
            _, reply, _ = self._process_frame_inner(frame, seq, wire_in)
        except Exception as e:
            debug(f"[Daemon PROCESS ERROR] seq={seq} peer={self.peer_ip}: {e!r}")
            reply = wrap_error(503, f"process error: {e!r}")
            _status = "fail"
        _rtt_ms = (time.monotonic() - _t0_link) * 1000.0
        # bytes_out is the reply we are about to enqueue for sending,
        # not "what actually went on the wire" - the write_loop may
        # later fail to send it.  That's fine: from the link's
        # perspective, this frame contributes (wire_in inbound,
        # len(reply) outbound) to demand on the path; a write_loop
        # failure shows up downstream as session teardown plus a
        # gap in samples.
        bump_link_quality(
            peer_ip=self.peer_ip,
            rtt_ms=_rtt_ms,
            bytes_out=len(reply),
            bytes_in=wire_in,
            status=_status,
        )
        self._reply_q.put((seq, reply, wire_in))

    def _process_frame_inner(self, frame: bytes, seq: int, wire_in: int) -> Tuple[int, bytes, int]:
        msg = try_parse(frame)

        if msg is None:
            _debug(f"non-FNW1 frame from {self.peer_ip}, {wire_in} bytes - rejecting")
            return (seq, wrap_error(400, "non-FNW1 frame rejected"), wire_in)

        if _DEBUG:
            print(f"[DBG-DAEMON] === FNW1 {op_name(msg.op)} from {self.peer_ip} seq={seq} wire={wire_in}B ===", flush=True)
            if msg.req_hash:
                print(f"[DBG-DAEMON] req_hash={msg.req_hash.hex()} (len={len(msg.req_hash)})", flush=True)
            if msg.payload:
                print(f"[DBG-DAEMON] payload_len={len(msg.payload)}", flush=True)

        req_hash = msg.req_hash
        can_coalesce = (
            req_hash is not None
            and len(req_hash) == REQ_HASH_LEN
            and req_hash != HEALTH_CHECK_HASH
        )

        if can_coalesce:
            with _daemon_inflight_lock:
                existing = _daemon_inflight.get(req_hash)
                if existing is not None:
                    _debug(f"COALESCE: waiting on in-flight exec for hash={req_hash.hex()} (from {self.peer_ip})")
                else:
                    entry = _DaemonInflight()
                    _daemon_inflight[req_hash] = entry

            if existing is not None:
                if existing.event.wait(timeout=_COALESCE_WAIT_SEC):
                    if existing.error is None and existing.reply is not None:
                        _debug(f"COALESCE: served from in-flight result for hash={req_hash.hex()} ({len(existing.reply)}B)")
                        bump_daemon_coalesce(peer_ip=self.peer_ip)
                        return (seq, existing.reply, wire_in)
                _debug(f"COALESCE: timeout/fail for hash={req_hash.hex()}, executing independently")
                reply = self._dispatch_fnw1(msg)
                return (seq, reply, wire_in)

            try:
                reply = self._dispatch_fnw1(msg)
                entry.reply = reply
            except Exception as e:
                entry.error = e
                reply = wrap_error(503, f"handler error: {e!r}")
            finally:
                entry.event.set()
                with _daemon_inflight_lock:
                    _daemon_inflight.pop(req_hash, None)

            return (seq, reply, wire_in)

        # No coalescing (health check, no hash, unknown op)
        reply = self._dispatch_fnw1(msg)
        return (seq, reply, wire_in)

    def _dispatch_fnw1(self, msg) -> bytes:
        """Dispatch a parsed FNW1 message to the appropriate handler."""
        op_name = {OP_REQ_REPEAT: "REQ_REPEAT", OP_REQ_FULL: "REQ_FULL",
                   OP_REQ_DIFF: "REQ_DIFF", OP_REQ_RAW: "REQ_RAW"}.get(msg.op, f"OP_{msg.op:#04x}")
        print(f"[DAEMON-DISPATCH] {op_name} hash={msg.req_hash.hex()[:8] if msg.req_hash else 'none'} peer={self.peer_ip}", flush=True)
        if msg.op == OP_REQ_REPEAT:
            return self._handle_req_repeat(msg.req_hash)
        elif msg.op == OP_REQ_FULL:
            return self._handle_req_full(msg.req_hash, msg.payload)
        elif msg.op == OP_REQ_DIFF:
            return self._handle_req_diff(msg.req_hash, msg.payload)
        elif msg.op == OP_REQ_RAW:
            return self._handle_req_raw(msg.req_hash, msg.payload)
        else:
            return wrap_error(400, f"unknown FNW1 op: {msg.op}")

    # [SAME_IS_BACK_V1] Restored dispatcher. The purge stub returned REQ_MISS
    # unconditionally -- "Nothing is kept, so the honest answer is always
    # REQ_MISS" -- which meant _reexecute_semantic and _reexecute_raw, restored
    # in the first pass, were DEAD CODE: nothing could reach them. Every
    # REQ_REPEAT the proxy sent was answered with a miss, and the proxy then
    # resent REQ_FULL, so a static endpoint could never see RESP_SAME.
    #
    # semcache_db holds hashes and a bounded body cache, not replayed answers:
    # this path RE-EXECUTES the request and compares the result.
    def _handle_req_repeat(self, req_hash: bytes) -> bytes:
        t0 = time.time()
        if _DEBUG:
            print(f"[DBG-DAEMON] _handle_req_repeat hash={req_hash.hex() if req_hash else 'None'} len={len(req_hash) if req_hash else 0} expected_len={REQ_HASH_LEN}", flush=True)

        if req_hash == HEALTH_CHECK_HASH:
            if _DEBUG:
                print(f"[DBG-DAEMON] health check shortcut", flush=True)
            return wrap_resp_same(HEALTH_CHECK_SAME_ID)

        if not req_hash or len(req_hash) != REQ_HASH_LEN:
            if _DEBUG:
                print(f"[DBG-DAEMON] !!! HASH LENGTH REJECT: got {len(req_hash) if req_hash else 0}, need {REQ_HASH_LEN}", flush=True)
            return wrap_req_miss(req_hash or b"\x00" * REQ_HASH_LEN)

        cached = semcache_db.lookup_for_repeat(req_hash)
        if _DEBUG:
            print(f"[DBG-DAEMON] lookup_for_repeat result: {'HIT' if cached else 'MISS'}", flush=True)
        if cached:
            req_blob, old_raw_hash, old_same_id, is_raw = cached
            # Cache opcode for priority routing on future REQ_REPEAT
            if req_blob and len(req_blob) >= 4 and not is_raw:
                # [NO_FALLBACK_V1] This was `except (struct.error, IndexError):
                # pass`, inherited from the pre-purge original. The opcode drives
                # PRIORITY ROUTING for later REQ_REPEATs, so failing to read it
                # silently mis-routes every repeat of this request with no
                # symptom. The guard above requires >= 4 bytes while the unpack
                # reads bytes 2..6, i.e. needs SIX -- so a 4 or 5 byte blob threw
                # every time and the pass hid it. Check the real length and say
                # so if it is still wrong.
                if len(req_blob) < 6:
                    print("[Daemon] req_blob for %s is %d bytes; need 6 to read "
                          "the opcode - priority routing unavailable for this "
                          "request" % (req_hash.hex()[:8], len(req_blob)),
                          flush=True)
                else:
                    _remember_opcode(req_hash,
                                     struct.unpack("<I", req_blob[2:6])[0])
            if _DEBUG:
                print(f"[DBG-DAEMON]   req_blob_len={len(req_blob) if req_blob else 0} old_raw_hash={old_raw_hash.hex()[:16] if old_raw_hash else 'None'}... old_same_id={old_same_id.hex() if old_same_id else 'None'} is_raw={is_raw}", flush=True)
        if not cached:
            if _DEBUG:
                print(f"[DBG-DAEMON] !!! CACHE MISS for {req_hash.hex()}", flush=True)
            reply = wrap_req_miss(req_hash)
            bump_daemon_cache(
                peer_ip=self.peer_ip,
                req_type="req_repeat",
                resp_type="req_miss",
                bytes_in=REQ_HASH_LEN,
                bytes_out=len(reply),
                exec_ms=(time.time() - t0) * 1000,
            )
            return reply

        req_blob, old_raw_hash, old_same_id, is_raw = cached

        if is_raw:
            return self._reexecute_raw(req_hash, req_blob, old_raw_hash, old_same_id, t0)
        else:
            return self._reexecute_semantic(req_hash, req_blob, old_raw_hash, old_same_id, t0)

    # [NO_CACHES_V1] _reexecute_semantic / _reexecute_raw deleted. Both existed
    # only to serve REQ_REPEAT by comparing a fresh execution against a cached
    # response hash and answering RESP_SAME when they matched.

    # [SAME_IS_BACK_V1] The RAW-signal reply path, restored with its RESP_SAME
    # branch. This is where a [RAW_SIGNAL_V1] execution lands, and it hashes the
    # BODY -- which the raw path always did correctly -- so it needs no change to
    # the comparison, only its SAME branch back.
    def _build_raw_reply(
        self,
        raw_info,
        req_hash: bytes,
        sem_req: bytes,
        cached,
        t0: float,
        req_type: str,
        bytes_in: int,
    ) -> bytes:
        """[RAW_SIGNAL_V1] Engine signaled RESP_RAW (no extractable
        template fields).  Build wire reply, update cache via
        upsert_raw.  Honors SAME shortcut when caller provides a prior
        cached entry."""
        body          = raw_info["body"]
        status        = raw_info["status"]
        headers_bytes = raw_info["headers_bytes"]

        new_raw_hash = hashlib.sha256(body).digest()
        exec_ms = (time.time() - t0) * 1000

        if cached is not None:
            _, old_raw_hash, old_same_id, _ = cached
            if (old_raw_hash and new_raw_hash == old_raw_hash
                    and old_same_id and len(old_same_id) == 16):
                _debug(f"{req_type}->RESP_SAME(raw) for {req_hash.hex()}")
                reply = wrap_resp_same(old_same_id)
                bump_daemon_cache(
                    peer_ip=self.peer_ip,
                    req_type=req_type,
                    resp_type="resp_same",
                    bytes_in=bytes_in,
                    bytes_out=len(reply),
                    bytes_full_response=len(body),
                    exec_ms=exec_ms,
                )
                return reply

        new_same_id = compute_same_id(req_hash, new_raw_hash)
        if status < 400:
            # [SAME_CACHE_IS_MEMORY_ONLY_V1] LRU only; see the REQ_FULL writer.
            semcache_db._lru_put(req_hash, (sem_req, new_raw_hash, new_same_id, True))
        else:
            _debug(f"SKIP CACHE: status={status} for {req_hash.hex()}")

        _debug(f"{req_type}->RESP_RAW for {req_hash.hex()}")
        reply = wrap_resp_raw(new_same_id, status, headers_bytes, body)
        bump_daemon_cache(
            peer_ip=self.peer_ip,
            req_type=req_type,
            resp_type="resp_raw",
            bytes_in=bytes_in,
            bytes_out=len(reply),
            exec_ms=exec_ms,
        )
        return reply

    # ================================================================
    # REQ_FULL handler - BUG 2 FIX
    # ================================================================

    def _reexecute_semantic(
        self,
        req_hash: bytes,
        sem_req: bytes,
        old_raw_hash: bytes,
        old_same_id: bytes,
        t0: float
    ) -> bytes:
        """Re-execute a semantic request and compare to cached response."""

        try:
            opcode = struct.unpack("<I", sem_req[2:6])[0] if len(sem_req) >= 6 else None
        except (struct.error, IndexError):
            opcode = None

        origin_ip, dest_host, normalized = extract_origin_dest_and_normalize_packet(
            packet=sem_req,
            peer_ip=self.peer_ip,
        )

        try:
            sem_resp, status, raw_info, resp_opcode, dyn_vals, type_map = self.engine.execute(
                normalized_packet=normalized,
                peer_ip=self.peer_ip,
                origin_ip=origin_ip,
                dest_host=dest_host,
            )
        except Exception as e:
            debug(f"[Daemon EXEC ERROR] {e!r}")
            return wrap_error(500, f"exec error: {e!r}")

        # [RAW_SIGNAL_V1] Engine signaled RESP_RAW fallback.
        if isinstance(raw_info, dict) and raw_info.get("raw"):
            cached = (None, old_raw_hash, old_same_id, False)
            return self._build_raw_reply(
                raw_info, req_hash, sem_req, cached, t0,
                req_type="req_repeat", bytes_in=REQ_HASH_LEN,
            )

        # Engine-level errors (no templates, decode failures, etc.) produce
        # a legacy sem_resp that can't be diff-decoded.  Return OP_ERROR so
        # the proxy can parse and act on it (e.g. template mismatch -> raw fallback).
        if status >= 400 and dyn_vals is None:
            # [ENGINE_ERROR_PRESERVE_REASON_V1] Forward the engine's ACTUAL reason
            # (e.g. "no templates for opcode=...") instead of a generic string, so the
            # proxy's template-miss detector fires a RAW re-bootstrap rather than
            # dead-ending a recoverable opcode into a permanent 503.
            _er = self.engine.codec.decode_error_reply(sem_resp)
            return wrap_error(status, _er[1] if _er else f"engine error status={status}")

        # [SAME_COMPARES_THE_BODY_V1] was sha256(sem_resp) -- the ENCODED
        # reply, which is diff-encoded against the reply reference and so
        # changes when the reference changes even though the response has
        # not. That is the miss. Hash the response itself.
        new_raw_hash = response_body_hash(dyn_vals, status)
        exec_ms = (time.time() - t0) * 1000

        if new_raw_hash == old_raw_hash:
            # Response unchanged - RESP_SAME
            if dyn_vals is not None and resp_opcode is not None:
                new_ref = {k: v for k, v in dyn_vals}
                _set_response_reference(self.peer_ip, resp_opcode, new_ref)

            _debug(f"REQ_REPEAT->RESP_SAME for {req_hash.hex()}")
            reply = wrap_resp_same(old_same_id)
            bump_daemon_cache(
                peer_ip=self.peer_ip,
                req_type="req_repeat",
                resp_type="resp_same",
                bytes_in=REQ_HASH_LEN,
                bytes_out=len(reply),
                bytes_full_response=len(sem_resp),
                exec_ms=exec_ms,
            )
            return reply
        else:
            # Response changed - RESP_DIFF
            new_same_id = compute_same_id(req_hash, new_raw_hash)

            if dyn_vals is not None and resp_opcode is not None:
                wire_resp = _diff_encode_response(
                    self.peer_ip, resp_opcode, dyn_vals, type_map or {}
                )
            else:
                wire_resp = sem_resp

            # CACHE POISON GUARD: never cache error responses
            if status < 400:
                # [SAME_CACHE_IS_MEMORY_ONLY_V1] LRU only; see the REQ_FULL writer.
                semcache_db._lru_put(req_hash, (sem_req, new_raw_hash, new_same_id, False))
            else:
                _debug(f"SKIP CACHE: status={status} for {req_hash.hex()}")
            _debug(f"REQ_REPEAT->RESP_DIFF for {req_hash.hex()}")
            reply = wrap_resp_diff(new_same_id, wire_resp)
            bump_daemon_cache(
                peer_ip=self.peer_ip,
                req_type="req_repeat",
                resp_type="resp_diff",
                bytes_in=REQ_HASH_LEN,
                bytes_out=len(reply),
                exec_ms=exec_ms,
                opcode=opcode,
            )
            return reply

    def _reexecute_raw(
        self,
        req_hash: bytes,
        http_req: bytes,
        old_raw_hash: bytes,
        old_same_id: bytes,
        t0: float
    ) -> bytes:
        """Re-execute a raw HTTP request and compare to cached response."""

        try:
            req_data = json.loads(http_req.decode("utf-8"))
            method = req_data.get("method", "GET")
            path = req_data.get("path", "/")
            headers = req_data.get("headers", {})
            body = req_data.get("body", "")
            host = req_data.get("host", "127.0.0.1")
            port = req_data.get("port", 8080)
        except Exception as e:
            _debug(f"failed to parse cached REQ_RAW: {e!r}")
            return wrap_req_miss(req_hash)

        http = self._http_client
        try:
            status, resp_headers, resp_body_bytes, resp_text = http.request(
                peer_ip=self.peer_ip,
                upstream_port=port,
                method=method,
                path=path,
                body_text=body,
                headers=headers,
                host_header=host,
            )
        except Exception as e:
            _debug(f"raw HTTP re-exec failed: {e!r}")
            status = 502
            resp_body_bytes = f"upstream error: {e!r}".encode("utf-8")
            resp_headers = {"Content-Type": "text/plain"}

        new_raw_hash = hashlib.sha256(resp_body_bytes).digest()
        exec_ms = (time.time() - t0) * 1000
        headers_bytes = _serialize_headers(resp_headers if isinstance(resp_headers, dict) else {})

        if new_raw_hash == old_raw_hash:
            _debug(f"REQ_REPEAT(raw)->RESP_SAME for {req_hash.hex()}")
            reply = wrap_resp_same(old_same_id)
            bump_daemon_cache(
                peer_ip=self.peer_ip,
                req_type="req_repeat",
                resp_type="resp_same",
                bytes_in=REQ_HASH_LEN,
                bytes_out=len(reply),
                bytes_full_response=len(resp_body_bytes),
                exec_ms=exec_ms,
            )
            return reply
        else:
            new_same_id = compute_same_id(req_hash, new_raw_hash)
            # [SAME_IS_BACK_V1] CACHE POISON GUARD, which the original lacked
            # here. The semantic path has guarded status >= 400 all along -- this
            # raw path did not, and it stores the BODY, so a transient 502 from
            # upstream became the thing later requests were declared "the same"
            # as. Same rule, both paths.
            if status >= 400:
                _debug(f"SKIP CACHE(raw): status={status} for {req_hash.hex()}")
            else:
                # [SAME_CACHE_IS_MEMORY_ONLY_V1] LRU only; see the REQ_FULL writer.
                semcache_db._lru_put(req_hash, (http_req, new_raw_hash, new_same_id, True))
            _debug(f"REQ_REPEAT(raw)->RESP_DIFF for {req_hash.hex()}")
            reply = wrap_resp_raw(new_same_id, status, headers_bytes, resp_body_bytes)
            bump_daemon_cache(
                peer_ip=self.peer_ip,
                req_type="req_repeat",
                resp_type="resp_raw",
                bytes_in=REQ_HASH_LEN,
                bytes_out=len(reply),
                exec_ms=exec_ms,
            )
            return reply

    def _handle_req_full(self, req_hash: bytes, sem_req: bytes) -> bytes:
        """
        Handle REQ_FULL: execute request and return RESP_DIFF.
        """
        t0 = time.time()
        opcode = None
        if _DEBUG:
            print(f"[DBG-DAEMON] _handle_req_full hash={req_hash.hex() if req_hash else 'None'} len={len(req_hash) if req_hash else 0} expected={REQ_HASH_LEN} sem_req_len={len(sem_req) if sem_req else 0}", flush=True)

        if not req_hash or len(req_hash) != REQ_HASH_LEN:
            if _DEBUG:
                print(f"[DBG-DAEMON] !!! HASH LENGTH REJECT in REQ_FULL: got {len(req_hash) if req_hash else 0}, need {REQ_HASH_LEN}", flush=True)
            return wrap_error(400, f"invalid req_hash length: {len(req_hash) if req_hash else 0}")

        if not sem_req or len(sem_req) < SEM_HDR_V1_LEN:
            _debug("invalid sem_req in REQ_FULL")
            return wrap_req_miss(req_hash)

        try:
            opcode = struct.unpack("<I", sem_req[2:6])[0] if len(sem_req) >= 6 else None
        except (struct.error, IndexError):
            opcode = None

        # FIX 15: REQ_FULL means proxy is starting fresh - clear our response
        # reference so _diff_encode_response sends ALL fields, not a partial diff.
        if opcode is not None:
            _clear_response_reference(self.peer_ip, opcode)

        origin_ip, dest_host, normalized = extract_origin_dest_and_normalize_packet(
            packet=sem_req,
            peer_ip=self.peer_ip,
        )

        try:
            sem_resp, status, raw_info, resp_opcode, dyn_vals, type_map = self.engine.execute(
                normalized_packet=normalized,
                peer_ip=self.peer_ip,
                origin_ip=origin_ip,
                dest_host=dest_host,
            )
        except Exception as e:
            debug(f"[Daemon EXEC ERROR] {e!r}")
            return wrap_error(500, f"exec error: {e!r}")

        # [REF_INCOMPLETE_IS_A_REQ_MISS_V1] The engine found the merged request
        # reference missing a declared url_query_key. That is exactly the
        # condition REQ_MISS names -- "I cannot serve this, clear your reference
        # and resend FULL" -- and the proxy has handled REQ_MISS, with a retry
        # budget, since long before this check existed.
        #
        # The engine used to answer it with encode_error_reply(409). The proxy
        # keys its retry on OP_REQ_MISS and has no 409 branch, so the status was
        # relayed to the HTTP caller, urllib raised HTTPError, and the Windows
        # Communicator died in App.__init__ on its first tuple read. It repeated
        # because the daemon cleared ITS reference while the proxy kept its own.
        if isinstance(raw_info, dict) and raw_info.get("req_miss"):
            print(f"[REF_INCOMPLETE_IS_A_REQ_MISS_V1] peer={self.peer_ip} "
                  f"opcode={raw_info.get('opcode')} "
                  f"missing={raw_info.get('missing')} "
                  f"declared={raw_info.get('declared')} - reference cleared, "
                  f"answering REQ_MISS so the proxy resends FULL", flush=True)
            return wrap_req_miss(req_hash or b"\x00" * REQ_HASH_LEN)

        # [RAW_SIGNAL_V1] Engine signaled RESP_RAW fallback.  REQ_FULL means
        # proxy starts fresh - no prior cache to honor SAME against.
        if isinstance(raw_info, dict) and raw_info.get("raw"):
            return self._build_raw_reply(
                raw_info, req_hash, sem_req, None, t0,
                req_type="req_full", bytes_in=len(sem_req),
            )

        # Engine-level errors: return OP_ERROR so proxy can parse it
        if status >= 400 and dyn_vals is None:
            # [ENGINE_ERROR_PRESERVE_REASON_V1] Forward the engine's ACTUAL reason
            # (e.g. "no templates for opcode=...") instead of a generic string, so the
            # proxy's template-miss detector fires a RAW re-bootstrap rather than
            # dead-ending a recoverable opcode into a permanent 503.
            _er = self.engine.codec.decode_error_reply(sem_resp)
            return wrap_error(status, _er[1] if _er else f"engine error status={status}")

        # [SAME_COMPARES_THE_BODY_V1] NOT the full-encoded response, whatever
        # the old comment said. This is the WRITER: REQ_FULL stores the entry a
        # later REQ_REPEAT compares against. If the two ends of that comparison
        # hash different things, SAME can never match -- and hashing the encoded
        # reply here means the stored value depends on the reference state at
        # write time, so it will not equal the value computed at compare time
        # even for a byte-identical response.
        new_raw_hash = response_body_hash(dyn_vals, status)
        if _DEBUG:
            print(f"[DBG-DAEMON] REQ_FULL executed: status={status} sem_resp_len={len(sem_resp)} new_raw_hash={new_raw_hash.hex()[:16]}...", flush=True)

        # ------------------------------------------------------------------
        # BUG 2 NOTE: The RESP_SAME shortcut for unchanged responses lives
        # ONLY in _handle_req_repeat.  REQ_FULL means the proxy needs the
        # actual payload (cold start, cache eviction, SAME miss recovery).
        # Always respond with RESP_DIFF here so the proxy can populate its
        # cache.  Cost: one extra RESP_DIFF on the rare REQ_FULL-after-miss.
        # ------------------------------------------------------------------

        # Response is new or changed - RESP_DIFF path
        sid = compute_same_id(req_hash, new_raw_hash)

        if dyn_vals is not None and resp_opcode is not None:
            wire_resp = _diff_encode_response(
                self.peer_ip, resp_opcode, dyn_vals, type_map or {}
            )
        else:
            wire_resp = sem_resp

        # [SAME_IS_BACK_V1] Store the entry a later REQ_DIFF/REQ_REPEAT compares
        # against. Without this writer the SAME check above can never hit -- the
        # lookup returns nothing, forever.
        #
        # CACHE POISON GUARD: never store an error response, or a transient 500
        # becomes the thing every later request is declared "the same" as.
        if status < 400:
            # [SAME_CACHE_IS_MEMORY_ONLY_V1] (req_blob, raw_hash, same_id,
            # is_raw) -- the exact tuple lookup_for_repeat returns.
            semcache_db._lru_put(req_hash, (sem_req, new_raw_hash, sid, False))
        else:
            _debug(f"SKIP CACHE: status={status} for {req_hash.hex()}")

        exec_ms = (time.time() - t0) * 1000

        _debug(f"REQ_FULL->RESP_DIFF for {req_hash.hex()}")
        reply = wrap_resp_diff(sid, wire_resp)
        _resp_type = "resp_diff"
        bump_daemon_cache(
            peer_ip=self.peer_ip,
            req_type="req_full",
            resp_type=_resp_type,
            bytes_in=len(sem_req),
            bytes_out=len(reply),
            exec_ms=exec_ms,
            opcode=opcode,
        )
        return reply

    # ================================================================
    # REQ_DIFF handler - diff-encoded request (changed fields only)
    # ================================================================

    def _handle_req_diff(self, req_hash: bytes, diff_payload: bytes) -> bytes:
        """
        Handle REQ_DIFF: execute diff-encoded request, return RESP_SAME or RESP_DIFF.

        The proxy sent only the changed fields.  The execution engine merges
        them with its stored reference (per peer_ip, opcode) to reconstruct
        the full request.  If no reference exists (daemon restart, first
        contact), respond REQ_MISS so the proxy retries as REQ_FULL.
        """
        t0 = time.time()
        opcode = None

        if not req_hash or len(req_hash) != REQ_HASH_LEN:
            _debug(f"invalid req_hash in REQ_DIFF: got {len(req_hash) if req_hash else 0} bytes")
            return wrap_req_miss(req_hash or b"\x00" * REQ_HASH_LEN)

        if not diff_payload or len(diff_payload) < SEM_HDR_V1_LEN:
            _debug("invalid diff_payload in REQ_DIFF")
            return wrap_req_miss(req_hash)

        try:
            opcode = struct.unpack("<I", diff_payload[2:6])[0] if len(diff_payload) >= 6 else None
        except (struct.error, IndexError):
            opcode = None

        # Guard: execution engine must have a reference to merge the diff.
        # Without it, the diff payload is not self-contained.
        if opcode is None or not has_request_ref(self.peer_ip, opcode):
            _debug(f"REQ_DIFF->REQ_MISS for {req_hash.hex()} (no request ref for opcode={opcode})")
            reply = wrap_req_miss(req_hash)
            bump_daemon_cache(
                peer_ip=self.peer_ip,
                req_type="req_diff",
                resp_type="req_miss",
                bytes_in=len(diff_payload),
                bytes_out=len(reply),
                exec_ms=(time.time() - t0) * 1000,
            )
            return reply

        origin_ip, dest_host, normalized = extract_origin_dest_and_normalize_packet(
            packet=diff_payload,
            peer_ip=self.peer_ip,
        )

        try:
            sem_resp, status, raw_info, resp_opcode, dyn_vals, type_map = self.engine.execute(
                normalized_packet=normalized,
                peer_ip=self.peer_ip,
                origin_ip=origin_ip,
                dest_host=dest_host,
            )
        except Exception as e:
            debug(f"[Daemon EXEC ERROR] REQ_DIFF {e!r}")
            return wrap_error(500, f"exec error: {e!r}")

        # [REF_INCOMPLETE_IS_A_REQ_MISS_V1] The engine found the merged request
        # reference missing a declared url_query_key. That is exactly the
        # condition REQ_MISS names -- "I cannot serve this, clear your reference
        # and resend FULL" -- and the proxy has handled REQ_MISS, with a retry
        # budget, since long before this check existed.
        #
        # The engine used to answer it with encode_error_reply(409). The proxy
        # keys its retry on OP_REQ_MISS and has no 409 branch, so the status was
        # relayed to the HTTP caller, urllib raised HTTPError, and the Windows
        # Communicator died in App.__init__ on its first tuple read. It repeated
        # because the daemon cleared ITS reference while the proxy kept its own.
        if isinstance(raw_info, dict) and raw_info.get("req_miss"):
            print(f"[REF_INCOMPLETE_IS_A_REQ_MISS_V1] peer={self.peer_ip} "
                  f"opcode={raw_info.get('opcode')} "
                  f"missing={raw_info.get('missing')} "
                  f"declared={raw_info.get('declared')} - reference cleared, "
                  f"answering REQ_MISS so the proxy resends FULL", flush=True)
            return wrap_req_miss(req_hash or b"\x00" * REQ_HASH_LEN)

        # [RAW_SIGNAL_V1] Engine signaled RESP_RAW fallback.
        if isinstance(raw_info, dict) and raw_info.get("raw"):
            return self._build_raw_reply(
                raw_info, req_hash, diff_payload, None, t0,
                req_type="req_diff", bytes_in=len(diff_payload),
            )

        # Engine-level errors: return OP_ERROR so proxy can parse it
        if status >= 400 and dyn_vals is None:
            # [ENGINE_ERROR_PRESERVE_REASON_V1] Forward the engine's ACTUAL reason
            # (e.g. "no templates for opcode=...") instead of a generic string, so the
            # proxy's template-miss detector fires a RAW re-bootstrap rather than
            # dead-ending a recoverable opcode into a permanent 503.
            _er = self.engine.codec.decode_error_reply(sem_resp)
            return wrap_error(status, _er[1] if _er else f"engine error status={status}")

        # [SAME_COMPARES_THE_BODY_V1] same writer, same rule.
        new_raw_hash = response_body_hash(dyn_vals, status)

        # [SAME_IS_BACK_V1] REQ_DIFF is the HOT PATH -- this is where a repeated
        # request actually lands once templates are warm. The first restoration
        # pass only brought back _reexecute_semantic/_reexecute_raw, which serve
        # REQ_REPEAT, and REQ_REPEAT only fires on the BOOTSTRAP path. So a live
        # run showed 8210 REQ_FULL, 4130 REQ_DIFF, 0 REQ_REPEAT and 0 RESP_SAME:
        # the branch had been restored somewhere the traffic never goes.
        cached = semcache_db.lookup_for_repeat(req_hash)
        # [SAME_SAYS_WHY_NOT_V1] One line naming why SAME did not fire.
        #
        # 5458 REQ_DIFF, 0 RESP_SAME, with req_hash cfb6c1d5 repeating 2165
        # times -- so the lookup had something to find. "No lookup entry" and
        # "entry found, hashes differ" need opposite fixes and were
        # indistinguishable from the outside. Rate-limited to the first
        # occurrence per req_hash so it names the case without flooding.
        # Keyed on (req_hash, REASON). Keying on req_hash alone meant the first
        # request logged "NO entry / WROTE" and thereby suppressed the
        # "hash differs" line on every request after it -- the one that says
        # what is actually wrong. The diagnostic hid its own finding.
        if not hasattr(self, "_same_why_seen"):
            self._same_why_seen = set()

        def _why(reason, msg):
            k = (req_hash, reason)
            if k not in self._same_why_seen:
                self._same_why_seen.add(k)
                print("[SAME-WHY] %s: %s" % (req_hash.hex()[:8], msg), flush=True)

        if cached:
            _, old_raw_hash, old_same_id, _ = cached
            if not (old_raw_hash and new_raw_hash == old_raw_hash):
                # Show the VALUES, not just the digests. If a single field is
                # varying between two supposedly identical responses, the digest
                # says only that something did.
                _why("hashdiff",
                     "entry FOUND but hash DIFFERS old=%s new=%s status=%s\n"
                     "             fields=%r"
                     % (old_raw_hash.hex()[:16] if old_raw_hash else None,
                        new_raw_hash.hex()[:16], status,
                        [(k, (v[:40] if isinstance(v, str) else v))
                         for k, v in (dyn_vals or [])][:10]))
            elif not (old_same_id and len(old_same_id) == 16):
                _why("sameid", "hash MATCHES but same_id is %r" % (old_same_id,))
            else:
                _why("ok", "hash MATCHES -> RESP_SAME")
        else:
            _why("noentry", "NO semcache entry for this req_hash")
        if cached:
            _, old_raw_hash, old_same_id, _ = cached
            if (old_raw_hash and new_raw_hash == old_raw_hash
                    and old_same_id and len(old_same_id) == 16):
                if dyn_vals is not None and resp_opcode is not None:
                    new_ref = {k: v for k, v in dyn_vals}
                    _set_response_reference(self.peer_ip, resp_opcode, new_ref)
                exec_ms = (time.time() - t0) * 1000
                _debug(f"REQ_DIFF->RESP_SAME for {req_hash.hex()}")
                reply = wrap_resp_same(old_same_id)
                bump_daemon_cache(
                    peer_ip=self.peer_ip,
                    req_type="req_diff",
                    resp_type="resp_same",
                    bytes_in=len(diff_payload),
                    bytes_out=len(reply),
                    bytes_full_response=len(sem_resp),
                    exec_ms=exec_ms,
                    opcode=opcode,
                )
                return reply

        # Response is new or changed - RESP_DIFF path
        sid = compute_same_id(req_hash, new_raw_hash)

        if dyn_vals is not None and resp_opcode is not None:
            wire_resp = _diff_encode_response(
                self.peer_ip, resp_opcode, dyn_vals, type_map or {}
            )
        else:
            wire_resp = sem_resp

        # [NO_CACHES_V1] the response is not stored.

        # [SAME_IS_BACK_V1] REQ_DIFF writer. Same guard as REQ_FULL.
        # [SAME_CACHE_IS_MEMORY_ONLY_V1] LRU only -- no MySQL on the response
        # path. semcache_db.upsert() writes MySQL and does NOT populate the
        # daemon's own LRU, so it cost a disk round-trip HERE and the next
        # lookup_for_repeat still had to fault the row back in. Worst of both.
        # lookup_for_repeat's fast path is _lru_get, which is what actually
        # serves REQ_REPEAT.
        if status < 400:
            if not hasattr(self, "_same_wrote"):
                self._same_wrote = set()
            if req_hash not in self._same_wrote:
                self._same_wrote.add(req_hash)
                print("[SAME-WHY] %s: WROTE semcache entry raw_hash=%s "
                      "fields=%r"
                      % (req_hash.hex()[:8], new_raw_hash.hex()[:16],
                         [(k, (v[:40] if isinstance(v, str) else v))
                          for k, v in (dyn_vals or [])][:10]), flush=True)
            # diff_payload is NOT self-contained: decoding it needs the request
            # reference in exactly the state it was at this moment. A future
            # REQ_REPEAT re-executes by handing this blob to engine.execute(),
            # which merges against the CURRENT reference -- and that will have
            # advanced. Re-encode the fully-merged values as a FLAG_DIFF=0 full
            # packet so it decodes correctly at any time.
            #
            # I stored diff_payload directly here; the original did not, and its
            # comment says why. A REQ_REPEAT against that entry would have
            # reconstructed the WRONG request and returned a confidently wrong
            # answer -- worse than a miss.
            trailer = diff_payload[len(normalized):]
            _req_blob = (_make_full_req_blob(self.engine, opcode, self.peer_ip,
                                             trailer) or diff_payload)
            # [SAME_CACHE_IS_MEMORY_ONLY_V1] (req_blob, raw_hash, same_id,
            # is_raw) -- the exact tuple lookup_for_repeat returns.
            semcache_db._lru_put(req_hash, (_req_blob, new_raw_hash, sid, False))
        else:
            _debug(f"SKIP CACHE: status={status} for {req_hash.hex()}")

        exec_ms = (time.time() - t0) * 1000
        _debug(f"REQ_DIFF->RESP_DIFF for {req_hash.hex()}")
        reply = wrap_resp_diff(sid, wire_resp)
        bump_daemon_cache(
            peer_ip=self.peer_ip,
            req_type="req_diff",
            resp_type="resp_diff",
            bytes_in=len(diff_payload),
            bytes_out=len(reply),
            exec_ms=exec_ms,
            opcode=opcode,
        )
        return reply

    # ================================================================
    # REQ_RAW handler
    # ================================================================

    def _handle_req_raw(self, req_hash: bytes, http_req: bytes) -> bytes:
        """Handle REQ_RAW: execute raw HTTP request and return RESP_RAW."""
        t0 = time.time()

        if not req_hash or len(req_hash) != REQ_HASH_LEN:
            _debug(f"invalid req_hash in REQ_RAW: got {len(req_hash) if req_hash else 0} bytes")
            return wrap_req_miss(req_hash or b"\x00" * REQ_HASH_LEN)

        if not http_req:
            _debug("empty http_req in REQ_RAW")
            return wrap_req_miss(req_hash)

        try:
            req_data = json.loads(http_req.decode("utf-8"))
            method = req_data.get("method", "GET")
            path = req_data.get("path", "/")
            headers = req_data.get("headers", {})
            body = req_data.get("body", "")
            host = req_data.get("host", "127.0.0.1")
            port = req_data.get("port", 8080)
        except Exception as e:
            _debug(f"failed to parse REQ_RAW: {e!r}")
            return wrap_req_miss(req_hash)

        http = self._http_client
        try:
            status, resp_headers, resp_body_bytes, resp_text = http.request(
                peer_ip=self.peer_ip,
                upstream_port=port,
                method=method,
                path=path,
                body_text=body,
                headers=headers,
                host_header=host,
            )
        except Exception as e:
            _debug(f"raw HTTP failed: {e!r}")
            status = 502
            resp_body_bytes = f"upstream error: {e!r}".encode("utf-8")
            resp_headers = {"Content-Type": "text/plain"}

        new_raw_hash = hashlib.sha256(resp_body_bytes).digest()
        sid = compute_same_id(req_hash, new_raw_hash)
        headers_bytes = _serialize_headers(resp_headers if isinstance(resp_headers, dict) else {})

        # [NO_CACHES_V1] the body is not stored.

        exec_ms = (time.time() - t0) * 1000

        _debug(f"REQ_RAW: {len(resp_body_bytes)}B for {req_hash.hex()}")
        reply = wrap_resp_raw(sid, status, headers_bytes, resp_body_bytes)
        bump_daemon_cache(
            peer_ip=self.peer_ip,
            req_type="req_raw",
            resp_type="resp_raw",
            bytes_in=len(http_req),
            bytes_out=len(reply),
            exec_ms=exec_ms,
        )
        return reply

# ---- DEPLOYMENT MARKER ----
# v2026-04-06: priority executor -- fast unlimited, db throttled to hardware
print("[session] BUILD=2026-03-24-v1", flush=True)
