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
Minimal RFC 6455 WebSocket client.  No external dependencies.
"""

import base64
import os
import socket
import ssl
import struct
from typing import Optional
from urllib.parse import urlparse


class SimpleWebSocket:

    def __init__(self, url: str):
        self.url     = url
        parsed       = urlparse(url)
        self.host    = parsed.hostname
        self.port    = parsed.port or (443 if parsed.scheme == "wss" else 80)
        self.path    = parsed.path or "/"
        self.use_ssl = parsed.scheme == "wss"
        self.sock: Optional[socket.socket] = None

    def connect(self):
        raw = socket.create_connection((self.host, self.port), timeout=15)
        if self.use_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        self.sock = raw
        self.sock.settimeout(90)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Connection closed during handshake")
            response += chunk
        if b"101" not in response.split(b"\r\n")[0]:
            raise ConnectionError(f"WS handshake failed: {response[:200]}")

    def send(self, data):
        if isinstance(data, str):
            data = data.encode()
        frame = bytearray([0x81])
        mk = os.urandom(4)
        n = len(data)
        if n < 126:
            frame.append(0x80 | n)
        elif n < 65536:
            frame += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            frame += bytes([0x80 | 127]) + struct.pack(">Q", n)
        frame += mk + bytearray(b ^ mk[i % 4] for i, b in enumerate(data))
        self.sock.sendall(frame)

    def recv(self) -> str:
        def _r(n):
            buf = b""
            while len(buf) < n:
                c = self.sock.recv(n - len(buf))
                if not c:
                    raise ConnectionError("Connection closed")
                buf += c
            return buf

        h = _r(2)
        op = h[0] & 0x0F
        masked = bool(h[1] & 0x80)
        n = h[1] & 0x7F
        if n == 126: n = struct.unpack(">H", _r(2))[0]
        elif n == 127: n = struct.unpack(">Q", _r(8))[0]
        mk = _r(4) if masked else None
        payload = _r(n)
        if masked and mk:
            payload = bytes(b ^ mk[i % 4] for i, b in enumerate(payload))
        if op == 0x8:
            raise ConnectionError("Server sent close frame")
        if op == 0x9:
            mk2 = os.urandom(4)
            self.sock.sendall(bytearray([0x8A, 0x80 | len(payload)]) + mk2 +
                              bytearray(b ^ mk2[i % 4] for i, b in enumerate(payload)))
            return self.recv()
        if op == 0xA:
            return self.recv()
        return payload.decode()

    def close(self):
        if self.sock:
            try:
                self.sock.sendall(b"\x88\x80" + os.urandom(4))
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def send_json(self, obj):
        import json
        self.send(json.dumps(obj))

    def recv_json(self):
        import json
        return json.loads(self.recv())
