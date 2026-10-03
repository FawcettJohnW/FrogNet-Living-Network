#!/opt/frognet_semantic/venv/bin/python3
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
proxy/policy.py

Loads semantic policy from:
  - /etc/frognet/semantic_hosts

Supported line formats (both files):
  - <ip>
  - <cidr>
  - <cidr> via <via_ip>

Semantics:
  - Override sets (dest membership) determine "semantic required"
  - Edge-via hints determine "next hop is semantic peer"

Notes:
  - semantic_links is treated as equally authoritative as semantic_hosts.
  - Both files are merged into the same in-memory policy sets.
  - Policy reload triggers if either file changes.
"""

from __future__ import annotations

import os
import socket
from collections import defaultdict
from typing import Dict, Set, Tuple

from proxy.constants import DEBUG, debug
from proxy.netutil import ip_to_24

SEMANTIC_HOSTS_FILE = "/etc/frognet/semantic_hosts"

SEMANTIC_OVERRIDE_IPS: Set[str] = set()
SEMANTIC_OVERRIDE_NETS: Set[str] = set()
SEMANTIC_EDGE_VIA: Dict[str, Set[str]] = defaultdict(set)

_POLICY_MTIME_LAST: float = 0.0


def _is_v4(s: str) -> bool:
    """[NO_FALLBACK_V1] inet_aton raises OSError for a malformed address, and
    that is the answer this predicate exists to give.

    It was `except Exception`, which also caught TypeError (a non-str reaching
    here from a caller bug) and reported it as "that token is not an address".
    The consequence is not an error anywhere: _parse_cidr_or_ip returns
    (None, None) and the policy line is dropped from SEMANTIC_OVERRIDE_IPS
    without a word, so a host silently stops being treated as semantic.
    """
    try:
        socket.inet_aton(s)
        return True
    except OSError:
        return False


def _parse_cidr_or_ip(token: str) -> Tuple[str | None, str | None]:
    t = token.strip()
    if "/" in t:
        return (None, t)
    return (t if _is_v4(t) else None, None)


def _load_one_file(path: str) -> None:
    """
    Load policy entries from a single file into the global sets.

    This function does not clear globals; caller owns clear/merge behavior.
    """
    if not path or not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "#" in line:
                line = line.split("#", 1)[0].strip()
            if not line:
                continue

            parts = [p for p in line.replace("@", " ").split() if p]
            if not parts:
                continue

            ip_tok, cidr_tok = _parse_cidr_or_ip(parts[0])

            if ip_tok:
                SEMANTIC_OVERRIDE_IPS.add(ip_tok)
                SEMANTIC_OVERRIDE_NETS.add(ip_to_24(ip_tok))
                continue

            if cidr_tok:
                SEMANTIC_OVERRIDE_NETS.add(cidr_tok)
                if len(parts) >= 3 and parts[1].lower() == "via" and _is_v4(parts[2]):
                    SEMANTIC_EDGE_VIA[cidr_tok].add(parts[2])
                continue


def load_semantic_hosts() -> None:
    """
    Backward-compatible name (kept because other modules import it).
    Now loads BOTH semantic_hosts and semantic_links.
    """
    SEMANTIC_OVERRIDE_IPS.clear()
    SEMANTIC_OVERRIDE_NETS.clear()
    SEMANTIC_EDGE_VIA.clear()

    # Merge sources (both authoritative)
    _load_one_file(SEMANTIC_HOSTS_FILE)

    if DEBUG:
        debug(f"[Policy] semantic IPs={sorted(SEMANTIC_OVERRIDE_IPS)}")
        debug(f"[Policy] semantic NETs={sorted(SEMANTIC_OVERRIDE_NETS)}")
        debug(
            f"[Policy] semantic edges (subnet -> via)={{ "
            + ", ".join([f"{k}:{sorted(list(v))}" for k, v in SEMANTIC_EDGE_VIA.items()])
            + " }}"
        )  # noqa


def refresh_policy_if_changed() -> None:
    """
    Reload policy if either semantic_hosts OR semantic_links changed.
    """
    global _POLICY_MTIME_LAST

    mtimes = []

    try:
        mtimes.append(os.stat(SEMANTIC_HOSTS_FILE).st_mtime)
    except Exception:
        mtimes.append(0.0)

    # Single comparable stamp: max is sufficient for change detection across both files
    mtime = max(mtimes) if mtimes else 0.0

    if mtime != _POLICY_MTIME_LAST:
        _POLICY_MTIME_LAST = mtime
        load_semantic_hosts()
