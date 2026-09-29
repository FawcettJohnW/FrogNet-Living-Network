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
core/semcache_wire.py

FrogNet Wire Protocol v1 (FNW1) for semantic cache SAME/DIFF responses.

v4.1: req_hash truncated to REQ_HASH_LEN (16 bytes) on the wire.
      Saves 16 bytes per REQ_FULL, REQ_REPEAT, REQ_RAW, REQ_MISS.
      same_id remains 16 bytes (already optimal).

Message Types:
    Proxy -> Daemon:
        REQ_FULL    (0x01): Full semantic request with hash (self-contained)
        REQ_REPEAT  (0x02): Repeat request (hash only)
        REQ_RAW     (0x03): Raw HTTP request with hash (no template)
        REQ_DIFF    (0x04): Diff-encoded semantic request (changed fields only)
        HELLO       (0x50): Connection handshake with return IP

    Daemon -> Proxy:
        RESP_SAME   (0x13): Response unchanged (same_id only)
        RESP_DIFF   (0x11): Response changed (same_id + semantic blob)
        RESP_RAW    (0x14): Raw HTTP response (same_id + raw body)
        REQ_MISS    (0x21): Daemon doesn't have this request cached
        ERROR       (0x30): Processing error (status + message)

Wire Format:
    [MAGIC:4][op:1][payload...]

    REQ_FULL:   [MAGIC][0x01][req_hash:H][len:4][semantic_packet:len]
    REQ_REPEAT: [MAGIC][0x02][req_hash:H]
    REQ_RAW:    [MAGIC][0x03][req_hash:H][len:4][http_request:len]
    REQ_DIFF:   [MAGIC][0x04][req_hash:H][len:4][semantic_diff:len]
    RESP_SAME:  [MAGIC][0x13][same_id:16]
    RESP_DIFF:  [MAGIC][0x11][same_id:16][len:4][sem_blob:len]
    RESP_RAW:   [MAGIC][0x14][same_id:16][len:4][status:2][hdrs_len:2][hdrs:hdrs_len][body:rest]
    REQ_MISS:   [MAGIC][0x21][req_hash:H]
    ERROR:      [MAGIC][0x30][status:2][msg_len:2][msg:msg_len]
    SEQ_RESET:  [MAGIC][0x40]
    HELLO:      [MAGIC][0x50][ip_len:1][ip_str:ip_len]

    H = REQ_HASH_LEN (16 bytes)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from core.codec import REQ_HASH_LEN

# Protocol magic bytes
MAGIC = b"FNW1"

# Operation codes
OP_REQ_FULL   = 0x01
OP_REQ_REPEAT = 0x02
OP_REQ_RAW    = 0x03
OP_REQ_DIFF   = 0x04
OP_RESP_DIFF  = 0x11
OP_RESP_SAME  = 0x13
OP_RESP_RAW   = 0x14
OP_REQ_MISS   = 0x21
OP_ERROR      = 0x30
OP_SEQ_RESET  = 0x40
# ---- ADDED ----
OP_HELLO      = 0x50

# [LINK_POTENTIAL_PING_V1 2026-05-25] Connect-time link-potential
# probes.  See bottom of file for wrap_/parse_ implementations and
# the design rationale.
OP_RTT_PING   = 0x60
OP_RTT_PONG   = 0x61
# [LOOP_DETECT_9009_V1] Daemon short-circuit verdict: the HELLO's return_ip is
# one of MY local IPs, so this alive connection hairpinned back through us and
# the candidate route loops. Fail-fast - no PONG, no timeout. Re-derived every
# merge (stateless); a reassigned .1/.2 is never remembered.
OP_RTT_LOOP   = 0x62

# Legacy semantic packet (no FNW1 wrapper)
OP_LEGACY     = 0x00

# same_id is always 16 bytes
SAME_ID_LEN = 16


@dataclass
class WireMsg:
    """Parsed wire message."""
    op: int
    req_hash: Optional[bytes] = None
    same_id: Optional[bytes] = None
    payload: Optional[bytes] = None
    # For RESP_RAW
    status: Optional[int] = None
    headers: Optional[bytes] = None
    body: Optional[bytes] = None
    # [LINK_POTENTIAL_PING_V1 2026-05-25] Timestamps carried inside
    # OP_RTT_PING / OP_RTT_PONG.  All are monotonic nanoseconds and
    # use big-endian 8-byte unsigned.  Clocks on the two hosts are
    # NOT synchronized; only differences within ONE host's stamps
    # are meaningful (network_rtt = proxy_t_pongback - proxy_t_send;
    # daemon_proc = daemon_t_reply - daemon_t_recv).
    ping_id: Optional[int] = None
    proxy_t_send_ns: Optional[int] = None    # set on both PING and PONG
    daemon_t_recv_ns: Optional[int] = None   # set on PONG only
    daemon_t_reply_ns: Optional[int] = None  # set on PONG only


def is_fnw1(frame: bytes) -> bool:
    """Check if frame starts with FNW1 magic."""
    return len(frame) >= 5 and frame[:4] == MAGIC


def wrap_req_full(req_hash: bytes, payload: bytes) -> bytes:
    """Wrap a full semantic request."""
    if len(req_hash) != REQ_HASH_LEN:
        raise ValueError(f"req_hash must be {REQ_HASH_LEN} bytes, got {len(req_hash)}")
    return MAGIC + bytes([OP_REQ_FULL]) + req_hash + struct.pack("!I", len(payload)) + payload


def wrap_req_repeat(req_hash: bytes) -> bytes:
    """Wrap a repeat request (just the hash, no payload)."""
    if len(req_hash) != REQ_HASH_LEN:
        raise ValueError(f"req_hash must be {REQ_HASH_LEN} bytes, got {len(req_hash)}")
    return MAGIC + bytes([OP_REQ_REPEAT]) + req_hash


def wrap_req_raw(req_hash: bytes, http_request: bytes) -> bytes:
    """Wrap a raw HTTP request (no template)."""
    if len(req_hash) != REQ_HASH_LEN:
        raise ValueError(f"req_hash must be {REQ_HASH_LEN} bytes, got {len(req_hash)}")
    return MAGIC + bytes([OP_REQ_RAW]) + req_hash + struct.pack("!I", len(http_request)) + http_request


def wrap_req_diff(req_hash: bytes, diff_payload: bytes) -> bytes:
    """Wrap a diff-encoded semantic request (changed fields only)."""
    if len(req_hash) != REQ_HASH_LEN:
        raise ValueError(f"req_hash must be {REQ_HASH_LEN} bytes, got {len(req_hash)}")
    return MAGIC + bytes([OP_REQ_DIFF]) + req_hash + struct.pack("!I", len(diff_payload)) + diff_payload


def wrap_resp_same(same_id: bytes) -> bytes:
    """Wrap a SAME response (response unchanged)."""
    if len(same_id) != SAME_ID_LEN:
        raise ValueError(f"same_id must be {SAME_ID_LEN} bytes, got {len(same_id)}")
    return MAGIC + bytes([OP_RESP_SAME]) + same_id


def wrap_resp_diff(same_id: bytes, sem_blob: bytes) -> bytes:
    """Wrap a DIFF response (response changed)."""
    if len(same_id) != SAME_ID_LEN:
        raise ValueError(f"same_id must be {SAME_ID_LEN} bytes, got {len(same_id)}")
    return MAGIC + bytes([OP_RESP_DIFF]) + same_id + struct.pack("!I", len(sem_blob)) + sem_blob


def wrap_resp_raw(same_id: bytes, status: int, headers: bytes, body: bytes) -> bytes:
    """Wrap a raw HTTP response."""
    if len(same_id) != SAME_ID_LEN:
        raise ValueError(f"same_id must be {SAME_ID_LEN} bytes, got {len(same_id)}")
    payload = struct.pack("!HH", status, len(headers)) + headers + body
    return MAGIC + bytes([OP_RESP_RAW]) + same_id + struct.pack("!I", len(payload)) + payload


def wrap_req_miss(req_hash: bytes) -> bytes:
    """Wrap a REQ_MISS response (daemon doesn't have this request)."""
    if len(req_hash) != REQ_HASH_LEN:
        raise ValueError(f"req_hash must be {REQ_HASH_LEN} bytes, got {len(req_hash)}")
    return MAGIC + bytes([OP_REQ_MISS]) + req_hash


def wrap_error(status: int, message: str) -> bytes:
    """Wrap an error response.  Wire: [MAGIC][0x30][status:2][len:2][msg:len]."""
    msg_bytes = message.encode("utf-8", "replace")
    return MAGIC + bytes([OP_ERROR]) + struct.pack("!HH", status, len(msg_bytes)) + msg_bytes


def wrap_seq_reset() -> bytes:
    """Wrap a SEQ_RESET message.  Wire: [MAGIC][0x40].  5 bytes total.

    Sent by the proxy on new TCP connections to tell the daemon to reset
    its request sequence counter.  The daemon echoes an identical frame
    back as acknowledgement.

    This self-heals sequence counter divergence that occurs when either
    side restarts while the other keeps its counter running.
    """
    return MAGIC + bytes([OP_SEQ_RESET])


# ---- ADDED ----
def wrap_hello(return_ip: str) -> bytes:
    """Wrap a HELLO message with the sender's FrogNet return IP.

    Wire: [MAGIC][0x50][ip_len:1][ip_str:ip_len]

    Sent by the proxy as the first frame on a new TCP connection
    to daemon:9009.  Tells the daemon which IP to connect back to
    on port 9010, avoiding the unreliable echo-based resolution
    over WG transit addresses.
    """
    ip_bytes = return_ip.encode("ascii")
    if len(ip_bytes) > 255:
        raise ValueError(f"return_ip too long: {len(ip_bytes)}")
    return MAGIC + bytes([OP_HELLO]) + bytes([len(ip_bytes)]) + ip_bytes
# ---- END ADDED ----


# ---- LINK_POTENTIAL_PING_V1 2026-05-25 ----
def wrap_rtt_ping(ping_id: int, proxy_t_send_ns: int, pad_len: int) -> bytes:
    """Wrap a connect-time link-potential probe ping.

    Wire: [MAGIC][0x60][ping_id:4][proxy_t_send_ns:8][pad_len:4][pad:pad_len]

    The pad makes the frame variable-size for ladder measurement.
    pad bytes are zero - content doesn't matter, only the byte count
    does (we're measuring serialization cost per byte, not content).
    All numeric fields big-endian.

    Run sequentially during _open_send_sock, AFTER HELLO and BEFORE
    the SemanticSession is created on the daemon side.  Sessions and
    pings never coexist on the same connection.
    """
    if ping_id < 0 or ping_id > 0xFFFFFFFF:
        raise ValueError(f"ping_id out of range: {ping_id}")
    if proxy_t_send_ns < 0 or proxy_t_send_ns > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"proxy_t_send_ns out of range: {proxy_t_send_ns}")
    if pad_len < 0 or pad_len > 0xFFFFFFFF:
        raise ValueError(f"pad_len out of range: {pad_len}")
    # bytes(pad_len) creates a zero-filled buffer in a single C
    # allocation - no Python-level loop.
    return (
        MAGIC + bytes([OP_RTT_PING])
        + struct.pack("!IQI", ping_id, proxy_t_send_ns, pad_len)
        + bytes(pad_len)
    )


def wrap_rtt_pong(ping_id: int, proxy_t_send_ns_echo: int,
                  daemon_t_recv_ns: int, daemon_t_reply_ns: int) -> bytes:
    """Wrap the daemon's reply to a link-potential probe ping.

    Wire: [MAGIC][0x61][ping_id:4][proxy_t_send_ns_echo:8]
                       [daemon_t_recv_ns:8][daemon_t_reply_ns:8]

    proxy_t_send_ns_echo: copied from the inbound PING so the proxy
        does not need to maintain its own send-side state - the wire
        carries every timestamp the proxy needs to derive RTT.

    daemon_t_recv_ns: monotonic_ns() captured the moment the daemon
        finished reading the PING frame off the socket.

    daemon_t_reply_ns: monotonic_ns() captured just before writing
        the PONG to the socket.

    Daemon processing time = daemon_t_reply_ns - daemon_t_recv_ns.
    Network round-trip = (proxy_t_pongback_ns - proxy_t_send_ns) -
                         (daemon_t_reply_ns - daemon_t_recv_ns).

    PONG is fixed-size (no pad) - only the timestamps matter on the
    return leg.  Keeps the experiment cheap on links where the
    proxy-to-daemon path is fast but the daemon-to-proxy path is
    expensive (asymmetric satellite, etc.).
    """
    return MAGIC + bytes([OP_RTT_PONG]) + struct.pack(
        "!IQQQ", ping_id, proxy_t_send_ns_echo,
        daemon_t_recv_ns, daemon_t_reply_ns)
# ---- END LINK_POTENTIAL_PING_V1 ----


# [LOOP_DETECT_9009_V1] -------------------------------------------------------
def wrap_rtt_loop() -> bytes:
    """Fixed-size LOOP verdict, no payload. Emitted by the daemon the instant it
    reads a HELLO whose return_ip is one of its own local IPs - the alive probe's
    connection looped back to us, so the candidate route hairpins through this
    node. The discovery client maps this to a hard 'drop the candidate, never
    install it'. Replaces the reflect 508 with an explicit, fail-fast frame; the
    verdict is re-derived every merge and never persisted."""
    return MAGIC + bytes([OP_RTT_LOOP])
# ---- END LOOP_DETECT_9009_V1 ----


def try_parse(frame: bytes) -> Optional[WireMsg]:
    """
    Try to parse a wire frame.

    Returns WireMsg if valid FNW1 frame, None if legacy/invalid.
    Raises ValueError if frame is FNW1 but malformed.
    """
    if len(frame) < 5:
        return None
    if frame[:4] != MAGIC:
        return None

    op = frame[4]
    off = 5
    H = REQ_HASH_LEN

    if op == OP_REQ_REPEAT:
        if len(frame) < off + H:
            raise ValueError("short REQ_REPEAT frame")
        return WireMsg(op=op, req_hash=frame[off:off+H])

    if op == OP_REQ_FULL:
        if len(frame) < off + H + 4:
            raise ValueError("short REQ_FULL header")
        req_hash = frame[off:off+H]; off += H
        (payload_len,) = struct.unpack("!I", frame[off:off+4]); off += 4
        if len(frame) < off + payload_len:
            raise ValueError(f"short REQ_FULL payload: expected {payload_len}, got {len(frame) - off}")
        return WireMsg(op=op, req_hash=req_hash, payload=frame[off:off+payload_len])

    if op == OP_REQ_RAW:
        if len(frame) < off + H + 4:
            raise ValueError("short REQ_RAW header")
        req_hash = frame[off:off+H]; off += H
        (payload_len,) = struct.unpack("!I", frame[off:off+4]); off += 4
        if len(frame) < off + payload_len:
            raise ValueError(f"short REQ_RAW payload: expected {payload_len}, got {len(frame) - off}")
        return WireMsg(op=op, req_hash=req_hash, payload=frame[off:off+payload_len])

    if op == OP_REQ_DIFF:
        if len(frame) < off + H + 4:
            raise ValueError("short REQ_DIFF header")
        req_hash = frame[off:off+H]; off += H
        (payload_len,) = struct.unpack("!I", frame[off:off+4]); off += 4
        if len(frame) < off + payload_len:
            raise ValueError(f"short REQ_DIFF payload: expected {payload_len}, got {len(frame) - off}")
        return WireMsg(op=op, req_hash=req_hash, payload=frame[off:off+payload_len])

    if op == OP_RESP_SAME:
        if len(frame) < off + SAME_ID_LEN:
            raise ValueError("short RESP_SAME frame")
        return WireMsg(op=op, same_id=frame[off:off+SAME_ID_LEN])

    if op == OP_RESP_DIFF:
        if len(frame) < off + SAME_ID_LEN + 4:
            raise ValueError("short RESP_DIFF header")
        same_id = frame[off:off+SAME_ID_LEN]; off += SAME_ID_LEN
        (payload_len,) = struct.unpack("!I", frame[off:off+4]); off += 4
        if len(frame) < off + payload_len:
            raise ValueError(f"short RESP_DIFF payload: expected {payload_len}, got {len(frame) - off}")
        return WireMsg(op=op, same_id=same_id, payload=frame[off:off+payload_len])

    if op == OP_RESP_RAW:
        if len(frame) < off + SAME_ID_LEN + 4:
            raise ValueError("short RESP_RAW header")
        same_id = frame[off:off+SAME_ID_LEN]; off += SAME_ID_LEN
        (payload_len,) = struct.unpack("!I", frame[off:off+4]); off += 4
        if len(frame) < off + payload_len:
            raise ValueError(f"short RESP_RAW payload: expected {payload_len}, got {len(frame) - off}")
        payload = frame[off:off+payload_len]
        if len(payload) < 4:
            raise ValueError("short RESP_RAW payload content")
        status, hdrs_len = struct.unpack("!HH", payload[:4])
        if len(payload) < 4 + hdrs_len:
            raise ValueError("short RESP_RAW headers")
        headers = payload[4:4+hdrs_len]
        body = payload[4+hdrs_len:]
        return WireMsg(op=op, same_id=same_id, status=status, headers=headers, body=body)

    if op == OP_REQ_MISS:
        if len(frame) < off + H:
            raise ValueError("short REQ_MISS frame")
        return WireMsg(op=op, req_hash=frame[off:off+H])

    if op == OP_ERROR:
        if len(frame) < off + 4:
            raise ValueError("short ERROR header")
        status, msg_len = struct.unpack("!HH", frame[off:off+4]); off += 4
        if len(frame) < off + msg_len:
            raise ValueError(f"short ERROR message: expected {msg_len}, got {len(frame) - off}")
        return WireMsg(op=op, status=status, payload=frame[off:off+msg_len])

    if op == OP_SEQ_RESET:
        return WireMsg(op=op)

    # ---- ADDED ----
    if op == OP_HELLO:
        if len(frame) < off + 1:
            raise ValueError("short HELLO frame")
        ip_len = frame[off]; off += 1
        if len(frame) < off + ip_len:
            raise ValueError(f"short HELLO ip: expected {ip_len}, got {len(frame) - off}")
        ip_str = frame[off:off+ip_len]
        return WireMsg(op=op, body=ip_str)
    # ---- END ADDED ----

    # ---- LINK_POTENTIAL_PING_V1 2026-05-25 ----
    if op == OP_RTT_PING:
        # [MAGIC][0x60][ping_id:4][proxy_t_send_ns:8][pad_len:4][pad:pad_len]
        if len(frame) < off + 4 + 8 + 4:
            raise ValueError("short RTT_PING header")
        ping_id, proxy_t_send_ns, pad_len = struct.unpack(
            "!IQI", frame[off:off+16]); off += 16
        if len(frame) < off + pad_len:
            raise ValueError(
                f"short RTT_PING pad: expected {pad_len}, got {len(frame) - off}")
        # We don't keep the pad bytes - they're zero by construction
        # and the receiver only needs to know the frame's total size,
        # which is len(frame).
        return WireMsg(op=op, ping_id=ping_id,
                       proxy_t_send_ns=proxy_t_send_ns,
                       payload=b"")

    if op == OP_RTT_PONG:
        # [MAGIC][0x61][ping_id:4][proxy_t_send_ns_echo:8]
        #              [daemon_t_recv_ns:8][daemon_t_reply_ns:8]
        if len(frame) < off + 4 + 8 + 8 + 8:
            raise ValueError("short RTT_PONG frame")
        ping_id, proxy_t_send_ns_echo, daemon_t_recv_ns, daemon_t_reply_ns = (
            struct.unpack("!IQQQ", frame[off:off+28]))
        return WireMsg(
            op=op, ping_id=ping_id,
            proxy_t_send_ns=proxy_t_send_ns_echo,
            daemon_t_recv_ns=daemon_t_recv_ns,
            daemon_t_reply_ns=daemon_t_reply_ns,
        )
    # ---- END LINK_POTENTIAL_PING_V1 ----

    if op == OP_RTT_LOOP:
        # [LOOP_DETECT_9009_V1] Fixed-size verdict, no body to parse.
        return WireMsg(op=op)

    # Unknown op but valid magic
    return WireMsg(op=op)


def op_name(op: int) -> str:
    """Get human-readable name for operation code."""
    names = {
        OP_REQ_FULL: "REQ_FULL",
        OP_REQ_REPEAT: "REQ_REPEAT",
        OP_REQ_RAW: "REQ_RAW",
        OP_REQ_DIFF: "REQ_DIFF",
        OP_RESP_SAME: "RESP_SAME",
        OP_RESP_DIFF: "RESP_DIFF",
        OP_RESP_RAW: "RESP_RAW",
        OP_REQ_MISS: "REQ_MISS",
        OP_ERROR: "ERROR",
        OP_SEQ_RESET: "SEQ_RESET",
        OP_HELLO: "HELLO",
        OP_RTT_PING: "RTT_PING",
        OP_RTT_PONG: "RTT_PONG",
        OP_RTT_LOOP: "RTT_LOOP",
        OP_LEGACY: "LEGACY",
    }
    return names.get(op, f"UNKNOWN({op:#x})")
