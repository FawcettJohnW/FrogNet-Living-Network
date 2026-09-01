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
# core/text_handler.py
#
# TEXT + RAW handlers for FrogNet Semantic (no tokenization, no splitting).
#
# Contract:
#   - extract_*_dynamic returns List[(field, value)]
#   - rebuild_reply reconstructs body from values
#   - baseline markers are not introduced here
#
# Policy (today):
#   - TEXT is treated as a single "raw" field. No CSV/host/ip inference.
#   - This avoids the frognet_echo baseline poisoning class of bugs.
#   - Compression comes from semantic transport + LZ4 on fieldblock.

from __future__ import annotations
from typing import Any, Dict, List, Tuple


from .unrest_handler import UnRESTHandler  # the one UnREST handler interface


class RawFormatHandler(UnRESTHandler):
    """
    RAW mode:
      - Entire body is a single field "raw".
      - type_map uses "raw" -> "raw" so codec uses TYPE_RAW.
    """

    def learn_request_template(self, body_text: str) -> Dict[str, Any]:
        body_text = body_text or ""
        return {
            "mode": "raw",
            "fields": ["raw"],
            "field_order": ["raw"],
            "type_map": {"raw": "raw"},
            "baseline": {"raw": body_text},
            "baseline_blobs": {},
            "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
        }

    def learn_reply_template(self, body_text: str) -> Dict[str, Any]:
        body_text = body_text or ""
        return {
            "mode": "raw",
            "fields": ["raw"],
            "field_order": ["raw"],
            "type_map": {"raw": "raw"},
            "baseline": {"raw": body_text},
            "baseline_blobs": {},
            "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
        }

    def extract_request_dynamic(self, body_text: str, fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return [("raw", body_text or "")]

    def extract_reply_dynamic(self, body_text: str, fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return [("raw", body_text or "")]

    def rebuild_reply(self, fragment: Dict[str, Any], values: List[Any]) -> str:
        if not values:
            baseline = (fragment or {}).get("baseline") or {}
            return baseline.get("raw", "")

        v0 = values[0]
        if isinstance(v0, (tuple, list)) and len(v0) == 2:
            v = v0[1]
        else:
            v = v0
        return "" if v is None else str(v)


class TextFormatHandler(UnRESTHandler):
    """
    TEXT mode:
      - Behavior is identical to RAW for now (single "raw" field).
      - Keeps mode="text" so dashboards / Content-Type decisions can still distinguish it.
      - type_map uses raw->raw so codec uses TYPE_RAW.
      - No tokenization, no CSV splitting, no baseline substitution.
    """

    def learn_request_template(self, body_text: str) -> Dict[str, Any]:
        body_text = body_text or ""
        return {
            "mode": "text",
            "fields": ["raw"],
            "field_order": ["raw"],
            "type_map": {"raw": "raw"},
            "baseline": {"raw": body_text},
            "baseline_blobs": {},
            "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
        }

    def learn_reply_template(self, body_text: str) -> Dict[str, Any]:
        body_text = body_text or ""
        return {
            "mode": "text",
            "fields": ["raw"],
            "field_order": ["raw"],
            "type_map": {"raw": "raw"},
            "baseline": {"raw": body_text},
            "baseline_blobs": {},
            "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
        }

    def extract_request_dynamic(self, body_text: str, fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return [("raw", body_text or "")]

    def extract_reply_dynamic(self, body_text: str, fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return [("raw", body_text or "")]

    def rebuild_reply(self, fragment: Dict[str, Any], values: List[Any]) -> str:
        # Accept [(field,val)] or [val]
        if not values:
            baseline = (fragment or {}).get("baseline") or {}
            return baseline.get("raw", "")

        v0 = values[0]
        if isinstance(v0, (tuple, list)) and len(v0) == 2:
            v = v0[1]
        else:
            v = v0
        return "" if v is None else str(v)
