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
core/semcache_id.py

Generate HMAC-based same_id tokens for SAME/DIFF protocol.

The same_id is a 16-byte token derived from:
    - The shared secret (auto-reloaded)
    - The request hash (identifies the request)
    - The raw response hash (identifies the response content)

v4.1: req_hash may be REQ_HASH_LEN (16) bytes (wire truncated) or
      32 bytes (full SHA256 internally).  raw_hash is always 32 bytes
      (internal to daemon, never on the wire).
"""

from __future__ import annotations

import hmac
import hashlib

from core.frognet_secret import get_secret_bytes
from core.codec import REQ_HASH_LEN


def same_id(req_hash: bytes, raw_hash: bytes) -> bytes:
    """
    Generate a same_id token.

    Args:
        req_hash: Request identity hash (REQ_HASH_LEN or 32 bytes)
        raw_hash: SHA256 of the raw upstream response (32 bytes)

    Returns:
        16-byte HMAC token (truncated from SHA256)
    """
    if len(req_hash) not in (REQ_HASH_LEN, 32):
        raise ValueError(f"req_hash must be {REQ_HASH_LEN} or 32 bytes, got {len(req_hash)}")
    if len(raw_hash) != 32:
        raise ValueError(f"raw_hash must be 32 bytes, got {len(raw_hash)}")

    key = get_secret_bytes()
    mac = hmac.new(key, req_hash + raw_hash, hashlib.sha256).digest()
    return mac[:16]


def verify_same_id(token: bytes, req_hash: bytes, raw_hash: bytes) -> bool:
    """Verify a same_id token."""
    if len(token) != 16:
        return False
    expected = same_id(req_hash, raw_hash)
    return hmac.compare_digest(token, expected)


def same_id_hex(req_hash: bytes, raw_hash: bytes) -> str:
    """Generate same_id and return as hex string."""
    return same_id(req_hash, raw_hash).hex()
