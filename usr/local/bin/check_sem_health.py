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
/usr/local/bin/check_sem_health.py

FNW1 health check for semantic daemon connectivity.
Sends REQ_REPEAT with magic health-check hash, expects RESP_SAME back.

Usage:
    check_sem_health.py <ip> [port] [timeout]
    
Exit codes:
    0 = healthy (got valid FNW1 response)
    1 = unhealthy (connection failed, timeout, or invalid response)
    2 = usage error
"""

import socket
import struct
import sys

# Wire protocol constants (from semcache_wire.py)
MAGIC = b"FNW1"
OP_REQ_REPEAT = 0x02
OP_RESP_SAME = 0x13

# Magic health check hash - all 0xFF bytes
# Daemon should recognize this and respond without DB lookup
HEALTH_CHECK_HASH = b'\xff' * 32
HEALTH_CHECK_SAME_ID = b'\xff' * 16


def build_health_request() -> bytes:
    """Build a FNW1 REQ_REPEAT frame with health check hash."""
    payload = MAGIC + bytes([OP_REQ_REPEAT]) + HEALTH_CHECK_HASH
    frame = struct.pack("!I", len(payload)) + payload
    return frame


def check_health(ip: str, port: int = 9009, timeout: float = 2.0) -> bool:
    """
    Check if semantic daemon is healthy.
    
    Returns True if daemon responds with valid FNW1 frame.
    """
    frame = build_health_request()
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.connect((ip, port))
        s.sendall(frame)
        
        # Read response length (4 bytes)
        hdr = b""
        while len(hdr) < 4:
            chunk = s.recv(4 - len(hdr))
            if not chunk:
                s.close()
                return False
            hdr += chunk
        
        (resp_len,) = struct.unpack("!I", hdr)
        if resp_len <= 0 or resp_len > 1024:  # Sanity check
            s.close()
            return False
        
        # Read response payload
        resp = b""
        while len(resp) < resp_len:
            chunk = s.recv(resp_len - len(resp))
            if not chunk:
                s.close()
                return False
            resp += chunk
        
        s.close()
        
        # Validate response
        if len(resp) < 5:
            return False
        
        # Check FNW1 magic
        if resp[:4] != MAGIC:
            return False
        
        # Check opcode - should be RESP_SAME or could be REQ_MISS
        op = resp[4]
        if op == OP_RESP_SAME:
            return True
        
        # REQ_MISS is also a valid response (daemon is alive but doesn't have the hash)
        # This is fine for health check - daemon is responding
        if op == 0x21:  # OP_REQ_MISS
            return True
        
        return False
        
    except socket.timeout:
        return False
    except ConnectionRefusedError:
        return False
    except ConnectionResetError:
        return False
    except OSError:
        return False
    except Exception:
        return False


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <ip> [port] [timeout]", file=sys.stderr)
        sys.exit(2)
    
    ip = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9009
    timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    
    healthy = check_health(ip, port, timeout)
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
