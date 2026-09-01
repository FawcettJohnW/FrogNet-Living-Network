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
"""wire_medium.py - FrogNet sim WireMedium (lifted from transport_sim_tier.py).
Per-direction latency/jitter/bandwidth/outage shaping over real socketpairs. Endpoints
behave like sockets (sendall/recv/settimeout)."""
import os, socket, threading, time, random
from dataclasses import dataclass, field
from typing import Optional

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


