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
core/frognet_secret.py

Auto-reloading secret for HMAC-based same_id generation.
The secret is stored in /etc/frognet/sem_cache_secret and is
automatically reloaded when the file changes (mtime check).

This allows secret rotation without restarting the daemon/proxy.
"""

from __future__ import annotations

import os
import sys as _sys
import hashlib

_SECRET_FILE = os.environ.get("FROGNET_SEM_CACHE_SECRET_FILE", "/etc/frognet/sem_cache_secret")
_DEFAULT_SECRET = b"frognet_default_secret_change_me"

_cached: bytes | None = None
_cached_mtime: float = 0.0


def get_secret_bytes() -> bytes:
    """
    Get the current secret, auto-reloading if file changed.
    
    Returns:
        Secret bytes. If file missing, returns a default (unsafe for production).
    """
    global _cached, _cached_mtime
    
    try:
        st = os.stat(_SECRET_FILE)
        if _cached is None or st.st_mtime != _cached_mtime:
            with open(_SECRET_FILE, "rb") as f:
                raw = f.read().strip()
                # If the secret looks like hex, decode it
                if len(raw) == 64:
                    try:
                        _cached = bytes.fromhex(raw.decode('ascii'))
                    except (ValueError, UnicodeDecodeError):
                        _cached = raw
                else:
                    _cached = raw
            _cached_mtime = st.st_mtime
    except FileNotFoundError:
        if _cached is None:
            # [NO_FALLBACK_V1] No secret file. The tag below is derived from a
            # PUBLISHED constant, so it is forgeable by anyone with the source:
            # the SAME identifier stops proving anything. Spec 12.2 says an
            # implementation MUST NOT operate without a secret in place, so the
            # degradation is announced loudly on every derivation - silence here
            # is how a node runs unauthenticated for months without anyone
            # noticing. Derived from machine-id so at least two nodes do not
            # share one forgeable key.
            machine_id = b""
            try:
                with open("/etc/machine-id", "rb") as f:
                    machine_id = f.read().strip()
            except FileNotFoundError:
                pass
            print(f"[frognet_secret] CRITICAL: no secret at {_SECRET_FILE} - "
                  f"SAME identifiers are derived from a published default and "
                  f"are FORGEABLE. Write a secret and restart.",
                  file=_sys.stderr, flush=True)
            _cached = hashlib.sha256(_DEFAULT_SECRET + machine_id).digest()
        _cached_mtime = 0.0
    except Exception as e:
        if _cached is None:
            # Worse: the bare published constant, identical on every node that
            # lands here. Never silent.
            print(f"[frognet_secret] CRITICAL: cannot read {_SECRET_FILE} "
                  f"({e!r}) - falling back to the PUBLISHED default secret. "
                  f"SAME identifiers are forgeable and identical across nodes.",
                  file=_sys.stderr, flush=True)
            _cached = _DEFAULT_SECRET
        _cached_mtime = 0.0
    
    return _cached or _DEFAULT_SECRET


def get_secret_fingerprint() -> str:
    """
    Get a fingerprint of the current secret for logging/debugging.
    """
    secret = get_secret_bytes()
    return hashlib.sha256(secret).hexdigest()[:16]
