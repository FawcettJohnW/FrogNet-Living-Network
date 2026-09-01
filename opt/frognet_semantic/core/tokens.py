#!/opt/frognet_semantic/venv/bin/python
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
# core/tokens.py - TokenStore v3 (value<->id, categories: ip/host/str/enum)

from typing import Dict, Any


class TokenStore:
    """
    TokenStore maps semantic values to stable token IDs, per category.

    Internal structure (value -> id):

        {
            "ip":   {"10.x.y.1": 1, "10.x.y.2": 2},
            "host": {"FrogNetHost.TealBox": 1, ...},
            "str":  {...},
            "enum": {...}
        }

    v3 contract:
      - to_token(value, category)  -> string token (usually the numeric id as str)
      - from_token(token, category)-> original value (string)
      - encode uses value -> token
      - decode uses token -> value
      - tokens must be persisted with templates (export/to_dict)
    """

    def __init__(self, initial: Dict[str, Dict[str, int]] = None):
        base = {"ip": {}, "host": {}, "str": {}, "enum": {}}

        if isinstance(initial, dict):
            for cat in base.keys():
                if cat in initial and isinstance(initial[cat], dict):
                    fixed = {}
                    for k, v in initial[cat].items():
                        fixed[str(k)] = int(v)
                    base[cat] = fixed

        self.tokens: Dict[str, Dict[str, int]] = base

    # ------------------------------------------------------------------
    # Low-level add / lookup (value <-> id)
    # ------------------------------------------------------------------
    def add(self, category: str, value: Any) -> int:
        """
        Map a value to a numeric token id in the given category.
        Returns existing id if present; otherwise allocates a new one.
        """
        cat = self.tokens.setdefault(category, {})
        sval = str(value)

        if sval in cat:
            return cat[sval]

        new_id = len(cat) + 1
        cat[sval] = new_id
        return new_id

    def lookup(self, category: str, token_id: int):
        """
        Reverse lookup: given a token id, return the original value (string),
        or None if not found.
        """
        cat = self.tokens.get(category, {})
        tid = int(token_id)
        for val, vid in cat.items():
            if vid == tid:
                return val
        return None

    # ------------------------------------------------------------------
    # v3 convenience API used by SemanticCodec
    # ------------------------------------------------------------------
    def to_token(self, value: Any, category: str = "str") -> str:
        """
        Convert a value into a stable token string for a given category.
        Default category is 'str' unless caller specifies ip/host/enum.
        """
        tid = self.add(category, value)
        return str(tid)

    def from_token(self, token: str, category: str = "str") -> Any:
        """
        Convert a token string back into the original value for this category.
        """
        try:
            tid = int(token)
        except (TypeError, ValueError):
            return token
        val = self.lookup(category, tid)
        return val if val is not None else token

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def export(self) -> Dict[str, Dict[str, int]]:
        """
        Return a copy of the internal mapping suitable for JSON storage.
        """
        return {cat: dict(vals) for cat, vals in self.tokens.items()}

    # Backwards-compatible alias
    def to_dict(self) -> Dict[str, Dict[str, int]]:
        return self.export()
