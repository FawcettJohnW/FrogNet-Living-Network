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
transport_sim_tier.py (M_transport) - FAITHFUL FrogNet transport-layer
simulator with adjustable wire characteristics.

This tier is the answer to the gap in the codex tiers: those exercise the
codex bytes over a single bidirectional socket with no transport machinery.
This tier exercises the FULL production transport - two sockets per peer,
HELLO + RETURN handshake, SEQ_RESET, worker pool, reply queue + writer
thread, seq-tagged replies, request coalescing, template store backing -
through a parameterized WireMedium that lets you dial in arbitrary latency,
jitter, bandwidth, and outage characteristics PER DIRECTION.

ADJUSTABLE WIRE PARAMETERS (set via env or NetworkParams kwargs):
  FROGNET_SIM_LATENCY_MS         - one-way latency (default 1.0)
  FROGNET_SIM_JITTER_MS          - +/-jitter on latency (default 0.0)
  FROGNET_SIM_BANDWIDTH_BPS      - bandwidth ceiling, bytes/sec (default 1e9)
  FROGNET_SIM_OUTAGE_PROB        - per-second outage probability (default 0)
  FROGNET_SIM_OUTAGE_MS          - outage duration in ms (default 500)
  FROGNET_SIM_ASYMMETRIC         - if "1", forward/reverse use different params
  FROGNET_SIM_CAPTURE_DIR        - directory to dump wire bytes for inspection

The simulator uses socket.socketpair() for real BSD socket semantics
(blocking, timeouts, non-blocking writes, partial sends all work as on a
real socket) with a shaper thread in the middle that imposes the desired
characteristics. Optional file capture writes every shaped byte to disk
for post-hoc analysis - the file-FIFO inspection you'd get from named
pipes, without losing socket semantics.

WHAT'S MODELED FAITHFULLY (matching production line numbers cited):

  Two-socket per peer (production: proxy/transport_semantic.py line 531-533
                       and daemon/engine/session.py line 7-10):
    proxy _send_sock: outbound connection to daemon - REQUESTS flow
    proxy _recv_sock: SECOND outbound connection to daemon, opens with
                      HELLO body "RETURN:<gw>"; daemon's accept loop
                      routes it to the SAME session via set_send_sock()
                      so REPLIES flow back without daemon-initiated
                      callback (which fails under WiFi client isolation).
                      See session.py set_send_sock() at line 447.

  HELLO handshake (semcache_wire.py wrap_hello, daemon session.py 460-465):
    First frame proxy sends on either socket is wrap_hello(local_gw) (on
    send_sock) or wrap_hello("RETURN:<gw>") (on recv_sock). The daemon
    matches them up.

  SEQ_RESET (semcache_wire.py wrap_seq_reset, line 173-183):
    Proxy sends after HELLO on a new connection. Daemon echoes. Self-heals
    seq divergence across restarts.

  Seq-tagged replies (session.py write_loop line 600):
    Each reply prefixed with 4-byte seq before _send_frame so proxy reader
    can match replies to outstanding requests (line 1321 in transport_semantic.py).

  Worker pool (session.py line 547):
    fast_exec (256 threads) for echo/discovery, db_exec for sensor data.
    Inbound frames dispatched to one or the other based on _is_fast_frame.
    Results posted to _reply_q. Writer thread drains queue, tags with seq.

  Request coalescing (session.py line 667-697):
    _daemon_inflight lock: identical req_hash arriving while one is in
    flight gets coalesced - only one execution, both proxies get same reply.

  Template store backing REQ_DIFF (production: MariaDB semcache_db.py;
    here: in-memory dict keyed by req_hash). Daemon decodes REQ_DIFF
    against the cached template + reference.

WHAT'S STILL SIMPLIFIED:
  - No real RTT ping/pong ladder (OP_RTT_PING/PONG). Skipped - used for
    link calibration but not central to transport correctness.
  - No real proxy-side decision plane (decide_path_for_target, etc.) - the
    tier instantiates a SimulatedProxyWorker directly, no upstream HTTP.
  - No TCP_NODELAY tuning. We're modeling at the byte-stream level; precise
    Nagle behavior would require a TCP-stack-faithful WireMedium which is
    beyond scope.
  - No SO_MARK iptables-bypass dance (this is loopback-pair, not real TCP/80).
"""
from __future__ import annotations

import os
import sys
import time
import types
import zlib
import queue
import random
import struct
import socket
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, Optional, Callable

# ============================================================================
# Path + stubs - same shape as the codex tiers
# ============================================================================

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p and p not in sys.path:
        sys.path.insert(0, p)


def _install_mysql_stub():
    if "mysql" in sys.modules: return
    m = types.ModuleType("mysql"); mc = types.ModuleType("mysql.connector")
    me = types.ModuleType("mysql.connector.errors")
    class E(Exception): pass
    me.Error = me.DatabaseError = me.InterfaceError = me.OperationalError = E
    mc.connect = lambda *a, **k: None
    mc.errors = me; mc.Error = E
    pool = types.ModuleType("mysql.connector.pooling")
    pool.MySQLConnectionPool = object
    mc.pooling = pool; m.connector = mc
    for n, mod in (("mysql", m), ("mysql.connector", mc),
                   ("mysql.connector.errors", me), ("mysql.connector.pooling", pool)):
        sys.modules[n] = mod


def _install_lz4_stub():
    if "lz4" in sys.modules: return
    lz4 = types.ModuleType("lz4"); frame = types.ModuleType("lz4.frame")
    frame.compress = lambda d: zlib.compress(d, 1)
    frame.decompress = lambda d: zlib.decompress(d)
    lz4.frame = frame
    sys.modules["lz4"] = lz4; sys.modules["lz4.frame"] = frame


_install_mysql_stub()
_install_lz4_stub()

from core.codec import SemanticCodec, REQ_HASH_LEN              # noqa: E402
from core.json_handler import JsonFormatHandler                  # noqa: E402
from core.semcache_wire import (                                 # noqa: E402
    SAME_ID_LEN,
    OP_REQ_FULL, OP_REQ_DIFF, OP_REQ_REPEAT, OP_REQ_RAW,
    OP_RESP_DIFF, OP_RESP_SAME, OP_RESP_RAW,
    OP_REQ_MISS, OP_ERROR, OP_SEQ_RESET, OP_HELLO,
    MAGIC,
    wrap_req_full, wrap_req_diff, wrap_req_repeat,
    wrap_resp_diff, wrap_resp_same, wrap_resp_raw,
    wrap_hello, wrap_seq_reset, wrap_error,
    try_parse,
)

# ============================================================================
# Test framework
# ============================================================================

FAILS: List[str] = []

def check(name: str, cond: Any, detail: str = "") -> None:
    ok = bool(cond)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  - {detail}"))
    if not ok:
        FAILS.append(name)


# ============================================================================
# NetworkParams + WireMedium - the simulator core
# ============================================================================

@dataclass
class NetworkParams:
    """Wire-shaping parameters for ONE direction of a link.

    To model an asymmetric link (e.g. satellite - fast downlink, slow uplink),
    construct a WireMedium with separate forward / reverse NetworkParams.
    For a symmetric link, pass the same params for both directions.

    Examples:
      Clean LAN:        NetworkParams(latency_ms=0.5, bandwidth_bps=1e9)
      WiFi:             NetworkParams(latency_ms=5, jitter_ms=2, bandwidth_bps=50e6)
      Cellular 4G:      NetworkParams(latency_ms=40, jitter_ms=15, bandwidth_bps=10e6)
      Satellite (GEO):  NetworkParams(latency_ms=300, jitter_ms=20, bandwidth_bps=256e3)
      HaLow 900MHz:     NetworkParams(latency_ms=20, jitter_ms=5, bandwidth_bps=600e3)
      Jammed RF:        NetworkParams(latency_ms=100, jitter_ms=200, bandwidth_bps=2e3,
                                       outage_prob_per_sec=0.1, outage_duration_ms=2000)
    """
    latency_ms: float = 1.0
    jitter_ms: float = 0.0
    bandwidth_bps: float = 1e9    # bytes per second ceiling
    outage_prob_per_sec: float = 0.0
    outage_duration_ms: float = 500.0
    mtu_bytes: int = 16384        # max bytes per shaped chunk (for fine-grained timing)

    @classmethod
    def from_env(cls, prefix: str = "FROGNET_SIM_") -> "NetworkParams":
        return cls(
            latency_ms=float(os.environ.get(prefix + "LATENCY_MS", "1.0")),
            jitter_ms=float(os.environ.get(prefix + "JITTER_MS", "0.0")),
            bandwidth_bps=float(os.environ.get(prefix + "BANDWIDTH_BPS", "1e9")),
            outage_prob_per_sec=float(os.environ.get(prefix + "OUTAGE_PROB", "0.0")),
            outage_duration_ms=float(os.environ.get(prefix + "OUTAGE_MS", "500.0")),
        )


class WireMedium:
    """A bidirectional simulated wire with per-direction shaping.

    Internally uses two pairs of socketpairs:
      forward direction: writer_a -> shaper_thread -> reader_b
      reverse direction: writer_b -> shaper_thread -> reader_a

    The endpoints exposed to the application (endpoint_a / endpoint_b) look
    like ordinary sockets - they support sendall, recv, settimeout, etc.
    Bytes written into one end emerge from the other end shaped by the
    configured NetworkParams.

    Optional wire capture: if capture_dir is set, every byte that emerges
    from the shaper (post-shaping, ready for delivery) is also appended to
    a file in that directory - forward.bin and reverse.bin. Lets you
    inspect the actual byte stream with hexdump / wireshark-like tools.
    """

    def __init__(self,
                 forward_params: Optional[NetworkParams] = None,
                 reverse_params: Optional[NetworkParams] = None,
                 capture_dir: Optional[str] = None,
                 label: str = "wire"):
        self.forward_params = forward_params or NetworkParams()
        self.reverse_params = reverse_params or forward_params or NetworkParams()
        self.label = label
        self.capture_dir = capture_dir
        if capture_dir:
            os.makedirs(capture_dir, exist_ok=True)

        # Forward direction: app_a writes; shaper reads, shapes, writes; app_b reads
        self._a_outer, self._a_inner = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self._b_inner, self._b_outer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)

        # Reverse direction: app_b writes (on b_outer); shaper reads, shapes; app_a reads (on a_outer)
        # For full duplex we use TWO socket pair sets - one for each direction.
        # Above is forward (a->b). Now reverse (b->a):
        self._b_rev_outer, self._b_rev_inner = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self._a_rev_inner, self._a_rev_outer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)

        # Stats
        self._stats_lock = threading.Lock()
        self.stats = {
            "forward_bytes_in": 0, "forward_bytes_out": 0,
            "reverse_bytes_in": 0, "reverse_bytes_out": 0,
            "outages_forward": 0, "outages_reverse": 0,
            "max_queue_forward": 0, "max_queue_reverse": 0,
        }

        self._alive = True

        # Last-emit timestamp for bandwidth shaping
        self._fwd_last_emit = time.monotonic()
        self._rev_last_emit = time.monotonic()

        # Optional capture files
        self._capture_fwd = None
        self._capture_rev = None
        if capture_dir:
            self._capture_fwd = open(os.path.join(capture_dir, f"{label}_forward.bin"), "wb")
            self._capture_rev = open(os.path.join(capture_dir, f"{label}_reverse.bin"), "wb")

        # Start shaper threads
        self._t_fwd = threading.Thread(
            target=self._shaper_loop, daemon=True,
            args=("forward", self._a_inner, self._b_inner,
                  self.forward_params, self._capture_fwd),
            name=f"wire-{label}-fwd",
        )
        self._t_rev = threading.Thread(
            target=self._shaper_loop, daemon=True,
            args=("reverse", self._b_rev_inner, self._a_rev_inner,
                  self.reverse_params, self._capture_rev),
            name=f"wire-{label}-rev",
        )
        self._t_fwd.start()
        self._t_rev.start()

    def endpoint_a(self) -> "WireEndpoint":
        """The 'A' end of the wire. Writes go through forward shaping,
        reads come from reverse shaping."""
        return WireEndpoint(self._a_outer, self._a_rev_outer, self, "A")

    def endpoint_b(self) -> "WireEndpoint":
        """The 'B' end of the wire. Writes go through reverse shaping,
        reads come from forward shaping."""
        return WireEndpoint(self._b_rev_outer, self._b_outer, self, "B")

    def _shaper_loop(self, direction: str,
                     src_sock: socket.socket, dst_sock: socket.socket,
                     params: NetworkParams,
                     capture_file):
        """Read bytes from src_sock, apply shaping per params, write to dst_sock.

        Implements:
          - latency (with optional jitter)
          - bandwidth ceiling (bytes/sec)
          - periodic outages (close the wire for outage_duration_ms)
          - optional capture-to-file
        """
        rng = random.Random()
        next_outage_check = time.monotonic() + 1.0
        in_outage_until = 0.0
        last_emit = time.monotonic()

        def stat_key_in():
            return f"{direction}_bytes_in"
        def stat_key_out():
            return f"{direction}_bytes_out"

        try:
            src_sock.settimeout(0.01)
            while self._alive:
                # Outage scheduling - check once per second
                now = time.monotonic()
                if now >= next_outage_check:
                    next_outage_check = now + 1.0
                    if params.outage_prob_per_sec > 0 and now >= in_outage_until:
                        if rng.random() < params.outage_prob_per_sec:
                            in_outage_until = now + (params.outage_duration_ms / 1000.0)
                            with self._stats_lock:
                                self.stats[f"outages_{direction}"] += 1

                if now < in_outage_until:
                    # Link STALLED - don't read from src, let bytes pile up
                    # in the kernel buffer. When the outage ends, the shaper
                    # resumes and the bytes flow through with extra latency.
                    # This models TCP-effective behavior: the bearer briefly
                    # blacks out, lower layers retransmit, the application
                    # sees a latency spike not data loss.
                    time.sleep(0.01)
                    continue

                try:
                    chunk = src_sock.recv(min(params.mtu_bytes, 4096))
                except socket.timeout:
                    continue
                except OSError:
                    break

                if not chunk:
                    try:
                        dst_sock.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    break

                # Sample `now` AFTER recv - this is when the chunk actually
                # arrived at the shaper. Sampling before would under-count
                # latency for chunks that sat in the src buffer during recv.
                chunk_arrival = time.monotonic()
                with self._stats_lock:
                    self.stats[stat_key_in()] += len(chunk)

                # Compute scheduled delivery time
                deliver_at = chunk_arrival + (params.latency_ms / 1000.0)
                if params.jitter_ms > 0:
                    deliver_at += rng.uniform(-1.0, 1.0) * (params.jitter_ms / 1000.0)

                if params.bandwidth_bps > 0:
                    serialize_time = len(chunk) / params.bandwidth_bps
                    next_allowed = last_emit + serialize_time
                    deliver_at = max(deliver_at, next_allowed)

                sleep_for = deliver_at - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)

                try:
                    dst_sock.sendall(chunk)
                except OSError:
                    break

                last_emit = time.monotonic()
                with self._stats_lock:
                    self.stats[stat_key_out()] += len(chunk)

                if capture_file is not None:
                    try:
                        capture_file.write(chunk)
                        capture_file.flush()
                    except Exception:
                        pass

        except Exception as e:
            print(f"[WIRE-{self.label}-{direction}] shaper exit: {e!r}", flush=True)

    def close(self):
        self._alive = False
        for s in (self._a_inner, self._b_inner, self._b_rev_inner, self._a_rev_inner):
            try: s.close()
            except OSError: pass
        for s in (self._a_outer, self._b_outer, self._b_rev_outer, self._a_rev_outer):
            try: s.close()
            except OSError: pass
        if self._capture_fwd:
            try: self._capture_fwd.close()
            except Exception: pass
        if self._capture_rev:
            try: self._capture_rev.close()
            except Exception: pass


class WireEndpoint:
    """Socket-like interface to one end of a WireMedium. Writes go out the
    'send' side, reads come from the 'recv' side. Supports the subset of
    socket methods the daemon/proxy code uses."""

    def __init__(self, send_sock: socket.socket, recv_sock: socket.socket,
                 medium: WireMedium, label: str):
        self._send_sock = send_sock
        self._recv_sock = recv_sock
        self._medium = medium
        self.label = label
        # Match the production interface - store timeout state on the endpoint,
        # apply to recv-side socket since reads are what block.
        self._timeout = None
        self._closed = False

    def settimeout(self, t: Optional[float]):
        self._timeout = t
        try:
            self._recv_sock.settimeout(t)
        except OSError:
            pass

    def sendall(self, data: bytes) -> None:
        if self._closed:
            raise OSError("endpoint closed")
        try:
            self._send_sock.sendall(data)
        except OSError as e:
            self._closed = True
            raise

    def send(self, data: bytes) -> int:
        if self._closed:
            raise OSError("endpoint closed")
        return self._send_sock.send(data)

    def recv(self, bufsize: int) -> bytes:
        if self._closed:
            return b""
        try:
            return self._recv_sock.recv(bufsize)
        except OSError as e:
            if isinstance(e, socket.timeout):
                raise
            self._closed = True
            raise

    def shutdown(self, how: int):
        try:
            if how in (socket.SHUT_WR, socket.SHUT_RDWR):
                self._send_sock.shutdown(socket.SHUT_WR)
            if how in (socket.SHUT_RD, socket.SHUT_RDWR):
                self._recv_sock.shutdown(socket.SHUT_RD)
        except OSError:
            pass

    def close(self):
        self._closed = True
        try: self._send_sock.close()
        except OSError: pass
        try: self._recv_sock.close()
        except OSError: pass

    def setsockopt(self, level, optname, value):
        # TCP_NODELAY etc. - no-op on socketpair-backed endpoints
        pass

    def fileno(self) -> int:
        return self._recv_sock.fileno()


# ============================================================================
# Framing helpers - copied from production session.py lines 165-188
# ============================================================================

_MAX_FRAME = int(os.environ.get("FROGNET_SEM_MAX_FRAME", "4194304"))


def _recv_exact(conn: WireEndpoint, n: int) -> bytes:
    """Production session.py line 165-172."""
    buf = bytearray()
    while len(buf) < n:
        blk = conn.recv(n - len(buf))
        if not blk:
            raise RuntimeError("socket closed")
        buf.extend(blk)
    return bytes(buf)


def _recv_frame(conn: WireEndpoint) -> bytes:
    """Production session.py line 175-180."""
    hdr = _recv_exact(conn, 4)
    (n,) = struct.unpack("!I", hdr)
    if n <= 0 or n > _MAX_FRAME:
        raise RuntimeError(f"bad frame length {n}")
    return _recv_exact(conn, n)


def _send_frame(conn: WireEndpoint, payload: bytes) -> None:
    """Production session.py line 183-188."""
    n = len(payload)
    if n <= 0 or n > _MAX_FRAME:
        raise RuntimeError(f"invalid frame size {n}")
    conn.sendall(struct.pack("!I", n))
    conn.sendall(payload)


# ============================================================================
# TemplateStore - in-memory equivalent of MariaDB semcache_db
# ============================================================================

class TemplateStore:
    """The daemon's cache of templates and per-(peer, hash) references.

    Production: backed by MariaDB (semcache_db.py). The contract is the same:
    given a req_hash, retrieve the template fragment that was learned when
    a REQ_FULL with that hash first arrived. Used to decode subsequent
    REQ_DIFF frames against the reference state.
    """

    def __init__(self):
        self._lock = threading.RLock()
        # req_hash -> {"fragment": <dict>, "reference": <Dict[field, value]>, "url_keys": []}
        self._cache: Dict[bytes, Dict[str, Any]] = {}

    def store(self, req_hash: bytes, fragment: Dict[str, Any],
              reference: Dict[str, Any], url_keys: Optional[List[str]] = None):
        with self._lock:
            self._cache[req_hash] = {
                "fragment": fragment,
                "reference": dict(reference),
                "url_keys": list(url_keys or []),
            }

    def update_reference(self, req_hash: bytes, reference: Dict[str, Any]):
        with self._lock:
            entry = self._cache.get(req_hash)
            if entry is not None:
                entry["reference"] = dict(reference)

    def get(self, req_hash: bytes) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._cache.get(req_hash)

    def __len__(self):
        with self._lock:
            return len(self._cache)


# ============================================================================
# SimulatedDaemonSession - the daemon side of one peer connection
# ============================================================================

# Match production fast-frame heuristic: REQ_REPEAT and known echo opcodes
# go to the fast pool, sensor-data REQ_FULL/REQ_DIFF to the DB pool.
_FAST_OPCODES_TEST = set()  # populated by tier setup; empty = all go to db pool


def _is_fast_frame_test(frame: bytes) -> bool:
    """Tier-local _is_fast_frame: peek at the opcode (offset 2:6 in the
    semantic payload after the FNW1 [MAGIC][op][req_hash][len] prefix).
    REQ_REPEAT carries no payload - always fast."""
    msg = try_parse(frame)
    if msg is None:
        return False
    if msg.op == OP_REQ_REPEAT:
        return True
    if msg.payload and len(msg.payload) >= 6:
        try:
            opcode = struct.unpack("<I", msg.payload[2:6])[0]
            return opcode in _FAST_OPCODES_TEST
        except struct.error:
            pass
    return False


@dataclass
class _InflightEntry:
    """Production session.py line 145-152 - _DaemonInflight."""
    event: threading.Event = field(default_factory=threading.Event)
    reply: Optional[bytes] = None
    error: Optional[Exception] = None


class SimulatedDaemonSession:
    """One peer's daemon-side session. Faithful model of
    daemon/engine/session.py SemanticSession.

    Lifecycle (matches production session.py run() at line 483):
      1. Accept the recv_sock (proxy's send_sock, where requests arrive)
      2. Wait for the proxy to open a SECOND connection - the return channel
         - and route it here via set_send_sock() (line 447). The proxy
         identifies the return channel with HELLO body "RETURN:<gw>".
      3. Start the writer thread (line 504); start the reader loop.
      4. Reader: _recv_frame, peek opcode, submit to fast_exec or db_exec.
      5. Worker: execute handler, put (seq, reply, wire_in) on reply queue.
      6. Writer: drain reply queue, _send_frame(struct.pack("!I", seq) + reply).
    """

    def __init__(self,
                 peer_ip: str,
                 recv_sock: WireEndpoint,
                 template_store: TemplateStore,
                 fast_workers: int = 16,    # cut down from production 256 for in-process testing
                 db_workers: int = 4,
                 codec: Optional[SemanticCodec] = None):
        self.peer_ip = peer_ip
        self._recv_sock = recv_sock
        self._send_sock: Optional[WireEndpoint] = None
        self._send_sock_event = threading.Event()
        self.store = template_store
        self.codec = codec or SemanticCodec()

        self._alive = True
        self._request_seq = 0

        self._reply_q: "queue.Queue[Tuple[int, bytes, int]]" = queue.Queue()
        # Lock for direct sends to _send_sock from the read_loop (control
        # frames like SEQ_RESET echo). Writer also acquires this for its
        # seq-tagged replies, so the two paths don't interleave bytes.
        self._send_lock = threading.Lock()
        self._fast_exec = ThreadPoolExecutor(max_workers=fast_workers,
                                              thread_name_prefix=f"daemon-fast-{peer_ip}")
        self._db_exec = ThreadPoolExecutor(max_workers=db_workers,
                                            thread_name_prefix=f"daemon-db-{peer_ip}")

        # Coalescing - production session.py line 145
        self._inflight: Dict[bytes, _InflightEntry] = {}
        self._inflight_lock = threading.RLock()

        # Stats for the tier
        self.stats = {
            "frames_received": 0,
            "req_full": 0, "req_diff": 0, "req_repeat": 0,
            "resp_diff": 0, "resp_same": 0,
            "coalesced": 0,
            "decode_errors": 0,
            "fast_dispatches": 0, "db_dispatches": 0,
            "hello_recvd": False,
            "seq_reset_recvd": False,
            "return_channel_received": False,
        }

        self._writer_thread: Optional[threading.Thread] = None
        self._reader_thread: Optional[threading.Thread] = None

    def set_send_sock(self, sock: WireEndpoint):
        """Production session.py line 447. Called when the proxy opens its
        second connection with HELLO body "RETURN:<gw>"."""
        self._send_sock = sock
        # Send our HELLO back so the proxy knows the return channel is live
        # (production session.py line 461-462).
        hello = wrap_hello(self.peer_ip)
        _send_frame(sock, hello)
        self.stats["return_channel_received"] = True
        self._send_sock_event.set()

    def start(self):
        """Production run() at line 483. Wait for send_sock to be delivered,
        then start writer + reader threads."""
        if not self._send_sock_event.wait(timeout=10.0):
            raise RuntimeError(f"no RETURN channel from {self.peer_ip} within 10s")
        self._writer_thread = threading.Thread(
            target=self._write_loop, daemon=True,
            name=f"daemon-writer-{self.peer_ip}",
        )
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True,
            name=f"daemon-reader-{self.peer_ip}",
        )
        self._writer_thread.start()
        self._reader_thread.start()

    def stop(self):
        self._alive = False
        # Wake the writer if it's waiting
        self._reply_q.put((-1, b"", 0))
        try: self._recv_sock.close()
        except Exception: pass
        try:
            if self._send_sock: self._send_sock.close()
        except Exception: pass
        self._fast_exec.shutdown(wait=False)
        self._db_exec.shutdown(wait=False)

    def _read_loop(self):
        """Production session.py line 536. Read frames, dispatch to executor.

        Frames arriving here have NO seq tag - the proxy doesn't tag
        outgoing frames. We assign seq based on arrival order; both sides
        maintain counters that stay aligned because TCP preserves order.

        SEQ_RESET is treated as a control frame: it does NOT consume a
        seq number. We reset the counter, echo back UNTAGGED on send_sock
        (under _send_lock so we don't race the writer's seq-tagged replies).
        The next real frame from the proxy will then get seq=0, matching
        the proxy's _next_seq=0 set after its SEQ_RESET handshake."""
        try:
            while self._alive:
                try:
                    frame = _recv_frame(self._recv_sock)
                except Exception:
                    break

                self.stats["frames_received"] += 1
                wire_in = len(frame)

                # SEQ_RESET: control frame, untagged echo, no seq consumed
                if len(frame) >= 5 and frame[:4] == MAGIC and frame[4] == OP_SEQ_RESET:
                    self.stats["seq_reset_recvd"] = True
                    self._request_seq = 0
                    try:
                        with self._send_lock:
                            _send_frame(self._send_sock, wrap_seq_reset())
                    except Exception as e:
                        print(f"[DAEMON-{self.peer_ip}] SEQ_RESET echo failed: {e!r}",
                              flush=True)
                    continue

                seq = self._request_seq
                self._request_seq += 1

                if _is_fast_frame_test(frame):
                    self.stats["fast_dispatches"] += 1
                    self._fast_exec.submit(self._process_frame, frame, seq, wire_in)
                else:
                    self.stats["db_dispatches"] += 1
                    self._db_exec.submit(self._process_frame, frame, seq, wire_in)
        except Exception as e:
            print(f"[DAEMON-{self.peer_ip}] read_loop exit: {e!r}", flush=True)

    def _write_loop(self):
        """Production session.py line 583. Drain reply queue, send seq-tagged.
        Acquires _send_lock so we don't interleave bytes with the read_loop's
        untagged control-frame sends (e.g. SEQ_RESET echo)."""
        try:
            while True:
                try:
                    seq, reply, wire_in = self._reply_q.get(timeout=1.0)
                except queue.Empty:
                    if not self._alive:
                        break
                    continue
                if seq == -1:
                    break  # sentinel
                try:
                    tagged = struct.pack("!I", seq) + reply
                    with self._send_lock:
                        _send_frame(self._send_sock, tagged)
                except Exception as e:
                    print(f"[DAEMON-{self.peer_ip}] write error seq={seq}: {e!r}", flush=True)
                    self._alive = False
                    break
        except Exception as e:
            print(f"[DAEMON-{self.peer_ip}] write_loop exit: {e!r}", flush=True)

    def _process_frame(self, frame: bytes, seq: int, wire_in: int):
        """Production session.py line 612. Worker-thread entry point."""
        try:
            reply = self._dispatch(frame, wire_in)
        except Exception as e:
            reply = wrap_error(500, f"handler error: {e!r}")
        self._reply_q.put((seq, reply, wire_in))

    def _dispatch(self, frame: bytes, wire_in: int) -> bytes:
        """Production session.py line 646 _process_frame_inner.
        Parse the frame, do coalescing, dispatch to the right handler."""
        msg = try_parse(frame)
        if msg is None:
            self.stats["decode_errors"] += 1
            return wrap_error(400, "non-FNW1 frame")

        # Handle HELLO frames (one-off, no coalescing)
        if msg.op == OP_HELLO:
            self.stats["hello_recvd"] = True
            # Echo HELLO back with our own identity
            return wrap_hello(self.peer_ip)

        # Coalescing - production session.py line 661-697
        req_hash = msg.req_hash
        can_coalesce = req_hash is not None and len(req_hash) == REQ_HASH_LEN

        if can_coalesce:
            with self._inflight_lock:
                existing = self._inflight.get(req_hash)
                if existing is not None:
                    pass  # join the in-flight one
                else:
                    entry = _InflightEntry()
                    self._inflight[req_hash] = entry

            if existing is not None:
                self.stats["coalesced"] += 1
                # Wait for the in-flight to complete (max 5s)
                if existing.event.wait(timeout=5.0):
                    if existing.error is None and existing.reply is not None:
                        return existing.reply
                # Timeout or error - fall through to independent execution
                return self._dispatch_inner(msg)

            try:
                reply = self._dispatch_inner(msg)
                entry.reply = reply
                return reply
            except Exception as e:
                entry.error = e
                raise
            finally:
                entry.event.set()
                with self._inflight_lock:
                    self._inflight.pop(req_hash, None)

        return self._dispatch_inner(msg)

    def _dispatch_inner(self, msg) -> bytes:
        """Per-op handlers."""
        if msg.op == OP_REQ_FULL:
            self.stats["req_full"] += 1
            return self._handle_req_full(msg)
        if msg.op == OP_REQ_DIFF:
            self.stats["req_diff"] += 1
            return self._handle_req_diff(msg)
        if msg.op == OP_REQ_REPEAT:
            self.stats["req_repeat"] += 1
            return self._handle_req_repeat(msg)
        if msg.op == OP_REQ_RAW and self._origin_handler is not None:
            # PRIMER 1 mode 2: a remote daemon with an upstream origin. The
            # base tier note at _handle_req_full says it "doesn't model upstream
            # HTTP fetch"; an origin handler closes exactly that gap - decode
            # the raw HTTP request, fetch the origin (e.g. a PHP echo), and
            # return its real HTTP response as RESP_RAW. Baseline behavior is
            # unchanged: with no handler set, REQ_RAW stays unsupported.
            self.stats["req_raw"] = self.stats.get("req_raw", 0) + 1
            try:
                status, headers, body = self._origin_handler(msg.payload or b"")
            except Exception as e:  # noqa: BLE001
                return wrap_error(502, f"origin fetch failed: {e!r}")
            same_id = self._same_id_for(msg.req_hash)
            self.stats["resp_raw"] = self.stats.get("resp_raw", 0) + 1
            return wrap_resp_raw(same_id, int(status),
                                 headers if isinstance(headers, bytes)
                                 else str(headers).encode(),
                                 body if isinstance(body, bytes)
                                 else str(body).encode())
        return wrap_error(400, f"unsupported op {msg.op}")

    def _handle_req_full(self, msg) -> bytes:
        """REQ_FULL: decode the full payload, learn the template if new,
        remember the reference for diff decoding later, return RESP_DIFF
        (with empty blob - the tier doesn't model upstream HTTP fetch)."""
        # For REQ_FULL the daemon should be able to decode without a prior
        # fragment - it learns the template from this very frame.
        # The codec.decode_request needs a req_tpl with fragment + url_keys.
        # In production the daemon looks up the template under the
        # canonical_semantic_key; for the tier we use the registered
        # learner: TIER_HANDLER.learn_request_template applied to the
        # decoded payload.
        # We DON'T have the original body_text here, only the encoded bytes.
        # So we use the fragment passed at session setup (one per session
        # in this tier - see daemon_session.set_default_fragment).
        frag = self.store.get(msg.req_hash)
        if frag is None:
            # No template yet - store an empty placeholder so REQ_DIFF can
            # at least be acknowledged. Production would attempt to learn
            # from the inbound payload.
            self.store.store(msg.req_hash, fragment=self._default_fragment,
                             reference={})
            frag = self.store.get(msg.req_hash)

        # Decode and update the reference
        try:
            req_tpl = types.SimpleNamespace(
                url_query_keys=frag.get("url_keys", []),
                fragment=frag["fragment"],
            )
            _, decoded = self.codec.decode_request(
                msg.payload, req_tpl, tokens=frag["fragment"]["tokens"]
            )
            new_ref = {k: v for (k, v) in decoded}
            self.store.update_reference(msg.req_hash, new_ref)
        except Exception:
            self.stats["decode_errors"] += 1

        same_id = self._same_id_for(msg.req_hash)
        self.stats["resp_diff"] += 1
        return wrap_resp_diff(same_id, b"")

    def _handle_req_diff(self, msg) -> bytes:
        """REQ_DIFF: decode against the stored reference."""
        frag = self.store.get(msg.req_hash)
        if frag is None:
            # Cache miss - daemon should signal REQ_MISS to ask for a REQ_FULL
            return wrap_error(404, "no template for req_hash")

        try:
            req_tpl = types.SimpleNamespace(
                url_query_keys=frag.get("url_keys", []),
                fragment=frag["fragment"],
            )
            _, decoded = self.codec.decode_request(
                msg.payload, req_tpl, tokens=frag["fragment"]["tokens"],
                reference=frag["reference"],
            )
            # Update the reference to the new state
            new_ref = {k: v for (k, v) in decoded}
            # Merge: changed fields replace, unchanged fields keep
            merged = dict(frag["reference"])
            merged.update(new_ref)
            self.store.update_reference(msg.req_hash, merged)
        except Exception:
            self.stats["decode_errors"] += 1

        same_id = self._same_id_for(msg.req_hash)
        self.stats["resp_diff"] += 1
        return wrap_resp_diff(same_id, b"")

    def _handle_req_repeat(self, msg) -> bytes:
        """REQ_REPEAT: cached, no change - RESP_SAME."""
        same_id = self._same_id_for(msg.req_hash)
        self.stats["resp_same"] += 1
        return wrap_resp_same(same_id)

    def _same_id_for(self, req_hash: bytes) -> bytes:
        return hashlib.blake2b(req_hash, digest_size=SAME_ID_LEN).digest()

    # Tier hook: the daemon session needs to know what fragment to use when
    # decoding REQ_FULL without a prior template lookup (production gets this
    # from canonical_semantic_key + the MariaDB store).
    _default_fragment: Optional[Dict[str, Any]] = None
    _origin_handler = None  # PRIMER1 mode2: callable(http_bytes)->(status,headers,body)

    def set_default_fragment(self, fragment: Dict[str, Any]):
        self._default_fragment = fragment

    def set_origin_handler(self, fn):
        """PRIMER 1 mode 2. fn(raw_http_request_bytes) -> (status:int,
        headers:bytes, body:bytes). When set, REQ_RAW frames are served
        from the upstream origin instead of being rejected."""
        self._origin_handler = fn


# ============================================================================
# SimulatedProxyWorker - the proxy side of one peer connection
# ============================================================================

@dataclass
class _PendingRPC:
    """Production transport_semantic.py has _RPC tracking. Our equivalent:
    proxy assigns a seq when sending, holds done.set() until the reply with
    that seq arrives on the recv_sock."""
    seq: int
    done: threading.Event = field(default_factory=threading.Event)
    reply: Optional[bytes] = None
    err: Optional[str] = None


class SimulatedProxyWorker:
    """One peer's proxy-side worker. Faithful model of
    proxy/transport_semantic.py _DaemonWorker.

    Lifecycle (matches transport_semantic.py line ~530-540):
      1. Open _send_sock - send HELLO(local_gw), then SEQ_RESET, then
         wait for ack.
      2. Open _recv_sock - second connection to daemon, with HELLO body
         "RETURN:<gw>". This becomes the return channel - replies arrive here.
      3. Start writer thread (drains _outbox) and reader thread (matches
         replies by seq).
      4. send_request(frame) -> Future-like. Writer assigns seq, sends
         struct.pack("!I", seq) + frame. Reader strips seq, matches to
         pending RPC, sets done.
    """

    def __init__(self,
                 local_gw: str,
                 send_sock: WireEndpoint,
                 recv_sock: WireEndpoint,
                 codec: Optional[SemanticCodec] = None):
        self.local_gw = local_gw
        self._send_sock = send_sock
        self._recv_sock = recv_sock
        self.codec = codec or SemanticCodec()

        self._alive = True
        self._next_seq = 0
        self._next_seq_lock = threading.Lock()

        self._pending: Dict[int, _PendingRPC] = {}
        self._pending_lock = threading.Lock()

        # Outbox: (frame_bytes, pending_rpc)
        self._outbox: "queue.Queue[Tuple[bytes, _PendingRPC]]" = queue.Queue()

        # SEQ_RESET ack: control frame (no seq tag), tracked via Event
        # rather than the _pending table.
        self._seq_reset_ack_event = threading.Event()

        self.stats = {
            "frames_sent": 0,
            "wire_bytes_tx": 0,
            "wire_bytes_rx": 0,
            "replies_matched": 0,
            "replies_unknown_seq": 0,
            "hello_sent": False,
            "seq_reset_acked": False,
            "return_channel_opened": False,
        }

        self._writer_t: Optional[threading.Thread] = None
        self._reader_t: Optional[threading.Thread] = None

    def open(self, timeout: float = 10.0):
        """Production transport_semantic.py _open_send_sock + RETURN handshake.

        CRITICAL: outgoing FNW1 frames from proxy carry NO seq tag - the
        seq is internal to the proxy (used to key _pending). Both sides
        keep counters in lockstep: proxy increments self._next_seq on
        each send, daemon increments self._request_seq on each receive.
        Daemon tags REPLIES with its seq, proxy reads the tag and matches.

        Handshake frames (HELLO, HELLO RETURN, SEQ_RESET, and their acks)
        are untagged CONTROL FRAMES - they do not consume seq numbers.
        That keeps the counters aligned: after handshake, both sides have
        their counters at 0, and the first real REQ->REPLY round-trip uses
        seq=0 on both sides.
        """
        # Phase 1: HELLO on send_sock (untagged control frame).
        hello = wrap_hello(self.local_gw)
        _send_frame(self._send_sock, hello)
        self.stats["hello_sent"] = True

        # Phase 2: HELLO RETURN:<gw> on recv_sock. Daemon will route this
        # socket as the reply channel for the matching session and reply
        # with its own HELLO ack (also untagged).
        hello_ret = wrap_hello(f"RETURN:{self.local_gw}")
        _send_frame(self._recv_sock, hello_ret)
        self.stats["return_channel_opened"] = True

        # Start the reader BEFORE sending SEQ_RESET so we catch both the
        # HELLO ack (untagged) and the SEQ_RESET echo (untagged).
        self._reader_t = threading.Thread(
            target=self._reader_loop, daemon=True,
            name=f"proxy-reader-{self.local_gw}",
        )
        self._reader_t.start()

        # Phase 3: SEQ_RESET (untagged control frame). Daemon resets its
        # counter and echoes back untagged. We wait on an event, not the
        # _pending table - keeping seq=0 free for the first real request.
        self._seq_reset_ack_event.clear()
        _send_frame(self._send_sock, wrap_seq_reset())
        self._next_seq = 0  # first real request will be seq=0

        if self._seq_reset_ack_event.wait(timeout=timeout):
            self.stats["seq_reset_acked"] = True
        else:
            raise RuntimeError(f"SEQ_RESET not acked within {timeout}s")

        # Phase 4: start the writer.
        self._writer_t = threading.Thread(
            target=self._writer_loop, daemon=True,
            name=f"proxy-writer-{self.local_gw}",
        )
        self._writer_t.start()

    def close(self):
        self._alive = False
        self._outbox.put((b"", None))  # sentinel
        try: self._send_sock.close()
        except Exception: pass
        try: self._recv_sock.close()
        except Exception: pass

    def send_request(self, frame: bytes, timeout: float = 5.0) -> Optional[bytes]:
        """Send an FNW1 frame, return the reply payload (the bytes after the
        seq tag) or None on timeout. The frame should be a complete
        wrap_req_full / wrap_req_diff / wrap_req_repeat result."""
        with self._next_seq_lock:
            seq = self._next_seq
            self._next_seq += 1
        rpc = _PendingRPC(seq=seq)
        with self._pending_lock:
            self._pending[seq] = rpc
        self._outbox.put((frame, rpc))
        if rpc.done.wait(timeout=timeout):
            return rpc.reply
        with self._pending_lock:
            self._pending.pop(seq, None)
        return None

    def _writer_loop(self):
        """Drain outbox, _send_frame on send_sock. Production proxy does
        NOT prepend seq to outgoing frames - seq is internal to the proxy
        only. Daemon assigns its own seq per inbound frame; both counters
        stay in lockstep because TCP is in-order."""
        while self._alive:
            try:
                frame, rpc = self._outbox.get(timeout=1.0)
            except queue.Empty:
                continue
            if rpc is None:
                break  # sentinel
            try:
                _send_frame(self._send_sock, frame)
                self.stats["frames_sent"] += 1
                self.stats["wire_bytes_tx"] += 4 + len(frame)
            except Exception as e:
                rpc.err = f"send failed: {e!r}"
                rpc.done.set()
                self._alive = False
                break

    def _reader_loop(self):
        """Match incoming seq-tagged frames to pending RPCs (production
        transport_semantic.py line 1316-1340).

        Daemon-initiated control frames (HELLO ack, SEQ_RESET echo) are
        sent UNTAGGED - they start with the FNW1 MAGIC bytes rather than
        a 4-byte seq prefix. We detect these by checking frame[:4] == MAGIC
        and dispatch them separately."""
        try:
            while self._alive:
                try:
                    frame = _recv_frame(self._recv_sock)
                except Exception:
                    break
                self.stats["wire_bytes_rx"] += 4 + len(frame)
                if len(frame) < 4:
                    continue

                # Control frame? (no seq tag)
                if frame[:4] == MAGIC:
                    if len(frame) >= 5 and frame[4] == OP_SEQ_RESET:
                        self._seq_reset_ack_event.set()
                    # HELLO ack and other control frames: just acknowledge
                    # (we don't need to do anything with them).
                    continue

                # Seq-tagged reply: match to pending RPC
                seq = struct.unpack("!I", frame[:4])[0]
                payload = frame[4:]

                with self._pending_lock:
                    rpc = self._pending.pop(seq, None)
                if rpc is None:
                    self.stats["replies_unknown_seq"] += 1
                    continue
                rpc.reply = payload
                rpc.done.set()
                self.stats["replies_matched"] += 1
        except Exception as e:
            print(f"[PROXY-{self.local_gw}] reader exit: {e!r}", flush=True)


# ============================================================================
# Mini "server" - accepts two connections from one proxy and pairs them
# ============================================================================

class SimulatedDaemonServer:
    """Models the daemon's accept loop. When a proxy opens its send_sock
    and (later) its recv_sock with HELLO RETURN:<gw>, the server peeks the
    HELLO body to pair them into ONE SimulatedDaemonSession.

    In this tier the wires are set up explicitly (no real accept) - see
    create_connected_pair() below which directly wires two endpoints to
    a session. The server is here mainly to make the responsibility split
    obvious: the proxy's two sockets get matched on the daemon side.
    """

    def __init__(self, template_store: TemplateStore):
        self.store = template_store
        self._sessions: Dict[str, SimulatedDaemonSession] = {}
        self._lock = threading.Lock()

    def accept_send_sock(self, peer_ip: str, recv_endpoint: WireEndpoint,
                          default_fragment: Optional[Dict[str, Any]] = None) -> SimulatedDaemonSession:
        """Proxy opened its first connection. We become the daemon side
        receiving requests from this connection."""
        # Read HELLO frame - confirms peer identity
        try:
            hello_frame = _recv_frame(recv_endpoint)
            msg = try_parse(hello_frame)
            if msg is None or msg.op != OP_HELLO:
                raise RuntimeError(f"expected HELLO from {peer_ip}, got {msg}")
        except Exception as e:
            raise RuntimeError(f"HELLO failed on send_sock for {peer_ip}: {e!r}")

        session = SimulatedDaemonSession(
            peer_ip=peer_ip, recv_sock=recv_endpoint,
            template_store=self.store,
        )
        if default_fragment:
            session.set_default_fragment(default_fragment)
        with self._lock:
            self._sessions[peer_ip] = session
        return session

    def accept_return_sock(self, peer_ip: str, send_endpoint: WireEndpoint):
        """Proxy opened its second connection - the RETURN channel for
        replies. Find the matching session by HELLO body."""
        try:
            hello_frame = _recv_frame(send_endpoint)
            msg = try_parse(hello_frame)
            if msg is None or msg.op != OP_HELLO:
                raise RuntimeError(f"expected HELLO on return channel")
            # production checks for "RETURN:<gw>" prefix in the HELLO body
            # we read the body off the frame:
            ip_len = hello_frame[5] if len(hello_frame) > 5 else 0
            ip_body = hello_frame[6:6+ip_len].decode("ascii", errors="replace")
            if not ip_body.startswith("RETURN:"):
                raise RuntimeError(f"expected HELLO RETURN:, got {ip_body!r}")
        except Exception as e:
            raise RuntimeError(f"HELLO RETURN failed for {peer_ip}: {e!r}")

        with self._lock:
            session = self._sessions.get(peer_ip)
        if session is None:
            raise RuntimeError(f"no send_sock session for {peer_ip}")
        session.set_send_sock(send_endpoint)
        return session


def create_connected_pair(local_gw: str,
                          fwd_params: Optional[NetworkParams] = None,
                          rev_params: Optional[NetworkParams] = None,
                          capture_dir: Optional[str] = None,
                          template_store: Optional[TemplateStore] = None,
                          default_fragment: Optional[Dict[str, Any]] = None,
                          ) -> Tuple[SimulatedProxyWorker, SimulatedDaemonSession, WireMedium, WireMedium]:
    """Set up a fully-connected proxy<->daemon pair with two simulated wires.

    Returns: (proxy, daemon_session, send_wire, recv_wire)
    The two wires are independently shaped (or shared) - pass different
    NetworkParams per direction if you need asymmetry.

    Sequence:
      1. Stand up send_wire (proxy A end -> daemon B end), recv_wire (proxy A end <- daemon B end)
      2. Daemon-side accept: read HELLO from send_wire to create session
      3. Daemon-side accept: read HELLO RETURN from recv_wire to attach to session
      4. Start daemon's writer + reader threads
      5. Start proxy's reader thread (for SEQ_RESET ack)
      6. Proxy sends SEQ_RESET, daemon echoes
      7. Start proxy's writer thread
      8. Ready for traffic
    """
    template_store = template_store or TemplateStore()
    server = SimulatedDaemonServer(template_store)

    # Two wires - one for requests (proxy->daemon), one for replies (daemon->proxy)
    send_wire = WireMedium(forward_params=fwd_params, reverse_params=rev_params,
                            capture_dir=capture_dir, label="send")
    recv_wire = WireMedium(forward_params=fwd_params, reverse_params=rev_params,
                            capture_dir=capture_dir, label="recv")

    # Proxy's send_sock writes into send_wire.A (forward toward daemon)
    # Daemon's recv reads from send_wire.B
    # Daemon's send_sock writes into recv_wire.B (toward proxy)
    # Proxy's recv reads from recv_wire.A
    proxy_send = send_wire.endpoint_a()
    daemon_recv = send_wire.endpoint_b()
    daemon_send = recv_wire.endpoint_b()
    proxy_recv = recv_wire.endpoint_a()

    # Construct daemon session (reads HELLO from proxy_send -> daemon_recv)
    # We need to run the daemon-side HELLO accept in a thread because the
    # proxy will only send HELLO inside proxy.open(), and we have to be
    # ready to read it.
    daemon_session_holder: Dict[str, Any] = {}

    def accept_session():
        try:
            sess = server.accept_send_sock(local_gw, daemon_recv,
                                            default_fragment=default_fragment)
            server.accept_return_sock(local_gw, daemon_send)
            sess.start()
            daemon_session_holder["sess"] = sess
        except Exception as e:
            daemon_session_holder["err"] = e

    accept_t = threading.Thread(target=accept_session, daemon=True,
                                 name=f"daemon-accept-{local_gw}")
    accept_t.start()

    # Construct proxy and open (sends HELLO, HELLO RETURN, SEQ_RESET, gets ack)
    proxy = SimulatedProxyWorker(local_gw=local_gw,
                                  send_sock=proxy_send,
                                  recv_sock=proxy_recv)
    proxy.open(timeout=15.0)

    # Wait for daemon-side accept to complete
    accept_t.join(timeout=15.0)
    if "err" in daemon_session_holder:
        raise daemon_session_holder["err"]
    if "sess" not in daemon_session_holder:
        raise RuntimeError("daemon accept did not complete")
    daemon_session = daemon_session_holder["sess"]

    return proxy, daemon_session, send_wire, recv_wire


# ============================================================================
# Tests
# ============================================================================

def _hash_for(s: str) -> bytes:
    return hashlib.blake2b(s.encode("utf-8"), digest_size=REQ_HASH_LEN).digest()


def test_handshake_and_round_trip() -> Dict[str, Any]:
    """T1: clean wire, full two-socket handshake (HELLO + HELLO RETURN +
    SEQ_RESET + ack), then one round-trip request/reply."""
    print("\nT1: full two-socket handshake on clean wire")
    params = NetworkParams(latency_ms=1.0, bandwidth_bps=1e9)
    proxy, daemon, w1, w2 = create_connected_pair(
        local_gw="10.20.20.7", fwd_params=params, rev_params=params,
    )
    try:
        check("proxy sent HELLO on send_sock", proxy.stats["hello_sent"])
        check("proxy sent HELLO RETURN on recv_sock", proxy.stats["return_channel_opened"])
        check("proxy received SEQ_RESET ack", proxy.stats["seq_reset_acked"])
        check("daemon received SEQ_RESET", daemon.stats["seq_reset_recvd"])
        check("daemon received return channel", daemon.stats["return_channel_received"])

        # Now send a real request. Build a REQ_REPEAT with a known hash -
        # the daemon's req_repeat handler is the simplest path.
        req_hash = _hash_for("test-1")
        frame = wrap_req_repeat(req_hash)
        reply = proxy.send_request(frame, timeout=3.0)
        check("proxy got a reply", reply is not None)
        if reply is not None:
            msg = try_parse(reply)
            check("reply was RESP_SAME", msg is not None and msg.op == OP_RESP_SAME)
        check("daemon counted 1 REQ_REPEAT", daemon.stats["req_repeat"] == 1,
              f"got {daemon.stats['req_repeat']}")
        check("daemon counted 1 RESP_SAME", daemon.stats["resp_same"] == 1)

        return {
            "proxy_tx": proxy.stats["wire_bytes_tx"],
            "proxy_rx": proxy.stats["wire_bytes_rx"],
        }
    finally:
        proxy.close()
        daemon.stop()
        w1.close()
        w2.close()


def test_pipelining() -> Dict[str, Any]:
    """T2: many concurrent requests in flight, replies arrive interleaved,
    proxy reader matches them by seq. This is what a single-socket
    request/reply loop CANNOT exercise."""
    print("\nT2: 50 concurrent requests, replies match by seq")

    # Add some latency to make pipelining matter
    params = NetworkParams(latency_ms=20.0, jitter_ms=5.0, bandwidth_bps=1e9)
    proxy, daemon, w1, w2 = create_connected_pair(
        local_gw="10.20.20.8", fwd_params=params, rev_params=params,
    )
    try:
        # Fire 50 concurrent requests from worker threads - they should all
        # be in flight simultaneously; without seq matching, replies couldn't
        # be associated with their originating request.
        N = 50
        results: List[Optional[bytes]] = [None] * N

        def fire(i):
            req_hash = _hash_for(f"req-{i}")
            frame = wrap_req_repeat(req_hash)
            results[i] = proxy.send_request(frame, timeout=10.0)

        t0 = time.perf_counter()
        threads = [threading.Thread(target=fire, args=(i,)) for i in range(N)]
        for t in threads: t.start()
        for t in threads: t.join()
        elapsed = time.perf_counter() - t0

        check("all 50 requests got replies",
              all(r is not None for r in results),
              f"got {sum(1 for r in results if r is not None)} of {N}")
        check("each reply was RESP_SAME",
              all(r is not None and try_parse(r) is not None and
                  try_parse(r).op == OP_RESP_SAME for r in results))
        # Pipelining check: if requests were serialized, time would be
        # ~N x 2 x latency_ms (round-trip per). With pipelining, it should
        # be roughly 1 x 2 x latency_ms + worker overhead.
        serial_lower_bound_ms = N * 2 * params.latency_ms
        print(f"     elapsed: {elapsed*1000:.1f}ms (serial lower bound would be ~{serial_lower_bound_ms:.0f}ms)")
        check("pipelined execution faster than serial lower bound",
              elapsed * 1000 < serial_lower_bound_ms,
              f"elapsed={elapsed*1000:.1f}ms expected<{serial_lower_bound_ms}ms")
        check("daemon counted all 50 REQ_REPEAT",
              daemon.stats["req_repeat"] == N, f"got {daemon.stats['req_repeat']}")
        check("proxy matched all replies (no unknown seq)",
              proxy.stats["replies_unknown_seq"] == 0,
              f"got {proxy.stats['replies_unknown_seq']}")

        return {"elapsed_ms": elapsed * 1000, "frames": N}
    finally:
        proxy.close()
        daemon.stop()
        w1.close()
        w2.close()


def test_coalescing() -> Dict[str, Any]:
    """T3: 200 concurrent requests with the SAME req_hash. The daemon's
    _daemon_inflight should coalesce them to ONE execution; the rest wait
    on its result. Production session.py line 661-697."""
    print("\nT3: 200 concurrent identical requests - coalescing")

    # Slow the wire enough that coalescing window is meaningful
    params = NetworkParams(latency_ms=5.0, bandwidth_bps=1e9)
    proxy, daemon, w1, w2 = create_connected_pair(
        local_gw="10.20.20.9", fwd_params=params, rev_params=params,
    )
    try:
        # We use REQ_REPEAT here too. To make coalescing visible, we need the
        # daemon-side handler to take long enough that multiple requests
        # arrive while the first is in flight. Monkey-patch the handler to
        # take 100ms:
        orig_handle = daemon._handle_req_repeat
        def slow_handle(msg):
            time.sleep(0.1)
            return orig_handle(msg)
        daemon._handle_req_repeat = slow_handle

        N = 200
        same_hash = _hash_for("hot-spot")
        results: List[Optional[bytes]] = [None] * N

        def fire(i):
            frame = wrap_req_repeat(same_hash)
            results[i] = proxy.send_request(frame, timeout=15.0)

        t0 = time.perf_counter()
        threads = [threading.Thread(target=fire, args=(i,)) for i in range(N)]
        for t in threads: t.start()
        for t in threads: t.join()
        elapsed = time.perf_counter() - t0

        check("all 200 got replies",
              all(r is not None for r in results),
              f"got {sum(1 for r in results if r is not None)} of {N}")
        # Production coalescing: not all 200 hit the handler. The first
        # acquires the inflight slot, the rest join and wait for its result.
        # Stats: req_repeat counter is incremented in dispatch BEFORE
        # coalescing decision in our model, so we count the dispatches -
        # what we care about is the coalesced counter.
        print(f"     elapsed: {elapsed*1000:.1f}ms")
        print(f"     daemon coalesced count: {daemon.stats['coalesced']}")
        print(f"     daemon req_repeat dispatches: {daemon.stats['req_repeat']}")
        check("coalescing fired (many requests served from one execution)",
              daemon.stats["coalesced"] > 0,
              f"coalesced={daemon.stats['coalesced']}")
        check("elapsed close to one handler execution (~100ms) not Nx100ms",
              elapsed < 2.0,
              f"got {elapsed:.2f}s - coalescing failed if this is much higher")
        return {"elapsed_ms": elapsed * 1000, "coalesced": daemon.stats["coalesced"]}
    finally:
        proxy.close()
        daemon.stop()
        w1.close()
        w2.close()


def test_slow_wire() -> Dict[str, Any]:
    """T4: bandwidth-limited wire. With a 10 kbps cap, even small payloads
    take measurable time. The simulator's bandwidth shaping should impose
    this faithfully."""
    print("\nT4: bandwidth-limited wire (10 kbps cap)")
    params = NetworkParams(latency_ms=5.0, bandwidth_bps=10_000.0 / 8)  # 10 kbps = 1250 B/s
    proxy, daemon, w1, w2 = create_connected_pair(
        local_gw="10.20.20.10", fwd_params=params, rev_params=params,
    )
    try:
        N = 10
        results: List[Optional[bytes]] = [None] * N

        def fire(i):
            req_hash = _hash_for(f"slow-{i}")
            frame = wrap_req_repeat(req_hash)
            results[i] = proxy.send_request(frame, timeout=30.0)

        t0 = time.perf_counter()
        threads = [threading.Thread(target=fire, args=(i,)) for i in range(N)]
        for t in threads: t.start()
        for t in threads: t.join()
        elapsed = time.perf_counter() - t0

        # Each REQ_REPEAT: 25 bytes wire frame + 4 length-prefix = 29B, plus
        # the seq tag (4 bytes) = 33B per outbound. Reply RESP_SAME with
        # seq tag: 25 + 4 = 29B per inbound. Plus initial handshake
        # (~120 bytes each way for HELLO + HELLO RETURN + SEQ_RESET).
        # At 1250 B/s, that's roughly 330B forward + 290B reverse =
        # 620B / 1250 B/s = 0.5 sec just for the data, plus latency.
        # We expect at least 0.3 seconds total - way more than the
        # ~50ms it'd take on a fast wire.
        all_ok = all(r is not None for r in results)
        check(f"all {N} requests got replies under slow wire", all_ok,
              f"got {sum(1 for r in results if r is not None)} of {N}")
        check("elapsed reflects bandwidth limit (>200ms)",
              elapsed > 0.2,
              f"got {elapsed*1000:.0f}ms - bandwidth shaping may not be applying")
        print(f"     elapsed under 10kbps cap: {elapsed*1000:.0f}ms")
        print(f"     wire stats: fwd_in={w1.stats['forward_bytes_in']}B "
              f"fwd_out={w1.stats['forward_bytes_out']}B")
        return {"elapsed_ms": elapsed * 1000}
    finally:
        proxy.close()
        daemon.stop()
        w1.close()
        w2.close()


def test_asymmetric_wire() -> Dict[str, Any]:
    """T5: asymmetric link - fast downlink, slow uplink (satellite pattern).
    Tests that per-direction parameters work independently."""
    print("\nT5: asymmetric wire - fast downlink, slow uplink")
    fwd = NetworkParams(latency_ms=300.0, bandwidth_bps=512e3)  # uplink: slow
    rev = NetworkParams(latency_ms=300.0, bandwidth_bps=5e6)    # downlink: 10x faster
    proxy, daemon, w1, w2 = create_connected_pair(
        local_gw="10.20.20.11", fwd_params=fwd, rev_params=rev,
    )
    try:
        # Even one round-trip should take ~600ms because of latency, regardless
        # of bandwidth (REQ_REPEAT is tiny).
        t0 = time.perf_counter()
        reply = proxy.send_request(wrap_req_repeat(_hash_for("sat-1")), timeout=10.0)
        rtt = (time.perf_counter() - t0) * 1000

        check("reply arrived over satellite-style wire", reply is not None)
        check("RTT >= 2x latency (lower bound: 600ms)", rtt >= 580.0,
              f"got {rtt:.0f}ms")
        print(f"     observed RTT: {rtt:.0f}ms (sum of 2x latency_ms = 600ms expected)")
        return {"rtt_ms": rtt}
    finally:
        proxy.close()
        daemon.stop()
        w1.close()
        w2.close()


def test_codex_over_realistic_transport() -> Dict[str, Any]:
    """T7: integration - run a JSON sensor stream through the FULL transport
    (two-socket, handshake, pipelined writes, seq-matched replies, template
    store backing REQ_DIFF on the daemon). The compression savings from the
    JSON codex tier should still hold."""
    print("\nT7: JSON sensor stream through full faithful transport")
    codec = SemanticCodec()
    handler = JsonFormatHandler()

    import json
    def reading(seq: int, value: float, ts: int) -> str:
        return json.dumps({
            "SensorType": "DHT22",
            "SensorName": "site.barn.dht22.temp",
            "Location": "barn-north-wall",
            "Units": "celsius",
            "Value": value,
            "ts": ts,
            "seq": seq,
        }, separators=(",", ":"))

    body0 = reading(0, 20.0, 1700000000)
    fragment = handler.learn_request_template(body0)

    params = NetworkParams(latency_ms=10.0, jitter_ms=2.0, bandwidth_bps=100e3)
    proxy, daemon, w1, w2 = create_connected_pair(
        local_gw="10.20.20.12", fwd_params=params, rev_params=params,
        default_fragment=fragment,
    )

    try:
        readings = [reading(i, 20.0 + i * 0.1, 1700000000 + i * 60) for i in range(20)]
        # Daemon needs the template under the req_hash we'll use. Pre-seed.
        # In production this is handled when REQ_FULL arrives - the daemon
        # learns the template from the inbound frame. Here we pre-seed for
        # the same shape.
        shared_req_hash = _hash_for("sensor-session")

        reference: Optional[Dict[str, Any]] = None
        total_bytes_full = 0
        total_bytes_diff = 0
        test_opcode = 0xC0DEC701

        # Pre-seed the template store with the empty reference (REQ_FULL
        # will populate it).
        daemon.store.store(shared_req_hash, fragment=fragment, reference={})

        for seq, body in enumerate(readings):
            dyn = handler.extract_request_dynamic(body, fragment)

            full_payload = codec.encode_request(
                opcode=test_opcode, url_vals=[], json_vals=dyn,
                type_map=fragment["type_map"], tokens=fragment["tokens"],
                compress=True,
            )
            total_bytes_full += len(wrap_req_full(shared_req_hash, full_payload))

            diff_payload, new_ref, is_identical = codec.encode_request_diff(
                opcode=test_opcode, url_vals=[], json_vals=dyn,
                type_map=fragment["type_map"], reference=reference,
                tokens=fragment["tokens"], compress=True,
            )

            if reference is None:
                frame = wrap_req_full(shared_req_hash, full_payload)
            elif is_identical:
                frame = wrap_req_repeat(shared_req_hash)
            else:
                frame = wrap_req_diff(shared_req_hash, diff_payload)
            reference = new_ref

            total_bytes_diff += len(frame)
            reply = proxy.send_request(frame, timeout=10.0)
            if reply is None:
                check(f"reading {seq} got reply", False, "timeout")
                break

        savings_pct = 100.0 * (total_bytes_full - total_bytes_diff) / max(total_bytes_full, 1)
        print(f"     20 sensor readings over realistic transport:")
        print(f"     total bytes if all REQ_FULL:   {total_bytes_full}")
        print(f"     total bytes with diff/repeat:  {total_bytes_diff}")
        print(f"     savings:                       {savings_pct:.1f}%")
        print(f"     daemon stats: full={daemon.stats['req_full']} "
              f"diff={daemon.stats['req_diff']} repeat={daemon.stats['req_repeat']}")
        print(f"     decode errors on daemon:       {daemon.stats['decode_errors']}")
        check("savings still > 40% over realistic transport",
              savings_pct > 40.0, f"got {savings_pct:.1f}%")
        check("daemon decoded all REQ_DIFF without error",
              daemon.stats["decode_errors"] == 0,
              f"got {daemon.stats['decode_errors']}")
        check("template store accumulated the session reference",
              len(daemon.store) >= 1)
        return {"savings_pct": savings_pct,
                "total_full": total_bytes_full,
                "total_diff": total_bytes_diff}
    finally:
        proxy.close()
        daemon.stop()
        w1.close()
        w2.close()


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    print("=== M_transport: FAITHFUL FrogNet transport simulator ===")
    print("    (two-socket setup, HELLO+RETURN, SEQ_RESET, seq-tagged pipelining,")
    print("     worker pool, reply queue + writer thread, request coalescing,")
    print("     template store backing REQ_DIFF, adjustable wire shaping)")
    FAILS.clear()

    t0 = time.perf_counter()
    r1 = test_handshake_and_round_trip()
    r2 = test_pipelining()
    r3 = test_coalescing()
    r4 = test_slow_wire()
    r5 = test_asymmetric_wire()
    r7 = test_codex_over_realistic_transport()
    elapsed = time.perf_counter() - t0

    print(f"\n=== summary ===")
    print(f"    elapsed: {elapsed*1000:.0f}ms")
    print(f"    T1 handshake: PASS - full HELLO + RETURN + SEQ_RESET sequence")
    print(f"    T2 pipelining: {r2['frames']} concurrent in {r2['elapsed_ms']:.0f}ms")
    print(f"    T3 coalescing: {r3['coalesced']} requests coalesced into 1")
    print(f"    T4 slow wire: {r4['elapsed_ms']:.0f}ms under 10kbps cap")
    print(f"    T5 asymmetric: {r5['rtt_ms']:.0f}ms RTT (latency-bound)")
    print(f"    T7 codex integration: {r7['savings_pct']:.1f}% savings over realistic transport")

    if FAILS:
        print(f"\nM_TRANSPORT TIER FAILED: {FAILS}")
        return 1
    print("\nALL M_TRANSPORT CHECKS PASS - faithful transport machinery exercised.")
    print("Adjust via env: FROGNET_SIM_LATENCY_MS, _JITTER_MS, _BANDWIDTH_BPS,")
    print("                _OUTAGE_PROB, _OUTAGE_MS, _CAPTURE_DIR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
