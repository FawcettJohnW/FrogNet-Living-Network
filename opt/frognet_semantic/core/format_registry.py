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
# core/format_registry.py - v3 format registry (single source of truth)

import json
import re
from typing import Any, Dict

from .json_handler import JsonFormatHandler
from .xml_handler import XmlFormatHandler
from .html_handler import HtmlFormatHandler
from .text_handler import RawFormatHandler, TextFormatHandler

# Single handler instances
JSON_HANDLER = JsonFormatHandler()
XML_HANDLER  = XmlFormatHandler()
HTML_HANDLER = HtmlFormatHandler()
TEXT_HANDLER = TextFormatHandler()
RAW_HANDLER  = RawFormatHandler()

FORMAT_HANDLERS: Dict[str, Any] = {
    "json": JSON_HANDLER,
    "xml":  XML_HANDLER,
    "html": HTML_HANDLER,
    "text": TEXT_HANDLER,
    "raw":  RAW_HANDLER,
}

FORMAT_HANDLERS_V3 = FORMAT_HANDLERS

# ============================================================
# Role handlers - election CRITERIA live on handlers, dispatched through one
# registry (single source of truth). Moved to core/role_registry.py so they carry
# NO lxml dependency; re-exported here for compatibility (proxy/daemon and existing
# `from core.format_registry import role_handler` callers are unaffected).
# ============================================================
from .role_registry import (ROLE_HANDLERS, role_handler,        # noqa: F401
                            SOTF_MEDIA_HANDLER, DATABASE_HANDLER)

# register the media codex in the format registry too (it was previously unwired)
FORMAT_HANDLERS["sotf_media"] = SOTF_MEDIA_HANDLER


# ============================================================
# Body sniffing (request-side)
# ============================================================

def _looks_like_json(text: str) -> bool:
    t = text.strip()
    if not (t.startswith("{") or t.startswith("[")):
        return False
    try:
        json.loads(t)
        return True
    except Exception:
        return False

def _looks_like_xml(text: str) -> bool:
    t = text.lstrip()
    if not (t.startswith("<") and not t.lower().startswith("<!doctype")):
        return False
    if re.search(r"<[A-Za-z0-9\-_]+", t[:200]) is None:
        return False
    try:
        import xml.etree.ElementTree as ET
        ET.fromstring(t)
        return True
    except Exception:
        return False

def _looks_like_html(text: str) -> bool:
    t = text.lower()
    if t.startswith("<!doctype html") or t.startswith("<html"):
        return True
    if "<html" in t[:500] or "<body" in t[:500]:
        return True
    tag_count = len(re.findall(r"</?[a-z0-9]+", t[:400], re.IGNORECASE))
    return tag_count >= 8

def _looks_like_text(text: str) -> bool:
    if not text:
        return False
    # If >15% non-printable/control-ish -> not text
    bad = sum(1 for c in text if ord(c) < 9 or (ord(c) > 126 and ord(c) < 160))
    return bad <= len(text) * 0.15

def sniff_body_mode(body_bytes: bytes) -> str:
    """
    v3 body classifier - never trust Content-Type.
    """
    if not body_bytes:
        return "raw"

    try:
        text = body_bytes.decode("utf-8", "replace")
    except Exception:
        return "raw"

    if _looks_like_json(text):
        return "json"
    if _looks_like_xml(text):
        return "xml"
    if _looks_like_html(text):
        return "html"
    if _looks_like_text(text):
        return "text"
    return "raw"


# ============================================================
# Request handler detection
# ============================================================

def detect_request_handler(headers: Dict[str, str], body_bytes: bytes) -> Any:
    """
    Request resolver: Content-Type is a weak hint; sniffing wins.
    """
    ct = (headers.get("Content-Type") or headers.get("content-type") or "").lower()

    # weak hints
    if "json" in ct:
        return JSON_HANDLER
    if "xml" in ct:
        return XML_HANDLER
    if "html" in ct:
        # only choose HTML if body actually looks like HTML
        try:
            sample = (body_bytes[:512] or b"").decode("utf-8", "replace").strip().lower()
        except Exception:
            sample = ""
        if "<html" in sample or "<!doctype html" in sample or sample.startswith("<"):
            return HTML_HANDLER

    # empirical
    mode = sniff_body_mode(body_bytes)
    return FORMAT_HANDLERS.get(mode, RAW_HANDLER)

def detect_request(ctx_or_headers: Any, body_bytes: bytes = None) -> Any:
    """
    Backward-compatible wrapper used by older code and v3.

    Accepts either:
      - ctx: {"headers": {...}, "body": b"..."}
      - headers: dict, plus body_bytes
    """
    if isinstance(ctx_or_headers, dict) and "headers" in ctx_or_headers:
        headers = ctx_or_headers.get("headers") or {}
        if body_bytes is None:
            body_bytes = ctx_or_headers.get("body", b"")
    else:
        headers = ctx_or_headers or {}
        if body_bytes is None:
            body_bytes = b""
    return detect_request_handler(headers, body_bytes)


# ============================================================
# Reply handler detection (Option B fix)
# ============================================================

def detect_reply_handler(headers: dict, body: bytes) -> Any:
    """
    Reply resolver: do NOT blindly trust Content-Type for HTML.
    Many PHP endpoints default to text/html even when returning plain text.
    """
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    try:
        sample = (body[:1024] or b"").decode("utf-8", "replace").strip().lower()
    except Exception:
        sample = ""

    # JSON: trust both header and bytes
    if ("application/json" in ctype) or sample.startswith("{") or sample.startswith("["):
        return JSON_HANDLER

    # XML: trust header and bytes
    if ("xml" in ctype) or sample.startswith("<?xml") or re.match(r"<[a-z0-9\-_]+", sample):
        # Avoid classifying HTML as XML; html handler will win if it looks like html below
        if "<html" not in sample and "<!doctype html" not in sample:
            return XML_HANDLER

    # HTML: require actual HTML evidence in bytes (not just text/html header)
    if ("html" in ctype) or sample.startswith("<"):
        if "<!doctype html" in sample or "<html" in sample or "<body" in sample or _looks_like_html(sample):
            return HTML_HANDLER

    # Any other text/* becomes TEXT by default
    if ctype.startswith("text/"):
        return TEXT_HANDLER

    # If it looks like readable text, treat as TEXT; otherwise RAW
    if _looks_like_text(sample):
        return TEXT_HANDLER

    return RAW_HANDLER
