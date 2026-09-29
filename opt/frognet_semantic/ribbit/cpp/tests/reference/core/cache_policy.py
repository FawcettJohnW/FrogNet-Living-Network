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
core/cache_policy.py

Shared cache-write policy for proxy + daemon.

`is_cacheable_raw(headers)` is consulted *before* any semcache_db.upsert_raw
call. It returns False for opaque-binary HTTP responses (images, video, audio,
fonts, archives, octet-stream) and for bodies whose Content-Encoding is already
a compression algorithm (gzip/br/deflate/zstd/compress). Skipping these at the
write-side keeps the SemCacheProxy/SemCacheDaemon tables from filling up with
blobs the cache cannot meaningfully reuse, and avoids ever double-compressing
something the upstream already compressed.

`maybe_compress` / `maybe_decompress` provide gzip-on-store for the proxy
cache. Daemon side does not use them - its SemBlob column is dead-write.

DEPLOYMENT MARKER: v2026-04-30 - cache-policy / gzip-text v1
"""

import gzip
from typing import Mapping, Optional, Tuple

# Content-Type prefixes whose payloads we treat as opaque binary.
_BINARY_CT_PREFIXES = ("image/", "video/", "audio/", "font/")

# Content-Type values (exact) that are opaque binary or already-compressed.
_BINARY_CT_EXACT = frozenset({
    "application/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
    "application/x-gzip",
    "application/gzip",
    "application/pdf",
    "application/x-tar",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
    "application/wasm",
})

# Content-Encoding values that mean the body is already compressed.
_COMPRESSED_ENCODINGS = frozenset({"gzip", "br", "deflate", "zstd", "compress"})


def _header_lookup(headers: Optional[Mapping[str, str]], name: str) -> str:
    if not headers:
        return ""
    target = name.lower()
    for k, v in headers.items():
        if k and k.lower() == target:
            return (v or "").strip()
    return ""


def is_cacheable_raw(headers: Optional[Mapping[str, str]]) -> bool:
    """
    True if a raw HTTP response with these headers should be written to the
    semantic cache. False for binary or already-compressed bodies.
    """
    ce = _header_lookup(headers, "Content-Encoding").lower()
    if ce:
        for token in ce.split(","):
            if token.strip() in _COMPRESSED_ENCODINGS:
                return False

    ct = _header_lookup(headers, "Content-Type").lower()
    if not ct:
        # No Content-Type: be conservative, don't cache.
        return False
    primary = ct.split(";", 1)[0].strip()
    if not primary:
        return False
    if primary in _BINARY_CT_EXACT:
        return False
    for prefix in _BINARY_CT_PREFIXES:
        if primary.startswith(prefix):
            return False
    return True


# -- Compression helpers (proxy side) ----------------------------------

GZIP_MIN_BYTES = 256  # don't bother compressing tiny bodies


def maybe_compress(body: bytes) -> Tuple[bytes, int]:
    """
    Return (stored_bytes, compression_flag).
    flag == 1 -> stored_bytes is gzip-compressed
    flag == 0 -> stored_bytes is the original (small, or compression unhelpful)
    """
    if not body or len(body) < GZIP_MIN_BYTES:
        return body, 0
    try:
        compressed = gzip.compress(body, compresslevel=6)
    except Exception:
        return body, 0
    if len(compressed) >= len(body):
        return body, 0
    return compressed, 1


def maybe_decompress(stored: bytes, compression: int) -> bytes:
    """
    Inverse of maybe_compress. Tolerates legacy rows where the column is
    NULL/0 (treat as uncompressed verbatim).
    """
    if not stored:
        return b""
    if compression == 1:
        try:
            return gzip.decompress(stored)
        except Exception:
            # Defensive: corrupt row -> return raw bytes rather than 500.
            return stored
    return stored
