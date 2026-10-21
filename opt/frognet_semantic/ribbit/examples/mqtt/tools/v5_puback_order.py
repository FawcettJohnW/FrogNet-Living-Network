#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""v5_puback_order.py PORT TOPIC... -- one MQTT 5 connection publishes QoS 1 to each topic in turn (mid 1, 2, ...)
without waiting, then reads the PUBACKs. Prints "mid:reason" for each, in the order they arrived. [ASYNC_PUBACK_V1]"""
import socket, struct, sys
def vi(n):
    o = b""
    while True:
        b = n % 128; n //= 128
        o += bytes([b | (128 if n else 0)])
        if not n: return o
def pkt(h, body): return bytes([h]) + vi(len(body)) + body
def s(x): x = x.encode(); return struct.pack(">H", len(x)) + x
port, topics = int(sys.argv[1]), sys.argv[2:]
c = socket.create_connection(("127.0.0.1", port), timeout=5)
c.sendall(pkt(0x10, s("MQTT") + bytes([5, 2]) + struct.pack(">H", 60) + b"\0" + s("order-check")))
def read():
    h = c.recv(1)[0]; n = m = 0
    while True:
        b = c.recv(1)[0]; n += (b & 127) << (7 * m); m += 1
        if not b & 128: break
    d = b""
    while len(d) < n: d += c.recv(n - len(d))
    return h, d
h, _ = read()                                            # CONNACK
out = b"".join(pkt(0x32, s(t) + struct.pack(">H", i + 1) + b"\0" + b"x") for i, t in enumerate(topics))
c.sendall(out)
got = []
while len(got) < len(topics):
    h, d = read()
    if h >> 4 == 4: got.append(f"{struct.unpack('>H', d[:2])[0]}:{d[2] if len(d) > 2 else 0}")
print(" ".join(got))
