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
# core/template.py
#
# FrogNet Semantic Templates v3
# RequestTemplate + ReplyTemplate
#
# Guarantees:
#   - template fragments are always dicts
#   - tokens always exist
#   - baseline may be blob-backed but is never sent over the wire
#   - rebuild is handler-driven and defensive

from typing import Dict, Any, List, Tuple, Optional
from urllib.parse import urlparse, parse_qsl, urlencode
import json

from core.format_registry import FORMAT_HANDLERS
from core.template_utils import rebuild_json
from core.blob_store import BlobStore


# -------------------------------------------------------------------
# Internal helper
# -------------------------------------------------------------------

def _coerce_fragment(obj: Any) -> Dict[str, Any]:
    """
    Ensure template fragments are dicts.

    Accepts:
      - dict -> dict
      - str  -> json.loads(str) if possible
      - None -> {}

    Never returns non-dict.
    """
    if isinstance(obj, dict):
        return obj
    if obj is None:
        return {}
    if isinstance(obj, str):
        s = obj.strip()
        if not s:
            return {}
        try:
            j = json.loads(s)
            return j if isinstance(j, dict) else {}
        except Exception:
            # fallback: raw
            return {"mode": "raw"}
    return {}


# ===================================================================
# REQUEST TEMPLATE
# ===================================================================

class RequestTemplate:
    """
    RequestTemplate represents the semantic structure of an HTTP request.

    It is used ONLY by:
      - proxy (Phase I extraction)
      - daemon (Phase II rebuild)

    It NEVER rebuilds a response.
    """

    def __init__(
        self,
        template_id: str,
        opcode: int,
        method: str,
        url_static: str,
        url_query_keys: List[str],
        template_fragment: Any,
        tokens: Dict[str, Dict[str, int]],
    ):
        self.template_id = template_id
        self.opcode = int(opcode)
        self.method = method
        self.url_static = url_static or "/"
        self.url_query_keys = url_query_keys or []

        # Fragment normalization
        self.fragment: Dict[str, Any] = _coerce_fragment(template_fragment)

        # Tokens MUST be dict-of-dicts
        if not isinstance(tokens, dict):
            tokens = {}
        self.tokens: Dict[str, Dict[str, int]] = {
            "ip":   dict(tokens.get("ip", {})),
            "host": dict(tokens.get("host", {})),
            "str":  dict(tokens.get("str", {})),
            "enum": dict(tokens.get("enum", {})),
        }

        mode = self.fragment.get("mode", "raw")
        self.handler = FORMAT_HANDLERS.get(mode, FORMAT_HANDLERS["raw"])

    # ----------------------------
    # Properties
    # ----------------------------

    @property
    def mode(self) -> str:
        return self.fragment.get("mode", "raw")

    @property
    def type_map(self) -> Dict[str, str]:
        return self.fragment.get("type_map", {}) or {}

    # ----------------------------
    # Extraction
    # ----------------------------

    def extract_dynamic(self, body_text: str) -> List[Tuple[str, Any]]:
        """
        Extract semantic fields from request body.
        """
        if not body_text:
            return []
        return self.handler.extract_request_dynamic(body_text, self.fragment)

    # ----------------------------
    # URL + body rebuild (daemon side)
    # ----------------------------

    def build_url(self, url_vals: List[Tuple[str, Any]]) -> str:
        parsed = urlparse(self.url_static or "/")
        base_q = parse_qsl(parsed.query or "", keep_blank_values=True)

        # [NO_SILENT_FILTER_DROP_V1] A None was skipped here, turning "could not
        # resolve this parameter" into "not asked for" -- a different query, 200.
        # execution.py gates this ([REQUEST_REF_COMPLETE_V1]); reaching here means
        # a caller bypassed it, so say so.
        dyn_q = []
        for k, v in url_vals:
            if v is None:
                raise ValueError("build_url: %r is None for %s - refusing to drop "
                                 "the parameter" % (k, self.url_static))
            dyn_q.append((k, str(v)))

        combined = base_q + dyn_q
        q_str = urlencode(combined) if combined else ""
        path = parsed.path or "/"

        return f"{path}?{q_str}" if q_str else path

    def rebuild_body(self, json_vals: List[Tuple[str, Any]]) -> str:
        """
        Rebuild request body from semantic values.
        """
        mapping: Dict[str, Any] = {}

        if json_vals and isinstance(json_vals[0], (tuple, list)) and len(json_vals[0]) == 2:
            for f, v in json_vals:
                mapping[str(f)] = v
        else:
            field_order = self.fragment.get("field_order", []) or []
            for f, v in zip(field_order, json_vals):
                mapping[str(f)] = v

        if self.mode == "json":
            field_order = self.fragment.get("field_order", []) or []
            values = [mapping.get(f) for f in field_order]
            obj = rebuild_json(values, field_order)
            return json.dumps(obj, separators=(",", ":"))

        # raw / text / html / xml requests are generally empty or passthrough
        if self.mode in ("raw", "text", "html", "xml"):
            if "raw" in mapping:
                return "" if mapping["raw"] is None else str(mapping["raw"])
            return ""

        if mapping:
            v0 = next(iter(mapping.values()))
            return "" if v0 is None else str(v0)

        return ""

    def build_headers(self, body_text: str) -> Dict[str, str]:
        if self.mode == "json" and body_text:
            return {"Content-Type": "application/json"}
        return {}


# ===================================================================
# REPLY TEMPLATE
# ===================================================================

class ReplyTemplate:
    """
    ReplyTemplate represents the semantic structure of an HTTP RESPONSE.

    Used by:
      - daemon: extract + encode semantic reply
      - proxy: decode + rebuild HTTP response
    """

    def __init__(
        self,
        template_id: str,
        opcode: int,
        template_fragment: Any,
        tokens: Dict[str, Dict[str, int]],
        response_mode: Optional[str] = None,
    ):
        self.template_id = template_id
        self.opcode = int(opcode)

        frag = _coerce_fragment(template_fragment)

        # Apply responseMode column if fragment lacks mode
        if response_mode and "mode" not in frag:
            frag["mode"] = response_mode

        # Ensure baseline exists
        frag.setdefault("baseline", {})
        frag.setdefault("baseline_blobs", {})
        frag.setdefault("field_order", [])
        frag.setdefault("fields", [])

        # Materialize baseline from blob if present
        if "baseline_blob_id" in frag and "baseline" not in frag:
            bbid = frag.get("baseline_blob_id")
            if isinstance(bbid, str) and bbid:
                try:
                    data = BlobStore.load(bbid)
                    frag["baseline"] = {"raw": data.decode("utf-8", "replace")}
                except Exception:
                    frag["baseline"] = {}

        self.fragment: Dict[str, Any] = frag

        # Tokens MUST be dict-of-dicts
        if not isinstance(tokens, dict):
            tokens = {}
        self.tokens: Dict[str, Dict[str, int]] = {
            "ip":   dict(tokens.get("ip", {})),
            "host": dict(tokens.get("host", {})),
            "str":  dict(tokens.get("str", {})),
            "enum": dict(tokens.get("enum", {})),
        }

        mode = self.fragment.get("mode", "raw")
        self.handler = FORMAT_HANDLERS.get(mode, FORMAT_HANDLERS["raw"])

    # ----------------------------
    # Properties
    # ----------------------------

    @property
    def mode(self) -> str:
        return self.fragment.get("mode", "raw")

    @property
    def type_map(self) -> Dict[str, str]:
        return self.fragment.get("type_map", {}) or {}

    # ----------------------------
    # Extraction (daemon side)
    # ----------------------------

    def extract_dynamic(self, body_text: str) -> List[Tuple[str, Any]]:
        if not body_text:
            return []
        return self.handler.extract_reply_dynamic(body_text, self.fragment)

    # ----------------------------
    # Rebuild (proxy side)
    # ----------------------------

    def rebuild(self, values: List[Any]) -> str:
        """
        Rebuild HTTP body from semantic values.
        """
        return self.handler.rebuild_reply(self.fragment, values)

    def content_type(self) -> str:
        m = self.mode
        if m == "json":
            return "application/json"
        if m == "xml":
            return "application/xml"
        if m == "html":
            return "text/html; charset=utf-8"
        if m in ("raw", "text"):
            return "text/plain; charset=utf-8"
        return "application/octet-stream"
