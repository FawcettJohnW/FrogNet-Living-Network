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
# core/xml_handler.py
#
# XML semantic handler for FrogNet - FULL-FIDELITY (v4, lxml-backed).
#
# Goal: cover the entirety of XML 1.0 for ARBITRARY data with NO loss.
#   - elements, attributes (order preserved), namespaces (prefix+URI),
#     mixed content (text + tails), repeated siblings, comments, PIs,
#     CDATA sections (byte-preserved), DOCTYPE/internal subset, the XML
#     declaration, and character references - all round-trip.
#
# Template/dynamic split (preserves the codex contract):
#   - "skeleton": the full parsed document, serialized. This is the static
#     template learned once.
#   - value-slots: every element's text, every element's tail, and every
#     attribute value, enumerated in deterministic document order and keyed
#     by a unique path label (lxml getpath + "/#text" | "/#tail" | "/@name").
#     These are the dynamics that ride the wire.
#   - rebuild: re-parse the skeleton, overwrite each slot by label, serialize.
#     An unchanged reply (no provided values) replays the skeleton verbatim ->
#     byte-lossless. A changed value patches exactly that slot.
#
# SECURITY (non-negotiable - see PRIMER 2):
#   The parser runs with resolve_entities=False, no_network=True,
#   huge_tree=False, load_dtd=False. Therefore:
#     - external entities (XXE) are NOT resolved - &xxe; round-trips as a
#       literal reference, no file/network read.
#     - internal entity expansion (billion-laughs) does NOT occur.
#   Full *fidelity* (entity references preserved) without full *processing*
#   (no expansion). This is the deliberate line: cover the language, do not
#   execute its dangerous parts.
#
# Interface (unchanged, drop-in for format_registry):
#   learn_request_template / learn_reply_template -> fragment dict
#   extract_request_dynamic / extract_reply_dynamic -> List[(label, value)]
#   rebuild_reply(fragment, values) -> str
#
# Fragment keys kept for the codec/template machinery: mode, field_order,
# type_map, baseline, baseline_blobs, tokens. Adds: skeleton, had_decl.

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional
from io import BytesIO
import re

from lxml import etree

# We always hand lxml UTF-8 bytes (the handler receives an already-decoded
# str). If the body carries an XML declaration whose encoding attribute names
# a NON-UTF-8 charset (e.g. ISO-8859-1, UTF-16), libxml2 would mis-decode our
# UTF-8 bytes against that declared charset and mangle the text. Normalize the
# declaration's encoding to UTF-8 before parsing; the character DATA is
# unchanged (it is already correct Unicode), only the now-stale encoding label
# is corrected to match the bytes we actually emit.
_DECL_ENC_RE = re.compile(
    r'^(\s*<\?xml\b[^>]*?)\bencoding\s*=\s*(["\']).*?\2([^>]*\?>)',
    re.IGNORECASE | re.DOTALL,
)


def _normalize_decl_encoding(text: str) -> str:
    return _DECL_ENC_RE.sub(lambda m: m.group(1) + 'encoding="UTF-8"' + m.group(3),
                            text, count=1)


# Text/tail/attr slot suffixes (kept off the XML name grammar via '#'/'@').
_TEXT = "/#text"
_TAIL = "/#tail"


def _parser() -> etree.XMLParser:
    # Hardened: no entity resolution, no DTD load, no network, bounded tree,
    # CDATA preserved as CDATA.
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
        load_dtd=False,
        dtd_validation=False,
        strip_cdata=False,
        recover=False,
    )


def _looks_like_ip(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except Exception:
        return False


def _infer_type(val: Any) -> str:
    # Typing is a codec-compression hint only; "str" is always safe.
    if val is None:
        return "str"
    s = str(val)
    sl = s.strip().lower()
    if sl in ("true", "false"):
        return "bool"
    try:
        int(s)
        return "int"
    except Exception:
        pass
    try:
        float(s)
        return "float"
    except Exception:
        pass
    if _looks_like_ip(s):
        return "ip"
    if "." in s and " " not in s:
        return "host"
    return "str"


def _enumerate_slots(root: etree._Element):
    """Yield (label, kind, ref) in deterministic document order.
      kind == "text": ref = element, value is element.text
      kind == "tail": ref = element, value is element.tail
      kind == "attr": ref = (element, attr_name)
    Comments/PIs are static structure (ride the skeleton); not slots.
    """
    tree = root.getroottree()
    for el in root.iter():
        # element nodes only (skip comments / PIs which are also iter()'d)
        if not isinstance(el.tag, str):
            continue
        base = tree.getpath(el)
        yield (base + _TEXT, "text", el)
        for name in el.attrib:  # lxml preserves source order
            yield (base + "/@" + name, "attr", (el, name))
        # tail belongs to the element but is text AFTER its close tag
        yield (base + _TAIL, "tail", el)


def _slot_value(kind: str, ref) -> Optional[str]:
    if kind == "text":
        return ref.text
    if kind == "tail":
        return ref.tail
    if kind == "attr":
        el, name = ref
        return el.get(name)
    return None


def _set_slot(kind: str, ref, value: Any) -> None:
    v = None if value is None else str(value)
    if kind == "text":
        ref.text = v
    elif kind == "tail":
        ref.tail = v
    elif kind == "attr":
        el, name = ref
        # None would delete the attribute; preserve presence with "".
        el.set(name, "" if v is None else v)


def _empty_fragment() -> Dict[str, Any]:
    return {
        "mode": "xml",
        "skeleton": "",
        "had_decl": False,
        "field_order": [],
        "type_map": {},
        "baseline": {},
        "baseline_blobs": {},
        "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
    }


def _serialize(tree: etree._ElementTree, had_decl: bool) -> str:
    if had_decl:
        data = etree.tostring(tree, encoding="UTF-8", xml_declaration=True)
        return data.decode("utf-8")
    return etree.tostring(tree, encoding="unicode")


from .unrest_handler import UnRESTHandler  # the one UnREST handler interface


class XmlFormatHandler(UnRESTHandler):
    """Full-fidelity, security-hardened XML codex handler."""

    # ---- template learning -------------------------------------------------
    def learn_request_template(self, body_text: str) -> Dict[str, Any]:
        body_text = (body_text or "").strip()
        if not body_text:
            return _empty_fragment()
        body_text = _normalize_decl_encoding(body_text)
        try:
            tree = etree.parse(BytesIO(body_text.encode("utf-8")), _parser())
        except Exception:
            # Not well-formed (or DTD/entity rejected) -> graceful empty schema.
            return _empty_fragment()

        root = tree.getroot()
        had_decl = body_text.lstrip().startswith("<?xml")

        field_order: List[str] = []
        baseline: Dict[str, Any] = {}
        type_map: Dict[str, str] = {}
        for label, kind, ref in _enumerate_slots(root):
            val = _slot_value(kind, ref)
            field_order.append(label)
            baseline[label] = val
            type_map[label] = _infer_type(val)

        frag = _empty_fragment()
        frag.update({
            "skeleton": _serialize(tree, had_decl),
            "had_decl": had_decl,
            "field_order": field_order,
            "type_map": type_map,
            "baseline": baseline,
        })
        return frag

    def learn_reply_template(self, body_text: str) -> Dict[str, Any]:
        return self.learn_request_template(body_text)

    # ---- dynamic extraction ------------------------------------------------
    def extract_request_dynamic(self, body_text: str,
                                fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return self._extract_dynamic(body_text, fragment)

    def extract_reply_dynamic(self, body_text: str,
                              fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return self._extract_dynamic(body_text, fragment)

    def _extract_dynamic(self, body_text: str,
                         fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        body_text = (body_text or "").strip()
        if not body_text:
            return []
        body_text = _normalize_decl_encoding(body_text)
        try:
            tree = etree.parse(BytesIO(body_text.encode("utf-8")), _parser())
        except Exception:
            return []
        root = tree.getroot()
        out: List[Tuple[str, Any]] = []
        for label, kind, ref in _enumerate_slots(root):
            out.append((label, _slot_value(kind, ref)))
        return out

    # ---- rebuild -----------------------------------------------------------
    def rebuild_reply(self, fragment: Dict[str, Any],
                      values: List[Any]) -> str:
        fragment = fragment or {}
        skeleton: str = fragment.get("skeleton") or ""
        if not skeleton:
            return ""
        had_decl = bool(fragment.get("had_decl"))

        try:
            tree = etree.parse(BytesIO(skeleton.encode("utf-8")), _parser())
        except Exception:
            # Skeleton should always be well-formed (we serialized it); if a
            # caller corrupted it, fail closed to baseline-as-skeleton text.
            return skeleton
        root = tree.getroot()

        # Normalize incoming values -> {label: value}. Absent labels keep the
        # skeleton's baseline value (RESP_SAME replay is therefore lossless).
        valmap: Dict[str, Any] = {}
        if values:
            if isinstance(values[0], (tuple, list)) and len(values[0]) == 2:
                for label, v in values:
                    valmap[str(label)] = v
            else:
                fo = fragment.get("field_order") or []
                for label, v in zip(fo, values):
                    valmap[str(label)] = v

        if valmap:
            for label, kind, ref in _enumerate_slots(root):
                if label in valmap:
                    _set_slot(kind, ref, valmap[label])

        return _serialize(tree, had_decl)


# ---- DEPLOYMENT MARKER ----
print("[xml_handler] BUILD=2026-06-08-lxml-fullfidelity", flush=True)
