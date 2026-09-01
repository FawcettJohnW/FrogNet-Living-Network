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
frognet_monitor/formatting.py — Display formatting utilities.
"""

import json
from typing import Any


def fmt_bytes(b) -> str:
    try:
        b = int(b)
    except (TypeError, ValueError):
        return "—"
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f}MB"
    if b >= 1024:
        return f"{b / 1024:.1f}KB"
    return f"{b}B"


def fmt_bps(bps) -> str:
    """Format bits-per-second to a compact string (max ~8 chars)."""
    try:
        bps = float(bps)
    except (TypeError, ValueError):
        return "—"
    if bps <= 0:
        return "—"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f}Mbps"
    if bps >= 10_000:
        return f"{bps / 1000:.0f}kbps"
    if bps >= 1000:
        return f"{bps / 1000:.1f}kbps"
    return f"{bps:.0f}bps"


def fmt_ratio(r) -> str:
    try:
        return f"{float(r) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def safe_get(d, *keys, default="—"):
    """Navigate nested dicts safely."""
    cur = d
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k, default)
        else:
            return default
    return cur if cur not in (None, "") else default


def num(v, fallback=0):
    """Coerce a JSON value to float.  Semantic layer may return strings."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return fallback


def pretty_json(obj, indent=2) -> str:
    """Pretty-print JSON, unwrapping jsonData if present."""
    if isinstance(obj, dict) and "jsonData" in obj:
        jd = obj["jsonData"]
        if isinstance(jd, str):
            try:
                jd = json.loads(jd)
            except json.JSONDecodeError:
                pass
        obj = {**obj, "jsonData": jd}
    return json.dumps(obj, indent=indent, default=str, ensure_ascii=False)
